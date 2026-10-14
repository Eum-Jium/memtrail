"""archive_sources.py — 원본 자료를 archive/sources/ 한자리로 모은다.

왜 — 원본 json 이 md 보다 안전하다. 변환기가 무엇을
버렸는지는 늘 나중에 안다. 2026-08-26 하루에만 네 번 드러났다: ChatGPT 캔버스
본문, Claude 도구 호출 712블록, 첨부 파일명, 코드 세션 발화 시각 — 전부 md 엔
없고 원본엔 있었다. 원본이 있으면 변환기를 고쳐 다시 뽑고, 없으면 그걸로 끝이다
(옛 기기 포맷으로 코드 세션 250건이 그 "끝" 이 됐다).

그런데 지금은 원본이 **데이터 폴더 루트에 흩어져** 있다 — 공식 export zip,
Takeout, 콘솔 수집분… 545MB 가 정책 없이 놓여 있었다. 규칙이 없으면 다음에
누가(=미래의 나) 지운다. 한자리로 모으고 이름에 날짜를 박아 세대를 남긴다.

기본은 **옮기기**다. 데이터 루트는 받은 파일이 잠깐 머무는 자리이지 보관처가
아니고, 여기 두면 572MB 가 이중으로 쌓인다. 원본을 지우는 게 아니라 **제자리를
주는 것**이다 — archive/ 는 tar push·drive-sync 를 타서 3중이 된다.
`--copy` 로 복사만 할 수도 있다 (외장 매체에서 읽어올 때).
이미 같은 크기로 있으면 건너뛴다.

사용:
    python scripts/archive_sources.py            # 데이터 폴더 루트를 훑어 보관
    python scripts/archive_sources.py --dry-run
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
DEST = BASE_DIR / "archive" / "sources"

# 원본으로 볼 것 — 확장자와 이름 힌트. 파생물(md·카드)은 절대 여기 안 온다.
PATTERNS = ("*.zip", "*.json")
# 기계 생성물이라 원본이 아닌 것들 (재생성 가능)
SKIP_NAMES = {"manifest.json", "index_state.json", "trail_state.json"}
SKIP_HINTS = ("skill_ids", "topic_vocab")


def candidates(root: Path) -> list[Path]:
    out = []
    for pat in PATTERNS:
        for p in sorted(root.glob(pat)):
            if not p.is_file():
                continue
            if p.name in SKIP_NAMES or any(h in p.name for h in SKIP_HINTS):
                continue
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(BASE_DIR), help="훑을 폴더 (기본: 데이터 루트)")
    ap.add_argument("--dest", default=str(DEST))
    ap.add_argument("--copy", action="store_true",
                    help="옮기지 않고 복사만 (원본이 외장 매체 등에 있을 때)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src, dest = Path(args.src), Path(args.dest)
    rows = candidates(src)
    if not rows:
        print(f"보관할 원본이 없다: {src}")
        return 0

    new = kept = 0
    total = 0
    for p in rows:
        size = p.stat().st_size
        out = dest / p.name
        if out.exists() and out.stat().st_size == size:
            kept += 1
            continue
        total += size
        new += 1
        if not args.dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            if args.copy:
                shutil.copy2(p, out)
            else:
                shutil.move(str(p), str(out))
        verb = "복사" if args.copy else "이동"
        print(f"  {'[dry] ' if args.dry_run else ''}{verb} {p.name}  ({size / 1e6:.1f}MB)")

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}원본 {len(rows)}개 → 신규 {new} ({total / 1e6:.1f}MB) / 이미 있음 {kept}")
    print(f"{tag}보관 위치: {dest}")
    if new and args.copy:
        print("  --copy 라 원본 자리의 파일은 그대로 둔다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
