"""
rebuild_trail.py — processed/ 의 trail 영수증 전량 재집계 (명세 §6-6)

trail_state.json 이 유실·오염돼도 processed/ 의 t_*.json 원본에서 완전
재구성된다 — state 는 캐시고 원본은 우편이다 (reindex 와 같은 원리).

사용 (상시 PC):
    python scripts/rebuild_trail.py            # 재집계 후 교체
    python scripts/rebuild_trail.py --dry-run  # 결과만 출력, 교체 안 함

검증·집계 로직은 trail_store 가 단일 소스 (데몬과 공유). LLM 0, 검색 스택 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trail_store  # noqa: E402

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")
TRAIL_STATE = BASE_DIR / "trail" / "trail_state.json"
MANIFEST = BASE_DIR / "manifest.json"
MAX_BYTES = 16 * 1024


def rclone(args: list, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["rclone"] + args + ["--log-level", "ERROR"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    r = rclone(["lsjson", "--files-only", f"{REMOTE}/mailbox/processed"])
    if r.returncode != 0:
        print(f"processed/ 목록 실패: {r.stderr.strip()[:200]}")
        return 1
    tname_re = re.compile(r"^t_[A-Za-z0-9_.-]{1,80}\.json$")
    receipts = sorted((e for e in json.loads(r.stdout or "[]")
                       if tname_re.match(e.get("Name", ""))),
                      key=lambda e: e.get("Name", ""))
    print(f"processed/ 에서 trail 영수증 {len(receipts)}건 발견")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    conv_names = trail_store.conv_names_from_manifest(manifest)

    state = trail_store.new_state()
    ok = skipped = 0
    for e in receipts:
        name = e["Name"]
        if e.get("Size", 0) > MAX_BYTES:
            skipped += 1
            continue
        rr = rclone(["cat", f"{REMOTE}/mailbox/processed/{name}"])
        if rr.returncode != 0:
            print(f"  읽기 실패 (건너뜀): {name}")
            skipped += 1
            continue
        try:
            obj = json.loads(rr.stdout)
        except json.JSONDecodeError:
            skipped += 1                        # 격리됐던 오염 우편 — 재검증에서도 탈락
            continue
        spec, err = trail_store.validate_receipt(obj)
        if err:
            skipped += 1
            continue
        # last_used 는 원 처리 시점에 가장 가까운 ModTime 날짜로
        date = (e.get("ModTime") or "")[:10] or datetime.now().strftime("%Y-%m-%d")
        trail_store.apply_receipt(state, spec, conv_names, date=date,
                                  now_iso=datetime.now().isoformat(timespec="seconds"))
        ok += 1

    print(f"재집계 완료 — 수용 {ok}건 / 탈락 {skipped}건, "
          f"nodes {len(state['nodes'])}, edges {len(state['edges'])}")
    if args.dry_run:
        print(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True)[:2000])
        return 0
    trail_store.save_state(TRAIL_STATE, state)
    print(f"기록: {TRAIL_STATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
