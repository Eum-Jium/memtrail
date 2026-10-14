# 발행 파이프라인 — 로컬에서 Drive 로 (단방향)

정본은 항상 로컬(`MEMEX_DATA_DIR`), Drive 는 읽기 전용 사본.
**Drive → 로컬 방향 자동 동기화는 만들지 않는다** (정본 이원화 금지 — 예외는
검역소 승인 귀화 `quarantine_adopt` 뿐).

> 회상 경로는 이 문서가 아니라 [`mailbox.md`](mailbox.md) 다 (사서 데몬 + 스킬).
> 이 문서는 **쌓고 발행하는 쪽**을 다룬다.

전제: **상시 켜두는 개인 PC 한 대**가 인덱싱·발행·사서를 맡는다. 평소 쓰는 작업 기기가
따로 있으면 그쪽은 수집만 하고 밀어 넣는다 — CLI 대화 기록이 그 기기 디스크에만 있기 때문이다.
기기가 하나뿐이면 아래 두 스크립트 중 `daily.sh` 만 쓰면 된다.

## 구성

```
상시 PC (정본)                        Drive (gdrive:ClaudeMemory/)
─────────────────────────────        ─────────────────────────────
conversations/*.md  (정본)     ─┐
memory/*.md         (정본)      │
cards/    (대화별 카드 캐시)    ├─→  raw/            ← 원문 사본 (깊이 읽기용 + 백업)
trail/    (영수증 침전 상태)    │
archive/  (냉동 보관물)        ─┘
canon/                         ───→  canon/          ← 정본 프로필 (낭독 대상)
drive_export/recent.md         ───→  recent.md       ← 최근 카드 전문 (창은 토큰 예산이 정한다, ~5만 tok 이하)
                                     mailbox/        ← 우편함 (사서 데몬이 폴링)
```

Drive 사본이 필요한 이유는 백업이 아니라 **깊이 읽기**다. 사서는 좌표(파일 + 앵커)만
돌려주므로, 클라이언트가 원문을 직접 열려면 Drive 에 사본이 있어야 한다.

## 매일 도는 것

**상시 PC** — `bash scripts/daily.sh`
수집 → 증분 인덱싱 → (월초 프로필) → 카드 재생성 → 시크릿 스캔 → 업로드 → manifest.
변경이 없으면 발행을 통째로 건너뛴다 (`--force` 로 강제, `--no-publish` 로 로컬만,
`--no-collect` 로 인덱싱·발행만).

**작업 기기** — `bash scripts/collect_and_push.sh`
이 기기의 CLI 세션 수집 → tar over SSH 전송 → 원격 증분 인덱싱 → 발행 트리거.
`.env` 의 `MEMTRAIL_SERVER`(ssh 알리아스)를 본다. 전송은 tailnet 안에서만 일어난다.

전송은 **두 갈래**다.

| 대상 | 방식 | 왜 |
|---|---|---|
| `conversations/` `memory/` `canon/` | 통짜 tar | 작아서 신규 선별 없이 밀어도 수 초다 |
| `archive/` | **증분** (`--newer-mtime`) | 수백 MB~GB 라 통짜로 넣으면 매일 전부 흐른다 |

`archive/` 가 전송 대상인 이유는 그 안이 **이 기기에서 얼린 원본**이기 때문이다. 세션
JSONL 은 그 세션을 돌린 기계에만 생기고 5주 뒤 사라지므로, 동결본까지 같은 기계에만
두면 `archive_code_sessions.py` 의 존재 이유가 없어진다 (실측 2026-08-27: 작업 기기
1.8GB / 상시 PC 62MB, 겹치는 것 0 — 재취득 경로가 없는 588MB 가 사본 하나로 있었다).

증분 기준은 **지난 성공 push 시각**(`logs/last_push.stamp`)이다. STAMP 는 성공했을
때만 갱신되므로 실패한 회차의 몫도 다음 회차가 함께 가져간다.

전송 정책 둘: **삭제는 전파되지 않고**(tar 덮어쓰기), **상시 PC 쪽이 더 최신이면 안 덮는다**
(`--keep-newer-files`). 후자가 없으면 웹 수집을 상시 PC 가 하는 구조에서 작업 기기의 낡은
사본이 그날 수집분을 되돌린다.

스케줄링은 OS 에 맡긴다 — 맥 launchd(`scripts/com.memex.drive-sync.plist` 참고),
리눅스 systemd timer, 윈도우 작업 스케줄러.

> **놓친 날 따라잡기 옵션을 반드시 켤 것.** 작업 스케줄러의 `StartWhenAvailable` 같은 것.
> 늘 켜져 있지 않은 기기라면 새벽 시간대에 잡을 두면 매번 건너뛴다.

수집 단계의 두 플래그가 중요하다:

- `--skip-today` — 오늘 쓰이고 있는 세션은 진행 중일 수 있어 스냅샷으로 굳히지 않는다.
- `--refresh` — 어제까지의 세션 중 중간 스냅샷으로 남은 md 를 완전판으로 따라잡는다.

내용이 바뀐 파일 목록(`--changed-list`)은 `index_incremental.py` 로 넘어가 **stale 청크를
정리**한다. 파일명이 그대로면 "미인덱싱 파일 추가" 단계에 안 걸려 옛 청크가 조용히 남는다.

## 카드 생성

`scripts/build_memory_cards.py` — 새 대화만 골라 카드를 만들고 recent 로 조립한다.

- **낭독 창은 날짜가 아니라 토큰 예산이 정한다.** `RECENT_DAYS=90` 은 카드를 **고르는**
  범위이고, 그중 `RECENT_TOKEN_BUDGET`(48,000 tok)에 드는 만큼만 최신부터 담는다.
  그래서 실효 창은 90일보다 훨씬 짧다 — 2026-08-19 실측 대화 135건·카드 188장, 약 3주.
  **"최근 90일 카드 전문" 이라고 읽으면 안 된다**: 창 밖이라고 기록이 없는 게 아니라
  낭독 층에 없는 것뿐이고, 원문은 `conversations/` 에 그대로 있어 검색이 담당한다.
- 예산을 넘기면 **카드 단위**로 오래된 쪽부터 뺀다 (2026-08-15 이전엔 달 단위로
  통째 밀어내 48,000 중 31,005 만 쓰면서 326장을 버렸다). 최신 쪽을 남기되 중간을
  건너뛰지 않아 창이 연속 구간으로 유지된다.
- **월별 롤업(`archive.md`)은 2026-08-15 폐지.** 회상이 검색 기반이라 과거를
  미리 읽어둘 필요가 없다는 판단. 기존 `rollups/` 와 Drive 사본은 지우지 않고
  남겨뒀다 (되돌릴 여지). 잃은 것도 있다 — 롤업의 `결정적 대화` 슬롯은 어휘가
  안 맞아 검색이 놓치는 단발 인생 사건을 시간축으로 훑어 건졌다.
- **인증은 구독 CLI 로그인.** 종량제 API 키 불필요. PATH 에 `claude` 가 없으면
  `CLAUDE_BIN` 으로 경로 지정.
- CLI 가 없거나 실패하면 발췌 스텁 카드로 폴백 (로그에 남는다).

> **macOS 함정**: claude CLI 자격증명이 Keychain 에 있어 **SSH 세션에서는 못 읽는다.**
> 원격으로 카드 생성을 트리거하려면 SSH 직접 실행이 아니라 GUI 세션을 경유해야 한다
> (`launchctl kickstart gui/$(id -u)/<label>`). 최초 로그인도 화면 터미널에서 `claude` → `/login`.

## 업로드 제외 (Drive 에 올라가지 않는 것)

- `.env`
- `memory_db/` — ChromaDB 인덱스는 파생물. 날아가면 `reindex` 명령으로 재빌드
- rclone 설정 파일 (**OAuth 토큰 포함**): `%APPDATA%\rclone\rclone.conf` /
  macOS·리눅스 `~/.config/rclone/rclone.conf`
- 시크릿 패턴이 검출된 파일 — [`security.md`](security.md) 참고

## 웹 대화 수집 (선택)

claude.ai 웹 대화는 로그인된 브라우저가 필요해 별도 경로다.
`scripts/collect_web_via_cdp.py --index` 가 Chrome 을 DevTools Protocol 로 직접 몰아
고정 JS 를 주입하고 변환·색인까지 한다 — **LLM 도 사람 승인도 안 낀다.**

전제: 전용 Chrome 프로필이 claude.ai 에 로그인돼 있을 것. 최초 1회 `--login` 으로 열어
사람이 로그인한다. Chrome 136+ 는 기본 프로필에서 `--remote-debugging-port` 를 무시하므로
전용 프로필이 강제된다.

실측으로 밟은 함정 넷 (같은 걸 만들 사람을 위해 남긴다):

1. WS 핸드셰이크는 `suppress_origin` 없으면 403.
2. macOS 는 SSH 에서 직접 exec 하면 Aqua 세션에 못 붙는다 — `open -na` 필요.
3. 같은 프로필로 두 번째 Chrome 을 띄우면 기존 인스턴스에 흡수돼 포트가 안 열린다.
   먼저 `pkill` 해야 하는데, 이때 패턴이 `--` 로 시작하면 pkill 이 옵션으로 읽어 조용히 실패한다.
4. 갓 뜬 탭은 `about:blank` 라 `document.cookie` 가 SecurityError.

발행(04시경)보다 **앞선 시각**에 두어야 그날 수집분이 카드에 실린다.

## 원격 관리

집 밖에서 그 PC 를 만져야 할 때가 있다. 방법은 [`security.md` 의 원격 관리 절](security.md#원격-관리는-tailscale-로)
에 정리했다 — 요약하면 **포트포워딩·DDNS 없이 Tailscale 로만** 들어간다.

회상 자체는 원격 접속과 무관하다. 클라이언트는 Drive 우편함에만 편지를 넣으므로,
바깥에서 회상하려고 그 PC 에 접근할 일은 없다.

## 맨손 복구 — rclone 하나로

기기가 날아가 **리포도 Python 도 없을 때** 쓰는 절차. `scripts/restore_from_drive.py`
가 하는 일과 동등하다 — 그 스크립트는 이 명령들에 가드·건수 집계·재빌드 연동을
얹은 것뿐이다. 복구가 필요한 상황은 대개 그 스크립트를 돌릴 환경조차 없는
상황이라, 여기 맨손 경로를 남긴다.

```bash
DST="${MEMEX_DATA_DIR:?데이터 폴더 경로를 지정할 것}"

rclone copy "gdrive:ClaudeMemory/canon"              "$DST/canon"
rclone copy "gdrive:ClaudeMemory/threads"            "$DST/threads"
rclone copy "gdrive:ClaudeMemory/raw/conversations"  "$DST/conversations"
rclone copy "gdrive:ClaudeMemory/raw/memory"         "$DST/memory"
rclone copy "gdrive:ClaudeMemory/raw/cards"          "$DST/cards"
rclone copy "gdrive:ClaudeMemory/raw/trail"          "$DST/trail"
rclone copy "gdrive:ClaudeMemory/raw/rollups"        "$DST/rollups"
rclone copy "gdrive:ClaudeMemory/raw/archive"        "$DST/archive"
```

**받은 뒤 반드시 할 것** — 스크립트가 대신 챙겨주던 부분이다:

1. **중복 정리.** 발행은 삭제를 전파하지 않아 Drive 에는 로컬에서 지운 세션이
   남아 있다. 그대로 두면 같은 대화가 두 벌로 인덱싱된다 (실측 11세션).
   `python scripts/dedupe_sessions.py --apply --cards`
2. **인덱스 재빌드.** `memory_db` 는 파생물이라 Drive 에 올리지 않는다.
   `python scripts/rebuild_memory_db.py --md "$DST/conversations" --out "$DST/memory_db"`
   임베딩 모델이 인덱싱 당시와 같아야 한다 — 다르면 에러 없이 유사도만 무의미해진다.
3. **대상 폴더가 비어 있는지 먼저 확인.** `rclone copy` 는 같은 이름을 덮어쓴다.
   스크립트에는 비어 있지 않으면 중단하는 가드가 있지만 맨손 경로엔 없다.

이 목록은 손으로 관리하지 않는다 — 어긋나면 복구가 조용히 실패하므로
`python scripts/restore_from_drive.py --print-commands` 가 같은 출처(코드의
`ROOT_DIRS`·`RAW_DIRS`)에서 그대로 찍어준다. 층이 늘거나 줄면 그 출력으로 갱신할 것.
