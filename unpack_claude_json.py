"""
다운로드한 claude_conversations_*.json → 개별 MD 파일로 풀기
사용법: python unpack_claude_json.py claude_conversations_2026-04-04.json
"""
import json
import sys
from pathlib import Path

def unpack(json_path: str, output_dir: str = "./conversations"):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(json_path, "r", encoding="utf-8") as f:
        files = json.load(f)

    saved, skipped = 0, 0
    for item in files:
        filename = item["filename"]
        content = item["content"]
        filepath = out / filename

        if filepath.exists():
            skipped += 1
            continue

        filepath.write_text(content, encoding="utf-8")
        saved += 1

    print(f"완료! 새로 저장: {saved}, 스킵(이미 존재): {skipped}")
    print(f"저장 위치: {out.resolve()}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python unpack_claude_json.py <json파일경로>")
        sys.exit(1)
    unpack(sys.argv[1])
