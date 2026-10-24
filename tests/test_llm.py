"""T-4.4 DeepSeek 流式客户端测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.llm import DeepSeekLLM, LLMError


def _chunk(content: str = "", *, reasoning: str | None = None, finish: str | None = None, usage=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeCompletions:
    def __init__(self, chunks=None, error: Exception | None = None) -> None:
        self.chunks = chunks or []
        self.error = error
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return iter(self.chunks)


def _llm(completions: FakeCompletions) -> DeepSeekLLM:
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    settings = SimpleNamespace(temperature=0.2, llm_model="deepseek-v4-flash")
    return DeepSeekLLM(settings=settings, client=fake_client)


def test_stream_chat_disables_thinking_by_default() -> None:
    fake = FakeCompletions([
        _chunk("你"),
        _chunk("好"),
        _chunk("", finish="stop"),
    ])
    llm = _llm(fake)

    deltas = list(llm.stream_chat([{"role": "user", "content": "hi"}]))

    assert "".join(item.text for item in deltas) == "你好"
    assert fake.kwargs["stream"] is True
    assert fake.kwargs["model"] == "deepseek-v4-flash"
    assert fake.kwargs["temperature"] == 0.2
    assert fake.kwargs["max_tokens"] == 1024
    assert fake.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_optional_thinking_mode_does_not_send_disabled() -> None:
    fake = FakeCompletions([_chunk("ok")])
    llm = _llm(fake)

    list(llm.stream_chat([{"role": "user", "content": "hi"}], enable_thinking=True))

    assert fake.kwargs["extra_body"] is None


def test_stream_passes_reasoning_content() -> None:
    fake = FakeCompletions([_chunk("", reasoning="思考中")])
    llm = _llm(fake)

    delta = list(llm.stream_chat([{"role": "user", "content": "hi"}]))[0]

    assert delta.reasoning == "思考中"


def test_complete_collects_text_and_usage() -> None:
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12)
    fake = FakeCompletions([
        _chunk("你"),
        _chunk("好"),
        SimpleNamespace(choices=[], usage=usage),
    ])
    llm = _llm(fake)

    result = llm.complete([{"role": "user", "content": "hi"}])

    assert result.text == "你好"
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
    }
    assert result.model == "deepseek-v4-flash"
    assert result.elapsed_ms >= 0


def test_empty_messages_is_rejected() -> None:
    llm = _llm(FakeCompletions([]))
    with pytest.raises(ValueError):
        list(llm.stream_chat([]))


def test_upstream_error_is_wrapped() -> None:
    llm = _llm(FakeCompletions(error=RuntimeError("boom")))

    with pytest.raises(LLMError):
        list(llm.stream_chat([{"role": "user", "content": "hi"}]))
