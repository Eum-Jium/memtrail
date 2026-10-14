"""test_dedupe_sessions.py — 중복 대화 정리의 삭제 경로를 실제로 밟는다.

이 테스트가 없어서 사고가 났다 (2026-08-20): `--cards` 분기에서만 쓰이는 헬퍼가
정의되지 않은 채로 나갔고, 로컬에서는 `--cards` 없이 돌려서 안 드러났다. 상시 PC 에서
7건 중 1건을 지운 뒤 NameError 로 멈췄다 — **파일을 지우는 코드의 중간 실패**라
가장 나쁜 종류다.

그래서 여기서는 임시 폴더에 진짜 파일을 깔고 `--apply --cards` 를 끝까지 돌린다.

실행: python -m unittest test_dedupe_sessions
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import dedupe_sessions as ds  # noqa: E402

FIRST_TURN = "예제 기능 구현이 끝났으니 전체 흐름을 검토해줘"


def conv(first_user: str, tail: str = "") -> str:
    return (f"# 제목\n\n## 👤 User\n\n{first_user}\n\n"
            f"## 🤖 Assistant\n\n답변이다.{tail}\n")


class CardMonth(unittest.TestCase):
    """카드 폴더는 cards/YYYY-MM/ 인데 파일명 규칙이 두 가지다."""

    def test_both_filename_conventions(self):
        self.assertEqual(ds.card_month("20250314_Example_conversation.md"), "2025-03")
        self.assertEqual(ds.card_month("2025-03-14_Example conversation.md"), "2025-03")
        self.assertEqual(ds.card_month("2026-08-18_x__cc_ab12cd34.md"), "2026-08")

    def test_no_date_prefix(self):
        self.assertIsNone(ds.card_month("이상한이름.md"))


class Grouping(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, name, body):
        p = self.dir / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_renamed_pair_groups(self):
        self.write("2025-03-14_Example conversation.md", conv(FIRST_TURN, " 길게"))
        self.write("20250314_Example_conversation.md", conv(FIRST_TURN))
        groups = ds.group_by_session(self.dir)
        self.assertEqual(len(groups), 1)

    def test_different_conversations_do_not_group(self):
        self.write("2025-03-14_A 이야기.md", conv(FIRST_TURN))
        self.write("2025-03-14_B 이야기.md", conv("전혀 다른 질문"))
        self.assertEqual(ds.group_by_session(self.dir), {})

    def test_fingerprint_gate_blocks_same_title_different_talk(self):
        """키가 같아도 첫 발화가 다르면 지우지 않는다 — 삭제는 되돌릴 수 없다."""
        a = self.write("2025-03-14_같은 제목.md", conv(FIRST_TURN))
        b = self.write("20250314_같은_제목.md", conv("완전히 다른 첫 발화다 이건 다른 대화"))
        self.assertFalse(ds.same_conversation([a, b]))

    def test_fingerprint_gate_passes_real_pair(self):
        a = self.write("2025-03-14_같은 제목.md", conv(FIRST_TURN, " 더 긴 판"))
        b = self.write("20250314_같은_제목.md", conv(FIRST_TURN))
        self.assertTrue(ds.same_conversation([a, b]))


class ApplyWithCards(unittest.TestCase):
    """삭제 경로 전체 — 여기가 사고가 났던 자리다."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.conv_dir = self.base / "conversations"
        self.cards = self.base / "cards" / "2025-03"
        self.conv_dir.mkdir(parents=True)
        self.cards.mkdir(parents=True)

        self.keeper = self.conv_dir / "2025-03-14_Example conversation.md"
        self.dup = self.conv_dir / "20250314_Example_conversation.md"
        self.keeper.write_text(conv(FIRST_TURN, " " + "x" * 500), encoding="utf-8")
        self.dup.write_text(conv(FIRST_TURN), encoding="utf-8")
        # 카드는 대화 파일명 그대로 — 지워지는 쪽의 카드도 같이 걷혀야 한다
        self.dup_card = self.cards / self.dup.name
        self.keeper_card = self.cards / self.keeper.name
        for c in (self.dup_card, self.keeper_card):
            c.write_text("## 2025-03-14 | 카드\n", encoding="utf-8")

    def run_cli(self, *extra):
        argv = sys.argv
        sys.argv = ["dedupe_sessions.py", "--dir", str(self.conv_dir),
                    "--cards-dir", str(self.base / "cards"), *extra]
        try:
            return ds.main()
        finally:
            sys.argv = argv

    def test_apply_with_cards_completes(self):
        listing = self.base / "deleted.txt"
        rc = self.run_cli("--apply", "--cards", "--deleted-list", str(listing))
        self.assertEqual(rc, 0)
        self.assertTrue(self.keeper.exists(), "더 큰 쪽을 지웠다")
        self.assertFalse(self.dup.exists(), "중복본이 안 지워졌다")
        self.assertFalse(self.dup_card.exists(), "딸린 카드가 남았다")
        self.assertTrue(self.keeper_card.exists(), "유지본의 카드를 지웠다")
        # 목록 기록은 루프를 끝까지 돌아야 나온다 — 중간에 죽으면 여기서 걸린다
        self.assertEqual(listing.read_text(encoding="utf-8").split(),
                         [self.dup.name])

    def test_dry_run_touches_nothing(self):
        self.assertEqual(self.run_cli(), 0)
        self.assertTrue(self.dup.exists())
        self.assertTrue(self.dup_card.exists())


if __name__ == "__main__":
    unittest.main()
