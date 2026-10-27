"""T-5.4 聊天 SSE 接口测试（不访问真实模型）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import app.api.chat as chat_api
from app.core.llm import LLMDelta
from app.core.qa import NO_ANSWER
from app.core.retriever import RetrievalResult, RetrievedChunk
from app.main import app
from app.services.sessions import session_store
from fastapi.testclient import TestClient

client = TestClient(app)


class FakeRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result

    def retrieve(self, question, *, k=None):
        return self.result


class FakeLLM:
    def __init__(self, deltas: list[LLMDelta] | None = None, error: Exception | None = None) -> None:
        self.deltas = deltas or []
        self.error = error
        self.called = False

    def stream_chat(self, messages, *, temperature=None):
        self.called = True
        if self.error:
            raise self.error
        yield from self.deltas


class FakeService:
    def __init__(self, retriever, llm) -> None:
        self.retriever = retriever
        self.llm = llm
        self.settings = SimpleNamespace(max_context_tokens=6000, temperature=0.2)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _source() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        text="片段",
        file_name="a.pdf",
        page=3,
        kind="text",
        score=0.8,
    )


def _retrieval(*items: RetrievedChunk) -> RetrievalResult:
    return RetrievalResult(query="问题", items=tuple(items), elapsed_ms=5.0)


def _patch(monkeypatch, service: FakeService) -> None:
    monkeypatch.setattr(chat_api, "_service", lambda: service)


def _events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            events.append((event, json.loads(line[6:])))
            event = None
    return events


def test_chat_streams_sources_delta_done(monkeypatch) -> None:
    service = FakeService(
        FakeRetriever(_retrieval(_source())),
        FakeLLM([LLMDelta(text="答案"), LLMDelta(text="[1]")]),
    )
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "问题"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names == ["sources", "delta", "delta", "done"]
    assert events[0][1]["sources"][0]["n"] == 1
    assert events[0][1]["sources"][0]["file_name"] == "a.pdf"
    assert "".join(payload["text"] for name, payload in events if name == "delta") == "答案[1]"
    assert events[-1][1]["answer"] == "答案[1]"
    assert service.closed is True


def test_chat_multi_turn_rewrites_follow_up(monkeypatch) -> None:
    session_store.clear("sess_multi_turn_test")

    class RecordingRetriever(FakeRetriever):
        def __init__(self, result):
            super().__init__(result)
            self.queries = []

        def retrieve(self, question, *, k=None):
            self.queries.append(question)
            return self.result

    class MultiTurnLLM:
        def __init__(self):
            self.calls = []

        def stream_chat(self, messages, *, temperature=None, max_tokens=None):
            self.calls.append(messages)
            if "检索查询改写器" in messages[0]["content"]:
                yield LLMDelta(text="BERT 的消融实验")
            else:
                yield LLMDelta(text="消融实验验证了预训练任务的重要性。[1]")

    llm = MultiTurnLLM()
    retriever = RecordingRetriever(_retrieval(_source()))
    service = FakeService(retriever, llm)
    _patch(monkeypatch, service)

    first = client.post(
        "/api/v1/chat",
        json={"question": "BERT 的预训练任务是什么？", "session_id": "sess_multi_turn_test"},
    )
    second = client.post(
        "/api/v1/chat",
        json={"question": "那它的消融实验呢？", "session_id": "sess_multi_turn_test"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    second_events = _events(second.text)
    assert second_events[-1][1]["session_id"] == "sess_multi_turn_test"
    assert second_events[-1][1]["rewritten_query"] == "BERT 的消融实验"
    assert retriever.queries[0] == "BERT 的预训练任务是什么？"
    assert retriever.queries[1] == "BERT 的消融实验"
    assert len(session_store.history("sess_multi_turn_test")) == 2
    session_store.clear("sess_multi_turn_test")


def test_chat_no_context_falls_back_after_generation(monkeypatch) -> None:
    llm = FakeLLM([LLMDelta(text="根据当前知识库无法确定。")])
    service = FakeService(FakeRetriever(_retrieval()), llm)
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "库外问题"})

    events = _events(response.text)
    assert [name for name, _ in events] == ["sources", "delta", "done"]
    assert events[0][1]["sources"] == []
    assert events[1][1]["text"] == "根据当前知识库无法确定。"
    assert events[2][1]["answer"] == NO_ANSWER
    assert events[2][1]["fallback"] is True
    assert events[2][1]["chat_mode"] is False
    assert llm.called is True


def test_chat_no_context_greeting_uses_chat_mode(monkeypatch) -> None:
    llm = FakeLLM([LLMDelta(text="[[CHAT]]你好！有什么关于知识库的问题，随时问我。")])
    service = FakeService(FakeRetriever(_retrieval()), llm)
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "你好"})

    events = _events(response.text)
    done = events[-1][1]
    assert done["answer"] == "你好！有什么关于知识库的问题，随时问我。"
    assert done["fallback"] is False
    assert done["chat_mode"] is True


def test_chat_without_citations_falls_back_in_done(monkeypatch) -> None:
    service = FakeService(
        FakeRetriever(_retrieval(_source())),
        FakeLLM([LLMDelta(text="没有引用")]),
    )
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "问题"})

    events = _events(response.text)
    assert events[-1][1]["answer"] == NO_ANSWER
    assert events[-1][1]["fallback"] is True


def test_chat_mode_keeps_greeting_and_hides_marker(monkeypatch) -> None:
    service = FakeService(
        FakeRetriever(_retrieval(_source())),
        FakeLLM([
            LLMDelta(text="[[CH"),
            LLMDelta(text="AT]]你好！有什么关于知识库的问题，随时问我。"),
        ]),
    )
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "你好"})

    events = _events(response.text)
    deltas = "".join(payload["text"] for name, payload in events if name == "delta")
    done = events[-1][1]
    assert "[[CHAT]]" not in deltas
    assert "[[CH" not in deltas
    assert deltas.startswith("你好！")
    assert done["chat_mode"] is True
    assert done["fallback"] is False
    assert done["answer"].startswith("你好！")
    assert "[[CHAT]]" not in done["answer"]


def test_chat_stream_error_event(monkeypatch) -> None:
    service = FakeService(
        FakeRetriever(_retrieval(_source())),
        FakeLLM(error=RuntimeError("boom")),
    )
    _patch(monkeypatch, service)

    response = client.post("/api/v1/chat", json={"question": "问题"})

    events = _events(response.text)
    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "INTERNAL"
    assert service.closed is True
