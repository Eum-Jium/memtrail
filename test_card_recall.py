"""test_card_recall.py — 카드 회상어 다리가 '다리' 로 남는지 지킨다.

카드마다 LLM 이 만드는 `- 회상어:` 는 원문에 한 번도 안 나오는 별칭·통칭을 담는다.
그 어휘로도 원문이 잡히게 하려고 카드 블록당 다리 청크를 하나 만든다. 세 가지가
어긋나면 조용히 틀린다 — 그래서 테스트로 고정한다:

  1) 좌표가 카드가 아니라 **원본 대화**여야 한다. source_file 이 카드 파일명이면
     사서가 raw/conversations/<이름> 으로 manifest 를 못 찾아 file_id 가 없는,
     열 수 없는 히트가 나간다.
  2) 요약 본문(`결정된 것`·`미결`)은 **들어가면 안 된다**. 정본은 원문 대화이고
     정리된 결론은 파생물이라, 파생물이 원문과 랭킹을 다투게 두지 않는다.
  3) turn_from/turn_to 가 카드 헤더의 턴 범위와 같아야 한다. 사서의
     card_summary_for 가 turn_index 로 조각을 고르므로, 어긋나면 히트한 대목과
     다른 조각이 요약으로 붙는다.

실행: python test_card_recall.py   (pytest 불필요, standalone assert)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from memory_pipeline import (  # noqa: E402
    card_recall_blocks,
    card_signature,
    card_title,
    date_topic_from_stem,
    parse_card_file,
)

CONV = "2026-01-02_C--memex_Pi와_맥미니_설정_도움__cc_0a1b2c3d.md"

TWO_BLOCKS = """## 2026-01-02 | 인프라이전·원격접속 (턴 0-20)
- 결정된 것: Pi 공유기 이전 · 맥미니 무인서버화 · Tailscale 기기 연결
- 미결/보류: claude.ai 수집 맥미니 완전 이식
- 회상어: Tailscale, 원격접속, 홈서버, Pi
- 화두태그: 인프라, 자동화
- 원본 경로: conversations/{conv}
- 카드생성: 2026-01-15

## 2026-01-02 | memex·루틴 통합 (턴 21-34)
- 결정된 것: 주간 루틴을 매일로 통합
- 회상어: memex 동기화, 코워크
- 화두태그: 기억시스템
- 원본 경로: conversations/{conv}
- 카드생성: 2026-01-15
""".format(conv=CONV)

STUB = """## 2026-01-02 | Pi와 맥미니 설정 도움
- 내용: Pi 를 공유기 뒤로 옮기는 방법
- 원본 경로: conversations/{conv}
- (자동 스텁 — LLM 요약 아님)
""".format(conv=CONV)


def _write(tmp: Path, name: str, text: str) -> Path:
    p = tmp / name
    p.write_text(text, encoding="utf-8")
    return p


def _tmpdir() -> Path:
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="cardrecall_"))
    return d


def test_coordinate_is_the_conversation():
    """좌표는 원문이다 — 카드 파일명이 source_file 로 새면 열 수 없는 히트가 된다."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", TWO_BLOCKS)
    docs = parse_card_file(str(cp), CONV, source="claude_code")
    assert docs, "다리 청크가 하나도 안 나왔다"
    for d in docs:
        assert d.metadata["source_file"] == CONV, d.metadata["source_file"]
        assert d.metadata["kind"] == "card"
        assert d.metadata["card_file"] == Path(CONV).stem + ".md"


def test_blocks_and_turn_spans():
    """블록마다 청크 하나, 턴 범위는 헤더 그대로."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", TWO_BLOCKS)
    docs = parse_card_file(str(cp), CONV, source="claude_code")
    assert len(docs) == 2, len(docs)
    assert (docs[0].metadata["turn_index"], docs[0].metadata["turn_to"]) == (0, 20)
    assert (docs[1].metadata["turn_index"], docs[1].metadata["turn_to"]) == (21, 34)


def test_summary_body_excluded():
    """요약 본문은 안 들어간다 — 파생물이 원문과 랭킹을 다투지 않게."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", TWO_BLOCKS)
    docs = parse_card_file(str(cp), CONV, source="claude_code")
    joined = "\n".join(d.page_content for d in docs)
    assert "Tailscale" in joined and "코워크" in joined       # 회상어는 들어간다
    assert "인프라이전·원격접속" in joined                     # 카드 제목도 들어간다
    assert "공유기 이전" not in joined, joined                 # 결정된 것은 빠진다
    assert "claude.ai 수집" not in joined, joined              # 미결도 빠진다
    assert "원본 경로" not in joined and "카드생성" not in joined


def test_stub_card_yields_nothing():
    """회상 어휘가 없는 자동 스텁은 다리가 못 된다 — 원문에 이미 있는 말뿐이다."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", STUB)
    assert card_recall_blocks(STUB) == []
    assert parse_card_file(str(cp), CONV, source="claude_code") == []


def test_span_less_card_starts_at_zero():
    """턴 범위 표기가 없는 옛 카드는 0 에서 시작한다 (card_summary_for 폴백과 같다)."""
    text = TWO_BLOCKS.split("\n\n")[0].replace(" (턴 0-20)", "")
    blocks = card_recall_blocks(text)
    assert len(blocks) == 1, blocks
    assert blocks[0]["turn_from"] == 0 and blocks[0]["turn_to"] is None


def test_signature_tracks_recall_vocabulary():
    """지문은 회상 어휘에만 반응한다 — 카드가 재생성돼도 어휘가 같으면 재인덱싱 안 한다."""
    base = card_signature(card_recall_blocks(TWO_BLOCKS))
    same_day_regen = TWO_BLOCKS.replace("카드생성: 2026-01-15", "카드생성: 2026-01-20")
    assert card_signature(card_recall_blocks(same_day_regen)) == base
    reworded = TWO_BLOCKS.replace("결정된 것: Pi 공유기 이전", "결정된 것: 완전히 다른 요약")
    assert card_signature(card_recall_blocks(reworded)) == base
    new_vocab = TWO_BLOCKS.replace("회상어: Tailscale, 원격접속, 홈서버, Pi",
                                   "회상어: 테일스케일, 집서버")
    assert card_signature(card_recall_blocks(new_vocab)) != base


TITLED = "# 표제: Pi 홈서버 이전 — 맥미니 무인서버화 완료\n\n" + TWO_BLOCKS
RETITLED = ("# 표제: 새 이름\n"
            "# 옛 표제(2026-01-25): ~~Pi 홈서버 이전 — 맥미니 무인서버화 완료~~\n\n"
            + TWO_BLOCKS)


def test_title_does_not_disturb_blocks():
    """표제 줄이 블록 파싱을 흔들면 안 된다 — `# ` 라 `## ` 헤더에 안 걸린다."""
    assert len(card_recall_blocks(TITLED)) == len(card_recall_blocks(TWO_BLOCKS)) == 2
    assert card_recall_blocks(TITLED)[0]["turn_from"] == 0
    assert card_recall_blocks(TITLED)[0]["title"] == "인프라이전·원격접속"


def test_title_parsing():
    """활성 표제만 읽는다. 취소선 그은 지난 표제는 건너뛰고, 없으면 빈 문자열."""
    assert card_title(TITLED) == "Pi 홈서버 이전 — 맥미니 무인서버화 완료"
    assert card_title(RETITLED) == "새 이름"
    assert card_title(TWO_BLOCKS) == ""
    assert card_title(STUB) == ""


def test_title_rides_every_bridge_chunk():
    """표제는 블록마다 실린다 — 표제로 물었는데 특정 구간만 안 걸리면 안 된다."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", TITLED)
    docs = parse_card_file(str(cp), CONV, source="claude_code")
    assert len(docs) == 2
    for d in docs:
        assert "[표제] Pi 홈서버 이전" in d.page_content, d.page_content
        assert d.metadata["card_title"] == "Pi 홈서버 이전 — 맥미니 무인서버화 완료"
    # 표제가 붙어도 요약 본문은 여전히 안 들어간다 (다리는 다리로 남는다)
    joined = "\n".join(d.page_content for d in docs)
    assert "공유기 이전" not in joined and "claude.ai 수집" not in joined


def test_signature_reacts_to_title_but_stays_stable_without_one():
    """표제가 바뀌면 재인덱싱돼야 한다. 표제 없는 카드의 지문은 도입 전과 같아야 한다.

    후자를 안 지키면 표제 이관 도중 **아직 표제를 못 받은 카드 전부**가 지문
    변경으로 판정돼 공연히 다시 임베딩된다.
    """
    base = card_signature(card_recall_blocks(TWO_BLOCKS))
    assert card_signature(card_recall_blocks(TWO_BLOCKS), "") == base
    titled = card_signature(card_recall_blocks(TITLED), card_title(TITLED))
    assert titled != base
    assert card_signature(card_recall_blocks(RETITLED), card_title(RETITLED)) != titled


def test_date_topic_matches_conversation_rule():
    """날짜·주제는 대화 파싱과 같은 규칙 — 같은 대화가 두 이름으로 보이면 안 된다."""
    tmp = _tmpdir()
    cp = _write(tmp, Path(CONV).stem + ".md", TWO_BLOCKS)
    docs = parse_card_file(str(cp), CONV, source="claude_code")
    date, topic = date_topic_from_stem(Path(CONV).stem)
    assert docs[0].metadata["date"] == date == "2026-01-02"
    assert docs[0].metadata["topic"] == topic
    assert docs[0].metadata["date_int"] == 20260102


def test_anchor_does_not_pose_as_a_quote():
    """사서의 anchor 가 카드 히트를 원문 턴 인용처럼 내보내면 안 된다."""
    try:
        from mailbox_daemon import anchor_for
    except Exception as e:                      # 데몬 의존성이 없는 기기
        return f"mailbox_daemon import 실패 ({type(e).__name__})"
    card = anchor_for({"kind": "card", "turn_index": 0, "turn_to": 20},
                      "[회상어] Tailscale, 원격접속")
    assert card.startswith("카드 회상"), card
    assert "턴 0-20" in card, card
    turn = anchor_for({"turn_index": 3, "user_text": "이거 어떻게 해?"}, "본문")
    assert turn.startswith("턴 3:"), turn


def test_real_cards_parse():
    """실제 카드 폴더 대조 — 데이터 폴더가 있는 기기에서만 돈다."""
    base = Path(os.environ.get("MEMEX_DATA_DIR", ""))
    cards = base / "cards"
    convs = base / "conversations"
    if not cards.is_dir() or not convs.is_dir():
        return f"데이터 폴더 없음 (MEMEX_DATA_DIR={base or '미설정'})"
    import unicodedata
    on_disk = {unicodedata.normalize("NFC", p.name) for p in convs.glob("*.md")}
    files = sorted(cards.glob("*/*.md"))
    if not files:
        return "cards/ 가 비어 있음"
    bridged = orphan = 0
    for cp in files:
        conv = unicodedata.normalize("NFC", cp.stem + ".md")
        if conv not in on_disk:
            orphan += 1
            continue
        docs = parse_card_file(str(cp), conv, source="claude")
        for d in docs:
            assert d.metadata["source_file"] in on_disk
            assert d.page_content.strip()
        bridged += bool(docs)
    assert bridged, f"카드 {len(files)}장 중 다리가 된 게 하나도 없다"
    print(f"       (실파일 {len(files)}장 → 다리 {bridged}장, 원문 없는 카드 {orphan}장)")


if __name__ == "__main__":
    # 테스트가 문자열을 반환하면 "그 사유로 건너뛴 것" 으로 센다 (test_card_segments
    # 와 같은 규칙) — 스킵을 통과로 뭉개면 보증이 실제보다 강해 보인다.
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
    print(f"\n[OK] 카드 회상어 다리 테스트 통과 {passed}/{total}{tail}")
    if skipped:
        print("     스킵된 항목은 아무것도 검증하지 않았다:")
        for name, reason in skipped:
            print(f"       - {name}: {reason}")
