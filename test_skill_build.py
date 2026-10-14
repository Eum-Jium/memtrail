"""test_skill_build.py — 업로드본 zip 이 옛 판을 지우지 않는지 지킨다.

업로드한 zip 은 "그때 claude.ai 가 무엇을 읽고 있었나" 의 유일한 기록이다. 회상이
이상해졌을 때 되짚을 자리가 그것뿐인데, 처음엔 같은 날짜면 같은 파일명으로 덮어써서
2026-08-19 첫 판이 실제로 사라졌다. 조용히 잃는 종류라 테스트로 고정한다.

실행: python test_skill_build.py   (pytest 불필요, standalone assert)
"""
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from build_skill_zip import body_of, builds_of, next_free  # noqa: E402

NAME = "memtrail"


def _dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="skillbuild_"))


def _zip(path: Path, body: str, name: str = NAME) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/SKILL.md", body.encode("utf-8"))
    return path


def test_same_day_rebuild_keeps_the_old_one():
    """같은 날 두 번 뽑으면 덮어쓰지 않고 -2 로 나란히 남는다."""
    d = _dir()
    first = next_free(d, NAME)
    _zip(first, "판 1")
    second = next_free(d, NAME)
    assert second != first, "같은 경로를 다시 내줬다 — 덮어쓰기다"
    assert second.name.endswith("-2.zip"), second.name
    _zip(second, "판 2")
    assert first.exists() and second.exists()
    assert body_of(first, NAME) == "판 1", "첫 판이 훼손됐다"
    third = next_free(d, NAME)
    assert third.name.endswith("-3.zip"), third.name


def test_newest_is_last_despite_suffix():
    """최신 판단은 mtime 이다 — 파일명 정렬은 `-2` 가 원판보다 앞서 뒤집힌다."""
    d = _dir()
    a = _zip(next_free(d, NAME), "먼저")
    time.sleep(0.01)
    b = _zip(next_free(d, NAME), "나중")
    kept = builds_of(d, NAME)
    assert kept == [a, b], [p.name for p in kept]
    assert builds_of(d, NAME)[-1] == b
    # 파일명 정렬이었다면 뒤집혔을 상황인지 확인 (테스트가 실제로 뭔가를 지키는지)
    assert sorted([a.name, b.name])[0] == b.name, "이 케이스는 더 이상 함정이 아니다"


def test_body_of_reads_and_tolerates_junk():
    """내용 비교는 zip 안의 SKILL.md 로 한다. 깨진 파일은 None (비교에서 자연 탈락)."""
    d = _dir()
    ok = _zip(d / "memtrail-2026-01-01.zip", "본문")
    assert body_of(ok, NAME) == "본문"
    broken = d / "memtrail-2026-01-02.zip"
    broken.write_bytes(b"not a zip at all")
    assert body_of(broken, NAME) is None
    wrong_name = _zip(d / "memtrail-2026-01-03.zip", "본문", name="딴이름")
    assert body_of(wrong_name, NAME) is None


def test_empty_dir_is_not_an_error():
    """첫 빌드 — 보관 폴더가 없어도 죽지 않는다."""
    d = _dir() / "아직없음"
    assert builds_of(d, NAME) == []


if __name__ == "__main__":
    passed, skipped = 0, []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            reason = fn()
            if isinstance(reason, str):
                skipped.append((name, reason))
                print(f"  skip {name} — {reason}")
            else:
                passed += 1
                print(f"  ok   {name}")
    total = passed + len(skipped)
    tail = f" (스킵 {len(skipped)}개 — 실제 검증은 {passed}/{total})" if skipped else ""
    print(f"\n[OK] 업로드본 보관 테스트 통과 {passed}/{total}{tail}")
