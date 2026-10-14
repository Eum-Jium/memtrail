"""
claude.ai API 상세 응답 JSON → conversations/*.md 변환기.

Claude in Chrome 브라우저 파이프 (same-origin fetch → Blob 다운로드) 로 반출한
대화 상세 응답을 md 로 변환한다. 주간 sync 루틴에서 사용.

**상세 조회 시 렌더 옵션을 반드시 붙일 것** (2026-07-31 확인):
    /api/organizations/<org>/chat_conversations/<uuid>?tree=True&rendering_mode=messages&render_all_tools=true
옵션 없이 부르면 일부 블록이 "This block is not supported on your current device
yet." 플레이스홀더로 치환돼 내용이 유실된다. 옵션을 붙이면 해당 블록이 정상
복원되고 메시지 수도 더 많이 잡힌다.

입력 포맷 (둘 다 허용):
  - 상세 응답 객체 배열: [{uuid, name, created_at, chat_messages: [...]}, ...]
  - 단일 상세 응답 객체

chat_messages[].text 직접 필드 (2026-07 API) 우선, 구 content 배열 폴백.

사용법:
  python scripts/convert_claude_api_json.py <input.json> [--out $MEMEX_DATA_DIR/conversations] [--dry-run]

멱등: 기존 파일의 uuid (md 안 `- ID:` 줄) 또는 정규화 키 (YYYYMMDD|제목40자,
옛/새 파일명 스킴 수렴) 가 겹치면 스킵. 한 배치 안에서 날짜+제목이 겹치는 별개
대화는 제목에 uuid8 접미사를 붙여 분리 저장한다. 저장/스킵/truncated 요약 출력.
"""
import argparse
import os
import json
import re
import sys
from pathlib import Path

# Windows 콘솔 cp949 에서 이모지/특수문자 print 크래시 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FORBIDDEN = '<>:"/\\|?*'
CC_TAG = re.compile(r"__(?:(?:cc|mr)_[0-9a-f]{8}|cw_[A-Za-z0-9]{8})\.md$")
NEW_SCHEME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(.*)\.md$")
OLD_SCHEME = re.compile(r"^(\d{8})_(.*)\.md$")
UUID_LINE = re.compile(r"^- ID: ([0-9a-f-]{36})\s*$", re.M)


def norm_title(title: str) -> str:
    """dedup 키용 제목 정규화: 공백/_ 제거, 금지문자 제거, 40자."""
    t = re.sub(r"[\s_]+", "", title)
    t = "".join(c for c in t if c not in FORBIDDEN)
    return t.lower()[:40]


def dedup_key(date8: str, title: str) -> str:
    return f"{date8}|{norm_title(title)}"


def existing_keys(out_dir: Path):
    """기존 conversations/*.md → (date|title 키 set, title-only set, uuid→경로).

    uuid 는 md 머리말의 `- ID:` 줄에서 읽는다 (옛 변환기 산출물엔 없어서 키 폴백 필요).
    경로를 함께 들고 있어야 --refresh 가 같은 파일에 덮어쓸 수 있다.
    cc/mr 세션 파일은 제외.
    """
    keys, titles, uuids = set(), set(), {}
    for p in out_dir.glob("*.md"):
        name = p.name
        if CC_TAG.search(name):
            continue
        m = NEW_SCHEME.match(name)
        if m:
            date8, title = m.group(1) + m.group(2) + m.group(3), m.group(4)
        else:
            m = OLD_SCHEME.match(name)
            if not m:
                continue
            date8, title = m.group(1), m.group(2)
        keys.add(dedup_key(date8, title))
        titles.add(norm_title(title))
        head = p.read_text(encoding="utf-8", errors="replace")[:400]
        m = UUID_LINE.search(head)
        if m:
            uuids[m.group(1)] = p
    return keys, titles, uuids


# 도구 호출을 어디까지 담을지 — 2026-08-26 실측(대화 40건)이 정한 선이다.
#
#   text        449,648자
#   tool_use    238,000자   (text 의 53%)  → **요지만** 담는다
#   tool_result 1,978,000자 (text 의 4.4배) → 담지 않는다
#   thinking     34,298자   (대부분 빈 문자열) → 담지 않는다
#
# tool_result 를 그대로 실으면 파생물이 정본을 덮는다 (Drive 다운로드 하나가 평균
# 18KB, 유튜브 자막 하나가 113KB). 「정본은 원문 대화뿐」이 깨지는 지점이다.
#
# 그런데 **버려서는 안 되는 게 하나 있다** — "무엇을 근거로 삼았는가". 사용자가
# 준 문서를 읽고 판단만 대화에 남기면, 나중에 그 판단의 근거가 통째로 사라진다
# (실제 사고: 첨부된 근거 문서를 읽고 결론을 정했는데 코퍼스엔 결론만
# 남았고, 파일 이름조차 없어서 "그런 근거 없다" 고 답했다).
# 그건 tool_result 가 아니라 **tool_use 의 input** 에 있다. 한 줄이면 된다.
CREATE_TOOLS = ("create_file", "artifacts", "canvas")   # 산출물 — 본문까지 담는다
CREATE_MAX = 20_000                                     # 그래도 상한은 둔다
PATH_KEYS = ("path", "file_path", "filename", "name", "query", "url", "id")


def _tool_line(block: dict) -> str:
    """도구 호출 한 줄. **무엇을 했는지**만 남기고 결과는 남기지 않는다."""
    name = (block.get("name") or "?").strip()
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    desc = (block.get("message") or inp.get("description") or "").strip()
    where = next((str(inp[k]) for k in PATH_KEYS
                  if isinstance(inp.get(k), str) and inp[k].strip()), "")
    bits = [f"🔧 {name}"]
    if desc:
        bits.append(desc[:120])
    if where:
        bits.append(f"`{where[:160]}`")
    return "> " + " — ".join(bits)


def _created_body(block: dict) -> str:
    """create_file 류의 산출물 본문. 이건 **내가 만든 것**이라 원문 급이다."""
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    for k in ("content", "file_text", "text", "command_input"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            body = v.strip()
            if len(body) > CREATE_MAX:
                body = body[:CREATE_MAX] + f"\n… (이하 {len(v) - CREATE_MAX:,}자 생략)"
            return body
    return ""


def _files_lines(msg: dict) -> list[str]:
    """업로드된 첨부의 이름·크기. 실물은 안 오지만 **있었다는 사실**이 남는다."""
    out = []
    seen = set()
    for f in (msg.get("files") or []) + (msg.get("attachments") or []):
        if not isinstance(f, dict):
            continue
        name = (f.get("file_name") or f.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        size = f.get("size_bytes") or f.get("file_size")
        out.append(f"> 📎 첨부: {name}" + (f" ({size:,}B)" if isinstance(size, int) else ""))
    return out


def _result_size(block: dict) -> int:
    """tool_result 의 본문 크기. 담지는 않아도 **얼마나 뺐는지는 적는다.**"""
    c = block.get("content")
    if isinstance(c, list):
        return sum(len(x.get("text") or "") for x in c if isinstance(x, dict))
    if isinstance(c, str):
        return len(c)
    return 0


def msg_text(msg: dict) -> str:
    parts: list[str] = _files_lines(msg)
    blocks = msg.get("content")
    if isinstance(blocks, list) and blocks:
        # content 배열을 **순서대로** 훑는다 — 말과 도구 호출이 오간 차례가
        # 곧 그 턴의 진행이다. 평평한 .text 필드만 보면 그 차례가 사라진다.
        last_tool = -1                          # 직전 tool_use 줄의 자리
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                continue
            if btype == "tool_result":
                # 결과 본문은 안 담지만 **뺐다는 사실은 적는다.** 안 적으면
                # 읽는 쪽이 "원래 없었던 것" 과 "여기서 뺀 것" 을 구분할 수
                # 없다 — 그 구분이 없어서 "그런 근거 없다" 고 답한 적이 있다.
                # 조용한 누락은 수집기에서 고친 것과 같은 병이다.
                n = _result_size(block)
                if n >= 200 and last_tool >= 0:
                    err = " · 실패" if block.get("is_error") else ""
                    parts[last_tool] += f"\n> ⤷ 결과 {n:,}자 생략{err} (원본: archive/sources/)"
                continue
            if btype == "tool_use":
                parts.append(_tool_line(block))
                last_tool = len(parts) - 1
                if (block.get("name") or "") in CREATE_TOOLS:
                    body = _created_body(block)
                    if body:
                        parts.append(body)
                continue
            if isinstance(block.get("text"), str) and block["text"].strip():
                parts.append(block["text"].strip())
                last_tool = -1                  # 말이 오갔으면 짝짓기를 끊는다
        if any(p for p in parts):
            return "\n\n".join(p for p in parts if p.strip())
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        parts.append(text.strip())
    return "\n\n".join(p.strip() for p in parts if p.strip())


def to_md(conv: dict) -> str:
    title = (conv.get("name") or "").strip() or "Untitled"
    date = (conv.get("created_at") or "")[:10]
    lines = [f"# {title}", "", f"- ID: {conv.get('uuid', '')}", f"- 생성일: {date}", ""]
    for msg in conv.get("chat_messages") or []:
        text = msg_text(msg)
        if not text:
            continue
        who = "👤 User" if msg.get("sender") == "human" else "🤖 Assistant"
        lines += ["---", "", f"## {who}", "", text, ""]
    return "\n".join(lines)


def safe_filename(conv: dict) -> str:
    title = (conv.get("name") or "").strip() or "Untitled"
    title = "".join(c if c not in FORBIDDEN else " " for c in title)
    title = re.sub(r"\s+", " ", title).strip()[:80] or "Untitled"
    date = (conv.get("created_at") or "0000-00-00")[:10]
    return f"{date}_{title}.md"


def convert(input_path: str, out_dir: str, dry_run: bool = False,
            refresh: bool = False, changed_list: str = "",
            archive_dir: str = "") -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("conversations", [data])

    keys, titles, uuids = existing_keys(out)
    # key → 그 key 를 가진 코퍼스 파일들. 옛 스킴 파일(= `- ID:` 없음)을 uuid 가
    # 아니라 날짜+제목으로 다시 만나기 위한 역인덱스다. **여럿이면 손대지 않는다.**
    key_paths: dict[str, list[Path]] = {}
    for p in out.glob("*.md"):
        m = re.match(r"(\d{4})-?(\d{2})-?(\d{2})[_ ]", p.name)
        if not m:
            continue
        k = dedup_key("".join(m.groups()), re.sub(r"\.md$", "", p.name)[len(m.group(0)):])
        key_paths.setdefault(k, []).append(p)
    # export 쪽에서도 같은 key 가 여럿이면 짝을 못 짓는다 — 미리 세어둔다.
    key_counts: dict[str, int] = {}
    for c in data:
        if not isinstance(c, dict):
            continue
        if (c.get("uuid") or "").strip() in uuids:
            continue
        k = dedup_key((c.get("created_at") or "")[:10].replace("-", ""),
                      (c.get("name") or "").strip() or "Untitled")
        key_counts[k] = key_counts.get(k, 0) + 1
    batch_keys = set()
    saved, skipped, suffixed, warns = 0, 0, 0, []
    empty = 0
    refreshed, unchanged = 0, 0
    changed_paths = []

    for conv in data:
        if not isinstance(conv, dict) or "chat_messages" not in conv:
            warns.append(f"상세 응답 아님, 건너뜀: {str(conv)[:60]}")
            continue
        # **발화가 하나도 없는 대화는 만들지 않는다.** 공식 export 에는 열어만 보고
        # 지운 빈 대화가 섞여 있는데(2026-08-26 실측 101건), 그대로 쓰면 머리말
        # 네 줄짜리 73바이트 「Untitled」 파일이 코퍼스에 쌓인다. 청크가 0이라
        # 검색엔 안 걸리지만, 인덱서가 매번 "신규 60개 / 추가 0 청크" 를 찍어
        # **뭔가 고장난 것처럼 보이게** 만든다 — 실제로 그 줄을 보고 조사했다.
        if not any(msg_text(m) for m in (conv.get("chat_messages") or [])):
            empty += 1
            continue
        uuid = (conv.get("uuid") or "").strip()
        if uuid and uuid in uuids:
            # 이미 받아둔 대화. 기본은 스킵이지만, --refresh 면 그 사이 길어졌는지 본다
            # (웹 대화는 며칠에 걸쳐 이어지므로 한 번 받은 뒤 굳으면 뒷부분이 통째로 샌다).
            if not refresh:
                skipped += 1
                continue
            fpath = uuids[uuid]
            text = to_md(conv)
            old_text = fpath.read_text(encoding="utf-8", errors="replace")
            if old_text == text:
                unchanged += 1
                continue
            if not dry_run:
                # **덮기 전에 옛 판을 얼린다.** 재생성 산출물의 지난 판은 지우지도
                # 덮지도 않는다 (사용자 지시). 공식 export 로 갱신하면 대개 길어지지만
                # (실측 2026-08-26: 한 대화가 228,736 → 476,682자), 줄어드는 경우가
                # 있어도 되돌릴 수 있어야 한다 — ChatGPT 에서 옛 수집분에만 캔버스가
                # 있던 사례를 겪었다.
                if archive_dir:
                    ad = Path(archive_dir)
                    ad.mkdir(parents=True, exist_ok=True)
                    (ad / fpath.name).write_text(old_text, encoding="utf-8", newline="\n")
                fpath.write_text(text, encoding="utf-8", newline="\n")
            refreshed += 1
            changed_paths.append(fpath)
            print(f"  ~ {fpath.name}")
            continue
        title = (conv.get("name") or "").strip() or "Untitled"
        date8 = (conv.get("created_at") or "")[:10].replace("-", "")
        key = dedup_key(date8, title)
        if key in batch_keys and uuid:
            # 이번 배치 안의 별개 대화와 날짜+제목이 겹침 → uuid8 접미사로 분리.
            title = f"{title} ({uuid[:8]})"
            key = dedup_key(date8, title)
            conv = {**conv, "name": title}
            suffixed += 1
        if key in keys:
            # uuid 는 처음 보는데 날짜+제목이 겹친다 = 대개 **옛 스킴으로 받아둔
            # 같은 대화**다 (옛 파일엔 `- ID:` 줄이 없어 uuid 대조가 안 된다).
            # 그 파일들은 공식 export 판이 훨씬 크다 — 실측 2026-08-26: 403건이
            # 5,278,523 → 8,239,835자(1.56배), 가장 크게 는 대화 하나가 44,883 → 266,720자.
            #
            # 다만 **모호하면 손대지 않는다.** 같은 key 에 export 대화가 둘 이상이면
            # 어느 것이 그 파일인지 알 수 없고, 잘못 덮으면 다른 대화를 잃는다
            # (실측: 389개 key 중 8개가 그런 경우 — 「Untitled」 류).
            tgt = key_paths.get(key) if refresh else None
            if tgt is not None and key_counts.get(key, 0) == 1 and len(tgt) == 1:
                fpath = tgt[0]
                text = to_md(conv)
                old_text = fpath.read_text(encoding="utf-8", errors="replace")
                if old_text == text:
                    unchanged += 1
                    continue
                if not dry_run:
                    if archive_dir:
                        ad = Path(archive_dir)
                        ad.mkdir(parents=True, exist_ok=True)
                        (ad / fpath.name).write_text(old_text, encoding="utf-8",
                                                     newline="\n")
                    fpath.write_text(text, encoding="utf-8", newline="\n")
                refreshed += 1
                changed_paths.append(fpath)
                print(f"  ~ {fpath.name}")
                continue
            skipped += 1
            continue
        if norm_title(title) in titles:
            warns.append(f"제목 같고 날짜 다름 (dup 의심, 저장은 함): {title}")
        if conv.get("truncated") or any(m.get("truncated") for m in conv.get("chat_messages") or []):
            warns.append(f"truncated 플래그: {title} — 원본 재확인 필요")
        fpath = out / safe_filename(conv)
        if not dry_run:
            fpath.write_text(to_md(conv), encoding="utf-8", newline="\n")
        keys.add(key)
        batch_keys.add(key)
        titles.add(norm_title(title))
        if uuid:
            uuids[uuid] = fpath
        saved += 1
        print(f"  + {fpath.name}")

    if changed_list and not dry_run:
        Path(changed_list).expanduser().write_text(
            "".join(f"{p}\n" for p in changed_paths), encoding="utf-8")

    label = "저장 예정" if dry_run else "저장"
    tail = f", 키충돌 접미사 {suffixed}" if suffixed else ""
    if refresh:
        tail += f", 갱신 {refreshed}, 그대로 {unchanged}"
    print(f"완료: {label} {saved}, 스킵(기존) {skipped}{tail}")
    for w in warns:
        print(f"  [warn] {w}")
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/conversations")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="이미 받아둔 uuid 도 내용이 길어졌으면 같은 파일에 덮어쓴다 "
                         "(며칠에 걸쳐 이어지는 웹 대화 따라잡기용)")
    ap.add_argument("--changed-list", default="",
                    help="--refresh 로 실제 갱신된 md 경로를 적을 파일 (재인덱싱 대상)")
    ap.add_argument("--archive-dir", default="",
                    help="--refresh 로 덮기 전에 옛 판을 얼려둘 폴더 (지난 판 보존)")
    args = ap.parse_args()
    sys.exit(0 if convert(args.input, args.out, args.dry_run,
                          args.refresh, args.changed_list,
                          args.archive_dir) >= 0 else 1)
