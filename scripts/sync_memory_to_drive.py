"""
sync_memory_to_drive.py — 기억 시스템 → Google Drive 단방향 동기화

순서: 카드 재생성(build_memory_cards) → 시크릿 스캔 → rclone 업로드.

Drive 구조 (리모트 gdrive:, 폴더 ClaudeMemory/):
    ClaudeMemory/
      recent.md                ← 최근 대화 카드 전문 (창은 토큰 예산이 정한다)
      canon/                   ← 정본 (연표·인물·부록). 검색보다 먼저 읽는 층
      raw/
        conversations/         ← 대화 원문 전체 (백업 겸용)
        memory/                ← 프로필·타임라인 md
        cards/                 ← 파생 카드 캐시 (재생성 비용 절약용 백업)
        trail/                 ← 영수증 침전 상태 (trail_state.json). 3KB 지만
                                  여기 말고는 사본이 없던 유일한 단일본이었다
        archive/               ← 냉동 보관물 (구 리포 git 히스토리 tar 등).
                                  파생물이 아니라 재생성이 불가능한 층이다

원칙:
- 단방향 (로컬 → Drive). Drive → 로컬 **자동** 경로는 만들지 않는다. 정본은 항상 로컬.
  재해 복구는 사람이 부르는 1회성 도구로만 한다 — `scripts/restore_from_drive.py`.
  이것을 스케줄러에 걸면 Drive 가 사실상 정본이 되어 이 원칙이 깨진다.
- 삭제 전파 금지: rclone copy 사용 (로컬에서 지워도 Drive 사본은 남는다).
- 시크릿 패턴이 검출된 파일은 업로드에서 제외하고 로그에 목록을 남긴다.
- 실패 시 로그 기록 + 종료 코드 1.

사용법:
    python scripts/sync_memory_to_drive.py             # 전체 (카드 생성 + 업로드)
    python scripts/sync_memory_to_drive.py --no-build  # 업로드만
    python scripts/sync_memory_to_drive.py --no-upload # 카드 생성만

경로/바이너리 오버라이드 (macOS 이전 시 이 환경변수만 맞추면 된다):
    MEMEX_DATA_DIR   기억 데이터 루트 (기본: 이 스크립트의 부모 디렉토리)
    RCLONE_BIN       rclone 실행 파일 (기본: PATH 검색 → Windows winget 경로 폴백)
    CLAUDE_BIN       claude CLI (build_memory_cards 가 사용)
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_memory_cards as builder  # noqa: E402

BASE_DIR = builder.BASE_DIR
EXPORT_DIR = builder.EXPORT_DIR
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "drive_sync.log"
# 이 플래그 파일이 있으면 recent 창 카드를 전체 재생성 (스키마 변경 소급 적용).
# claude 는 GUI 컨텍스트에서만 되므로: ssh 로 touch 후 launchctl kickstart 로 실행.
REGEN_FLAG = LOG_DIR / "regen_cards.flag"
REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")

# archive 는 냉동 보관물(구 리포 git 히스토리 tar 등)이다. 자주 안 바뀌지만
# **재건 한 번에 사라지는 자리**라 올린다 — 2026-08-15 3차 재건 때 복구 목록에
# 없어서 실제로 노트북 사본이 없어졌다 (백업 zip 안에 있어 결과적으로만 무사).
RAW_DIRS = ["conversations", "memory", "cards", "trail", "archive"]

SECRET_PATTERNS = re.compile(
    r"sk-ant-[a-zA-Z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[a-zA-Z0-9]{36,}"
    r"|xox[bap]-[0-9a-zA-Z-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY"
)

WINGET_RCLONE = (Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
                 / "Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe"
                 / "rclone-v1.74.4-windows-amd64/rclone.exe")


def log(msg: str):
    LOG_DIR.mkdir(exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_rclone() -> str:
    exe = os.environ.get("RCLONE_BIN") or shutil.which("rclone")
    if not exe and WINGET_RCLONE.exists():
        exe = str(WINGET_RCLONE)
    if not exe:
        raise FileNotFoundError("rclone 을 찾지 못함 (RCLONE_BIN 설정 필요)")
    return exe


def scan_secrets(dirs) -> list:
    """업로드 대상에서 시크릿 패턴 검출 → 상대경로 목록."""
    hits = []
    for d in dirs:
        root = BASE_DIR / d
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in (".md", ".txt", ".json"):
                continue
            try:
                if SECRET_PATTERNS.search(p.read_text(encoding="utf-8", errors="replace")):
                    hits.append(p.relative_to(BASE_DIR).as_posix())
            except OSError:
                continue
    return hits


def run_rclone(rclone: str, args: list) -> int:
    r = subprocess.run([rclone] + args + ["--log-level", "ERROR"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log(f"rclone 실패 rc={r.returncode}: {' '.join(args[:3])} :: {(r.stderr or '').strip()[:400]}")
    return r.returncode


def _rclone_escape(name: str) -> str:
    """rclone 필터 특수문자 이스케이프 — 파일명을 리터럴로만 매칭시킨다.
    대화 제목에서 온 파일명엔 `[ ] { }` 가 남을 수 있는데(살균기는 글롭 중
    `* ?` 만 지운다), 그대로 --exclude 에 넣으면 문자클래스로 해석돼 정작
    시크릿 파일이 제외를 빠져나간다."""
    return re.sub(r"([*?\[\]{}\\])", r"\\\1", name)


def upload(rclone: str, excludes: list) -> bool:
    ok = True
    for name in ("recent.md",):
        src = EXPORT_DIR / name
        if not src.exists():
            log(f"경고: {src} 없음 — 건너뜀")
            ok = False
            continue
        # recent 는 카드 본문의 연결이라 카드에 걸린 시크릿이 그대로 들어 있다 —
        # 가장 많이 낭독되는 파일이므로 여기서도 게이트를 건다.
        if src.relative_to(BASE_DIR).as_posix() in excludes:
            log(f"업로드 제외 (시크릿 의심): {name} — 원본 정리 후 재발행할 것")
            ok = False
            continue
        ok &= run_rclone(rclone, ["copyto", str(src), f"{REMOTE}/{name}"]) == 0

    # canon/ — raw/ 백업이 아니라 recent.md 와 같은 급의 낭독 대상이다.
    # 어떤 세션이든 대화를 검색하기 전에 먼저 읽는 층이라 루트에 둔다 (fileId 안정).
    # copyto 로 파일별 업로드 — 이름이 바뀌지 않는 한 fileId 가 유지된다.
    canon_dir = BASE_DIR / "canon"
    if canon_dir.exists():
        n = 0
        for src in sorted(canon_dir.glob("*.md")):
            if src.relative_to(BASE_DIR).as_posix() in excludes:
                log(f"canon 업로드 제외 (시크릿 의심): {src.name}")
                continue
            ok &= run_rclone(rclone, ["copyto", str(src), f"{REMOTE}/canon/{src.name}"]) == 0
            n += 1
        log(f"canon: {n}개 업로드")
    else:
        log("경고: canon/ 없음 — 정본 프로필 레이어 미배포")

    for d in RAW_DIRS:
        src = BASE_DIR / d
        if not src.exists():
            continue
        args = ["copy", str(src), f"{REMOTE}/raw/{d}"]
        for e in excludes:
            if e.startswith(d + "/"):
                args += ["--exclude", _rclone_escape(e[len(d) + 1:])]
        ok &= run_rclone(rclone, args) == 0
    return ok


def topic_tag_candidates() -> list:
    """카드의 화두태그 집계 — 동일 태그 N회(기본 3) 이상 & 스레드 미존재 = 승격 후보 (명세 §5)."""
    from collections import Counter
    counts = Counter()
    cards_dir = BASE_DIR / "cards"
    if cards_dir.exists():
        for f in cards_dir.rglob("*.md"):
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("- 화두태그:"):
                        for t in line.split(":", 1)[1].split(","):
                            t = t.strip().strip("#")
                            if t:
                                counts[t] += 1
            except OSError:
                continue
    thr = int(os.environ.get("MEMEX_THREAD_THRESHOLD", "3"))
    threads = BASE_DIR / "threads"
    existing = {p.stem for p in threads.glob("*.md")} if threads.exists() else set()
    return [f"{t}({n}회)" for t, n in counts.most_common()
            if n >= thr and t not in existing]


# canon 후보 판정용 — 이력·인물이 실제로 바뀌는 사건어만.
# 일반적인 결정 어투("~하기로 했다")를 넣었더니 거의 모든 카드가 걸려 큐가 무의미해졌다
# (실측: 후보 8건 전부 오탐). 사건 명사로만 좁히고, 판단은 사람에게 넘긴다.
# 동음이의 주의: 이 코퍼스는 대부분 소프트웨어 대화라 "전역"(→전역 변수), "발령"(→명령 발령)
# 이 그대로 걸린다 (실측 오탐 다수). "전역" 은 뺐고, 발령은 "인사발령" 으로만 잡는다.
CANON_KEYWORDS = re.compile(
    r"휴직|이직|퇴사|입사|인사발령|승진|진학|학위|박사과정|자격증"
    r"|출산|결혼|이사(?!트|드)|커리어|진로")


def canon_update_candidates(limit: int = 8) -> list:
    """canon 에 아직 안 실린 이력·결정·인물 사실을 가진 카드를 후보로 보고한다.

    **자동으로 canon 을 쓰지 않는다.** 요약이 사실을 뭉갠 것이 canon 층을 만든 이유라,
    여기서 LLM 이 다시 요약해 넣으면 같은 병이 가장 하중 큰 층에서 재발한다.
    화두태그 승격 후보(promote_thread)와 같은 방식 — 후보만 내고 반영은 사람이 한다.

    이미 반영된 것은 canon 본문에 근거 대화 경로가 적혀 있으므로 그것으로 걸러낸다.
    """
    canon_dir = BASE_DIR / "canon"
    if not canon_dir.exists():
        return []
    canon_text = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                           for f in canon_dir.glob("*.md"))
    cards_dir = BASE_DIR / "cards"
    if not cards_dir.exists():
        return []
    out = []
    # 파일명이 날짜로 시작하므로 역순 = 최근순
    for f in sorted(cards_dir.rglob("*.md"), key=lambda p: p.name, reverse=True):
        try:
            card = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel, reasons = None, []
        for line in card.splitlines():
            if line.startswith("- 원본 경로:"):
                rel = line.split(":", 1)[1].strip()
            elif line.startswith("- 주체:"):
                reasons.append(f"주체={line.split(':', 1)[1].strip()}")
            elif line.startswith(("- 결정된 것:", "- 내용:")):
                m = CANON_KEYWORDS.search(line)
                if m:
                    reasons.append(m.group())   # 걸린 단어를 그대로 보고 — 사람이 바로 거른다
        if reasons and rel and rel not in canon_text:
            title = card.splitlines()[0].lstrip("# ").strip()
            out.append(f"{title} ({'/'.join(dict.fromkeys(reasons))})")
        if len(out) >= limit:
            break
    return out


_EVENT_RE = re.compile(r"^(\d{4}(?:-\d{2})?)\s*\|\s*(.+)$")


# canon 부록이 「여기 안 들어가는 것」으로 명시한 것들을 코드로 옮긴 것.
#   - 결정 / 계획·예정 / 해석·성향 / 프로젝트 결정 / 일회성 업무 사실
# 판정 문장도 거기 있다: **"내가 확인할 수 있는, 실제로 일어난 일인가?"**
#
# 실측(2026-08-26) 후보 중 버려야 했던 것들:
#   1687 프린키피아 출간 · <옛 학자의 실험>            → 학술사. 본인 사건 아님
#   <내년 계획> · <몇 해 뒤 예정>                      → 계획·예정
#   <업무 산출물 생성> · <도구 설계 완료>              → 일회성 업무
# 이보다 이전 연도는 본인 사건일 수 없다. **canon 에서 읽는다** — 생년은 이력이고
# 이력의 정본은 canon 이다. 코드에 굳히면 개인 정보가 리포에 남고, .env 로 빼면
# 같은 사실이 두 군데가 된다 (연표를 고쳐도 필터는 옛 값을 쓴다).
# 연표에서 `출생` 이 달린 가장 이른 해 = 하한. canon 이 없거나 못 찾으면 0 이고,
# 그러면 이 필터만 꺼진다 — 나머지 판정은 그대로 돈다.
_CANON_BIRTH_RE = re.compile(r"^\|\s*(\d{4})\D[^|]*\|[^|]*출생", re.M)


def _born_year() -> int:
    years = []
    canon_dir = BASE_DIR / "canon"
    if canon_dir.exists():
        for p in sorted(canon_dir.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            years += [int(y) for y in _CANON_BIRTH_RE.findall(text)]
    return min(years) if years else 0


_BORN_YEAR = _born_year()
_PLAN_WORDS = ("예정", "계획", "목표", "전망", "예상", "하기로", "검토")
_WORK_WORDS = ("설계", "구축", "개발", "배포", "리팩터", "마이그레이션", "생성",
               "구현", "적용", "테스트", "검증", "회의", "보고", "제출", "협의")


def _canon_worthy(when: str, what: str) -> bool:
    """canon 연표에 **올릴 만한** 사건인가. 아니면 조용히 버린다.

    거르는 게 아니라 **말 걸 값어치를 판정**하는 것이다 — 후보 제안 자체는
    계속 필요하다(수집 시작 이전의 인생 사건은 대화에만 있다).
    다만 canon 이 안 받는 종류까지 매일 말하면 그 목록을 아무도 안 본다.
    """
    from datetime import date
    m = re.match(r"(\d{4})", when)
    if not m:
        return False
    year = int(m.group(1))
    if _BORN_YEAR and year < _BORN_YEAR:
        return False                       # 학술사·역사 — 본인 사건이 아니다
    # 미래 = 계획. canon 은 "실제로 일어났을 때 그때 한 줄" 이 규칙이다.
    if year > date.today().year:
        return False
    if year == date.today().year:
        mm = re.match(r"\d{4}-(\d{2})", when)
        if mm and int(mm.group(1)) > date.today().month:
            return False
    if any(w in what for w in _PLAN_WORDS):
        return False                       # 문구 자체가 계획·예정
    if any(w in what for w in _WORK_WORDS):
        return False                       # 일회성 업무 사실
    return True


def timeline_candidates(limit: int = 12) -> list:
    """카드의 `- 사건:` 필드를 모아 canon 연표에 없는 것만 후보로 보고한다.

    수집 시작 이전의 인생 사건은 대화 속 언급으로만 존재하고, 카드·검색은
    "대화한 날" 기준이라 "사건이 일어난 때" 를 못 답한다. 카드 생성 때 LLM 이 이미
    대화를 읽으므로 거기서 뽑아 두고, 여기서는 **모아서 보고만** 한다 —
    canon 은 사람이 쓴다 (canon_update_candidates 와 같은 원칙).
    """
    canon_dir = BASE_DIR / "canon"
    cards_dir = BASE_DIR / "cards"
    if not canon_dir.exists() or not cards_dir.exists():
        return []
    canon_text = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                           for f in canon_dir.glob("*.md"))
    # 이미 보고했고 사람이 안 넣은 후보는 **다시 말하지 않는다.** 매번 카드를
    # 통째로 훑어 같은 걸 뱉으니, canon 에 안 넣으면 영원히 반복됐다 —
    # 2026-08-26 실측 로그에 819줄이 쌓여 있었다. 아무도 안 볼 제안이 매일
    # 찍히면 로그 전체를 못 믿게 된다 (오늘 고친 다른 셋과 같은 병).
    #
    # 「거절」이 아니라 「이미 말했다」다. 사람이 넣기로 하면 canon 에 들어가
    # canon_text 검사에서 걸러지고, 안 넣으면 그건 그 사람의 답이다.
    told_path = LOG_DIR / "canon_candidates_told.txt"
    try:
        told = {l.strip() for l in told_path.read_text(encoding="utf-8").splitlines()
                if l.strip()}
    except OSError:
        told = set()
    seen, out, fresh = set(), [], []
    for f in sorted(cards_dir.rglob("*.md"), key=lambda p: p.name, reverse=True):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.startswith("- 사건:"):
                continue
            for raw in line.split(":", 1)[1].split(";"):
                m = _EVENT_RE.match(raw.strip())
                if not m:
                    continue
                when, what = m.group(1), m.group(2).strip()
                key = (when, what[:20])
                if key in seen or when in canon_text:
                    continue        # 이미 연표에 있는 시점은 후보에서 뺀다
                seen.add(key)
                if not _canon_worthy(when, what):
                    continue
                row = f"{when} | {what[:40]}  ← {f.stem[:34]}"
                if row in told:
                    continue        # 이미 말했다 (위 주석)
                fresh.append(row)
                out.append(row)
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    if fresh:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with told_path.open("a", encoding="utf-8") as fh:
                fh.write("".join(r + "\n" for r in fresh))
        except OSError:
            pass
    return out


def aftercare(rclone: str) -> None:
    """업로드 후속 — manifest 재생성, 검역 목록, 화두태그 후보. 전부 보고성:
    실패해도 sync 성패에는 영향 없다 (명세 §6.2·6.6, §5)."""
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "build_manifest.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        tail = (r.stdout or "").strip().splitlines()
        log(f"manifest: {tail[-1] if tail else '(출력 없음)'}"
            + ("" if r.returncode == 0 else f" — 실패 rc={r.returncode}"))
    except Exception as e:
        log(f"manifest 재생성 실패: {e!r}")

    try:
        from quarantine import scan_quarantine
        q = scan_quarantine(rclone, REMOTE, BASE_DIR)
        n_raw, n_root = len(q["raw_strays"]), len(q["root_strays"])
        # 미지 접두(raw/ 아래인데 아는 층 밖)는 세지도 찍지도 않고 있었다 — 판정에만
        # 쓰여서 **있어도 안 보였다.** 2026-08-14~19 사이 `raw/trail/trail_state.json`
        # 이 닷새 내내 여기 걸렸는데 로그엔 "raw 22건" 만 떠서 아무도 몰랐다.
        # 이 항목은 대개 침입이 아니라 설정 어긋남이다 — RAW_DIRS 에 층을 더하면서
        # quarantine.RAW_LOCAL_MAP 을 안 더한 것. 그러니 더욱 보여야 한다.
        n_unk = len(q["raw_unknown_prefix"])
        if n_raw or n_root or n_unk:
            log(f"검역소: Drive 에만 있는 것 — raw {n_raw}건, 미지 접두 {n_unk}건,"
                f" 루트 {n_root}건 (자동 편입 금지 — 귀화는 quarantine_adopt 명령으로)")
            for it in q["raw_strays"][:20]:
                log(f"  격리: {it['path']} ({it['id']})")
            for it in q["raw_unknown_prefix"][:10]:
                log(f"  미지 접두: {it['path']} ({it['id']}) — RAW_LOCAL_MAP 확인")
            for it in q["root_strays"][:10]:
                log(f"  루트: {it['name']} ({it['id']})")
        else:
            log("검역소: 이상 없음")
    except Exception as e:
        log(f"검역 스캔 실패: {e!r}")

    try:
        cands = topic_tag_candidates()
        if cands:
            log(f"화두태그 승격 후보 (스레드 미존재): {', '.join(cands[:10])}"
                " — promote_thread 명령으로 승격 가능")
    except Exception as e:
        log(f"화두태그 집계 실패: {e!r}")

    try:
        cc = canon_update_candidates()
        if cc:
            log("canon 갱신 후보 (아직 canon 에 근거가 없는 이력·결정·인물 사실):")
            for c in cc:
                log(f"  · {c}")
            log("  → 자동 반영하지 않는다. canon/*.md 를 손으로 고칠 것 (근거 등급 표기 필수)")
    except Exception as e:
        log(f"canon 후보 판정 실패: {e!r}")

    try:
        ev = timeline_candidates()
        if ev:
            log("연표 후보 (카드에서 뽑힌 사건 중 canon 연표에 없는 시점):")
            for e in ev:
                log(f"  · {e}")
            log("  → 근거를 확인한 뒤 canon.md 4절에 손으로 넣을 것")
    except Exception as e:
        log(f"연표 후보 집계 실패: {e!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    log(f"=== sync 시작 (data={BASE_DIR}) ===")
    try:
        if not args.no_build:
            force = REGEN_FLAG.exists()
            if force:
                log("regen_cards.flag 감지 — recent 창 카드 전체 재생성 (스키마 소급)")
            builder.build_cards(force_recent=force)
            builder.assemble()
            if force:
                if builder._CLAUDE_DOWN:
                    log("claude 불가로 일부 카드 미재생성 — 플래그 유지 (다음 sync 재시도)")
                else:
                    REGEN_FLAG.unlink()
        if not args.no_upload:
            rclone = find_rclone()
            # drive_export(recent/archive)는 카드 본문의 연결이라 같이 스캔한다 —
            # 카드만 제외하고 이 둘을 놓치면 같은 시크릿이 루트로 올라간다.
            secrets = scan_secrets(RAW_DIRS + ["canon", "drive_export"])
            if secrets:
                log(f"시크릿 의심 파일 {len(secrets)}개 — 업로드 제외: {secrets}")
            if not upload(rclone, secrets):
                log("=== sync 실패 ===")
                return 1
            aftercare(rclone)
        log("=== sync 완료 ===")
        return 0
    except Exception as e:
        import traceback
        log(f"예외: {e}\n{traceback.format_exc()}")
        log("=== sync 실패 ===")
        return 1


if __name__ == "__main__":
    sys.exit(main())
