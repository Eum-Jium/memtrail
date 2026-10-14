"""
trail_report.py — trail 침전 현황 관측 (순수 stdlib, LLM 0)

P2(1-hop 이웃 보너스)는 엣지가 실제로 밟혀서 쌓인 뒤에 여는 트랙이다
(명세 §5 "밟히기 전에 길 넓히지 않기"). 그 진입 판단을 감으로 하지 않으려고
숫자를 뽑는다. 핵심 지표는 현재 엣지 수가 아니라 **영수증당 used 개수 분포**
— 엣지는 한 회상에서 문서 둘 이상을 써야 나오므로, used=1 영수증만 쌓이는
동안은 시간이 더 흘러도 엣지는 생기지 않는다 (2026-08-13 실측: 5통 전부 1).

판정은 하지 않는다. 어느 영수증이 검증용이고 어느 게 진짜 회상인지는
q/note 를 보고 사용자가 가른다 — trail 은 굵기지 정답이 아니다.

사용 (상시 PC):
    python scripts/trail_report.py                # state + processed/ 영수증
    python scripts/trail_report.py --no-receipts  # state 만 (rclone 호출 없음)
    python scripts/trail_report.py --top 20       # 노드 표시 개수
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trail_store  # noqa: E402

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")
TRAIL_STATE = BASE_DIR / "trail" / "trail_state.json"
MAX_BYTES = 16 * 1024
TNAME_RE = re.compile(r"^t_[A-Za-z0-9_.-]{1,80}\.json$")
ARROW = {"근거": "─근거→", "후속": "─후속→", "연관": "─연관─"}


def rclone(args: list, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["rclone"] + args + ["--log-level", "ERROR"],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def summarize_receipts(recs: list) -> dict:
    """영수증 레코드 목록 → P2 진입 판단 지표. 순수 함수 (테스트 대상).

    recs 항목: {"used": int|None, "edges": int, "date": str, "err": str|None}
    used 분포·엣지 보유율은 **검증 통과분만** 센다 (탈락분은 집계에 안 들어가므로).
    """
    ok = [r for r in recs if not r.get("err")]
    dist = Counter(r["used"] for r in ok)
    dates = sorted(r["date"] for r in ok if r.get("date"))
    return {
        "total": len(recs),
        "ok": len(ok),
        "rejected": len(recs) - len(ok),
        "used_dist": dist,
        "multi_used": sum(n for u, n in dist.items() if u >= 2),
        "with_edges": sum(1 for r in ok if r["edges"] > 0),
        "edges_total": sum(r["edges"] for r in ok),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "by_date": Counter(r["date"] for r in ok if r.get("date")),
    }


def fetch_receipts() -> list:
    """processed/ 의 t_*.json 을 읽어 레코드화. 검증은 trail_store 단일 소스."""
    r = rclone(["lsjson", "--files-only", f"{REMOTE}/mailbox/processed"])
    if r.returncode != 0:
        print(f"  processed/ 목록 실패: {r.stderr.strip()[:200]}")
        return []
    entries = sorted((e for e in json.loads(r.stdout or "[]")
                      if TNAME_RE.match(e.get("Name", ""))),
                     key=lambda e: e.get("Name", ""))
    recs = []
    for e in entries:
        name = e["Name"]
        date = (e.get("ModTime") or "")[:10]
        if e.get("Size", 0) > MAX_BYTES:
            recs.append({"name": name, "date": date, "err": "oversized",
                         "used": None, "edges": 0, "q": "", "note": ""})
            continue
        rr = rclone(["cat", f"{REMOTE}/mailbox/processed/{name}"])
        if rr.returncode != 0:
            recs.append({"name": name, "date": date, "err": "read fail",
                         "used": None, "edges": 0, "q": "", "note": ""})
            continue
        try:
            obj = json.loads(rr.stdout)
        except json.JSONDecodeError as ex:
            recs.append({"name": name, "date": date, "err": f"invalid json ({ex.msg})",
                         "used": None, "edges": 0, "q": "", "note": ""})
            continue
        spec, err = trail_store.validate_receipt(obj)
        if err:
            recs.append({"name": name, "date": date, "err": err, "used": None,
                         "edges": 0, "q": str(obj.get("q", ""))[:60],
                         "note": str(obj.get("note", ""))[:60]})
            continue
        recs.append({
            "name": name, "date": date, "err": None,
            "used": len(spec["used"]),
            "deep": sum(1 for u in spec["used"] if u["depth"] == "deep"),
            "edges": len(spec["edges"]),
            "q": spec["q"][:60], "note": str(obj.get("note", ""))[:60],
        })
    return recs


def print_state(state: dict, top: int) -> None:
    nodes, edges = state.get("nodes", {}), state.get("edges", {})
    print(f"=== state (as_of {state.get('as_of') or '없음'})")
    print(f"  nodes {len(nodes)} / edges {len(edges)}\n")

    if nodes:
        print(f"=== 자주 밟힌 노드 (상위 {min(top, len(nodes))})")
        ranked = sorted(nodes.items(),                  # count desc, 동률은 최근 순
                        key=lambda kv: (kv[1].get("count", 0), kv[1].get("last_used", "")),
                        reverse=True)
        for name, n in ranked[:top]:
            deep = n.get("deep_count", 0)
            depth = f" (원문 {deep}회)" if deep else ""
            print(f"  {n.get('count', 0):2d}회{depth}  {name}   최근 {n.get('last_used', '?')}")
            for q in n.get("recent_queries", [])[:1]:
                print(f"        └ \"{q}\"")
        print()

    print("=== 엣지")
    if not edges:
        print("  없음 — P2(1-hop 이웃)가 참조할 게 아직 없다.\n")
    else:
        for key, ev in sorted(edges.items(), key=lambda kv: -kv[1].get("count", 0)):
            parts = key.split("|")
            if len(parts) != 3:
                continue
            frm, rel, to = parts
            print(f"  {ev.get('count', 0):2d}회  {frm} {ARROW.get(rel, rel)} {to}"
                  f"   최근 {ev.get('last_used', '?')}")
        print()


def print_receipts(recs: list) -> None:
    s = summarize_receipts(recs)
    print(f"=== 영수증 (processed/ {s['total']}건 — 수용 {s['ok']} / 탈락 {s['rejected']})")
    if not s["ok"]:
        print("  집계된 영수증 없음\n")
        return
    dist = "  ".join(f"used {u}개: {n}건" for u, n in sorted(s["used_dist"].items()))
    print(f"  {dist}")
    print(f"  문서 2개 이상 쓴 회상: {s['multi_used']}건   "
          f"edges 포함: {s['with_edges']}건 (엣지 {s['edges_total']}개)")
    print(f"  기간: {s['first_date']} ~ {s['last_date']}   "
          f"({len(s['by_date'])}일에 걸쳐 {s['ok']}건)")
    print()
    print("  ※ 엣지는 한 회상에서 문서 2개 이상을 써야 나온다. 'used 1개'만 쌓이는")
    print("     동안은 시간이 더 흘러도 엣지는 생기지 않는다 — P2 진입의 선행지표는")
    print("     엣지 수가 아니라 위의 '문서 2개 이상 쓴 회상' 건수다.\n")

    print("=== 영수증 목록 (검증용/실회상 구분은 q·note 로)")
    for r in recs:
        if r.get("err"):
            print(f"  {r['date']}  {r['name']}  ✗ {r['err']}")
        else:
            deep = f"/deep {r['deep']}" if r.get("deep") else ""
            print(f"  {r['date']}  {r['name']}  used {r['used']}{deep}  "
                  f"edges {r['edges']}  \"{r['q']}\"")
        if r.get("note"):
            print(f"        [{r['note']}]")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-receipts", action="store_true",
                    help="processed/ 스캔 생략 (rclone 호출 없음)")
    ap.add_argument("--top", type=int, default=15, help="노드 표시 개수 (기본 15)")
    args = ap.parse_args()

    print_state(trail_store.load_state(TRAIL_STATE), args.top)
    if not args.no_receipts:
        print_receipts(fetch_receipts())
    return 0


if __name__ == "__main__":
    sys.exit(main())
