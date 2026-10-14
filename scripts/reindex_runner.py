"""
reindex_runner.py — reindex 명령의 백그라운드 본체 (사서 데몬이 Popen 으로 띄움)

rebuild_memory_db.rebuild 로 memory_db.rebuild 에 전체 재빌드 후,
가드(새 청크수 >= 기존 * 0.95)를 통과하면 교체한다. 이전 인덱스는
memory_db.old 로 1세대 보관 (롤백용). 실패 시 원본 무손상.

완료/실패는 receipts/r_{cid}_done.json 으로 추가 통지 — 데몬의 즉시
영수증(accepted)과 짝을 이루는 감사 로그다. 종료 시 reindex.lock 해제.

데몬과 같은 이유로 LLM 미사용 (E5 임베딩 + Chroma 만).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(os.environ.get("MEMEX_DATA_DIR", ROOT))
REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")
DB = BASE / "memory_db"
DB_NEW = BASE / "memory_db.rebuild"
DB_OLD = BASE / "memory_db.old"
LOCK = BASE / "logs" / "reindex.lock"
STATE = BASE / "logs" / "index_state.json"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def find_rclone() -> str | None:
    exe = os.environ.get("RCLONE_BIN") or shutil.which("rclone")
    if not exe:
        local = Path.home() / ".local/bin/rclone"
        exe = str(local) if local.exists() else None
    return exe


def send_receipt(cid: str, payload: dict) -> None:
    rc = find_rclone()
    if not rc:
        print("[runner] rclone 없음 — 영수증 생략")
        return
    body = {"command_id": cid,
            "processed_at": datetime.now().isoformat(timespec="seconds"), **payload}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
            tmp = f.name
        subprocess.run([rc, "copyto", tmp,
                        f"{REMOTE}/mailbox/receipts/r_{cid}_done.json",
                        "--log-level", "ERROR"], timeout=60)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def count_chunks(path: Path) -> int:
    import chromadb
    return chromadb.PersistentClient(path=str(path)).get_collection("memory").count()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--command-id", required=True)
    args = ap.parse_args()
    cid = args.command_id
    t0 = time.time()
    try:
        old_count = count_chunks(DB) if DB.exists() else 0
        print(f"[runner] 재빌드 시작 — 기존 {old_count:,} 청크")

        from rebuild_memory_db import rebuild
        rebuild(str(BASE / "conversations"), str(DB_NEW), batch=256, dry_run=False)

        new_count = count_chunks(DB_NEW)
        if new_count < old_count * 0.95:
            raise RuntimeError(f"가드 실패: 새 인덱스 {new_count:,} < 기존 {old_count:,}의 95% — 교체 중단")

        if DB_OLD.exists():
            shutil.rmtree(DB_OLD)
        # 윈도우는 열린 chroma 핸들이 rename 을 막는다 — count 용 클라이언트가
        # 수거되도록 힌트를 주고, 그래도 두 번째 rename 이 실패하면 원위치 롤백
        # (안 하면 memory_db 가 .old 로 사라진 채 성공처럼 보인다).
        import gc
        gc.collect()
        DB.rename(DB_OLD)
        try:
            DB_NEW.rename(DB)
        except OSError:
            DB_OLD.rename(DB)
            raise

        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "index_as_of": datetime.now().isoformat(timespec="minutes"),
            "chunks": new_count, "full_rebuild": True,
        }, ensure_ascii=False), encoding="utf-8")

        mins = (time.time() - t0) / 60
        print(f"[runner] 완료 — {old_count:,} → {new_count:,} 청크, {mins:.0f}분")
        send_receipt(cid, {"type": "reindex", "result": "ok",
                           "detail": f"재빌드 완료: {old_count:,} → {new_count:,} 청크, {mins:.0f}분. "
                                     f"이전 인덱스는 memory_db.old 보관"})
        return 0
    except (Exception, SystemExit) as e:
        # SystemExit 도 잡는다 — rebuild 가 md 0건 등에서 SystemExit 를 던지는데,
        # 놓치면 완료/실패 영수증이 아예 안 나가 클라이언트가 무한 대기한다.
        print(f"[runner] 실패: {e!r}")
        send_receipt(cid, {"type": "reindex", "result": "error",
                           "detail": f"{e!r} — 인덱스는 교체 전 상태로 유지"})
        return 1
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
