"""
memory_db 전체 재빌드 (conversations/*.md → 새 ChromaDB).

기존 DB 를 건드리지 않고 --out 디렉토리에 새로 만든다. 검증 후 사람이 교체.

왜 필요한가: add_new_files 는 이미 인덱싱된 파일을 건너뛰므로, 파서가 고쳐져도
옛 청크는 옛 파서 산출물 그대로 남는다. 파서 수정을 소급 적용하려면 재빌드뿐.

source 메타는 파일명으로 정한다 (`__cc_`/`__mr_` → claude_code, `__cw_` → cowork, 그 외 claude).
현행 DB 에서 이 규칙과 불일치하는 파일은 0개였고, source 가 아예 없는 옛 청크
16,547개도 이 재빌드로 태깅된다.

사용법:
  python scripts/rebuild_memory_db.py [--md $MEMEX_DATA_DIR/conversations]
                                      [--out $MEMEX_DATA_DIR/memory_db.new]
                                      [--batch 256] [--dry-run]
"""
import argparse
import os
import collections
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory_pipeline import E5Embeddings, parse_md_file  # noqa: E402
from chroma_paging import get_all  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def source_for(name: str) -> str:
    if "__cw_" in name:
        return "cowork"
    return "claude_code" if ("__cc_" in name or "__mr_" in name) else "claude"


def rebuild(md_folder: str, out_dir: str, batch: int, dry_run: bool, resume: bool = False) -> int:
    md_files = sorted(Path(md_folder).glob("*.md"))
    if not md_files:
        raise SystemExit(f"md 파일 없음: {md_folder}")

    print(f"[1/3] 파싱 — {len(md_files)}개 파일")
    docs, per_source = [], {}
    for i, p in enumerate(md_files, 1):
        src = source_for(p.name)
        d = parse_md_file(str(p), source=src)
        docs.extend(d)
        per_source[src] = per_source.get(src, 0) + len(d)
        if i % 200 == 0:
            print(f"  {i}/{len(md_files)} (누적 {len(docs)} 청크)")
    print(f"  [done] {len(docs)} 청크 | source 분포: {per_source}")

    if dry_run:
        print("[dry-run] 임베딩·저장 생략")
        return len(docs)

    out = Path(out_dir)
    if out.exists() and not resume:
        print(f"[warn] {out} 이미 있음 → 삭제 후 재생성")
        shutil.rmtree(out)

    print(f"[2/3] 임베딩 + 저장 → {out}  (batch={batch}, resume={resume})")
    from langchain_chroma import Chroma

    embeddings = E5Embeddings(model_name="intfloat/multilingual-e5-base")
    db = Chroma(
        persist_directory=str(out),
        embedding_function=embeddings,
        collection_name="memory",
    )

    if resume:
        # 중단 지점의 배치는 파일 경계를 가로질러 끊겼을 수 있다. 파일별로
        # "DB 에 있는 청크 수 == 파싱한 청크 수" 인 것만 완료로 보고 건너뛴다.
        have = collections.Counter(
            m["source_file"]
            for m in get_all(db, ["metadatas"])["metadatas"]
            if m.get("source_file")
        )
        want = collections.Counter(d.metadata["source_file"] for d in docs)
        done_files = {f for f, n in want.items() if have.get(f) == n}
        partial = {f for f in have if f not in done_files}
        if partial:
            print(f"  [resume] 덜 들어간 파일 {len(partial)}개 → 해당 청크 삭제 후 재삽입")
            db._collection.delete(where={"source_file": {"$in": sorted(partial)}})
        docs = [d for d in docs if d.metadata["source_file"] not in done_files]
        print(f"  [resume] 완료 파일 {len(done_files)}개 건너뜀 → 남은 {len(docs)} 청크")

    t0 = time.time()
    for i in range(0, len(docs), batch):
        chunk = docs[i:i + batch]
        db.add_documents(chunk)
        done = min(i + batch, len(docs))
        el = time.time() - t0
        rate = done / el if el else 0
        eta = (len(docs) - done) / rate if rate else 0
        print(f"  {done}/{len(docs)}  {rate:.1f} chunk/s  ETA {eta / 60:.1f}분", flush=True)

    print(f"[3/3] 검증 — 컬렉션 {db._collection.count()} 청크")
    return len(docs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/conversations")
    ap.add_argument("--out", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/memory_db.new")
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true", help="기존 --out 을 이어받아 남은 파일만 추가")
    a = ap.parse_args()
    rebuild(a.md, a.out, a.batch, a.dry_run, a.resume)
