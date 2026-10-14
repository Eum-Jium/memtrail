# Memtrail 아키텍처

> 평생 누적되는 개인 메모리 시스템의 설계 문서.
> 다섯 가지 검증된 아이디어를 한 시스템에 통합한다.

## 설계 원칙

1. **평생 자산** — 30년 이상 운영되어야 한다. 포맷의 장수성, 마이그레이션 부담 최소화가 최우선.
2. **데이터 격리** — 코드와 데이터의 명확한 분리. 데이터는 환경변수로 위치 지정.
3. **점진적 확장** — 한번에 모든 것을 만들지 않는다. 각 레이어가 독립적으로 동작 가능.
4. **로컬 우선** — 단일 사용자·단일 머신 기준. 모든 처리는 로컬에서.
5. **표준 포맷** — 마크다운, SQLite, JSON 등 미래에도 읽힐 수 있는 포맷만 사용.

## 다섯 기둥

기존 메모리 시스템들의 핵심 아이디어를 한 시스템에 통합한다:

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   1. 하이브리드 트리플 저장   (from Mem0)                │
│      ├─ Vector Store : 의미 검색 (Chroma)                │
│      ├─ Graph Store  : 엔티티 + 관계 (SQLite)            │
│      └─ KV Store     : 빠른 사실 조회 (SQLite)           │
│                                                         │
│   2. 시간 인식 그래프         (from Zep / Graphiti)      │
│      모든 사실에 (valid_from, valid_to) 부여             │
│      "X는 Y였다" → "X는 2024년에 Y였고, 2026년부턴 Z"     │
│                                                         │
│   3. 메모리 티어              (from Letta / MemGPT)      │
│      Hot   : 최근 30일, in-memory                        │
│      Warm  : 1년 이내, 로컬 SQLite                       │
│      Cold  : 그 이전, 클라우드 (R2, Drive)               │
│                                                         │
│   4. 감쇠 + 강화              (from OpenMemory)          │
│      score = relevance × time_decay × access_count       │
│      자주 회상되면 강화, 안 쓰이면 자연 감쇠              │
│                                                         │
│   5. 자기 모델 분리           (from Stash)               │
│      "사용자 자신에 대한 사실"을 별도 테이블/문서로 관리   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## 시스템 아키텍처

**클라이언트 인터페이스는 우편함 하나다.** MCP 서버는 은퇴했다 — 이유는 아래
[§0. 왜 우편함인가](#0-왜-우편함인가) 참고.

```
     어떤 AI 클라이언트든 (웹 / 데스크톱 / 모바일 / CLI)
                         │
                         │ 파일 쓰기/읽기 — 커넥터 또는 스토리지 API
                         ▼
           ┌────────────────────────────┐
           │   클라우드 스토리지 우편함  │
           │ queries/ answers/ trails/  │
           └─────────────┬──────────────┘
                         │ 폴링 (2초)
           ┌─────────────▼──────────────┐
           │  사서 데몬 (켜진 컴퓨터)    │
           │        LLM 0개              │
           └─────────────┬──────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
┌───────────────┐ ┌───────────────┐ ┌──────────────┐
│  Search Layer │ │ Storage Layer │ │  Self Model  │
│               │ │               │ │              │
│ - Vector(MMR) │ │ Vector: Chroma│ │ profile_*.md │
│ - BM25        │ │ Graph: SQLite │ │ narrative.md │
│ - Entity match│ │ KV:    SQLite │ │ timeline.md  │
│ - Fusion score│ │ Audit log     │ │              │
└───────┬───────┘ └───────┬───────┘ └──────┬───────┘
        │                 │                │
        └─────────────────┼────────────────┘
                          ▼
                  ┌───────────────┐
                  │ Temporal Layer│
                  │ valid_from/to │
                  └───────┬───────┘
                          │
                  ┌───────▼───────┐
                  │  Decay Layer  │
                  │  + reinforce  │
                  └───────┬───────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   ┌────────┐       ┌────────┐         ┌────────┐
   │  Hot   │       │  Warm  │         │  Cold  │
   │ ≤30d   │       │ ≤1y    │         │ >1y    │
   │in-mem  │       │SQLite  │         │R2/Drive│
   └────────┘       └────────┘         └────────┘
```

## 0. 왜 우편함인가

처음 만든 회상 인터페이스는 직접 운영하는 MCP 서버였다. 2026-08 재편에서 우편함으로
일원화했는데, **바꾼 1차 동기는 이식성이 아니라 보안이었다.**

### 왜 서버를 열지 않기로 했나

"폰에서도 회상한다"를 서버 방식으로 풀면 결론이 하나다 — 집에 있는 개인 PC 에 **밖에서
들어올 수 있는 문**을 내야 한다. 포트포워딩이든 리버스 터널이든 공개 엔드포인트든.
그리고 그 기계에는 **평생 쌓은 대화 원문 전부**가 들어 있고, 24시간 켜져 있다.
개인이 그 문을 내고 평생 관리한다는 게 이 프로젝트가 감당할 만한 위험이 아니었다.

우편함은 방향을 뒤집어 그 문을 없앤다. 밖에서 안으로 오는 연결이 존재하지 않고,
**서버가 자기 쪽에서 나가 확인할 뿐이다.** 그래서 포트가 0개다.

명세 v1 의 설계 원칙 다섯 중 셋이 여기서 나왔다:

| 원칙 | 내용 |
|---|---|
| **인바운드 제로** | 어떤 포트도 열지 않는다. Drive 를 아웃바운드 폴링만. 터널·공개 엔드포인트 없음 |
| **정본 단방향** | 로컬 → Drive 만 자동. Drive → 로컬은 검역소(수동 승인)로만 |
| **화이트리스트 실행** | 우편으로 실행되는 것은 사전 정의된 작업 유형뿐. **자유 텍스트는 절대 명령으로 해석하지 않는다** |

나머지 둘(단일 경로, 좌표 반환)도 같은 뿌리다 — 출입구를 하나로 좁혀야 거기서만 검사할 수
있고, 내용 대신 좌표만 돌려줘야 사서가 원문을 밖으로 흘리지 않는다.

### 부수 효과: 어디서든 붙는다

의도한 1차 목표는 아니었지만, 결과적으로 이게 컸다.
**우편함 클라이언트의 요구사항은 "파일 하나를 쓰고 읽을 수 있을 것" 뿐이다.** 그러면
붙을 경로가 두 겹으로 확보된다:

1. **커넥터** — 클라우드 스토리지 커넥터는 요즘 웬만한 AI 제품에 기본으로 달려 있다.
   이미 붙어 있는 것을 쓰므로 이쪽이 해야 할 통합 작업이 **0** 이다.
2. **스토리지 API** — 커넥터가 없는 환경이라도 API 승인만 받으면 투함할 수 있다.
   즉 **폴백이 항상 존재한다.**

자체 서버 방식은 이게 안 된다. 서피스마다 지원 여부가 다르고, 헤드리스 CLI·모바일처럼
붙일 자리가 없는 곳이 생긴다. **평생 자산에서 중요한 건 최고 속도가 아니라 30년 뒤에도
붙을 자리가 있느냐**다. 프로토콜 표준은 바뀌어도 "파일을 쓴다"는 계약은 안 바뀐다.

### MCP 는 버린 게 아니라 옮겼다

없앤 것은 **직접 운영하던 회상 서버**다. MCP 자체는 그대로 쓴다 —

- 클라이언트 절차(질의 스키마·투함·폴백·영수증)는 **스킬**로 구현돼 있다
  (`skills/memex-mailbox/SKILL.md`).
- 실제 투함·수령은 **이미 깔려 있는 스토리지 커넥터**가 한다. 그것도 MCP 다.

즉 *"내가 서버를 띄우는 MCP"* 에서 *"이미 있는 MCP 를 쓰는 스킬"* 로 바뀐 것이다.
유지·노출해야 할 서버가 하나 줄었다는 게 요점이다.

### 그 밖에 따라온 성질들

- **비동기** — 클라이언트가 답을 기다리다 죽어도 답장은 폴더에 남는다. 나중에 주우면 된다.
- **입구가 하나** — 검증·화이트리스트·검역을 한 지점에서 건다. 우편함이 신뢰 경계다.
- **인덱스 일원화** — 인덱스는 사서를 올린 컴퓨터 한 대에만 있으면 되고, 클라이언트는 아무것도 안 깐다.

### 사서를 어디에 두는가

**상시 켜두는 개인 PC 한 대.** 이건 최적화가 아니라 요구사항에서 따라 나온다 —
"폰만 들고 있어도 회상된다"가 목표인데, 검색을 대신해줄 무언가가 자고 있으면
그 목표가 성립하지 않는다. 편지가 유실되진 않지만 **내일 오는 답은 회상이 아니다.**

전용 하드웨어가 필요하다는 뜻은 아니다. 사서는 파이썬 프로세스 하나이고, 하는 일은
임베딩 검색이라 저전력 미니PC·SBC 급이면 충분하다. **클라우드 서버도 VPS 도 쓰지 않는다** —
남의 인프라에 평생 대화 원문을 올리지 않겠다는 것이 이 선택의 절반이다.

| | 상시 PC | 평소 쓰는 노트북에서만 |
|---|---|---|
| 폰에서 회상 | 된다 | 노트북이 켜져 있을 때만 |
| 무인 운용 | 된다 | 안 된다 |
| 밀린 편지 | 즉시 처리 | 켤 때 따라잡음 |

아래 칸도 동작은 한다 — 그래서 기기가 하나뿐인 사람도 쓸 수 있다. 다만 그건
이 시스템이 풀려던 문제를 반쯤 포기한 구성이다.

### 작업 기기가 따로 있으면

CLI 대화 기록은 **그 대화를 나눈 기기의 디스크에만** 있다. 상시 PC 가 아무리 깨어 있어도
노트북에서 나눈 대화는 스스로 알 수 없으므로, 작업 기기가 밀어 넣어야 한다
(`scripts/collect_and_push.sh` — tar over SSH).

반대로 웹 대화는 상시 PC 가 직접 걷는다 (`scripts/collect_web_via_cdp.py`).
브라우저 세션만 있으면 되고 특정 기기에 묶이지 않기 때문이다.

전송로는 **tailnet** 이다. 포트를 여는 대신 Tailscale 안에서만 붙으므로
인바운드 제로가 유지된다 — [`docs/security.md`](docs/security.md).

**치르는 값**: 왕복 10초쯤 걸린다 — 동기 호출보다 느리다.
대신 그 10초는 클라이언트가 무엇이든 상관없다.

## 1. 하이브리드 트리플 저장

영감: [Mem0](https://github.com/mem0ai/mem0)

세 가지 스토어가 같은 사실을 다른 관점으로 저장한다:

### Vector Store (Chroma)

**역할**: 의미 검색. "프로젝트 설계 관련 대화" 같은 추상적 쿼리에 강함.

```python
{
    "id": "uuid",
    "embedding": [0.1, 0.2, ...],  # 768-dim e5-base
    "page_content": "[User] ... [Assistant] ...",
    "metadata": {
        "date": "2026-04-27",
        "topic": "Memtrail 설계",
        "source_id": "conv-uuid",
        "tier": "hot",
        "decay_score": 1.0,
        "access_count": 0,
        "last_accessed": "2026-04-27"
    }
}
```

### Graph Store (SQLite)

**역할**: 엔티티 + 관계. "홈서버 프로젝트와 관련된 결정들" 같은 구조적 쿼리에 강함.

```sql
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT,           -- person, project, tool, concept, decision, ...
    aliases     JSON,           -- ["홈서버", "홈서버 프로젝트", ...]
    created_at  TIMESTAMP,
    valid_from  TIMESTAMP,      -- 시간 레이어
    valid_to    TIMESTAMP,
    UNIQUE(name, type)
);

CREATE TABLE relations (
    id          INTEGER PRIMARY KEY,
    src_id      INTEGER REFERENCES entities(id),
    rel         TEXT,           -- USES, DEPENDS_ON, MENTIONED_WITH, ...
    dst_id      INTEGER REFERENCES entities(id),
    weight      REAL DEFAULT 1.0,
    valid_from  TIMESTAMP,
    valid_to    TIMESTAMP,
    source_doc  TEXT            -- provenance
);

CREATE INDEX idx_rel_src ON relations(src_id, rel);
CREATE INDEX idx_rel_dst ON relations(dst_id, rel);
```

### KV Store (SQLite)

**역할**: 빠른 사실 조회. "사용자 직업?" 같은 단순 키-값 쿼리에 강함.

```sql
CREATE TABLE facts (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    confidence  REAL,
    valid_from  TIMESTAMP,
    valid_to    TIMESTAMP,
    source      TEXT
);
```

### 융합 검색

쿼리 시 세 스토어를 병렬 조회하고 점수를 융합:

```
final_score = α × vector_sim
            + β × bm25_score
            + γ × entity_overlap
            + δ × graph_distance
            × time_decay
            × tier_boost
```

기본값: α=0.4, β=0.2, γ=0.2, δ=0.2 (튜닝 대상)

## 2. 시간 인식 레이어

영감: [Zep / Graphiti](https://github.com/getzep/graphiti)

평생 메모리에서 가장 중요한 것: **사실은 변한다**.

```
2024년 사실: "회사는 X 사용"
2026년 사실: "회사는 Y로 마이그레이션"
```

→ 모든 사실/엔티티/관계에 `(valid_from, valid_to)` 부여.

쿼리 시 옵션:
- **현재 시점 (기본)**: `valid_to IS NULL OR valid_to > now()`
- **특정 시점**: `valid_from <= T AND (valid_to IS NULL OR valid_to > T)`
- **변화 추적**: `같은 (subject, predicate)의 모든 (valid_from, valid_to) 시퀀스`

업데이트 시: 새 사실이 들어오면 모순되는 기존 사실의 `valid_to`를 현재 시각으로 닫는다.

```python
def update_fact(key, new_value, confidence):
    old = db.get_current_fact(key)
    if old and old.value != new_value:
        db.close_fact(old.id, valid_to=now())
    db.insert_fact(key, new_value, confidence, valid_from=now())
```

## 3. 메모리 티어

영감: [Letta / MemGPT](https://github.com/letta-ai/letta)

OS 메모리 계층을 모방. 최근 + 자주 쓰는 것은 빠르게, 오래된 + 안 쓰이는 것은 느리게.

| 티어 | 기준 | 저장 위치 | 검색 속도 | 비용 |
|---|---|---|---|---|
| **Hot** | 최근 30일 OR access_count ≥ 10 | in-memory cache | <10ms | RAM |
| **Warm** | 최근 1년 | 로컬 SQLite + Chroma | <100ms | 디스크 |
| **Cold** | 1년 이상 | Cloudflare R2 + Google Drive | <2s | 클라우드 (저비용) |

**티어 이동 규칙** (cron 자동 실행):
```
Hot → Warm: 30일 미사용
Warm → Cold: 1년 미사용
Cold → Warm: 검색 히트 시 즉시 승격
Warm → Hot: access_count 임계값 초과 시
```

**검색 흐름**:
```
1. Hot 검색 (in-memory) — 즉시 결과
2. 결과 부족 시 Warm 검색
3. 그래도 부족 시 Cold 검색 (사용자 동의 또는 명시적 옵션)
```

## 4. 감쇠 + 강화

영감: [OpenMemory](https://github.com/CaviraOSS/OpenMemory)

생물학적 기억처럼 자연 감쇠 + 회상 시 강화.

### 감쇠 함수

```python
def decay_score(fact, now):
    age_days = (now - fact.last_accessed).days
    half_life = config.HALF_LIFE_DAYS  # 기본 180일
    return 0.5 ** (age_days / half_life)
```

### 강화

검색 결과에 포함될 때마다 `access_count++`, `last_accessed = now()`.

자주 회상되는 사실 → score 유지 → 검색 시 위로 → 더 자주 회상 → 강화 루프.

### 망각

`final_score < threshold` (예: 0.01) AND `access_count == 0` 으로 1년 이상 → archive 또는 삭제 후보로 표시 (자동 삭제 X, 사용자 승인 후).

## 5. 자기 모델 (Self Model)

영감: [Stash](https://alash3al.github.io/stash/)

"사용자 자신에 대한 사실"은 일반 메모리와 분리해 별도 관리. 5개 카테고리 (현재):

```
$MEMEX_DATA_DIR/memory/
├── profile_01_identity.md      # 직업, 전문성
├── profile_02_personality.md   # 성격, 소통 스타일
├── profile_03_interests.md     # 관심사, 학습 분야
├── profile_04_emotional.md     # 감정 패턴, 가치관
└── profile_05_ai_usage.md      # AI 활용 방식
```

**자동 갱신**: 대화가 끝나면 `update_profiles()` 도구가 LLM (Ollama)로 프로필 차분 업데이트.

**미래 추가 예정**:
- `narrative.md` — 시간 순 자전적 서사
- `timeline.md` — 주요 사건 연표
- `relationships.md` — 인물 관계망
- `goals.md` — 단기/장기 목표

## 데이터 흐름

### 입력 (대화 흡수)

```
1. Claude.ai 대화 → download_claude_conversations.js → conversations/*.md
2. memory_pipeline.py:
   a. parse_md_file()     : User+Assistant 쌍 추출
   b. E5Embeddings        : 임베딩 생성
   c. Chroma.add()        : Vector store 저장
   d. extract_entities()  : LLM으로 엔티티 추출 (Phase 1+)
   e. graph.upsert()      : Graph store 갱신 (Phase 1+)
   f. update_facts()      : KV store 갱신 (Phase 2+)
3. update_profiles()      : 자기 모델 차분 업데이트 (LLM)
```

### 출력 (검색)

```
사용자 쿼리
   ↓
search_memory_tool(query, k, date_filter)
   ↓
┌────────────┬────────────┬────────────┐
│ Vector MMR │   BM25     │  Graph 탐색 │
└──────┬─────┴──────┬─────┴──────┬─────┘
       └────────────┼────────────┘
                    ▼
              점수 융합
                    ▼
             감쇠 적용
                    ▼
             티어별 정렬
                    ▼
              상위 K개 반환
```

## 배포 모델

### Phase A: 로컬 (현재)
- 본인 PC에서 stdio MCP
- 데이터 = 본인 PC

### ~~Phase B: 클라우드 호스팅~~ (2026-07 폐기)
- Railway 배포 (`cloud_server.py`, OAuth 2.0 + DCR)는 한 달 test 후 폐기.
- 실 사용 0 — 단일 사용자 규모에서 클라우드 hop 은 유지 가치 없음.
- 필요 시 Cold 티어용 저비용 스토리지 (R2 등) 만 별도로 검토.

### Phase C: 분산 (장기, 우선순위 낮음)
- 여러 디바이스에서 공유 — 현재 단일 머신 사용이라 실질 필요 없음.
- 충돌 해결: vector clock 또는 CRDT
- 동기화 채널: WebSocket

## 다른 프로젝트와의 통합

Memtrail 은 자기 완결적이지만, 같은 사용자가 굴리는 다른 도구들의 **장기 기억층**으로도
동작하도록 설계했다. 붙이는 방향은 셋이다:

| 방향 | 예 |
|---|---|
| 도메인 지식 → Memtrail | 따로 관리하던 참고자료를 인제스트해 검색 대상에 포함 |
| 결정 → Memtrail | 자동화 도구가 내린 판단과 근거를 대화와 같은 형식으로 적재 |
| Memtrail → 도구 | 문서·리포트 생성 시 과거 맥락을 우편함으로 조회해 사용 |

어느 쪽이든 인터페이스는 하나다 — 대화 형식의 마크다운을 `conversations/` 에 넣거나,
우편함에 검색 편지를 넣는 것.

## 보안 / 프라이버시

- **로컬 우선**: 기본은 100% 로컬 처리
- **암호화 (예정)**: Cold 티어로 보낼 때 암호화 (age 또는 GPG)
- **선택적 외부 LLM**: 프로필 업데이트는 로컬 Ollama 사용 (외부 API 옵션)
- **감사 로그**: 모든 메모리 추가/수정 이력 보존 (audit table)

## 미해결 질문

- [ ] 그래프 DB: SQLite 자체 스키마 vs Neo4j Embedded vs DuckDB
- [ ] 임베딩 마이그레이션 전략 (모델 업그레이드 시)
- [ ] 멀티 디바이스 동기화 충돌 해결
- [ ] 외부 LLM 사용 시 데이터 마스킹 정책
- [ ] 백업 주기 / 보존 정책

## 참고 자료

- [Vannevar Bush, "As We May Think" (1945)](https://www.theatlantic.com/magazine/archive/1945/07/as-we-may-think/303881/)
- [Mem0 Architecture](https://docs.mem0.ai/platform/features/graph-memory)
- [Zep Temporal Knowledge Graph](https://www.getzep.com/product/knowledge-graph)
- [Letta (MemGPT) Paper](https://arxiv.org/abs/2310.08560)
- [Cognee Knowledge Engine](https://github.com/topoteretes/cognee)
- [OpenMemory Cognitive Model](https://github.com/CaviraOSS/OpenMemory)
