"""Chroma 向量库封装测试（T-3.2）。

用假 Embeddings 避免真实 API；覆盖 upsert、删除、查询、cosine 配置与持久化。
"""

from __future__ import annotations

import gc
from pathlib import Path

import pytest
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


class FailingEmbeddings(FakeEmbeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding down")


def test_has_file_and_count_by_hash(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks([_chunk(0, "a", file_hash="hash-a")])

    assert store.has_file("hash-a")
    assert not store.has_file("hash-b")
    assert store.count_by_file_hash("hash-a") == 1
    assert store.count_by_file_hash("hash-b") == 0


def test_index_skips_same_file_without_embedding(tmp_path: Path) -> None:
    fake = FakeEmbeddings()
    store = VectorStore(settings=_settings(tmp_path), embedding_function=fake)
    chunks = [_chunk(0, "a"), _chunk(1, "b")]

    first = store.index_chunks(chunks)
    second = store.index_chunks(chunks)

    assert not first.skipped and first.indexed == 2 and first.count_before == 0
    assert second.skipped and second.indexed == 0 and second.count_before == 2
    assert store.count() == 2
    # 第二次应在嵌入之前就跳过，只产生一次嵌入调用
    assert len(fake.doc_calls) == 1


def test_index_skips_renamed_same_content(tmp_path: Path) -> None:
    fake = FakeEmbeddings()
    store = VectorStore(settings=_settings(tmp_path), embedding_function=fake)

    store.index_chunks([_chunk(0, "a", source="old.pdf")])
    result = store.index_chunks([_chunk(0, "a", source="new.pdf")])

    assert result.skipped
    assert store.count() == 1
    assert len(fake.doc_calls) == 1


def test_index_reindexes_partial_existing_chunks(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks([_chunk(0, "a")])

    result = store.index_chunks([_chunk(0, "a"), _chunk(1, "b")])

    assert not result.skipped
    assert result.count_before == 1
    assert store.count() == 2


def test_index_removes_stale_chunks_after_rechunk(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks([_chunk(0, "a"), _chunk(1, "b"), _chunk(2, "c")])

    result = store.index_chunks([_chunk(0, "a"), _chunk(1, "b")])

    assert not result.skipped
    assert store.count() == 2
    remaining = store._collection.get()
    assert sorted(remaining["ids"]) == ["hash-a-0", "hash-a-1"]


def test_index_rejects_mixed_file_hashes(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    with pytest.raises(ValueError, match="同一个文件"):
        store.index_chunks(
            [
                _chunk(0, "a", file_hash="hash-a"),
                _chunk(0, "b", file_hash="hash-b"),
            ]
        )
    assert store.count() == 0


def test_index_empty_chunks_is_noop(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    result = store.index_chunks([])

    assert result.file_hash == ""
    assert result.indexed == 0
    assert result.skipped
    assert store.count() == 0


def test_index_skip_existing_false_forces_rewrite(tmp_path: Path) -> None:
    fake = FakeEmbeddings()
    store = VectorStore(settings=_settings(tmp_path), embedding_function=fake)
    chunks = [_chunk(0, "a")]

    store.index_chunks(chunks)
    result = store.index_chunks(chunks, skip_existing=False)

    assert not result.skipped
    assert result.indexed == 1
    assert len(fake.doc_calls) == 2
    assert store.count() == 1


def test_index_embedding_failure_leaves_no_writes(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FailingEmbeddings(),
    )
    with pytest.raises(RuntimeError, match="embedding down"):
        store.index_chunks([_chunk(0, "a")])

    assert store.count() == 0
    assert not store.has_file("hash-a")


def test_modified_content_is_a_new_version(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks([_chunk(0, "old", file_hash="hash-a", source="a.pdf")])
    store.index_chunks([_chunk(0, "new", file_hash="hash-b", source="a.pdf")])

    # 内容变了就是新文件；旧版本不会自动删除（T-3.5 提供按文件删除）
    assert store.has_file("hash-a")
    assert store.has_file("hash-b")
    assert store.count() == 2


def test_list_files_groups_by_hash(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks(
        [
            _chunk(0, "a0", source="a.pdf", file_hash="hash-a", page=1),
            _chunk(1, "a1", source="a.pdf", file_hash="hash-a", page=2),
        ]
    )
    store.index_chunks(
        [_chunk(0, "b0", source="b.pdf", file_hash="hash-b", page=0)]
    )

    files = store.list_files()

    assert len(files) == 2
    by_hash = {item.file_hash: item for item in files}
    assert by_hash["hash-a"].source_file == "a.pdf"
    assert by_hash["hash-a"].chunks == 2
    assert by_hash["hash-a"].pages == 2
    assert by_hash["hash-b"].chunks == 1
    assert by_hash["hash-b"].pages == 0
    assert by_hash["hash-a"].created_at.startswith("2026-10-23")


def test_delete_all_clears_collection_but_keeps_it_usable(tmp_path: Path) -> None:
    store = VectorStore(
        settings=_settings(tmp_path),
        embedding_function=FakeEmbeddings(),
    )
    store.index_chunks([_chunk(0, "a"), _chunk(1, "b")])

    assert store.delete_all() == 2
    assert store.count() == 0
    assert store.list_files() == []

    store.index_chunks([_chunk(0, "c", file_hash="hash-c")])
    assert store.count() == 1
    assert store.has_file("hash-c")
