"""
restore_from_drive.py — Drive 사본에서 로컬 정본을 되살린다 (재해 복구 전용)

`sync_memory_to_drive.py` 는 "단방향 (로컬 → Drive). Drive → 로컬 경로는 만들지
않는다. 정본은 항상 로컬" 을 원칙으로 둔다. 이 스크립트는 그 원칙의 예외가
아니라 **사람이 부를 때만 도는 1회성 복구 도구**다 — 스케줄러·launchd 에
걸지 말 것. 자동 경로가 생기는 순간 Drive 가 사실상 정본이 되어 원칙이 깨진다.

복원 대상 (Drive → $MEMEX_DATA_DIR):
    canon/                      ← 정본 프로필 (이력·결정·인물·연표)
    threads/                    ← 화두 스레드 (사서가 promote 때 직접 올린 것)
    raw/{conversations, memory, cards, rollups, trail}/ → 같은 이름의 로컬 폴더

복원되지 않는 것 — 의도된 것이므로 실패가 아니다:
    memory_db/    파생물. `--reindex` 로 재빌드한다 (상시 PC 실측 ~17분 / 32k 청크).
                  임베딩 모델이 인덱싱 당시와 같아야 한다 — 다르면 에러 없이
                  유사도만 조용히 무의미해진다 (.env `MEMEX_EMBEDDING_MODEL`).
    logs/         기기 로컬 기록. 복원 대상이 아니다.

주의 — 복원은 "지운 것"까지 되살린다:
    발행은 삭제를 전파하지 않으므로(`sync_memory_to_drive` 의 copy 정책) Drive 에는
    로컬에서 이미 지운 파일이 남아 있다. 2026-08-14 클린 테스트 실측:
      · conversations 1761 → 1772 — 프로젝트 폴더 개명으로 갈라졌던 중복 세션이
        11개 되살아났다. **`scripts/dedupe_sessions.py --apply --cards` 필수** —
        안 돌리면 같은 세션이 두 벌로 인덱싱된다 (돌린 뒤 1761 로 정확히 복귀).
      · cards 389 → 409, dedupe 후 398 — 앞서 걷어냈던 카드 9개가 남는다.
        dedupe 는 중복 세션만 보므로 이 층은 못 잡는다. 카드는 파생물이고 벡터
        인덱스에 들어가지 않아 검색에는 영향이 없다. 실측상 9개 중 6개는 원본
        대화가 살아 있어 오히려 정합적이었고, 진짜 고아는 3개뿐이었다 — 그래서
        일괄 삭제하지 않는다. 눈에 거슬리면 손으로 정리할 것.

안전장치:
- rclone `copy` 만 쓴다 (`sync` 아님) — Drive 에 없는 로컬 파일을 지우지 않는다.
- 대상 폴더에 파일이 이미 있으면 **중단**한다. `--force` 로만 진행 (덮어쓰기 위험).
- `--dry-run` 으로 무엇이 내려올지 먼저 볼 수 있다.

사용법:
    python scripts/restore_from_drive.py --dry-run   # 무엇이 내려올지만 본다
    python scripts/restore_from_drive.py             # 복원 (빈 폴더에만)
    python scripts/restore_from_drive.py --force     # 기존 파일이 있어도 진행
    python scripts/restore_from_drive.py --reindex   # 복원 후 인덱스 재빌드까지

환경변수:
    MEMEX_DATA_DIR       기억 데이터 루트 (기본: 리포 디렉토리)
    MEMEX_DRIVE_REMOTE   rclone 리모트 경로 (기본: gdrive:ClaudeMemory)
    RCLONE_BIN           rclone 실행 파일 (기본: PATH 검색 → Windows winget 폴백)
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_REPO_DIR = Path(__file__).resolve().parent.parent


def _load_env(path: Path):
    """단순 .env 로더 — 셸 export 최우선(setdefault), 값의 따옴표·CR 은 벗긴다."""
    if not path.exists():
        return
    for _line in path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))


# build_memory_cards 와 같은 순서 — MEMEX_DATA_DIR 자체가 리포 .env 에 적히는
# 값이라 BASE_DIR 를 먼저 굳히면 안 된다. 복구 도구는 리포가 갓 클론된 상태에서
# 도는 것이 정상이므로 무거운 모듈을 import 하지 않는다 (langchain 미사용).
_load_env(_REPO_DIR / ".env")
BASE_DIR = Path(os.environ.get("MEMEX_DATA_DIR", _REPO_DIR))
_load_env(BASE_DIR / ".env")

REMOTE = os.environ.get("MEMEX_DRIVE_REMOTE", "gdrive:ClaudeMemory")
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "restore.log"

# sync_memory_to_drive.RAW_DIRS 와 짝을 이룬다 — 그쪽이 올리는 것을 그대로 받는다.
# rollups 는 폐지(2026-08-15)돼 더는 올라가지 않지만, 이미 Drive 에 있는 사본은
# 남겨둔 상태라 복구 때는 같이 받아온다 (사본을 실제로 지우면 이 항목도 뺄 것).
RAW_DIRS = ["conversations", "memory", "cards", "trail", "rollups", "archive"]
# 루트에 두는 낭독 대상 (raw/ 백업이 아니라 fileId 안정이 필요한 층).
ROOT_DIRS = ["canon", "threads"]
# 이 층이 Drive 에 없으면 복구 자체가 무의미하다 — 건너뛰지 않고 중단한다.
CRITICAL_DIRS = {"conversations", "canon"}

WINGET_RCLONE = (Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
                 / "Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe"
                 / "rclone-v1.74.4-windows-amd64/rclone.exe")


def log(msg: str):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_rclone() -> str:
    exe = os.environ.get("RCLONE_BIN") or shutil.which("rclone")
    if not exe and WINGET_RCLONE.exists():
        exe = str(WINGET_RCLONE)
    if not exe:
        raise FileNotFoundError("rclone 을 찾지 못함 (RCLONE_BIN 설정 필요)")
    return exe


def run_rclone(rclone: str, args: list, level: str = "ERROR") -> tuple[int, str]:
    r = subprocess.run([rclone] + args + ["--log-level", level],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        log(f"rclone 실패 rc={r.returncode}: {' '.join(args[:3])} :: {out[:400]}")
    return r.returncode, out


def local_file_count(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for p in d.rglob("*") if p.is_file())


def plan() -> list:
    """(Drive 경로, 로컬 경로) 목록. 복원 순서는 가벼운 것부터."""
    pairs = [(f"{REMOTE}/{d}", BASE_DIR / d) for d in ROOT_DIRS]
    pairs += [(f"{REMOTE}/raw/{d}", BASE_DIR / d) for d in RAW_DIRS]
    return pairs


def print_commands() -> None:
    """이 스크립트와 **동등한 rclone 명령**을 그대로 찍는다.

    복구가 필요한 상황은 대개 Python·리포·venv 가 다 없는 상황이다. 그런데
    이 스크립트를 돌리려면 그게 다 있어야 한다 — 앞뒤가 안 맞는다. 그래서
    맨손 복구 경로를 따로 구현하지 않고(두 벌은 반드시 어긋난다) 여기서
    **출력**한다. 이 출력이 docs/drive_sync.md 의 맨손 절차와 같은 출처다.

    출력된 명령만 있으면 rclone 하나로 복구가 끝난다. 단, 이 스크립트가
    해 주던 것 세 가지는 사람이 챙겨야 한다:
      · 대상 폴더가 비어 있는지 확인 (안 그러면 덮어쓴다)
      · 복구 후 dedupe_sessions.py — Drive 엔 지운 중복이 남아 있다
      · memory_db 재빌드 (rclone 으로는 못 받는다 — 애초에 안 올린다)
    """
    print(f"# memtrail 맨손 복구 — rclone 만 있으면 된다 (리포·Python 불필요)")
    print(f"# 리모트: {REMOTE}   대상: $MEMEX_DATA_DIR")
    print()
    print('DST="${MEMEX_DATA_DIR:?데이터 폴더 경로를 지정할 것}"')
    print()
    for d in ROOT_DIRS:
        print(f'rclone copy "{REMOTE}/{d}" "$DST/{d}"')
    for d in RAW_DIRS:
        print(f'rclone copy "{REMOTE}/raw/{d}" "$DST/{d}"')
    print()
    print("# 받은 뒤 반드시:")
    print("#   1) 중복 정리 — Drive 에는 로컬에서 지운 세션이 남아 있어 되살아난다")
    print("#      python scripts/dedupe_sessions.py --apply --cards")
    print("#   2) 인덱스 재빌드 — memory_db 는 파생물이라 Drive 에 없다")
    print("#      python scripts/rebuild_memory_db.py --md \"$DST/conversations\" --out \"$DST/memory_db\"")
    print("#   3) 옛 격리 폴더는 2026-08-15 에 없어졌고, logs/ 는 기기 로컬 기록이라 복구 대상이 아니다")


def preflight(rclone: str) -> bool:
    """Drive 쪽에 기대한 폴더가 실제로 있는지 먼저 확인 — 절반 복원을 막는다."""
    rc, out = run_rclone(rclone, ["lsd", REMOTE])
    if rc != 0:
        log(f"중단: Drive 리모트에 접근 못 함 ({REMOTE}). rclone config 확인 필요")
        return False
    top = {ln.split()[-1] for ln in out.splitlines() if ln.strip()}
    missing = [d for d in ROOT_DIRS if d not in top]
    if "raw" not in top:
        missing.append("raw")
    if missing:
        log(f"중단: Drive 에 없는 폴더 {missing} — 발행이 한 번도 안 돌았을 수 있다")
        return False
    return True


def guard_nonempty(force: bool) -> bool:
    """대상 폴더에 이미 파일이 있으면 덮어쓰기 위험 — 기본은 중단."""
    occupied = [(d.name, n) for _, d in plan() if (n := local_file_count(d))]
    if not occupied:
        return True
    desc = ", ".join(f"{name}({n})" for name, n in occupied)
    if force:
        log(f"경고: 기존 파일 위에 덮어쓴다 (--force) — {desc}")
        return True
    log(f"중단: 대상 폴더가 비어 있지 않다 — {desc}")
    log("       빈 데이터 폴더에 복원하거나, 덮어쓸 각오가 되면 --force 를 줄 것")
    return False


def restore(rclone: str, dry_run: bool) -> bool:
    ok = True
    for src, dst in plan():
        args = ["copy", src, str(dst)]
        if dry_run:
            args.append("--dry-run")
        rc, out = run_rclone(rclone, args, level="NOTICE" if dry_run else "ERROR")
        # Drive 쪽 폴더가 아예 없는 경우 — 그 층을 한 번도 발행하지 않은 구성이다
        # (예: trail/ 은 2026-08-14 에야 발행 대상이 됐다). 정본이 아닌 층이면
        # 복구를 통째로 실패시키지 않고 건너뛴다.
        if rc != 0 and "directory not found" in out:
            if dst.name in CRITICAL_DIRS:
                log(f"중단: Drive 에 {src} 가 없다 — 정본 층이라 복구를 계속할 수 없다")
                return False
            log(f"건너뜀: Drive 에 {src} 없음 (아직 발행된 적 없는 층)")
            continue
        ok &= rc == 0
        if dry_run:
            # rclone --dry-run 은 파일마다 "Skipped copy as --dry-run is set" 을 남긴다.
            # 이 마커로만 센다 — 에러 줄에도 'copy' 가 들어 있어 느슨히 세면 거짓말이 된다.
            n = sum(1 for ln in out.splitlines() if "Skipped copy as --dry-run" in ln)
            log(f"[dry-run] {src} → {dst.name}/ : {n}건 예정")
        else:
            log(f"{src} → {dst.name}/ : 로컬 {local_file_count(dst)}개")
    return ok


def reindex() -> bool:
    """전체 인덱스 재빌드. 기존 memory_db 가 있으면 손대지 않는다 —
    교체 가드(새 청크 >= 기존 95%)와 .old 보관은 사서의 reindex 명령
    (reindex_runner.py) 소관이라 여기서 흉내내지 않는다."""
    db = BASE_DIR / "memory_db"
    if db.exists():
        log(f"재빌드 생략: {db} 가 이미 있다 — 교체는 우편함 reindex 명령으로 (가드·롤백 포함)")
        return True
    conv = BASE_DIR / "conversations"
    if not conv.is_dir() or not local_file_count(conv):
        log("재빌드 생략: conversations/ 가 비었다")
        return False
    model = os.environ.get("MEMEX_EMBEDDING_MODEL", "(기본값)")
    log(f"인덱스 재빌드 시작 — 임베딩 모델 {model}")
    log("  (인덱싱 당시와 다른 모델이면 에러 없이 유사도만 무의미해진다. 지금 멈출 것)")
    sys.path.insert(0, str(_REPO_DIR / "scripts"))
    import rebuild_memory_db  # noqa: E402  (무거운 import 는 이 시점까지 미룬다)
    rc = rebuild_memory_db.rebuild(str(conv), str(db), batch=256, dry_run=False)
    log(f"인덱스 재빌드 종료 rc={rc}")
    if rc == 0:
        stamp_index_state(db)
    return rc == 0


def stamp_index_state(db: Path) -> None:
    """logs/index_state.json 을 전진시킨다.

    rebuild_memory_db 는 인덱스만 만들고 이 스탬프를 안 쓴다 (쓰는 쪽은
    index_incremental.py 와 reindex_runner.py 뿐이다). 안 올리면 사서 데몬이
    컬렉션·BM25 캐시를 비우지 않아 **옛 인덱스를 계속 서빙하면서 answer 에는
    새 값을 실어** 겉으로만 정상으로 보인다 (mailbox_daemon.py 의 stale 사고
    주석 참조). 2026-08-14 클린 테스트에서 실제로 스탬프가 재빌드 전 값에
    머무는 것을 확인해 여기서 채운다."""
    import json
    state = BASE_DIR / "logs" / "index_state.json"
    try:
        import chromadb
        n = chromadb.PersistentClient(path=str(db)).get_collection("memory").count()
    except Exception as e:  # 스탬프 실패가 복구 자체를 실패시키지는 않는다
        log(f"경고: 청크 수를 못 읽어 스탬프 생략 ({e}) — index_incremental.py 를 한 번 돌릴 것")
        return
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "index_as_of": datetime.now().isoformat(timespec="minutes"),
        "chunks": n,
    }, ensure_ascii=False), encoding="utf-8")
    log(f"index_state.json 전진 — 청크 {n:,}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive 사본 → 로컬 정본 복구 (1회성)")
    ap.add_argument("--dry-run", action="store_true", help="무엇이 내려올지만 본다")
    ap.add_argument("--force", action="store_true", help="대상 폴더가 비어 있지 않아도 진행")
    ap.add_argument("--reindex", action="store_true", help="복원 후 memory_db 재빌드")
    ap.add_argument("--print-commands", action="store_true",
                    help="동등한 rclone 명령만 출력하고 끝낸다 (맨손 복구용 — 아무것도 받지 않는다)")
    a = ap.parse_args()

    if a.print_commands:
        print_commands()
        return 0

    log(f"=== 복구 시작 (remote={REMOTE}, base={BASE_DIR}{', dry-run' if a.dry_run else ''})")
    rclone = find_rclone()
    if not preflight(rclone):
        return 1
    if not a.dry_run and not guard_nonempty(a.force):
        return 1
    ok = restore(rclone, a.dry_run)
    if a.dry_run:
        log("=== dry-run 종료 — 실제로 받으려면 --dry-run 을 빼고 다시")
        return 0 if ok else 1
    if a.reindex:
        ok &= reindex()
    else:
        log("memory_db 는 복원 대상이 아니다 — 재빌드하려면 --reindex")
    log("다음 할 일 ①: scripts/dedupe_sessions.py --apply --cards — Drive 에는 로컬에서")
    log("              지운 중복 세션이 남아 있어 그대로 두면 두 벌로 인덱싱된다")
    log("다음 할 일 ②: 그 다음에 인덱스 재빌드 (--reindex 또는 rebuild_memory_db.py).")
    log("              순서를 바꾸면 중복까지 인덱싱한 뒤 다시 걷어내야 한다")
    log(f"=== 복구 {'완료' if ok else '실패'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
