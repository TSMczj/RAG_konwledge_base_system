"""Chroma 向量库封装（对应 FR-04 向量化与入库 / T-3.2）。

方案 A 的分工
-------------
- 向量库用 LangChain 的 ``langchain_chroma.Chroma``；
- 嵌入仍由自写 :class:`~app.core.embedder.QwenEmbedder` 完成，
  通过 :class:`QwenEmbeddings` 适配成 LangChain ``Embeddings`` 接口；
- 批量、超时、重试继续由自写嵌入层控制。

存储约定
--------
- collection 固定使用 **cosine** 距离；
- 持久化目录来自配置 ``CHROMA_DIR``（默认 ``data/chroma/``）；
- 每条向量的 id 用 ``chunk_id``（``{file_hash}-{chunk_index}``），
  document 存 chunk 文本，metadata 存 ``Chunk.to_metadata()``。

注意
----
``Chroma`` 没有公开 ``upsert`` / ``count``，这里通过底层 ``_collection`` 调用；
这是 LangChain 封装未覆盖、但本项目明确需要的能力。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.config import Settings, get_settings
from app.core.embedder import QwenEmbedder
from app.core.splitter import Chunk
from app.logging_config import logger

#: collection 的相似度度量；首次创建时写入
COSINE_METADATA = {"hnsw:space": "cosine"}


class QwenEmbeddings(Embeddings):
    """把 :class:`QwenEmbedder` 适配成 LangChain ``Embeddings`` 接口。

    LangChain 的 Chroma 会在写入/查询时调用 ``embed_documents`` /
    ``embed_query``；适配器只做转发，分批、超时、重试仍由 ``QwenEmbedder`` 负责。
    """

    def __init__(self, embedder: QwenEmbedder | None = None) -> None:
        self._embedder = embedder or QwenEmbedder()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """把文档文本批量转成向量。"""
        return self._embedder.embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        """把查询文本转成向量。"""
        return self._embedder.embed_query(text)


class VectorStore:
    """Chroma 持久化向量库的薄封装。"""

    def __init__(
        self,
        settings: Settings | None = None,
        embedding_function: Embeddings | None = None,
        persist_directory: str | Path | None = None,
        collection_name: str | None = None,
    ) -> None:
        """打开（必要时创建）一个持久化 collection。

        Args:
            settings: 配置；缺省时用 :func:`get_settings`。
            embedding_function: 可注入的 LangChain Embeddings（测试用）；
                缺省时用 ``QwenEmbeddings(QwenEmbedder(settings))``。
            persist_directory: 覆盖持久化目录（测试用）；缺省用配置值。
            collection_name: 覆盖 collection 名（测试用）；缺省用配置值。
        """
        self._settings = settings or get_settings()
        self._persist_directory = Path(
            persist_directory or self._settings.chroma_dir
        )
        self._persist_directory.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name or self._settings.collection_name
        self._embeddings = embedding_function or QwenEmbeddings(
            QwenEmbedder(settings=self._settings)
        )

        self._store = Chroma(
            collection_name=self._collection_name,
            embedding_function=self._embeddings,
            persist_directory=str(self._persist_directory),
            collection_metadata=dict(COSINE_METADATA),
        )
        # LangChain 没公开 upsert/count，这里拿到底层 collection；
        # add_texts 只做 add，重复 chunk_id 会报错，所以入库统一用 upsert。
        self._collection = self._store._collection

        metric = (self._collection.metadata or {}).get("hnsw:space")
        if metric and metric != "cosine":
            logger.warning(
                "Chroma collection {} 的相似度度量是 {}，不是 cosine；"
                "请删除旧库或换 collection 名后重建",
                self._collection_name, metric,
            )
        logger.debug(
            "Chroma 已打开: collection={} path={} count={}",
            self._collection_name, self._persist_directory, self._collection.count(),
        )

    @property
    def collection_name(self) -> str:
        """当前 collection 名。"""
        return self._collection_name

    @property
    def persist_directory(self) -> Path:
        """持久化目录。"""
        return self._persist_directory

    @property
    def store(self) -> Chroma:
        """底层 LangChain Chroma 实例（只读用途，如 T-4.1 检索）。"""
        return self._store

    def count(self) -> int:
        """当前 collection 的向量条数。"""
        return int(self._collection.count())

    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int:
        """写入或更新一批 chunk，返回写入条数。

        使用 ``chunk_id`` 作为向量 id，因此同一个 chunk 重复写入是幂等的；
        真正的“同一文件是否已入库”判重由 T-3.3 负责。

        Args:
            chunks: :class:`~app.core.splitter.Chunk` 可迭代对象。

        Returns:
            实际提交的 chunk 数（空输入返回 0）。
        """
        items = list(chunks)
        if not items:
            return 0

        ids = [chunk.chunk_id for chunk in items]
        texts = [chunk.text for chunk in items]
        metadatas = [chunk.to_metadata() for chunk in items]

        # 先由自写嵌入层算好向量，再显式 upsert，避免 Chroma 内部逐条调用。
        embeddings = self._embeddings.embed_documents(texts)
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("向量入库: {} 个 chunk（累计 {}）", len(items), self.count())
        return len(items)

    def add_chunks(self, chunks: Iterable[Chunk]) -> int:
        """``upsert_chunks`` 的别名，便于按“添加”语义调用。"""
        return self.upsert_chunks(chunks)

    def delete_by_ids(self, ids: Sequence[str]) -> int:
        """按向量 id 删除，返回请求删除的条数。"""
        items = list(ids)
        if not items:
            return 0
        self._collection.delete(ids=items)
        logger.info("按 id 删除: {} 个 chunk", len(items))
        return len(items)

    def delete_by_file_hash(self, file_hash: str) -> int:
        """删除某个文件（内容哈希）对应的全部 chunk。"""
        return self._delete_where({"file_hash": file_hash})

    def delete_by_source(self, source_file: str) -> int:
        """按原始文件名删除；同名不同内容的文件会被一起删除，慎用。

        精确删除请优先用 :meth:`delete_by_file_hash`。
        """
        return self._delete_where({"source_file": source_file})

    def _delete_where(self, where: dict) -> int:
        """按 metadata 条件删除，返回实际删除条数。"""
        found = self._collection.get(where=where)
        ids = list(found.get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        logger.info("按条件删除: {} -> {} 个 chunk", where, len(ids))
        return len(ids)

    def query(
        self,
        query_text: str,
        k: int | None = None,
        where: dict | None = None,
    ) -> list[tuple[Document, float]]:
        """相似度检索，返回 ``(Document, cosine 距离)``。

        Args:
            query_text: 查询文本。
            k: 返回条数；缺省用配置 ``TOP_K``。
            where: 可选的 metadata 过滤条件（Chroma where 语法）。

        Returns:
            按距离升序的 ``(Document, distance)``；cosine 距离越小越相似。
        """
        limit = k if k is not None else self._settings.top_k
        kwargs: dict = {"k": limit}
        if where:
            kwargs["filter"] = where
        return self._store.similarity_search_with_score(query_text, **kwargs)


def get_vectorstore() -> VectorStore:
    """构造向量库实例。"""
    return VectorStore()


__all__ = ["COSINE_METADATA", "QwenEmbeddings", "VectorStore", "get_vectorstore"]
