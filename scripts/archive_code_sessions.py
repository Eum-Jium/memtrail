"""archive_code_sessions.py — Claude Code 세션 원본 JSONL 을 얼려둔다.

왜 필요한가 — `~/.claude/projects/**/*.jsonl` 은 **그 기계에만 있다.**
기계를 바꾸면 그걸로 끝이다.

기기를 바꾸면 원본이 통째로 사라진다 — 실제로 코드 세션 250건(7,565턴)을
그렇게 잃었다. 429건 중 원본이 남은 건 **작업 기기를 바꾼 뒤**의 101건뿐이고,
옛 기계는 이미 포맷됐다.

보관 기간 만료와는 다르다. 만료라면 "기한 안에만 챙기면 된다" 지만, 기계 교체는
① 반드시 또 일어나고 ② 그때 손실이 100%이며 ③ 예고가 없다 (옮기고 나서야
안다). **평소에 얼려두는 것 말고 방법이 없다.**

대화 자체는 살아남았다 — 매일 md 로 변환해 상시 PC 로 밀어넣었기 때문이다.
잃은 것은 원본에만 있던 것들이다 (발화 시각·도구 호출 상세). 그래서 시각을
넣는 개선을 그 250건에는 소급할 수 없다.

md 변환본은 원본이 아니다. 변환기가 버리는 것이 늘 있고(도구 호출·시각·
사이드체인), **무엇을 버렸는지는 나중에야 안다.** 오늘만 해도 두 번 겪었다:
claude.ai 변환기가 tool_use 를 버리고 있었고, 이 변환기가 timestamp 를
버리고 있었다. 원본이 있으면 고쳐서 다시 뽑으면 되고, 없으면 그걸로 끝이다.

md 를 다시 만들지 않는다 — **JSONL 을 그대로 복사**해 둘 뿐이다.
같은 이름이 이미 있으면 건드리지 않는다 (원본은 append-only 라 커지기만 하는데,
크기가 다르면 새 이름으로 세대를 남긴다).

사용:
    python scripts/archive_code_sessions.py
    python scripts/archive_code_sessions.py --dry-run
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
DEST = BASE_DIR / "archive" / "code_sessions"
SRC = Path.home() / ".claude" / "projects"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--dest", default=str(DEST))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src, dest = Path(args.src), Path(args.dest)
    if not src.is_dir():
        print(f"[abort] 원본 폴더가 없다: {src}")
        return 1

    new = kept = grown = 0
    total_bytes = 0
    for p in sorted(src.rglob("*.jsonl")):
        # 프로젝트 폴더 이름을 살려 둔다 — 세션 id 만으로는 어느 리포였는지 모른다.
        rel = p.relative_to(src)
        out = dest / rel
        size = p.stat().st_size
        total_bytes += size
        if out.exists():
            if out.stat().st_size == size:
                kept += 1
                continue
            # 원본은 append-only 라 대개 커진 것이다. 지난 판을 지우지 않고
            # 세대를 남긴다 (「옛 판 보존」).
            stamp = datetime.now().strftime("%Y%m%d")
            out = out.with_name(f"{out.stem}.{stamp}{out.suffix}")
            grown += 1
        else:
            new += 1
        if not args.dry_run:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}JSONL {new + kept + grown}개 ({total_bytes / 1e6:.1f}MB)"
          f" → 신규 {new} / 그대로 {kept} / 커져서 새 세대 {grown}")
    print(f"{tag}보관 위치: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
