# 원본: https://github.com/nir3y/Memory-Layer (Apache-2.0) — 이 리포에서 수정했다.
"""
두 임베딩 모델 비교 테스트

paraphrase-multilingual-MiniLM-L12-v2 (현재)
vs
multilingual-e5-base (새 모델)

실행:
    python test_embeddings.py
"""

import time
import random
from pathlib import Path
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from memory_pipeline import parse_md_file

# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
CONVERSATIONS_FOLDER = "./conversations"
SAMPLE_FILES = 30        # 비교용 미니 DB에 쓸 MD 파일 수 (전체 빌드는 오래 걸려서 일부만)
TEST_QUERIES = [
    "업무 관련 질문",
    "AI 활용 방법",
    "코딩 오류 해결",
    "감정이나 일상 이야기",
    "데이터 분석",
]


# ─────────────────────────────────────────
# 모델 정의
# ─────────────────────────────────────────
def get_minilm():
    return HuggingFaceEmbeddings(
        model_name="paraphrase-multilingual-MiniLM-L12-v2",
    )


def get_e5():
    # e5 모델은 query/passage prefix 필수
    return HuggingFaceEmbeddings(
        model_name="intfloat/multilingual-e5-base",
        encode_kwargs={"normalize_embeddings": True},
        query_instruction="query: ",       # 검색 쿼리에 붙는 prefix
        embed_instruction="passage: ",     # 저장 문서에 붙는 prefix
    )


# ─────────────────────────────────────────
# 미니 ChromaDB 구축
# ─────────────────────────────────────────
def build_mini_db(embedding_model, db_path: str, label: str):
    md_files = sorted(Path(CONVERSATIONS_FOLDER).glob("*.md"))
    if not md_files:
        print(f"  ❌ '{CONVERSATIONS_FOLDER}' 폴더에 MD 파일 없음")
        return None

    # 랜덤 샘플링
    sampled = random.sample(md_files, min(SAMPLE_FILES, len(md_files)))

    all_docs = []
    for f in sampled:
        all_docs.extend(parse_md_file(str(f)))

    print(f"  [{label}] {len(sampled)}개 파일 → {len(all_docs)}개 대화쌍 파싱 완료")

    start = time.time()
    db = Chroma.from_documents(
        documents=all_docs,
        embedding=embedding_model,
        persist_directory=db_path,
        collection_name="test",
    )
    elapsed = time.time() - start
    print(f"  [{label}] 임베딩 완료: {elapsed:.1f}초")
    return db


# ─────────────────────────────────────────
# 쿼리 검색 비교
# ─────────────────────────────────────────
def compare_search(db_minilm, db_e5):
    print("\n" + "="*60)
    print("검색 결과 비교")
    print("="*60)

    for query in TEST_QUERIES:
        print(f"\n🔍 쿼리: '{query}'")
        print("-" * 50)

        for label, db in [("MiniLM", db_minilm), ("E5-base", db_e5)]:
            start = time.time()
            results = db.similarity_search_with_score(query, k=3)
            elapsed = time.time() - start

            print(f"\n  [{label}] ({elapsed:.2f}초)")
            for i, (doc, score) in enumerate(results, 1):
                date = doc.metadata.get("date", "?")
                topic = doc.metadata.get("topic", "")
                preview = doc.page_content[:80].replace("\n", " ")
                # 코사인 거리 → 유사도로 변환 (낮을수록 유사)
                print(f"    {i}. [{date}] {topic}")
                print(f"       유사도 점수: {score:.4f} | {preview}...")


# ─────────────────────────────────────────
# 속도 및 스펙 요약
# ─────────────────────────────────────────
def print_model_specs():
    print("\n" + "="*60)
    print("모델 스펙 비교")
    print("="*60)
    specs = [
        ("항목",                   "MiniLM-L12-v2",       "E5-base"),
        ("벡터 차원",              "384",                 "768"),
        ("모델 크기",              "~470MB",              "~1.1GB"),
        ("한국어 지원",            "✅",                  "✅"),
        ("query prefix 필요",      "❌",                  "✅ (query: )"),
        ("일반적 정확도",          "중간",                "높음"),
        ("임베딩 속도",            "빠름",                "느림"),
    ]
    col_w = [20, 22, 22]
    for row in specs:
        line = "  ".join(str(v).ljust(col_w[i]) for i, v in enumerate(row))
        print(line)


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────
if __name__ == "__main__":
    print_model_specs()

    print(f"\n{'='*60}")
    print(f"미니 DB 구축 (각 모델로 {SAMPLE_FILES}개 파일 임베딩)")
    print(f"{'='*60}")

    print("\n[1/2] MiniLM 모델 로딩 및 DB 구축...")
    minilm = get_minilm()
    db_minilm = build_mini_db(minilm, "./test_db_minilm", "MiniLM")

    print("\n[2/2] E5-base 모델 로딩 및 DB 구축...")
    e5 = get_e5()
    db_e5 = build_mini_db(e5, "./test_db_e5", "E5-base")

    if db_minilm and db_e5:
        compare_search(db_minilm, db_e5)

    print("\n\n테스트 완료!")
    print("test_db_minilm/, test_db_e5/ 폴더는 삭제해도 됩니다.")
