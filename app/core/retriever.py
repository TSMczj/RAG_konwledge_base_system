"""相似度检索器（对应 FR-06 / T-4.1）。

职责
----
- 问题文本向量化：由 ``QwenEmbedder.embed_query`` 使用 ``text_type="query"``；
- 在 Chroma 中取 Top-K；
- 把 Chroma 的 cosine 距离转换为对 UI/提示词更友好的相似度分数；
- 输出文本、分数、文件名、页码、chunk_id、kind 等溯源字段。

为什么不在 API 层直接拼 Chroma 返回值
------------------------------------
Chroma 返回 ``(Document, distance)``，distance 越小越相似；而 UI/契约需要
``score`` 越大越相关。这一层统一转换，并固定字段名，避免每个调用方各写一套。
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from langchain_core.documents import Document

from app.config import Settings, get_settings
from app.context import RequestContext, local_context
from app.core.vectorstore import VectorStore
from app.logging_config import logger


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """一个检索结果。"""

    chunk_id: str
    text: str
    file_name: str
    page: int
    kind: str
    score: float
    file_hash: str = ""
    tenant_id: str = "local"

    def to_source(self, n: int) -> dict:
        """转成 API 契约里的 ``sources[n]`` 结构。"""
        return {
            "n": n,
            "document_id": "",  # Phase 0 还没有独立 document_id；服务器阶段补
            "file_name": self.file_name,
            "page": self.page,
            "chunk_id": self.chunk_id,
            "kind": self.kind,
            "score": round(self.score, 4),
            "excerpt": self.text[:300],
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """一次检索的完整结果与耗时。"""

    query: str
    items: tuple[RetrievedChunk, ...]
    elapsed_ms: float

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def sources(self) -> list[dict]:
        """按 1..N 生成来源列表，供 SSE / 提示词使用。"""
        return [item.to_source(n) for n, item in enumerate(self.items, start=1)]


class Retriever:
    """问题 → Top-K 片段。"""

    def __init__(
        self,
        store: VectorStore | None = None,
        settings: Settings | None = None,
        context: RequestContext | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._context = context or local_context()
        self._store = store or VectorStore(settings=self._settings)

    def close(self) -> None:
        """关闭底层向量库（API 请求结束时调用）。"""
        close = getattr(self._store, "close", None)
        if close is not None:
            close()

    def retrieve(
        self,
        question: str,
        *,
        k: int | None = None,
    ) -> RetrievalResult:
        """检索与问题最相关的片段。

        Args:
            question: 用户问题，不能为空。
            k: 返回条数；缺省用配置 ``TOP_K``，必须 > 0。

        Returns:
            ``RetrievalResult``，items 按相似度从高到低排序，分数在 0~1。

        Raises:
            ValueError: 问题为空或 k <= 0。
        """
        query = str(question or "").strip()
        if not query:
            raise ValueError("检索问题不能为空")

        limit = self._settings.top_k if k is None else int(k)
        if limit <= 0:
            raise ValueError("k 必须大于 0")

        started = perf_counter()
        raw = self._store.query(query, k=limit)
        elapsed_ms = (perf_counter() - started) * 1000

        items: list[RetrievedChunk] = []
        for document, distance in raw[:limit]:
            items.append(self._to_item(document, distance))

        # Chroma 已按距离升序返回；这里再显式排序，避免未来换 adapter 改变顺序。
        items.sort(key=lambda item: item.score, reverse=True)

        # 最低相似度阈值：低于阈值的片段不进入上下文。
        # 问候/闲聊通常只有 0.1~0.2，过滤后上下文为空，再交给提示词第 7 条。
        min_score = float(getattr(self._settings, "min_score", 0.0) or 0.0)
        before = len(items)
        if min_score > 0:
            items = [item for item in items if item.score >= min_score]
        dropped = before - len(items)

        result = RetrievalResult(query=query, items=tuple(items), elapsed_ms=elapsed_ms)
        logger.debug(
            "检索完成: k={} 命中={} 过滤={}（阈值 {:.2f}）耗时={:.1f}ms",
            limit, len(result), dropped, min_score, elapsed_ms,
        )
        return result

    def _to_item(self, document: Document, distance: float) -> RetrievedChunk:
        """把 Chroma 结果转成稳定字段；容错缺失 metadata。"""
        meta = document.metadata or {}
        try:
            page = int(meta.get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0

        # cosine distance ∈ [0, 2]；常见范围 [0,1]；clamp 到 [0,1] 便于展示。
        score = max(0.0, min(1.0, 1.0 - float(distance)))

        return RetrievedChunk(
            chunk_id=str(meta.get("chunk_id", "")),
            text=document.page_content,
            file_name=str(meta.get("source_file", "未知文件")),
            page=page,
            kind=str(meta.get("kind", "text")),
            score=score,
            file_hash=str(meta.get("file_hash", "")),
            tenant_id=str(meta.get("tenant_id", self._context.tenant_id)),
        )


__all__ = ["RetrievedChunk", "RetrievalResult", "Retriever"]
