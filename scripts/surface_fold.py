"""
surface_fold.py — 같은 세션이 두 표면에서 두 벌 수집되는 것을 접는다 (cc ↔ cw).

왜 생기나 (2026-08-31 실사용 발견): 데스크톱 앱 기본 탭이 "채팅 및 Cowork" 라서
Claude Code 세션이 **로컬 JSONL**(`__cc_<uuid8>`) 로도, 클라우드 `/v1/code/sessions`
반출(`__cw_<cse8>`) 로도 들어온다. 두 경로의 세션 id 가 서로 달라 conv_key 의
sid 판정으로는 안 접히고, 같은 대화가 인덱스에 두 벌 — 검색 hits 에 두 번 뜬다.

    2026-08-29_C--binder_예제_기능_추가__cc_1a2b3c4d.md   (로컬 JSONL)
    2026-08-29_예제_기능_추가__cw_01AbCdEf.md              (클라우드 반출)

**제목으로 접지 않는다** — 위 실측 쌍이 보여주듯 cc 파일명에는 프로젝트 슬러그가
붙어 같은 세션도 제목이 안 맞는다. 정체 판정은 발화로 한다:

  ① 첫 사용자 발화 두 개의 슬러그가 접두로 맞물린다 (합쳐 20자 이상).
  ② 짧은 쪽의 **마지막** 사용자 발화가 긴 쪽 어딘가에 있다 — 같은 세션의
     스냅샷 두 장은 한쪽이 다른 쪽의 접두이므로 항상 참이고, "같은 첫 발화로
     다시 시작한 별개 세션"(재시도) 은 이후 갈라져 대개 거짓이다. 접기는
     되돌릴 수 없으니 이 두 번째 관문이 필요하다.

접은 뒤 남는 쪽(keeper)은 **cc** 다 — 로컬 JSONL 이 원본 계열이고(발화 시각 보존,
archive 로 원본 동결) 스냅샷이 뒤처져 있어도 다음 --refresh 가 따라잡는다.
keeper 헤더에 두 줄을 남긴다:

    - surfaces: code, cowork
    - cowork_id: cse_...

`surfaces` 는 이 대화가 열린 자리의 기록이고, `cowork_id` 는 다음 클라우드 반출이
같은 세션을 다시 md 로 만들지 않게 하는 열쇠다 (convert_cowork_sessions 가 읽는다).
unpack 의 --refresh 재기록은 이 두 줄을 이어 옮긴다 (carry_annotations).

쓰는 곳: dedupe_sessions.py (이미 들어온 중복 정리) ·
convert_cowork_sessions.py (수집 시점 예방) · unpack_claude_code_jsonl.py (주석 보존).
순수 stdlib — 데몬이 직접 import 하지는 않는다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conv_key import slug  # noqa: E402
from turn_reader import read_text, turn_chunks  # noqa: E402

CSE_ID_RE = re.compile(r"^- (?:ID|cowork_id): (cse_[A-Za-z0-9]+)\s*$", re.M)
ANNOT_RE = re.compile(r"^- (?:surfaces|cowork_id): .+$", re.M)
MIN_FP = 20          # 첫 발화 지문 최소 길이 (dedupe_sessions 와 같은 기준)
MIN_TAIL = 10        # ② 관문의 마지막 발화 최소 길이


def user_slugs_from_md(path: Path) -> list[str]:
    """대화 md → 사용자 발화 슬러그 목록 (빈 발화 제외)."""
    return [s for c in turn_chunks(read_text(path)) if (s := slug(c["user"]))]


def same_session(users_a: list[str], users_b: list[str]) -> bool:
    """두 사용자 발화 흐름이 같은 세션의 스냅샷인가 (①+② — 위 모듈 주석).

    입력은 이미 슬러그다. 어느 한쪽이 비었으면 **모른다 = 아니다** (접기는
    되돌릴 수 없다 — dedupe_sessions.same_conversation 과 같은 보수 원칙).
    """
    if not users_a or not users_b:
        return False
    fp_a, fp_b = "".join(users_a[:2])[:400], "".join(users_b[:2])[:400]
    if len(fp_a) < MIN_FP or len(fp_b) < MIN_FP:
        return False
    if not (fp_a.startswith(fp_b) or fp_b.startswith(fp_a)):
        return False
    short, long_ = (users_a, users_b) if len(users_a) <= len(users_b) else (users_b, users_a)
    tail = short[-1][:200]
    if len(tail) < MIN_TAIL:
        # 마지막 발화가 너무 짧으면("응" 등) 그 앞 발화까지 이어 본다
        tail = ("".join(short[-2:]))[:200] if len(short) >= 2 else tail
        if len(tail) < MIN_TAIL:
            return len(short) == 1          # 한 턴짜리는 ① 만으로 판정
    return tail in "".join(long_)


def cowork_id_of(text: str) -> str | None:
    """md 본문(머리)에서 cse_ 세션 id — cw 파일의 `- ID:` 또는 keeper 의 `- cowork_id:`."""
    m = CSE_ID_RE.search(text[:600])
    return m.group(1) if m else None


def _header_insert_at(lines: list[str]) -> int:
    """헤더 메타 블록(`- ` 줄 연쇄)의 끝 — 그 뒤에 주석 줄을 넣는다."""
    last_meta = 0
    for i, line in enumerate(lines):
        if i == 0 and line.startswith("# "):
            last_meta = 1
            continue
        if not line.strip():
            continue
        if line.startswith("- "):
            last_meta = i + 1
            continue
        break                                # 본문(## 턴 헤더·--- 등) 시작
    return last_meta


def insert_annotations(text: str, cse_id: str) -> str:
    """`- surfaces:`/`- cowork_id:` 두 줄을 헤더에 넣는다. 이미 있으면 그대로."""
    if f"cowork_id: {cse_id}" in text[:600]:
        return text
    lines = text.split("\n")
    at = _header_insert_at(lines)
    add = []
    if not re.search(r"^- surfaces: ", text[:600], re.M):
        add.append("- surfaces: code, cowork")
    add.append(f"- cowork_id: {cse_id}")
    return "\n".join(lines[:at] + add + lines[at:])


def annotate_cowork(path: Path, cse_id: str) -> bool:
    """keeper(cc md)에 표면 주석을 기록한다. mtime 은 보존 — 헤더 두 줄은 대화
    내용이 아니므로 카드 재생성을 일으키지 않아야 한다 (턴 파서는 첫 `## ` 이전을
    통째로 건너뛰므로 턴 번호·청크에도 영향이 없다). 반환: 실제로 썼는가."""
    text = read_text(path)
    new = insert_annotations(text, cse_id)
    if new == text:
        return False
    st = path.stat()
    path.write_text(new, encoding="utf-8", newline="\n")
    os.utime(path, (st.st_atime, st.st_mtime))
    return True


def carry_annotations(old_text: str, new_text: str) -> str:
    """재렌더된 md 에 기존 헤더 주석(`surfaces`/`cowork_id`)을 이어 옮긴다.

    unpack --refresh 는 JSONL 에서 통째로 다시 그리므로, 이게 없으면 접기 주석이
    매 갱신마다 지워지고 — 다음 클라우드 반출이 같은 세션을 또 md 로 만든다.
    """
    kept = ANNOT_RE.findall(old_text[:600])
    if not kept:
        return new_text
    lines = new_text.split("\n")
    at = _header_insert_at(lines)
    body = "\n".join(lines[:at])
    add = [ln for ln in kept if ln not in body]
    if not add:
        return new_text
    return "\n".join(lines[:at] + add + lines[at:])


def find_cc_partner(conv_dir: Path, date: str, cw_users: list[str]) -> list[Path]:
    """같은 날짜의 cc 파일 중 같은 세션으로 판정되는 것들 (보통 0 또는 1개).

    둘 이상이면 호출자가 보류한다 — 같은 날 같은 발화로 시작한 cc 가 여럿이라는
    뜻이라, 기계가 고를 자리가 아니다.
    """
    out = []
    for p in sorted(conv_dir.glob(f"{date}_*__cc_*.md")):
        try:
            if same_session(cw_users, user_slugs_from_md(p)):
                out.append(p)
        except OSError:
            continue
    return out
