"""restore_gemini_attachments.py — Gemini Takeout zip 의 미디어를 살려낸다.

`restore_chatgpt_attachments.py` 의 형제다. 하는 일도 같다:

  1. zip 안의 이미지·음성·영상을 `archive/<라벨>/attachments/` 로 꺼낸다.
  2. 이미 코퍼스에 있는 Gemini 대화 md 끝에 첨부 목록을 덧붙인다.

왜 필요한가 — `convert_gemini_takeout.py` 는 `내활동.json` 만 읽는다. 그런데
Takeout zip 에는 미디어가 함께 들어 있고(2026-08-26 실측: png 184 · jpg 113 ·
wav 59 · mp4 4 = 360개), 활동 항목의 `attachedFiles` 가 그것들을 **파일명으로**
가리킨다(300/360 매칭). 지금까지 그 360개는 코퍼스 밖에 있었다.

ChatGPT 쪽과 다른 점 하나 — Gemini 는 **실물이 zip 안에 다 있다.** ChatGPT 공식
export 는 첨부 참조 492건 중 301건(61%)만 실물이 왔다.

대화 짝짓기는 파일명의 `__gm_<cid8>` 로 한다 (Gemini 대화 md 의 `- ID:` 는
16자 hex 라 ChatGPT 용 IDRE 의 30자 이상 규칙에 안 걸린다).

턴 좌표는 안 건드린다 — 덧붙이는 머리글 `## 📎` 는 대화 턴 정규식
`^##[ \t]+(👤|🧑|🤖)` 에 안 걸린다. 두 번 돌려도 안전하다.

사용:
    python scripts/restore_gemini_attachments.py <takeout.zip> --label gemini_2026-08-26
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
CONV_DIR = BASE_DIR / "conversations"
ARCHIVE_DIR = BASE_DIR / "archive"

MARK = "## 📎 첨부"
CONV_URL = re.compile(r"gemini\.google\.com/app/([0-9a-f]+)")
TITLE_SUFFIX = re.compile(r"^(프롬프트를 입력함:|Prompted)\s*", re.M)
GM_TAG = re.compile(r"__gm_([0-9a-f]{8})\.md$")


def conv_id_of(entry: dict) -> str | None:
    for x in entry.get("titleUrl", []) if isinstance(entry.get("titleUrl"), list) else []:
        pass
    for x in (entry.get("details") or []) + (entry.get("subtitles") or []):
        m = CONV_URL.search(x.get("url") or "")
        if m:
            return m.group(1)
    m = CONV_URL.search(entry.get("titleUrl") or "")
    return m.group(1) if m else None


def group_entries(entries: list[dict]) -> dict[str, list[dict]]:
    """convert_gemini_takeout.convert 와 **같은 규칙**으로 묶는다 —
    다르게 묶으면 짝이 안 맞아 첨부가 엉뚱한 대화에 붙는다."""
    groups: dict[str, list[dict]] = {}
    for e in entries:
        if (e.get("header") or "").strip() not in ("Gemini 앱", "Gemini Apps"):
            continue
        cid = conv_id_of(e)
        if cid is None:
            cid = hashlib.md5(
                f"{e.get('time')}|{e.get('title')}".encode()).hexdigest()[:16]
        groups.setdefault(cid, []).append(e)
    return groups


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--label", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    z = zipfile.ZipFile(args.src)
    jname = next((n for n in z.namelist()
                  if n.endswith(("내활동.json", "MyActivity.json"))), None)
    if not jname:
        sys.exit("[abort] zip 안에 내활동.json 이 없다")
    entries = json.loads(z.read(jname).decode("utf-8"))

    # zip 안 미디어: 파일명 → zip 경로
    media = {n.rsplit("/", 1)[-1]: n for n in z.namelist()
             if not n.endswith("/") and not n.endswith(".json")}

    groups = group_entries(entries)
    # 대화별 첨부 파일명 (순서 유지, 중복 제거)
    atts_by_cid: dict[str, list[str]] = {}
    for cid, items in groups.items():
        seen, rows = set(), []
        for e in sorted(items, key=lambda x: x.get("time") or ""):
            for f in (e.get("attachedFiles") or []):
                name = f if isinstance(f, str) else (f.get("name") or "")
                if name and name not in seen:
                    seen.add(name)
                    rows.append(name)
        if rows:
            atts_by_cid[cid] = rows

    dest = ARCHIVE_DIR / args.label / "attachments"
    rel_dir = f"archive/{args.label}/attachments"
    n_files = 0
    if not args.dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for name, entry in media.items():
        dst = dest / name
        if not args.dry_run and not dst.exists():
            dst.write_bytes(z.read(entry))
        n_files += 1

    # 코퍼스의 Gemini 대화: cid8 → 경로
    corpus: dict[str, Path] = {}
    for p in CONV_DIR.glob("*__gm_*.md"):
        m = GM_TAG.search(unicodedata.normalize("NFC", p.name))
        if m:
            corpus[m.group(1)] = p

    touched = skipped = missing = 0
    for cid, names in atts_by_cid.items():
        p = corpus.get(cid[:8])
        if p is None:
            missing += 1
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            skipped += 1
            continue
        lines = [MARK + " (Takeout 에서 복원)", ""]
        for n in names:
            where = f"`{rel_dir}/{n}`" if n in media else "**실물 없음**"
            lines.append(f"- {n} — {where}")
        if not args.dry_run:
            p.write_text(text.rstrip() + "\n\n---\n\n" + "\n".join(lines) + "\n",
                         encoding="utf-8")
        touched += 1

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}첨부 있는 대화 {len(atts_by_cid)}건 | 미디어 {n_files}개 → {rel_dir}")
    print(f"{tag}코퍼스 덧붙임 {touched} / 이미 있음 {skipped} / 코퍼스에 없는 대화 {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
