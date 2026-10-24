"""DeepSeek 流式生成客户端（对应 FR-08 / T-4.4）。

设计要点
--------
- 使用 OpenAI 兼容接口，``stream=True`` 逐块返回；
- **默认关闭思考模式**：``extra_body={"thinking": {"type": "disabled"}}``；
  官方文档：https://api-docs.deepseek.com/guides/thinking_mode/
- ``temperature=0.2``（可在 Settings 调整）；
- 客户端设置超时与有限重试；流式过程中不自动重试，避免重复输出；
- 错误统一收敛为 :class:`LLMError`，调用方决定如何展示/兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable, Iterator

from openai import OpenAI

from app.config import Settings, get_settings

#: 单次回答的默认输出上限
DEFAULT_MAX_TOKENS = 1024

#: 请求超时（秒）
REQUEST_TIMEOUT = 60.0

#: OpenAI SDK 的内置重试次数；只覆盖“请求尚未建立/首包前”的错误
MAX_RETRIES = 1


class LLMError(RuntimeError):
    """DeepSeek 调用失败。"""


@dataclass(frozen=True, slots=True)
class LLMDelta:
    """一个流式增量。"""

    text: str = ""
    reasoning: str | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class LLMResult:
    """非流式聚合结果。"""

    text: str
    usage: dict[str, int] | None
    elapsed_ms: float
    model: str
    finish_reason: str | None = None


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


class DeepSeekLLM:
    """DeepSeek OpenAI 兼容客户端。"""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
        model: str | None = None,
        enable_thinking: bool = False,
    ) -> None:
        self._settings = settings or get_settings()
        self._model = model or self._settings.llm_model
        self._enable_thinking = enable_thinking

        if client is not None:
            self._client = client
        else:
            key = api_key or self._settings.deepseek_api_key.get_secret_value()
            self._client = OpenAI(
                api_key=key,
                base_url=self._settings.deepseek_base_url,
                timeout=REQUEST_TIMEOUT,
                max_retries=MAX_RETRIES,
            )

    @property
    def model(self) -> str:
        """当前模型名。"""
        return self._model

    def stream_chat(
        self,
        messages: Iterable[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        enable_thinking: bool | None = None,
    ) -> Iterator[LLMDelta]:
        """流式生成，逐块 yield 文本/思考增量。

        Args:
            messages: OpenAI 格式消息列表。
            temperature: 覆盖默认温度；思考模式关闭时生效。
            max_tokens: 最大输出 token 数。
            enable_thinking: 覆盖实例默认值；默认 False。

        Raises:
            LLMError: 请求失败、上游超时、返回结构异常。
        """
        items = list(messages)
        if not items:
            raise ValueError("messages 不能为空")

        thinking = self._enable_thinking if enable_thinking is None else enable_thinking
        extra_body = {} if thinking else {"thinking": {"type": "disabled"}}
        temp = self._settings.temperature if temperature is None else temperature

        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=items,
                stream=True,
                temperature=temp,
                max_tokens=max_tokens,
                extra_body=extra_body or None,
            )
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                usage = _usage_dict(getattr(chunk, "usage", None))
                if not choices:
                    if usage:
                        yield LLMDelta(usage=usage)
                    continue

                choice = choices[0]
                delta = getattr(choice, "delta", None)
                text = str(getattr(delta, "content", "") or "")
                reasoning = getattr(delta, "reasoning_content", None)
                finish = getattr(choice, "finish_reason", None)
                if text or reasoning or finish or usage:
                    yield LLMDelta(
                        text=text,
                        reasoning=str(reasoning) if reasoning else None,
                        finish_reason=finish,
                        usage=usage,
                    )
        except Exception as exc:  # noqa: BLE001 —— 上游错误类型很多，统一收敛
            raise LLMError(
                f"DeepSeek 流式调用失败: {type(exc).__name__}: {exc}"
            ) from exc

    def complete(
        self,
        messages: Iterable[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        enable_thinking: bool | None = None,
    ) -> LLMResult:
        """收集流式输出，返回完整文本与耗时。

        适合 CLI / 测试；Web 层应直接迭代 :meth:`stream_chat` 做 SSE。
        """
        started = perf_counter()
        parts: list[str] = []
        usage: dict[str, int] | None = None
        finish: str | None = None

        for delta in self.stream_chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        ):
            if delta.text:
                parts.append(delta.text)
            if delta.usage:
                usage = delta.usage
            if delta.finish_reason:
                finish = delta.finish_reason

        return LLMResult(
            text="".join(parts),
            usage=usage,
            elapsed_ms=(perf_counter() - started) * 1000,
            model=self._model,
            finish_reason=finish,
        )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "DeepSeekLLM",
    "LLMDelta",
    "LLMError",
    "LLMResult",
]
