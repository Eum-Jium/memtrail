"""test_card_segments.py — 카드 구간 분할이 사서의 청크 규칙과 어긋나지 않는지 지킨다.

build_memory_cards.split_turn_chunks 는 memory_pipeline.parse_md_content 의 규칙을
재현한 것이다 (langchain import 가 16초라 재사용 대신 재현). 규칙이 갈라지면 카드의
턴 범위와 검색 히트의 turn_index 가 어긋나 **엉뚱한 조각이 요약으로 붙는다** —
조용히 틀리는 종류라 테스트로 고정해 둔다.

실행: python test_card_segments.py   (pytest 불필요, standalone assert)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from build_memory_cards import split_turn_chunks, plan_segments, SEGMENT_MAX_CHARS  # noqa: E402

U = "## 👤 User"
A = "## 🤖 Claude"


def test_pairs():
    c = f"{U}\n질문1\n{A}\n답변1\n{U}\n질문2\n{A}\n답변2"
    got = split_turn_chunks(c)
    assert [i for i, _ in got] == [0, 1], got
    assert "질문1" in got[0][1] and "답변1" in got[0][1]
    assert "질문2" in got[1][1] and "답변2" in got[1][1]


def test_consecutive_user_kept():
    """연속 User — 앞엣것이 단독 청크로 살아야 한다 (예전엔 통째로 유실됐다)."""
    c = f"{U}\n첫질문\n{U}\n둘째질문\n{A}\n답변"
    got = split_turn_chunks(c)
    assert len(got) == 2, got
    assert "첫질문" in got[0][1] and "답변" not in got[0][1]
    assert "둘째질문" in got[1][1] and "답변" in got[1][1]


def test_trailing_user_kept():
    """답변 없이 끝난 마지막 질문도 청크가 된다."""
    c = f"{U}\n질문\n{A}\n답변\n{U}\n답없는질문"
    got = split_turn_chunks(c)
    assert len(got) == 2 and "답없는질문" in got[1][1], got


def test_assistant_only():
    c = f"{A}\n답변만"
    got = split_turn_chunks(c)
    assert len(got) == 1 and "답변만" in got[0][1]


def test_short_is_one_segment():
    chunks = [(0, "a" * 100), (1, "b" * 100)]
    segs = plan_segments(chunks)
    assert len(segs) == 1 and segs[0][0] == 0 and segs[0][1] == 1


def test_long_splits_and_covers_all_turns():
    n = 40
    chunks = [(i, "x" * 2000) for i in range(n)]     # 80,000자
    segs = plan_segments(chunks)
    assert 2 <= len(segs) <= 4, len(segs)
    # 턴 범위가 빈틈·겹침 없이 전체를 덮어야 한다 — 아니면 히트가 어느 조각에도 안 걸린다
    assert segs[0][0] == 0 and segs[-1][1] == n - 1
    for a, b in zip(segs, segs[1:]):
        assert b[0] == a[1] + 1, (a[:2], b[:2])


def test_segment_cap():
    chunks = [(i, "x" * 5000) for i in range(100)]   # 500,000자
    segs = plan_segments(chunks)
    assert len(segs) <= 4, len(segs)
    assert segs[-1][1] == 99


def test_matches_memory_pipeline_on_real_files():
    """실파일로 사서 파서와 1:1 대조. langchain 없으면 건너뛴다."""
    try:
        from memory_pipeline import parse_md_content
    except Exception as e:                                    # noqa: BLE001
        return f"memory_pipeline 로드 불가 ({type(e).__name__})"
    # 데이터가 리포 밖인 구성(MEMEX_DATA_DIR)에서도 실파일 대조가 돌게 —
    # 리포 상대 경로만 보면 권장 구성에서 이 가드가 상시 skip 으로 무력화된다.
    import os
    base = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent))
    conv = base / "conversations"
    files = sorted(conv.glob("*.md"))[:40]
    if not files:
        return f"{conv} 에 md 없음"
    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        mine = split_turn_chunks(text)
        theirs = parse_md_content(text, p.stem, "claude")
        assert len(mine) == len(theirs), f"{p.name}: 청크 수 {len(mine)} != {len(theirs)}"
        for (idx, body), doc in zip(mine, theirs):
            assert idx == doc.metadata["turn_index"], f"{p.name}: turn_index 어긋남"
            assert body == doc.page_content, f"{p.name}: 청크 본문 어긋남 (턴 {idx})"
    print(f"  실파일 {len(files)}개 대조 일치")


if __name__ == "__main__":
    # 테스트가 문자열을 반환하면 "그 사유로 건너뛴 것" 으로 센다. 스킵을 ok 로
    # 뭉개면 신규 설치 검증에서 **보증이 실제보다 강해 보인다** — 실파일 대조는
    # 데이터 폴더가 비면 아무것도 안 하는데 겉으론 전부 통과로 읽혔다 (2026-08-15).
    passed, skipped = 0, []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            reason = fn()
            if isinstance(reason, str):
                skipped.append((name, reason))
                print(f"  skip {name} — {reason}")
            else:
                passed += 1
                print(f"  ok   {name}")
    total = passed + len(skipped)
    tail = f" (스킵 {len(skipped)}개 — 실제 검증은 {passed}/{total})" if skipped else ""
    print(f"\n[OK] 카드 구간 분할 테스트 통과 {passed}/{total}{tail}")
    if skipped:
        print("     스킵된 항목은 아무것도 검증하지 않았다:")
        for name, reason in skipped:
            print(f"       - {name}: {reason}")
