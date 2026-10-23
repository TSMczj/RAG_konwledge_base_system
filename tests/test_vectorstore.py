"""Chroma 向量库封装测试（T-3.2）。

用假 Embeddings 避免真实 API；覆盖 upsert、删除、查询、cosine 配置与持久化。
"""

from __future__ import annotations

import gc
from pathlib import Path

from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.core.loader import KIND_TABLE, KIND_TEXT
from app.core.splitter import Chunk
from app.core.vectorstore import QwenEmbeddings, VectorStore


class FakeEmbeddings(Embeddings):
    """用文本长度构造 3 维向量，避免依赖真实嵌入接口。"""

    def __init__(self) -> None:
        self.doc_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls.append(list(texts))
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [float(len(text)), 1.0, 0.0]


def _settings(tmp_path: Path, collection: str = "test") -> Settings:
    return Settings(
        deepseek_api_key="test-key",
        dashscope_api_key="test-key",
        chroma_dir=tmp_path / "chroma",
        collection_name=collection,
    )


def _chunk(
    index: int,
    text: str,
    *,
    source: str = "a.pdf",
    file_hash: str = "hash-a",
    page: int = 1,
    kind: str = KIND_TEXT,
) -> Chunk:
    return Chunk(
        text=text,
        source_file=source,
        file_path=f"C:/tmp/{source}",
        doc_type="pdf",
        page=page,
        chunk_index=index,
        file_hash=file_hash,
        chunk_id=f"{file_hash}-{index}",
        created_at="2026-10-23T00:00:00+00:00",
        kind=kind,
    )


def test_upsert_and_count(tmp_path: Path) -> None:
    fake = FakeEmbeddings()
    store = VectorStore(settings=_settings(tmp_path), embedding_function=fake)

    assert store.count() == 0
    chunks = [
        _chunk(0, "aa"),
        _chunk(1, "bbbb"),
        _chunk(2, "| a | b |\n| --- | --- |", kind=KIND_TABLE),
    ]
    assert store.upsert_chunks(chunks) == 3
    assert store.count() == 3
    assert fake.doc_calls == [[chunk.text for chunk in chunks]]


def test_upsert_same_ids_updates_not_duplicates(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.upsert_chunks([_chunk(0, "old")])
    store.upsert_chunks([_chunk(0, "new")])

    assert store.count() == 1
    stored = store._collection.get(ids=["hash-a-0"])
    assert stored["documents"] == ["new"]


def test_delete_by_file_hash(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.upsert_chunks(
        [
            _chunk(0, "a0", file_hash="hash-a"),
            _chunk(1, "a1", file_hash="hash-a"),
            _chunk(0, "b0", file_hash="hash-b", source="b.pdf"),
        ]
    )

    assert store.delete_by_file_hash("hash-a") == 2
    assert store.count() == 1
    assert store.delete_by_file_hash("missing") == 0
    remaining = store._collection.get()
    assert remaining["metadatas"][0]["file_hash"] == "hash-b"


def test_delete_by_ids_and_source(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.upsert_chunks(
        [
            _chunk(0, "a0", file_hash="hash-a", source="a.pdf"),
            _chunk(0, "b0", file_hash="hash-b", source="b.pdf"),
        ]
    )

    assert store.delete_by_ids(["hash-a-0"]) == 1
    assert store.count() == 1
    assert store.delete_by_source("b.pdf") == 1
    assert store.count() == 0


def test_query_returns_document_and_distance(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.upsert_chunks([_chunk(0, "aa"), _chunk(1, "bbbb", page=2)])

    results = store.query("aa", k=2)
    assert len(results) == 2
    document, distance = results[0]
    assert document.page_content == "aa"
    assert isinstance(distance, float)
    assert document.metadata["chunk_id"] == "hash-a-0"
    assert document.metadata["kind"] == KIND_TEXT

    filtered = store.query("aa", k=5, where={"page": 2})
    assert len(filtered) == 1
    assert filtered[0][0].page_content == "bbbb"


def test_cosine_metric_is_configured(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    assert store._collection.metadata["hnsw:space"] == "cosine"


def test_persistence_across_instances(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = VectorStore(settings=settings, embedding_function=FakeEmbeddings())
    store.upsert_chunks([_chunk(0, "aa"), _chunk(1, "bbbb")])
    assert store.count() == 2

    del store
    gc.collect()

    reopened = VectorStore(settings=settings, embedding_function=FakeEmbeddings())
    assert reopened.count() == 2
    results = reopened.query("aa", k=1)
    assert results and results[0][0].page_content == "aa"


def test_empty_upsert_is_noop(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    assert store.upsert_chunks([]) == 0
    assert store.count() == 0


class FakeEmbedder:
    """只用于验证 QwenEmbeddings 的转发，不触碰真实 API。"""

    def embed_texts(self, texts):
        return [[1.0, 2.0] for _ in texts]

    def embed_query(self, text):
        return [3.0, 4.0]


def test_qwen_embeddings_adapter_forwards() -> None:
    adapter = QwenEmbeddings(embedder=FakeEmbedder())  # type: ignore[arg-type]

    assert adapter.embed_documents(["a", "b"]) == [[1.0, 2.0], [1.0, 2.0]]
    assert adapter.embed_query("q") == [3.0, 4.0]
