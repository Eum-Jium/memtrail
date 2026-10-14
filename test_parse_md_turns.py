"""
memory_pipeline.parse_md_content 턴 파싱 테스트 (비대화형, assert 기반).

실행: python test_parse_md_turns.py
기대: 전부 통과 시 exit 0, 실패 시 AssertionError 로 exit != 0
"""
import sys

import tempfile
from pathlib import Path

from memory_pipeline import decode_cesu8, parse_md_content, parse_md_file

sys.stdout.reconfigure(encoding="utf-8")

HEAD = "# 제목\n\n- 생성일: 2026-07-27\n- ID: 11111111-2222-3333-4444-555555555555\n\n"


def md(*turns: tuple[str, str]) -> str:
    """unpack_claude_code_jsonl 포맷 (구분선 없음)."""
    body = "".join(
        f"## {'👤 User' if r == 'u' else '🤖 Assistant'}\n\n{t}\n\n" for r, t in turns
    )
    return HEAD + body


def parse(content: str):
    return parse_md_content(content, "2026-07-27_제목", "claude", "2026-07-27_제목.md")


print("[1/9] 정상 U/A 쌍은 하나의 청크로 묶인다")
docs = parse(md(("u", "질문1"), ("a", "답변1"), ("u", "질문2"), ("a", "답변2")))
assert len(docs) == 2, len(docs)
assert docs[0].page_content == "[User]\n질문1\n\n[Assistant]\n답변1"
assert docs[0].metadata["user_text"] == "질문1"
assert [d.metadata["turn_index"] for d in docs] == [0, 1]
print("   ✅ OK")

print("[2/9] Assistant 만 있는 파일도 청크가 나온다 (예전엔 0개)")
docs = parse(md(("a", "답변1"), ("a", "답변2"), ("a", "답변3")))
assert len(docs) == 3, len(docs)
assert docs[0].page_content == "[Assistant]\n답변1"
assert docs[0].metadata["user_text"] == ""
print("   ✅ OK")

print("[3/9] 연속 Assistant 턴이 유실되지 않는다")
docs = parse(md(("u", "질문"), ("a", "답변A"), ("a", "답변B")))
assert len(docs) == 2, len(docs)
assert docs[0].page_content == "[User]\n질문\n\n[Assistant]\n답변A"
assert docs[1].page_content == "[Assistant]\n답변B"
print("   ✅ OK")

print("[4/9] 답변 없이 끝난 질문 / 연속 User 턴도 살아남는다")
docs = parse(md(("u", "질문1"), ("u", "질문2")))
assert len(docs) == 2, len(docs)
assert docs[0].page_content == "[User]\n질문1"
assert docs[1].page_content == "[User]\n질문2"
print("   ✅ OK")

print("[5/9] Assistant→User 순서(첨부만 있던 첫 질문 유실)도 둘 다 인덱싱된다")
docs = parse(md(("a", "답변"), ("u", "뒤늦은 질문")))
assert len(docs) == 2, len(docs)
assert docs[0].page_content == "[Assistant]\n답변"
assert docs[1].page_content == "[User]\n뒤늦은 질문"
print("   ✅ OK")

print("[6/9] 헤더 변종(🧑 사용자 / 🤖 Claude) 인식, 본문 소제목은 턴이 아니다")
content = (
    HEAD
    + "## 🧑 사용자\n\n질문\n\n## 🤖 Claude\n\n답변 본문\n\n"
    + "## 그냥 소제목\n\n이어지는 설명\n\n### 🤖 들여쓴 제목\n\n더\n"
)
docs = parse(content)
assert len(docs) == 1, len(docs)
assert docs[0].metadata["user_text"] == "질문"
assert "그냥 소제목" in docs[0].page_content
assert "들여쓴 제목" in docs[0].page_content
print("   ✅ OK")

print("[7/9] convert_claude_api_json 포맷(--- 구분선)도 턴 수가 같다")
content = (
    HEAD
    + "---\n\n## 👤 User\n\n질문1\n\n"
    + "---\n\n## 🤖 Assistant\n\n답변1\n\n"
    + "---\n\n## 🤖 Assistant\n\n답변2\n\n"
)
docs = parse(content)
assert len(docs) == 2, len(docs)
assert docs[0].metadata["user_text"].startswith("질문1")
assert "답변2" in docs[1].page_content
assert docs[1].metadata["user_text"] == ""
print("   ✅ OK")

print("[8/9] CESU-8 로 깨진 이모지를 버리지 않고 복구한다")
cesu = bytes.fromhex("eda0bdedb4b8")  # 🔸 를 서로게이트 쌍 3바이트씩 인코딩한 것
assert decode_cesu8(cesu) == "🔸"
assert decode_cesu8("정상 텍스트 🔹".encode()) == "정상 텍스트 🔹"
assert decode_cesu8(b"\xff\xfe deco" + "딩 실패".encode()) .endswith("딩 실패")  # 폴백
print("   ✅ OK")

print("[9/9] parse_md_file 이 CESU-8 파일도 정상 파싱한다")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "2026-07-27_깨진파일.md"
    body = md(("u", "질문"), ("a", "답변 X 표시"))
    p.write_bytes(body.replace("X", "�").encode().replace("�".encode(), cesu))
    docs = parse_md_file(str(p))
    assert len(docs) == 1, len(docs)
    assert "🔸" in docs[0].page_content, docs[0].page_content
print("   ✅ OK")

print("\n모든 테스트 통과 ✅")
