"""
retag_provenance.py — 이미 색인된 청크의 source 메타데이터를 판정 목록대로 고친다.

왜 있나: 인덱서가 웹 대화를 전부 `claude` 로 태깅해 왔는데, 실제로는 ChatGPT 시절
대화가 섞여 있었다 (판정 경위는 index_incremental.source_for 주석 참조). 신규 색인은
source_for 가 목록을 보지만, **이미 들어간 청크는 다시 안 지나간다** — 파일명이
인덱스에 있으면 증분이 건너뛰기 때문이다. 이 스크립트가 그 소급분이다.

임베딩은 안 건드린다 — 본문이 그대로니 벡터도 그대로고, 메타데이터만 제자리
업데이트라 몇 초면 끝난다. 끝나면 index_as_of 를 전진시켜 사서의 BM25·컬렉션
캐시를 무효화한다 (coverage 가 새 분포를 보게).

    python scripts/retag_provenance.py --dry-run
    python scripts/retag_provenance.py
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb  # noqa: E402

from chroma_paging import get_all  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    prov = BASE_DIR / "config" / "provenance_chatgpt.txt"
    names = {unicodedata.normalize("NFC", l.strip())
             for l in prov.read_text(encoding="utf-8").splitlines() if l.strip()}
    print(f"[retag] 판정 목록 {len(names)}건 ({prov})")

    col = chromadb.PersistentClient(path=str(BASE_DIR / "memory_db")).get_collection("memory")
    got = get_all(col, ["metadatas"])
    todo_ids, todo_metas = [], []
    for cid, m in zip(got["ids"], got["metadatas"]):
        m = dict(m or {})
        sf = unicodedata.normalize("NFC", m.get("source_file") or "")
        if sf in names and m.get("source") != "chatgpt":
            m["source"] = "chatgpt"
            todo_ids.append(cid)
            todo_metas.append(m)
    print(f"[retag] 청크 {col.count():,} 중 갱신 대상 {len(todo_ids):,}")
    if args.dry_run or not todo_ids:
        print("[retag] " + ("dry-run — 변경 없음" if args.dry_run else "할 일 없음"))
        return 0

    for i in range(0, len(todo_ids), 500):
        col.update(ids=todo_ids[i:i + 500], metadatas=todo_metas[i:i + 500])
    print(f"[retag] 갱신 완료")

    # 사서가 다음 질의에서 캐시를 새로 만들게 — coverage 도 이 신호로 다시 계산된다
    state = BASE_DIR / "logs" / "index_state.json"
    state.write_text(json.dumps({
        "index_as_of": datetime.now().isoformat(timespec="minutes"),
        "chunks": col.count(),
    }, ensure_ascii=False), encoding="utf-8")
    print("[retag] index_as_of 전진 — 데몬이 다음 질의에서 새 분포를 본다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
