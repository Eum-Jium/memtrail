"""test_mailbox_query.py — 사서의 질의 계층(스키마·계보·커버리지)을 고정한다.

2026-08-20 회상 사용에서 나온 세 가지 오답 원인을 각각 잡아둔다:
  1) 파일명 규칙이 바뀐 대화가 두 벌 색인돼 검색 슬롯을 두 개 먹던 것 (conv_key)
  2) 빈 결과가 "미색인" 인지 "기록 없음" 인지 구분 안 되던 것 (coverage)
  3) 점수순이라 개념의 **최초 언급**이 안 잡히던 것 (genealogy)

2026-08-25 에 그 대칭점이 드러나 넷째가 붙었다:
  4) 점수순이라 **최신 언급**이 안 잡히던 것 (latest 트랙 · timeline · _spread 꼬리).
     검토는 길고 확정은 짧아서, 현재값을 물으면 옛 논의가 k 를 다 먹었다.

2026-08-31 실사용(claude.ai 스킬 왕복)에서 다섯째 축 — **운반**:
  5) 큰 답장이 커넥터 경로에서 깨지던 것. 답장 크기 예산(read 바이트 예산·기본 6턴·
     timeline 25), eom 종결 표지, BMP 밖 문자 이스케이프 (명세 v4).

실행: python -m unittest test_mailbox_query
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# MEMEX_DATA_DIR 은 건드리지 않는다 — 같은 실행에 도는 test_turn_reader 가 .env 의
# 값으로 실제 대화와 대조하는데, 여기서 setdefault 로 선점하면 그 대조가 조용히
# 스킵된다 (한 번 그렇게 새어나갔다). 아래 테스트는 인덱스·모델·카드를 전부 스텁으로
# 갈아끼우므로 데이터 폴더가 무엇이든 무관하다.
import mailbox_daemon as md  # noqa: E402


class QuerySchema(unittest.TestCase):
    def test_mode_defaults_to_search(self):
        spec, err = md.validate_query({"type": "search", "q": "x"})
        self.assertIsNone(err)
        self.assertEqual(spec["mode"], "search")

    def test_unknown_mode_rejected(self):
        _, err = md.validate_query({"type": "search", "q": "x", "mode": "시간순"})
        self.assertIn("mode", err)

    def test_genealogy_mode_accepted(self):
        spec, err = md.validate_query({"type": "search", "q": "개념어 A",
                                       "mode": "genealogy", "k": 8})
        self.assertIsNone(err)
        self.assertEqual((spec["mode"], spec["k"]), ("genealogy", 8))

    def test_unknown_type_rejected(self):
        _, err = md.validate_query({"type": "delete", "q": "x"})
        self.assertIn("type", err)


class ReadSchema(unittest.TestCase):
    def test_ok(self):
        spec, err = md.validate_query({"type": "read", "file": "a.md",
                                       "from": 5, "to": 20})
        self.assertIsNone(err)
        self.assertEqual((spec["from"], spec["to"]), (5, 20))

    def test_default_span_is_six_turns(self):
        """앵커는 한 턴을 가리킨다 — 기본 10턴은 답장 대부분이 곁가지였다 (v4 §2)."""
        spec, err = md.validate_query({"type": "read", "file": "a.md", "from": 5})
        self.assertIsNone(err)
        self.assertEqual((spec["from"], spec["to"]), (5, 10))

    def test_path_is_refused(self):
        """우편 내용이 파일시스템 좌표가 되면 mailbox 밖을 읽는다 — 이름만 받는다."""
        for bad in ("../../.env", "/etc/passwd", "sub/dir/a.md", "..\\a.md"):
            _, err = md.validate_query({"type": "read", "file": bad})
            self.assertIsNotNone(err, f"막지 못했다: {bad!r}")

    def test_span_cap(self):
        _, err = md.validate_query({"type": "read", "file": "a.md",
                                    "from": 0, "to": md.READ_MAX_TURNS})
        self.assertIn("턴까지", err)

    def test_reversed_span(self):
        _, err = md.validate_query({"type": "read", "file": "a.md",
                                    "from": 9, "to": 3})
        self.assertIsNotNone(err)


class Spread(unittest.TestCase):
    def test_keeps_head_and_spreads(self):
        items = list(range(100))
        got = md._spread(items, 6, head=3)
        self.assertEqual(got[:3], [0, 1, 2])            # 최초 언급은 확정 자리
        self.assertEqual(len(got), 6)
        self.assertEqual(got, sorted(got))              # 시간순 유지
        self.assertGreater(got[-1], 50)                 # 뒤쪽 전개도 실린다

    def test_short_list_untouched(self):
        self.assertEqual(md._spread([1, 2, 3], 6, head=3), [1, 2, 3])

    def test_keeps_tail_too(self):
        """머리만 지키면 개념의 **끝** 이 표본에서 빠진다 — 현재값 질문엔 그 자리가 답이다."""
        got = md._spread(list(range(100)), 6, head=3)
        self.assertEqual(got[-1], 99, "최신이 표본에서 빠졌다")
        self.assertEqual(got[:3], [0, 1, 2])
        self.assertEqual(len(got), 6)
        self.assertEqual(got, sorted(got))

    def test_tail_survives_tight_budget(self):
        """머리+꼬리가 예산을 다 먹어도 꼬리를 버리지 않는다."""
        self.assertEqual(md._spread(list(range(50)), 4, head=3), [0, 1, 2, 49])


def _meta(sf, date, turn, source="claude", kind=None):
    m = {"source_file": sf, "date": date, "turn_index": turn, "source": source,
         "topic": sf.split("_", 1)[-1][:-3], "user_text": f"{sf} 턴 {turn}"}
    if kind:
        m["kind"] = kind
    return m


class _FakeBM:
    """ranked=None 이면 전부 어절 일치(동점) — 계보의 정상 경로.
    ranked=[] 를 주면 어절 0건이라 의미 폴백(basis=semantic)으로 내려간다."""

    def __init__(self, metas, docs, ranked=None):
        self.stamp = "test"
        self.metas, self.docs = metas, docs
        self.ids = [str(i) for i in range(len(metas))]
        self._ranked = ([(i, 10.0) for i in range(len(metas))]
                        if ranked is None else ranked)

    def search(self, q, topn):
        return self._ranked


class _FakeCol:
    def __init__(self, metas, docs):
        self.metas, self.docs = metas, docs

    def query(self, query_embeddings, n_results, where=None, include=None):
        n = len(self.metas)
        return {"ids": [[str(i) for i in range(n)]], "metadatas": [self.metas],
                "documents": [self.docs], "distances": [[0.5] * n]}


def _encode(text):
    """SentenceTransformer.encode 는 ndarray 를 준다 (.tolist() 가 붙는다)."""
    return type("V", (), {"tolist": staticmethod(lambda: [0.0])})()


class _Stubs:
    """genealogy/coverage 는 인덱스·모델에 붙어 있다 — 그 둘만 가짜로 갈아끼운다."""

    # card_blocks_for 를 반드시 갈아끼운다 — _hit 이 이걸 직접 부르므로, 안 끼우면
    # 테스트가 실제 CARDS_DIR 를 읽어 로컬 데이터에 따라 결과가 달라진다.
    KEYS = ("get_model", "get_collection", "get_bm25", "load_manifest",
            "load_trail_state", "card_summary_for", "card_blocks_for",
            "card_is_stub")

    def __init__(self, metas, docs=None, ranked=None):
        self.metas = metas
        self.docs = docs or [f"[User]\n{m['user_text']}" for m in metas]
        self.ranked = ranked

    def __enter__(self):
        self.saved = {k: getattr(md, k) for k in self.KEYS}
        col = _FakeCol(self.metas, self.docs)
        bm = _FakeBM(self.metas, self.docs, self.ranked)
        md.get_model = lambda: type("M", (), {"encode": staticmethod(_encode)})
        md.get_collection = lambda: col
        md.get_bm25 = lambda c=None: bm
        md.load_manifest = lambda: {"files": {}}
        md.load_trail_state = lambda: {"nodes": {}, "edges": {}}
        md.card_summary_for = lambda *a, **k: None
        md.card_blocks_for = lambda *a, **k: []
        md.card_is_stub = lambda *a, **k: False
        md._COVERAGE = None
        md._TURNS = None            # 가짜 stamp 가 늘 "test" 라 캐시를 꼭 비운다
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(md, k, v)
        md._COVERAGE = None
        md._TURNS = None


class Coverage(unittest.TestCase):
    def test_latest_per_source_excludes_card_bridges(self):
        metas = [
            _meta("2026-08-18_a__cc_11111111.md", "2026-08-18", 0, "claude_code"),
            _meta("2026-08-10_b__cc_22222222.md", "2026-08-10", 0, "claude_code"),
            _meta("2026-08-19_c.md", "2026-08-19", 0, "claude"),
            _meta("2026-08-19_c.md", "2026-08-19", 0, "claude", kind="card"),
        ]
        with _Stubs(metas):
            cov = md.coverage()
        self.assertEqual(cov["code"]["latest"], "2026-08-18")
        self.assertEqual(cov["code"]["convs"], 2)
        self.assertEqual(cov["claude_ai"]["latest"], "2026-08-19")
        self.assertEqual(cov["claude_ai"]["convs"], 1, "카드 다리를 두 번 셌다")


class LatestTrack(unittest.TestCase):
    def test_newest_mention_gets_a_slot_though_score_buries_it(self):
        """검토는 길고 확정은 짧다 — 점수순만 보면 옛 논의가 k 를 다 먹는다.

        실측(2026-08-25): 장비 하나의 현재값을 물었더니 몇 해 전 **검토** 대화가
        상위를 차지해, 실물과 다른 값을 답했다. 실물을 말한 대화는
        짧아서 순위 꼴찌였다. 아래 배열이 그 모양이다.
        """
        metas = [
            _meta("2022-03-01_홈서버 뭐 살까.md", "2022-03-01", 0),
            _meta("2022-03-05_2베이 대 4베이.md", "2022-03-05", 0),
            _meta("2022-04-02_예산 다시 짜기.md", "2022-04-02", 0),
            _meta("2025-06-01_새 서버 왔다.md", "2025-06-01", 0),   # 실물 — 순위 꼴찌
        ]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "홈서버", "k": 3})
            hits = md.search(spec)
        files = [h["source_file"] for h in hits]
        self.assertEqual(len(hits), 3)
        self.assertIn("2025-06-01_새 서버 왔다.md", files,
                      "최신 언급이 점수에 묻혀 후보에서 아예 빠졌다 — 정렬로는 못 고친다")

    def test_latest_slot_is_not_wasted_on_a_duplicate(self):
        """최신이 이미 다른 트랙으로 들어왔으면 conv_key dedupe 로 접힌다."""
        metas = [_meta("2026-08-11_하나뿐.md", "2026-08-11", 0)]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "x", "k": 5})
            hits = md.search(spec)
        self.assertEqual(len(hits), 1, "같은 대화가 슬롯을 두 번 먹었다")


class SearchTimeline(unittest.TestCase):
    def test_answer_carries_date_ascending_timeline(self):
        """최신 한 건만 보면 그게 확정인지 또 다른 검토인지 갈리지 않는다."""
        metas = [
            _meta("2026-05-02_중간.md", "2026-05-02", 0),
            _meta("2026-01-09_처음.md", "2026-01-09", 0),
            _meta("2026-08-11_마지막.md", "2026-08-11", 0),
        ]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "x", "k": 3})
            out = md.execute(spec)
        dates = [t["date"] for t in out["timeline"]]
        self.assertEqual(dates, sorted(dates), "타임라인이 날짜순이 아니다")
        self.assertEqual(dates[-1], "2026-08-11", "종점이 꼬리에 안 왔다")
        self.assertEqual(len(out["timeline"]), len(out["hits"]))

    def test_genealogy_answer_has_no_search_timeline_key_collision(self):
        """계보 응답의 timeline 은 예전 의미 그대로다 (하위호환)."""
        with _Stubs([_meta("2026-05-22_초기.md", "2026-05-22", 0)]):
            spec, _ = md.validate_query({"type": "search", "q": "x",
                                         "mode": "genealogy"})
            out = md.execute(spec)
        self.assertEqual(out["mode"], "genealogy")
        self.assertIn("first_mention", out)


class Genealogy(unittest.TestCase):
    def test_oldest_first_and_earliest_turn_wins(self):
        metas = [
            _meta("2026-06-27_개념어 A 논의.md", "2026-06-27", 3),
            _meta("2026-05-22_초기 메모.md", "2026-05-22", 7),
            _meta("2026-05-22_초기 메모.md", "2026-05-22", 2),   # 같은 대화의 더 이른 턴
            _meta("2026-08-01_후속.md", "2026-08-01", 0),
        ]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "개념어 A",
                                         "mode": "genealogy", "k": 5})
            out = md.genealogy(spec)
        self.assertEqual(out["matched"], 3)
        self.assertEqual(out["first_mention"]["date"], "2026-05-22")
        self.assertEqual(out["first_mention"]["turn"], 2, "대화 안 최초 턴이 아니다")
        self.assertEqual([h["date"] for h in out["hits"]],
                         ["2026-05-22", "2026-06-27", "2026-08-01"])
        self.assertEqual([t["date"] for t in out["timeline"]],
                         ["2026-05-22", "2026-06-27", "2026-08-01"])

    def test_renamed_duplicate_folds_into_one(self):
        """규칙이 바뀐 두 파일명은 같은 대화다 — 계보에서도 한 칸만 차지한다."""
        metas = [
            _meta("2025-03-14_Weekly backup review.md", "2025-03-14", 1),
            _meta("20250314_Weekly_backup_review.md", "2025-03-14", 4),
        ]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "백업 점검",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual(out["matched"], 1)
        self.assertEqual(len(out["hits"]), 1)

    def test_dense_alone_cannot_seed_the_timeline(self):
        """계보의 핵심 계약 — 날짜순에서는 약한 히트가 **맨 앞**으로 올라온다.

        실측(2026-08-20): 개념어 하나의 dense 400건 점수 폭이 0.7683~0.6993 이라
        관련·무관을 못 가른다. 그 잡음이 최초 언급 자리를 먹으면 답 자체가 틀린다.
        그래서 어절이 확인된 대화만 후보가 되고, dense 는 `both` 표시만 붙인다.
        """
        metas = [
            _meta("2026-01-01_의미로만 걸린 잡음.md", "2026-01-01", 0),
            _meta("2026-05-22_진짜 시작.md", "2026-05-22", 0),
        ]
        with _Stubs(metas, ranked=[(1, 10.0)]):         # 둘째만 어절 일치
            spec, _ = md.validate_query({"type": "search", "q": "개념어 A",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual(out["basis"], "lexical")
        self.assertEqual(out["matched"], 1, "dense 잡음이 후보로 들어왔다")
        self.assertEqual(out["first_mention"]["date"], "2026-05-22")
        self.assertEqual(out["first_mention"]["match"], "both")

    def test_semantic_fallback_is_labelled(self):
        """어절 0건이면 의미로 모으되, 최초를 믿지 말라고 알린다."""
        with _Stubs([_meta("2026-05-22_하나뿐.md", "2026-05-22", 0)], ranked=[]):
            spec, _ = md.validate_query({"type": "search", "q": "x",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual(out["basis"], "semantic")
        self.assertIn("어절이 일치한 대화가 없어", out["note"])
        self.assertEqual(out["first_mention"]["match"], "dense")

    def test_lexical_basis_carries_no_warning(self):
        with _Stubs([_meta("2026-05-22_하나뿐.md", "2026-05-22", 0)]):
            spec, _ = md.validate_query({"type": "search", "q": "x",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual((out["basis"], out["note"]), ("lexical", ""))

    def test_summary_kind_marks_raw_excerpt(self):
        """카드가 없으면 card_summary 는 요약이 아니라 원문 조각이다."""
        with _Stubs([_meta("2025-01-01_옛 대화.md", "2025-01-01", 0)]):
            spec, _ = md.validate_query({"type": "search", "q": "옛",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual(out["hits"][0]["summary_kind"], "excerpt")

    def test_timeline_samples_above_25(self):
        """55건 전량을 실었더니 답장이 20.9KB — 답에 쓰인 건 first_mention 과
        hits 뿐이었다 (2026-08-31 실사용). 상한 25, 양끝 보존은 그대로."""
        metas = [_meta(f"2025-{m:02d}-{d:02d}_대화{m:02d}{d}.md",
                       f"2025-{m:02d}-{d:02d}", 0)
                 for m in range(1, 13) for d in (3, 12, 21)]      # 36건
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "x",
                                         "mode": "genealogy"})
            out = md.genealogy(spec)
        self.assertEqual(out["matched"], 36)
        self.assertEqual(len(out["timeline"]), 25)
        self.assertTrue(out["timeline_sampled"])
        self.assertEqual(out["timeline"][0]["date"], "2025-01-03", "최초가 빠졌다")
        self.assertEqual(out["timeline"][-1]["date"], "2025-12-21", "종점이 빠졌다")


class MailSerialization(unittest.TestCase):
    """운반 계층 (v4 §1) — 답장이 끝까지 왔는지 표시하고, 4바이트 문자를 지킨다."""

    def test_eom_is_last_key(self):
        s = md.serialize_mail({"query_id": "x", "hits": []})
        self.assertTrue(s.rstrip().endswith('"eom": "끝"\n}'),
                        f"eom 이 꼬리가 아니다: …{s[-40:]!r}")

    def test_nonbmp_survives_as_surrogate_escape(self):
        """커넥터 읽기가 BMP 밖 문자를 깨뜨린다 (👤→ð¤ 실측). \\u 쌍으로 보내면
        운반로가 무엇이든 ASCII 라 무사하고, json 파서가 원문자로 복원한다."""
        import json as _json
        s = md.serialize_mail({"turns": [{"user": "손 흔들었다 👋", "assistant": "👤 확인"}]})
        self.assertNotIn("👋", s)
        self.assertIn("\\ud83d", s.lower())
        back = _json.loads(s)
        self.assertEqual(back["turns"][0]["user"], "손 흔들었다 👋")
        self.assertEqual(back["eom"], "끝")

    def test_korean_stays_literal(self):
        """한글(BMP)은 이스케이프하지 않는다 — 답장은 사람이 읽는 좌표다."""
        self.assertIn("우편함 사서", md.serialize_mail({"q": "우편함 사서"}))


class TrailEdgeCandidates(unittest.TestCase):
    """v4 §4 — 함께 쓰인 쌍을 부본으로 되돌려준다. 적립은 아니다."""

    def test_pairs_without_existing_edge(self):
        import trail_store as ts
        state = ts.new_state()
        cands = ts.edge_candidates(state, ["a.md", "b.md", "a.md", "c.md"])
        self.assertEqual([c["pair"] for c in cands],
                         [["a.md", "b.md"], ["a.md", "c.md"], ["b.md", "c.md"]])

    def test_confirmed_edge_not_suggested_again(self):
        import trail_store as ts
        state = ts.new_state()
        state["edges"][ts.edge_key("a.md", "연관", "b.md")] = {"count": 1}
        cands = ts.edge_candidates(state, ["b.md", "a.md"])   # 무방향 — 역순도 접힌다
        self.assertEqual(cands, [])

    def test_single_used_yields_nothing(self):
        import trail_store as ts
        self.assertEqual(ts.edge_candidates(ts.new_state(), ["a.md"]), [])


class TailSchema(unittest.TestCase):
    """"저번에 하던 얘기 이어서" 가 필요로 하는 것은 대화의 **끝**이다."""

    def test_tail_accepted(self):
        spec, err = md.validate_query({"type": "read", "file": "a.md", "tail": 12})
        self.assertIsNone(err)
        self.assertEqual(spec["tail"], 12)

    def test_tail_beats_from_to(self):
        """끝을 달라는 요청이 구간 지정보다 구체적인 의도다."""
        spec, _ = md.validate_query({"type": "read", "file": "a.md",
                                     "from": 0, "to": 9, "tail": 5})
        self.assertEqual(spec["tail"], 5)

    def test_tail_bounds(self):
        for bad in (0, -3, md.READ_MAX_TURNS + 1):
            _, err = md.validate_query({"type": "read", "file": "a.md", "tail": bad})
            self.assertIsNotNone(err, f"막지 못했다: tail={bad}")

    def test_plain_read_has_tail_none(self):
        spec, _ = md.validate_query({"type": "read", "file": "a.md", "from": 3})
        self.assertIsNone(spec["tail"])


class SummaryHonesty(unittest.TestCase):
    """스텁을 카드라고 부르면 읽는 쪽이 첫 발화를 대화의 요지로 읽는다."""

    def test_stub_card_is_labelled_stub(self):
        metas = [_meta("2026-08-30_긴 대화.md", "2026-08-30", 0)]
        with _Stubs(metas):
            md.card_blocks_for = lambda *a, **k: [
                {"title": "긴 대화", "span": None,
                 "body": ["- 내용: 이 기능을 계속 쓸지 모르겠어",
                          "- 원본 경로: conversations/2026-08-30_긴 대화.md"]}]
            md.card_is_stub = lambda *a, **k: True
            spec, _ = md.validate_query({"type": "search", "q": "기능", "k": 1})
            hits = md.search(spec)
        self.assertEqual(hits[0]["summary_kind"], "stub")

    def test_real_card_still_says_card(self):
        metas = [_meta("2026-08-30_긴 대화.md", "2026-08-30", 0)]
        with _Stubs(metas):
            md.card_blocks_for = lambda *a, **k: [
                {"title": "긴 대화", "span": None, "body": ["- 결정된 것: 정했다"]}]
            spec, _ = md.validate_query({"type": "search", "q": "기능", "k": 1})
            hits = md.search(spec)
        self.assertEqual(hits[0]["summary_kind"], "card")


class TotalTurns(unittest.TestCase):
    """긴 대화에서 앵커가 앞머리면 꼬리로 가야 한다 — 그 판단의 재료."""

    def test_hits_carry_total_turns(self):
        sf = "2026-08-30_긴 대화.md"
        metas = [_meta(sf, "2026-08-30", t) for t in (0, 6, 202)]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "기능", "k": 1})
            hits = md.search(spec)
        self.assertEqual(hits[0]["total_turns"], 203)

    def test_card_bridge_turn_does_not_set_the_total(self):
        """다리 청크의 turn_index 는 구간 시작점이지 마지막 턴이 아니다."""
        sf = "2026-08-30_긴 대화.md"
        metas = [_meta(sf, "2026-08-30", 0), _meta(sf, "2026-08-30", 5),
                 _meta(sf, "2026-08-30", 999, kind="card")]
        with _Stubs(metas):
            spec, _ = md.validate_query({"type": "search", "q": "x", "k": 1})
            hits = md.search(spec)
        self.assertEqual(hits[0]["total_turns"], 6)

    def test_unknown_total_is_omitted_not_null(self):
        """null 을 실으면 읽는 쪽이 '짧은 대화' 로 오독한다."""
        metas = [_meta("2026-08-30_긴 대화.md", "2026-08-30", 0)]
        saved = md.safe_total_turns
        self.addCleanup(setattr, md, "safe_total_turns", saved)
        with _Stubs(metas):
            md.safe_total_turns = lambda: {}
            spec, _ = md.validate_query({"type": "search", "q": "x", "k": 1})
            hits = md.search(spec)
        self.assertNotIn("total_turns", hits[0])


if __name__ == "__main__":
    unittest.main()
