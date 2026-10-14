"""
trail_store.py — trail 영수증 검증·집계 (순수 stdlib, LLM 0)

명세: docs/specs/2026-08-08_trail_spec_v1.md
검색은 매번 재계산하지만 trail 은 한 번 검증된 연결의 저장이다. 회상 세션이
"실제로 유용했던 문서"를 영수증으로 투함하면, 여기서 검증하고 trail_state.json
에 적립만 한다 — 판단(무엇이 유용했는가)은 회상 세션의 LLM+사용자가 이미 했다.

mailbox_daemon(실시간 수령)과 rebuild_trail(processed/ 재집계)이 공유한다.
chromadb 등 무거운 의존성을 여기 들이지 말 것 — rebuild 는 검색 스택 없이 돈다.
맥 시스템 python3(3.9)로도 돌아야 한다 (future import 가 그 조건) — 데몬 venv 전용 아님.
"""

from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from pathlib import Path

# 명세 §2-1: relation 화이트리스트 3종 고정. 늘리지 않는다 —
# 풍성한 스키마는 유지 못 해 썩는다 (온톨로지 금지 원칙).
RELATIONS = {"근거", "후속", "연관"}
MAX_USED = 10
MAX_EDGES = 5
RECENT_QUERIES_KEEP = 3


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def validate_receipt(obj) -> tuple[dict | None, str | None]:
    """스키마 검증. (정규화된 영수증, 에러). 명세 §2-1.

    query 와 같은 관용: 스키마 밖 top-level 필드는 무시한다.
    단 relation 화이트리스트 밖 값은 영수증 전체 거부 (§6-4).
    """
    if not isinstance(obj, dict):
        return None, "schema: JSON object 아님"
    if obj.get("type") != "trail_receipt":
        return None, f"schema: 지원하지 않는 type ({obj.get('type')!r})"
    q = obj.get("q")
    if not isinstance(q, str) or not q.strip():
        return None, "schema: q (원 질의) 필수"
    if len(q) > 2000:
        return None, "schema: q 2000자 초과"
    # used 항목은 문자열(구판=card 간주) 또는 {"file","depth"} (P1.5). 하위호환.
    used = obj.get("used")
    if not isinstance(used, list) or not used or len(used) > MAX_USED:
        return None, f"schema: used 는 비어있지 않은 배열 (최대 {MAX_USED})"
    used_norm = []
    for u in used:
        if isinstance(u, str):
            f, d = u, "card"
        elif isinstance(u, dict):
            f, d = u.get("file"), u.get("depth", "card")
        else:
            return None, "schema: used 항목은 문자열 또는 {file, depth}"
        if not isinstance(f, str) or not f.strip():
            return None, "schema: used 항목의 file 필수"
        if d not in ("card", "deep"):
            return None, f"schema: depth 는 card|deep ({d!r})"
        used_norm.append({"file": nfc(f.strip()), "depth": d})
    edges_in = obj.get("edges") or []
    if not isinstance(edges_in, list) or len(edges_in) > MAX_EDGES:
        return None, f"schema: edges 는 배열 (최대 {MAX_EDGES})"
    edges = []
    for e in edges_in:
        if not isinstance(e, dict):
            return None, "schema: edge 는 object"
        frm, to, rel = e.get("from"), e.get("to"), e.get("relation")
        if not (isinstance(frm, str) and frm.strip() and isinstance(to, str) and to.strip()):
            return None, "schema: edge 의 from/to 필수"
        if rel not in RELATIONS:
            return None, f"relation 화이트리스트 밖: {rel!r} (허용: {sorted(RELATIONS)})"
        if nfc(frm) == nfc(to):
            return None, "schema: edge 의 from == to (자기 참조)"
        edges.append({"from": nfc(frm.strip()), "to": nfc(to.strip()), "relation": rel})
    note = obj.get("note")
    if note is not None and not isinstance(note, str):
        return None, "schema: note 는 문자열"
    return {"q": q.strip(), "used": used_norm, "edges": edges}, None


def edge_key(frm: str, rel: str, to: str) -> str:
    """`연관` 은 무방향 — 파일명 정렬로 A|연관|B == B|연관|A (명세 §2-3)."""
    if rel == "연관":
        frm, to = sorted((frm, to))
    return f"{frm}|{rel}|{to}"


def new_state() -> dict:
    return {"as_of": None, "nodes": {}, "edges": {}}


def apply_receipt(state: dict, spec: dict, conv_names: set,
                  date: str, now_iso: str) -> dict:
    """검증된 영수증 하나를 state 에 적립. manifest 대조는 **부분 수용** (§2-1):
    미존재 파일명 항목만 드랍하고 나머지는 받는다. 드랍 내역을 반환한다.

    conv_names: manifest 의 raw/conversations/ 파일명 집합 (NFC).
    date: last_used 로 기록할 YYYY-MM-DD (실시간=오늘, rebuild=파일 ModTime).
    """
    dropped_used = [u["file"] for u in spec["used"] if u["file"] not in conv_names]
    kept_used = [u for u in spec["used"] if u["file"] in conv_names]
    dropped_edges = [e for e in spec["edges"]
                     if e["from"] not in conv_names or e["to"] not in conv_names]
    kept_edges = [e for e in spec["edges"] if e not in dropped_edges]

    # 같은 영수증에 같은 파일이 두 번 나오면 1회만 세되, 하나라도 deep 이면 deep.
    depth_by_file: dict = {}
    for u in kept_used:
        depth_by_file[u["file"]] = "deep" if (depth_by_file.get(u["file"]) == "deep"
                                              or u["depth"] == "deep") else "card"
    deep_kept = 0
    for f, depth in depth_by_file.items():
        node = state["nodes"].setdefault(f, {"count": 0, "deep_count": 0,
                                             "last_used": date, "recent_queries": []})
        node.setdefault("deep_count", 0)        # 구판 state 하위호환
        node["count"] += 1
        if depth == "deep":
            node["deep_count"] += 1
            deep_kept += 1
        node["last_used"] = max(node["last_used"], date)
        rq = [spec["q"]] + [x for x in node["recent_queries"] if x != spec["q"]]
        node["recent_queries"] = rq[:RECENT_QUERIES_KEEP]

    seen_keys = set()
    for e in kept_edges:
        key = edge_key(e["from"], e["relation"], e["to"])
        if key in seen_keys:                    # 같은 영수증 내 중복 엣지 1회만
            continue
        seen_keys.add(key)
        edge = state["edges"].setdefault(key, {"count": 0, "last_used": date})
        edge["count"] += 1
        edge["last_used"] = max(edge["last_used"], date)

    state["as_of"] = now_iso
    return {"used_kept": len(depth_by_file), "used_dropped": dropped_used,
            "deep_kept": deep_kept,
            "edges_kept": len(seen_keys), "edges_dropped": dropped_edges}


def load_state(path: Path) -> dict:
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("nodes"), dict) \
                and isinstance(st.get("edges"), dict):
            return st
    except (OSError, json.JSONDecodeError):
        pass
    return new_state()


def save_state(path: Path, state: dict) -> None:
    """원자적 기록 — 같은 디렉토리에 tmp 후 replace (부분 기록 방지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def lookup_trail(state: dict, source_file: str, max_edges: int = 3) -> dict | None:
    """검색 hit(대화 파일)에 붙일 trail 요약 (P1 병기용). 노드가 없으면 None.

    반환: {"uses": 전체 회상수, "deep_uses": 원문까지 읽은 수,
           "edges": [{"to","relation","count"}, ...]}  (count 내림차순, 상위 max_edges).
    edges 는 이 파일이 from 인 방향 + `연관` 무방향(정렬 키라 어느 쪽이든)만.
    """
    sf = nfc(source_file)
    node = state.get("nodes", {}).get(sf)
    if not node:
        return None
    edges = []
    for key, ev in state.get("edges", {}).items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        frm, rel, to = parts
        if frm == sf:
            other = to
        elif rel == "연관" and to == sf:        # 무방향은 반대편도 이 노드 소유
            other = frm
        else:
            continue
        edges.append({"to": other, "relation": rel, "count": ev.get("count", 0)})
    edges.sort(key=lambda e: -e["count"])
    out = {"uses": node.get("count", 0), "deep_uses": node.get("deep_count", 0)}
    if edges:
        out["edges"] = edges[:max_edges]
    return out


def has_edge(state: dict, a: str, b: str) -> bool:
    """두 대화 사이에 (방향·relation 불문) 이미 확인된 edge 가 있는가."""
    edges = state.get("edges", {})
    for rel in RELATIONS:
        if edge_key(a, rel, b) in edges or edge_key(b, rel, a) in edges:
            return True
    return False


def edge_candidates(state: dict, used_files: list, limit: int = 5) -> list:
    """한 영수증에서 **함께 쓰인** 문서 쌍 중 아직 edge 가 없는 것 — 후보 제안용.

    같은 답을 만드는 데 둘 다 필요했다는 사실은 관계의 *후보*이지 관계가 아니다
    (온톨로지 금지 — 추측으로 잇지 않는다는 §2-1 원칙 그대로). 그래서 적립하지
    않고 부본에 실어 되돌려준다: 다음 회상에서 그 관계가 사용자 발화로 실제
    확인되면 클라이언트가 edges 로 넣는다. 영수증의 edges 가 늘 비어 오는 문제
    (2026-08-31 실사용: 3건 모두 공란)에 대한 사서 쪽 마중이다.
    """
    uniq: list = []
    for f in used_files:
        if f not in uniq:
            uniq.append(f)
    out = []
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            if not has_edge(state, a, b):
                out.append({"pair": [a, b]})
                if len(out) >= limit:
                    return out
    return out


def conv_names_from_manifest(manifest: dict) -> set:
    """manifest → raw/conversations/ 파일명 집합 (NFC). 경로 주입 차단의 근거 —
    manifest 존재값만 노드가 될 수 있다 (promote_thread 와 동일 원칙)."""
    return {nfc(Path(k).name) for k in (manifest.get("files") or {})
            if k.startswith("raw/conversations/")}
