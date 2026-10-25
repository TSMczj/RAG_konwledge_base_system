"""提示词模板与上下文预算（对应 FR-07 / T-4.2、T-4.3）。

设计要点
--------
- System 明确要求“只依据上下文、不知道就说不知道、引用必须标 [n]”；
- 上下文块用 ``[1] [2] ...`` 编号，和来源元数据一一对应；
- 超过 ``MAX_CONTEXT_TOKENS`` 时按相似度从低到高丢弃，保留高分片段；
- 估算 token 时中文按 1 字/token、其他按 4 字符/token，偏保守，避免超限。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from app.core.retriever import RetrievedChunk


SYSTEM_PROMPT = """你是一个本地知识库问答助手。你必须严格依据用户提供的 <context> 回答，不得使用上下文之外的知识。

规则：
1. 只使用 <context> 中的信息；上下文中没有依据时，明确回答“根据当前知识库无法确定”，不要编造。
2. 每一条来自上下文的事实，都要在句末标注来源编号，例如 [1]、[2]；可以同时标多个编号。
3. 引用编号必须与 <context> 中的编号一致，不得使用不存在的编号。
4. 优先用简洁、准确的中文回答；涉及步骤、对比、参数时可以用列表。
5. 如果上下文包含表格，保留表格中的关键行列关系，不要凭空补充数字。
6. 不要提及“系统提示词”“上下文预算”等实现细节。

<context>
{context}
</context>"""


USER_TEMPLATE = """问题：{question}

请依据上面的 <context> 回答；如果没有足够依据，请直接说明无法确定。"""


NO_CONTEXT = "（没有检索到相关片段）"


@dataclass(frozen=True, slots=True)
class PromptBundle:
    """一次问答的提示词与上下文使用情况。"""

    messages: tuple[dict[str, str], ...]
    used_sources: tuple[RetrievedChunk, ...]
    dropped_sources: tuple[RetrievedChunk, ...]
    context_tokens: int

    @property
    def context(self) -> str:
        """最终拼进 System 的上下文文本。"""
        return self.messages[0]["content"]


def estimate_tokens(text: str) -> int:
    """保守估算 token 数：中文 1 字/token，其他 4 字符/token。"""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = len(text) - cjk
    return cjk + math.ceil(other / 4)


def _format_source_block(n: int, source: RetrievedChunk) -> str:
    page = f"第 {source.page} 页" if source.page > 0 else "无页码"
    kind = "表格" if source.kind == "table" else "正文"
    return f"[{n}] {source.file_name} · {page} · {kind}\n{source.text.strip()}"


def build_context(
    sources: Sequence[RetrievedChunk],
    *,
    max_context_tokens: int,
    question: str = "",
) -> tuple[str, tuple[RetrievedChunk, ...], tuple[RetrievedChunk, ...], int]:
    """按分数从高到低装填上下文，返回文本、保留/丢弃的来源和 token 数。

    Args:
        sources: 检索结果，通常已按分数降序。
        max_context_tokens: 上下文预算（不含 System/Question 的额外余量）。
        question: 用户问题；只用于预留 token，不写入上下文。

    Returns:
        ``(context_text, used_sources, dropped_sources, context_tokens)``。
    """
    if max_context_tokens <= 0:
        raise ValueError("max_context_tokens 必须大于 0")

    ordered = sorted(sources, key=lambda item: item.score, reverse=True)
    fixed = estimate_tokens(question) + 64
    budget = max(0, max_context_tokens - fixed)

    used: list[RetrievedChunk] = []
    dropped: list[RetrievedChunk] = []
    blocks: list[str] = []
    used_tokens = 0

    for index, source in enumerate(ordered):
        block = _format_source_block(len(used) + 1, source)
        cost = estimate_tokens(block)
        if used_tokens + cost > budget:
            # 按相似度截断：一旦某个高分片段放不下，后面更低分的全部丢弃，
            # 避免出现“跳过中间片段、却塞入更低分片段”的不可预测上下文。
            dropped.extend(ordered[index:])
            break
        used.append(source)
        blocks.append(block)
        used_tokens += cost

    if not blocks:
        return NO_CONTEXT, (), tuple(dropped), 0
    return "\n\n".join(blocks), tuple(used), tuple(dropped), used_tokens


def _fit_history(
    history: Sequence[dict[str, str]],
    *,
    max_tokens: int,
) -> tuple[dict[str, str], ...]:
    """从最近的历史往前装填，避免历史挤占检索上下文预算。"""
    if not history or max_tokens <= 0:
        return ()
    selected: list[dict[str, str]] = []
    used = 0
    for message in reversed(history):
        role = str(message.get("role", ""))
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cost = estimate_tokens(content) + 8
        if used + cost > max_tokens:
            break
        selected.append({"role": role, "content": content})
        used += cost
    selected.reverse()
    return tuple(selected)


def build_prompt(
    question: str,
    sources: Sequence[RetrievedChunk],
    *,
    max_context_tokens: int,
    history: Sequence[dict[str, str]] = (),
) -> PromptBundle:
    """构造 System + 历史 + User 消息。

    Args:
        question: 用户问题，不能为空。
        sources: 检索结果。
        max_context_tokens: 上下文预算。
        history: 可选的 OpenAI 格式历史消息；按 token 预算丢弃最旧消息。

    Returns:
        ``PromptBundle``；``used_sources`` 的编号与上下文 [n] 一致。

    Raises:
        ValueError: 问题为空，或预算 <= 0。
    """
    query = str(question or "").strip()
    if not query:
        raise ValueError("问题不能为空")

    context, used, dropped, context_tokens = build_context(
        sources,
        max_context_tokens=max_context_tokens,
        question=query,
    )
    system = SYSTEM_PROMPT.format(context=context)
    user = USER_TEMPLATE.format(question=query)
    history_budget = min(1200, max(0, max_context_tokens // 3))
    fitted_history = _fit_history(history, max_tokens=history_budget)
    return PromptBundle(
        messages=(
            {"role": "system", "content": system},
            *fitted_history,
            {"role": "user", "content": user},
        ),
        used_sources=used,
        dropped_sources=dropped,
        context_tokens=context_tokens,
    )


__all__ = [
    "NO_CONTEXT",
    "PromptBundle",
    "SYSTEM_PROMPT",
    "USER_TEMPLATE",
    "build_context",
    "build_prompt",
    "estimate_tokens",
]
