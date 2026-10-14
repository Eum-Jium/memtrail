# memex 우편함 명세 v2 — 회상 계층 보강 (2026-08-20)

[v1](2026-08-03_mailbox_spec_v1.md) 에 대한 **증분 명세**다. v1 의 설계 원칙(§0)·Drive
구조(§1)·명령 프로토콜(§3)은 그대로다. 여기서 바뀌는 것은 §2 검색 프로토콜뿐이고,
전부 **하위호환**이다 — v1 형식 그대로 보낸 편지는 v1 때와 같은 답을 받는다.

근거는 하루치 실사용 피드백(2026-08-20). 운영 기록은 [../mailbox.md](../mailbox.md)
「회상 실사용 피드백 반영」 절.

---

## 1. query 스키마 — `mode` 추가

```json
{"type":"search","q":"…","k":5,"mode":"search","date_from":null,"date_to":null,"source":null}
```

`mode` 는 `"search"`(기본·생략 가능) 또는 `"genealogy"`. 목록 밖 값은 reject.

## 2. query 스키마 — `read` 타입 신설

```json
{"type":"read","file":"<대화 파일명>","from":5,"to":20}
```

- `file` 은 hits 의 `source_file` 값. **경로가 아니라 이름만** 받는다 — 구분자·`..`·
  선행 `.`·비 `.md` 는 reject. 우편 내용이 파일시스템 좌표가 되면 "mailbox 밖은 읽기만"
  원칙이 깨진다.
- `from`/`to` 는 0 기반 턴 번호로, **인덱스의 `turn_index` 및 answer 의 `anchor` 와 같은
  체계**다. 생략 시 `from=0`, `to=from+9`.
- 한 번에 40턴 상한. 초과는 reject.

응답:

```json
{"file":"…","total_turns":17,"turn_from":5,"turn_to":18,"truncated_at":18,
 "turns":[{"turn":5,"user":"…","assistant":"…"}]}
```

- 범위는 실제 턴 수로 **클램프**한다 (넘겨 부르면 빈 결과가 아니라 있는 만큼).
- 문자 예산(40,000) 초과 시 **턴 경계에서만** 자르고 `truncated_at` 에 마지막으로 담은
  턴을 적는다. 반쪽 턴을 내보내면 인용이 문장 중간에서 끊긴다.
- `file` 을 못 찾으면 정체 키(§4)로 한 번 더 조회하고, 그래도 없으면 `error`.

## 3. answer 스키마 — 필드 3종 추가

```json
{
  "query_id": "...",
  "status": "pending | done",
  "index_as_of": "2026-08-20T09:10",
  "coverage": {"code": {"latest":"2026-08-18","convs":641},
               "claude_ai": {"latest":"2026-08-19","convs":1102}},
  "notice": "…",
  "hits": [ { "…": "…", "summary_kind": "card | excerpt", "turn": 12 } ]
}
```

- **`status`** — 사서는 편지를 집는 즉시 답장 자리에 `pending` 을 기록하고, 처리가 끝나면
  같은 경로를 `done` 으로 덮는다. `pending` 문서에는 **`hits` 를 싣지 않는다** (빈 배열은
  "결과 없음" 으로 오독된다). 클라이언트는 `status` 를 먼저 읽는다.
  끄기: `MEMEX_MAILBOX_PENDING=0`.
- **`coverage`** — 소스별 (색인된 최신 대화 날짜, 대화 수). `hits: []` 의 원인이
  "미색인" 인지 "기록 없음" 인지 가르는 유일한 근거다. 카드 다리 청크는 원문의 사본이라
  집계에서 제외한다.
- **`summary_kind`** — `card_summary` 필드에 실린 것이 LLM 카드 요약(`card`)인지 원문 청크
  앞부분(`excerpt`)인지. 카드는 recent 창 안에서만 생성되므로 창 밖 히트는 `excerpt` 다.
  `excerpt` 로 대화의 요지를 단정하면 안 된다.
- **`turn`** — 히트한 청크의 `turn_index`. `read` 요청의 좌표로 그대로 쓴다.

## 4. 히트 dedupe — 파일명이 아니라 정체 키

같은 대화가 서로 다른 파일명으로 두 벌 색인돼 있어(수집기 파일명 규칙 변경기) 검색 슬롯을
두 개 먹는 사고가 있었다. dedupe 키를 `source_file` 에서 **정체 키**로 바꾼다:

- 파일명 꼬리에 `__<cc|cw|mr>_<sid8>` 가 있으면 그것이 정체.
- 없으면 `날짜(하이픈 무시) + 제목 슬러그(영숫자·한글만)`.

단일 구현: `scripts/conv_key.py`. 같은 날 제목까지 같은 서로 다른 웹 대화는 하나로 접히지만,
접히지 않을 때의 손해(같은 대화가 답변에 두 번)가 더 크다. **파일을 지우는 쪽**
(`dedupe_sessions.py`)은 이 키만으로 결정하지 않고 첫 사용자 발화 지문까지 대조한다.

## 5. `mode: "genealogy"` — 시간순 응답

점수순 검색은 개념의 **최초 언급**을 못 잡는다. 가장 무르익은 대화가 상위를 먹기 때문이다.

- **후보는 어절이 확인된 것만** (bm25, 컷 0.60 — `search` 와 같은 값). dense 는 새 대화를
  들이지 못하고, 이미 들어온 대화에 `both` 표시만 붙인다.
- 대화 단위로 접을 때 **그 대화 안에서 가장 이른 턴**을 대표로 남긴다.
- 날짜 오름차순 정렬.
- 어절 일치가 0건이면 의미 폴백(dense 60건)으로 내려가고 `basis: "semantic"` 으로 알린다.

> **컷을 0.35 로 낮췄다가 되돌렸다** (2026-08-20 실측). "이 모드의 치명상은 최초를 놓치는
> 것이니 재현이 정밀보다 앞선다" 는 추론이 정확히 거꾸로였다 — 일반 검색에서는 약한 히트가
> 순위 아래로 묻히지만, **날짜순으로 세우면 약한 히트가 맨 앞(=답의 자리)으로 올라온다.**
>
> | 질의 | 컷 0.35 의 최초 | 컷 0.60 의 최초 |
> |---|---|---|
> | `<개념어>` | 2025-03 무관한 대화 | **2025-05 그 개념이 처음 나온 대화** (정답) |
> | `화두 스레드` | 2025-05 무관한 대화 (334건) | 2026-08-03 작업계획 대화 (3건) |
> | `우편함 사서` | 2025-04 무관한 대화 | 2026-08-03 우편함 재편 대화 |
>
> dense 를 seed 로 쓰지 않는 근거도 실측이다: `<개념어>` 의 dense 400건 점수가
> 0.7683~0.6993, 폭 0.07 이라 **점수 하한으로도 관련·무관이 안 갈린다.**

응답에 추가되는 필드:

```json
{"mode":"genealogy","basis":"lexical","matched":7,"note":"",
 "first_mention":{"date":"2025-05-22","title":"…","source_file":"…","turn":2,
                  "match":"both","anchor":"…"},
 "timeline":[{"date","title","source","source_file","turn","match"}],
 "timeline_sampled":false,
 "hits":[…]}
```

- `timeline` 은 최대 60건. 넘으면 **앞머리 10건은 확정**으로 두고 나머지를 시간축에 고르게
  표본한다 (`timeline_sampled: true`).
- `hits` 의 앞 3자리(k<3 이면 k)는 최초 쪽에 확정 배정하고, 나머지를 같은 규칙으로 흩는다.
- `basis: "semantic"` 일 때 `note` 가 비어 있지 않다 — 최초를 단정하지 말라는 신호다.

## 6. trail 영수증 — 부본 (§3 감사 로그 확장)

영수증은 여전히 **단방향**이고 사서는 답장하지 않는다. 다만 처리 결과 부본을
`mailbox/receipts/t_{receipt_id}.json` 에 남긴다:

```json
{"receipt_id":"…","processed_at":"…","status":"applied | rejected",
 "applied":{"used":2,"deep":1,"edges":1},
 "dropped":{"used":["…md"],"edges":0},
 "trail":{"…md":{"uses":5,"deep_uses":2}}}
```

스키마 위반·JSON 파손·초과 크기도 `status:"rejected"` + `reason` 으로 남는다 — 이전에는
로그만 남기고 조용히 사라져, 클라이언트가 드랍을 알 방법이 없었다.

## 7. 검증

- `test_mailbox_query.py` — 스키마(mode·read·경로 주입 차단·구간 상한), `_spread`,
  coverage(카드 다리 제외), genealogy(시간순·대화 내 최초 턴·정체 접기·summary_kind).
- `test_turn_reader.py` — 턴 페어링 규칙, 구간 클램프·턴 정렬 절단, 그리고 **실제 대화
  40건으로 `memory_pipeline.parse_md_file` 의 `turn_index` 와 1:1 대조**. 데몬은 langchain
  을 안 들이려고 청크 규칙을 복제해 갖고 있으므로, 이 대조가 read 좌표의 유일한 안전장치다.
