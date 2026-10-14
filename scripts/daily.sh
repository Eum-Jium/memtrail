#!/usr/bin/env bash
# 매일 잡 — 수집 → 증분 인덱싱 → 발행. **상시 PC**(사서를 올려둔 기기)에서 돈다.
#
#   bash scripts/daily.sh              # 변경 없으면 발행 생략
#   bash scripts/daily.sh --force      # 변경 없어도 발행 강행
#   bash scripts/daily.sh --no-publish # 로컬 수집·인덱싱만 (스토리지 안 건드림)
#   bash scripts/daily.sh --no-collect # 인덱싱·발행만 (작업 기기가 이미 밀어 넣은 뒤)
#
# 다른 기기에서 나눈 Claude Code 대화는 이 스크립트가 알 수 없다 — 그 기기가
# scripts/collect_and_push.sh 로 밀어 넣는다. 기기가 하나뿐이면 이것만 돌리면 된다.
#
# 사서 데몬(scripts/mailbox_daemon.py)은 이 스크립트가 아니라 상주로 따로 돈다.
# 이건 "쌓는 쪽", 데몬은 "꺼내 쓰는 쪽"이다.
#
# 스케줄링: 맥 launchd / 리눅스 systemd timer / 윈도우 작업 스케줄러.
# 컴퓨터가 꺼져 있어 놓친 날을 따라잡는 옵션(작업 스케줄러의 StartWhenAvailable 등)을
# 켜둘 것 — 안 그러면 노트북처럼 꺼져 있는 시간이 긴 기기에서 매번 건너뛴다.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
# 데이터 위치는 셸 export 가 우선, 없으면 리포 .env, 그것도 없으면 리포 자신.
# 값의 CR(윈도우 편집기 저장분)과 따옴표는 벗긴다 — 안 벗기면 경로 끝에 \r 이
# 붙어 이후 모든 mkdir/find 가 엉뚱한 경로를 본다.
if [ -z "${MEMEX_DATA_DIR:-}" ] && [ -f "$ROOT/.env" ]; then
  MEMEX_DATA_DIR=$(grep -E '^MEMEX_DATA_DIR=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '\r' || true)
  MEMEX_DATA_DIR=${MEMEX_DATA_DIR%\"}; MEMEX_DATA_DIR=${MEMEX_DATA_DIR#\"}
  MEMEX_DATA_DIR=${MEMEX_DATA_DIR%\'}; MEMEX_DATA_DIR=${MEMEX_DATA_DIR#\'}
fi
export MEMEX_DATA_DIR="${MEMEX_DATA_DIR:-$ROOT}"

# 리다이렉트되면 파이썬 stdout 이 콘솔 코드페이지로 나가 로그의 한글만 깨진다.
export PYTHONIOENCODING=utf-8

# .venv 가 있으면 그쪽 파이썬, 없으면 PATH 의 python3.
if   [ -x "$ROOT/.venv/bin/python" ];        then PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
else PY=$(command -v python3 || command -v python); fi

CHANGED="$MEMEX_DATA_DIR/logs/daily_changed.txt"
STAMP="$MEMEX_DATA_DIR/logs/last_publish.stamp"
mkdir -p "$MEMEX_DATA_DIR/logs"

FORCE=""; PUBLISH=1; COLLECT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --force)      FORCE=1;   shift ;;
    --no-publish) PUBLISH=0; shift ;;
    --no-collect) COLLECT=0; shift ;;
    *) echo "[daily] 알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

say() { echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
say "daily 시작 (data=$MEMEX_DATA_DIR)"

# ── 1. 수집 ────────────────────────────────────────────────────────────────
# --skip-today: 오늘 쓰이고 있는 세션은 진행 중일 수 있어 스냅샷으로 굳히지 않는다.
# --refresh   : 어제까지의 세션 중 중간 스냅샷인 md 를 완전판으로 따라잡는다.
if [ "$COLLECT" = "1" ]; then
  say "이 기기의 Claude Code 세션 수집"
  "$PY" unpack_claude_code_jsonl.py \
    --out "$MEMEX_DATA_DIR/conversations" \
    --refresh --skip-today --changed-list "$CHANGED"
fi

# 작업 기기가 push 하며 scp 로 남긴 갱신 목록도 합류시킨다 (collect_and_push.sh).
# 원본 목록은 여기서 비우지 않는다 — 인덱싱이 성공한 뒤에만 비워야, 실패한
# 회차의 목록이 남아 다음 실행이 다시 싣는다 (at-least-once).
INCOMING="$MEMEX_DATA_DIR/logs/incoming_changed.txt"
MERGED=()
if [ -s "$INCOMING" ]; then cat "$INCOMING" >> "$CHANGED"; MERGED+=("$INCOMING"); fi

# claude.ai 웹 대화는 로그인된 브라우저 조작이 필요해 별도다:
#   scripts/collect_web_via_cdp.py  (맥 상주 예시는 com.memex.web-collect.plist)
# 그쪽이 만드는 갱신 목록이 있으면 여기 합류시킨다.
for L in "$MEMEX_DATA_DIR/logs/web_changed.txt" "$MEMEX_DATA_DIR/logs/cowork_changed.txt"; do
  if [ -s "$L" ]; then cat "$L" >> "$CHANGED"; MERGED+=("$L"); fi
done
HAD_CHANGES=0
if [ -s "$CHANGED" ]; then
  sort -u -o "$CHANGED" "$CHANGED"
  HAD_CHANGES=1
  say "갱신 파일 $(wc -l < "$CHANGED" | tr -d ' ')개 — stale 청크 정리 대상"
fi

# ── 2. 증분 인덱싱 ─────────────────────────────────────────────────────────
# 내용이 바뀐 파일은 기존 청크를 지우고 다시 넣는다. 파일명이 그대로면
# "미인덱싱 파일 추가" 단계에 안 걸려 옛 청크가 조용히 남기 때문이다.
say "증분 인덱싱"
"$PY" scripts/index_incremental.py --changed "$CHANGED"
# 여기 도달 = 인덱싱 성공 (set -e) — 이제야 원본 목록을 소비한다.
for L in ${MERGED[@]+"${MERGED[@]}"}; do : > "$L"; done

# ── 3. 월초 프로필 재합성 (선택) ───────────────────────────────────────────
# 인덱싱과 분리 실행이라 실패해도 수집·발행은 계속된다.
# 예전엔 `command -v ollama` 가 조건에 붙어 있었다. 상시 PC 에 ollama 가 없어
# 매달 1일 조건이 조용히 거짓이 됐고, 실패 로그조차 안 남아 프로필이 두 달간
# 멎은 것을 아무도 몰랐다 (2026-06-22 ~ 08-15). 이제 구독 CLI 로 도므로 게이트를
# 걷어낸다 — CLI 가 없으면 스크립트가 종료 코드로 알리고 아래 || 가 받는다.
if [ "$(date +%d)" = "01" ]; then
  say "월초 프로필 재합성 (claude -p)"
  "$PY" scripts/monthly_update.py --skip-index \
    || say "프로필 재합성 실패 — 다음 달 재시도 (수집·발행은 계속)"
fi

# ── 4. 발행 ────────────────────────────────────────────────────────────────
# 클라이언트가 원문까지 열어보려면 Drive 에 사본이 있어야 한다 (깊이 읽기).
# 변경이 없으면 통째로 건너뛴다 — 카드 재생성·업로드·manifest 재빌드가 헛돌지 않게.
if [ "$PUBLISH" = "0" ]; then
  say "daily 완료 (--no-publish)"
  exit 0
fi
# 갱신 신호는 인덱싱 전에 기록해 둔 HAD_CHANGES 를 쓴다 — $CHANGED 파일 자체는
# index_incremental 이 소비(.done 개명)해서 이 시점엔 항상 없다. mtime 판정만
# 남기면 tar --keep-newer-files 로 도착한 밀린 대화(mtime 이 과거)가 발행에서
# 빠진다. 소급 재생성 플래그가 대기 중일 때도 발행을 건너뛰지 않는다.
if [ -z "$FORCE" ] && [ -f "$STAMP" ] && [ "$HAD_CHANGES" = "0" ] \
   && [ ! -e "$MEMEX_DATA_DIR/logs/regen_cards.flag" ] \
   && [ -z "$(cd "$MEMEX_DATA_DIR" && find conversations memory canon -type f -newer "$STAMP" -print -quit 2>/dev/null)" ]; then
  say "지난 발행 이후 변경 없음 — 생략 (--force 로 강제)"
  exit 0
fi

say "Drive 발행 (카드 재생성 → 시크릿 스캔 → 업로드)"
"$PY" scripts/sync_memory_to_drive.py

# manifest 는 업로드 뒤에 만들어야 오늘 신규 파일의 fileId 가 잡힌다.
say "manifest 갱신 (로컬 경로 ↔ Drive fileId)"
"$PY" scripts/build_manifest.py

touch "$STAMP"
say "daily 완료"
