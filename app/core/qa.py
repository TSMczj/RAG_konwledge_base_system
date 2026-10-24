"""问答编排与引用解析（对应 FR-09 / T-4.5）。

职责
----
- 检索 Top-K；
- 构造带编号上下文的提示词；
- 没有可用上下文时返回固定兜底话术，不调用 LLM；
- 生成后解析回答中的 ``[n]``，映射到实际来源；
- 为 T-4.6 CLI / T-5.4 SSE 提供稳定的 ``QAResult``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter

from app.config import Settings, get_settings
from app.core.llm import DeepSeekLLM, LLMResult
from app.core.prompt import PromptBundle, build_prompt
from app.core.retriever import RetrievalResult, RetrievedChunk, Retriever

#: 没有检索到可用上下文时的固定话术（T-4.5 验收要求）
NO_ANSWER = "知识库中没有相关信息。"

_CITATION_RE = re.compile(r"\[(\d+)\]")


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

    def answer(self, question: str, *, k: int | None = None) -> QAResult:
        """完成一次问答。

        Args:
            question: 用户问题，不能为空。
            k: 检索条数；缺省用配置。

        Returns:
            ``QAResult``；没有可用上下文时 ``no_context=True``、``answer=NO_ANSWER``，
            且不会调用 LLM。
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
        )

        if not prompt.used_sources:
            return QAResult(
                question=query,
                answer=NO_ANSWER,
                citations=(),
                retrieval=retrieval,
                prompt=prompt,
                llm=None,
                elapsed_ms=(perf_counter() - started) * 1000,
                no_context=True,
            )

        llm_result = self._llm.complete(
            prompt.messages,
            temperature=self._settings.temperature,
        )
        answer = llm_result.text.strip()
        if not answer:
            # 空正文通常是上游异常/被截断；不伪装成“知识库无信息”。
            raise RuntimeError("模型返回了空回答")

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


__all__ = ["Citation", "NO_ANSWER", "QAResult", "QAService", "parse_citations"]
