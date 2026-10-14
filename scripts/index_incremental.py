"""
index_incremental.py — 인덱스 증분 갱신 (사서 데몬용, LLM 없음)

scripts/daily.sh 가 수집 직후 호출한다 (LLM 자격증명 불필요).

동작:
  1. --changed 목록(unpack 의 --changed-list 산출물)이 있으면
     해당 파일들의 기존 청크 삭제 (내용 갱신분의 stale 정리) 후 목록 소비(.done)
  2. conversations/ 의 미인덱싱 파일을 파일명 규칙으로 source 태깅해 추가
     (__cc_/__mr_ → claude_code, __cw_ → cowork, 그 외 → claude)
  3. cards/ 의 회상어·화두태그를 다리 청크로 태운다 (좌표는 원본 대화).
     갱신 판정은 파일명이 아니라 card_sig — 카드는 다시 생성되기 때문이다
  4. logs/index_state.json 에 index_as_of 기록 → 데몬이 answer 에 싣는다

사용: python scripts/index_incremental.py [--changed logs/incoming_changed.txt] [--dry-run]
환경: MEMEX_DATA_DIR (기본: 레포 루트)
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")        # 데몬 머신은 캐시로만 동작
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chromadb  # noqa: E402

from chroma_paging import get_all  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", ROOT))
CONV_DIR = BASE_DIR / "conversations"
CARDS_DIR = BASE_DIR / "cards"
DB_PATH = BASE_DIR / "memory_db"
STATE = BASE_DIR / "logs" / "index_state.json"

_DB = None


def get_db():
    """langchain Chroma 핸들 — 첫 호출에서만 만든다.

    대화 추가와 카드 다리 추가가 같은 핸들을 나눠 쓴다. E5 모델 로딩이 한 번이면
    되고, 같은 실행 안에서 두 컬렉션 핸들이 갈리지 않는다.
    """
    global _DB
    if _DB is None:
        from langchain_chroma import Chroma
        from memory_pipeline import E5Embeddings
        _DB = Chroma(persist_directory=str(DB_PATH), collection_name="memory",
                     embedding_function=E5Embeddings(model_name="intfloat/multilingual-e5-base"))
    return _DB


# 웹 대화의 출처는 파일에 안 남는다 — 수집기가 어디서 받았든 같은 md 로 굳힌다.
# 그래서 "웹이면 claude" 로 태깅했는데, 실제로는 ChatGPT 시절 대화가 섞여 있었다
# (2026-08-22 판정: 검색 인용 토큰 iturn/PUA 237건 + 2025 이전 증언 79건 + 자칭
# 호칭(그쪽 모델을 부르던 별칭) 201건 → 확정 422건). 판정 결과를 목록 파일로 굳혀 여기서 읽는다.
# 목록은 개인 데이터라 리포가 아니라 MEMEX_DATA_DIR/config/ 에 둔다.
_PROV_CHATGPT: set | None = None


def _chatgpt_files() -> set:
    global _PROV_CHATGPT
    if _PROV_CHATGPT is None:
        import unicodedata
        f = BASE_DIR / "config" / "provenance_chatgpt.txt"
        try:
            _PROV_CHATGPT = {unicodedata.normalize("NFC", l.strip())
                             for l in f.read_text(encoding="utf-8").splitlines() if l.strip()}
        except OSError:
            _PROV_CHATGPT = set()
    return _PROV_CHATGPT


def source_for(name: str) -> str:
    if "__cw_" in name:
        return "cowork"
    if "__cc_" in name or "__mr_" in name:
        return "claude_code"
    if "__gm_" in name:
        return "gemini"          # convert_gemini_takeout.py 가 만든 파일명 태그
    if "__gp_" in name:
        return "chatgpt"         # convert_chatgpt_json.py (신규 이관분 — 기존은 아래 목록)
    import unicodedata
    if unicodedata.normalize("NFC", name) in _chatgpt_files():
        return "chatgpt"
    return "claude"


def delete_stale(col, names: set[str], dry: bool) -> int:
    got = get_all(col, ["metadatas"])
    victims = [cid for cid, m in zip(got["ids"], got["metadatas"])
               if (m or {}).get("source_file") in names]
    if victims and not dry:
        for i in range(0, len(victims), 500):
            col.delete(ids=victims[i:i + 500])
    return len(victims)


def index_cards(col, dry: bool) -> tuple[int, int, int]:
    """카드의 회상어를 다리 청크로 태운다. 반환은 (추가 청크, 삭제 청크, 대상 카드).

    대상 카드 수를 따로 돌려주는 건 --dry-run 때문이다. dry 에서는 추가가 0 이라
    "+0 / -0" 만 찍히는데, 그러면 448장이 밀려 있어도 할 일이 없는 것처럼 읽힌다.

    카드는 대화와 달리 **다시 생성된다** (재요약·구간 재분할). 그래서 파일명만 보고
    "이미 넣었다" 고 판정하면 재생성된 카드가 옛 어휘로 굳는다 — 대화 쪽 증분이
    파일명만 보다가 겪은 사각지대와 같은 함정이다. card_sig(회상 어휘 지문)를
    대조해 **바뀐 카드만** 지우고 다시 넣는다.
    """
    import unicodedata

    from memory_pipeline import (card_recall_blocks, card_signature, card_title,
                                 parse_card_file)

    def nfc(s: str) -> str:
        return unicodedata.normalize("NFC", s or "")

    conv_on_disk = {nfc(p.name) for p in CONV_DIR.glob("*.md")}

    got = get_all(col, ["metadatas"])
    have: dict[str, str] = {}                 # card_file → 인덱스에 들어있는 sig
    ids_by_card: dict[str, list[str]] = {}
    for cid, m in zip(got["ids"], got["metadatas"]):
        m = m or {}
        if m.get("kind") != "card":
            continue
        cf = nfc(m.get("card_file"))
        have[cf] = m.get("card_sig") or ""
        ids_by_card.setdefault(cf, []).append(cid)

    keep: set[str] = set()                    # 내용 그대로 — 손대지 않는다
    todo: list[tuple[Path, str]] = []         # (카드 경로, 원본 대화 파일명)
    for cp in sorted(CARDS_DIR.glob("*/*.md")):
        conv = nfc(cp.stem + ".md")
        # 원문이 없는 카드는 넣지 않는다. 좌표가 죽은 히트가 되어 사서가
        # "manifest 에 없음" 만 달고 돌려준다 (고아 카드는 실제로 있었다).
        if conv not in conv_on_disk:
            continue
        text = cp.read_text(encoding="utf-8", errors="replace")
        blocks = card_recall_blocks(text)
        if not blocks:                        # 스텁 카드 등 — 회상 어휘가 없다
            continue
        # 표제까지 넣어 대조한다. 빼면 회상어가 그대로인 채 표제만 붙은 카드가
        # "이미 넣었다" 로 판정돼 새 이름이 인덱스에 영영 안 들어간다.
        if have.get(nfc(cp.name)) == card_signature(blocks, card_title(text)):
            keep.add(nfc(cp.name))
        else:
            todo.append((cp, conv))

    # keep 에 없는 카드의 옛 청크를 한 번에 걷는다 — 바뀐 카드, 사라진 카드,
    # 회상 어휘를 잃은 카드, 원문이 사라진 카드가 모두 여기로 온다.
    victims = [cid for cf, ids in ids_by_card.items() if cf not in keep for cid in ids]
    if victims and not dry:
        for i in range(0, len(victims), 500):
            col.delete(ids=victims[i:i + 500])

    added = 0
    if todo and not dry:
        db = get_db()
        for cp, conv in todo:
            docs = parse_card_file(str(cp), conv, source=source_for(conv))
            if docs:
                db.add_documents(docs)
                added += len(docs)
    return added, len(victims), len(todo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed", help="내용 갱신 파일 목록 (한 줄에 하나, 경로/이름 무관)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    col = chromadb.PersistentClient(path=str(DB_PATH)).get_collection("memory")
    print(f"[index] 시작 — 청크 {col.count():,}")

    # 1) stale 정리
    # 목록의 각 줄은 작성 기기의 경로 표기 그대로 온다 — 윈도우 노트북이 쓴
    # `C:\...\x.md` 를 POSIX 에서 Path().name 하면 백슬래시가 구분자가 아니라
    # 문자열 전체가 돌아온다. 구분자 양쪽을 직접 갈라 마지막 조각을 취하고,
    # 인덱스 메타(NFC)와 맞도록 정규화한다 (아래 2 단계와 같은 규칙).
    import unicodedata
    deleted = 0
    changed = Path(args.changed) if args.changed else None
    if changed and changed.exists():
        names = {unicodedata.normalize("NFC", l.strip().replace("\\", "/").rsplit("/", 1)[-1])
                 for l in changed.read_text(encoding="utf-8").splitlines() if l.strip()}
        if names:
            deleted = delete_stale(col, names, args.dry_run)
            print(f"[index] stale 삭제 {deleted} 청크 (갱신 파일 {len(names)}개)")
        if not args.dry_run:
            changed.replace(changed.with_suffix(changed.suffix + ".done"))

    # 2) 신규 추가 (source 는 파일명 규칙). 비교는 NFC 로 — 맥 디스크가 NFD 여도
    #    인덱스 메타(NFC, parse_md_file 이 정규화)와 어긋나 중복 추가되지 않게.
    got = get_all(col, ["metadatas"])
    indexed = {unicodedata.normalize("NFC", (m or {}).get("source_file") or "")
               for m in got["metadatas"]}
    new_files = [p for p in sorted(CONV_DIR.glob("*.md"))
                 if unicodedata.normalize("NFC", p.name) not in indexed]
    print(f"[index] 신규 {len(new_files)}개")

    added = 0
    if new_files and not args.dry_run:
        from memory_pipeline import parse_md_file
        db = get_db()
        per_source: dict[str, int] = {}
        for p in new_files:
            docs = parse_md_file(str(p), source=source_for(p.name))
            if docs:
                db.add_documents(docs)
                added += len(docs)
                s = source_for(p.name)
                per_source[s] = per_source.get(s, 0) + len(docs)
        print(f"[index] 추가 {added} 청크 | source 분포: {per_source}")

    # 3) 카드 회상어 다리. 대화 추가 뒤에 온다 — 갓 들어온 대화의 카드도 같은
    #    실행에서 다리를 얻고, 원문 없는 카드를 거르는 판정이 최신 목록을 본다.
    card_added, card_deleted, card_todo = index_cards(col, args.dry_run)
    print(f"[index] 카드 다리 — 대상 {card_todo}장 | +{card_added} / -{card_deleted} 청크")

    # 4) 상태 기록 — 실제로 뭔가 바뀌었을 때만 전진시킨다. index_as_of 는 데몬의
    #    BM25/컬렉션 캐시 무효화 신호라, 무변경 실행이 매번 스탬프를 밀면 다음 첫
    #    질의가 헛된 전량 재빌드를 문다.
    if not args.dry_run and (deleted or added or card_added or card_deleted
                             or not STATE.exists()):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "index_as_of": datetime.now().isoformat(timespec="minutes"),
            "chunks": col.count(),
        }, ensure_ascii=False), encoding="utf-8")
    print(f"[index] 완료 — 청크 {col.count():,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
