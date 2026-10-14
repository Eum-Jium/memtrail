# 원본: https://github.com/nir3y/Memory-Layer (Apache-2.0) — 이 리포에서 수정했다.
"""
5개 카테고리별 profile MD 파일 생성기

conversations/*.md 에서 User 턴을 샘플링
→ **구독 claude CLI(`claude -p`)** 로 배치 처리
→ 5개 카테고리별 profile MD 파일 합성

생성되는 파일:
  memory/profile_01_identity.md      - 직업, 소속, 전문분야, 진행 프로젝트
  memory/profile_02_personality.md   - 성격, 소통 스타일, 사고방식
  memory/profile_03_interests.md     - 관심사, 취미, 공부 분야
  memory/profile_04_emotional.md     - 감정 패턴, 가치관, 생활 패턴
  memory/profile_05_ai_usage.md      - AI 활용 방식, 질문 패턴, 선호 답변 스타일

엔진 교체 (2026-08-15): 원래 Ollama(qwen2.5)로 돌렸는데, 상시 PC(맥미니)에
ollama 가 없어 daily.sh 의 `command -v ollama` 게이트가 매달 1일 조용히 거짓이
됐다. 실패 로그조차 안 남아 2026-06-22 이후 두 달간 프로필이 멎은 것을 아무도
몰랐다. 카드 생성이 이미 쓰고 있는 구독 CLI 로 옮겨 의존을 하나 없앤다.
"""

import os
import re
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
# 카드 생성이 쓰는 것과 **같은** 호출부를 재사용한다 — 타임아웃 연속 감지,
# 미로그인 감지, 실패 시 _CLAUDE_DOWN 전파가 이미 다 들어 있다.
# _CLAUDE_DOWN 은 모듈을 통해서만 읽는다 (_cards._CLAUDE_DOWN) — 값으로 import 하면
# 임포트 시점의 False 가 박제돼 이후 갱신을 못 본다.
from build_memory_cards import call_claude  # noqa: E402
import build_memory_cards as _cards  # noqa: E402


# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
CONVERSATIONS_FOLDER = "./conversations"
OUTPUT_DIR = "./memory"
SAMPLE_SIZE = 3000   # 전체 턴 pool에서 3000턴 샘플링
# 배치당 턴 수. Ollama 시절엔 16384 컨텍스트에 맞춰 30이었는데(=프로필당 100배치,
# 5개면 500회 호출), CLI 는 컨텍스트가 훨씬 넓어 크게 묶는 편이 낫다. 턴 하나가
# 600자 상한이므로 250턴 ≈ 150,000자 — 프로필당 12배치, 전체 60회로 줄어든다.
BATCH_SIZE = 250
FACTS_MAX_CHARS = 10000  # 합성 단계에 넘길 facts 최대 길이

PROFILE_CONFIGS = [
    {
        "filename": "profile_01_identity.md",
        "title": "직업 및 전문 정체성",
        "focus": "직업, 소속 기관, 직급, 전문 분야, 기술 스택, 현재 진행 중인 프로젝트",
        "extract_prompt_section": "[A. 직업/소속] 직업, 소속 기관, 직급, 역할\n[B. 전문분야] 전문 기술, 사용 도구, 주요 도메인\n[C. 프로젝트] 현재 진행 중인 업무/개인 프로젝트",
        "synthesis_template": """# 직업 및 전문 정체성

## 직업 및 소속
(직업, 소속 기관, 직급, 담당 역할 등)

## 전문 분야
(전문 지식, 기술 스택, 주요 도메인 등)

## 현재 진행 중인 프로젝트
(업무/개인 프로젝트, 최근 집중하는 과제 등)

## 자주 언급되는 기술/도구 키워드
(반복적으로 나오는 기술명, 플랫폼, 소프트웨어 등)"""
    },
    {
        "filename": "profile_02_personality.md",
        "title": "성격 및 소통 스타일",
        "focus": "성격 특성, 소통 방식, 사고 흐름, 의사결정 패턴",
        "extract_prompt_section": "[A. 성격] 성격 특성, 가치관에서 드러나는 특성\n[B. 소통방식] 질문을 어떻게 던지는가? 짧게/길게, 배경 설명 후 질문, 단도직입 등\n[C. 사고흐름] 어떤 주제에서 어떤 주제로 생각이 이어지는가?",
        "synthesis_template": """# 성격 및 소통 스타일

## 성격 특성
(반복적으로 드러나는 성격, 태도, 행동 패턴)

## 질문 패턴 및 소통 방식
(어떻게 질문하는가? 짧게/길게, 맥락 설명 방식, 확인 습관 등)

## 사고 흐름 및 의사결정 방식
(어떤 논리로 생각을 전개하는가? 무엇을 중요하게 여기는가?)

## 대화 스타일 특징
(특유의 말투, 반복적인 표현 방식, 확인/검토 습관 등)"""
    },
    {
        "filename": "profile_03_interests.md",
        "title": "관심사 및 학습 분야",
        "focus": "관심사, 취미, 자기계발, 공부 주제, 최근 탐구하는 분야",
        "extract_prompt_section": "[A. 관심사] 반복적으로 등장하는 관심 주제, 탐구 분야\n[B. 취미/여가] 취미, 여가 활동, 자기계발 방식\n[C. 학습] 최근 공부하거나 배우고자 하는 것",
        "synthesis_template": """# 관심사 및 학습 분야

## 주요 관심사
(반복적으로 탐구하는 주제, 지적 관심 분야)

## 취미 및 여가 활동
(취미, 여가 활동, 자기계발 방식)

## 최근 공부/탐구 분야
(새롭게 배우거나 깊이 파고드는 주제)

## 자주 언급되는 관심 키워드
(인물, 개념, 장소, 문화 등 반복 등장하는 키워드)"""
    },
    {
        "filename": "profile_04_emotional.md",
        "title": "감정 패턴 및 가치관",
        "focus": "감정 표현 방식, 스트레스 반응, 가치관, 생활 패턴, 인간관계",
        "extract_prompt_section": "[A. 감정패턴] 감정을 어떻게 표현하는가? 긍정적/부정적 감정 표현 방식\n[B. 가치관] 무엇을 중요하게 여기는가? 삶의 우선순위\n[C. 생활패턴] 일상 루틴, 생활 습관, 인간관계 언급",
        "synthesis_template": """# 감정 패턴 및 가치관

## 감정 표현 방식
(기쁨, 스트레스, 불안, 성취감 등을 어떻게 표현하는가?)

## 핵심 가치관
(삶에서 중요하게 여기는 것, 의사결정의 기준)

## 생활 패턴 및 루틴
(일상적인 생활 방식, 시간 관리, 습관)

## 인간관계 및 주변 환경
(가족, 동료, 사회적 관계에 대한 언급 패턴)

## 메모
(기타 알아두면 유용한 개인적 맥락)"""
    },
    {
        "filename": "profile_05_ai_usage.md",
        "title": "AI 활용 방식",
        "focus": "AI를 어떤 용도로 사용하는가, 선호하는 답변 스타일, 반복적인 요청 유형",
        "extract_prompt_section": "[A. 주요용도] AI를 주로 어떤 목적으로 사용하는가? (코드, 글쓰기, 정보검색, 감정나눔 등)\n[B. 선호스타일] 어떤 답변 형식/길이/톤을 선호하는가?\n[C. 반복요청] 자주 반복되는 요청 패턴이나 작업 유형",
        "synthesis_template": """# AI 활용 방식

## 주요 활용 목적
(AI를 어떤 용도로 주로 쓰는가? 코드 작성, 정보 검색, 글쓰기, 의사결정, 감정 나눔 등)

## 선호하는 답변 스타일
(답변 길이, 형식, 톤, 구체성 수준 등)

## 자주 반복되는 요청 패턴
(특정 작업 유형, 반복 질문 주제, 특유의 요청 방식)

## AI와의 상호작용 특징
(어떻게 피드백을 주는가? 수정 요청 방식, 만족/불만족 표현)"""
    },
]


# ─────────────────────────────────────────
# 1단계: User 턴 샘플링
# ─────────────────────────────────────────
def sample_user_turns(folder: str, sample_size: int) -> list[dict]:
    """
    MD 파일에서 User 턴만 추출 후 랜덤 샘플링.
    너무 짧은 턴(20자 미만)은 제외.
    """
    all_turns = []
    md_files = sorted(Path(folder).glob("*.md"))

    for md_file in md_files:
        content = None
        for encoding in ["utf-8", "cp949", "euc-kr"]:
            try:
                with open(md_file, "r", encoding=encoding) as f:
                    content = f.read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            with open(md_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

        date_match = re.match(r"(\d{4}-\d{2}-\d{2})", md_file.stem)
        date = date_match.group(1) if date_match else "unknown"

        user_turns = re.findall(
            r"## 👤 User\s*\n(.*?)(?=## |\Z)", content, re.DOTALL
        )
        for turn in user_turns:
            turn = turn.strip()
            if len(turn) >= 20:
                all_turns.append({"date": date, "text": turn[:600]})

    print(f"전체 User 턴: {len(all_turns)}개")
    sampled = random.sample(all_turns, min(sample_size, len(all_turns)))
    print(f"샘플링: {len(sampled)}개")
    return sampled


# ─────────────────────────────────────────
# 2단계: 배치별 사실 추출 (카테고리 특화)
# ─────────────────────────────────────────
def extract_facts_from_batch(batch: list[dict], profile_config: dict) -> str | None:
    """
    특정 프로필 카테고리에 맞춰 사실 추출.
    """
    batch_text = "\n\n".join(
        [f"[{t['date']}] {t['text']}" for t in batch]
    )

    prompt = f"""다음은 한 사람이 AI와 나눈 대화의 질문들입니다.
이 사람의 '{profile_config['title']}'와 관련된 정보를 분석해주세요.

분석 항목:
{profile_config['extract_prompt_section']}

규칙:
- 반복적으로 나타나는 패턴 위주로 추출
- 한국어로 작성
- 확실하지 않은 내용은 추측하지 말고 생략

대화 내용:
{batch_text}

분석 결과:"""

    return call_claude(prompt)


# ─────────────────────────────────────────
# 3단계: 카테고리별 profile.md 합성
# ─────────────────────────────────────────
def synthesize_profile(all_facts: str, profile_config: dict) -> str | None:
    """
    추출된 사실을 바탕으로 해당 카테고리 profile.md 생성.
    """
    prompt = f"""다음은 한 사람의 '{profile_config['title']}' 관련 분석 결과들입니다.
중복을 합치고 중요한 것 위주로 아래 형식의 프로필을 작성해주세요.

수집된 분석:
{all_facts}

---

{profile_config['synthesis_template']}"""

    return call_claude(prompt)


# ─────────────────────────────────────────
# 단일 프로필 생성
# ─────────────────────────────────────────
def generate_single_profile(
    turns: list[dict],
    profile_config: dict,
    output_dir: str,
) -> str | None:
    filename = profile_config["filename"]
    output_path = Path(output_dir) / filename

    print(f"\n  📄 '{filename}' 생성 중...")

    batches = [turns[i:i+BATCH_SIZE] for i in range(0, len(turns), BATCH_SIZE)]
    all_facts = []

    for i, batch in enumerate(batches):
        print(f"    배치 {i+1}/{len(batches)} 처리 중...", end="\r")
        facts = extract_facts_from_batch(batch, profile_config)
        if facts:
            all_facts.append(facts)

    if not all_facts:
        # 한 배치도 못 건졌다 = CLI 가 죽었다. 기존 프로필을 덮지 않는다 —
        # 카드 쪽 "LLM 실패 시 기존 카드 유지" 와 같은 보호다. 빈 파일로 덮으면
        # 다음 달까지 프로필 층이 통째로 비어 있게 된다.
        print(f"    ⚠️  '{filename}' 추출 0건 (claude 불가) — 기존 파일 유지")
        return None

    print(f"    {len(all_facts)}/{len(batches)}개 배치 완료, 합성 중...")
    combined_facts = "\n\n".join(all_facts)
    # 합성 단계 컨텍스트 오버플로 방지
    if len(combined_facts) > FACTS_MAX_CHARS:
        combined_facts = combined_facts[:FACTS_MAX_CHARS] + "\n...(이하 생략)"
    profile = synthesize_profile(combined_facts, profile_config)
    if not profile:
        print(f"    ⚠️  '{filename}' 합성 실패 — 기존 파일 유지")
        return None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(profile)

    print(f"    ✅ '{filename}' 저장 완료")
    return profile


# ─────────────────────────────────────────
# 메인: 5개 프로필 전체 생성
# ─────────────────────────────────────────
def generate_all_profiles(
    conversations_folder: str = CONVERSATIONS_FOLDER,
    output_dir: str = OUTPUT_DIR,
):
    # 1단계: 샘플링 (1회만)
    print("\n[1/3] User 턴 샘플링 중...")
    turns = sample_user_turns(conversations_folder, SAMPLE_SIZE)

    # 2~3단계: 각 프로필 생성
    print(f"\n[2/3] 5개 카테고리별 프로필 생성 시작 (claude -p)...")
    results = {}
    for i, config in enumerate(PROFILE_CONFIGS, 1):
        print(f"\n  ── 프로필 {i}/5: {config['title']} ──")
        profile = generate_single_profile(turns, config, output_dir)
        results[config["filename"]] = profile
        if _cards._CLAUDE_DOWN:
            # 한 번 죽으면 이후 호출은 전부 None 이라 남은 프로필을 도는 것은
            # 시간 낭비다. 여기서 끊고 종료 코드로 알린다.
            print("\n  ⚠️  claude CLI 불가 — 남은 프로필은 다음 달 재시도")
            break

    # 완료 요약
    print("\n[3/3] 완료!")
    print(f"\n생성된 프로필 파일 ({output_dir}/):")
    for config in PROFILE_CONFIGS:
        path = Path(output_dir) / config["filename"]
        size = path.stat().st_size if path.exists() else 0
        print(f"  ✅ {config['filename']} ({size} bytes)")

    print("\n── profile_01 미리보기 ──")
    first_profile = results.get(PROFILE_CONFIGS[0]["filename"]) or "(생성 안 됨)"
    print(first_profile[:400])

    return results


if __name__ == "__main__":
    # 사용법:
    #   python generate_profile.py          → 전체 1~5 생성
    #   python generate_profile.py 4        → 4번만 생성
    #   python generate_profile.py 4 5      → 4, 5번만 생성

    args = [int(a) for a in sys.argv[1:] if a.isdigit()]

    if not args:
        generate_all_profiles()
    else:
        print("\n[1/2] User 턴 샘플링 중...")
        turns = sample_user_turns(CONVERSATIONS_FOLDER, SAMPLE_SIZE)

        print(f"\n[2/2] 프로필 {args} 생성 시작 (claude -p)...")
        for num in args:
            idx = num - 1  # 1-based → 0-based
            if idx < 0 or idx >= len(PROFILE_CONFIGS):
                print(f"  ⚠️  {num}번 프로필은 없음 (1~5만 가능)")
                continue
            config = PROFILE_CONFIGS[idx]
            print(f"\n  ── 프로필 {num}/5: {config['title']} ──")
            generate_single_profile(turns, config, OUTPUT_DIR)

    # claude 가 죽어 아무것도 못 만들었으면 종료 코드로 알린다 — monthly_update
    # 가 이걸 보고 실패를 남기게. 조용히 성공으로 끝나던 것이 두 달 공백의 원인이었다.
    sys.exit(1 if _cards._CLAUDE_DOWN else 0)
