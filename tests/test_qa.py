"""T-4.5 兜底话术与引用解析测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.llm import LLMResult
from app.core.qa import NO_ANSWER, QAService, parse_citations
from app.core.retriever import RetrievalResult, RetrievedChunk


def _source(name: str, page: int, text: str, score: float = 0.8) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{name}-{page}",
        text=text,
        file_name=name,
        page=page,
        kind="text",
        score=score,
        file_hash=f"hash-{name}",
        tenant_id="local",
    )


def _retrieval(*sources: RetrievedChunk) -> RetrievalResult:
    return RetrievalResult(
        query="问题",
        items=tuple(sources),
        elapsed_ms=3.0,
    )


class FakeRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.called: tuple[str, int | None] | None = None

    def retrieve(self, question: str, *, k: int | None = None) -> RetrievalResult:
        self.called = (question, k)
        return self.result


class FakeLLM:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.called = False

    def complete(self, messages, *, temperature=None) -> LLMResult:
        self.called = True
        return LLMResult(
            text=self.text,
            usage=None,
            elapsed_ms=1.0,
            model="fake",
            finish_reason="stop",
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(max_context_tokens=6000, temperature=0.2, top_k=5)


def test_no_context_falls_back_when_llm_has_no_citation() -> None:
    retriever = FakeRetriever(_retrieval())
    llm = FakeLLM("根据当前知识库无法确定。")
    service = QAService(retriever=retriever, llm=llm, settings=_settings())

    result = service.answer("库外问题")

    assert result.no_context is True
    assert result.answer == NO_ANSWER
    assert result.citations == ()
    assert llm.called is True
    assert result.llm is not None


def test_no_context_chat_marker_returns_greeting() -> None:
    retriever = FakeRetriever(_retrieval())
    llm = FakeLLM("[[CHAT]]你好！有什么关于知识库的问题，随时问我。")
    service = QAService(retriever=retriever, llm=llm, settings=_settings())

    result = service.answer("你好")

    assert result.chat_mode is True
    assert result.no_context is False
    assert result.answer == "你好！有什么关于知识库的问题，随时问我。"
    assert result.citations == ()


def test_parse_citations_maps_and_deduplicates() -> None:
    sources = (
        _source("a.pdf", 1, "A", score=0.9),
        _source("b.pdf", 2, "B", score=0.8),
    )

    citations = parse_citations("结论 A[2]，补充 B[1]，重复[2]，无效[9]", sources)

    assert [item.n for item in citations] == [2, 1]
    assert citations[0].file_name == "b.pdf"
    assert citations[0].page == 2
    assert citations[1].chunk_id == "a.pdf-1"
    assert citations[0].excerpt == "B"


def test_parse_citations_without_markers() -> None:
    sources = (_source("a.pdf", 1, "A"),)
    assert parse_citations("没有引用", sources) == ()
    assert parse_citations("[1]", ()) == ()


def test_qa_returns_answer_and_citations() -> None:
    retriever = FakeRetriever(_retrieval(_source("a.pdf", 3, "片段")))
    llm = FakeLLM("这是回答[1]。")
    service = QAService(retriever=retriever, llm=llm, settings=_settings())

    result = service.answer("问题", k=1)

    assert result.no_context is False
    assert result.answer == "这是回答[1]。"
    assert [item.n for item in result.citations] == [1]
    assert llm.called is True
    assert retriever.called == ("问题", 1)


def test_answer_without_citations_falls_back_to_fixed_text() -> None:
    retriever = FakeRetriever(_retrieval(_source("a.pdf", 3, "无关片段")))
    llm = FakeLLM("根据当前知识库无法确定。")
    service = QAService(retriever=retriever, llm=llm, settings=_settings())

    result = service.answer("库外问题")

    assert result.answer == NO_ANSWER
    assert result.citations == ()
    assert result.no_context is True
    assert result.llm is not None


def test_chat_marker_keeps_greeting_without_fallback() -> None:
    retriever = FakeRetriever(_retrieval(_source("a.pdf", 3, "无关片段")))
    llm = FakeLLM("[[CHAT]]你好！有什么关于知识库的问题，随时问我。")
    service = QAService(retriever=retriever, llm=llm, settings=_settings())

    result = service.answer("你好")

    assert result.chat_mode is True
    assert result.answer == "你好！有什么关于知识库的问题，随时问我。"
    assert result.citations == ()
    assert result.no_context is False


def test_empty_answer_raises() -> None:
    retriever = FakeRetriever(_retrieval(_source("a.pdf", 1, "片段")))
    service = QAService(retriever=retriever, llm=FakeLLM(""), settings=_settings())

    with pytest.raises(RuntimeError):
        service.answer("问题")


def test_empty_question_is_rejected() -> None:
    service = QAService(
        retriever=FakeRetriever(_retrieval()),
        llm=FakeLLM(),
        settings=_settings(),
    )
    with pytest.raises(ValueError):
        service.answer("   ")


def test_citation_to_dict() -> None:
    citation = parse_citations("[1]", (_source("a.pdf", 2, "片段"),))[0]
    assert citation.to_dict() == {
        "n": 1,
        "file_name": "a.pdf",
        "page": 2,
        "chunk_id": "a.pdf-2",
        "kind": "text",
        "score": 0.8,
        "excerpt": "片段",
    }
