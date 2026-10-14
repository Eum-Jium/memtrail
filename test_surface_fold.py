"""test_surface_fold.py — cc↔cw 교차 표면 접기의 판정과 삭제 경로를 밟는다.

접기는 파일을 지우는 결정이라 dedupe 와 같은 무게다: 잘못 접으면 대화 하나가
사라진다. 그래서 여기서는 ① 판정(same_session)의 양성·음성 양쪽, ② 주석이
턴 번호를 건드리지 않는 것, ③ unpack 재렌더에서 주석이 살아남는 것, ④ 수집기
(convert_cowork_sessions)와 정리기(dedupe_sessions)의 실제 경로를 임시 폴더에서
끝까지 돌린다.

실행: python -m unittest test_surface_fold
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import surface_fold as sf  # noqa: E402
from turn_reader import turn_chunks  # noqa: E402

U1 = "예제 기능을 추가하고 싶은데 지금 구조에서 어디를 고쳐야 하는지 봐줘"
U2 = "승인했어 다시해봐"
U3 = "이번엔 입력 검증 폴백까지 넣어줘"


def cc_md(users_and_answers, title="C--binder — 예제 기능 추가"):
    lines = [f"# {title}", "",
             "- 생성일: 2026-08-29",
             "- ID: 1a2b3c4d-1111-2222-3333-444444444444",
             "- source: claude_code",
             "- project: C--binder",
             "- title: 예제 기능 추가", ""]
    for u, a in users_and_answers:
        lines += [f"## 👤 User · 10:0{len(lines) % 10}", "", u, "",
                  "## 🤖 Assistant", "", a, ""]
    return "\n".join(lines)


def cw_md(users_and_answers, cse="cse_01AbCdEfXXXX"):
    lines = ["# 예제 기능 추가", "",
             "- 생성일: 2026-08-29",
             f"- ID: {cse}",
             "- source: cowork",
             "- origin: desktop_app",
             "- title: 예제 기능 추가", ""]
    for u, a in users_and_answers:
        lines += ["---", "", "## 👤 User", "", u, "",
                  "## 🤖 Assistant", "", a, ""]
    return "\n".join(lines)


class SameSession(unittest.TestCase):
    def slugs(self, texts):
        from conv_key import slug
        return [slug(t) for t in texts]

    def test_identical_streams_match(self):
        a = self.slugs([U1, U2, U3])
        self.assertTrue(sf.same_session(a, list(a)))

    def test_stale_snapshot_still_matches(self):
        """같은 세션의 스냅샷 두 장 — 한쪽이 다른 쪽의 접두다 (cc 수집이 뒤처진 꼴)."""
        full = self.slugs([U1, U2, U3])
        self.assertTrue(sf.same_session(full[:2], full))
        self.assertTrue(sf.same_session(full, full[:2]))

    def test_retry_sessions_do_not_match(self):
        """같은 첫 발화로 다시 시작한 **별개** 세션 — 접으면 대화가 사라진다.

        첫 두 발화가 같아도 이후 갈라지면 짧은 쪽의 마지막 발화가 긴 쪽에 없다.
        이 관문(②)이 없으면 재시도 세션이 접힌다 — 제목·날짜·첫 발화가 다 같은
        경우라 사용자 제안의 3요소만으로는 못 가르는 지점이다.
        """
        a = self.slugs([U1, U2, "결과가 이상해서 그냥 여기서 멈출게 정리해줘"])
        b = self.slugs([U1, U2, U3, "테스트도 통과했네 커밋해"])
        self.assertFalse(sf.same_session(a, b))

    def test_short_fingerprint_refused(self):
        """첫 발화가 짧으면 **모른다 = 아니다** (접기는 되돌릴 수 없다)."""
        a = self.slugs(["고쳐줘", "응"])
        self.assertFalse(sf.same_session(a, list(a)))

    def test_empty_refused(self):
        self.assertFalse(sf.same_session([], self.slugs([U1])))


class Annotation(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_annotate_preserves_turns_and_mtime(self):
        p = self.dir / "2026-08-29_C--binder_x__cc_1a2b3c4d.md"
        p.write_text(cc_md([(U1, "설계안이다"), (U2, "반영했다")]), encoding="utf-8")
        before_chunks = turn_chunks(p.read_text(encoding="utf-8"))
        before_mtime = p.stat().st_mtime

        self.assertTrue(sf.annotate_cowork(p, "cse_01AbCdEfXXXX"))
        text = p.read_text(encoding="utf-8")
        self.assertIn("- surfaces: code, cowork", text)
        self.assertIn("- cowork_id: cse_01AbCdEfXXXX", text)
        # 주석은 헤더에 머문다 — 턴 파서가 보는 세계는 그대로여야 한다
        self.assertEqual(turn_chunks(text), before_chunks, "주석이 턴을 건드렸다")
        # 첫 턴 헤더보다 앞(머리 600B 안)에 있어야 cowork_id_of 가 읽는다
        self.assertEqual(sf.cowork_id_of(text[:600]), "cse_01AbCdEfXXXX")
        self.assertEqual(p.stat().st_mtime, before_mtime, "mtime 이 바뀌면 카드가 재생성된다")

    def test_annotate_is_idempotent(self):
        p = self.dir / "2026-08-29_x__cc_1a2b3c4d.md"
        p.write_text(cc_md([(U1, "답")]), encoding="utf-8")
        self.assertTrue(sf.annotate_cowork(p, "cse_A"))
        self.assertFalse(sf.annotate_cowork(p, "cse_A"), "두 번째 호출이 또 썼다")
        self.assertEqual(p.read_text(encoding="utf-8").count("- surfaces:"), 1)

    def test_cowork_id_reads_both_spellings(self):
        self.assertEqual(sf.cowork_id_of("- ID: cse_ABC\n"), "cse_ABC")
        self.assertEqual(sf.cowork_id_of("- cowork_id: cse_DEF\n"), "cse_DEF")
        self.assertIsNone(sf.cowork_id_of("- ID: 1a2b3c4d-1111\n"))


class CarryAnnotations(unittest.TestCase):
    def test_survives_rerender(self):
        old = cc_md([(U1, "답")])
        old = sf.insert_annotations(old, "cse_01AbCdEfXXXX")
        new = cc_md([(U1, "답"), (U2, "더 긴 새 답")])   # 세션이 자란 재렌더
        carried = sf.carry_annotations(old, new)
        self.assertIn("- cowork_id: cse_01AbCdEfXXXX", carried[:600])
        self.assertIn("- surfaces: code, cowork", carried[:600])

    def test_unannotated_passthrough(self):
        old, new = cc_md([(U1, "답")]), cc_md([(U1, "답"), (U2, "둘")])
        self.assertEqual(sf.carry_annotations(old, new), new)

    def test_stable_when_content_unchanged(self):
        """주석 보존 후 비교가 같아야 --refresh 가 매일 '갱신됨' 을 찍지 않는다."""
        old = sf.insert_annotations(cc_md([(U1, "답")]), "cse_A")
        self.assertEqual(sf.carry_annotations(old, cc_md([(U1, "답")])), old)


class ConvertPreventsDuplicate(unittest.TestCase):
    """수집기 경로 — cc 가 이미 있으면 cw md 를 만들지 않고 접는다."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        import convert_cowork_sessions as ccs
        self.ccs = ccs
        self.cc = self.dir / "2026-08-29_C--binder_예제_기능__cc_1a2b3c4d.md"
        self.cc.write_text(cc_md([(U1, "설계"), (U2, "반영")]), encoding="utf-8")

    def export(self, sessions):
        p = self.dir / "export.json"
        p.write_text(json.dumps(sessions, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def sess(self, sid="cse_01AbCdEfXXXX", msgs=((U1, "설계"), (U2, "반영"))):
        messages = []
        for u, a in msgs:
            messages += [{"role": "user", "text": u}, {"role": "assistant", "text": a}]
        return {"id": sid, "title": "예제 기능 추가",
                "created_at": "2026-08-29T09:00:00Z", "origin": "desktop_app",
                "messages": messages}

    def test_same_session_folds_instead_of_creating(self):
        self.ccs.convert(self.export([self.sess()]), str(self.dir))
        self.assertFalse(list(self.dir.glob("*__cw_*.md")), "cw md 가 생겼다")
        self.assertEqual(sf.cowork_id_of(self.cc.read_text(encoding="utf-8")[:600]),
                         "cse_01AbCdEfXXXX", "keeper 에 열쇠가 안 남았다")

    def test_folded_session_skipped_on_next_run_and_refresh(self):
        self.ccs.convert(self.export([self.sess()]), str(self.dir))
        annotated = self.cc.read_text(encoding="utf-8")
        # 다음 반출 — cowork_id 로 즉시 알아보고, --refresh 여도 keeper 를 덮지 않는다
        self.ccs.convert(self.export([self.sess(msgs=((U1, "설계"), (U2, "반영"), (U3, "추가")))]),
                         str(self.dir), refresh=True)
        self.assertFalse(list(self.dir.glob("*__cw_*.md")))
        self.assertEqual(self.cc.read_text(encoding="utf-8"), annotated,
                         "cc keeper 가 cw 렌더로 덮였다 — 갱신은 로컬 수집 소유다")

    def test_unrelated_session_still_created(self):
        other = self.sess(sid="cse_ZZZZ9999YYYY",
                          msgs=(("전혀 다른 프로젝트의 회귀 테스트 좀 돌려서 결과를 봐줘", "돌렸다"),))
        self.ccs.convert(self.export([other]), str(self.dir))
        self.assertEqual(len(list(self.dir.glob("*__cw_*.md"))), 1)


class DedupeFoldsExisting(unittest.TestCase):
    """정리기 경로 — 이미 두 벌 들어온 cc/cw 쌍을 --apply 로 접는다."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.conv = self.base / "conversations"
        self.cards = self.base / "cards" / "2026-08"
        self.conv.mkdir(parents=True)
        self.cards.mkdir(parents=True)
        import dedupe_sessions as ds
        self.ds = ds

    def run_cli(self, *extra):
        argv = sys.argv
        sys.argv = ["dedupe_sessions.py", "--dir", str(self.conv),
                    "--cards-dir", str(self.base / "cards"), *extra]
        try:
            return self.ds.main()
        finally:
            sys.argv = argv

    def test_apply_folds_pair_and_removes_card(self):
        cc = self.conv / "2026-08-29_C--binder_예제_기능__cc_1a2b3c4d.md"
        cw = self.conv / "2026-08-29_예제_기능_추가__cw_01AbCdEf.md"
        cc.write_text(cc_md([(U1, "설계"), (U2, "반영"), (U3, "추가")]), encoding="utf-8")
        cw.write_text(cw_md([(U1, "설계"), (U2, "반영")]), encoding="utf-8")
        cw_card = self.cards / cw.name
        cw_card.write_text("## 카드\n", encoding="utf-8")

        listing = self.base / "deleted.txt"
        self.assertEqual(self.run_cli("--apply", "--cards",
                                      "--deleted-list", str(listing)), 0)
        self.assertTrue(cc.exists() and not cw.exists(), "cc 를 남기고 cw 를 지워야 한다")
        self.assertFalse(cw_card.exists(), "cw 카드가 남았다")
        self.assertIn("- cowork_id: cse_01AbCdEf", cc.read_text(encoding="utf-8")[:600])
        self.assertEqual(listing.read_text(encoding="utf-8").split(), [cw.name])

    def test_retry_pair_left_alone(self):
        cc = self.conv / "2026-08-29_C--binder_예제_기능__cc_1a2b3c4d.md"
        cw = self.conv / "2026-08-29_예제_기능_추가__cw_01AbCdEf.md"
        cc.write_text(cc_md([(U1, "a"), (U2, "b"),
                             ("여기까지 하고 오늘은 정리하고 멈추자", "정리했다")]), encoding="utf-8")
        cw.write_text(cw_md([(U1, "a"), (U2, "b"), (U3, "c"),
                             ("테스트 통과했으니 커밋해", "커밋했다")]), encoding="utf-8")
        self.assertEqual(self.run_cli("--apply"), 0)
        self.assertTrue(cc.exists() and cw.exists(), "별개 세션을 접었다 — 대화가 사라진다")

    def test_dry_run_touches_nothing(self):
        cc = self.conv / "2026-08-29_C--binder_x__cc_1a2b3c4d.md"
        cw = self.conv / "2026-08-29_x__cw_01AbCdEf.md"
        cc.write_text(cc_md([(U1, "설계"), (U2, "반영")]), encoding="utf-8")
        cw.write_text(cw_md([(U1, "설계"), (U2, "반영")]), encoding="utf-8")
        self.assertEqual(self.run_cli(), 0)
        self.assertTrue(cw.exists())
        self.assertNotIn("cowork_id", cc.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
