"""分块器的表格保护与元数据测试（T-2.2 / T-2.3）。

覆盖：表格整表保留、表格文本不变量，以及 chunk 元数据 Schema / chunk_id / 文件哈希。
完整的分块器测试（分隔符、overlap、配置覆盖等）按计划在 T-7.1 扩展。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from app.config import reset_settings_cache
from app.core.loader import (
    KIND_TABLE,
    KIND_TEXT,
    LoadedDocument,
    Segment,
    compute_file_hash,
    load_document,
)
from app.core.splitter import split_document


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch):
    """splitter 通过 get_settings 读分块参数；测试不依赖真实密钥。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _doc(
    *segments: Segment,
    name: str = "table.pdf",
    file_hash: str = "testhash",
) -> LoadedDocument:
    doc = LoadedDocument(
        source_file=name,
        file_path=f"C:/tmp/{name}",
        doc_type="pdf",
        file_hash=file_hash,
    )
    doc.segments = list(segments)
    return doc


def _big_table(rows: int = 80) -> str:
    header = "| 指标 | 数值 |\n| --- | --- |"
    body = "\n".join(f"| row{i} | value{i} |" for i in range(rows))
    return f"{header}\n{body}"


def test_long_table_is_one_chunk() -> None:
    """超过 chunk_size 的表格也不能被切开。"""
    table = _big_table()
    assert len(table) > 500

    chunks = split_document(_doc(Segment(table, page=2, kind=KIND_TABLE)))

    assert len(chunks) == 1
    assert chunks[0].text == table
    assert chunks[0].kind == KIND_TABLE
    assert chunks[0].page == 2
    assert chunks[0].chunk_index == 0


def test_multiple_tables_keep_order_and_text() -> None:
    """同页/跨页多张表：顺序、文本、页码、chunk_index 都不能变。"""
    t1 = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    t2 = "| c | d |\n| --- | --- |\n| 3 | 4 |"

    chunks = split_document(
        _doc(
            Segment(t1, page=1, kind=KIND_TABLE),
            Segment("中间正文。", page=1, kind=KIND_TEXT),
            Segment(t2, page=2, kind=KIND_TABLE),
        )
    )

    table_chunks = [c for c in chunks if c.kind == KIND_TABLE]
    assert [c.text for c in table_chunks] == [t1, t2]
    assert [c.page for c in table_chunks] == [1, 2]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_table_text_is_not_altered() -> None:
    """表格首尾空白只会被 strip，正文内容一字不改。"""
    table = "  | a | b |\n| --- | --- |\n| 1 | 2 |  "
    chunks = split_document(_doc(Segment(table, page=1, kind=KIND_TABLE)))

    assert len(chunks) == 1
    assert chunks[0].text == table.strip()


def test_empty_table_is_skipped() -> None:
    chunks = split_document(_doc(Segment("   \n  ", page=1, kind=KIND_TABLE)))
    assert chunks == []


def test_text_still_splits_around_tables() -> None:
    """表格整表保留，不影响前后正文按 chunk_size 正常切分。"""
    long_text = "".join(f"第{i}句正文内容。" for i in range(60))
    table = _big_table(rows=20)

    chunks = split_document(
        _doc(
            Segment(long_text, page=1, kind=KIND_TEXT),
            Segment(table, page=1, kind=KIND_TABLE),
            Segment(long_text, page=2, kind=KIND_TEXT),
        )
    )

    table_chunks = [c for c in chunks if c.kind == KIND_TABLE]
    assert len(table_chunks) == 1
    assert table_chunks[0].text == table
    assert len([c for c in chunks if c.kind == KIND_TEXT]) > 1
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_metadata_matches_schema() -> None:
    """§6.2 字段齐全，chunk_id 与索引对应，同文档共享 created_at。"""
    chunks = split_document(
        _doc(
            Segment("第一段正文。", page=1, kind=KIND_TEXT),
            Segment("| a | b |\n| --- | --- |\n| 1 | 2 |", page=2, kind=KIND_TABLE),
        )
    )
    assert [c.chunk_id for c in chunks] == ["testhash-0", "testhash-1"]

    meta = chunks[0].to_metadata()
    assert set(meta) == {
        "source_file",
        "file_path",
        "file_hash",
        "doc_type",
        "page",
        "chunk_index",
        "chunk_id",
        "created_at",
        "kind",
    }
    assert meta["source_file"] == "table.pdf"
    assert meta["file_path"] == "C:/tmp/table.pdf"
    assert meta["file_hash"] == "testhash"
    assert meta["doc_type"] == "pdf"
    assert meta["page"] == 1
    assert meta["chunk_index"] == 0
    assert meta["chunk_id"] == "testhash-0"
    assert meta["kind"] == "text"

    assert len({c.created_at for c in chunks}) == 1
    datetime.fromisoformat(chunks[0].created_at)


def test_chunk_ids_unique_across_many_chunks() -> None:
    text = "".join(f"第{i}句用于测试 chunk_id 唯一性。" for i in range(120))
    chunks = split_document(_doc(Segment(text, page=1, kind=KIND_TEXT)))

    assert len(chunks) > 1
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids == [f"testhash-{i}" for i in range(len(chunks))]
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_file_hash_from_real_file(tmp_path) -> None:
    """真实文件：load_document 计算内容哈希，chunk 复用同一个值。"""
    content = "真实文件哈希测试。".encode("utf-8")
    path = tmp_path / "real.txt"
    path.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    assert compute_file_hash(path) == expected
    doc = load_document(path)
    assert doc.ok, doc.error
    assert doc.file_hash == expected

    chunks = split_document(doc)
    assert chunks
    assert all(c.file_hash == expected for c in chunks)
    assert [c.chunk_id for c in chunks] == [
        f"{expected}-{i}" for i in range(len(chunks))
    ]


def test_missing_hash_falls_back_for_manual_doc() -> None:
    """手工构造的文档没有 file_hash 且原文件不可读时，用路径哈希兜底。"""
    chunks = split_document(_doc(Segment("正文。", page=1), file_hash=""))

    assert chunks
    fallback = chunks[0].file_hash
    assert len(fallback) == 64
    assert chunks[0].chunk_id == f"{fallback}-0"
