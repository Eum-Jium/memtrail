"""test_card_backfill.py — 창 밖 카드 백필이 스텁을 굳히지 않는지 지킨다.

백필의 존재 이유는 "카드가 없어서 원문 조각이 요약 행세를 하던 것" 을 없애는
것이다. 그런데 LLM 이 안 뜨면 generate_card 가 스텁(제목 + 첫 사용자 발화 발췌)을
쓰는데, 그건 **아무것도 안 하느니 못하다**:

  - 내용은 여전히 첫 발화 발췌인데 사서는 카드로 보고 summary_kind="card" 를 붙인다
    — 고치려던 오독을 라벨만 바꿔 되살린다.
  - 파일이 생기므로 다음 백필이 그 대화를 건너뛴다. 영구히 막힌다.

실제로 상시 PC 에 SSH 로 --backfill 을 돌렸다가 스텁이 하나 깔렸다 (2026-08-20).
맥은 구독 자격증명이 Keychain 에 있어 SSH 세션에서 `claude -p` 가 rc=1 로 죽는다.

실행: python -m unittest test_card_backfill
"""

import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_memory_cards as b  # noqa: E402

STUB = "## 2025-01-02 | 제목\n- 내용: 첫 발화다\n- (자동 스텁 — LLM 요약 아님)\n"
REAL = "## 2025-01-02 | 제목\n- 결정된 것: 무언가\n- 회상어: 가, 나, 다\n"


class Backfill(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.old_base, self.old_cards = b.BASE_DIR, b.CARDS_DIR
        b.BASE_DIR = self.base
        b.CARDS_DIR = self.base / "cards"
        self.addCleanup(self._restore)

        self.conv = self.base / "conversations"
        self.conv.mkdir(parents=True)
        b.CARDS_DIR.mkdir(parents=True)
        # 창(90일) 한참 밖의 대화 세 건
        self.old_day = date.today() - timedelta(days=400)
        self.files = []
        for i in range(3):
            p = self.conv / f"{self.old_day.isoformat()}_대화{i}.md"
            p.write_text("## 👤 User\n\n질문\n\n## 🤖 Assistant\n\n답\n", encoding="utf-8")
            self.files.append(p)

        self.old_list = b.list_conversations
        b.list_conversations = lambda: [(self.old_day, p) for p in sorted(self.files)]

    def _restore(self):
        b.BASE_DIR, b.CARDS_DIR = self.old_base, self.old_cards
        b.list_conversations = self.old_list

    def write_card(self, p: Path, body: str):
        cp = b.card_path(self.old_day, p)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(body, encoding="utf-8")
        return cp

    def stub_generator(self):
        """LLM 이 죽은 상태 — generate_card 가 스텁을 쓰는 상황을 흉내낸다."""
        def gen(d, p, use_llm=True):
            self.write_card(p, STUB)
        b.generate_card = gen

    def real_generator(self):
        def gen(d, p, use_llm=True):
            self.write_card(p, REAL)
        b.generate_card = gen

    def tearDown(self):
        if hasattr(b, "_orig_generate"):
            b.generate_card = b._orig_generate

    def test_stub_is_removed_and_run_aborts(self):
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        self.stub_generator()
        made = b.backfill_cards()
        self.assertEqual(made, 0, "스텁을 생성으로 셌다")
        for p in self.files:
            self.assertFalse(b.card_path(self.old_day, p).exists(),
                             "스텁이 디스크에 남았다 — 다음 백필이 이 대화를 건너뛴다")

    def test_existing_stub_counts_as_missing(self):
        """이미 깔린 스텁은 '카드 있음' 이 아니다 — 다시 대상이 되어야 한다."""
        self.write_card(self.files[0], STUB)
        self.assertFalse(b.has_real_card(self.old_day, self.files[0]))
        self.write_card(self.files[1], REAL)
        self.assertTrue(b.has_real_card(self.old_day, self.files[1]))

    def test_real_cards_are_made_and_existing_kept(self):
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        keep = self.write_card(self.files[0], REAL)
        keep_mtime = keep.stat().st_mtime_ns
        self.real_generator()
        made = b.backfill_cards()
        self.assertEqual(made, 2, "이미 카드가 있는 대화를 다시 만들었거나 빠뜨렸다")
        self.assertEqual(keep.stat().st_mtime_ns, keep_mtime, "기존 카드를 덮었다")

    def _capture_log(self):
        lines = []
        orig_log = b.log
        self.addCleanup(lambda: setattr(b, "log", orig_log))
        b.log = lines.append
        return lines

    def test_daily_fills_holes_within_its_nightly_quota(self):
        """창 밖 구멍은 daily 가 밤당 정해진 몫만큼 저절로 메운다 —
        초판 카드의 유일한 기회(창 안 90일)를 놓친 대화가 영구 구멍이 되지 않게."""
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        self.real_generator()
        old_q, b.NIGHTLY_BACKFILL = b.NIGHTLY_BACKFILL, 2
        self.addCleanup(lambda: setattr(b, "NIGHTLY_BACKFILL", old_q))
        lines = self._capture_log()
        self.assertEqual(b.build_cards(), 2, "밤당 몫을 안 지켰다")
        self.assertTrue(any("3건 — 이번 밤에 2건 메움, 1건 남음" in ln for ln in lines),
                        f"진행을 안 알렸다: {lines}")
        # 남은 1건은 다음 밤에
        self.assertEqual(b.build_cards(), 1)

    def test_nightly_quota_off_means_report_only(self):
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        b.generate_card = lambda d, p, use_llm=True: self.fail("몫이 0인데 만들었다")
        old_q, b.NIGHTLY_BACKFILL = b.NIGHTLY_BACKFILL, 0
        self.addCleanup(lambda: setattr(b, "NIGHTLY_BACKFILL", old_q))
        lines = self._capture_log()
        self.assertEqual(b.build_cards(), 0)
        self.assertTrue(any("3건 — 이번 밤에 0건 메움" in ln for ln in lines), lines)

    def test_transient_failure_is_not_counted_as_made(self):
        """카드를 못 만든 건 생성이 아니다 — 예전엔 파일이 없으면 스텁 검사를
        통과해 성공으로 셌고, 로그의 '생성 N' 이 실제보다 컸다."""
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        b.generate_card = lambda d, p, use_llm=True: None      # 아무것도 안 씀
        self.assertEqual(b.backfill_cards(), 0)

    def test_transient_failure_does_not_abort_the_batch(self):
        """한 건의 일시 실패로 1,000장짜리 배치가 멎으면 안 된다."""
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        seen = []

        def gen(d, p, use_llm=True):
            seen.append(p)
            if len(seen) == 1:
                return None                                   # 첫 장만 실패
            self.write_card(p, REAL)

        b.generate_card = gen
        self.assertEqual(b.backfill_cards(), 2)
        self.assertEqual(len(seen), 3, "실패 뒤로 진행을 멈췄다")

    def test_parallel_makes_every_card(self):
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        self.real_generator()
        self.assertEqual(b.backfill_cards(jobs=4), 3)
        for p in self.files:
            self.assertTrue(b.card_path(self.old_day, p).exists(), f"빠진 카드: {p.name}")

    def test_parallel_leaves_no_stub_behind(self):
        """동시 실행 중 인증이 죽어도 디스크에 스텁이 남으면 안 된다 —
        남으면 그 대화가 '카드 있음' 으로 보여 다음 백필이 영영 건너뛴다."""
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        self.stub_generator()
        self.assertEqual(b.backfill_cards(jobs=4), 0)
        for p in self.files:
            self.assertFalse(b.card_path(self.old_day, p).exists(),
                             f"스텁이 남았다: {p.name}")

    def test_limit_splits_the_batch(self):
        orig = b.generate_card
        self.addCleanup(lambda: setattr(b, "generate_card", orig))
        self.real_generator()
        self.assertEqual(b.backfill_cards(limit=2), 2)
        self.assertEqual(b.backfill_cards(limit=2), 1)      # 남은 1건만
        self.assertEqual(b.backfill_cards(limit=2), 0)


class RecentWindow(unittest.TestCase):
    """낭독 층(recent.md)이 표제 줄 때문에 비면 안 된다.

    2026-08-25 실사고: 카드 머리에 `# 표제:` 를 붙였더니 assemble() 의
    `re.match(r"## \\d{4}-...")` 가 **카드 첫 글자에 고정**돼 전부 빗나갔고,
    recent.md 가 0장으로 조립돼 그대로 Drive 에 올라갔다. 더 나쁜 건 그게
    정상 로그처럼 찍혀서 아무도 안 멈췄다는 것이다.
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.old = (b.BASE_DIR, b.CARDS_DIR, b.EXPORT_DIR)
        b.BASE_DIR = self.base
        b.CARDS_DIR = self.base / "cards"
        b.EXPORT_DIR = self.base / "drive_export"
        self.addCleanup(lambda: setattr_all(b, self.old))
        self.today = date(2026, 8, 25)
        self.day = self.today - timedelta(days=3)
        mdir = b.CARDS_DIR / self.day.strftime("%Y-%m")
        mdir.mkdir(parents=True)
        self.card = mdir / "대화.md"

    def _write(self, text):
        self.card.write_text(text, encoding="utf-8")

    def test_titled_card_still_enters_recent(self):
        """표제가 붙어도 카드가 창에 들어온다. 단 표제 줄 자체는 안 실린다 —
        낭독 층에서 이름표는 할 일이 없고 예산만 먹는다."""
        self._write(f"# 표제: 어떤 결론까지 갔다\n\n## {self.day} | 제목\n"
                    "- 결정된 것: 무언가\n- 회상어: 가, 나\n")
        b.assemble(today=self.today)
        out = (b.EXPORT_DIR / "recent.md").read_text(encoding="utf-8")
        self.assertIn("제목", out)
        self.assertIn("무언가", out)
        self.assertNotIn("어떤 결론까지 갔다", out)
        self.assertNotIn("# 표제:", out)

    def test_retired_title_lines_are_stripped_too(self):
        self._write(f"# 표제: 새 이름\n# 옛 표제(2026-08-25): ~~헌 이름~~\n\n"
                    f"## {self.day} | 제목\n- 결정된 것: 무언가\n- 회상어: 가\n")
        b.assemble(today=self.today)
        out = (b.EXPORT_DIR / "recent.md").read_text(encoding="utf-8")
        self.assertNotIn("헌 이름", out)
        self.assertNotIn("새 이름", out)
        self.assertIn("무언가", out)

    def test_stripping_does_not_touch_the_card_file(self):
        """파일은 정본이다 — 조립용 사본에서만 뺀다."""
        text = (f"# 표제: 어떤 결론까지 갔다\n\n## {self.day} | 제목\n"
                "- 결정된 것: 무언가\n- 회상어: 가\n")
        self._write(text)
        b.assemble(today=self.today)
        self.assertEqual(self.card.read_text(encoding="utf-8"), text)

    def test_untitled_card_still_enters_recent(self):
        self._write(f"## {self.day} | 제목\n- 결정된 것: 무언가\n- 회상어: 가, 나\n")
        b.assemble(today=self.today)
        out = (b.EXPORT_DIR / "recent.md").read_text(encoding="utf-8")
        self.assertIn("제목", out)

    def test_empty_window_with_cards_present_refuses_to_overwrite(self):
        """헤더를 못 읽으면 빈 recent.md 로 덮지 않고 경고하고 멈춘다."""
        self._write("헤더가 없는 이상한 카드\n- 회상어: 가\n")
        self.assertIsNone(b.assemble(today=self.today))
        self.assertFalse((b.EXPORT_DIR / "recent.md").exists())


def setattr_all(mod, vals):
    mod.BASE_DIR, mod.CARDS_DIR, mod.EXPORT_DIR = vals


if __name__ == "__main__":
    unittest.main()
