"""T-4.7 检索评估脚本测试。"""

from __future__ import annotations

from types import SimpleNamespace

import scripts.eval_retrieval as eval_module
from app.core.retriever import RetrievalResult, RetrievedChunk


class FakeRetriever:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def retrieve(self, question, *, k=None):
        self.calls.append((question, k))
        files = self.mapping[question]
        items = tuple(
            RetrievedChunk(
                chunk_id=f"{name}-0",
                text="片段",
                file_name=name,
                page=1,
                kind="text",
                score=0.8 - index * 0.01,
            )
            for index, name in enumerate(files)
        )
        return RetrievalResult(query=question, items=items, elapsed_ms=10.0 + len(items))


def test_percentile() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert eval_module._percentile(values, 0.5) == 25.0
    assert eval_module._percentile([7.0], 0.95) == 7.0
    assert eval_module._percentile([], 0.5) == 0.0


def test_evaluate_hit_and_miss() -> None:
    retriever = FakeRetriever({
        "问题一": ["a.pdf", "b.pdf"],
        "问题二": ["b.pdf", "c.pdf"],
    })
    questions = [
        {"id": "Q1", "question": "问题一", "expected_sources": ["a.pdf"]},
        {"id": "Q2", "question": "问题二", "expected_sources": ["a.pdf"]},
    ]

    report = eval_module.evaluate(questions, k=2, retriever=retriever)

    assert report["total"] == 2
    assert report["hits"] == 1
    assert report["hit_rate"] == 0.5
    assert report["records"][0]["rank"] == 1
    assert report["records"][1]["rank"] is None
    assert retriever.calls == [("问题一", 2), ("问题二", 2)]


def test_evaluate_accepts_string_expected_source() -> None:
    retriever = FakeRetriever({"问题": ["a.pdf"]})
    report = eval_module.evaluate(
        [{"question": "问题", "expected_sources": "a.pdf"}],
        k=1,
        retriever=retriever,
    )
    assert report["hit_rate"] == 1.0
