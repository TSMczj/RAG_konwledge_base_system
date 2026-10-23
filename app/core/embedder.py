"""Qwen 文本嵌入客户端（对应 FR-04 向量化与入库 / T-3.1）。

职责
----
把一批文本转成 1024 维向量，供 Chroma 入库（T-3.2）与查询向量化（T-4.1）：

- 模型、维度、批量大小来自配置（``text-embedding-v4`` / 1024 / ≤10）；
- 百炼接口单次最多 10 条，按 ``EMBEDDING_BATCH_SIZE`` 自动分批；
- 连接 10s / 读取 60s 超时；失败最多尝试 3 次，指数退避；
- 返回顺序与输入严格一致，调用方不需要自己排序。

为什么用 OpenAI SDK 而不是 LangChain Embeddings
----------------------------------------------
百炼的批量上限、超时与重试策略需要精确控制；直连 SDK 比再包一层更透明。
向量库侧仍按 A 方案使用 LangChain 组件（T-3.2）。

注意
----
这些常量是接口约束 / 固定策略，不是业务可调参数；业务可调项在 ``.env``。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

import httpx
import openai
from openai import OpenAI

from app.config import Settings, get_settings
from app.logging_config import logger

#: 百炼嵌入接口的硬限制：单次最多 10 条（与 config.EMBEDDING_BATCH_LIMIT 一致）
EMBEDDING_MAX_BATCH = 10

#: 最多尝试 3 次（含首次）；指数退避 1s → 2s
EMBEDDING_MAX_ATTEMPTS = 3
EMBEDDING_RETRY_BASE_SECONDS = 1.0

#: 连接 10s / 读取 60s（NFR-2）
EMBEDDING_CONNECT_TIMEOUT = 10.0
EMBEDDING_READ_TIMEOUT = 60.0


class EmbeddingError(RuntimeError):
    """嵌入接口调用失败（重试耗尽、返回条数或维度不合法）。"""


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试：网络 / 限流 / 5xx 重试，参数与鉴权错误不重试。"""
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code >= 500
    return False


class QwenEmbedder:
    """阿里云百炼 ``text-embedding-v4`` 客户端。"""

    def __init__(
        self,
        settings: Settings | None = None,
        client: Any | None = None,
    ) -> None:
        """构造客户端。

        Args:
            settings: 配置；缺省时用 :func:`app.config.get_settings`。
            client: 可注入的 OpenAI 兼容客户端（测试用），必须提供
                ``embeddings.create`` 方法。
        """
        self._settings = settings or get_settings()
        self._model = self._settings.embedding_model
        self._dim = self._settings.embedding_dim
        # 配置层已校验 ≤10；这里再兜一层，避免有人绕过 pydantic 直接构造 Settings
        self._batch_size = min(self._settings.embedding_batch_size, EMBEDDING_MAX_BATCH)
        self._client = client or OpenAI(
            api_key=self._settings.dashscope_api_key.get_secret_value(),
            base_url=self._settings.dashscope_base_url,
            timeout=httpx.Timeout(
                EMBEDDING_READ_TIMEOUT,
                connect=EMBEDDING_CONNECT_TIMEOUT,
            ),
            # 重试由本类统一控制；SDK 再重试一次会让退避时间不可预期
            max_retries=0,
        )

    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self._dim

    @property
    def model(self) -> str:
        """嵌入模型名。"""
        return self._model

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        text_type: str | None = None,
    ) -> list[list[float]]:
        """批量向量化文本，返回顺序与输入一致。

        Args:
            texts: 文本序列；不能包含空串。
            text_type: 可选，百炼的 ``text_type``（``"query"`` / ``"document"``）。
                默认不传，避免兼容端点不支持时直接 400；T-4.1 实测后再启用。

        Returns:
            与 ``texts`` 等长的向量列表，每个向量长度为 :attr:`dimension`。

        Raises:
            ValueError: 输入为空串或不是字符串。
            EmbeddingError: 接口重试耗尽、返回条数或维度不符。
        """
        items = list(texts)
        if not items:
            return []

        for index, text in enumerate(items):
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"第 {index} 条嵌入文本为空或不是字符串")

        batches = [
            items[start:start + self._batch_size]
            for start in range(0, len(items), self._batch_size)
        ]
        vectors: list[list[float]] = []

        for batch_no, batch in enumerate(batches, start=1):
            logger.debug(
                "嵌入批次 {}/{}: {} 条（模型 {}）",
                batch_no, len(batches), len(batch), self._model,
            )
            vectors.extend(
                self._embed_batch(batch, batch_no, len(batches), text_type)
            )

        logger.debug("嵌入完成: {} 条 -> {} 批", len(items), len(batches))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """为查询生成向量（T-4.1 使用；``text_type="query"`` 需实测端点支持）。"""
        return self.embed_texts([text], text_type="query")[0]

    def _embed_batch(
        self,
        batch: list[str],
        batch_no: int,
        batch_count: int,
        text_type: str | None,
    ) -> list[list[float]]:
        """嵌入一个批次，内部负责重试与退避。"""
        last_exc: Exception | None = None

        for attempt in range(1, EMBEDDING_MAX_ATTEMPTS + 1):
            try:
                return self._request(batch, text_type)
            except Exception as exc:  # noqa: BLE001 —— 需要按异常类型决定是否重试
                # 返回条数/维度不合法属于确定性错误，重试没有意义，直接抛出
                if isinstance(exc, EmbeddingError):
                    raise
                last_exc = exc
                if not _is_retryable(exc) or attempt == EMBEDDING_MAX_ATTEMPTS:
                    raise EmbeddingError(
                        f"嵌入失败（批次 {batch_no}/{batch_count}，{len(batch)} 条，"
                        f"第 {attempt} 次尝试）: {type(exc).__name__}: {exc}"
                    ) from exc

                delay = EMBEDDING_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "嵌入批次 {}/{} 第 {} 次失败，{:.0f}s 后重试: {}",
                    batch_no, batch_count, attempt, delay, type(exc).__name__,
                )
                time.sleep(delay)

        # 理论到不了：循环内要么 return，要么 raise
        raise EmbeddingError(
            f"嵌入失败（批次 {batch_no}/{batch_count}，{len(batch)} 条）"
        ) from last_exc

    def _request(
        self,
        batch: list[str],
        text_type: str | None,
    ) -> list[list[float]]:
        """发一次嵌入请求并校验返回条数与维度。"""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": batch,
            "dimensions": self._dim,
        }
        if text_type:
            kwargs["extra_body"] = {"text_type": text_type}

        response = self._client.embeddings.create(**kwargs)
        # 不依赖服务端返回顺序：按 index 排序，保证与输入一一对应
        data = sorted(response.data, key=lambda item: item.index)
        if len(data) != len(batch):
            raise EmbeddingError(
                f"嵌入返回条数不符: 期望 {len(batch)}，实际 {len(data)}"
            )

        vectors = [list(item.embedding) for item in data]
        for index, vector in enumerate(vectors):
            if len(vector) != self._dim:
                raise EmbeddingError(
                    f"嵌入维度不符（第 {index} 条）: 期望 {self._dim}，实际 {len(vector)}"
                )
        return vectors


def get_embedder() -> QwenEmbedder:
    """构造一个嵌入客户端。

    OpenAI 客户端本身是轻量的，这里不缓存；需要复用时由调用方持有实例即可。
    """
    return QwenEmbedder()


__all__ = [
    "EMBEDDING_MAX_ATTEMPTS",
    "EMBEDDING_MAX_BATCH",
    "EmbeddingError",
    "QwenEmbedder",
    "get_embedder",
]
