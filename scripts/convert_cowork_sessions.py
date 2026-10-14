"""
convert_cowork_sessions.py — 코워크 세션 반출 JSON → conversations/*.md

코워크(Cowork) 대화는 claude.ai 일반 대화 API 에도, 로컬 세션 jsonl 에도 남지 않는다.
`/v1/code/sessions` + `/{id}/events` 로만 읽히며, 브라우저에서 받아 반출한 JSON 을
여기서 md 로 옮긴다 (수집 절차는 daily-memex-web-sync 스킬 참조).

입력 JSON 형태 (반출 스크립트가 만드는 모양):
    [{"id": "cse_...", "title": "...", "created_at": "...", "origin": "desktop_app",
      "messages": [{"role": "user"|"assistant", "text": "...", "at": "..."}]}]

출력: conversations/{YYYY-MM-DD}_{title}__cw_{sid8}.md

멱등: 같은 세션 id 는 같은 파일로 간다. `--refresh` 면 대화가 길어졌을 때만 덮어쓰고,
내용이 같으면 파일을 건드리지 않는다 (mtime 이 바뀌면 카드가 통째로 재생성된다).

**교차 표면 접기 (2026-08-31)**: 데스크톱 앱 기본 탭에서 연 세션은 로컬 JSONL 로도
수집돼 이미 `__cc_` md 가 있다 — 그때 cw md 를 또 만들면 같은 대화가 인덱스에 두 벌
들어간다 (실측: 8/29 binder 세션 하나가 cc·cw 두 벌). 그래서 새 세션마다 같은 날짜의
cc 파일과 발화 흐름을 대조해(surface_fold.same_session), 같은 세션이면 md 를 만들지
않고 그 cc 파일 헤더에 `surfaces`/`cowork_id` 를 기록한다. 이후 실행은 그 주석의
cowork_id 로 바로 알아본다 (대조 재계산 없음). cc keeper 로 접힌 세션은 --refresh
대상도 아니다 — 그 대화의 갱신은 로컬 JSONL 수집(unpack --refresh)이 소유한다.
"""

import argparse
import os
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import surface_fold as sf  # noqa: E402
from conv_key import slug as _cmp_slug  # noqa: E402
from unpack_claude_code_jsonl import _clean_user_turn, _slug  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CW_TAG = re.compile(r"__cw_([A-Za-z0-9]{8})\.md$")
ID_LINE = re.compile(r"^- ID: (cse_[A-Za-z0-9]+)\s*$", re.M)


def sid8(session_id: str) -> str:
    """cse_01AbCdEfXXXX... → 01AbCdEf (접두사를 뺀 앞 8자)."""
    return re.sub(r"^cse_", "", session_id)[:8]


def session_title(sess: dict) -> str:
    """세션 제목. 서버가 안 붙여준 세션(로컬 바인딩분)은 첫 실발화에서 뽑는다."""
    title = (sess.get("title") or "").strip()
    if title:
        return title
    for msg in sess.get("messages") or []:
        if msg.get("role") != "user":
            continue
        text, _ = _clean_user_turn((msg.get("text") or "").strip())
        if text:
            return re.sub(r"\s+", " ", text).strip()[:40]
    return "Untitled"


def to_md(sess: dict) -> str:
    title = session_title(sess)
    date = (sess.get("created_at") or "")[:10]
    lines = [
        f"# {title}", "",
        f"- 생성일: {date}",
        f"- ID: {sess.get('id', '')}",
        "- source: cowork",
        f"- origin: {sess.get('origin') or 'unknown'}",
        f"- title: {title}",
        "",
    ]
    for msg in sess.get("messages") or []:
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        if msg.get("role") == "user":
            text, _is_cmd = _clean_user_turn(text)
            if not text:
                continue
            who = "👤 User"
        else:
            who = "🤖 Assistant"
        lines += ["---", "", f"## {who}", "", text, ""]
    return "\n".join(lines)


def out_name(sess: dict) -> str:
    date = (sess.get("created_at") or "0000-00-00")[:10]
    title = _slug(session_title(sess), 40) or "untitled"
    return f"{date}_{title}__cw_{sid8(sess.get('id', ''))}.md"


def existing_by_id(out_dir: Path) -> dict[str, Path]:
    """이미 저장된 세션 → {cse id: 경로}. 제목이 바뀌어도 같은 파일을 찾는다.

    cw md 의 `- ID:` 만 보다가, 접힌 세션의 열쇠인 cc keeper 의 `- cowork_id:` 도
    함께 걷는다 — 전수 스캔이지만 파일당 머리 600B 라 수천 건에도 수 초다.
    """
    found = {}
    for p in out_dir.glob("*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            continue
        cid = sf.cowork_id_of(head)
        if cid:
            # keeper(cc) 가 cw md 보다 우선 — 같은 id 가 양쪽에 있으면 접힌 쪽이 정답
            if cid not in found or "__cw_" in found[cid].name:
                found[cid] = p
    return found


def session_user_slugs(sess: dict) -> list[str]:
    """반출 세션 → 사용자 발화 슬러그 목록 (cc 변환과 같은 세척을 거친다)."""
    out = []
    for msg in sess.get("messages") or []:
        if msg.get("role") != "user":
            continue
        text, _ = _clean_user_turn((msg.get("text") or "").strip())
        if text and (s := _cmp_slug(text)):
            out.append(s)
    return out


def convert(input_path: str, out_dir: str, refresh: bool = False,
            changed_list: str = "", dry_run: bool = False) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sessions", [data])

    known = existing_by_id(out)
    saved = skipped = refreshed = unchanged = empty = folded = 0
    changed_paths = []

    for sess in data:
        if not isinstance(sess, dict) or not sess.get("id"):
            continue
        text = to_md(sess)
        if "## " not in text:          # 발화가 하나도 안 남은 세션 (주입 블록뿐)
            empty += 1
            continue
        prev = known.get(sess["id"])
        if prev is not None:
            if "__cw_" not in prev.name:
                # cc keeper 로 접힌 세션 — 이 대화의 갱신은 로컬 JSONL 수집 소유
                skipped += 1
                continue
            if not refresh:
                skipped += 1
                continue
            if prev.read_text(encoding="utf-8", errors="replace") == text:
                unchanged += 1
                continue
            if not dry_run:
                prev.write_text(text, encoding="utf-8", newline="\n")
            refreshed += 1
            changed_paths.append(prev)
            print(f"  ~ {prev.name}")
            continue
        # 새 세션 — 같은 날짜 cc 파일과 같은 세션이면 md 를 만들지 않고 접는다.
        date = (sess.get("created_at") or "")[:10]
        users = session_user_slugs(sess)
        matches = (sf.find_cc_partner(out, date, users)
                   if re.match(r"\d{4}-\d{2}-\d{2}$", date) and users else [])
        if len(matches) == 1:
            if not dry_run:
                sf.annotate_cowork(matches[0], sess["id"])
            known[sess["id"]] = matches[0]
            folded += 1
            print(f"  ⇒ {matches[0].name}  (cc 와 같은 세션 — cw md 생성 안 함)")
            continue
        if matches:
            # 후보가 여럿이면 접지 않고 그대로 만든다 — 잘못 접으면 대화가 사라지고,
            # 중복은 남아도 dedupe_sessions 가 보류로 보여준다.
            print(f"  ⚠ {sess['id']}: 같은 날 cc 후보 {len(matches)}개 — 접지 않고 저장")
        fpath = out / out_name(sess)
        if not dry_run:
            fpath.write_text(text, encoding="utf-8", newline="\n")
        known[sess["id"]] = fpath
        saved += 1
        print(f"  + {fpath.name}")

    if changed_list and not dry_run:
        Path(changed_list).expanduser().write_text(
            "".join(f"{p}\n" for p in changed_paths), encoding="utf-8")

    label = "저장 예정" if dry_run else "저장"
    tail = f", 갱신 {refreshed}, 그대로 {unchanged}" if refresh else ""
    if folded:
        tail += f", cc 로 접음 {folded}"
    if empty:
        tail += f", 발화없음 {empty}"
    print(f"완료: {label} {saved}, 스킵(기존) {skipped}{tail}")
    return saved + refreshed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=os.environ.get("MEMEX_DATA_DIR", ".") + "/conversations")
    ap.add_argument("--refresh", action="store_true",
                    help="이미 받아둔 세션도 대화가 길어졌으면 같은 파일에 덮어쓴다")
    ap.add_argument("--changed-list", default="",
                    help="--refresh 로 실제 갱신된 md 경로를 적을 파일 (재인덱싱 대상)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    convert(args.input, args.out, args.refresh, args.changed_list, args.dry_run)
