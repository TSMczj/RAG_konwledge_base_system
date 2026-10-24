"""建库 CLI 测试（T-3.4）。

用假 Embeddings + 临时文件跑通 build_index 的完整流程，不调用真实 API。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from langchain_core.embeddings import Embeddings

import scripts.build_index as build_module
from app.config import Settings, reset_settings_cache
from app.core.vectorstore import VectorStore
from scripts.build_index import build_index


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    reset_settings_cache()
    yield
    reset_settings_cache()


class FakeEmbeddings(Embeddings):
    def __init__(self) -> None:
        self.doc_calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.doc_calls.append(list(texts))
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]


class FailingEmbeddings(FakeEmbeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding down")


def _settings(tmp_path: Path, collection: str = "build-test") -> Settings:
    return Settings(
        deepseek_api_key="test-key",
        dashscope_api_key="test-key",
        upload_dir=tmp_path / "uploads",
        chroma_dir=tmp_path / "chroma",
        collection_name=collection,
    )


def _make_uploads(tmp_path: Path) -> Path:
    uploads = tmp_path / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "a.txt").write_text("第一份文档。", encoding="utf-8")
    (uploads / "b.txt").write_text("第二份文档。", encoding="utf-8")
    (uploads / "bad.docx").write_bytes(b"not a docx")
    (uploads / "note.md").write_text("# note", encoding="utf-8")
    return uploads


def test_build_index_full_flow_then_skip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploads = _make_uploads(tmp_path)
    fake = FakeEmbeddings()
    store = VectorStore(settings=settings, embedding_function=fake)

    first = build_index(uploads, store=store, settings=settings)

    assert first.scanned == 4
    assert first.parsed_ok == 2
    assert first.parse_failed == 1
    assert first.skipped_files == 1
    assert first.empty_docs == 0
    assert first.indexed_files == 2
    assert first.already_indexed_files == 0
    assert first.index_failed == 0
    assert first.chunks_written == 2
    assert store.count() == 2
    assert any("bad.docx" in line for line in first.failures)

    # 第二次：同一批文件全部按 file_hash 跳过，不再调用嵌入
    second = build_index(uploads, store=store, settings=settings)

    assert second.indexed_files == 0
    assert second.already_indexed_files == 2
    assert second.chunks_written == 0
    assert store.count() == 2
    assert len(fake.doc_calls) == 2


def test_build_index_force_reindexes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploads = _make_uploads(tmp_path)
    fake = FakeEmbeddings()
    store = VectorStore(settings=settings, embedding_function=fake)

    build_index(uploads, store=store, settings=settings)
    forced = build_index(uploads, force=True, store=store, settings=settings)

    assert forced.indexed_files == 2
    assert forced.already_indexed_files == 0
    assert forced.chunks_written == 2
    assert store.count() == 2
    assert len(fake.doc_calls) == 4


def test_build_index_empty_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    store = VectorStore(
        settings=settings,
        embedding_function=FakeEmbeddings(),
    )

    stats = build_index(uploads, store=store, settings=settings)

    assert stats.scanned == 0
    assert stats.parsed_ok == 0
    assert stats.failures == []
    assert store.count() == 0


def test_build_index_empty_document_is_skipped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    Document().save(uploads / "empty.docx")
    store = VectorStore(
        settings=settings,
        embedding_function=FakeEmbeddings(),
    )

    stats = build_index(uploads, store=store, settings=settings)

    assert stats.parsed_ok == 1
    assert stats.empty_docs == 1
    assert stats.indexed_files == 0
    assert store.count() == 0


def test_build_index_missing_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = VectorStore(
        settings=settings,
        embedding_function=FakeEmbeddings(),
    )

    stats = build_index(tmp_path / "nope", store=store, settings=settings)

    assert stats.scanned == 1
    assert stats.parse_failed == 1
    assert stats.failures


def test_build_index_continues_after_embedding_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "a.txt").write_text("会失败。", encoding="utf-8")
    store = VectorStore(
        settings=settings,
        embedding_function=FailingEmbeddings(),
    )

    stats = build_index(uploads, store=store, settings=settings)

    assert stats.parsed_ok == 1
    assert stats.index_failed == 1
    assert stats.indexed_files == 0
    assert stats.failures
    assert store.count() == 0


def test_cli_main_wires_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    uploads = tmp_path / "cli_uploads"
    uploads.mkdir()
    (uploads / "a.txt").write_text("第一份文档。", encoding="utf-8")
    (uploads / "b.txt").write_text("第二份文档。", encoding="utf-8")
    store = VectorStore(
        settings=settings,
        embedding_function=FakeEmbeddings(),
    )

    monkeypatch.setattr(build_module, "get_settings", lambda: settings)
    monkeypatch.setattr(build_module, "VectorStore", lambda settings=None: store)
    monkeypatch.setattr(build_module, "setup_logging", lambda: None)
    monkeypatch.setattr(store, "close", lambda: None)

    code = build_module.main(["--dir", str(uploads)])

    assert code == 0
    assert store.count() == 2
