#!/usr/bin/env python3
"""claude.ai 웹 대화 + 코워크 세션을 LLM 없이 수집한다.

Claude in Chrome 확장(= Claude 가 브라우저를 조종하는 방식)을 쓰면 실행마다 도구 승인이
붙어서 무인 실행이 안 된다. 주입하는 JS 는 내용이 고정이므로 LLM 이 필요 없다 —
Chrome 을 DevTools Protocol 로 직접 몰아 같은 JS 를 넣고 결과만 받아온다.

Cloudflare 가 스크립트 HTTP 요청을 막기 때문에(2026-07-16 확인) 로그인된 실제 브라우저를
경유하는 것 자체는 여전히 필수다. 바뀐 건 "누가 브라우저를 모느냐" 뿐이다.

전용 프로필을 쓰는 이유: Chrome 136+ 는 기본 user-data-dir 에 대해 --remote-debugging-port
를 무시한다. 최초 1회 `--login` 으로 열어 claude.ai 에 로그인해두면 이후는 무인이다.

    python scripts/collect_web_via_cdp.py --login   # 최초 1회, 사람이 로그인
    python scripts/collect_web_via_cdp.py           # 매일 무인 실행
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import websocket  # websocket-client

REPO = Path(__file__).resolve().parent.parent
# 산출물(대화 md·갱신 목록·반출 JSON)은 데이터 루트에 쓴다 — 다른 수집·인덱싱
# 스크립트와 같은 규약. 리포와 데이터를 분리한 구성에서 여기만 리포에 쓰면
# 웹 대화가 인덱싱·발행에서 통째로 빠진다.
BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", REPO))

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

# 코워크 세션 목록 API 는 "이 기기에 묶인" 세션을 일부러 뺀다. 앱 로컬 저장소에서 id 를 긁어
# 보강한다 — 수집기를 상시 PC 로 옮기면 작업 기기에서 만든 세션은 서버 목록에 정상적으로 나오므로
# 이 보강은 수집기 자신의 기기 세션만 건진다.
LEVELDB_DIRS = [
    Path.home() / "Library/Application Support/Claude/Local Storage/leveldb",
    Path(os.environ.get("APPDATA", "")) / "Claude/Local Storage/leveldb",
    Path.home() / ".config/Claude/Local Storage/leveldb",
]


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):  # Windows 콘솔 기본이 cp949 라 한글/em dash 에서 죽는다
        _stream.reconfigure(encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- Chrome 기동

def find_chrome(explicit: str | None) -> str:
    if explicit:
        return explicit
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    raise SystemExit("Chrome 실행파일을 못 찾았다 — --chrome 로 경로를 지정해라")


def devtools_up(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except Exception:
        return False


def kill_profile_chrome(profile: Path) -> None:
    """이 프로필로 이미 떠 있는 Chrome 을 정리한다.

    같은 user-data-dir 로 두 번째 Chrome 을 띄우면 새 프로세스가 기존 인스턴스에 요청만
    넘기고 즉시 죽는다 — 디버그 포트가 안 열린다. 로그인하느라 열어둔 창이 딱 그 상황이다.
    전용 프로필이라 죽여도 사람 작업을 건드리지 않는다.
    """
    # 앞에 `--` 를 붙이면 pkill 이 패턴을 자기 옵션으로 읽는다 (그래서 조용히 실패했었다)
    needle = f"user-data-dir={profile}"
    try:
        if platform.system() == "Windows":
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                            f"Where-Object {{ $_.CommandLine -like '*{profile}*' }} | "
                            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
                           capture_output=True, timeout=30)
        else:
            subprocess.run(["pkill", "-f", "--", needle], capture_output=True, timeout=30)
    except Exception as e:
        log(f"  [warn] 기존 Chrome 정리 실패 (계속 진행): {e}")
        return
    time.sleep(2)


def launch_chrome(chrome: str, profile: Path, port: int, headful: bool, url: str) -> bool:
    """Chrome 을 디버그 포트와 함께 띄운다. 우리가 띄웠으면 True."""
    if devtools_up(port):
        log(f"이미 떠 있는 DevTools 에 붙는다 (port {port})")
        return False
    kill_profile_chrome(profile)
    profile.mkdir(parents=True, exist_ok=True)
    flags = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
    ]
    if not headful:
        # 창을 화면 밖으로 밀어둔다. --headless 는 claude.ai 에서 CF 챌린지를 부를 수 있어 안 쓴다.
        flags.append("--window-position=-2400,-2400")

    if platform.system() == "Darwin":
        # ssh 로 직접 exec 하면 Aqua 세션에 못 붙어 창이 안 뜨고 포트도 안 열린다.
        # launchservices 를 거쳐야 GUI 세션에서 뜬다.
        app = chrome.split("/Contents/MacOS/")[0]
        cmd = ["open", "-na", app, "--args"] + flags + [url]
        log("Chrome 기동 (open -na)")
        subprocess.run(cmd, capture_output=True, timeout=60)
    else:
        log("Chrome 기동")
        subprocess.Popen([chrome] + flags + [url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(60):
        if devtools_up(port):
            return True
        time.sleep(0.5)
    kill_profile_chrome(profile)
    raise SystemExit("Chrome DevTools 포트가 안 열렸다")


def claude_tab_ws(port: int, timeout: float = 30.0) -> str:
    """claude.ai 탭의 WebSocket 주소. 없으면 새로 연다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
            tabs = json.load(r)
        for t in tabs:
            if t.get("type") == "page" and t.get("url", "").startswith("https://claude.ai"):
                return t["webSocketDebuggerUrl"]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?https://claude.ai/recents", method="PUT"
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.load(r)["webSocketDebuggerUrl"]
        except urllib.error.HTTPError:
            time.sleep(1)
    raise SystemExit("claude.ai 탭을 못 열었다")


# ---------------------------------------------------------------- CDP 최소 클라이언트

class CDP:
    def __init__(self, ws_url: str):
        # suppress_origin 이 없으면 Chrome 이 Origin 헤더를 보고 WS 핸드셰이크를 403 으로 막는다
        self.ws = websocket.create_connection(ws_url, timeout=180, suppress_origin=True)
        self._id = 0
        self.send("Runtime.enable")

    def send(self, method: str, **params) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            # 이벤트는 버린다

    def eval(self, expr: str):
        """page 컨텍스트에서 평가. top-level await 를 쓰려고 async IIFE 로 감싼다."""
        wrapped = f"(async () => {{ {expr} }})()"
        res = self.send(
            "Runtime.evaluate",
            expression=wrapped,
            awaitPromise=True,
            returnByValue=True,
            timeout=600000,
        )
        if res.get("exceptionDetails"):
            raise RuntimeError(f"JS 예외: {res['exceptionDetails'].get('text')} / {res}")
        return res["result"].get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def wait_for_claude(cdp: CDP, url: str, timeout: float = 60.0) -> None:
    """탭이 실제로 claude.ai 문서를 들고 있을 때까지 기다린다.

    갓 뜬 Chrome 의 탭은 아직 초기 about:blank 라, 거기서 document.cookie 를 읽으면
    opaque origin 이라 SecurityError 가 난다.
    """
    deadline = time.time() + timeout
    navigated = False
    while time.time() < deadline:
        try:
            state = cdp.eval("return location.origin + '|' + document.readyState;")
        except RuntimeError:
            state = "|"
        origin, _, ready = (state or "|").partition("|")
        if origin == "https://claude.ai" and ready in ("interactive", "complete"):
            return
        if not navigated:
            cdp.send("Page.navigate", url=url)
            navigated = True
        time.sleep(1)
    raise SystemExit(f"claude.ai 문서 로딩 실패 (마지막 상태: {state})")


def pull_array(cdp: CDP, js_var: str, count: int, label: str) -> list:
    """큰 배열은 한 번에 못 받는다(WS 프레임·직렬화 비용). 원소 단위로 끌어온다."""
    out = []
    for i in range(count):
        raw = cdp.eval(f"return JSON.stringify({js_var}[{i}]);")
        out.append(json.loads(raw))
        if (i + 1) % 10 == 0 or i + 1 == count:
            log(f"  {label} {i + 1}/{count}")
    return out


# ---------------------------------------------------------------- JS 페이로드

JS_LOGIN_CHECK = """
const orgId = document.cookie.match(/lastActiveOrg=([^;]+)/)?.[1];
if (!orgId) return JSON.stringify({ok: false, why: 'lastActiveOrg 쿠키 없음'});
const r = await fetch(`/api/organizations/${orgId}/chat_conversations?limit=1`);
return JSON.stringify({ok: r.ok, status: r.status, orgId});
"""

JS_WEB_LIST = """
const orgId = document.cookie.match(/lastActiveOrg=([^;]+)/)?.[1];
const BASE = `/api/organizations/${orgId}/chat_conversations`;
let all = [], offset = 0;
while (offset < 3000) {
  const r = await fetch(`${BASE}?limit=50&offset=${offset}`);
  if (!r.ok) break;
  const b = await r.json();
  if (!b.length) break;
  all.push(...b); offset += 50;
  if (b.length < 50) break;
}
window.__recent = all.filter(c => (c.updated_at || c.created_at || '') >= 'CUTOFF');
window.__allIds = all.map(c => ({uuid: c.uuid, name: c.name || '',
                                 updated: (c.updated_at || c.created_at || '').slice(0,10)}));
return JSON.stringify({total: all.length, recent: window.__recent.length});
"""

# 렌더 옵션 3종은 필수다 — 빼면 일부 블록이 "This block is not supported on your current
# device yet." 로 치환돼 내용이 조용히 유실된다 (2026-07-31 확인, 반출 630KB -> 1.6MB).
JS_WEB_DETAIL = """
const orgId = document.cookie.match(/lastActiveOrg=([^;]+)/)?.[1];
const BASE = `/api/organizations/${orgId}/chat_conversations`;
const Q = '?tree=True&rendering_mode=messages&render_all_tools=true';
window.__webOut = []; let fail = 0;
for (const c of window.__recent) {
  try { const r = await fetch(`${BASE}/${c.uuid}${Q}`);
        if (r.ok) window.__webOut.push(await r.json()); else fail++; }
  catch (e) { fail++; }
}
return JSON.stringify({fetched: window.__webOut.length, fail,
                       msgCounts: window.__webOut.map(d => (d.chat_messages||[]).length)});
"""

# anthropic-version 헤더가 없으면 400.
JS_COWORK_LIST = """
const H = {'anthropic-version':'2023-06-01'};
let ids = [], cursor = null;
for (let p = 0; p < 10; p++) {
  const r = await fetch('/v1/code/sessions?limit=100' + (cursor?'&cursor='+encodeURIComponent(cursor):''), {headers:H});
  if (!r.ok) break;
  const b = await r.json();
  (b.data||[]).forEach(s => ids.push({id:s.id, title:s.title||'', created_at:s.created_at||'', origin:s.config?.origin||''}));
  if (!b.next_cursor) break; cursor = b.next_cursor;
}
window.__cwIds = ids;
const extra = EXTRA_IDS;
const have = new Set(ids.map(x => x.id));
extra.forEach(id => { if (!have.has(id)) window.__cwIds.push({id, title:'', created_at:'', origin:'local_bound'}); });
return JSON.stringify({fromApi: ids.length, total: window.__cwIds.length});
"""

JS_COWORK_DETAIL = """
const H = {'anthropic-version':'2023-06-01'};
const txt = c => Array.isArray(c) ? c.map(x => typeof x?.text==='string'?x.text:'').filter(Boolean).join('\\n\\n') : (typeof c==='string'?c:'');
window.__cwOut = []; let fail = 0;
for (const s of window.__cwIds) {
  try {
    let msgs = [], cur = null;
    for (let p = 0; p < 12; p++) {
      const r = await fetch(`/v1/code/sessions/${s.id}/events?limit=500` + (cur?'&cursor='+encodeURIComponent(cur):''), {headers:H});
      if (!r.ok) { if (p===0) fail++; break; }
      const b = await r.json();
      for (const e of (b.data||[])) {
        if (e.event_type!=='user' && e.event_type!=='assistant') continue;
        const t = txt(e.payload?.message?.content);
        if (t && t.trim()) msgs.push({role:e.event_type, text:t, at:e.created_at});
      }
      if (!b.next_cursor) break; cur = b.next_cursor;
    }
    msgs.sort((a,b)=>(a.at||'').localeCompare(b.at||''));
    window.__cwOut.push({id:s.id, title:s.title, created_at: s.created_at || msgs[0]?.at || '', origin:s.origin, messages:msgs});
  } catch(e) { fail++; }
}
return JSON.stringify({sessions: window.__cwOut.length, fail,
                       totalMsgs: window.__cwOut.reduce((n,s)=>n+s.messages.length,0)});
"""


_ID_LINE = re.compile(r"^- (?:\*\*)?ID(?:\*\*)?: ?([0-9a-fA-F-]{30,})", re.M)
_DATE_HEAD = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})[_ ]")


def corpus_index(conv_dir: Path) -> tuple[set[str], set[str]]:
    """코퍼스의 (uuid 집합, 날짜+제목 키 집합).

    두 벌인 이유는 옛 스킴 파일에 `- ID:` 줄이 없기 때문이다 — uuid 만 보면
    이미 갖고 있는 대화를 "없다" 고 세어 경보가 양치기 소년이 된다.
    """
    uuids: set[str] = set()
    keys: set[str] = set()
    for p in conv_dir.glob("*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        m = _ID_LINE.search(head)
        if m:
            uuids.add(m.group(1))
        d = _DATE_HEAD.match(p.name)
        if d:
            title = re.sub(r"\.md$", "", p.name)[len(d.group(0)):]
            keys.add("".join(d.groups()) + "|" + re.sub(r"\s+", " ", title).strip().lower()[:40])
    return uuids, keys


def report_coverage(ids: list[dict], conv_dir: Path, health_path: Path) -> int:
    """**서버에 N건, 내게 M건** 을 매 실행 적는다. 반환은 누락 수.

    이게 없어서 사고가 났다 — claude.ai 웹 대화가 오랫동안 절반도 안 들어와
    있었는데(2026-08-26 공식 export 대조: 755건 중 코퍼스에 270건) 아무도 몰랐다.
    **실패는 시끄럽지만 누락은 조용하다.** 수집기가 매번 자기 몫을 세면 그 종류가
    통째로 막힌다. 공식 export 를 받아봐야만 알 수 있는 상태로 두지 않는다.
    """
    uuids, keys = corpus_index(conv_dir)
    missing = []
    for c in ids:
        u = (c.get("uuid") or "").strip()
        if u and u in uuids:
            continue
        d = (c.get("updated") or "").replace("-", "")
        k = d + "|" + re.sub(r"\s+", " ", c.get("name") or "").strip().lower()[:40]
        if k in keys:
            continue
        missing.append(c)
    log(f"수집 커버리지 — 서버 {len(ids)}건 / 코퍼스에 {len(ids) - len(missing)}건"
        f" / **누락 {len(missing)}건**")
    if missing:
        log("  누락이 있다. 최근 것 몇 개:")
        for c in sorted(missing, key=lambda x: x.get("updated") or "", reverse=True)[:5]:
            log(f"    {c.get('updated')}  {(c.get('name') or 'Untitled')[:50]}")
        log("  메우는 법: claude.ai 설정 → 데이터 내보내기 → conversations zip 을 받아")
        log("    python scripts/convert_claude_api_json.py <conversations.json>"
            " --refresh --archive-dir archive/claude_superseded_<날짜>")
    try:
        health_path.parent.mkdir(parents=True, exist_ok=True)
        health_path.write_text(json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "server": len(ids), "corpus": len(ids) - len(missing),
            "missing": len(missing),
            "missing_sample": [{"date": c.get("updated"), "name": c.get("name")}
                               for c in missing[:20]],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log(f"  (건강 파일 기록 실패: {e})")
    return len(missing)


def local_bound_ids() -> list[str]:
    ids: set[str] = set()
    for d in LEVELDB_DIRS:
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            try:
                blob = f.read_bytes()
            except OSError:
                continue  # 앱이 잠근 파일 — 읽히는 것만 써도 된다
            ids.update(m.group().decode() for m in re.finditer(rb"cse_[A-Za-z0-9]{22,26}", blob))
    return sorted(ids)


# ---------------------------------------------------------------- 변환

def run_converter(python: str, script: str, src: Path, out_dir: Path, changed: Path, extra: list[str]) -> int:
    cmd = [python, str(REPO / "scripts" / script), str(src),
           "--out", str(out_dir), "--refresh", "--changed-list", str(changed)] + extra
    log("변환: " + " ".join(cmd[1:3]))
    # **인코딩을 명시한다.** text=True 만 주면 Windows 가 로캘(cp949)로 디코드해서
    # 한글 로그 한 줄에 UnicodeDecodeError 로 죽고, 그때 p.stdout 이 None 이라
    # 다음 write 에서 TypeError 로 또 죽는다 (2026-08-26 실측). 변환 자체는
    # 멀쩡히 끝났는데 **로그를 읽다가** 수집이 실패로 끝나는 모양이었다.
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    sys.stdout.write(p.stdout or "")
    if p.returncode != 0:
        sys.stderr.write(p.stderr or "")
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BASE_DIR / "conversations"), help="md 출력 폴더")
    ap.add_argument("--raw-dir", default=str(BASE_DIR / "claude_downloads"), help="반출 JSON 보관 폴더")
    ap.add_argument("--profile", default=str(Path.home() / ".memex-chrome"))
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--cutoff-days", type=int, default=3,
                    help="이 일수 안에 갱신된 웹 대화만 (겹쳐 받는 게 정상)")
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--headful", action="store_true", help="창을 화면 안에 띄운다 (디버그용)")
    ap.add_argument("--keep-chrome", action="store_true", help="끝나고 Chrome 을 안 닫는다")
    ap.add_argument("--login", action="store_true",
                    help="로그인용으로 프로필 Chrome 만 띄우고 끝낸다 (최초 1회)")
    ap.add_argument("--skip-web", action="store_true")
    ap.add_argument("--skip-cowork", action="store_true")
    ap.add_argument("--index", action="store_true",
                    help="변환 후 index_incremental 로 색인까지 (수집기가 정본 기계일 때)")
    args = ap.parse_args()

    chrome = find_chrome(args.chrome)
    profile = Path(args.profile)

    if args.login:
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([chrome, f"--user-data-dir={profile}", "--no-first-run",
                          "--no-default-browser-check", "https://claude.ai/login"])
        print(f"프로필 {profile} 로 Chrome 을 띄웠다. claude.ai 에 로그인한 뒤 창을 닫아라.")
        return 0

    out_dir = Path(args.out)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    cutoff = (datetime.now() - timedelta(days=args.cutoff_days)).strftime("%Y-%m-%d")

    launched = launch_chrome(chrome, profile, args.port, args.headful, "https://claude.ai/recents")
    cdp = None
    rc = 0
    try:
        cdp = CDP(claude_tab_ws(args.port))
        cdp.send("Page.enable")
        wait_for_claude(cdp, "https://claude.ai/recents")
        chk = json.loads(cdp.eval(JS_LOGIN_CHECK))
        if not chk.get("ok"):
            # **소리나게 죽는다.** 예전엔 rc=2 로 조용히 끝났고, 그 rc 를 아무도
            # 안 봐서 8/13부터 열이틀 넘게 멈춰 있었다 (카드에 「web-collect 고장」이
            # 미결로 올라와 있었는데도 안 닫혔다). 로그는 읽히지 않는다는 전제로
            # 상태를 파일에 남긴다 — 이건 drive-sync 가 올려서 회상에도 닿는다.
            log("!" * 62)
            log("!! claude.ai 로그인이 풀렸다 — 웹 대화 수집이 **멈춘다**")
            log(f"!! 고치는 법: python {Path(__file__).name} --login  (프로필 {profile})")
            log("!" * 62)
            try:
                health = BASE_DIR / "logs" / "collect_health.json"
                health.parent.mkdir(parents=True, exist_ok=True)
                health.write_text(json.dumps({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "ok": False, "why": "login_expired", "detail": chk,
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
            return 2
        log(f"로그인 확인 (org {chk['orgId'][:8]})")

        if not args.skip_web:
            info = json.loads(cdp.eval(JS_WEB_LIST.replace("CUTOFF", cutoff)))
            log(f"웹 대화: 전체 {info['total']}, {cutoff} 이후 {info['recent']}")
            # 커버리지는 **수집 전에** 잰다 — 이번 실행이 메우기 전의 실상이 남아야
            # "얼마나 밀려 있었나" 를 알 수 있다.
            try:
                # 목록은 uuid·제목·날짜뿐이라 가볍다(700건에 ~60KB) — pull_array 처럼
                # 원소 단위로 끌면 700번 왕복이 된다. 한 번에 받는다.
                all_ids = json.loads(cdp.eval("return JSON.stringify(window.__allIds);"))
                report_coverage(all_ids, out_dir, BASE_DIR / "logs" / "collect_health.json")
            except Exception as e:                 # 커버리지 실패가 수집을 막지 않게
                log(f"  (커버리지 대조 실패: {e!r})")
            if info["recent"]:
                det = json.loads(cdp.eval(JS_WEB_DETAIL))
                log(f"  상세 {det['fetched']}개 (실패 {det['fail']}) msgs={det['msgCounts']}")
                convs = pull_array(cdp, "window.__webOut", det["fetched"], "웹")
                # 브라우저가 센 메시지 수와 대조 — 다르면 반출이 잘린 것이다
                got = [len(c.get("chat_messages", [])) for c in convs]
                if got != det["msgCounts"]:
                    log(f"  [error] 메시지 수 불일치 {got} != {det['msgCounts']}")
                    return 3
                src = raw_dir / f"claude_web_{stamp}.json"
                src.write_text(json.dumps(convs, ensure_ascii=False), encoding="utf-8")
                log(f"  저장 {src} ({src.stat().st_size / 1e6:.1f}MB)")
                rc |= run_converter(args.python, "convert_claude_api_json.py", src, out_dir,
                                    BASE_DIR / "logs" / "web_changed.txt", [])

        if not args.skip_cowork:
            extra = local_bound_ids()
            log(f"로컬 바인딩 코워크 id {len(extra)}개")
            info = json.loads(cdp.eval(
                JS_COWORK_LIST.replace("EXTRA_IDS", json.dumps(extra))))
            log(f"코워크 세션: API {info['fromApi']}, 합계 {info['total']}")
            if info["total"]:
                det = json.loads(cdp.eval(JS_COWORK_DETAIL))
                log(f"  본문 {det['sessions']}개 (실패 {det['fail']}) 발화 {det['totalMsgs']}")
                sessions = pull_array(cdp, "window.__cwOut", det["sessions"], "코워크")
                src = raw_dir / f"cowork_{stamp}.json"
                src.write_text(json.dumps(sessions, ensure_ascii=False), encoding="utf-8")
                log(f"  저장 {src} ({src.stat().st_size / 1e6:.1f}MB)")
                rc |= run_converter(args.python, "convert_cowork_sessions.py", src, out_dir,
                                    BASE_DIR / "logs" / "cowork_changed.txt", [])
        if args.index:
            # stale 청크 정리는 index_incremental 이 --changed 목록으로 한다. 목록이 비어도
            # 신규 파일은 잡히므로 그냥 돌린다.
            changed = BASE_DIR / "logs" / "cdp_changed.txt"
            merged = []
            for f in (BASE_DIR / "logs" / "web_changed.txt", BASE_DIR / "logs" / "cowork_changed.txt"):
                if f.exists():
                    merged += [ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
            changed.write_text("\n".join(merged), encoding="utf-8")
            log(f"색인 (갱신 목록 {len(merged)}개)")
            p = subprocess.run([args.python, str(REPO / "scripts" / "index_incremental.py"),
                                "--changed", str(changed)], capture_output=True, text=True)
            sys.stdout.write(p.stdout)
            if p.returncode != 0:
                sys.stderr.write(p.stderr)
                rc |= p.returncode
    finally:
        if cdp:
            cdp.close()
        if launched and not args.keep_chrome:
            log("Chrome 종료")
            kill_profile_chrome(profile)

    return rc


if __name__ == "__main__":
    sys.exit(main())
