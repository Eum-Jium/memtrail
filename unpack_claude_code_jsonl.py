"""
Claude Code 세션 JSONL → conversations/*.md 변환기.

`~/.claude/projects/<project-slug>/<session-uuid>.jsonl` 형태의 세션 로그를
`memory_pipeline.parse_md_content` 가 받는 Markdown 포맷으로 풀어준다:

    # <project-slug> — <aiTitle>

    - 생성일: YYYY-MM-DD
    - ID: <sessionId>
    - source: claude_code

    ## 👤 User
    ...

    ## 🤖 Assistant
    ...

기본 동작:
  - 메인 세션 JSONL 만 처리 (서브에이전트 jsonl 은 무시 — `*/subagents/*.jsonl`).
  - `type=user` & content 가 str & isSidechain=false 인 항목만 사용자 발화.
    (tool_result 자동 메시지는 제외)
  - **하네스가 user 레코드로 주입하는 비발화 컨텐츠는 저장 단계에서 필터**
    (`<task-notification>`, `<system-reminder>`, `<local-command-stdout>`,
    auto-compact 이어달리기 요약, `[Request interrupted by user]` 등).
    슬래시 커맨드 주입(`<command-name>` 류)은 사용자가 실제 친
    `/name args` 한 줄로 복원 — `/<커맨드> 첫발화` 처럼 args 가
    실발화인 경우를 보존하기 위함.
  - `type=assistant` 의 `text` 블록만 합쳐서 어시스턴트 발화.
  - `thinking` 과 `tool_use` 는 기본 drop. `--keep-tools` 로 한 줄 요약 켤 수 있음.
  - 모든 세션은 `conversations/` 한 곳으로 간다. 예전에는 특정 슬래시
    커맨드로 연 세션만 별도 폴더로 격리했으나 **2026-08-15 폐지** —
    어차피 전부 개인 대화라 한 층만 다르게 다룰 이유가 없었다.

사용법:
  python unpack_claude_code_jsonl.py                          # 기본 위치 → ./conversations
  python unpack_claude_code_jsonl.py --out $DATA/conversations
  python unpack_claude_code_jsonl.py --projects ~/.claude/projects --out ...
  python unpack_claude_code_jsonl.py --keep-tools             # tool_use 한 줄 요약 포함
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from surface_fold import carry_annotations  # noqa: E402 — cc↔cw 접기 주석 보존


# memory_pipeline.VALID_SOURCES 와 일치해야 함 — 변경 시 양쪽 동시 갱신
SOURCE_NAME = "claude_code"
# 출력 파일명에서 source 를 식별하는 짧은 토큰 (예: "...__cc_<sid8>.md")
FILENAME_TAG = "cc"

# 2026-08-15 이전에 옛 격리 세션이 쓰던 파일명 태그. 격리를
# 폐지하면서 신규 산출은 전부 FILENAME_TAG 로 통일했지만, 이미 conversations/ 로
# 옮긴 옛 파일이 이 태그를 달고 있다. 같은 세션이 두 벌 생기지 않으려면 재사용
# 글롭이 옛 태그도 봐야 한다 (index_incremental.source_for 도 __mr_ 를 계속 받는다).
LEGACY_TAGS = ("mr",)

# memex 자체 툴링이 `claude -p` 로 호출해 생긴 세션 마커.
# build_memory_cards.py 의 카드/롤업 생성 프롬프트가 그대로 user 발화로 남는데,
# 이건 사용자의 대화가 아니라 파이프라인 부산물이므로 통째로 버린다.
# (안 거르면 카드 생성 → 세션 로그 → 다음 unpack 때 대화로 편입 → 그 대화의
#  카드를 또 생성하는 재귀 오염이 된다.)
# 이 패턴은 scripts/build_memory_cards.py 의 CARD_PROMPT/ROLLUP_PROMPT 문구와
# 맞물려 있다 — 프롬프트 문구를 바꾸면 여기도 같이 바꿀 것 (2026-08 카드 스키마
# 확장은 첫 문장을 안 건드려서 이 마커 그대로 유효).
_TOOLING_MARKER = re.compile(
    r"이 대화를 아래 형식의 '카드' 한 장으로 요약하라"
    r"|한 달치 대화 카드 모음이다"
)

# CLI 인증이 끊긴 상태에서 `claude -p` 를 부르면 어시스턴트 발화가 로그인 안내
# 한 줄뿐인 껍데기 세션이 남는다. 대화가 아니므로 같이 버린다.
# (인증 만료 시 야간 sync 가 이 껍데기를 대량 생산하는 걸 막는 안전장치)
_FAILED_CLI_MARKER = re.compile(r"Not logged in\s*·\s*Please run /login")


# ────────────────────────────────────────────────────────────────────
# 추출
# ────────────────────────────────────────────────────────────────────
def _is_user_speech(d: dict) -> bool:
    """사람이 친 진짜 user 메시지인지 (tool_result 자동 응답이 아닌지).

    주의: 하네스 주입 컨텐츠(<task-notification> 등)는 여기서 거르지 않는다.
    peek_session_meta 의 옛 격리 세션 판별이 "첫 user 발화" 의 원본 content
    (<command-name> 마커)를 봐야 하므로, 주입 필터는
    parse_session 의 turns 저장 단계(_clean_user_turn)에서만 적용한다.
    """
    if d.get("type") != "user":
        return False
    if d.get("isSidechain"):  # 서브에이전트 사이드채널
        return False
    content = (d.get("message") or {}).get("content")
    # 사람 입력은 항상 str. tool_result 등 자동 메시지는 list of blocks.
    return isinstance(content, str) and content.strip() != ""


# ── 시스템 주입 필터 (turns 저장 단계 전용) ──────────────────────────
# 하네스가 type=user & content=str 레코드로 주입하는 비발화 블록 태그들.
# 실측 (user 턴 2606개 스캔): task-notification 146,
# local-command-caveat 35, local-command-stdout 35, system-reminder 2.
_INJECTED_TAGS = (
    "task-notification",     # 백그라운드 작업 완료/실패 알림
    "system-reminder",       # 시스템 리마인더 (실발화 앞에 붙는 경우 있음)
    "local-command-caveat",  # 로컬 커맨드 주의문 (isMeta=True)
    "local-command-stdout",  # 로컬 커맨드 출력 (/model 등)
    "local-command-stderr",
)
# 턴 선두의 완결된 <tag>...</tag> 블록. 선두 앵커링이 핵심 — 본문 중간에
# 인용된 태그(백틱 등)까지 지우면 이 태그들을 *언급하는* 실발화가 훼손된다.
_LEADING_BLOCK_RE = re.compile(
    r"^<(%s)>.*?</\1>\s*" % "|".join(_INJECTED_TAGS), re.S
)
# 선두에 여는 태그만 남은 경우 (닫는 태그 없이 잘린 주입) — 턴 통째로 drop
_LEADING_OPEN_RE = re.compile(r"^<(?:%s)\b" % "|".join(_INJECTED_TAGS))

# 단독/선두 하네스 마커 — 제거 후 잔여 발화만 저장
_LEADING_MARKERS = (
    "[Request interrupted by user for tool use]",
    "[Request interrupted by user]",
)

# 이 프리픽스로 시작하는 턴은 전체가 기계 생성 → 통째로 drop
_DROP_PREFIXES = (
    # auto-compact 이어달리기: 이전 대화 요약 + "Resume directly..." 지시문
    "This session is being continued from a previous conversation",
)

# 슬래시 커맨드 주입: <command-name>/<command-message>/<command-args>
_CMD_TURN_RE = re.compile(r"^<command-(?:name|message)>")
_CMD_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_CMD_MSG_RE = re.compile(r"<command-message>\s*(.*?)\s*</command-message>", re.S)
_CMD_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)


def _clean_user_turn(content: str) -> tuple[str | None, bool]:
    """user 턴에서 하네스 주입을 걷어낸 실발화를 반환. (text, is_command)

    - text=None 이면 저장할 발화가 없는 턴 → 턴 자체를 emit 하지 않는다.
      (직전 페어에 assistant 텍스트가 계속 붙으므로, 주입 이후의 어시스턴트
      발화는 직전 실발화 턴에 자연스럽게 합쳐진다)
    - 주입 블록이 실발화 앞에 붙은 혼합 턴은 블록만 벗기고 발화를 살린다.
    - 슬래시 커맨드 턴은 사용자가 친 "/name args" 로 복원, is_command=True.
    """
    s = content.strip()

    # 선두의 완결 블록/마커를 반복 제거 (여러 개 연달아 붙을 수 있음)
    changed = True
    while changed and s:
        changed = False
        for marker in _LEADING_MARKERS:
            if s.startswith(marker):
                s = s[len(marker):].lstrip()
                changed = True
        m = _LEADING_BLOCK_RE.match(s)
        if m:
            s = s[m.end():]
            changed = True

    if not s:
        return None, False
    if _LEADING_OPEN_RE.match(s):  # 닫는 태그 없이 잘린 주입 블록
        return None, False
    for prefix in _DROP_PREFIXES:
        if s.startswith(prefix):
            return None, False

    if _CMD_TURN_RE.match(s):
        m_name = _CMD_NAME_RE.search(s)
        name = m_name.group(1).strip() if m_name else ""
        if not name:
            m_msg = _CMD_MSG_RE.search(s)
            msg = m_msg.group(1).strip() if m_msg else ""
            name = f"/{msg}" if msg else ""
        m_args = _CMD_ARGS_RE.search(s)
        args = m_args.group(1).strip() if m_args else ""
        text = f"{name} {args}".strip()
        return (text or None), True

    return s, False


def _assistant_text(d: dict, keep_tools: bool = False,
                    _dropped_out: list | None = None) -> str:
    """어시스턴트 메시지에서 사람이 읽을 텍스트만 모아서 반환."""
    if d.get("type") != "assistant" or d.get("isSidechain"):
        return ""
    msg = d.get("message") or {}
    blocks = msg.get("content")
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    dropped: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            txt = (b.get("text") or "").strip()
            if txt:
                parts.append(txt)
        elif t == "tool_use" and keep_tools:
            name = b.get("name", "?")
            inp = b.get("input") or {}
            # 핵심 인자 한 줄로 요약 (file_path, command, pattern 등)
            key_args = []
            for k in ("file_path", "path", "command", "pattern", "url", "query"):
                if k in inp and isinstance(inp[k], str):
                    val = inp[k]
                    if len(val) > 120:
                        val = val[:117] + "..."
                    key_args.append(f"{k}={val}")
                    break
            arg_str = " ".join(key_args)
            parts.append(f"_[Tool: {name}{(' ' + arg_str) if arg_str else ''}]_")
        elif t == "tool_use":
            dropped.append(b.get("name") or "?")
    if _dropped_out is not None:
        _dropped_out.extend(dropped)
    return "\n\n".join(parts).strip()


def tool_summary_line(dropped: list[str]) -> str:
    """**뺐다는 사실은 적는다.** 코드 세션은 도구를 담을 수가 없다 — 실측
    (큰 세션 12개, 2026-08-26): tool_use 가 본문의 771%, tool_result 는
    13,506% 다. 담으면 파생물이 정본을 통째로 덮는다.

    그렇다고 조용히 버리면 읽는 쪽이 "원래 없었던 것" 과 "여기서 뺀 것" 을
    구분할 수 없다. 그 구분이 없어서 "그런 근거 없다" 고 답한 적이 있다.
    본문 대신 **무엇을 몇 번 했는지**만 남긴다 — 원본은 archive 에 있다.

    **턴 단위로 한 줄이다.** 메시지마다 붙이면 도구 호출이 잦은 코드 세션에서
    한 턴에 수십 줄이 된다.
    """
    if not dropped:
        return ""
    c = Counter(dropped)
    top = " · ".join(f"{k}×{v}" if v > 1 else k for k, v in c.most_common(6))
    rest = len(c) - 6
    return (f"> ⤷ 도구 {len(dropped)}회 생략: {top}"
            + (f" 외 {rest}종" if rest > 0 else "")
            + " (원본: archive/code_sessions/)")


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [warn] {path.name} line {i} JSON parse fail: {e}", file=sys.stderr)


_PEEK_MAX_RECORDS = 50  # ai-title 이 없는 비표준 JSONL 도 EOF 까지 안 가게 하는 가드


def peek_session_meta(jsonl_path: Path) -> dict:
    """
    Output filename/라우팅에 필요한 메타만 빠르게 추출 (turns 는 파싱 안 함).

    재실행 시 이미 변환된 세션을 풀파싱 없이 skip-check 할 수 있게 해 줌.
    표준 JSONL 은 ai-title 이 1번째 줄, timestamp 가 2번째 줄에 있으므로
    대부분 첫 user 발화 직후 break. ai-title 이 없는 세션도 50 record 안에선 끊음.

    """
    ai_title = ""
    first_ts: str | None = None
    is_tooling = False
    saw_user = False
    for i, d in enumerate(_read_jsonl(jsonl_path), 1):
        if not ai_title and d.get("type") == "ai-title":
            ai_title = (d.get("aiTitle") or "").strip()
        if not first_ts:
            ts = d.get("timestamp")
            if ts:
                first_ts = ts
        if not saw_user and _is_user_speech(d):
            content = (d.get("message") or {}).get("content") or ""
            if _TOOLING_MARKER.search(content):
                is_tooling = True
            # 하네스 주입(<task-notification> 등)이 첫 user 레코드로 올 수 있다.
            # 여기서 잠그면 바로 뒤에 오는 실발화(슬래시 커맨드 주입 포함)를
            # 영영 안 보게 되므로, 주입이면 잠그지 않고 다음 user 를 계속 본다.
            if not content.lstrip().startswith(tuple(f"<{t}>" for t in _INJECTED_TAGS)):
                saw_user = True
        if not is_tooling and d.get("type") == "assistant":
            if _FAILED_CLI_MARKER.search(_assistant_text(d)):
                is_tooling = True
        if (ai_title and first_ts and saw_user) or i >= _PEEK_MAX_RECORDS:
            break
    return {
        "session_id":     jsonl_path.stem,
        "project":        jsonl_path.parent.name,
        "ai_title":       ai_title,
        "first_date":     _local_date(first_ts),
        "is_tooling":     is_tooling,
    }


def _local_time(ts: str | None) -> str:
    """발화 시각을 로컬 `MM-DD HH:MM` 으로. 날짜를 함께 두는 이유가 있다 —
    코드 세션은 자정을 넘겨 이어지는 일이 잦아서(파일 날짜는 시작일 하나뿐),
    시각만 적으면 25시가 다음 날인지 그날인지 구분이 안 된다."""
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return ""


def _local_date(ts: str | None) -> str:
    """JSONL 타임스탬프(UTC, `...Z`)를 이 기기의 로컬 날짜로.

    그대로 [:10] 을 자르면 KST 00~09시에 시작한 세션이 전부 전날 날짜가 되어
    파일명·생성일 헤더·카드·날짜 검색까지 하루씩 밀린다.
    """
    if not ts:
        return "unknown"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except ValueError:
        return ts[:10] or "unknown"


def parse_session(
    jsonl_path: Path,
    keep_tools: bool = False,
    meta: dict | None = None,
) -> dict | None:
    """
    하나의 세션 JSONL 을 읽어 메타+turns 딕셔너리로 변환.

    `meta` 가 주어지면 (main 루프에서 peek 한 결과 재활용용) ai-title/first_ts 를
    중복 추출하지 않고 turns 만 채워서 반환. 비어 있으면 peek_session_meta 호출.

    Returns dict 또는 None (대화가 비어 있으면 None)
        {
          "session_id": "...",
          "ai_title":   "...",       # 없으면 ""
          "first_date": "YYYY-MM-DD",
          "project":    "<폴더명>",
          "turns":      [(user_str, assistant_str), ...],
        }
    """
    if meta is None:
        meta = peek_session_meta(jsonl_path)

    # 진행 상태: 직전 user 발화를 잡아두고, 다음 user 발화 (또는 EOF) 직전까지의
    # 모든 assistant text 를 모아 한 페어로 emit.
    # 턴마다 **발화 시각**을 함께 실어 나른다. JSONL 레코드엔 timestamp 가 붙어
    # 있는데 예전엔 첫 줄 것만 써서 파일 날짜를 정하고 나머지를 버렸다. 그래서
    # 코드 세션은 "며칠" 만 알고 "몇 시" 를 몰랐다 — 2026-08 기준 발화의 80%가
    # 코드 세션이라, 시간대를 볼 수 있는 자료가 20%(웹 대화)뿐이었다.
    turns: list[tuple[str, str, str]] = []
    pending_user: str | None = None
    pending_ts: str = ""
    pending_asst: list[str] = []
    pending_is_cmd = False

    pending_dropped: list[str] = []

    def flush():
        nonlocal pending_user, pending_asst, pending_is_cmd, pending_ts
        if pending_user is not None:
            # 이 턴에서 버린 도구를 한 줄로 덧붙인다 (tool_summary_line 참조).
            tools = tool_summary_line(pending_dropped)
            asst = "\n\n".join(pending_asst + ([tools] if tools else [])).strip()
            # 커맨드 복원 턴(/model 등)은 응답 텍스트가 없으면 페어 자체가
            # 무의미 → drop. 실발화 턴은 응답이 없어도 보존.
            if asst or not pending_is_cmd:
                turns.append((pending_user, asst, pending_ts))
        pending_user = None
        pending_asst = []
        pending_dropped.clear()
        pending_is_cmd = False
        pending_ts = ""

    for d in _read_jsonl(jsonl_path):
        if _is_user_speech(d):
            # 하네스 메타 레코드(local-command-caveat 등)와 주입 전용 턴은
            # 발화로 치지 않음 — 직전 페어에 assistant 텍스트가 계속 붙는다.
            if d.get("isMeta"):
                continue
            text, is_cmd = _clean_user_turn(
                (d.get("message") or {}).get("content", "")
            )
            if text is None:
                continue
            flush()
            pending_user = text
            pending_is_cmd = is_cmd
            pending_ts = _local_time(d.get("timestamp"))
            continue

        if d.get("type") == "assistant":
            txt = _assistant_text(d, keep_tools=keep_tools,
                                  _dropped_out=pending_dropped)
            if txt and pending_user is not None:
                pending_asst.append(txt)

    flush()

    if not turns:
        return None

    return {**meta, "turns": turns}


# ────────────────────────────────────────────────────────────────────
# 직렬화
# ────────────────────────────────────────────────────────────────────
_FILENAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _slug(s: str, maxlen: int = 40) -> str:
    s = _FILENAME_BAD.sub("", s).strip()
    s = re.sub(r"\s+", "_", s)
    return s[:maxlen].rstrip("_") if s else ""


def render_markdown(parsed: dict) -> str:
    title_bits = [parsed["project"]]
    if parsed["ai_title"]:
        title_bits.append(parsed["ai_title"])
    header_title = " — ".join(title_bits)

    lines = [
        f"# {header_title}",
        "",
        f"- 생성일: {parsed['first_date']}",
        f"- ID: {parsed['session_id']}",
        f"- source: {SOURCE_NAME}",
        f"- project: {parsed['project']}",
    ]
    if parsed["ai_title"]:
        lines.append(f"- title: {parsed['ai_title']}")
    lines.append("")

    for turn in parsed["turns"]:
        user_text, asst_text = turn[0], turn[1]
        ts = turn[2] if len(turn) > 2 else ""
        # 시각은 **헤더 줄에** 붙인다. 본문에 넣으면 카드 발췌와 청크 본문에
        # 섞여 검색 잡음이 되고, 헤더면 파서가 이미 통째로 건너뛴다.
        lines.append(f"## 👤 User · {ts}" if ts else "## 👤 User")
        lines.append("")
        lines.append(user_text)
        lines.append("")
        lines.append("## 🤖 Assistant")
        lines.append("")
        lines.append(asst_text or "_(no text reply)_")
        lines.append("")

    return "\n".join(lines)


def output_filename(parsed: dict) -> str:
    sid8 = parsed["session_id"][:8]
    proj = _slug(parsed["project"], 30) or "unknown"
    date = parsed["first_date"]
    tag = FILENAME_TAG
    title_slug = _slug(parsed["ai_title"], 30) if parsed["ai_title"] else ""
    if title_slug:
        return f"{date}_{proj}_{title_slug}__{tag}_{sid8}.md"
    return f"{date}_{proj}__{tag}_{sid8}.md"


# ────────────────────────────────────────────────────────────────────
# 폴더 일괄 처리
# ────────────────────────────────────────────────────────────────────
def iter_session_jsonls(projects_root: Path):
    """`~/.claude/projects/<slug>/<sessionId>.jsonl` 만. subagents/ 는 제외."""
    for p in projects_root.glob("*/*.jsonl"):
        if "subagents" in p.parts:
            continue
        yield p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Claude Code 세션 JSONL → conversations/*.md")
    ap.add_argument(
        "--projects",
        default="~/.claude/projects",
        help="Claude Code projects 루트 (기본: ~/.claude/projects)",
    )
    ap.add_argument(
        "--out",
        default="./conversations",
        help="출력 폴더 (기본: ./conversations)",
    )
    # 2026-08-15 격리 폐지로 무의미해졌지만 인자는 **받고 무시**한다. 이 스크립트는
    # 매일 도는 수집 잡의 첫 단계라, 옛 셸 스크립트가 남아 있는 기기에서 argparse 가
    # "unrecognized arguments" 로 죽으면 그날 수집이 통째로 날아간다.
    ap.add_argument("--mental-out", default=None, help=argparse.SUPPRESS)
    ap.add_argument(
        "--keep-tools",
        action="store_true",
        help="assistant 의 tool_use 를 한 줄 요약으로 보존 (기본: drop)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리할 세션 개수 제한 (0=전부, 디버깅용)",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 존재하는 .md 도 덮어쓰기 (기본: 스킵)",
    )
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="jsonl 이 .md 보다 최신인 세션만 재변환 "
             "(진행 중에 잡힌 스냅샷을 이후 실행에서 따라잡기용)",
    )
    ap.add_argument(
        "--rerender",
        action="store_true",
        help="원본이 그대로여도 전부 다시 렌더해 **내용이 달라진 것만** 저장 "
             "(변환기를 고친 뒤 소급 적용용 — 같으면 mtime 도 안 건드린다)",
    )
    ap.add_argument(
        "--changed-list",
        default="",
        help="내용이 실제로 바뀐 .md 경로를 한 줄에 하나씩 적을 파일 "
             "(재인덱싱 대상 목록으로 사용)",
    )
    ap.add_argument(
        "--skip-today",
        action="store_true",
        help="오늘(로컬 자정 이후) 쓰인 jsonl 은 건너뜀 — 아직 진행 중일 수 있어 "
             "중간 스냅샷으로 굳는 것을 막는다 (매일 자동 실행용)",
    )
    args = ap.parse_args(argv)

    projects_root = Path(args.projects).expanduser()
    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not projects_root.is_dir():
        print(f"[error] projects 루트 없음: {projects_root}", file=sys.stderr)
        return 2

    jsonls = list(iter_session_jsonls(projects_root))
    if args.limit:
        jsonls = jsonls[: args.limit]
    print(f"[info] 세션 JSONL {len(jsonls)}개 발견 (projects={projects_root})")
    print(f"[info] 출력 폴더: {out_dir.resolve()}")

    saved = skipped = empty = tooling = 0
    refreshed = unchanged = active = 0
    changed_paths: list[Path] = []
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    for i, jp in enumerate(jsonls, 1):
        if args.skip_today and jp.stat().st_mtime >= midnight:
            active += 1
            continue
        # 1) peek 으로 출력 파일명/라우팅만 먼저 계산 → 이미 있으면 풀파싱 회피
        try:
            meta = peek_session_meta(jp)
        except Exception as e:
            print(f"  [error] {jp.name} peek 실패: {e}", file=sys.stderr)
            continue

        # memex 툴링(`claude -p` 카드/롤업 생성)이 남긴 세션은 대화가 아니므로 버림
        if meta.get("is_tooling"):
            tooling += 1
            continue

        fpath = out_dir / output_filename(meta)
        # 같은 세션은 파일 하나로 — 이름 재료(프로젝트 슬러그·제목·날짜)가 나중에
        # 달라져도 sid 로 기존 산출물을 찾아 그 경로를 계속 쓴다. 안 그러면 요소가
        # 바뀔 때마다 같은 세션의 md 가 한 벌 더 생겨 인덱스에 이중 노출된다.
        # 옛 격리 태그(__mr_)도 함께 본다 — 격리 폐지로 넘어온 파일이 새 태그로
        # 한 벌 더 생기는 것을 막는다.
        sid8 = meta["session_id"][:8]
        prior = sorted(p for t in (FILENAME_TAG, *LEGACY_TAGS)
                       for p in out_dir.glob(f"*__{t}_{sid8}.md"))
        if prior and prior[0] != fpath:
            fpath = prior[0]
        existed = fpath.exists()
        # --refresh 는 **원본이 자란 경우**만 잡는다 (mtime 비교). 렌더러가 바뀐
        # 경우는 원본 mtime 이 그대로라 영영 안 잡히는데, 그건 실제로 일어난다 —
        # 2026-08-26 에 발화 시각과 도구 생략 표시를 넣고서야 알았다.
        # --rerender 는 내용을 비교해 **달라졌을 때만** 다시 쓴다 (같으면 mtime 도
        # 안 건드려서, 카드가 공연히 재생성되지 않는다).
        stale = existed and args.refresh and jp.stat().st_mtime > fpath.stat().st_mtime
        if existed and args.rerender and not stale:
            stale = "rerender"          # 아래에서 내용 비교 후 결정
        if existed and not (args.overwrite or stale):
            skipped += 1
        else:
            # 2) 신규/덮어쓰기일 때만 풀파싱 + 렌더 (meta 는 peek 결과 재활용)
            try:
                parsed = parse_session(jp, keep_tools=args.keep_tools, meta=meta)
            except Exception as e:
                print(f"  [error] {jp.name} 파싱 실패: {e}", file=sys.stderr)
                continue
            if parsed is None:
                empty += 1
                continue
            text = render_markdown(parsed)
            # 내용이 같으면 쓰지 않는다 — mtime 이 갱신되면 카드가 통째로 재생성된다
            # (읽기/쓰기 실패는 그 파일만 건너뛴다 — 잠긴 파일 하나가 daily 잡의
            #  set -e 아래서 그날 수집 전체를 죽이지 않게)
            if existed:
                try:
                    old = fpath.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    old = None  # 못 읽는 기존 파일이면 아래에서 새 내용으로 덮는다
                if old is not None:
                    # cc↔cw 접기 주석(surfaces/cowork_id)은 렌더 재료(JSONL)에 없다 —
                    # 이어 옮기지 않으면 --refresh 마다 지워지고, 다음 클라우드 반출이
                    # 같은 세션을 cw md 로 한 벌 더 만든다. 비교도 옮긴 뒤에 해야
                    # 주석 두 줄 차이로 매일 "갱신됨" 이 되지 않는다.
                    text = carry_annotations(old, text)
                    if old == text:
                        unchanged += 1
                        continue
            try:
                fpath.write_text(text, encoding="utf-8")
            except OSError as e:
                print(f"  [error] {jp.name} 저장 실패: {e}", file=sys.stderr)
                continue
            if existed:
                refreshed += 1
                changed_paths.append(fpath)
            else:
                saved += 1

        if i % 25 == 0 or i == len(jsonls):
            print(f"  진행 {i}/{len(jsonls)} (saved={saved} skipped={skipped} empty={empty})")

    if args.changed_list:
        Path(args.changed_list).expanduser().write_text(
            "".join(f"{p}\n" for p in changed_paths), encoding="utf-8")

    print(f"\n[done] saved={saved} refreshed={refreshed} unchanged={unchanged} "
          f"skipped={skipped} empty(no_turns)={empty} "
          f"tooling(제외)={tooling} active(오늘분 보류)={active}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
