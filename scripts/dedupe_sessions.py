#!/usr/bin/env python3
"""
dedupe_sessions.py — 같은 세션이 여러 md 로 갈라진 것을 정리한다.

왜 생기나 — 세 갈래다.

**① 코드·코워크 세션 (sid 있음)**: 출력 파일명은
`<날짜>_<프로젝트슬러그>_<제목>__<태그>_<sid8>.md` 인데 슬러그·제목·날짜가 나중에
바뀌면(프로젝트 폴더 개명, ai-title 뒤늦은 기록, UTC→로컬 날짜 교정) 같은 세션이
**다른 이름으로 한 번 더** 저장됐다. 재발은 unpack_claude_code_jsonl.py 가 sid 로
기존 파일을 재사용하도록 고쳐 막았다.

**② 웹 대화 (sid 없음)**: 수집기의 파일명 규칙이 `YYYYMMDD_제목_밑줄.md` 에서
`YYYY-MM-DD_제목 공백.md` 로 바뀌던 시점의 것들이 개명 없이 한 벌 더 저장됐다.

    2025-03-14_Example conversation title.md
    20250314_Example_conversation_title.md

**③ 교차 표면 (cc ↔ cw, 2026-08-31)**: 데스크톱 앱 기본 탭("채팅 및 Cowork")에서
연 Code 세션은 로컬 JSONL(`__cc_`)과 클라우드 반출(`__cw_`) 양쪽으로 수집되는데,
두 경로의 세션 id 가 서로 달라 conv_key 로도 안 접힌다. 판정·접기 규칙은
scripts/surface_fold.py 가 단일 소스다 — 제목이 아니라 사용자 발화 흐름으로
같은 세션임을 확인하고, cc 를 남기며 헤더에 `surfaces`/`cowork_id` 를 기록한다
(다음 클라우드 반출이 같은 세션을 다시 만들지 않게 하는 열쇠).

어느 쪽이든 옛 파일은 지워지지 않아 한 대화가 인덱스에 두 벌 들어가고, 회상 결과에서
**검색 슬롯을 두 개 먹는다** (k=6 질의가 실질 5건이 된 사고 — 2026-08-20).
사서 쪽은 conv_key 로 히트를 접어 즉시 막았고, 이 스크립트는 원인을 치운다.
(③ 은 conv_key 가 못 접는 갈래라 이 스크립트와 수집기 예방이 전부다.)

정체 판정은 scripts/conv_key.py 가 단일 소스다. 다만 **지우는 결정은 키만으로 하지
않는다** — 같은 날 제목까지 같은 다른 대화가 이론상 가능하므로, 첫 사용자 발화의
지문까지 맞을 때만 삭제하고 어긋나면 보고만 하고 건드리지 않는다.

남길 파일: **내용이 가장 많은 것**(같은 대화의 더 완전한 스냅샷). 크기가 같으면
이름이 사전순으로 앞선 것 — 수집기의 재사용 규칙(sorted glob 의 첫 번째)과 같은
기준이라, 남은 파일이 그대로 다음 수집에 이어진다.

    python scripts/dedupe_sessions.py                    # 미리보기 (기본)
    python scripts/dedupe_sessions.py --apply            # 실제 삭제
    python scripts/dedupe_sessions.py --apply --cards    # 딸린 카드까지 삭제
    python scripts/dedupe_sessions.py --dir /path/to/conversations

삭제한 파일명은 --deleted-list 로 받아 적을 수 있다 (원격 인덱스 정리용).
인덱스의 고아 청크는 scripts/reindex_repaired.py 가 따로 걷어낸다.
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import surface_fold as sf  # noqa: E402
from conv_key import DATE_RE, conv_key, slug  # noqa: E402
from turn_reader import read_text, turn_chunks  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path):
    """단순 .env 로더 — 셸 export 최우선(setdefault), 값의 따옴표·CR 은 벗긴다.
    (build_memory_cards.py 와 같은 규약: 리포 .env 를 먼저 읽어야 MEMEX_DATA_DIR 가 산다)"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(_REPO_DIR / ".env")
BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", _REPO_DIR))
_load_env(BASE_DIR / ".env")

def group_by_session(conv_dir: Path) -> dict:
    """정체 키 → 같은 대화의 파일들 (2개 이상인 것만)."""
    groups = defaultdict(list)
    for p in conv_dir.glob("*.md"):
        groups[conv_key(p.name)].append(p)
    return {k: v for k, v in groups.items() if len(v) > 1}


def fingerprint(p: Path) -> str:
    """첫 사용자 발화의 지문. 추출 세대가 달라도 사용자의 말은 그대로다 —
    Assistant 쪽은 세대에 따라 thinking 블록 유무가 갈려 지문으로 못 쓴다."""
    for c in turn_chunks(read_text(p)):
        if c["user"]:
            return slug(c["user"])[:200]
    return ""


def same_conversation(files: list) -> bool:
    """지문이 서로 맞물리는가. 한쪽이 잘려 있을 수 있어 접두 포함까지 인정한다.
    지문을 못 뽑은 파일이 있으면 **모른다 = 아니다** 로 본다 (삭제는 되돌릴 수 없다)."""
    fps = [fingerprint(p) for p in files]
    if any(len(f) < 20 for f in fps):
        return False
    base = max(fps, key=len)
    return all(base.startswith(f) or f.startswith(base) for f in fps)


def pick_keeper(files: list) -> Path:
    """가장 큰 것 → 동률이면 사전순 첫 번째 (수집기의 재사용 규칙과 동일)."""
    return sorted(files, key=lambda p: (-p.stat().st_size, p.name))[0]


def card_month(name: str) -> str | None:
    """대화 파일명 → 카드 폴더 이름(YYYY-MM). 두 파일명 규칙을 모두 받는다 —
    `name[:7]` 로 자르던 옛 방식은 `20250314_…` 를 `2025031` 이라는 없는 달로 보냈다."""
    m = DATE_RE.match(name)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def delete_card(cards_dir: Path, name: str) -> Path | None:
    """삭제된 대화에 딸린 카드를 걷는다. 걷었으면 그 경로를 돌려준다."""
    month = card_month(name)
    cp = cards_dir / month / name if month else None
    if cp and cp.exists():
        cp.unlink()
        return cp
    return None


def cross_surface_pairs(conv: Path) -> tuple[list, list]:
    """cw 파일마다 같은 세션의 cc 파일을 찾는다 → ([(cw, cc)], 보류 목록).

    판정은 surface_fold.same_session (발화 흐름 대조 — 제목은 형식이 달라 못 쓴다).
    같은 날 후보가 둘 이상이면 보류 — 기계가 고를 자리가 아니다.
    """
    pairs, held = [], []
    for cwp in sorted(conv.glob("*__cw_*.md")):
        if not DATE_RE.match(cwp.name):
            continue
        try:
            cw_users = sf.user_slugs_from_md(cwp)
        except OSError:
            continue
        matches = sf.find_cc_partner(conv, cwp.name[:10], cw_users)
        if len(matches) == 1:
            pairs.append((cwp, matches[0]))
        elif matches:
            held.append((cwp, matches))
    return pairs, held


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(BASE_DIR / "conversations"))
    ap.add_argument("--cards-dir", default=str(BASE_DIR / "cards"))
    ap.add_argument("--apply", action="store_true", help="실제로 삭제 (기본은 미리보기)")
    ap.add_argument("--cards", action="store_true", help="삭제 대상의 카드도 같이 삭제")
    ap.add_argument("--deleted-list", help="삭제한 파일명을 적을 경로")
    args = ap.parse_args()

    conv = Path(args.dir)
    if not conv.is_dir():
        sys.exit(f"[abort] 대화 폴더가 없다: {conv}")

    dups = group_by_session(conv)
    deleted, suspect = [], []
    for key, files in sorted(dups.items()):
        keeper = pick_keeper(files)
        # 키가 같아도 지문이 어긋나면 손대지 않는다. sid 그룹은 세션 id 자체가 정체라
        # 지문을 묻지 않는다 — 같은 세션의 스냅샷 두 장은 앞부분이 잘려 있을 수 있고,
        # 그걸 "다른 대화" 로 오판하면 정작 걷어야 할 중복이 안 걷힌다.
        if not key.startswith("sid:") and not same_conversation(files):
            suspect.append(key)
            print(f"[{key}]  ⚠ 정체 키는 같은데 첫 발화 지문이 다르다 — 건너뛴다")
            for p in sorted(files, key=lambda x: x.name):
                print(f"  보존 {p.stat().st_size:>8,}B  {p.name}")
            continue
        print(f"[{key}]")
        print(f"  유지 {keeper.stat().st_size:>8,}B  {keeper.name}")
        for p in sorted(files, key=lambda x: x.name):
            if p == keeper:
                continue
            print(f"  삭제 {p.stat().st_size:>8,}B  {p.name}")
            deleted.append(p.name)
            if args.apply:
                p.unlink()
                if args.cards:
                    cp = delete_card(Path(args.cards_dir), p.name)
                    if cp:
                        print(f"       └ 카드 삭제: {cp.parent.name}/{cp.name}")

    # ── ③ 교차 표면 접기 (cc ↔ cw) — conv_key 그룹 정리 **뒤에** 돈다:
    #    같은 sid 의 cw 스냅샷 두 장이 남아 있으면 후보가 둘로 보여 보류되므로,
    #    sid 그룹부터 걷어야 짝이 하나로 좁혀진다.
    pairs, held = cross_surface_pairs(conv)
    folded = 0
    for cwp, ccp in pairs:
        cid = sf.cowork_id_of(read_text(cwp))
        print(f"[표면 접기 {cwp.name[:10]}]")
        print(f"  유지 {ccp.stat().st_size:>8,}B  {ccp.name}"
              f"  ← surfaces: code, cowork{f' / {cid}' if cid else ''}")
        print(f"  삭제 {cwp.stat().st_size:>8,}B  {cwp.name}")
        deleted.append(cwp.name)
        folded += 1
        if args.apply:
            if cid:
                sf.annotate_cowork(ccp, cid)
            cwp.unlink()
            if args.cards:
                cp = delete_card(Path(args.cards_dir), cwp.name)
                if cp:
                    print(f"       └ 카드 삭제: {cp.parent.name}/{cp.name}")
    for cwp, matches in held:
        suspect.append(cwp.name)
        print(f"[표면 접기]  ⚠ {cwp.name} — 같은 날 cc 후보가 {len(matches)}개라 보류")
        for p in matches:
            print(f"  후보 {p.name}")

    if not deleted and not suspect:
        print("[done] 중복 세션 없음")
        return 0

    if args.deleted_list and args.apply:
        Path(args.deleted_list).write_text(
            "".join(f"{n}\n" for n in deleted), encoding="utf-8")

    print(f"\n[{'done' if args.apply else 'dry-run'}] 중복 {len(dups)}그룹"
          + (f" / 표면 접기 {folded}쌍" if folded else "")
          + f" / 삭제 대상 {len(deleted)}개"
          + (f" / 보류 {len(suspect)}건" if suspect else ""))
    if suspect:
        print("  보류 그룹은 손으로 확인할 것 — 같은 날 같은 제목의 다른 대화일 수 있다")
    if not args.apply:
        print("  실제로 지우려면 --apply (카드까지면 --apply --cards)")
    else:
        print("  인덱스의 고아 청크 정리: python scripts/reindex_repaired.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
