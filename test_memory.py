# 원본: https://github.com/nir3y/Memory-Layer (Apache-2.0) — 이 리포에서 수정했다.
"""
메모리 레이어 대화형 테스트 스크립트
실행: python test_memory.py
"""
from memory_pipeline import search_memory

print("=" * 60)
print("  메모리 검색 테스트  (종료: 'q' 입력)")
print("=" * 60)

while True:
    query = input("\n🔍 검색어 입력: ").strip()

    if query.lower() == "q":
        print("종료합니다.")
        break

    if not query:
        continue

    results = search_memory(query, k=3)

    if not results:
        print("  관련 기억 없음")
        continue

    print(f"\n  📌 '{query}' 관련 기억 {len(results)}개 발견\n")
    for i, doc in enumerate(results, 1):
        source = doc.metadata.get("source_file", "알 수 없음")
        date = doc.metadata.get("date", "날짜 없음")
        preview = doc.page_content[:200].replace("\n", " ")
        print(f"  [{i}] 📄 {source}  ({date})")
        print(f"       {preview}...")
        print()
