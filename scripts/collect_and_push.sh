#!/usr/bin/env bash
# 작업 기기 → 상시 PC 단방향 push. **작업 기기**(노트북·데스크톱)에서 돈다.
#
# 왜 필요한가: Claude Code 세션 기록은 그 대화를 나눈 기기의 디스크에만 있다.
# 상시 PC 가 아무리 켜져 있어도 노트북에서 나눈 대화는 스스로 알 수 없으므로,
# 작업 기기가 밀어 넣어줘야 한다. (웹 대화는 반대로 상시 PC 가 직접 걷는다 —
# scripts/collect_web_via_cdp.py)
#
#   bash scripts/collect_and_push.sh              # 변경 없으면 push 생략
#   bash scripts/collect_and_push.sh --force
#   bash scripts/collect_and_push.sh --no-publish # 전송·인덱싱만, 발행은 상시 PC 스케줄에 맡김
#
# 설정 (.env 또는 환경변수):
#   MEMTRAIL_SERVER       ssh 알리아스. tailnet 주소를 ~/.ssh/config 에 적어두는 것을 권장
#   MEMTRAIL_SERVER_DIR   상시 PC 의 리포 경로 (기본 ~/memtrail)
#   MEMTRAIL_SERVER_LAUNCHD  (맥 서버 전용) 발행 launchd 라벨. 있으면 kickstart 로 부른다
#
# 전송은 SSH 다. 포트를 여는 대신 **Tailscale tailnet 안에서** 붙는 것을 전제한다
# — docs/security.md 참고.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
export PYTHONIOENCODING=utf-8

# .env 에 적어둔 설정을 읽는다. 우선순위: 셸 export > 리포 .env > 데이터 폴더 .env.
# 리포 .env 가 먼저인 이유: MEMEX_DATA_DIR 자체가 거기 적히므로, 데이터 폴더 .env 만
# 읽어서는 데이터가 어디 있는지부터 알 수 없다 (코드와 데이터가 한 폴더이던 시절에는
# 같은 파일이라 이 순서 문제가 드러나지 않았다).
read_env() {
  [ -f "$1" ] || return 0
  while IFS='=' read -r k v; do
    # 값의 CR(윈도우 편집기 저장분)과 따옴표를 벗긴다 — 안 벗기면 ssh 호스트명
    # 끝에 \r 이 붙어 "Could not resolve hostname" 이 나는데 눈에는 안 보인다.
    v=${v%$'\r'}
    v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
    [ -n "${!k:-}" ] || export "$k=$v"
  done < <(grep -E '^(MEMEX_DATA_DIR|MEMTRAIL_[A-Z_]+)=' "$1" || true)
}
read_env "$ROOT/.env"
export MEMEX_DATA_DIR="${MEMEX_DATA_DIR:-$ROOT}"
read_env "$MEMEX_DATA_DIR/.env"

SERVER="${MEMTRAIL_SERVER:-}"
SERVER_DIR="${MEMTRAIL_SERVER_DIR:-~/memtrail}"
LAUNCHD="${MEMTRAIL_SERVER_LAUNCHD:-}"
if [ -z "$SERVER" ]; then
  echo "MEMTRAIL_SERVER 가 없다. .env 에 상시 PC 의 ssh 알리아스를 적을 것." >&2
  exit 2
fi

if   [ -x "$ROOT/.venv/bin/python" ];         then PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"
else PY=$(command -v python3 || command -v python); fi

CHANGED="$MEMEX_DATA_DIR/logs/daily_changed.txt"
STAMP="$MEMEX_DATA_DIR/logs/last_push.stamp"
mkdir -p "$MEMEX_DATA_DIR/logs"

FORCE=""; PUBLISH=1
while [ $# -gt 0 ]; do
  case "$1" in
    --force)      FORCE=1;   shift ;;
    --no-publish) PUBLISH=0; shift ;;
    *) echo "[push] 알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

say() { echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
say "수집 시작 (server=$SERVER)"

# ── 1. 이 기기의 Claude Code 세션 수집 ─────────────────────────────────────
# --skip-today: 오늘 쓰이고 있는 세션은 진행 중이라 스냅샷으로 굳히지 않는다.
# --refresh   : 어제까지의 중간 스냅샷을 완전판으로 따라잡는다.
"$PY" unpack_claude_code_jsonl.py \
  --out "$MEMEX_DATA_DIR/conversations" \
  --refresh --skip-today --changed-list "$CHANGED"

# 원본 JSONL 을 얼린다. `~/.claude/projects` 는 **그 기계에만 있다** — 어느 날
# 작업 기기를 바꾸면서 그 이전 코드 세션 250건(7,565턴)의 원본이 통째로 사라졌다
# (옛 기계는 포맷). 대화 자체는 매일 밀어넣어 살았지만 발화 시각·도구 호출은
# 못 살렸다. 기계 교체는 반드시 또 일어나고 예고가 없으므로 평소에 얼려둔다.
# md 는 원본이 아니고, 변환기가 무엇을 버렸는지는 늘 나중에 안다.
"$PY" scripts/archive_code_sessions.py || echo "[warn] 세션 원본 보관 실패 (수집은 계속)"

# ── 2. 보낼 게 있나 ────────────────────────────────────────────────────────
# 판정은 수집기 카운터가 아니라 전송 대상 트리의 mtime — 어떤 경로로 바뀌었든
# (손으로 고친 canon 포함) 한 기준으로 잡힌다.
TREES=""
for t in conversations memory canon; do
  [ -d "$MEMEX_DATA_DIR/$t" ] && TREES="$TREES $t"
done
# archive/ 도 전송 대상이다 — 이 기기에서 얼린 원본(세션 JSONL·export)이라 **재취득
# 경로가 없는데**, 여기 말고는 사본이 없었다. 다만 수백 MB~GB 라 통짜 tar 에는 넣지
# 않는다 (§3 에서 증분으로 따로 민다).
ARCHIVE=""
[ -d "$MEMEX_DATA_DIR/archive" ] && ARCHIVE="archive"
if [ -z "$FORCE" ] && [ -f "$STAMP" ] && [ ! -s "$CHANGED" ] \
   && [ -z "$(cd "$MEMEX_DATA_DIR" && find $TREES $ARCHIVE -type f -newer "$STAMP" -print -quit 2>/dev/null)" ]; then
  say "지난 push 이후 변경 없음 — 생략 (--force 로 강제)"
  exit 0
fi

# ── 3. 전송 ────────────────────────────────────────────────────────────────
# 전체 tar 파이프. 신규 선별 없이 통째로 밀어도 개인 규모에선 수 초다.
# --keep-newer-files: 상시 PC 쪽이 더 최신이면 안 덮는다. 웹 수집은 저쪽이 하므로
# 이게 없으면 이 기기의 낡은 사본이 그날 수집분을 되돌린다.
# 삭제는 전파되지 않는다 (tar 덮어쓰기) — 스토리지 사본과 같은 정책.
say "전송$TREES → $SERVER:$SERVER_DIR"
(cd "$MEMEX_DATA_DIR" && tar -cf - $TREES) \
  | ssh "$SERVER" "tar -x --keep-newer-files -f - -C $SERVER_DIR"

# archive/ 는 **증분**으로 민다. 통짜로 넣으면 세션이 매일 얼기 때문에 매번 전체가
# 흐른다 (실측 2026-08-27: 1.8GB, 2분 30초). 기준은 지난 **성공** push 시각이고,
# STAMP 는 성공했을 때만 갱신되므로 중간에 실패한 회차의 몫도 다음 회차가 함께
# 가져간다. STAMP 가 없으면(첫 실행) 조건 없이 통째로 — 그때는 그게 맞다.
if [ -n "$ARCHIVE" ]; then
  NEWER=()
  if [ -f "$STAMP" ]; then
    NEWER=(--newer-mtime "@$(stat -c %Y "$STAMP" 2>/dev/null || stat -f %m "$STAMP")")
  fi
  # 보낼 게 있는지 먼저 센다 — 없는데 ssh 를 여는 것은 매일 도는 잡에서 낭비다.
  n=$(cd "$MEMEX_DATA_DIR" && find $ARCHIVE -type f \
        $( [ ${#NEWER[@]} -gt 0 ] && echo -newer "$STAMP" ) | wc -l | tr -d ' ')
  if [ "${n:-0}" -gt 0 ]; then
    say "전송 archive (신규 ${n}개) → $SERVER:$SERVER_DIR"
    (cd "$MEMEX_DATA_DIR" && tar -cf - "${NEWER[@]}" $ARCHIVE) \
      | ssh "$SERVER" "tar -x --keep-newer-files -f - -C $SERVER_DIR"
  else
    say "archive 신규 없음 — 생략"
  fi
fi

# ── 4. 원격 증분 인덱싱 ────────────────────────────────────────────────────
# LLM 을 안 쓰므로 SSH 컨텍스트로 충분하다. 내용이 갱신된 파일 목록을 실어 보내
# stale 청크까지 정리한다 (여기서 index_as_of 가 전진한다).
if [ -s "$CHANGED" ]; then
  scp -q "$CHANGED" "$SERVER:$SERVER_DIR/logs/incoming_changed.txt"
fi
say "원격 증분 인덱싱"
ssh "$SERVER" "cd $SERVER_DIR && MEMEX_DATA_DIR=\$(pwd) \
  \$( [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3 ) \
  scripts/index_incremental.py --changed logs/incoming_changed.txt"

# ── 5. 발행 트리거 ─────────────────────────────────────────────────────────
# 카드 생성이 구독 CLI 를 쓰는데, macOS 는 그 자격증명이 Keychain 에 있어
# SSH 세션에서 못 읽는다. 그래서 맥 서버는 SSH 직접 실행이 아니라 launchd
# GUI 세션을 경유해야 한다 (MEMTRAIL_SERVER_LAUNCHD). 리눅스는 그냥 실행.
if [ "$PUBLISH" = "0" ]; then
  say "완료 (--no-publish — 발행은 상시 PC 스케줄에 맡김)"
  touch "$STAMP"; exit 0
fi
if [ -n "$LAUNCHD" ]; then
  say "발행 트리거 (launchd GUI 세션 경유: $LAUNCHD)"
  ssh "$SERVER" "launchctl kickstart \"gui/\$(id -u)/$LAUNCHD\""
  say "발행은 백그라운드로 진행된다 — 로그: ssh $SERVER 'tail -f $SERVER_DIR/logs/drive_sync.log'"
else
  say "원격 발행"
  ssh "$SERVER" "cd $SERVER_DIR && bash scripts/daily.sh --no-collect"
fi

touch "$STAMP"
say "완료"
