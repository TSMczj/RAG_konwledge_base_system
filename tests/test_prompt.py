"""T-4.2 / T-4.3 提示词与上下文预算测试。"""

from __future__ import annotations

import pytest

from app.core.prompt import (
    NO_CONTEXT,
    build_prompt,
    estimate_tokens,
)
from app.core.retriever import RetrievedChunk


def _source(
    name: str,
    text: str,
    *,
    page: int = 1,
    score: float = 0.8,
    kind: str = "text",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{name}-{page}",
        text=text,
        file_name=name,
        page=page,
        kind=kind,
        score=score,
        file_hash=f"hash-{name}",
        tenant_id="local",
    )


def test_prompt_has_system_rules_and_numbered_context() -> None:
    bundle = build_prompt(
        "Transformer 的核心结构是什么？",
        [
            _source("attention.pdf", "编码器和解码器堆叠。", page=2),
            _source("attention.pdf", "多头自注意力。", page=5),
        ],
        max_context_tokens=6000,
    )

    assert [message["role"] for message in bundle.messages] == ["system", "user"]
    system = bundle.messages[0]["content"]
    assert "只使用 <context>" in system
    assert "根据当前知识库无法确定" in system
    assert "[1] attention.pdf · 第 2 页 · 正文" in system
    assert "[2] attention.pdf · 第 5 页 · 正文" in system
    assert "问题：Transformer" in bundle.messages[1]["content"]
    assert len(bundle.used_sources) == 2
    assert bundle.used_sources[0].page == 2


def test_no_sources_uses_no_context() -> None:
    bundle = build_prompt("没有答案的问题", [], max_context_tokens=6000)

    assert NO_CONTEXT in bundle.context
    assert bundle.used_sources == ()
    assert bundle.dropped_sources == ()


def test_context_budget_drops_lowest_scores() -> None:
    long_text = "很长的上下文。" * 80
    sources = [
        _source("high.pdf", long_text, page=1, score=0.95),
        _source("mid.pdf", long_text, page=2, score=0.80),
        _source("low.pdf", long_text, page=3, score=0.40),
    ]

    bundle = build_prompt("问题", sources, max_context_tokens=800)

    assert bundle.used_sources
    assert bundle.dropped_sources
    assert bundle.used_sources[0].file_name == "high.pdf"
    assert all(item.file_name != "low.pdf" for item in bundle.used_sources)
    assert bundle.context_tokens <= 800


def test_context_numbering_is_continuous_after_drop() -> None:
    sources = [
        _source("high.pdf", "高相关。", score=0.95),
        _source("low.pdf", "低相关。" * 200, score=0.30),
        _source("mid.pdf", "中相关。", score=0.70),
    ]

    bundle = build_prompt("问题", sources, max_context_tokens=200)

    assert len(bundle.used_sources) >= 1
    for index, _ in enumerate(bundle.used_sources, start=1):
        assert f"[{index}]" in bundle.context


def test_estimate_tokens_is_conservative() -> None:
    assert estimate_tokens("中文") == 2
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0


def test_empty_question_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_prompt("   ", [], max_context_tokens=100)


def test_invalid_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        build_prompt("问题", [], max_context_tokens=0)
