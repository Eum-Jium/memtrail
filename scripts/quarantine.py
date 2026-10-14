"""
quarantine.py — 검역소 공용 로직: "Drive 에만 있는 것" 스캔 (명세 §6.6)

가벼운 import 만 쓴다 (json/subprocess/pathlib) — 사서 데몬(quarantine_list 명령)과
sync_memory_to_drive(말미 로그 출력)가 같은 판정을 공유하기 위한 단일 소스.
자동 편입은 여기 없다 — 귀화는 quarantine_adopt 명령만이 한다.
"""

import json
import subprocess
from pathlib import Path

# Drive raw/ 하위 ↔ 정본 로컬 디렉토리 (sync_memory_to_drive.RAW_DIRS 와 동일해야 함)
# rollups 는 2026-08-15 폐지됐지만 Drive·로컬 사본을 지우지 않고 남겼다. 여기서
# 빼면 매일 "Drive 에만 있는 것" 으로 검역 보고돼 노이즈가 된다 — 사본을 실제로
# 지우는 날 이 줄과 ROOT_KNOWN 의 archive.md 를 같이 뺄 것.
# trail 은 2026-08-14 에 발행 목록(RAW_DIRS)엔 들어갔는데 여기 빠져 있어,
# 그날부터 `raw/trail/trail_state.json` 이 매일 '정체불명' 으로 보고됐다.
# 발행 요약이 strays 와 unknown_prefix 를 합쳐 한 숫자로 찍어 묻혔다 —
# **RAW_DIRS 에 층을 더하면 이 맵도 같이 더한다** (2026-08-19 수리).
RAW_LOCAL_MAP = {"conversations": "conversations", "memory": "memory",
                 "cards": "cards", "rollups": "rollups",
                 "trail": "trail", "archive": "archive"}
# Drive 루트에서 "우리가 아는" 항목 — 그 외는 검역 보고 대상
# specs/ = 사용자의 Fable 세션 응고 문서함 (Drive 직행 저장, 로컬 짝 없음이 정상 — 2026-08-03 확인)
# canon/ = 정본 프로필 레이어 (2026-08-07). recent.md 와 같은 급의 낭독 대상이라 루트에 산다
ROOT_KNOWN = {"mailbox", "raw", "threads", "specs", "canon",
              "recent.md", "archive.md", "manifest.json"}


def _lsjson(rclone_bin: str, target: str, recursive: bool = False,
            timeout: int = 300) -> list[dict]:
    args = [rclone_bin, "lsjson", "--no-mimetype", "--no-modtime"]
    if recursive:
        args += ["--files-only", "-R"]
    args.append(target)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"rclone lsjson 실패 ({target}): {(r.stderr or '').strip()[:200]}")
    return json.loads(r.stdout or "[]")


def scan_quarantine(rclone_bin: str, remote: str, base_dir: Path) -> dict:
    """'Drive 에만 있는 것' 목록.

    - raw_strays: raw/ 미러인데 로컬 짝이 없는 파일 (역방향 유입·과거 이름변경 잔재)
    - raw_unknown_prefix: raw/ 하위인데 아는 4개 디렉토리 밖
    - root_strays: ClaudeMemory 루트의 정체불명 항목 (예: 외부에서 만든 폴더)
    """
    strays, unknown_prefix = [], []
    for e in _lsjson(rclone_bin, f"{remote}/raw", recursive=True):
        path = e.get("Path", "")
        top, _, rest = path.partition("/")
        item = {"path": f"raw/{path}", "id": e.get("ID"), "size": e.get("Size")}
        if top not in RAW_LOCAL_MAP or not rest:
            unknown_prefix.append(item)
        elif not (base_dir / RAW_LOCAL_MAP[top] / rest).exists():
            strays.append(item)

    root_strays = []
    try:
        for e in _lsjson(rclone_bin, remote):
            if e.get("Name") not in ROOT_KNOWN:
                root_strays.append({"name": e.get("Name", "") + ("/" if e.get("IsDir") else ""),
                                    "id": e.get("ID")})
    except RuntimeError:
        pass                                   # 루트 목록 실패는 raw 결과만이라도 반환

    return {"raw_strays": strays, "raw_unknown_prefix": unknown_prefix,
            "root_strays": root_strays}
