"""多轮查询改写（T-6.2 / FR-10）。

只在有历史且当前问题看起来是追问时调用 LLM；失败时回退原问题，
检索不应因为改写失败而整体失败。
"""

from __future__ import annotations

import re
from typing import Iterable

from app.logging_config import logger
from app.services.sessions import Turn

_FOLLOW_UP_RE = re.compile(
    r"(它|他|她|其|该|这个|那个|这些|那些|上面|前面|刚才|这种|那样|那么|呢|还|再)"
)
_REWRITE_SYSTEM = """你是检索查询改写器。请把用户当前问题结合对话历史改写成一个独立、完整、可检索的问题。
要求：
1. 只输出改写后的问题，不要解释、不要加引号、不要加“独立问题：”前缀。
2. 保留历史中的专有名词、文件名、型号和数字。
3. 如果当前问题已经独立完整，原样输出。
"""


def needs_rewrite(question: str) -> bool:
    """判断当前问题是否像依赖上文的追问。"""
    query = str(question or "").strip()
    if not query:
        return False
    if _FOLLOW_UP_RE.search(query):
        return True
    return len(query) <= 12


def _history_messages(history: Iterable[Turn], limit: int) -> list[dict[str, str]]:
    turns = list(history)[-max(1, limit):]
    messages: list[dict[str, str]] = []
    for turn in turns:
        answer = turn.answer[:300].replace("\n", " ")
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": answer})
    return messages


class QueryRewriter:
    """基于 DeepSeek 的轻量查询改写器。"""

    def __init__(self, llm, *, max_history_turns: int = 5) -> None:
        self._llm = llm
        self._max_history_turns = max_history_turns

    def rewrite(self, question: str, history: Iterable[Turn]) -> str:
        original = str(question or "").strip()
        turns = list(history)
        if not original or not turns or not needs_rewrite(original):
            return original

        history_block = _history_messages(turns, self._max_history_turns)
        messages = [
            {"role": "system", "content": _REWRITE_SYSTEM},
            *history_block,
            {
                "role": "user",
                "content": f"当前问题：{original}\n\n只输出改写后的独立问题。",
            },
        ]
        try:
            parts: list[str] = []
            for delta in self._llm.stream_chat(
                messages,
                temperature=0.0,
                max_tokens=128,
            ):
                if delta.text:
                    parts.append(delta.text)
            rewritten = "".join(parts).strip().strip('"“”').strip()
            rewritten = re.sub(r"^(独立问题|改写后的问题)[：:]\s*", "", rewritten)
            rewritten = rewritten.splitlines()[0].strip() if rewritten else ""
            if not rewritten or len(rewritten) > 300:
                return original
            return rewritten
        except Exception as exc:  # noqa: BLE001 —— 改写失败必须回退
            logger.warning("查询改写失败，使用原问题: {}: {}", type(exc).__name__, exc)
            return original


__all__ = ["QueryRewriter", "needs_rewrite"]
