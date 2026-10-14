"""
build_skill_zip.py — claude.ai 업로드용 스킬 zip 을 만든다.

리포의 `skills/<src>/SKILL.md` 는 **질문지**다: Drive ID 자리가 플레이스홀더로
비어 있고 스킬 이름도 리포 이름이다. 업로드본은 거기에 개인 ID 를 이식한
**답안지**이므로 리포에 두지 않는다 — 이 스크립트가 둘을 잇는다.

  python scripts/build_skill_zip.py                     # 기본값으로 빌드
  python scripts/build_skill_zip.py --name memtrail --ids <경로> --out <경로>
  python scripts/build_skill_zip.py --check             # 치환 없이 플레이스홀더 점검만

**옛 판은 절대 지우거나 덮어쓰지 않는다.** 업로드한 zip 은 "그때 claude.ai 가 무엇을
읽고 있었나" 의 유일한 기록이라, 회상이 이상해졌을 때 되짚을 자리가 그것뿐이다.
같은 날 두 번 뽑으면 `-2`, `-3` 을 붙여 나란히 남긴다 (전엔 같은 이름으로 덮어써서
2026-08-19 첫 판이 실제로 사라졌다). 직전 판과 내용이 같으면 새 파일을 만들지
않고 어느 판과 같은지 알려준다 — 안 지우는 것과 안 쌓는 것을 같이 지키려는 것.
`--out` 을 직접 주는데 그 경로가 이미 있으면 중단한다 (`--overwrite` 로만 강제).

보관 위치 `$MEMEX_DATA_DIR/skill_build/` 는 gitignore 이고 **Drive 발행 대상도
아니다** — 기기가 날아가면 같이 날아간다. 다만 리포와 ID 만 있으면 다시 만들 수 있다.

ID 파일(JSON)은 리포 밖에 둔다. 기본 경로 `$MEMEX_DATA_DIR/config/skill_ids.json`:

    {"CANON_FOLDER_ID": "...", "QUERIES_FOLDER_ID": "...",
     "ANSWERS_FOLDER_ID": "...", "TRAILS_FOLDER_ID": "...",
     "RECENT_MD_FILE_ID": "..."}

이 파일은 **백업 대상이 아니다.** 잃어도 Drive 에서 다시 뽑으면 된다:

    rclone lsjson gdrive:ClaudeMemory --dirs-only          # canon
    rclone lsjson gdrive:ClaudeMemory/mailbox --dirs-only  # queries·answers·trails
    rclone lsjson gdrive:ClaudeMemory --files-only         # recent.md

두 가지가 이 스크립트의 존재 이유다:

1. **압축은 반드시 파이썬 zipfile 로.** PowerShell `Compress-Archive` 는 경로
   구분자를 백슬래시로 넣어 claude.ai 가 반려한다 (2026-08-15 실제로 한 번 반려됨).
   한글 파일명이 섞이면 UTF-8 플래그 문제도 겹친다.
2. **미치환 플레이스홀더를 들고 나가지 않는다.** `<QUERIES_FOLDER_ID>` 가 하나라도
   남은 zip 은 업로드돼도 투함이 안 된다 — 조용히 실패하는 종류라 빌드에서 막는다.
"""

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", ROOT))

PLACEHOLDER_RE = re.compile(r"<([A-Z_]+_ID)>")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def builds_of(out_dir: Path, name: str) -> list[Path]:
    """이 스킬의 보관 판 — 만든 순서(mtime). 파일명 정렬은 못 쓴다: `-2` 접미가
    붙은 판이 `-` < `.` 라 원판보다 앞서 정렬돼 최신 판단이 뒤집힌다."""
    if not out_dir.is_dir():
        return []
    return sorted(out_dir.glob(f"{name}-*.zip"), key=lambda p: p.stat().st_mtime)


def body_of(zip_path: Path, name: str) -> str | None:
    try:
        with zipfile.ZipFile(zip_path) as z:
            return z.read(f"{name}/SKILL.md").decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def next_free(out_dir: Path, name: str) -> Path:
    stem = f"{name}-{date.today().isoformat()}"
    out = out_dir / f"{stem}.zip"
    n = 2
    while out.exists():
        out = out_dir / f"{stem}-{n}.zip"
        n += 1
    return out


def show_history(out_dir: Path, name: str) -> None:
    kept = builds_of(out_dir, name)
    print(f"       보관 중인 판 {len(kept)}개 (지우지 않는다):")
    for p in kept[-6:]:
        print(f"         {p.name}  {p.stat().st_size:,}B")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="memex-mailbox", help="skills/ 아래 원본 폴더명")
    ap.add_argument("--name", default="memtrail", help="업로드본 스킬 이름")
    ap.add_argument("--ids", default=str(BASE_DIR / "config" / "skill_ids.json"))
    ap.add_argument("--out", help="기본: $MEMEX_DATA_DIR/skill_build/<name>-<날짜>.zip")
    ap.add_argument("--check", action="store_true", help="플레이스홀더 목록만 보고 끝")
    ap.add_argument("--overwrite", action="store_true",
                    help="--out 경로가 이미 있어도 덮어쓴다 (기본은 중단 — 옛 판 보존)")
    args = ap.parse_args()

    src = ROOT / "skills" / args.src / "SKILL.md"
    if not src.exists():
        print(f"[abort] 원본이 없다: {src}")
        return 2
    text = src.read_text(encoding="utf-8")

    found = sorted(set(PLACEHOLDER_RE.findall(text)))
    if args.check:
        print(f"[check] {src.relative_to(ROOT)} 플레이스홀더 {len(found)}개")
        for k in found:
            print(f"  <{k}>")
        return 0

    ids_path = Path(args.ids)
    if not ids_path.exists():
        print(f"[abort] ID 파일이 없다: {ids_path}")
        print("        리포 밖에 둔다 (개인 답안지). 형식은 이 파일 docstring 참고.")
        return 2
    ids = json.loads(ids_path.read_text(encoding="utf-8"))

    missing = [k for k in found if k not in ids]
    if missing:
        print(f"[abort] ID 파일에 없는 자리: {', '.join(missing)}")
        return 2

    # 스킬 이름 — frontmatter 의 name 과 본문 첫 H1 제목 둘 다 맞춘다. 이름은
    # memtrail 인데 본문 제목은 memex-mailbox 인 zip 이 나가면 읽는 쪽이 딴 스킬로
    # 읽는다. 그 밖의 본문 서술("memex 에 근거가 없다" 등)은 canon 표기와 짝이라
    # 건드리지 않는다.
    text, n = re.subn(rf"^name: {re.escape(args.src)}$", f"name: {args.name}",
                      text, count=1, flags=re.M)
    if n != 1:
        print(f"[abort] frontmatter 의 'name: {args.src}' 를 못 찾았다")
        return 2
    text, n_h1 = re.subn(rf"^# {re.escape(args.src)}\b", f"# {args.name}",
                         text, count=1, flags=re.M)
    if n_h1 != 1:
        print(f"[warn] 본문 첫 H1 '# {args.src}' 를 못 찾아 제목은 그대로 둔다")

    for k in found:
        text = text.replace(f"<{k}>", ids[k])

    left = PLACEHOLDER_RE.findall(text)
    if left:
        print(f"[abort] 미치환 플레이스홀더가 남았다: {sorted(set(left))}")
        return 2

    out_dir = BASE_DIR / "skill_build"
    if args.out:
        out = Path(args.out)
        if out.exists() and not args.overwrite:
            print(f"[abort] 그 경로엔 이미 판이 있다: {out}")
            print("        옛 판은 지우지 않는다 — 다른 경로를 주거나 --overwrite.")
            return 2
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        kept = builds_of(out_dir, args.name)
        latest = kept[-1] if kept else None
        if latest is not None and body_of(latest, args.name) == text:
            print(f"[skip] 직전 판과 내용이 같다 — 새로 만들지 않는다: {latest.name}")
            show_history(out_dir, args.name)
            return 0
        out = next_free(out_dir, args.name)
    out.parent.mkdir(parents=True, exist_ok=True)

    # zipfile 로 쓴다 — arcname 은 POSIX 구분자여야 claude.ai 가 받는다
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{args.name}/SKILL.md", text.encode("utf-8"))

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        body = z.read(f"{args.name}/SKILL.md").decode("utf-8")
    assert "\\" not in "".join(names), names
    assert not PLACEHOLDER_RE.search(body)

    print(f"[done] {out}")
    print(f"       구조: {names}")
    print(f"       이름: {args.name} | 치환 {len(found)}자리 | {len(body):,}자 / {out.stat().st_size:,}B")
    if not args.out:
        show_history(out_dir, args.name)
    print("       업로드는 claude.ai 설정 → 스킬 (구판 교체).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
