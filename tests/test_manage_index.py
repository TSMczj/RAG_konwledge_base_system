"""索引管理 CLI 测试（T-3.5）。

用假 Embeddings + 临时 collection 覆盖列表、按哈希/文件名删除与重建。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

import scripts.manage_index as manage_module
from app.config import Settings, reset_settings_cache
from app.core.loader import KIND_TEXT
from app.core.splitter import Chunk
from app.core.vectorstore import VectorStore

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    reset_settings_cache()
    yield
    reset_settings_cache()


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]


def _settings(tmp_path: Path, collection: str = "manage-test") -> Settings:
    return Settings(
        deepseek_api_key="test-key",
        dashscope_api_key="test-key",
        upload_dir=tmp_path / "uploads",
        chroma_dir=tmp_path / "chroma",
        collection_name=collection,
    )


def _chunk(
    index: int,
    text: str,
    *,
    source: str = "a.pdf",
    file_hash: str = HASH_A,
    page: int = 1,
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
        kind=KIND_TEXT,
    )


def _store_with_two_files(settings: Settings) -> VectorStore:
    store = VectorStore(settings=settings, embedding_function=FakeEmbeddings())
    store.index_chunks(
        [
            _chunk(0, "a0", source="a.pdf", file_hash=HASH_A, page=1),
            _chunk(1, "a1", source="a.pdf", file_hash=HASH_A, page=2),
        ]
    )
    store.index_chunks(
        [_chunk(0, "b0", source="b.pdf", file_hash=HASH_B, page=1)]
    )
    return store


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    store: VectorStore,
) -> None:
    monkeypatch.setattr(manage_module, "get_settings", lambda: settings)
    monkeypatch.setattr(manage_module, "VectorStore", lambda settings=None: store)
    # CLI 结束会 close；测试要继续用同一个 store 做断言
    monkeypatch.setattr(store, "close", lambda: None)
    # 避免把 loguru sink 绑到 pytest 的临时捕获流上
    monkeypatch.setattr(manage_module, "setup_logging", lambda: None)


def test_list_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)

    code = manage_module.main(["--list"])
    output = capsys.readouterr().out

    assert code == 0
    assert "a.pdf" in output
    assert "b.pdf" in output
    assert "共 2 个文件" in output
    assert HASH_A in output


def test_delete_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)

    code = manage_module.main(["--delete", HASH_A, "--yes"])

    assert code == 0
    assert store.count() == 1
    assert not store.has_file(HASH_A)
    assert store.has_file(HASH_B)


def test_delete_by_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)

    code = manage_module.main(["--delete", "b.pdf", "--yes"])

    assert code == 0
    assert store.count() == 2  # a.pdf 两个 chunk
    assert not store.has_file(HASH_B)


def test_delete_missing_target_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)

    code = manage_module.main(["--delete", "missing.pdf", "--yes"])

    assert code == 1
    assert store.count() == 3


def test_delete_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    code = manage_module.main(["--delete", HASH_A])

    assert code == 1
    assert store.count() == 3


def test_rebuild_clears_and_reindexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    (settings.upload_dir / "a.txt").write_text("重建后的文档。", encoding="utf-8")
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)

    code = manage_module.main(["--rebuild", "--yes"])

    assert code == 0
    assert store.count() == 1
    files = store.list_files()
    assert len(files) == 1
    assert files[0].source_file == "a.txt"


def test_rebuild_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    store = _store_with_two_files(settings)
    _patch_cli(monkeypatch, settings, store)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    code = manage_module.main(["--rebuild"])

    assert code == 1
    assert store.count() == 3
