"""
conv_key.py — 대화 하나의 **정체(identity)** 를 파일명에서 뽑는다.

왜 필요한가: 같은 대화가 서로 다른 파일명으로 두 벌 남아 있다. 수집기의 파일명
규칙이 바뀌던 시점(`YYYYMMDD_제목_밑줄.md` → `YYYY-MM-DD_제목 공백.md`)의 것들이
개명 없이 한 번 더 저장돼, 인덱스에 두 벌 들어가고 회상 결과에서 슬롯을 두 개 먹는다.

    2025-03-14_Sample conversation title.md
    20250314_Sample_conversation_title.md

(2026-08-20 실측: 로컬 1,799건 중 7쌍. k=6 질의가 실질 5건이 되는 사고가 났다.)

판정 규칙 — 두 단계다:
  1. 꼬리에 `__<cc|cw|mr|gm>_<sid8>` 가 있으면 **그게 정체다** (코드·코워크 세션,
     Gemini 변환분).
     dedupe_sessions.py 가 쓰던 규칙과 같은 것 — 날짜·제목이 나중에 바뀌어도
     세션 id 는 안 바뀐다.
  2. 없으면(웹 대화) **날짜 + 제목 슬러그**. 날짜는 하이픈 유무를 지우고,
     제목은 영숫자·한글만 남긴다 (밑줄·공백·이모지·구두점이 규칙 변경의 차이 전부라
     이걸 지우면 두 이름이 한 값으로 모인다).

한계: 같은 날 **제목까지 같은** 서로 다른 웹 대화는 하나로 접힌다. 웹 대화 제목은
LLM 이 붙이는 요약문이라 충돌 확률이 낮고, 접혀도 손해는 슬롯 하나다 — 반대로 안
접으면 같은 대화가 답변에 두 번 실린다. 파일을 **지우는** 쪽(dedupe_sessions.py)은
이 키만으로 결정하지 않고 본문 지문까지 대조한다.
"""

import re
import unicodedata

SID_RE = re.compile(r"__(cc|cw|mr|gm|gp)_([0-9a-f]{8})$")
DATE_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})[_ .-]+")
_SLUG_RE = re.compile(r"[^0-9a-z가-힣]")


def slug(text: str) -> str:
    """비교용 슬러그 — 영숫자·한글만. 밑줄/공백/하이픈/이모지/구두점을 모두 버린다."""
    return _SLUG_RE.sub("", unicodedata.normalize("NFC", text or "").lower())


def conv_key(name: str) -> str:
    """대화 파일명 → 정체 키. 접두(`sid:`/`date:`/`name:`)로 판정 경로를 남긴다."""
    stem = unicodedata.normalize("NFC", name or "")
    if stem.endswith(".md"):
        stem = stem[:-3]
    if m := SID_RE.search(stem):
        return f"sid:{m.group(1)}_{m.group(2)}"
    if m := DATE_RE.match(stem):
        return f"date:{m.group(1)}{m.group(2)}{m.group(3)}|{slug(stem[m.end():])}"
    return f"name:{slug(stem)}"
