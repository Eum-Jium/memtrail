"""
재빌드된 memory_db 검증 — 기존 DB 와 나란히 비교한다.

교체 전에 돌려서 "새 DB 가 옛 DB 를 온전히 대체하는가" 를 확인하는 용도.
읽기만 하고 아무것도 바꾸지 않는다.

사용법:
  python scripts/verify_memory_db.py [--old $MEMEX_DATA_DIR/memory_db]
                                     [--new $MEMEX_DATA_DIR/memory_db.new]
                                     [--md $MEMEX_DATA_DIR/conversations]
"""
import argparse
import os
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from chroma_paging import get_all  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load(path: str):
    col = chromadb.PersistentClient(path=path).get_collection("memory")
    got = get_all(col, ["metadatas", "documents"])
    return col, got


def summarize(name: str, col, got):
    metas = got["metadatas"]
    files = {m.get("source_file") for m in metas if m.get("source_file")}
    srcs = collections.Counter(m.get("source") for m in metas)
    chars = sum(len(d) for d in got["documents"])
    print(f"[{name}] 청크 {col.count():,} | 파일 {len(files):,} | 문자 {chars:,}")
    print(f"         source: {dict(srcs)}")
    return files, chars


def main(old_p: str, new_p: str, md: str) -> int:
    ok = True
    print("=" * 62)
    old_col, old_got = load(old_p)
    old_files, old_chars = summarize("OLD", old_col, old_got)
    new_col, new_got = load(new_p)
    new_files, new_chars = summarize("NEW", new_col, new_got)
    print("=" * 62)

    md_files = {p.name for p in Path(md).glob("*.md")}

    # 1) 폴더의 모든 md 가 새 DB 에 있는가
    missing = md_files - new_files
    print(f"[1] md 폴더 {len(md_files):,}개 중 새 DB 에 없는 파일: {len(missing)}")
    for f in sorted(missing)[:10]:
        print(f"      - {f}")
    ok &= not missing

    # 2) 옛 DB 에만 있던 파일 (= 유실 후보)
    dropped = old_files - new_files
    print(f"[2] 옛 DB 에만 있는 파일: {len(dropped)}")
    for f in sorted(dropped)[:10]:
        gone = "  (md 도 없음 → 의도된 삭제)" if f not in md_files else "  ⚠ md 는 있는데 빠짐"
        print(f"      - {f}{gone}")
    ok &= not (dropped & md_files)

    # 3) source 태그 누락
    no_src = sum(1 for m in new_got["metadatas"] if not m.get("source"))
    print(f"[3] source 태그 없는 청크: {no_src}")
    ok &= no_src == 0

    # 4) 커버리지 (문자)
    delta = new_chars - old_chars
    pct = (delta / old_chars * 100) if old_chars else 0
    print(f"[4] 문자 커버리지: {old_chars:,} → {new_chars:,} ({delta:+,}, {pct:+.1f}%)")
    ok &= delta >= 0

    # 5) 실제 검색이 되는가 (임베딩 정상성)
    print("[5] 샘플 검색")
    from langchain_chroma import Chroma  # noqa: E402

    from memory_pipeline import E5Embeddings  # noqa: E402

    db = Chroma(
        persist_directory=new_p,
        embedding_function=E5Embeddings(model_name="intfloat/multilingual-e5-base"),
        collection_name="memory",
    )
    for q in ["ChromaDB 인덱싱", "임베딩 모델", "우편함 프로토콜"]:
        hits = db.similarity_search(q, k=2)
        print(f"    '{q}' → {len(hits)}건")
        for h in hits:
            print(f"        {h.metadata.get('date')} {str(h.metadata.get('topic'))[:40]}")
        ok &= len(hits) > 0

    print("=" * 62)
    print("검증 통과 ✅" if ok else "검증 실패 ❌ — 교체하지 말 것")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/memory_db")
    ap.add_argument("--new", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/memory_db.new")
    ap.add_argument("--md", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/conversations")
    a = ap.parse_args()
    sys.exit(main(a.old, a.new, a.md))
