"""
convert_chatgpt_json.py — ChatGPT 대화 JSON → 대화 md.

입력은 두 가지를 다 받는다 (구조가 같다 — mapping 노드 트리):
  - 공식 데이터 내보내기의 conversations.json
  - 콘솔 스크립트(/backend-api/conversation 수집)가 만든 배열

ChatGPT 대화는 선형이 아니라 **트리**다 — 메시지를 수정하면 가지가 갈라진다.
current_node 에서 parent 를 따라 뿌리까지 올라간 경로가 "화면에 보이던 대화"이고,
그것만 저장한다 (버려진 가지는 사용자도 못 보던 것).

이미 코퍼스에 있는 대화는 건너뛴다 — 기존 파일 머리의 `- ID:` 와 export 의
conversation_id 를 대조한다 (2026-08-25 실측: 다른 export 116건 중 92건이 이미
이관돼 있었다. 파일명이 아니라 ID 로 대조해야 하는 이유다).

출력 파일명 `YYYY-MM-DD_<제목>__gp_<id8>.md` — `__gp_` 가 출처 태그(chatgpt).
기존 424건은 소급 판정이라 목록(provenance)을 쓰지만, 새로 만드는 파일은
Gemini(`__gm_`)와 같은 파일명 규칙이 더 단순하다.

사용:
    python scripts/convert_chatgpt_json.py <export.json> [--out conversations/] [--dry-run]
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
IDRE = re.compile(r"^- (?:\*\*)?ID(?:\*\*)?: ?([0-9a-fA-F-]{30,})", re.M)
KST = timezone(timedelta(hours=9))


def kst_date(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "unknown"
    return datetime.fromtimestamp(ts, tz=KST).date().isoformat()


def clean_title(t: str) -> str:
    t = FORBIDDEN.sub(" ", (t or "").strip())
    t = re.sub(r"\s+", " ", t).strip()[:40].strip()
    return t or "무제"


def msg_text(msg: dict) -> str:
    """content 타입별 텍스트 추출. 이미지 등 비텍스트는 표식만 남긴다."""
    c = msg.get("content") or {}
    ct = c.get("content_type")
    if ct == "text":
        return "\n".join(p for p in (c.get("parts") or []) if isinstance(p, str)).strip()
    if ct == "multimodal_text":
        out = []
        for p in c.get("parts") or []:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                out.append("[이미지]")
        return "\n".join(out).strip()
    if ct == "code":
        t = (c.get("text") or "").strip()
        return f"```\n{t}\n```" if t else ""
    return ""


def attachments_of(msg: dict) -> list[dict]:
    """메시지에 붙은 첨부의 **메타데이터**. 실물은 export 에 다 오지 않는다.

    2026-08-26 실측: 공식 export 의 첨부 참조 492건 중 zip 에 실물이 든 건 301건
    (61%) 이고, 빠진 191건은 대부분 문서다 (.txt 96 · application 86). 그런데
    **이름·크기·형식은 메타에 남아 있다.** 그것만이라도 원문에 적어두면
    "그때 이 문서를 근거로 삼았다" 는 사실이 회상에 걸린다 — 지금은 그조차
    없어서, 문서를 읽고 내린 결론만 남고 근거는 통째로 증발했다
    (어느 대화에 붙어 있던 근거 문서(표)가 그 사례).
    """
    return [a for a in ((msg.get("metadata") or {}).get("attachments") or [])
            if isinstance(a, dict)]


def render_attachments(atts: list[dict]) -> str:
    out = []
    for a in atts:
        name = (a.get("name") or "이름없음").strip()
        size = a.get("size")
        mime = a.get("mime_type") or ""
        bits = [x for x in (f"{size:,}B" if isinstance(size, int) else "", mime) if x]
        out.append(f"> 📎 첨부: {name}" + (f" ({' · '.join(bits)})" if bits else ""))
    return "\n".join(out)


def main_path(conv: dict) -> list[dict]:
    """current_node → 뿌리 경로의 메시지들 (시간순)."""
    mp = conv.get("mapping") or {}
    node_id = conv.get("current_node")
    chain = []
    seen = set()
    while node_id and node_id in mp and node_id not in seen:
        seen.add(node_id)
        node = mp[node_id]
        if node.get("message"):
            chain.append(node["message"])
        node_id = node.get("parent")
    chain.reverse()
    out = []
    for m in chain:
        role = (m.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue                       # system/tool 은 화면에 없던 배관
        if (m.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        t = msg_text(m)
        atts = attachments_of(m)
        if t or atts:
            out.append({"role": role, "text": t, "atts": atts})
    return out


def existing_ids(conv_dir: Path) -> set[str]:
    ids = set()
    for p in conv_dir.glob("*.md"):
        m = IDRE.search(p.read_text(encoding="utf-8", errors="replace")[:600])
        if m:
            ids.add(m.group(1))
    return ids


def render(conv: dict) -> tuple[str, str] | None:
    cid = conv.get("conversation_id") or conv.get("id") or ""
    msgs = main_path(conv)
    if not cid or not msgs:
        return None
    date = kst_date(conv.get("create_time"))
    title = clean_title(conv.get("title") or msgs[0]["text"][:40])
    parts = [f"# {title}", "", f"- ID: {cid}", f"- 생성일: {date}", "", "---", ""]
    for m in msgs:
        hdr = "## 👤 User" if m["role"] == "user" else "## 🤖 Assistant"
        body = m["text"]
        if m.get("atts"):
            att = render_attachments(m["atts"])
            body = f"{att}\n\n{body}" if body else att
        parts += [hdr, "", body, "", "---", ""]
    sid = cid.replace("-", "")[:8]
    fname = f"{date}_{title}__gp_{sid}.md"
    return unicodedata.normalize("NFC", fname), "\n".join(parts).rstrip() + "\n"


def load_conversations(src: Path) -> list[dict]:
    """대화 배열을 읽는다. 세 가지 입력을 다 받는다:

      - 공식 데이터 내보내기 **zip** (안에 conversations-000.json … 이 여러 개)
      - 단일 conversations.json
      - 콘솔 스크립트가 만든 배열

    zip 을 직접 받는 이유는 공식 export 가 대화를 **여러 파일로 쪼개 넣기** 때문이다
    (2026-08-26 export: 8개 파일 769건). 손으로 풀어 합치게 두면 한 조각을
    빠뜨려도 알 길이 없다.
    """
    if src.suffix.lower() == ".zip":
        import zipfile
        out: list[dict] = []
        with zipfile.ZipFile(src) as z:
            names = sorted(n for n in z.namelist()
                           if re.search(r"conversations(-\d+)?\.json$", n))
            if not names:
                raise SystemExit(f"zip 안에 conversations*.json 이 없다: {src}")
            for n in names:
                d = json.loads(z.read(n).decode("utf-8"))
                out.extend(d if isinstance(d, list) else d.get("conversations") or [])
        print(f"zip {src.name}: {len(names)}개 조각 → 대화 {len(out)}건")
        return out
    data = json.loads(src.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("conversations") or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default="conversations")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    convs = load_conversations(Path(args.src))
    outdir = Path(args.out)
    have = existing_ids(outdir) if outdir.is_dir() else set()

    made = skipped_dup = empty = 0
    for c in convs:
        cid = c.get("conversation_id") or c.get("id") or ""
        if cid in have:
            skipped_dup += 1
            continue
        r = render(c)
        if r is None:
            empty += 1
            continue
        fname, body = r
        dst = outdir / fname
        if dst.exists():
            skipped_dup += 1
            continue
        if not args.dry_run:
            outdir.mkdir(parents=True, exist_ok=True)
            dst.write_text(body, encoding="utf-8")
        made += 1
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}export {len(convs)}건 → 신규 {made} / 기존(ID 일치) {skipped_dup}"
          f" / 빈 대화 {empty} ({outdir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
