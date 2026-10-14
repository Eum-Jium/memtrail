"""
chroma_paging.py — 컬렉션 전량 조회를 페이지로 끊는다.

chroma 의 `col.get()` 을 id/필터 없이 부르면 컬렉션의 모든 레코드를 한 번의
SQL 로 긁는데, 레코드 하나가 바인딩 변수 하나라 SQLite 의 상한
(SQLITE_MAX_VARIABLE_NUMBER — 3.32+ 기본 32,766)을 넘는 순간
`too many SQL variables` 로 죽는다.

2026-08-17 에 실제로 밟았다: 8/16 인덱싱이 청크를 32,510 → 32,812 로 올리며
선을 넘었고, 다음 날부터 증분 인덱싱이 사흘 연속 크래시했다. 코드가 상한을
넘긴 게 아니라 **누적 규모가** 넘긴 거라, 한 번 넘으면 저절로 낫지 않는다.

`get_all()` 은 같은 결과를 limit/offset 으로 나눠 받는다. raw chromadb
Collection 과 langchain_chroma 의 Chroma 래퍼 양쪽에 그대로 쓴다 — 둘 다
get(limit=, offset=) 을 받는다.

사서 데몬(`scripts/mailbox_daemon.py` 의 BM25 build)은 처음부터 같은 방식으로
페이지를 끊고 있었다 — 그래서 이 사고에 안 걸렸다.
"""

PAGE = 5000


def get_all(col, include, page: int = PAGE) -> dict:
    """col 의 전 레코드를 페이지로 나눠 받아 한 dict 으로 합친다.

    반환 형태는 col.get() 과 같다 — {"ids": [...], <include 각 키>: [...]}.
    """
    include = list(include)
    out: dict[str, list] = {"ids": []}
    for k in include:
        out[k] = []
    off = 0
    while True:
        got = col.get(include=include, limit=page, offset=off)
        n = len(got["ids"])
        if not n:
            break
        out["ids"] += got["ids"]
        for k in include:
            # include 로 요청한 키가 비어 돌아오는 구현 차이를 흡수한다
            out[k] += list(got.get(k) or [None] * n)
        off += n
    return out
