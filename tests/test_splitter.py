"""分块器的表格保护测试（T-2.2）。

只覆盖 T-2.2 的表格不变量：表格 Segment 必须整表成为一个 chunk，且文本不变。
完整的分块器测试（分隔符、overlap、配置覆盖等）按计划在 T-7.1 扩展。
"""

from __future__ import annotations

import pytest

from app.config import reset_settings_cache
from app.core.loader import KIND_TABLE, KIND_TEXT, LoadedDocument, Segment
from app.core.splitter import split_document


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch: pytest.MonkeyPatch):
    """splitter 通过 get_settings 读分块参数；测试不依赖真实密钥。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    reset_settings_cache()
    yield
    reset_settings_cache()


def _doc(*segments: Segment, name: str = "table.pdf") -> LoadedDocument:
    doc = LoadedDocument(source_file=name, file_path=f"C:/tmp/{name}", doc_type="pdf")
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
