# Memtrail 기여 가이드

Memtrail에 관심을 가져주셔서 감사합니다. 이슈와 PR 모두 환영합니다.

한 사람의 실사용 시스템에서 나온 리포라, 기여 전에 [README 의 "알아둘 것"](README.md#알아둘-것)을 먼저 읽으면
어디까지가 범용이고 어디부터가 개인 설정인지 감이 잡힙니다.

## 시작하기

### 1. 저장소 클론

```bash
git clone https://github.com/Eum-Jium/memtrail.git
cd memtrail
```

### 2. 개발 환경 설정

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # Linux/Mac

pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 본인 환경에 맞게 수정
```

### 4. 로컬 테스트

```bash
python test_memory.py
python test_embeddings.py
```

## 브랜치 전략

```
main                ← 보호된 기본 브랜치 (직접 push 금지)
├── feat/*          ← 새 기능
├── fix/*           ← 버그 수정
├── refactor/*      ← 리팩토링
├── docs/*          ← 문서
└── exp/*           ← 실험적 변경
```

### 브랜치 이름 예시

```
feat/temporal-graph-layer
feat/memory-decay
fix/chroma-encoding-issue
docs/architecture-update
exp/sqlite-vec-migration
```

## 커밋 메시지 규칙

[Conventional Commits](https://www.conventionalcommits.org/) 스타일을 사용합니다:

```
<type>(<scope>): <짧은 설명>

<본문 - 무엇을 왜 했는지>

<푸터 - issue 번호, breaking change 등>
```

### Type 종류

| type | 용도 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `refactor` | 동작 변화 없는 코드 개선 |
| `docs` | 문서 |
| `test` | 테스트 추가/수정 |
| `chore` | 빌드/설정/패키지 |
| `perf` | 성능 개선 |

### 예시

```
feat(graph): 엔티티 추출 LLM 파이프라인 추가

대화 텍스트에서 인물/프로젝트/도구 엔티티를 추출하고
SQLite 그래프 DB에 저장. Phase 1 시간 인식 그래프의 기반.

Closes #12
```

## Pull Request 절차

1. **이슈부터** — 큰 변경은 Issue에서 먼저 논의
2. **브랜치 생성** — `feat/`, `fix/` 등 prefix 사용
3. **변경사항 커밋** — 작고 의미 있는 단위로
4. **테스트** — 기존 테스트 통과 확인 + 가능하면 새 테스트 추가
5. **PR 생성** — 템플릿 작성, 관련 이슈 링크
6. **리뷰** — maintainer 승인 필요
7. **Merge** — Squash merge 권장 (히스토리 깔끔)

## 코드 스타일

- **Python**: PEP 8 준수, 타입 힌트 권장
- **포매터**: `ruff format` (또는 `black`)
- **린터**: `ruff check`
- **들여쓰기**: 4 spaces
- **최대 라인 길이**: 100자

```bash
# 포매팅 + 린팅
pip install ruff
ruff format .
ruff check .
```

## 테스트

기존 테스트 실행:

```bash
python test_memory.py
python test_embeddings.py
```

새 기능 추가 시 가능하면 테스트도 함께 추가해주세요.

## 데이터 보안 (중요!)

이 프로젝트는 개인 메모리/대화 데이터를 다룹니다. **반드시** 지켜주세요:

### 절대 커밋하면 안 되는 것

- `memory_db/` — ChromaDB 벡터 저장소 (개인 대화 임베딩)
- `memory/` — 사용자 프로필 (개인정보)
- `conversations/` — 대화 원본 (claude.ai + Claude Code)
- `.env` — API 키, 시크릿 등
- 본인의 실제 데이터가 담긴 어떤 파일도

`.gitignore` 가 이미 설정되어 있지만, **commit 전 반드시 `git status`로 확인**.

### 실수로 민감 데이터를 커밋했다면

```bash
# 마지막 커밋만 되돌리기 (push 안 했을 때)
git reset --soft HEAD~1
git rm --cached <민감_파일>
git commit -c ORIG_HEAD

# 이미 push 했다면 즉시 알리고 BFG로 히스토리 청소
```

## 이슈 라벨

| 라벨 | 의미 |
|---|---|
| `bug` | 버그 |
| `enhancement` | 새 기능 |
| `documentation` | 문서 |
| `good first issue` | 입문자 추천 |
| `help wanted` | 도움 환영 |
| `phase-1` ~ `phase-4` | 로드맵 단계 |
| `discussion` | 논의 필요 |

## 코드 오너십 (CODEOWNERS 예정)

```
# 핵심 아키텍처 변경 — Eum-Jium 리뷰 필수
ARCHITECTURE.md          @Eum-Jium
ROADMAP.md               @Eum-Jium
scripts/mailbox_daemon.py @Eum-Jium

# 일반 코드 — 누구나
*.py                     @Eum-Jium
docs/                    @Eum-Jium
```

## 행동 강령

존중과 호기심으로. 자세한 내용: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## 질문이 있다면

- GitHub Issues
- Discussions (활성화 예정)

---

미친 프로젝트에 함께해주셔서 감사합니다 🚀
