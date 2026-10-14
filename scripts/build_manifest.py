"""
build_manifest.py — 로컬경로 ↔ Drive fileId 매핑 생성 (명세 §1, §6.2)

rclone lsjson 으로 Drive 실측 → manifest.json 생성 → Drive 에도 업로드.
사서 데몬이 검색 결과의 source_file 을 file_id 좌표로 변환할 때 쓴다.

사용: python scripts/build_manifest.py [--no-upload]
환경: MEMEX_DATA_DIR, MEMEX_DRIVE_REMOTE(기본 gdrive:ClaudeMemory), RCLONE_BIN
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", Path(__file__).resolve().parent.parent))
REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")
OUT = BASE_DIR / "manifest.json"


def find_rclone() -> str:
    exe = os.environ.get("RCLONE_BIN") or shutil.which("rclone")
    if not exe:
        local = Path.home() / ".local/bin/rclone"
        if local.exists():
            exe = str(local)
    if not exe:
        raise SystemExit("rclone 을 찾지 못함 (RCLONE_BIN 설정 필요)")
    return exe


def lsjson(rclone: str, target: str, recursive: bool) -> list[dict]:
    args = [rclone, "lsjson", "--files-only", "--no-mimetype", "--no-modtime"]
    if recursive:
        args.append("-R")
    args.append(target)
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"rclone lsjson 실패 ({target}): {r.stderr.strip()[:300]}")
    return json.loads(r.stdout or "[]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()

    rclone = find_rclone()

    # 키는 항상 NFC — macOS NFD 파일명과의 정규화 불일치 방지 (좌표 조회의 기준면)
    files: dict[str, str] = {}
    for e in lsjson(rclone, f"{REMOTE}/raw", recursive=True):
        fid = e.get("ID")
        if not fid:
            raise SystemExit("lsjson 에 ID 필드 없음 — drive 백엔드가 맞는지 확인")
        files[unicodedata.normalize("NFC", f"raw/{e['Path']}")] = fid

    root: dict[str, str] = {}
    for e in lsjson(rclone, REMOTE, recursive=False):
        if e.get("ID"):
            root[unicodedata.normalize("NFC", e["Name"])] = e["ID"]

    manifest = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "remote": REMOTE,
        "root": root,           # recent.md / archive.md / manifest.json 등
        "files": files,         # raw/conversations/... → fileId
    }
    # 원자 교체 — 사서 데몬이 2초 폴링 중에 읽는 파일이라, 제자리 덮어쓰기 도중
    # 읽히면 잘린 JSON → {} 폴백 → 그 창의 trail 영수증이 통째로 드랍된다.
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"[manifest] {len(files)}개 raw 파일 + 루트 {len(root)}개 → {OUT}")

    if not args.no_upload:
        r = subprocess.run([rclone, "copyto", str(OUT), f"{REMOTE}/manifest.json",
                            "--log-level", "ERROR"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[manifest] 업로드 실패: {r.stderr.strip()[:200]}")
            return 1
        print(f"[manifest] {REMOTE}/manifest.json 업로드 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
