"""
대화 분석 파이프라인 - "AI와 대화하는 나"를 그리는 프로젝트

사용법:
    python analyze_pipeline.py                      # 전체 실행 (Phase 1~4)
    python analyze_pipeline.py --phase 1            # Phase 1만 (월별 추출)
    python analyze_pipeline.py --phase 2            # Phase 2만 (프로필 합성)
    python analyze_pipeline.py --phase 3            # Phase 3만 (타임라인)
    python analyze_pipeline.py --phase 4            # Phase 4만 (서사 문서)
    python analyze_pipeline.py --phase 1 --month 2025-06  # 특정 월만 재처리

환경변수:
    ANTHROPIC_API_KEY=sk-ant-...
"""

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import anthropic

# Windows cp949 터미널에서 한글/이모지 출력 문제 해결
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
CONVERSATIONS_DIR = BASE_DIR / "conversations"
CHECKPOINT_DIR = BASE_DIR / "analysis_checkpoints"
MEMORY_DIR = BASE_DIR / "memory"

CHECKPOINT_DIR.mkdir(exist_ok=True)
MEMORY_DIR.mkdir(exist_ok=True)

MODEL = "claude-sonnet-4-6"

client = anthropic.Anthropic()


# ─────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────

def normalize_month(filename: str) -> str:
    """파일명에서 월(YYYY-MM) 추출 - YYYYMMDD, YYYY-MM-DD 양쪽 지원"""
    stem = Path(filename).stem
    # YYYY-MM-DD 형식
    m = re.match(r"(\d{4}-\d{2})-\d{2}", stem)
    if m:
        return m.group(1)
    # YYYYMMDD 형식
    m = re.match(r"(\d{4})(\d{2})\d{2}", stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return "unknown"


def read_file_safe(path: Path) -> str:
    for enc in ["utf-8", "cp949", "euc-kr"]:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_user_turns(files: list[Path]) -> str:
    """여러 파일에서 User 턴만 추출해 하나의 문자열로 합침"""
    USER_HDR = re.compile(r"##\s+(?:👤|🧑)[^\n]*\n")
    NEXT_HDR = re.compile(r"##\s+(?:👤|🧑|🤖)[^\n]*\n")

    parts = []
    for f in files:
        content = read_file_safe(f)
        # 파일명에서 날짜/주제
        stem = f.stem
        date_match = re.match(r"(\d{4}[-]?\d{2}[-]?\d{2})[_\- ]*(.*)", stem)
        topic = date_match.group(2) if date_match else stem

        turns = []
        for m in USER_HDR.finditer(content):
            start = m.end()
            next_m = NEXT_HDR.search(content, start)
            end = next_m.start() if next_m else len(content)
            turn = content[start:end].strip()
            if len(turn) >= 15:
                turns.append(turn[:500])

        if turns:
            parts.append(f"[주제: {topic}]")
            parts.extend(turns)

    return "\n\n".join(parts)


def group_files_by_month() -> dict[str, list[Path]]:
    """conversations/ 의 모든 MD 파일을 월별로 그룹핑"""
    groups: dict[str, list[Path]] = {}
    for f in sorted(CONVERSATIONS_DIR.glob("*.md")):
        month = normalize_month(f.name)
        if month == "unknown":
            continue
        groups.setdefault(month, []).append(f)
    return dict(sorted(groups.items()))


def call_api(prompt: str, max_tokens: int = 2000) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ─────────────────────────────────────────
# 프롬프트
# ─────────────────────────────────────────

EXTRACT_PROMPT = """다음은 {month}에 한 사람이 AI와 나눈 대화에서 그 사람의 발화만 모은 것입니다.
총 {file_count}개 대화에서 추출했습니다.

아래 5개 항목에서 **대화에서 실제로 확인된 구체적인 사실**만 추출하세요.
추측하지 말고, 언급되지 않은 것은 쓰지 마세요.

[직업/전문성]
- 실제 언급된 업무, 기관명, 프로젝트명, 직급, 기술 스택

[성격/소통 스타일]
- 질문 방식의 특징 (짧게/길게, 맥락 설명 방식)
- 특유의 말투나 표현 패턴
- 반복되는 행동 패턴

[관심사 및 활동]
- 이 달에 등장한 주제들 (업무/취미/학습 구분)
- 반복해서 물어본 주제
- 새롭게 탐구한 분야

[감정/가치관]
- 감정을 드러낸 순간 (기쁨, 걱정, 스트레스, 성취감 등)
- 중요하게 여기는 것
- 주변 상황 (가족, 동료 등 언급)

[AI 활용 방식]
- 주로 어떤 용도로 사용했는가
- 특유의 요청 패턴
- 어떻게 피드백을 주는가

마지막에 반드시:
[이달의 특징] 이 달을 한 줄로 요약 (50자 이내)

---
{user_turns}"""


PROFILE_SYNTHESIS_PROMPTS = {
    "identity": """다음은 전체 기간의 월별 분석에서 추출한 [직업/전문성] 관련 정보입니다.
중복을 정리하고, 시간 흐름에 따른 변화를 반영해 아래 형식으로 프로필을 작성하세요.
구체적인 사실 위주로, 확인된 내용만 쓰세요.

# 직업 및 전문 정체성

## 직업 및 소속
(직업, 소속 기관, 직급, 담당 역할)

## 전문 분야
(전문 지식, 기술 스택, 주요 도메인)

## 주요 프로젝트 및 업무
(실제 언급된 프로젝트명, 업무 내용)

## 성장 흐름
(시간에 따라 어떻게 역할/전문성이 변화했는가)

---
{facts}""",

    "personality": """다음은 전체 기간의 월별 분석에서 추출한 [성격/소통 스타일] 관련 정보입니다.
반복적으로 나타나는 패턴을 중심으로 아래 형식으로 정리하세요.

# 성격 및 소통 스타일

## 성격 특성
(반복적으로 드러나는 성격, 태도)

## 질문 패턴 및 소통 방식
(어떻게 질문하는가? 짧게/길게, 맥락 설명 방식 등)

## 사고 흐름 및 의사결정 방식
(어떤 논리로 생각을 전개하는가)

## 특유의 말투와 표현
(반복적인 표현, 특유의 어투)

---
{facts}""",

    "interests": """다음은 전체 기간의 월별 분석에서 추출한 [관심사 및 활동] 관련 정보입니다.
시간 흐름에 따른 관심사 변화를 포함해 아래 형식으로 정리하세요.

# 관심사 및 학습 분야

## 핵심 관심사
(지속적으로 등장하는 주요 관심 분야)

## 취미 및 여가
(취미, 엔터테인먼트, 생활 관련 주제)

## 학습 및 탐구
(새롭게 배우거나 깊이 파고든 분야)

## 관심사 변화 흐름
(시간이 지나면서 어떤 주제가 늘고 줄었는가)

---
{facts}""",

    "emotional": """다음은 전체 기간의 월별 분석에서 추출한 [감정/가치관] 관련 정보입니다.
아래 형식으로 정리하세요.

# 감정 패턴 및 가치관

## 감정 표현 방식
(어떤 상황에서 어떻게 감정을 드러내는가)

## 핵심 가치관
(삶에서 중요하게 여기는 것)

## 삶의 맥락
(가족, 직장, 취미 등 배경)

## 스트레스와 기쁨의 패턴
(무엇이 힘들고 무엇이 즐거운가)

---
{facts}""",

    "ai_usage": """다음은 전체 기간의 월별 분석에서 추출한 [AI 활용 방식] 관련 정보입니다.
활용 방식의 변화(단순 검색 → 협업 도구 등)를 포함해 아래 형식으로 정리하세요.

# AI 활용 방식

## 주요 활용 목적
(AI를 어떤 용도로 주로 쓰는가)

## 선호하는 요청 스타일
(어떻게 질문하는가, 어떤 답변 형식을 원하는가)

## 자주 반복되는 패턴
(특정 작업 유형, 반복 질문 주제)

## AI와의 관계 변화
(초기 vs 현재 - 어떻게 활용 방식이 진화했는가)

---
{facts}""",
}


TIMELINE_PROMPT = """다음은 전체 기간의 월별 대화 분석 결과 요약입니다.
이를 바탕으로 월별 관심사와 삶의 변화를 보여주는 타임라인을 작성하세요.

형식:
# 월별 관심사 변화 타임라인

각 월마다:
## YYYY-MM
- **핵심 주제**: (이 달의 주요 관심사 2~3개)
- **특이사항**: (새로운 변화, 중요한 사건)
- **한 줄 요약**: (이 달을 대표하는 한 문장)

---
{monthly_summaries}"""


NARRATIVE_PROMPT = """당신은 한 사람의 여러 해에 걸친 AI 대화 기록을 분석한 전기작가입니다.
아래 프로필과 타임라인을 바탕으로, "AI와 함께 살아온 한 사람의 이야기"를 써주세요.

이 글은:
- 3~5페이지 분량의 서사적 에세이
- 시간 흐름을 따라가되, 주제별로도 엮어냄
- 구체적인 사실에 기반하되, 그 이면의 맥락을 해석
- 일과 일상 등 여러 역할에서의 다면적 모습
- AI와의 관계가 어떻게 변화했는가 (도구 → 동료 → ?)

아래 4개 섹션으로 구성하세요:

## 1. 처음 만남 — AI를 발견하다
(초기: 어떻게 AI를 쓰기 시작했는가)

## 2. 본업과 열정 — 전문 분야에서 AI를 만났을 때
(전문 분야에서 AI를 어떻게 활용하고 성장했는가)

## 3. 삶의 결 — 직업 너머의 이 사람
(가정, 취미, 감정 — 인간으로서의 면모)

## 4. 지금 이 사람 — AI와 나 사이
(현재 AI와의 관계, 앞으로의 방향)

---
[프로필]
{profiles}

[타임라인]
{timeline}"""


# ─────────────────────────────────────────
# Phase 1: 월별 추출
# ─────────────────────────────────────────

def run_phase1(target_month: str = None):
    groups = group_files_by_month()

    if target_month:
        if target_month not in groups:
            print(f"❌ '{target_month}' 월 데이터가 없습니다.")
            print(f"   사용 가능한 월: {', '.join(groups.keys())}")
            return
        groups = {target_month: groups[target_month]}

    total = len(groups)
    print(f"\n[Phase 1] 월별 추출 시작 - 총 {total}개 월")
    print("=" * 50)

    for i, (month, files) in enumerate(groups.items(), 1):
        checkpoint = CHECKPOINT_DIR / f"{month}.json"

        if checkpoint.exists():
            print(f"  [{i:2d}/{total}] ✅ {month} ({len(files)}개) — 이미 완료, 스킵")
            continue

        print(f"  [{i:2d}/{total}] 🔄 {month} ({len(files)}개 파일) 처리 중...", end=" ", flush=True)
        t0 = time.time()

        user_turns = extract_user_turns(files)
        # 토큰 제한을 고려해 8000자로 자름 (User 턴만이라 밀도 높음)
        if len(user_turns) > 8000:
            user_turns = user_turns[:8000] + "\n...(이하 생략)"

        prompt = EXTRACT_PROMPT.format(
            month=month,
            file_count=len(files),
            user_turns=user_turns,
        )

        try:
            result_text = call_api(prompt, max_tokens=1500)
        except Exception as e:
            print(f"❌ API 오류: {e}")
            continue

        # 구조화 파싱
        def extract_section(text, header):
            m = re.search(rf"\[{re.escape(header)}\](.*?)(?=\[[\w/가-힣 ]+\]|\Z)", text, re.DOTALL)
            return m.group(1).strip() if m else ""

        result = {
            "month": month,
            "file_count": len(files),
            "identity_facts": extract_section(result_text, "직업/전문성"),
            "personality_facts": extract_section(result_text, "성격/소통 스타일"),
            "interests_facts": extract_section(result_text, "관심사 및 활동"),
            "emotional_facts": extract_section(result_text, "감정/가치관"),
            "ai_usage_facts": extract_section(result_text, "AI 활용 방식"),
            "monthly_summary": extract_section(result_text, "이달의 특징"),
            "raw": result_text,
        }

        checkpoint.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        elapsed = time.time() - t0
        summary = result["monthly_summary"][:40] if result["monthly_summary"] else "(요약 없음)"
        print(f"완료 ({elapsed:.1f}s) — {summary}")

    print("\n✅ Phase 1 완료")


# ─────────────────────────────────────────
# Phase 2: 프로필 합성
# ─────────────────────────────────────────

def run_phase2():
    checkpoints = sorted(CHECKPOINT_DIR.glob("*.json"))
    if not checkpoints:
        print("❌ Phase 1 체크포인트가 없습니다. Phase 1을 먼저 실행하세요.")
        return

    print(f"\n[Phase 2] 프로필 합성 — {len(checkpoints)}개 월 데이터 사용")
    print("=" * 50)

    # 모든 월 데이터 로드
    all_data = []
    for cp in checkpoints:
        try:
            all_data.append(json.loads(cp.read_text(encoding="utf-8")))
        except Exception:
            continue

    # 카테고리별로 facts 취합
    fact_keys = {
        "identity": "identity_facts",
        "personality": "personality_facts",
        "interests": "interests_facts",
        "emotional": "emotional_facts",
        "ai_usage": "ai_usage_facts",
    }

    profile_files = {
        "identity": "profile_01_identity.md",
        "personality": "profile_02_personality.md",
        "interests": "profile_03_interests.md",
        "emotional": "profile_04_emotional.md",
        "ai_usage": "profile_05_ai_usage.md",
    }

    for i, (category, fact_key) in enumerate(fact_keys.items(), 1):
        print(f"  [{i}/5] {category} 프로필 합성 중...", end=" ", flush=True)
        t0 = time.time()

        # 월별 facts 합치기 (날짜 레이블 포함)
        facts_parts = []
        for d in all_data:
            fact = d.get(fact_key, "").strip()
            if fact:
                facts_parts.append(f"[{d['month']}]\n{fact}")

        combined_facts = "\n\n".join(facts_parts)
        # 너무 길면 자름
        if len(combined_facts) > 12000:
            combined_facts = combined_facts[:12000] + "\n...(이하 생략)"

        prompt = PROFILE_SYNTHESIS_PROMPTS[category].format(facts=combined_facts)

        try:
            profile_text = call_api(prompt, max_tokens=2000)
        except Exception as e:
            print(f"❌ API 오류: {e}")
            continue

        output_path = MEMORY_DIR / profile_files[category]
        output_path.write_text(profile_text, encoding="utf-8")

        elapsed = time.time() - t0
        print(f"완료 ({elapsed:.1f}s) → {output_path.name}")

    print("\n✅ Phase 2 완료")


# ─────────────────────────────────────────
# Phase 3: 타임라인 생성
# ─────────────────────────────────────────

def run_phase3():
    checkpoints = sorted(CHECKPOINT_DIR.glob("*.json"))
    if not checkpoints:
        print("❌ Phase 1 체크포인트가 없습니다.")
        return

    print(f"\n[Phase 3] 타임라인 생성")
    print("=" * 50)

    summaries = []
    for cp in checkpoints:
        try:
            d = json.loads(cp.read_text(encoding="utf-8"))
            month = d.get("month", cp.stem)
            summary = d.get("monthly_summary", "").strip()
            interests = d.get("interests_facts", "").strip()[:200]
            file_count = d.get("file_count", 0)

            entry = f"[{month}] ({file_count}개 대화)\n요약: {summary}\n관심사: {interests}"
            summaries.append(entry)
        except Exception:
            continue

    monthly_summaries = "\n\n".join(summaries)
    if len(monthly_summaries) > 10000:
        monthly_summaries = monthly_summaries[:10000] + "\n...(이하 생략)"

    print(f"  {len(summaries)}개 월 요약 → 타임라인 생성 중...", end=" ", flush=True)
    t0 = time.time()

    try:
        timeline_text = call_api(
            TIMELINE_PROMPT.format(monthly_summaries=monthly_summaries),
            max_tokens=3000,
        )
    except Exception as e:
        print(f"❌ API 오류: {e}")
        return

    output_path = MEMORY_DIR / "timeline.md"
    output_path.write_text(timeline_text, encoding="utf-8")

    elapsed = time.time() - t0
    print(f"완료 ({elapsed:.1f}s) → timeline.md")
    print("\n✅ Phase 3 완료")


# ─────────────────────────────────────────
# Phase 4: 서사 문서 생성
# ─────────────────────────────────────────

def run_phase4():
    profile_files = [
        MEMORY_DIR / "profile_01_identity.md",
        MEMORY_DIR / "profile_02_personality.md",
        MEMORY_DIR / "profile_03_interests.md",
        MEMORY_DIR / "profile_04_emotional.md",
        MEMORY_DIR / "profile_05_ai_usage.md",
    ]
    timeline_file = MEMORY_DIR / "timeline.md"

    missing = [f for f in profile_files + [timeline_file] if not f.exists()]
    if missing:
        print(f"❌ 필요한 파일이 없습니다: {[f.name for f in missing]}")
        print("   Phase 2, 3을 먼저 실행하세요.")
        return

    print(f"\n[Phase 4] 서사 문서 생성")
    print("=" * 50)

    profiles_text = ""
    for f in profile_files:
        profiles_text += f"\n### {f.stem}\n{f.read_text(encoding='utf-8')[:1000]}\n"

    timeline_text = timeline_file.read_text(encoding="utf-8")[:3000]

    print("  서사 문서 생성 중...", end=" ", flush=True)
    t0 = time.time()

    try:
        narrative_text = call_api(
            NARRATIVE_PROMPT.format(
                profiles=profiles_text,
                timeline=timeline_text,
            ),
            max_tokens=4000,
        )
    except Exception as e:
        print(f"❌ API 오류: {e}")
        return

    output_path = MEMORY_DIR / "narrative.md"
    output_path.write_text(narrative_text, encoding="utf-8")

    elapsed = time.time() - t0
    print(f"완료 ({elapsed:.1f}s) → narrative.md")
    print("\n✅ Phase 4 완료")


# ─────────────────────────────────────────
# 진행 상황 체크
# ─────────────────────────────────────────

def show_status():
    groups = group_files_by_month()
    checkpoints = {cp.stem for cp in CHECKPOINT_DIR.glob("*.json")}

    print("\n[현재 상태]")
    print("=" * 50)
    print(f"총 월 수: {len(groups)}")
    print(f"Phase 1 완료: {len(checkpoints)}/{len(groups)}")

    incomplete = [m for m in groups if m not in checkpoints]
    if incomplete:
        print(f"미완료 월: {', '.join(incomplete)}")

    for fname in ["profile_01_identity.md", "profile_02_personality.md",
                  "profile_03_interests.md", "profile_04_emotional.md",
                  "profile_05_ai_usage.md", "timeline.md", "narrative.md"]:
        path = MEMORY_DIR / fname
        status = f"✅ ({path.stat().st_size:,}bytes)" if path.exists() else "❌ 미생성"
        print(f"  {fname}: {status}")


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="대화 분석 파이프라인")
    parser.add_argument("--phase", choices=["1", "2", "3", "4", "all"], default="all")
    parser.add_argument("--month", type=str, help="Phase 1에서 특정 월만 처리 (예: 2025-06)")
    parser.add_argument("--status", action="store_true", help="현재 진행 상태 확인")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.phase in ("1", "all"):
        run_phase1(target_month=args.month)

    if args.phase in ("2", "all"):
        run_phase2()

    if args.phase in ("3", "all"):
        run_phase3()

    if args.phase in ("4", "all"):
        run_phase4()

    if args.phase == "all":
        print("\n" + "=" * 50)
        print("🎉 전체 파이프라인 완료!")
        print(f"결과 파일: {MEMORY_DIR}/")
        show_status()


if __name__ == "__main__":
    main()
