#!/usr/bin/env bash
# pii_gate.sh — 추적 파일에 개인 기록이 남아 있는지 본다. 공개 전에, 그리고 평소에.
#
# 왜 필요한가:
#   `.gitignore` 는 **데이터**를 막지, 문서와 주석에 예시로 끼어든 개인 기록까지
#   막지는 못한다. 실측을 적다 보면 "무엇에 관한 일이었는지" 가 같이 적히기 때문이다
#   (2026-08-27 전면 검토에서 추적 파일 18개 56곳이 나왔다).
#
#   원칙 하나로 줄이면 — **실측에 필요한 건 구조지 사안이 아니다.** 무엇이 어떻게
#   망가졌는지는 남기고, 무엇에 관한 일이었는지는 뺀다.
#
# 패턴 목록은 그 자체가 개인 데이터라 리포가 아니라
# $MEMEX_DATA_DIR/config/pii_patterns.txt 에 둔다 (index_incremental.py 와 같은 규칙).
# 한 줄에 하나, 리터럴 문자열. `#` 로 시작하는 줄과 빈 줄은 무시한다.
# **새 실측을 적을 때 사안 이름이 들어갔다 싶으면 그 목록에 한 줄 더할 것.**
#
# 사용법:
#   bash scripts/pii_gate.sh              # 추적 파일 (= 공개되는 것)
#   bash scripts/pii_gate.sh --messages   # 커밋 메시지도 같이 (HEAD 부터 전부)
#
# 걸리면 종료 코드 1 이다 — 훅이나 CI 에 그대로 걸 수 있다.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -z "${MEMEX_DATA_DIR:-}" ] && [ -f "$ROOT/.env" ]; then
  MEMEX_DATA_DIR=$(grep -E '^MEMEX_DATA_DIR=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '\r' || true)
fi
MEMEX_DATA_DIR="${MEMEX_DATA_DIR:-$ROOT}"
PATTERNS="$MEMEX_DATA_DIR/config/pii_patterns.txt"

if [ ! -f "$PATTERNS" ]; then
  echo "PII 패턴 목록이 없다: $PATTERNS" >&2
  echo "한 줄에 한 패턴씩 적을 것. 목록 자체가 개인 데이터이므로 리포에 두지 말 것." >&2
  exit 1
fi

WITH_MSG=0
[ "${1:-}" = "--messages" ] && WITH_MSG=1

hits=0
while IFS= read -r pat; do
  case "$pat" in ''|'#'*) continue ;; esac

  # 추적 파일만 본다 — 공개되는 것이 그것뿐이다.
  # -I 바이너리 제외, -F 리터럴 (패턴에 정규식 특수문자가 섞여도 안전)
  if out=$(git grep -nIF -- "$pat" $(git ls-files) 2>/dev/null); then
    echo "── $pat  (파일)"
    printf '%s\n' "$out" | sed 's/^/     /'
    hits=$((hits + 1))
  fi

  if [ "$WITH_MSG" = 1 ]; then
    if out=$(git log --format="%h %s%n%b" | grep -nF -- "$pat" 2>/dev/null); then
      echo "── $pat  (커밋 메시지)"
      printf '%s\n' "$out" | sed 's/^/     /'
      hits=$((hits + 1))
    fi
  fi
done < "$PATTERNS"

if [ "$hits" -gt 0 ]; then
  echo >&2
  echo "PII 게이트: $hits 건. 정리 전에는 공개하지 말 것." >&2
  exit 1
fi

n=$(git ls-files | wc -l | tr -d ' ')
if [ "$WITH_MSG" = 1 ]; then
  echo "PII 게이트 통과 — 추적 파일 ${n}개 + 커밋 메시지 $(git rev-list --count HEAD)개, 걸린 패턴 없음"
else
  echo "PII 게이트 통과 — 추적 파일 ${n}개, 걸린 패턴 없음"
fi
