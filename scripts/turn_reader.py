"""
turn_reader.py — 대화 원문에서 **턴 구간만** 잘라 읽는다 (의존성 0).

왜: 사서가 돌려주는 앵커는 `턴 12` 인데, 정작 그 대목을 읽으려면 파일을 통째로
받아야 했다. 8/18 세션 하나가 컨텍스트를 크게 먹은 원인이 이것이다. 좌표가 구간인데
읽기가 전량이면 깊이 읽기의 비용이 대화 길이에 비례해 버린다.

**턴 번호는 memory_pipeline.parse_md_content 의 `turn_index` 와 같은 값이어야 한다** —
앵커가 그 값으로 오기 때문이다. 그래서 청크 규칙을 그대로 옮겼다:
User+Assistant 쌍이 한 청크, 짝 없는 턴도 단독 청크. test_turn_reader.py 가 실제
대화 파일들로 두 구현의 일치를 대조한다 (langchain 의존을 데몬에 들이지 않으려고
복제한 것이라, 대조가 유일한 안전장치다).
"""

import re
import unicodedata
from pathlib import Path

TURN_HDR = re.compile(r"^##[ \t]+(👤|🧑|🤖)[^\n]*$", re.MULTILINE)


def split_turns(content: str) -> list[tuple[str, str]]:
    """원문 → [(role, text)]. role 은 'user' | 'assistant'."""
    out: list[tuple[str, str]] = []
    marks = list(TURN_HDR.finditer(content))
    for j, m in enumerate(marks):
        end = marks[j + 1].start() if j + 1 < len(marks) else len(content)
        role = "assistant" if m.group(1) == "🤖" else "user"
        out.append((role, content[m.end():end].strip()))
    return out


def turn_chunks(content: str) -> list[dict]:
    """원문 → [{turn, user, assistant}] — turn 이 인덱스의 turn_index 와 같다."""
    chunks: list[dict] = []

    def emit(user_text: str, assistant_text: str) -> None:
        if not user_text and not assistant_text:
            return
        chunks.append({"turn": len(chunks), "user": user_text,
                       "assistant": assistant_text})

    pending: str | None = None
    for role, text in split_turns(content):
        if role == "user":
            if pending is not None:
                emit(pending, "")            # 연속 User → 앞엣것 단독
            pending = text
        else:
            emit(pending or "", text)
            pending = None
    if pending is not None:
        emit(pending, "")                    # 답변 없이 끝난 마지막 질문
    return chunks


def read_text(path: Path) -> str:
    """parse_md_file 과 같은 인코딩 폴백. 실패해도 예외 대신 대체문자로 읽는다."""
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def _size(c: dict) -> int:
    return len(c["user"].encode("utf-8")) + len(c["assistant"].encode("utf-8"))


def read_range(path: Path, turn_from: int, turn_to: int,
               max_bytes: int = 20_000, tail: int | None = None) -> dict:
    """[turn_from, turn_to] 구간을 잘라 돌려준다. `tail=N` 이면 **마지막 N턴**.

    반환: {total_turns, turn_from, turn_to, truncated_at, truncated_reason, turns}
    - 범위는 파일 실제 턴 수로 클램프한다 (넘겨 부르면 빈 결과가 아니라 있는 만큼).
    - max_bytes 를 넘으면 **턴 단위로 자른다.** 반쪽 턴을 내보내면 인용이 문장
      중간에서 끊긴다. 예산은 **UTF-8 바이트**다 (2026-08-31, 문자 수에서 전환):
      한글은 글자당 3바이트라 문자 예산은 내용에 따라 3배씩 출렁였다. 20KB 는
      커넥터 read_file_content 무손실 수신이 실측된 크기(20,915B 실답장)다.

    **`tail` 은 예산을 앞에서 깎는다.** "저번에 하던 얘기 이어서" 류가 필요로 하는
    것은 대화의 **끝**인데, 앞에서부터 채우면 예산이 다 떨어졌을 때 남는 게 꼬리가
    아니라 그 구간의 앞머리다 — 꼬리를 달라고 해놓고 앞머리를 받는다. 그래서 tail
    모드는 마지막 턴부터 거꾸로 채우고, 모자라면 **앞쪽을 버린다.**
    """
    content = unicodedata.normalize("NFC", read_text(path))
    chunks = turn_chunks(content)
    total = len(chunks)
    if total == 0:
        return {"total_turns": 0, "turn_from": 0, "turn_to": 0,
                "truncated_at": None, "truncated_reason": None, "turns": []}

    if tail:
        lo, hi = max(0, total - tail), total - 1
    else:
        lo = max(0, min(turn_from, total - 1))
        hi = max(lo, min(turn_to, total - 1))

    window = chunks[lo:hi + 1]
    out, used, cut, reason = [], 0, None, None

    if tail:
        # 끝에서부터 거꾸로 채운다 — 예산이 모자라면 앞쪽이 잘린다.
        for c in reversed(window):
            if out and used + _size(c) > max_bytes:
                reason = (f"바이트 예산 {max_bytes:,}B 초과 — 꼬리를 지키려고 "
                          f"앞쪽 턴을 잘랐다 (요청 tail={tail})")
                break
            out.insert(0, c)
            used += _size(c)
    else:
        for c in window:
            if out and used + _size(c) > max_bytes:
                cut = c["turn"] - 1
                reason = (f"바이트 예산 {max_bytes:,}B 초과 — 턴 {c['turn']} 부터 "
                          f"이어서 다시 부를 것")
                break
            out.append(c)
            used += _size(c)

    return {"total_turns": total,
            "turn_from": (out[0]["turn"] if out else lo),
            "turn_to": (out[-1]["turn"] if out else lo),
            "truncated_at": cut, "truncated_reason": reason, "turns": out}
