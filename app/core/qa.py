"""问答编排与引用解析（对应 FR-09 / T-4.5）。

职责
----
- 检索 Top-K；
- 构造带编号上下文的提示词；
- 没有可用上下文时仍调用 LLM：问候/闲聊需要暖心回应，知识问题没有有效引用时再兜底；
- 生成后解析回答中的 ``[n]``，映射到实际来源；
- 为 T-4.6 CLI / T-5.4 SSE 提供稳定的 ``QAResult``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter
from typing import Sequence

from app.config import Settings, get_settings
from app.core.llm import DeepSeekLLM, LLMResult
from app.core.prompt import PromptBundle, build_prompt
from app.core.retriever import RetrievalResult, RetrievedChunk, Retriever

#: 没有检索到可用上下文时的固定话术（T-4.5 验收要求）
NO_ANSWER = "知识库中没有相关信息。"

#: 闲聊/问候回答的内部标记；模型按系统提示词第 8 条输出，服务端负责剥掉。
CHAT_MARKER = "[[CHAT]]"

_CITATION_RE = re.compile(r"\[(\d+)\]")


def extract_chat_mode(answer: str) -> tuple[str, bool]:
    """剥离闲聊标记，返回 ``(正文, 是否闲聊)``。

    闲聊回答按产品要求不带引用；但“无引用”原本会被统一替换成
    ``NO_ANSWER``。用一个显式标记区分“模型主动闲聊”和“知识回答漏了引用”，
    避免把暖心问候又变成“知识库中没有相关信息”。
    """
    text = str(answer or "")
    if CHAT_MARKER not in text:
        return text.strip(), False
    return text.replace(CHAT_MARKER, "").strip(), True


@dataclass(frozen=True, slots=True)
class Citation:
    """回答中实际引用的一条来源。"""

    n: int
    file_name: str
    page: int
    chunk_id: str
    kind: str
    score: float
    excerpt: str

    def to_dict(self) -> dict:
        """转成 API/CLI 可展示结构。"""
        return {
            "n": self.n,
            "file_name": self.file_name,
            "page": self.page,
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "score": round(self.score, 4),
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True, slots=True)
class QAResult:
    """一次问答的完整结果。"""

    question: str
    answer: str
    citations: tuple[Citation, ...]
    retrieval: RetrievalResult
    prompt: PromptBundle
    llm: LLMResult | None
    elapsed_ms: float
    no_context: bool = False
    chat_mode: bool = False


def parse_citations(answer: str, sources: tuple[RetrievedChunk, ...]) -> tuple[Citation, ...]:
    """解析回答中的 ``[n]``，按首次出现顺序返回有效引用。

    规则：
    - 只接受 ``1 <= n <= len(sources)`` 的编号；
    - 重复编号只保留一次；
    - 不存在的编号直接忽略，不报错；
    - 不改变回答正文，正文原样返回。
    """
    seen: set[int] = set()
    citations: list[Citation] = []

    for match in _CITATION_RE.finditer(answer or ""):
        n = int(match.group(1))
        if n in seen or not (1 <= n <= len(sources)):
            continue
        seen.add(n)
        source = sources[n - 1]
        citations.append(
            Citation(
                n=n,
                file_name=source.file_name,
                page=source.page,
                chunk_id=source.chunk_id,
                kind=source.kind,
                score=source.score,
                excerpt=source.text[:300],
            )
        )

    return tuple(citations)


class QAService:
    """检索 + 提示词 + 生成 + 引用解析。"""

    def __init__(
        self,
        retriever: Retriever | None = None,
        llm: DeepSeekLLM | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._retriever = retriever or Retriever(settings=self._settings)
        self._llm = llm or DeepSeekLLM(settings=self._settings)

    @property
    def retriever(self) -> Retriever:
        return self._retriever

    @property
    def llm(self) -> DeepSeekLLM:
        return self._llm

    @property
    def settings(self) -> Settings:
        return self._settings

    def close(self) -> None:
        """释放检索器持有的向量库连接。"""
        close = getattr(self._retriever, "close", None)
        if close is not None:
            close()

    def answer(
        self,
        question: str,
        *,
        k: int | None = None,
        history: Sequence[dict[str, str]] = (),
    ) -> QAResult:
        """完成一次问答。

        Args:
            question: 用户问题，不能为空。
            k: 检索条数；缺省用配置。

        Returns:
            ``QAResult``；没有可用上下文或没有有效引用时 ``no_context=True``、
            ``answer=NO_ANSWER``；问候/闲聊由 ``chat_mode=True`` 标记，保留无引用回答。
        """
        query = str(question or "").strip()
        if not query:
            raise ValueError("问题不能为空")

        started = perf_counter()
        retrieval = self._retriever.retrieve(query, k=k)
        prompt = build_prompt(
            query,
            retrieval.items,
            max_context_tokens=self._settings.max_context_tokens,
            history=history,
        )

        llm_result = self._llm.complete(
            prompt.messages,
            temperature=self._settings.temperature,
        )
        answer, chat_mode = extract_chat_mode(llm_result.text)
        if not answer:
            # 空正文通常是上游异常/被截断；不伪装成“知识库无信息”。
            raise RuntimeError("模型返回了空回答")

        if chat_mode:
            # 问候/闲聊：产品要求暖心回应且不标引用，不能走“无引用”兜底。
            return QAResult(
                question=query,
                answer=answer,
                citations=(),
                retrieval=retrieval,
                prompt=prompt,
                llm=llm_result,
                elapsed_ms=(perf_counter() - started) * 1000,
                no_context=False,
                chat_mode=True,
            )

        citations = parse_citations(answer, prompt.used_sources)
        if not citations:
            # 没有有效引用 = 没有可溯源依据。T-4.8 要求答案 100% 带来源，
            # 因此这里统一回退到固定话术，而不是把无出处的回答展示给用户。
            return QAResult(
                question=query,
                answer=NO_ANSWER,
                citations=(),
                retrieval=retrieval,
                prompt=prompt,
                llm=llm_result,
                elapsed_ms=(perf_counter() - started) * 1000,
                no_context=True,
            )

        return QAResult(
            question=query,
            answer=answer,
            citations=citations,
            retrieval=retrieval,
            prompt=prompt,
            llm=llm_result,
            elapsed_ms=(perf_counter() - started) * 1000,
            no_context=False,
        )


__all__ = [
    "CHAT_MARKER",
    "Citation",
    "NO_ANSWER",
    "QAResult",
    "QAService",
    "extract_chat_mode",
    "parse_citations",
]
