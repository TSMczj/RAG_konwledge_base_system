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

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class IndexResult:
    """单个文件一次索引操作的结果。

    Attributes:
        file_hash: 文件内容 SHA-256；空输入时为空串。
        indexed: 本次实际写入/更新的 chunk 数。
        skipped: 是否因为已存在相同文件而跳过。
        count_before: 操作前该文件在库内的 chunk 数。
    """

    file_hash: str
    indexed: int
    skipped: bool
    count_before: int


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """已入库文件的聚合信息（供索引管理 CLI 列表展示）。

    Attributes:
        file_hash: 文件内容 SHA-256。
        source_file: 原始文件名。
        file_path: 原始文件路径。
        doc_type: pdf / docx / txt。
        chunks: 该文件在库内的 chunk 数。
        pages: 有文本的页码最大值；TXT / Word 为 0。
        created_at: 首次入库时间（ISO8601）。
    """

    file_hash: str
    source_file: str
    file_path: str
    doc_type: str
    chunks: int
    pages: int
    created_at: str


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

    def list_files(self) -> list[IndexedFile]:
        """按 ``file_hash`` 聚合已入库文件（供索引管理列表展示）。"""
        result = self._collection.get(include=["metadatas"])
        metadatas = result.get("metadatas") or []

        groups: dict[str, dict] = {}
        for meta in metadatas:
            if not meta:
                continue
            key = meta.get("file_hash") or f"src:{meta.get('source_file', '')}"
            group = groups.get(key)
            if group is None:
                group = {
                    "file_hash": meta.get("file_hash", ""),
                    "source_file": meta.get("source_file", ""),
                    "file_path": meta.get("file_path", ""),
                    "doc_type": meta.get("doc_type", ""),
                    "chunks": 0,
                    "pages": set(),
                    "created_at": meta.get("created_at", "") or "",
                }
                groups[key] = group

            group["chunks"] += 1
            page = meta.get("page", 0)
            if isinstance(page, int) and page > 0:
                group["pages"].add(page)

        files = [
            IndexedFile(
                file_hash=group["file_hash"],
                source_file=group["source_file"],
                file_path=group["file_path"],
                doc_type=group["doc_type"],
                chunks=group["chunks"],
                pages=max(group["pages"]) if group["pages"] else 0,
                created_at=group["created_at"],
            )
            for group in groups.values()
        ]
        return sorted(files, key=lambda item: (item.source_file.lower(), item.file_hash))

    def delete_all(self) -> int:
        """清空当前 collection 的全部向量，返回删除条数。

        保留 collection 本身（以及 cosine 配置），便于重建后继续复用同一个实例。
        """
        ids = list(self._collection.get().get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        logger.info("清空 collection: {} 个 chunk", len(ids))
        return len(ids)

    def has_file(self, file_hash: str) -> bool:
        """该内容哈希是否已有 chunk 入库（T-3.3 幂等判断）。"""
        return self.count_by_file_hash(file_hash) > 0

    def count_by_file_hash(self, file_hash: str) -> int:
        """该文件当前在库内的 chunk 数。"""
        return len(self._ids_where({"file_hash": file_hash}))

    def index_chunks(
        self,
        chunks: Iterable[Chunk],
        *,
        skip_existing: bool = True,
    ) -> IndexResult:
        """索引一个文件的全部 chunk，并做内容哈希判重（对应 T-3.3 / FR-04）。

        判重规则：

        - 同一 ``file_hash`` 且 chunk id 集合完全一致 → 直接跳过，不调用嵌入接口；
        - 库内已有该文件但 chunk 集合不一致（例如分块参数变了、上次留下部分数据）
          → 重新写入全部 chunk，并删除该文件多余的旧 chunk；
        - 文件内容改了 → ``file_hash`` 变了，视为新文件入库（旧版本需显式删除）；
        - 同内容文件改名后仍会跳过，保留首次入库的 ``source_file``；要更新展示名
          请先删除再导入。

        嵌入在写入之前完成，嵌入失败不会留下半入库数据。

        Args:
            chunks: 同一个文件的全部 chunk；不能混合多个 ``file_hash``。
            skip_existing: 为 True 时命中已有文件直接跳过；False 时强制重写。

        Returns:
            :class:`IndexResult`，含是否跳过、写入条数、操作前数量。

        Raises:
            ValueError: 空输入的 file_hash 为空，或一次混入多个文件的 chunk。
        """
        items = list(chunks)
        if not items:
            return IndexResult(file_hash="", indexed=0, skipped=True, count_before=0)

        hashes = {chunk.file_hash for chunk in items}
        if len(hashes) != 1:
            raise ValueError(
                f"index_chunks 一次只能处理同一个文件的 chunk；收到 {len(hashes)} 个 file_hash"
            )
        file_hash = next(iter(hashes))
        if not file_hash:
            raise ValueError("chunk 缺少 file_hash，无法做幂等判重")

        ids = [chunk.chunk_id for chunk in items]
        existing_ids = self._ids_where({"file_hash": file_hash})

        if skip_existing and set(existing_ids) == set(ids):
            logger.info(
                "文件已入库，跳过: {}（{} 个 chunk）",
                file_hash[:12], len(items),
            )
            return IndexResult(
                file_hash=file_hash,
                indexed=0,
                skipped=True,
                count_before=len(existing_ids),
            )

        # 先算完全部向量再写；嵌入失败时库内数据保持不变
        self._write_items(items)

        # 分块参数变化/上次中断可能留下多余旧 chunk，按 id 精确清理
        new_ids = set(ids)
        stale_ids = [item for item in existing_ids if item not in new_ids]
        if stale_ids:
            self._collection.delete(ids=stale_ids)
            logger.info("清理旧版本 chunk: {} 个", len(stale_ids))

        logger.info(
            "向量入库: {} 个 chunk（文件 {}，累计 {}）",
            len(items), file_hash[:12], self.count(),
        )
        return IndexResult(
            file_hash=file_hash,
            indexed=len(items),
            skipped=False,
            count_before=len(existing_ids),
        )

    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int:
        """写入或更新一批 chunk（不做文件级判重），返回写入条数。

        需要幂等导入时用 :meth:`index_chunks`；本方法只保证 ``chunk_id`` 级 upsert。
        """
        items = list(chunks)
        if not items:
            return 0
        self._write_items(items)
        logger.info("向量入库: {} 个 chunk（累计 {}）", len(items), self.count())
        return len(items)

    def add_chunks(self, chunks: Iterable[Chunk]) -> int:
        """``upsert_chunks`` 的别名，便于按“添加”语义调用。"""
        return self.upsert_chunks(chunks)

    def _write_items(self, items: list[Chunk]) -> None:
        """计算向量并一次性 upsert。

        先算 embedding 再写库：嵌入失败不会产生任何写入。
        """
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

    def _ids_where(self, where: dict) -> list[str]:
        """按 metadata 条件取 id 列表。"""
        found = self._collection.get(where=where)
        return list(found.get("ids") or [])

    def _delete_where(self, where: dict) -> int:
        """按 metadata 条件删除，返回实际删除条数。"""
        ids = self._ids_where(where)
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


__all__ = [
    "COSINE_METADATA",
    "IndexResult",
    "IndexedFile",
    "QwenEmbeddings",
    "VectorStore",
    "get_vectorstore",
]
