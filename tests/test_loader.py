"""PDF 表格过滤规则测试（T-1.4 / T-3.6 真实文档回归）。

这些用例来自真实文档的失败模式：
- attention 可视化图被切成 33–59 列的单字符“表格”；
- TSLA 财报是 22–31 列、数字占比很高的真实宽表；
- lm358 数据手册有很小的稀疏表格，不能误删。
"""

from __future__ import annotations

from app.core.loader import _is_useful_table, _join_wrapped_lines


def test_attention_visualization_is_rejected() -> None:
    words = "It is in this spirit that a majority of American governments".split()
    rows: list[list[str | None]] = []
    for index in range(4):
        row = [""] * 40
        row[index] = words[index]
        rows.append(row)

    assert not _is_useful_table(rows)


def test_wide_financial_table_is_kept() -> None:
    rows: list[list[str | None]] = [["Item"] + ["Col"] * 21]
    for index in range(3):
        row = [f"Row{index}"] + [""] * 15 + [str(index)] * 6
        rows.append(row)

    assert _is_useful_table(rows)


def test_wide_non_numeric_table_is_rejected() -> None:
    rows = [["word"] * 27 for _ in range(3)]
    assert not _is_useful_table(rows)


def test_small_sparse_real_table_is_kept() -> None:
    rows = [
        ["", "", "0.25", "C", "A", "B"],
        ["", "", "", "", "", ""],
    ]
    assert _is_useful_table(rows)


def test_too_few_rows_or_columns_are_rejected() -> None:
    assert not _is_useful_table([["a", "b", "c"]])
    assert not _is_useful_table([["a"], ["b"], ["c"]])


def test_join_wrapped_english_lines() -> None:
    assert _join_wrapped_lines("Hello\nworld") == "Hello world"
    assert _join_wrapped_lines("pre-\ntrained model") == "pre-trained model"


def test_join_wrapped_chinese_lines() -> None:
    assert _join_wrapped_lines("第一行\n第二行") == "第一行第二行"
    assert _join_wrapped_lines("个人信息处理者\n应当提供便捷方式。") == "个人信息处理者应当提供便捷方式。"


def test_join_wrapped_keeps_paragraph_breaks() -> None:
    assert _join_wrapped_lines("para one\nline two\n\npara two") == "para one line two\n\npara two"
