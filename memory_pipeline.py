# 원본: https://github.com/nir3y/Memory-Layer (Apache-2.0) — 이 리포에서 수정했다.
"""
메모리 레이어 파이프라인
- MD 대화 파일 → (User + Assistant) 쌍 파싱 → 임베딩 → ChromaDB 저장
"""

import os
import re
from pathlib import Path

# self-signed 인증서를 쓰는 프록시 환경 우회 (옵트인): MEMEX_DISABLE_SSL_VERIFY=1
if os.environ.get("MEMEX_DISABLE_SSL_VERIFY", "").lower() in ("1", "true", "yes"):
    import httpx
    _orig_client = httpx.Client
    class _NoSSLClient(_orig_client):
        def __init__(self, *a, **kw):
            kw['verify'] = False
            super().__init__(*a, **kw)
    httpx.Client = _NoSSLClient

from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from chroma_paging import get_all


class E5Embeddings(HuggingFaceEmbeddings):
    """intfloat/multilingual-e5-base용 래퍼: query/passage prefix 자동 적용"""
    def embed_documents(self, texts):
        return super().embed_documents(["passage: " + t for t in texts])

    def embed_query(self, text: str):
        return super().embed_query("query: " + text)


# ─────────────────────────────────────────
# 1단계: MD 파일 파서
# ─────────────────────────────────────────
VALID_SOURCES = ("claude", "claude_code", "cowork", "chatgpt", "gemini")


def date_topic_from_stem(stem: str) -> tuple[str, str]:
    """파일명 stem → (날짜, 주제). 대화와 카드가 **같은 규칙**을 써야 한다 —
    카드 다리 청크의 date/topic 이 원본 대화의 것과 어긋나면 같은 대화가 답변에서
    두 이름으로 보인다 (히트 dedupe 는 source_file 로 하므로 표시만 갈린다).
    """
    date = "unknown"
    if m := re.match(r"(\d{4}-\d{2}-\d{2})", stem):
        date = m.group(1)
    elif m := re.match(r"(\d{4})(\d{2})(\d{2})[_\-]", stem):
        date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    if m := re.match(r"\d{4}-\d{2}-\d{2}[_\- ]*(.*)", stem):
        topic = m.group(1)
    elif m := re.match(r"\d{8}[_\-]*(.*)", stem):
        topic = m.group(1)
    else:
        topic = stem
    return date, topic


def parse_md_content(
    content: str,
    filename_stem: str,
    source: str = "claude",
    source_file: str | None = None,
) -> list[Document]:
    """
    Markdown 콘텐츠 문자열에서 User+Assistant 쌍을 추출해 Document 리스트로 반환.

    Args:
        content: Markdown 본문
        filename_stem: 메타데이터용 파일명 (확장자 제외). 날짜·주제 추출에 사용.
        source: "claude" | "chatgpt" | "gemini" — 메타데이터에 기록되어 검색 시 필터 가능
        source_file: 원본 파일명 (확장자 포함). 미지정 시 filename_stem + ".md"
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got: {source!r}")

    # 1순위: 파일 내부 생성일 파싱 (볼드/리스트 형식, ISO 8601 모두 지원)
    inner_date_match = re.search(r"\*{0,2}생성일\*{0,2}[:\s]+(\d{4}-\d{2}-\d{2})", content)
    date, topic = date_topic_from_stem(filename_stem)
    if inner_date_match:
        date = inner_date_match.group(1)

    # 파일 내부의 ID 추출
    id_match = re.search(r"- ID: ([a-f0-9\-]+)", content)
    source_id = id_match.group(1) if id_match else filename_stem

    # ── 턴 추출 ─────────────
    # 다양한 헤더 형식 지원: "## 👤 User", "## 🧑 사용자", "## 🤖 Claude" 등
    TURN_HDR = re.compile(r"^##[ \t]+(👤|🧑|🤖)[^\n]*$", re.MULTILINE)

    turns: list[tuple[str, str]] = []  # (role, text)
    marks = list(TURN_HDR.finditer(content))
    for j, m in enumerate(marks):
        end = marks[j + 1].start() if j + 1 < len(marks) else len(content)
        role = "assistant" if m.group(1) == "🤖" else "user"
        turns.append((role, content[m.end():end].strip()))

    # ── 턴 → 청크 ─────────────
    # 기본은 User+Assistant 쌍. 짝이 없는 턴 (첨부만 있던 질문이 변환 때 사라져
    # Assistant 만 남은 파일 등) 도 단독 청크로 살린다 — 예전엔 통째로 유실됐다.
    file_label = source_file or f"{filename_stem}.md"
    documents = []
    i = 0
    pending_user: str | None = None

    def emit(user_text: str, assistant_text: str) -> None:
        nonlocal i
        if not user_text and not assistant_text:
            return
        parts = []
        if user_text:
            parts.append(f"[User]\n{user_text}")
        if assistant_text:
            parts.append(f"[Assistant]\n{assistant_text}")
        documents.append(
            Document(
                page_content="\n\n".join(parts),
                metadata={
                    "date": date,
                    # Chroma where $gte/$lte 프리필터용 (문자열 date 는 범위 비교 불가).
                    # unknown=0 — date 필터 검색에서 자연 탈락.
                    "date_int": int(date.replace("-", "")) if date != "unknown" else 0,
                    "topic": topic,
                    "source": source,
                    "source_id": source_id,
                    "source_file": file_label,
                    "turn_index": i,      # 같은 파일 내 몇 번째 청크인지
                    "user_text": user_text,  # 검색 디버깅용 (User 발화만 따로 보관)
                },
            )
        )
        i += 1

    for role, text in turns:
        if role == "user":
            if pending_user is not None:
                emit(pending_user, "")   # 연속 User → 앞엣것 단독 저장
            pending_user = text
        else:
            emit(pending_user or "", text)
            pending_user = None
    if pending_user is not None:
        emit(pending_user, "")           # 답변 없이 끝난 마지막 질문

    return documents


def decode_cesu8(raw: bytes) -> str:
    """CESU-8 (UTF-16 서로게이트 쌍을 3바이트씩 따로 인코딩) 복구 후 디코딩.

    옛 브라우저 콘솔 다운로드 산출물에 이모지가 이렇게 깨져 들어온 게 있다.
    예전엔 errors="ignore" 로 그 바이트를 통째로 버렸다. 복구도 실패하면
    그때 가서 버린다.
    """
    try:
        return (
            raw.decode("utf-8", errors="surrogatepass")
            .encode("utf-16", "surrogatepass")
            .decode("utf-16")
        )
    except (UnicodeDecodeError, UnicodeEncodeError):
        return raw.decode("utf-8", errors="ignore")


def parse_md_file(file_path: str, source: str = "claude") -> list[Document]:
    for encoding in ["utf-8", "cp949", "euc-kr"]:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        content = decode_cesu8(Path(file_path).read_bytes())

    # macOS 는 tar 추출 등으로 한글 파일명이 NFD 로 저장될 수 있다. 메타데이터가
    # NFD 로 들어가면 manifest(NFC)·클라이언트 파일명과 안 맞아 좌표 조회가 깨진다
    # — 경계에서 NFC 로 통일 (Windows 에선 no-op).
    import unicodedata
    return parse_md_content(
        content,
        filename_stem=unicodedata.normalize("NFC", Path(file_path).stem),
        source=source,
        source_file=unicodedata.normalize("NFC", Path(file_path).name),
    )


# ─────────────────────────────────────────
# 카드 회상어 다리 — 어휘 간극을 메운다
# ─────────────────────────────────────────
# 카드마다 LLM 이 `- 회상어:` 를 만든다. "나중에 이 대화를 다시 찾을 때 쓸 법한
# 검색어" 로, 원문에는 한 번도 안 나오는 별칭·통칭이 들어간다. 그런데 인덱스는
# conversations/ 만 넣어서 **정작 검색이 그 필드를 안 썼다** — 회상 실패의 근본
# 원인(2026-08-15 진단). 여기서 카드 블록당 다리 청크 한 장을 만들어 메운다.
#
# 다리이지 목적지가 아니다. 세 가지가 그 원칙에서 나온다:
#   1) source_file 은 **원본 대화**의 파일명이다. 히트가 카드가 아니라 원문 좌표로
#      떨어지고, 사서의 source_file 단위 dedupe 가 카드와 원문의 중복 자리를 막는다.
#   2) 넣는 건 회상어·화두태그·카드 제목뿐. `결정된 것`·`미결` 같은 요약 본문은
#      **일부러 뺐다** — 정본은 원문 대화이고 정리된 결론은 파생물이라, 파생물이
#      원문과 랭킹을 다투게 두지 않는다. 낭독 층(recent.md)이 이미 그 몫을 한다.
#   3) turn_index 는 카드 블록의 시작 턴이다. 사서의 card_summary_for 가 이 값으로
#      히트한 대목의 카드 조각을 고르므로, 다리로 걸려도 요약이 제 짝을 찾는다.
CARD_HDR_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*$")
CARD_SPAN_RE = re.compile(r"\(턴\s*(\d+)\s*-\s*(\d+)\)")
CARD_RECALL_RE = re.compile(r"^-\s*(회상어|화두태그)\s*:\s*(.+?)\s*$")

# 표제 — 대화 **한 건**의 이름. 카드 파일 맨 위에 한 줄 (`## ` 가 아니라 `# ` 라
# CARD_HDR_RE 에 안 걸린다). 블록 제목(10자)과 층이 다르다:
#
#   블록 제목  구간 하나가 무엇이었나        ## 2025-11-14 | 백업 도구 비교와 이전 목표 (턴 13-21)
#   표제       대화 전체가 **어떻게 끝났나**  # 표제: 백업 체계 전환 결정 (클라우드 이전)
#
# 왜 따로 두나 — 제목은 대화 **초반**에 정해지는데 회상에 쓰는 이름은 대화가
# **끝났을 때**의 것이다. 실측(2026-08-25) 1,378건 중 267건(19.4%)이 어긋나 있었고,
# 「<자동 생성 제목>」 이라는 파일명 아래 222턴짜리 전혀 다른 주제의 대화가 있었다.
# 검색은 회상어로 이미 닿고 있었는데 결과에 붙는 이름표가 파일명이라 **읽는 쪽이
# 맞는 히트를 버렸다** (그 사고가 실제로 났다). 그래서 고친 곳은 생성이 아니라 표시다.
#
# 지난 표제는 지우지 않는다 — canon 의 취소선 관례와 같다.
CARD_TITLE_RE = re.compile(r"^#\s*표제:\s*(.+?)\s*$", re.M)
CARD_TITLE_OLD_RE = re.compile(r"^#\s*옛 표제\(", re.M)


def card_title(content: str) -> str:
    """카드 파일 → 표제. 없으면 빈 문자열 (표제 없는 옛 카드는 폴백이 받는다)."""
    for line in content.splitlines():
        if CARD_TITLE_OLD_RE.match(line):
            continue                       # 취소선 그은 지난 표제는 건너뛴다
        if m := CARD_TITLE_RE.match(line):
            return m.group(1).strip()
        if line.startswith("## "):
            break                          # 첫 블록에 닿았으면 표제는 없다
    return ""


def card_recall_blocks(content: str) -> list[dict]:
    """카드 본문 → 블록별 {title, turn_from, turn_to, 회상어, 화두태그}.

    긴 대화는 카드가 여러 장이고 헤더에 `(턴 0-20)` 이 붙는다. 범위 표기가 없는
    옛 카드·단일 카드는 turn_from=0 으로 둔다 (card_summary_for 의 폴백과 같다).
    """
    blocks: list[dict] = []
    cur: dict | None = None
    for line in content.splitlines():
        if m := CARD_HDR_RE.match(line):
            title = m.group(2)
            span = CARD_SPAN_RE.search(title)
            if span:
                title = CARD_SPAN_RE.sub("", title).strip()
            cur = {"title": title,
                   "turn_from": int(span.group(1)) if span else 0,
                   "turn_to": int(span.group(2)) if span else None,
                   "회상어": "", "화두태그": ""}
            blocks.append(cur)
        elif cur is not None and (m := CARD_RECALL_RE.match(line.strip())):
            cur[m.group(1)] = m.group(2)
    # 회상 어휘가 없는 블록은 버린다 — LLM 실패 시의 자동 스텁 카드가 여기 걸린다.
    # 스텁은 제목+첫 발화뿐이라 원문에 이미 있는 말이고, 넣어봐야 다리가 안 된다.
    return [b for b in blocks if b["회상어"] or b["화두태그"]]


def card_signature(blocks: list[dict], title: str = "") -> str:
    """카드의 회상 어휘 지문. 카드가 다시 생성돼 어휘가 바뀌면 값이 바뀌므로,
    증분 인덱싱이 '이 카드는 이미 넣었는가' 가 아니라 '같은 내용으로 넣었는가' 를
    물을 수 있다 (파일명만 보면 재생성된 카드가 옛 어휘로 굳는다).

    표제도 지문에 든다 — 안 넣으면 회상어가 그대로인 채 표제만 바뀐 카드가
    **인덱스에 반영되지 않는다.** 표제 없는 카드(title="")의 지문은 표제 도입
    전과 **같은 값**이다. 아직 표제를 못 받은 카드를 공연히 다시 태우지 않는다.
    """
    import hashlib
    raw = "|".join(f"{b['turn_from']}|{b['title']}|{b['회상어']}|{b['화두태그']}"
                   for b in blocks)
    if title:
        raw = f"{title}||{raw}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def parse_card_file(card_path: str, conv_file: str, source: str = "claude") -> list[Document]:
    """카드 파일 → 다리 청크. conv_file 은 원본 대화의 파일명(확장자 포함).

    카드 파일명 stem 은 대화 파일명 stem 과 같다 (build_memory_cards.card_path).
    그래도 conv_file 을 받는 건, 좌표가 되는 값을 추측하지 않고 **디스크에 실재하는
    대화 파일명**을 호출자가 확정해 넘기게 하기 위해서다.
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {VALID_SOURCES}, got: {source!r}")

    import unicodedata
    content = Path(card_path).read_text(encoding="utf-8", errors="replace")
    blocks = card_recall_blocks(content)
    if not blocks:
        return []

    conv_file = unicodedata.normalize("NFC", conv_file)
    stem = Path(conv_file).stem
    date, topic = date_topic_from_stem(stem)
    ctitle = card_title(content)
    sig = card_signature(blocks, ctitle)
    card_file = unicodedata.normalize("NFC", Path(card_path).name)

    docs = []
    for b in blocks:
        parts = []
        if b["회상어"]:
            parts.append(f"[회상어] {b['회상어']}")
        if b["화두태그"]:
            parts.append(f"[화두] {b['화두태그']}")
        if b["title"]:
            parts.append(f"[주제] {b['title']}")
        # 표제는 **블록마다 같은 값**이 실린다 — 대화의 이름으로 물었을 때 어느
        # 구간이 걸리든 그 대화에 닿게 하려는 것이다. 표제로 검색해 놓고 3번
        # 블록만 안 걸리는 일이 없어야 한다.
        if ctitle:
            parts.append(f"[표제] {ctitle}")
        docs.append(Document(
            page_content="\n".join(parts),
            metadata={
                "date": date,
                "date_int": int(date.replace("-", "")) if date != "unknown" else 0,
                "topic": topic,
                "source": source,
                "source_id": stem,
                "source_file": conv_file,     # 좌표는 원문 — 카드가 아니다
                "turn_index": b["turn_from"],
                "turn_to": b["turn_to"] if b["turn_to"] is not None else -1,
                "user_text": "",
                "kind": "card",               # 다리 청크 표시 (대화 청크엔 없다)
                "card_file": card_file,       # 증분 인덱싱의 대조 키
                "card_sig": sig,
                "card_title": ctitle,         # 사서가 결과에 붙일 이름 (없으면 "")
            },
        ))
    return docs


# ─────────────────────────────────────────
# 2단계: 전체 빌드 (초기 1회)
# ─────────────────────────────────────────
def build_memory_db(md_folder: str, db_path: str = "./memory_db") -> Chroma:
    # 로컬 임베딩 모델 
    embeddings = E5Embeddings(
        model_name="intfloat/multilingual-e5-base"
    )

    # 모든 MD 파일 수집 및 파싱
    all_documents = []
    md_files = sorted(Path(md_folder).glob("*.md"))

    print(f"총 {len(md_files)}개 파일 발견")

    for i, md_file in enumerate(md_files):
        docs = parse_md_file(str(md_file))
        all_documents.extend(docs)

        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(md_files)} 파일 파싱 완료...")

    print(f"총 {len(all_documents)}개 대화 쌍 추출 → 임베딩 시작")

    # ChromaDB에 한 번에 저장 (임베딩은 내부에서 자동 처리)
    db = Chroma.from_documents(
        documents=all_documents,
        embedding=embeddings,
        persist_directory=db_path,
        collection_name="memory",
    )

    print(f"완료! '{db_path}'에 저장됨")
    return db





# ─────────────────────────────────────────
# 3단계: 새 대화 추가 (세션마다)
# ─────────────────────────────────────────
def add_new_conversation(file_path: str, db_path: str = "./memory_db"):
    """
    새 MD 파일 1개를 기존 ChromaDB에 추가.
    Cowork 세션 종료 후 자동 호출되는 함수.
    """
    embeddings = E5Embeddings(
        model_name="intfloat/multilingual-e5-base"
    )

    db = Chroma(
        persist_directory=db_path,
        embedding_function=embeddings,
        collection_name="memory",
    )

    new_docs = parse_md_file(file_path)
    db.add_documents(new_docs)

    print(f"'{Path(file_path).name}' → {len(new_docs)}개 쌍 추가 완료")


# ─────────────────────────────────────────
# 3단계 (월간 사용): 폴더 증분 인덱싱
# ─────────────────────────────────────────
def add_new_files(
    md_folder: str,
    db_path: str = "./memory_db",
    source: str = "claude",
) -> tuple[int, int]:
    """
    md_folder 내 .md 파일 중 ChromaDB 에 아직 없는 것만 추가한다.

    "이미 있는 파일" 판단 기준: Chroma 컬렉션의 모든 문서 메타데이터에서
    source_file 값을 모아 집합을 만들고, 폴더 파일명과 비교해 차집합을 add.

    Args:
        md_folder: 대화 MD 폴더
        db_path:   ChromaDB persist_directory
        source:    신규 파일에 부여할 출처 ("claude" | "chatgpt" | "gemini")

    Returns:
        (신규로 추가된 파일 수, 추가된 청크(=대화쌍) 수)
    """
    folder = Path(md_folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"MD 폴더 없음: {md_folder}")

    md_files = sorted(folder.glob("*.md"))
    if not md_files:
        print(f"  [info] {md_folder} 에 .md 파일이 없음")
        return (0, 0)

    embeddings = E5Embeddings(model_name="intfloat/multilingual-e5-base")

    db = Chroma(
        persist_directory=db_path,
        embedding_function=embeddings,
        collection_name="memory",
    )

    # 기존 컬렉션의 모든 source_file 메타데이터 수집
    existing = get_all(db, ["metadatas"])
    indexed_files: set[str] = set()
    for m in existing.get("metadatas", []) or []:
        sf = (m or {}).get("source_file")
        if sf:
            indexed_files.add(sf)

    print(f"  [info] 기존 인덱싱된 파일 {len(indexed_files)}개 / 폴더 .md {len(md_files)}개")

    # 차집합만 add
    new_files = [p for p in md_files if p.name not in indexed_files]
    if not new_files:
        print("  [skip] 신규 파일 없음")
        return (0, 0)

    print(f"  [start] 신규 {len(new_files)}개 파일 임베딩 시작...")

    total_chunks = 0
    for i, f in enumerate(new_files, 1):
        docs = parse_md_file(str(f), source=source)
        if docs:
            db.add_documents(docs)
            total_chunks += len(docs)
        if i % 20 == 0 or i == len(new_files):
            print(f"    {i}/{len(new_files)} 처리 (누적 {total_chunks} 청크)")

    print(f"  [done] {len(new_files)}개 파일 / {total_chunks}개 청크 추가 완료")
    return (len(new_files), total_chunks)


# ─────────────────────────────────────────
# 4단계: 검색
# ─────────────────────────────────────────
def search_memory(
    query: str,
    db_path: str = "./memory_db",
    k: int = 5,
    date_filter: str = None,
) -> list[Document]:
    """
    저장된 메모리에서 관련 대화 쌍 검색.
    MMR 방식으로 다양한 맥락의 결과를 반환.

    Args:
        query     : 검색어 (예: "독서 관련 관심사", "문서 자동화 업무")
        k         : 반환할 결과 수
        date_filter: "2025-06" 처럼 특정 연월로 필터링 (선택)
    """
    embeddings = E5Embeddings(
        model_name="intfloat/multilingual-e5-base"
    )

    db = Chroma(
        persist_directory=db_path,
        embedding_function=embeddings,
        collection_name="memory",
    )

    search_kwargs = {"k": k, "fetch_k": k * 4, "lambda_mult": 0.5}

    # 날짜 필터 적용 (선택)
    if date_filter:
        search_kwargs["filter"] = {"date": {"$contains": date_filter}}

    retriever = db.as_retriever(search_type="mmr", search_kwargs=search_kwargs)
    results = retriever.invoke(query)

    return results


# ─────────────────────────────────────────
# 직접 실행 시 ChromaDB 빌드
# ─────────────────────────────────────────
if __name__ == "__main__":
    import sys

    md_folder = sys.argv[1] if len(sys.argv) > 1 else "./conversations"
    db_path   = sys.argv[2] if len(sys.argv) > 2 else "./memory_db"

    print(f"MD 폴더: {md_folder}")
    print(f"DB 경로: {db_path}")

    build_memory_db(md_folder=md_folder, db_path=db_path)
