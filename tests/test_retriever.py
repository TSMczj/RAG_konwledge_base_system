"""T-4.1 检索器测试：Top-K、排序、分数转换与字段契约。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from app.context import RequestContext
from app.core.retriever import Retriever


class FakeStore:
    """只记录 query 调用并返回预设 (Document, distance)。"""

    def __init__(self, rows: list[tuple[Document, float]]) -> None:
        self.rows = rows
        self.last_query: tuple[str, int] | None = None

    def query(self, query_text: str, k: int | None = None):
        self.last_query = (query_text, int(k or 0))
        return self.rows[: int(k or len(self.rows))]


def _doc(name: str, page: int, text: str, *, chunk_id: str = "", kind: str = "text") -> Document:
    return Document(
        page_content=text,
        metadata={
            "source_file": name,
            "page": page,
            "chunk_id": chunk_id or f"{name}-{page}",
            "file_hash": f"hash-{name}",
            "kind": kind,
            "tenant_id": "local",
        },
    )


def test_retrieve_returns_sorted_scores_and_limit() -> None:
    store = FakeStore([
        (_doc("a.pdf", 1, "A"), 0.20),
        (_doc("b.pdf", 2, "B"), 0.50),
        (_doc("c.pdf", 3, "C"), 0.10),
    ])
    retriever = Retriever(store=store, settings=SimpleNamespace(top_k=5))

    result = retriever.retrieve("问题", k=3)

    assert store.last_query == ("问题", 3)
    assert [item.file_name for item in result.items] == ["c.pdf", "a.pdf", "b.pdf"]
    assert [round(item.score, 2) for item in result.items] == [0.90, 0.80, 0.50]
    assert result.elapsed_ms >= 0


def test_default_k_uses_settings() -> None:
    store = FakeStore([(_doc("a.pdf", 1, "A"), 0.2)])
    retriever = Retriever(store=store, settings=SimpleNamespace(top_k=5))

    retriever.retrieve("问题")

    assert store.last_query == ("问题", 5)


def test_empty_question_is_rejected() -> None:
    retriever = Retriever(
        store=FakeStore([]),
        settings=SimpleNamespace(top_k=5),
    )
    with pytest.raises(ValueError):
        retriever.retrieve("   ")


def test_invalid_k_is_rejected() -> None:
    retriever = Retriever(
        store=FakeStore([]),
        settings=SimpleNamespace(top_k=5),
    )
    with pytest.raises(ValueError):
        retriever.retrieve("问题", k=0)


def test_sources_match_api_contract() -> None:
    store = FakeStore([
        (_doc("a.pdf", 2, "片段内容", chunk_id="chunk-1", kind="table"), 0.2),
    ])
    retriever = Retriever(store=store, settings=SimpleNamespace(top_k=5))

    result = retriever.retrieve("问题", k=1)
    source = result.sources()[0]

    assert source["n"] == 1
    assert source["file_name"] == "a.pdf"
    assert source["page"] == 2
    assert source["chunk_id"] == "chunk-1"
    assert source["kind"] == "table"
    assert source["score"] == 0.8
    assert source["excerpt"] == "片段内容"
    assert source["document_id"] == ""


def test_missing_metadata_falls_back() -> None:
    store = FakeStore([(Document(page_content="无 metadata", metadata={}), 0.3)])
    retriever = Retriever(store=store, settings=SimpleNamespace(top_k=5))

    item = retriever.retrieve("问题", k=1).items[0]

    assert item.file_name == "未知文件"
    assert item.page == 0
    assert item.kind == "text"
    assert item.chunk_id == ""
    assert item.tenant_id == "local"


def test_scores_are_clamped() -> None:
    store = FakeStore([
        (_doc("a.pdf", 1, "A"), -0.5),
        (_doc("b.pdf", 1, "B"), 1.5),
    ])
    retriever = Retriever(store=store, settings=SimpleNamespace(top_k=5))

    items = retriever.retrieve("问题", k=2).items

    assert items[0].score == 1.0
    assert items[1].score == 0.0


def test_context_tenant_is_used_when_metadata_missing() -> None:
    store = FakeStore([(Document(page_content="无 metadata", metadata={}), 0.3)])
    retriever = Retriever(
        store=store,
        settings=SimpleNamespace(top_k=5),
        context=RequestContext(tenant_id="tenant-a"),
    )

    item = retriever.retrieve("问题", k=1).items[0]

    assert item.tenant_id == "tenant-a"
