"""turn_reader 가 인덱스의 turn_index 와 같은 번호를 매기는지 대조한다.

이 대조가 read 프로토콜의 유일한 안전장치다 — 사서는 langchain 을 안 들이려고
청크 규칙을 복제해 갖고 있고, 규칙이 갈리면 앵커 `턴 12` 가 다른 대목을 가리킨다.
"""

import os
import sys
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from turn_reader import read_range, read_text, turn_chunks  # noqa: E402



def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")
CONV = Path(os.environ.get("MEMEX_DATA_DIR", ROOT)) / "conversations"

SAMPLE = """# 제목

## 👤 User

첫 질문

## 🤖 Assistant

첫 답

## 🧑 사용자

둘째 질문

## 🧑 사용자

셋째 질문 (연속)

## 🤖 Claude

셋째 답

## 👤 User

답 없이 끝난 질문
"""


class TurnChunking(unittest.TestCase):
    def test_pairing(self):
        cs = turn_chunks(SAMPLE)
        self.assertEqual([c["turn"] for c in cs], [0, 1, 2, 3])
        self.assertEqual(cs[0]["user"], "첫 질문")
        self.assertEqual(cs[0]["assistant"], "첫 답")
        self.assertEqual(cs[1]["assistant"], "")          # 연속 User → 단독 청크
        self.assertEqual(cs[2]["user"], "셋째 질문 (연속)")
        self.assertEqual(cs[3]["assistant"], "")          # 답 없이 끝난 마지막 질문

    def test_range_clamps(self):
        r = read_range_from_text(SAMPLE, 2, 99)
        self.assertEqual([t["turn"] for t in r["turns"]], [2, 3])
        self.assertEqual(r["total_turns"], 4)

    def test_truncation_is_turn_aligned(self):
        r = read_range_from_text(SAMPLE, 0, 3, max_bytes=1)
        self.assertEqual(len(r["turns"]), 1)              # 첫 턴은 항상 담는다
        self.assertEqual(r["truncated_at"], 0)

    def test_budget_counts_bytes_not_chars(self):
        """한글은 글자당 3바이트 — 문자 예산이던 시절엔 같은 예산이 내용에 따라
        3배 출렁여 운반 한도를 넘겼다 (spec v4 §2)."""
        # 턴 0 의 크기: "첫 질문"+"첫 답" = 12자·28바이트. 문자 예산이라면 20 은
        # 두 턴을 담고, 바이트 예산이라면 턴 0 하나로 이미 초과라 거기서 끊는다.
        r = read_range_from_text(SAMPLE, 0, 3, max_bytes=20)
        self.assertEqual(len(r["turns"]), 1)
        self.assertEqual(r["truncated_at"], 0)


class TailRead(unittest.TestCase):
    """"이어서 하던 얘기" 는 대화의 끝이 필요하다 — 예산이 모자라면 앞을 버린다."""

    def test_tail_returns_the_last_turns(self):
        r = read_range_from_text(SAMPLE, 0, 0, tail=2)
        self.assertEqual([t["turn"] for t in r["turns"]], [2, 3])
        self.assertEqual((r["turn_from"], r["turn_to"]), (2, 3))
        self.assertEqual(r["total_turns"], 4)

    def test_tail_larger_than_conversation_is_clamped(self):
        r = read_range_from_text(SAMPLE, 0, 0, tail=99)
        self.assertEqual(len(r["turns"]), 4)
        self.assertIsNone(r["truncated_reason"])

    def test_tail_drops_the_front_not_the_end(self):
        """앞에서부터 채우면 꼬리를 달라고 해놓고 앞머리를 받는다 — 그 실패를 잡는다."""
        r = read_range_from_text(SAMPLE, 0, 0, tail=4, max_bytes=1)
        self.assertEqual([t["turn"] for t in r["turns"]], [3], "꼬리가 안 남았다")
        self.assertIn("앞쪽 턴을 잘랐다", r["truncated_reason"])
        self.assertIsNone(r["truncated_at"], "tail 은 이어 읽을 앞 방향이 없다")


class TruncationReason(unittest.TestCase):
    def test_forward_truncation_says_where_to_resume(self):
        r = read_range_from_text(SAMPLE, 0, 3, max_bytes=1)
        self.assertEqual(r["truncated_at"], 0)
        self.assertIn("턴 1 부터", r["truncated_reason"])

    def test_no_truncation_no_reason(self):
        r = read_range_from_text(SAMPLE, 0, 3)
        self.assertIsNone(r["truncated_at"])
        self.assertIsNone(r["truncated_reason"])


def read_range_from_text(text, a, b, **kw):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        p = Path(f.name)
    try:
        return read_range(p, a, b, **kw)
    finally:
        p.unlink(missing_ok=True)


@unittest.skipUnless(CONV.is_dir(), "conversations/ 없음")
class MatchesIndexer(unittest.TestCase):
    """실제 대화 파일로 memory_pipeline 의 turn_index 와 1:1 대조."""

    def test_same_turn_indices(self):
        try:
            from memory_pipeline import parse_md_file
        except ImportError as e:
            self.skipTest(f"memory_pipeline 미가용: {e}")
        files = sorted(CONV.glob("*.md"))[:40]
        self.assertTrue(files, "대조할 대화가 없다")
        for p in files:
            docs = parse_md_file(str(p))
            mine = turn_chunks(unicodedata.normalize("NFC", read_text(p)))
            self.assertEqual([d.metadata["turn_index"] for d in docs],
                             [c["turn"] for c in mine], f"턴 번호 불일치: {p.name}")
            for d, c in zip(docs, mine):
                self.assertEqual(d.metadata["user_text"], c["user"],
                                 f"User 본문 불일치: {p.name} 턴 {c['turn']}")


if __name__ == "__main__":
    unittest.main()
