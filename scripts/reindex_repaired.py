"""
복구된 대화의 stale 청크 정리 — 파일명 기준 증분 인덱싱의 사각지대를 메운다.

`memory_pipeline.add_new_files` 는 "DB 에 없는 **파일명**" 만 추가한다. 그래서
파일 내용이 바뀌어도 (예: claude.ai 재수집으로 유실 블록을 복구) 파일명이 같으면
영영 재인덱싱되지 않고 옛 내용의 청크가 검색에 계속 잡힌다.

이 스크립트는 두 종류의 죽은 청크를 지운다:
  1) orphan  — source_file 이 더 이상 conversations/ 에 없는 청크
                (중복본 정리·파일명 변경으로 생김)
  2) stale   — --files 로 지정한 파일들의 기존 청크 (내용이 바뀐 파일)

지운 뒤 monthly_update.py 를 돌리면 해당 파일들이 "신규" 로 잡혀 재인덱싱된다.

사용법:
  python scripts/reindex_repaired.py --dry-run
  python scripts/reindex_repaired.py --files-from repaired.txt
  python scripts/reindex_repaired.py            # orphan 만 정리
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from chroma_paging import get_all  # noqa: E402

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(BASE_DIR / "memory_db"))
    ap.add_argument("--md", default=str(BASE_DIR / "conversations"))
    ap.add_argument("--files-from", help="stale 처리할 파일명 목록 (한 줄에 하나)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    col = chromadb.PersistentClient(path=args.db).get_collection("memory")
    got = get_all(col, ["metadatas"])
    ids, metas = got["ids"], got["metadatas"]

    on_disk = {p.name for p in Path(args.md).glob("*.md")}
    if not on_disk:
        # 빈 목록이면 아래 orphan 판정이 전 청크에 참이 되어 인덱스가 통째로
        # 지워진다 — --md 경로 오설정(다른 기기·데이터 이사)일 가능성이 압도적.
        raise SystemExit(f"[abort] {args.md} 에 md 가 0개 — 경로 확인 (전량 삭제 방지)")
    stale_names = set()
    if args.files_from:
        # 경로로 적혀 있어도 받아준다 (unpack --changed-list 출력을 그대로 먹이기 위함)
        stale_names = {Path(l.strip()).name
                       for l in Path(args.files_from).read_text(encoding="utf-8").splitlines()
                       if l.strip()}

    orphan_ids, stale_ids = [], []
    orphan_files, stale_files = set(), set()
    for cid, m in zip(ids, metas):
        f = (m or {}).get("source_file")
        if not f:
            continue
        if f not in on_disk:
            orphan_ids.append(cid); orphan_files.add(f)
        elif f in stale_names:
            stale_ids.append(cid); stale_files.add(f)

    print(f"[before] 청크 {col.count():,} / 디스크 md {len(on_disk):,}")
    print(f"  orphan (파일 없음)  : 청크 {len(orphan_ids):,} / 파일 {len(orphan_files):,}")
    print(f"  stale  (내용 바뀜)  : 청크 {len(stale_ids):,} / 파일 {len(stale_files):,}")

    victims = orphan_ids + stale_ids
    if args.dry_run:
        print("[dry-run] 삭제 안 함")
        return 0
    if not victims:
        print("[done] 지울 것 없음")
        return 0

    for i in range(0, len(victims), 500):          # 대량 삭제는 배치로
        col.delete(ids=victims[i:i + 500])
    print(f"[after]  청크 {col.count():,} (삭제 {len(victims):,})")

    # 인덱스가 바뀌었으니 스탬프를 전진시킨다 — 사서 데몬은 index_as_of 로 컬렉션·BM25
    # 캐시를 무효화하므로, 안 올리면 지운 청크를 계속 검색 결과로 돌려준다.
    state = BASE_DIR / "logs" / "index_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "index_as_of": datetime.now().isoformat(timespec="minutes"),
        "chunks": col.count(),
    }, ensure_ascii=False), encoding="utf-8")
    print(f"[state]  index_as_of 갱신 — 사서가 다음 질의에서 캐시를 새로 만든다")
    print("→ 이제 monthly_update.py --skip-profile 로 재인덱싱하면 된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
