"""
월간 메모리 업데이트 도구 (로컬 전용).

흐름 (2단계):
  1. conversations/ 폴더 증분 인덱싱 (DB에 없는 신규 .md 만 add)
  2. (선택) generate_profile.py 호출 → 5개 프로필 .md 재합성

사전 조건:
  MEMEX_DATA_DIR : 데이터 루트 ($DATA/conversations, $DATA/memory_db, $DATA/memory)

사용 예:
  python scripts/monthly_update.py                   # 전 단계 실행
  python scripts/monthly_update.py --skip-profile    # 프로필 재합성 생략 (claude CLI 안 돌림)
  python scripts/monthly_update.py --skip-index      # 인덱싱 생략 (프로필만)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# 같은 레포 루트 기준으로 memory_pipeline import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from memory_pipeline import add_new_files


def _resolve_data_dir() -> Path:
    raw = os.environ.get("MEMEX_DATA_DIR")
    if not raw:
        sys.exit("[error] MEMEX_DATA_DIR 환경변수 미설정 (.env 확인)")
    p = Path(raw).expanduser()
    if not p.is_dir():
        sys.exit(f"[error] MEMEX_DATA_DIR 가 디렉토리 아님: {p}")
    return p


def step_index(data_dir: Path, source: str) -> None:
    print("\n[1/2] 증분 인덱싱")
    conv_dir = data_dir / "conversations"
    db_dir = data_dir / "memory_db"
    if not conv_dir.is_dir():
        print(f"  [skip] {conv_dir} 없음 - 인덱싱 건너뜀")
        return
    add_new_files(str(conv_dir), str(db_dir), source=source)


def step_profile(data_dir: Path) -> None:
    print("\n[2/2] 프로필 재합성 (구독 claude CLI)")
    script = ROOT / "generate_profile.py"
    if not script.exists():
        sys.exit(f"[error] {script} 못 찾음")
    # generate_profile.py 는 ./conversations / ./memory 상대경로 사용
    # → cwd 를 데이터 디렉토리로 두면 그대로 정확한 위치에 입출력 됨
    res = subprocess.run([sys.executable, str(script)], cwd=str(data_dir))
    if res.returncode != 0:
        sys.exit(f"[error] generate_profile.py 실패 (exit {res.returncode})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Memex 월간 업데이트 (인덱싱 -> 프로필)"
    )
    ap.add_argument("--skip-index", action="store_true", help="증분 인덱싱 생략")
    ap.add_argument("--skip-profile", action="store_true",
                    help="프로필 재합성 생략 (claude CLI 안 돌림)")
    ap.add_argument("--source", default="claude",
                    help="신규 파일 source 메타데이터 (claude|claude_code|chatgpt|gemini, 기본 claude)")
    args = ap.parse_args()

    data_dir = _resolve_data_dir()
    print(f"[init] MEMEX_DATA_DIR = {data_dir}")

    if not args.skip_index:
        step_index(data_dir, args.source)
    if not args.skip_profile:
        step_profile(data_dir)

    print("\n[done] 월간 업데이트 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
