"""restore_chatgpt_attachments.py — 공식 export zip 의 첨부를 살려낸다.

두 가지를 한다.

  1. **실물 보관** — zip 안의 `file-*` 를 `archive/<라벨>/attachments/` 로 꺼내고,
     원래 파일명(`참고자료.txt` 같은 것)을 되붙인다. export 안에서는
     `file-XXXX.dat` 라 무엇인지 알 수 없다.
  2. **기존 대화에 첨부 목록 덧붙이기** — 이미 코퍼스에 있는 대화(ID 일치)의
     md 끝에 첨부 목록을 붙인다.

왜 2가 필요한가 — 변환기는 **새로 만드는 파일**에만 첨부를 싣는다. 그런데 공식
export 769건 중 598건은 이미 코퍼스에 있고(옛 수집분), 그 파일들엔 첨부 흔적이
없다. 하필 근거 문서가 거기 많다 — 어느 옛 대화에 붙어 있던 근거 문서(표)가
그렇다. 그 문서를 읽고 결정을 내렸는데, 코퍼스엔 결론만 남고 근거는 이름조차
없었다.

**원문을 다시 만들지 않는다.** 옛 수집분에는 공식 export 에 없는 것이 들어 있다
(캔버스 본문 — 2026-08-26 실측: 콘솔 수집분 textdoc_id 411회 vs 공식 export 0회).
덮어쓰면 그게 사라진다. 그래서 **끝에 덧붙이기만** 한다.

턴 좌표는 안 건드린다. 덧붙이는 머리글은 `## 📎` 인데 대화 턴 정규식은
`^##[ \t]+(👤|🧑|🤖)` 라 안 걸린다 — 카드의 turn_index 와 read 좌표가 그대로다.

두 번 돌려도 안전하다 (이미 붙은 파일은 건너뛴다).

사용:
    python scripts/restore_chatgpt_attachments.py <export.zip> --label chatgpt_2026-08-26
    python scripts/restore_chatgpt_attachments.py <export.zip> --label ... --dry-run
"""

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
CONV_DIR = BASE_DIR / "conversations"
ARCHIVE_DIR = BASE_DIR / "archive"

IDRE = re.compile(r"^- (?:\*\*)?ID(?:\*\*)?: ?([0-9a-fA-F-]{30,})", re.M)
FILE_ID_RE = re.compile(r"(file-[A-Za-z0-9]+)")
MARK = "## 📎 첨부"
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(s: str) -> str:
    s = FORBIDDEN.sub("_", (s or "").strip())
    return (s[:120] or "이름없음")


def stored_name(fid: str, orig: str | None, fallback_ext: str = "") -> str:
    """보관 파일명. 원래 이름을 되붙이되 **id 를 두 번 적지 않는다.**

    export 안에서는 `file-XXXX.dat` 라 무엇인지 알 수 없어 이름을 붙이는 것인데,
    메타의 name 이 그냥 id 인 첨부가 섞여 있어 `file-XXX__file-XXX` 가 나왔다.
    그런 경우엔 id 만 남긴다.
    """
    if not orig or orig.startswith("file-"):
        return f"{fid}{fallback_ext}"
    out = f"{fid}__{safe_name(orig)}"
    ext = Path(orig).suffix.lower()
    if fallback_ext and not out.lower().endswith(ext or fallback_ext):
        out += fallback_ext
    return out


def load_conversations(z: zipfile.ZipFile) -> list[dict]:
    out: list[dict] = []
    for n in sorted(x for x in z.namelist()
                    if re.search(r"conversations(-\d+)?\.json$", x)):
        d = json.loads(z.read(n).decode("utf-8"))
        out.extend(d if isinstance(d, list) else d.get("conversations") or [])
    return out


def attachments_by_conv(convs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in convs:
        cid = c.get("conversation_id") or c.get("id") or ""
        seen: set[str] = set()
        rows: list[dict] = []
        for node in (c.get("mapping") or {}).values():
            m = node.get("message") or {}
            for a in ((m.get("metadata") or {}).get("attachments") or []):
                if not isinstance(a, dict):
                    continue
                key = f"{a.get('id')}|{a.get('name')}"
                if key in seen:
                    continue                      # 같은 첨부가 여러 턴에 실린다
                seen.add(key)
                rows.append(a)
        if rows:
            out[cid] = rows
    return out


def corpus_by_id() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in CONV_DIR.glob("*.md"):
        m = IDRE.search(p.read_text(encoding="utf-8", errors="replace")[:600])
        if m:
            out[m.group(1)] = p
    return out


def extract_files(z: zipfile.ZipFile, dest: Path, names: dict[str, str], dry: bool) -> int:
    """zip 의 file-* 를 꺼내며 원래 이름을 되붙인다. 반환은 꺼낸 개수."""
    n = 0
    for entry in z.namelist():
        if not entry.startswith("file-"):
            continue
        m = FILE_ID_RE.match(Path(entry).name)
        if not m:
            continue
        fid = m.group(1)
        dst = dest / stored_name(fid, names.get(fid), Path(entry).suffix)
        if not dry:
            dest.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                dst.write_bytes(z.read(entry))
        n += 1
    return n


def render_block(atts: list[dict], have: set[str], rel_dir: str,
                 stored_names: dict[str, str]) -> str:
    lines = [MARK + " (공식 export 에서 복원)", ""]
    for a in atts:
        name = (a.get("name") or "이름없음").strip()
        size = a.get("size")
        mime = a.get("mime_type") or ""
        bits = [x for x in (f"{size:,}B" if isinstance(size, int) else "", mime) if x]
        fid = a.get("id") or ""
        where = (f"`{rel_dir}/{stored_names.get(fid, fid)}`" if fid in have
                 else "**실물 없음**")
        lines.append(f"- {name}" + (f" ({' · '.join(bits)})" if bits else "") + f" — {where}")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--label", required=True, help="archive 안 보관 폴더 이름")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.src)
    convs = load_conversations(z)
    by_conv = attachments_by_conv(convs)
    names = {a.get("id"): a.get("name") for rows in by_conv.values() for a in rows
             if a.get("id")}
    have = {FILE_ID_RE.match(Path(n).name).group(1) for n in z.namelist()
            if n.startswith("file-") and FILE_ID_RE.match(Path(n).name)}

    dest = ARCHIVE_DIR / args.label / "attachments"
    n_files = extract_files(z, dest, names, args.dry_run)
    # md 가 가리킬 실제 보관 파일명 (실물이 있는 것만)
    stored = {}
    for entry in z.namelist():
        if not entry.startswith("file-"):
            continue
        m = FILE_ID_RE.match(Path(entry).name)
        if m:
            stored[m.group(1)] = stored_name(m.group(1), names.get(m.group(1)),
                                             Path(entry).suffix)

    rel_dir = f"archive/{args.label}/attachments"
    corpus = corpus_by_id()
    touched = skipped = missing = 0
    for cid, atts in by_conv.items():
        p = corpus.get(cid)
        if p is None:
            missing += 1
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            skipped += 1
            continue
        block = render_block(atts, have, rel_dir, stored)
        if not args.dry_run:
            p.write_text(text.rstrip() + "\n\n---\n\n" + block, encoding="utf-8")
        touched += 1

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}첨부 있는 대화 {len(by_conv)}건 | 실물 {n_files}개 → {rel_dir}")
    print(f"{tag}코퍼스 덧붙임 {touched} / 이미 있음 {skipped} / 코퍼스에 없는 대화 {missing}")
    if touched:
        print("  인덱스에 태우려면: python scripts/index_incremental.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
