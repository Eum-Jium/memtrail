"""
convert_gemini_takeout.py — Google Takeout 의 Gemini 앱 활동을 대화 md 로 변환한다.

입력은 Takeout zip (또는 풀어놓은 내활동.json). Gemini 는 공식 대화 내보내기가
없어서 「내 활동」 기록이 유일한 원천인데, 형태가 대화가 아니라 **프롬프트 한 건이
활동 한 항목**이다. 다행히 각 항목의 details 에 대화 URL
(gemini.google.com/app/<id>)이 붙어 있어서, 그 id 로 항목들을 대화로 되묶는다
(2026-08-25 실측: 912항목 → 194대화, URL 없는 항목 27건은 단독 대화로).

출력 파일명은 `YYYY-MM-DD_<제목>__gm_<id8>.md` — `__gm_` 이 출처 태그다.
ChatGPT 는 이미 섞여 들어온 것을 소급 판정해야 해서 목록(provenance)이 필요했지만,
Gemini 는 우리가 파일을 만드는 쪽이므로 코드 세션(`__cc_`)과 같은 파일명 규칙이
더 단순하고 안 어긋난다. index_incremental.source_for / conv_key 가 이 태그를 안다.

주의:
- 응답(safeHtmlItem)이 없는 항목이 실측 52건 — 질문만 저장한다 (파서가 짝 없는
  턴도 살린다). 활동 기록이 응답을 안 남긴 것이지 수집 실패가 아니다.
- title 은 프롬프트 전문 + " 항목을 검색함" 접미사다. 접미사만 떼면 발화 원문이다.
- 시각은 UTC — 날짜는 KST(+9) 로 바꿔 적는다. 파일명·헤더가 대화 날짜 기준이라
  자정 근처 대화가 하루 어긋나는 것을 막는다.

사용:
    python scripts/convert_gemini_takeout.py <takeout.zip> [--out conversations/] [--dry-run]
"""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import zipfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TITLE_SUFFIX = re.compile(r"\s*항목을 검색함\s*$")
CONV_URL = re.compile(r"gemini\.google\.com/app/([0-9a-f]+)")
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')   # Windows 파일명 금지 문자


class _HtmlToText(HTMLParser):
    """Gemini 응답 HTML → 읽을 수 있는 평문. 충실도 우선, 마크다운 흉내는 최소만.

    <pre> 는 코드펜스로 감싼다 — 응답에 코드가 실제로 많고, 펜스가 없으면
    인덱스 청크에서 코드와 산문이 뭉개진다.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.in_pre = False

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre = True
            self.out.append("\n```\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * min(int(tag[1]) + 1, 6) + " ")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag == "pre":
            self.in_pre = False
            self.out.append("\n```\n")
        elif tag in ("p", "ul", "ol", "div", "tr", "table",
                     "h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")

    def handle_data(self, data):
        self.out.append(data)

    def text(self) -> str:
        t = "".join(self.out)
        t = re.sub(r"[ \t]+\n", "\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()


def html_to_text(html: str) -> str:
    p = _HtmlToText()
    p.feed(html)
    return p.text()


def conv_id_of(entry: dict) -> str | None:
    for x in entry.get("details") or []:
        m = CONV_URL.search(x.get("url") or "")
        if m:
            return m.group(1)
    return None


def kst_date(iso: str) -> str:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(hours=9)
    return t.date().isoformat()


def title_of(prompt: str) -> str:
    first = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "제목없음")
    t = FORBIDDEN.sub(" ", first)
    t = re.sub(r"\s+", " ", t).strip()[:40].strip()
    return t or "제목없음"


def load_entries(src: Path) -> list[dict]:
    if src.suffix == ".zip":
        z = zipfile.ZipFile(src)
        name = next((n for n in z.namelist()
                     if n.endswith(("내활동.json", "MyActivity.json"))), None)
        if not name:
            sys.exit("[abort] zip 안에 내활동.json / MyActivity.json 이 없다")
        return json.loads(z.read(name).decode("utf-8"))
    return json.loads(src.read_text(encoding="utf-8"))


def convert(entries: list[dict]) -> dict[str, str]:
    """활동 항목들 → {파일명: md 본문}."""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        if (e.get("header") or "").strip() not in ("Gemini 앱", "Gemini Apps"):
            continue
        cid = conv_id_of(e)
        if cid is None:
            # URL 없는 항목 — 단독 대화. 시각+제목 해시로 재실행에도 같은 이름이
            # 나온다. 순수 hex 여야 한다 — conv_key 의 `__gm_<hex8>` 태그 정규식과
            # 맞아야 정체 판정이 이 id 로 붙는다.
            cid = hashlib.md5(
                f"{e.get('time')}|{e.get('title')}".encode()).hexdigest()[:16]
        groups.setdefault(cid, []).append(e)

    out: dict[str, str] = {}
    for cid, items in groups.items():
        items.sort(key=lambda e: e.get("time") or "")
        first = items[0]
        date = kst_date(first["time"]) if first.get("time") else "unknown"
        prompt0 = TITLE_SUFFIX.sub("", first.get("title") or "")
        parts = [f"# {title_of(prompt0)}", "",
                 f"- ID: {cid}", f"- 생성일: {date}", "", "---", ""]
        for e in items:
            q = TITLE_SUFFIX.sub("", e.get("title") or "").strip()
            if q:
                parts += ["## 👤 User", "", q, "", "---", ""]
            html = "".join(x.get("html") or "" for x in e.get("safeHtmlItem") or [])
            if html:
                parts += ["## 🤖 Assistant", "", html_to_text(html), "", "---", ""]
        fname = f"{date}_{title_of(prompt0)}__gm_{cid[:8]}.md"
        out[unicodedata.normalize("NFC", fname)] = "\n".join(parts).rstrip() + "\n"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="Takeout zip 또는 내활동.json 경로")
    ap.add_argument("--out", default="conversations", help="출력 폴더")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = convert(load_entries(Path(args.src)))
    outdir = Path(args.out)
    made = kept = 0
    for name, body in sorted(files.items()):
        dst = outdir / name
        if dst.exists():
            kept += 1              # 재실행 멱등 — 있던 파일은 안 덮는다
            continue
        if not args.dry_run:
            outdir.mkdir(parents=True, exist_ok=True)
            dst.write_text(body, encoding="utf-8")
        made += 1
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}대화 {len(files)}건 → 신규 {made} / 기존 유지 {kept} ({outdir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
