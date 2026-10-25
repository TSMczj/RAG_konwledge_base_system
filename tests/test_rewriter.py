"""T-6.2 查询改写测试（不访问真实模型）。"""

from __future__ import annotations

from app.core.llm import LLMDelta
from app.core.rewriter import QueryRewriter, needs_rewrite
from app.services.sessions import Turn


class FakeLLM:
    def __init__(self, text: str = "BERT 的消融实验") -> None:
        self.text = text
        self.calls = 0

    def stream_chat(self, messages, *, temperature=None, max_tokens=None):
        self.calls += 1
        for char in self.text:
            yield LLMDelta(text=char)


def test_needs_rewrite_detects_follow_up() -> None:
    assert needs_rewrite("那它的消融实验呢？")
    assert needs_rewrite("它有什么结论？")
    assert not needs_rewrite("Transformer 的核心结构是什么？")


def test_rewrite_uses_history_and_returns_standalone_question() -> None:
    llm = FakeLLM("BERT 的消融实验")
    rewriter = QueryRewriter(llm)
    history = [Turn(question="BERT 的预训练任务是什么？", answer="MLM 和 NSP。")]
    assert rewriter.rewrite("那它的消融实验呢？", history) == "BERT 的消融实验"
    assert llm.calls == 1


def test_rewrite_is_skipped_without_history_or_follow_up() -> None:
    llm = FakeLLM()
    rewriter = QueryRewriter(llm)
    assert rewriter.rewrite("那它呢？", []) == "那它呢？"
    assert rewriter.rewrite("Transformer 的核心结构是什么？", [Turn("旧问题", "旧回答")]) == "Transformer 的核心结构是什么？"
    assert llm.calls == 0


def test_rewrite_failure_falls_back_to_original() -> None:
    class Broken:
        def stream_chat(self, *args, **kwargs):
            raise RuntimeError("boom")

    original = "那它的消融实验呢？"
    assert QueryRewriter(Broken()).rewrite(original, [Turn("BERT", "回答")]) == original
