"""test_segment_title.py — 구간 표제 층 (명세 v5).

주제가 여럿인 대화에서 히트가 **어느 대목인지** 말하는 층이다. 여기서 잡는 실패는
2026-08-31 실측의 구조 그대로다: 이름표는 대화 한 건의 표제인데 요약은 히트가 속한
**다른 구간**의 것이라, 둘이 어긋나 읽는 쪽이 맞는 히트를 버렸다. 정작 답은 같은
대화의 또 다른 구간에 있었고 재질의 한 번을 더 써서야 닿았다.

  1) 사서가 히트한 턴의 구간 제목을 싣는가 (요약과 같은 블록인가)
  2) 나머지 구간을 목차로 실어 "다른 데 있다" 를 그 자리에서 보이게 하는가
  3) 카드 머리 표식(`# 구간표제:`)이 본문·요약으로 새지 않는가
  4) 구간 제목 재작성이 날짜·턴 범위·본문을 보존하는가, 응답이 밀리면 통째로 버리는가

실행: python -m unittest test_segment_title
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mailbox_daemon as md  # noqa: E402

E = chr(10)          # 카드 본문을 조립할 때 쓰는 개행

# 실측 사례의 **구조**를 옮긴 카드 (블록 3개, 표제 하나, 히트는 마지막 블록).
# 사안은 뺀다 — 실측에 필요한 건 구조지 무엇에 관한 일이었나가 아니다
# (scripts/pii_gate.sh 가 이 원칙을 검사한다).
CARD = """# 표제: 도구 도입 검토 및 판별 체계 점검

## 2026-01-26 | 도입 경로·비용 검토 (턴 0-23)
- 결정된 것: 후보 셋을 비용·유지보수로 좁혀 둘로 추렸다.
- 미결/보류: 무엇을 기준으로 최종안을 고를지 미정.
- 회상어: 도입 경로, 비용 비교, 후보 축소
- 원본 경로: conversations/2026-01-26_도구 도입 검토.md

## 2026-01-26 | 비교 끝 → 도입 보류 (턴 24-44)
- 결정된 것: 비교 기준을 못 넘었다는 이유로 "도입 보류".
- 미결/보류: 추후 재검토.
- 회상어: 비교 기준, 유지보수 부담, 도입 보류
- 원본 경로: conversations/2026-01-26_도구 도입 검토.md

## 2026-01-26 | 턴 종료 실패 규명 (턴 45-66)
- 결정된 것: 턴 종료에 실패해 다음 발화를 이어 쓰는 버그 확인, 가설 4개.
- 미결/보류: 원인 판별 대기.
- 회상어: 턴 종료 실패, 역할 표지 노출, 메타 대화
- 원본 경로: conversations/2026-01-26_도구 도입 검토.md
"""

CONV = "2026-01-26_도구 도입 검토.md"
DATE = "2026-01-26"


class _Cards:
    """CARDS_DIR 를 임시 폴더로 갈아끼운다 (카드 캐시도 비운다)."""

    def __init__(self, text=CARD, name=CONV):
        self.text, self.name = text, name

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        month = self.dir / DATE[:7]
        month.mkdir(parents=True)
        (month / f"{Path(self.name).stem}.md").write_text(self.text, encoding="utf-8")
        self.saved = md.CARDS_DIR
        md.CARDS_DIR = self.dir
        md._CARD_CACHE.clear()
        return self

    def __exit__(self, *exc):
        md.CARDS_DIR = self.saved
        md._CARD_CACHE.clear()
        shutil.rmtree(self.dir, ignore_errors=True)


def _hit_for(turn: int) -> dict:
    """원문 청크 히트 하나를 만든다 (다리 청크가 아니라 흔한 경로)."""
    meta = {"source_file": CONV, "date": DATE, "turn_index": turn,
            "source": "claude", "topic": "도구 도입 검토",
            "user_text": f"턴 {turn} 발화"}
    return md._hit({"meta": meta, "content": "[User] …", "dense": None,
                    "match": "latest"}, CONV, {})


class SegmentOnHit(unittest.TestCase):
    def test_title_and_summary_come_from_the_same_block(self):
        """실패의 핵심 — 이름표와 요약이 다른 구간을 가리키면 히트를 버린다."""
        with _Cards():
            h = _hit_for(48)
        self.assertEqual(h["segment_title"], "턴 종료 실패 규명")
        self.assertEqual(h["segment_turns"], "45-66")
        self.assertIn("턴 종료에 실패해", h["card_summary"])
        # 대화 표제는 그대로 있다 — 구간 제목이 대체하는 게 아니라 층이 다르다
        self.assertIn("판별 체계 점검", h["title"])

    def test_other_segments_point_to_where_the_answer_is(self):
        """답이 같은 대화 **다른 구간**에 있을 때 재질의 없이 보이게 한다."""
        with _Cards():
            h = _hit_for(48)
        others = {o["title"]: o["turns"] for o in h["other_segments"]}
        self.assertEqual(others,
                         {"도입 경로·비용 검토": "0-23",
                          "비교 끝 → 도입 보류": "24-44"})

    def test_hit_in_another_block_flips_the_segment(self):
        with _Cards():
            h = _hit_for(38)
        self.assertEqual(h["segment_title"], "비교 끝 → 도입 보류")
        self.assertIn("도입 보류", h["card_summary"])
        self.assertNotIn("비교 끝 → 도입 보류",
                         [o["title"] for o in h["other_segments"]])

    def test_single_block_card_carries_no_segment_fields(self):
        """구간이 하나면 '어느 구간인가' 라는 질문이 없다 — 답장만 커진다."""
        one = ("# 표제: 한 가지 이야기\n\n## 2026-01-26 | 단일 주제\n"
               "- 결정된 것: 하나뿐이다.\n- 회상어: 하나\n")
        with _Cards(text=one):
            h = _hit_for(3)
        self.assertNotIn("segment_title", h)
        self.assertNotIn("other_segments", h)
        self.assertEqual(h["summary_kind"], "card")


class HeadLineIsolation(unittest.TestCase):
    """머리 표식이 본문으로 새면 첫 구간 요약 자리에 표식이 실린다 (v1 §8-1 ②)."""

    def test_segment_mark_never_reaches_summary(self):
        marked = CARD.replace("# 표제:", "# 구간표제: 2026-08-31\n# 표제:")
        with _Cards(text=marked):
            h = _hit_for(2)
        self.assertNotIn("구간표제", h["card_summary"])
        self.assertNotIn("구간표제", h["segment_title"])
        self.assertEqual(h["segment_title"], "도입 경로·비용 검토")

    def test_blocks_survive_unknown_head_lines(self):
        with _Cards(text="# 새 표식: x\n" + CARD):
            blocks = md.card_blocks_for(CONV, DATE)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(blocks[0]["body"][0].startswith("- 결정된 것"))


class Rewrite(unittest.TestCase):
    """헤더 제목만 갈아 끼운다 — 날짜·턴 범위·본문·회상어는 불변."""

    def setUp(self):
        import build_memory_cards as bc
        self.bc = bc

    def test_rewrite_preserves_date_span_and_body(self):
        new = self.bc.rewrite_segment_titles(
            CARD, ["가1", "나2", "다3"])
        self.assertIn("## 2026-01-26 | 가1 (턴 0-23)", new)
        self.assertIn("## 2026-01-26 | 나2 (턴 24-44)", new)
        self.assertIn("## 2026-01-26 | 다3 (턴 45-66)", new)
        self.assertIn("- 회상어: 비교 기준, 유지보수 부담, 도입 보류", new)
        self.assertIn("# 표제: 도구 도입 검토", new)
        self.assertEqual(new.count("## 2026-01-26"), 3)

    def test_parse_requires_exact_count(self):
        """한 줄이 밀리면 **다른 구간의 이름**이 붙는다 — 부분 적용 금지."""
        self.assertEqual(self.bc.parse_segment_titles("1. 하나\n2. 둘\n", 3), [])
        self.assertEqual(self.bc.parse_segment_titles("1. 하나\n2. 둘\n3. 셋\n", 3),
                         ["하나", "둘", "셋"])

    def test_parse_tolerates_chatter_around_numbered_lines(self):
        """머리말·맺음말이 딸려와도 번호가 정렬 근거라 안전하게 흘린다.

        실측 (2026-09-01): 한 카드가 두 번 연속 실패했는데 원인이 여분의 줄이었다.
        """
        raw = "네, 각 구간 제목입니다:" + E + "1. 하나" + E + "2. 둘" + E + "이상입니다."
        self.assertEqual(self.bc.parse_segment_titles(raw, 2), ["하나", "둘"])

    def test_parse_rejects_scrambled_numbering(self):
        """번호를 매길 줄 아는 응답이 순서를 틀렸으면 그 순서를 믿을 근거가 없다."""
        self.assertEqual(self.bc.parse_segment_titles("2. 둘" + E + "1. 하나", 2), [])

    def test_parse_rejects_header_echo(self):
        raw = "1. ## 2026-01-26 | 버그 (턴 45-66)\n2. 둘\n"
        self.assertEqual(self.bc.parse_segment_titles(raw, 2), [])

    def test_parse_strips_numbering_and_quotes(self):
        self.assertEqual(
            self.bc.parse_segment_titles('1. "비교 끝 → 도입 보류"\n', 1),
            ["비교 끝 → 도입 보류"])

    def test_marked_cards_are_skipped(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        month = base / "2026-08"
        month.mkdir(parents=True)
        (month / "done.md").write_text("# 구간표제: 2026-08-31\n" + CARD, encoding="utf-8")
        (month / "todo.md").write_text(CARD, encoding="utf-8")
        (month / "single.md").write_text("## 2026-01-26 | 하나\n- 회상어: x\n",
                                         encoding="utf-8")
        saved, self.bc.CARDS_DIR = self.bc.CARDS_DIR, base
        saved_call = self.bc.call_claude
        self.addCleanup(setattr, self.bc, "call_claude", saved_call)
        try:
            called = []
            self.bc.call_claude = lambda *a, **k: called.append(1) or ""
            self.bc.backfill_segment_titles()
        finally:
            self.bc.CARDS_DIR = saved
        self.assertEqual(len(called), 1, "표식 있는 카드나 단일 블록 카드를 다시 불렀다")


class Retitle(unittest.TestCase):
    """표제 재생성 — 고정 원칙의 예외라 대상을 명시해야만 돈다 (명세 v1 §6)."""

    def setUp(self):
        import build_memory_cards as bc
        self.bc = bc

    def test_split_head_keeps_other_markers(self):
        """구간표제 표식을 걷어내면 그 카드가 다음 실행에서 통째로 다시 돈다."""
        text = ("# 표제: 옛 이름" + E + "# 옛 표제(2026-08-01): ~~더 옛 이름~~" + E
                + "# 구간표제: 2026-08-31" + E + E + "## 2026-01-26 | 구간 (턴 0-3)" + E
                + "- 결정된 것: 정했다" + E)
        retired, other, body = self.bc._split_head(text)
        self.assertEqual(len(retired), 1)
        self.assertEqual(other, ["# 구간표제: 2026-08-31"])
        self.assertTrue(body.startswith("## 2026-01-26"))
        self.assertNotIn("표제:", body)

    def test_refuses_without_a_target(self):
        called = []
        saved = self.bc.call_claude
        self.addCleanup(setattr, self.bc, "call_claude", saved)
        self.bc.call_claude = lambda *a, **k: called.append(1) or "새 이름"
        self.assertEqual(self.bc.retitle_cards(), 0)
        self.assertEqual(called, [], "대상 없이 표제를 흔들었다")

    def test_retitle_archives_old_and_keeps_marker(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        month = base / "2026-08"; month.mkdir(parents=True)
        cp = month / "a.md"
        cp.write_text("# 표제: 도구 도입 목표 설정" + E + "# 구간표제: 2026-08-31" + E + E
                      + "## 2026-01-26 | 비교 끝 → 보류 (턴 0-3)" + E
                      + "- 결정된 것: 접기로 했다" + E, encoding="utf-8")
        saved_dir, saved_call = self.bc.CARDS_DIR, self.bc.call_claude
        self.addCleanup(setattr, self.bc, "CARDS_DIR", saved_dir)
        self.addCleanup(setattr, self.bc, "call_claude", saved_call)
        self.bc.CARDS_DIR = base
        self.bc.call_claude = lambda *a, **k: "비교 끝 → 도입 보류"
        self.assertEqual(self.bc.retitle_cards(only={"a.md"}), 1)
        got = cp.read_text(encoding="utf-8")
        self.assertIn("# 표제: 비교 끝 → 도입 보류", got)
        self.assertIn("~~도구 도입 목표 설정~~", got, "지난 표제를 버렸다")
        self.assertIn("# 구간표제: 2026-08-31", got, "구간표제 표식이 사라졌다")
        self.assertIn("- 결정된 것: 접기로 했다", got, "본문이 상했다")


if __name__ == "__main__":
    unittest.main()
