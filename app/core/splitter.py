"""文本分块器（对应 FR-03 文本分块 / T-2.1 分块器）。

职责
----
把 Loader 产出的 :class:`~app.core.loader.LoadedDocument` 切成检索用的 chunk：

- 正文 Segment 用 ``RecursiveCharacterTextSplitter`` 按中文标点降级切分；
- 表格 Segment **整表保留**（T-2.2 的表格保护在此先落地）；
- 每个 chunk 携带 ``source_file`` / ``page`` / ``chunk_index``，供 FR-09 溯源。

为什么按 Segment 切而不是把整篇拍平
------------------------------------
PDF 的页码、Word/TXT 的 ``page=0`` 都挂在 Segment 上；拍平后再切就补不回页码。
所以每个 Segment 独立切分，表格 Segment 不进入 splitter。

参数来源
--------
``chunk_size`` / ``chunk_overlap`` 从 :func:`app.config.get_settings` 读取（.env），
不在代码里硬编码；默认 500 / 80，来自 FR-03。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.core.loader import (
    KIND_TABLE,
    KIND_TEXT,
    LoadedDocument,
    Segment,
    compute_file_hash,
)
from app.logging_config import logger

#: 分隔符优先级：段落 → 中文句读 → 英文句末 → 空格 → 空串。
#: 英文句末（``". "`` 等）只在中英文混排的 PDF 上生效，避免在单词中间切。
#: 最后两个是兜底：空格与空串，保证没有任何标点的长文本也能被切开。
#: 空串必须放最后——它会把文本拆成单字符，只有前面全部失败时才会用到。
SEPARATORS: tuple[str, ...] = (
    "\n\n", "\n", "。", "；", "！", "？", "，",
    ". ", "! ", "? ", " ", "",
)


@dataclass(frozen=True, slots=True)
class Chunk:
    """一个检索单元。

    Attributes:
        text: chunk 文本。
        source_file: 原始文件名，用于展示与溯源。
        file_path: 原始文件绝对路径。
        doc_type: 文档类型（pdf / docx / txt）。
        page: 页码；PDF 从 1 开始，Word/TXT 为 0。
        chunk_index: 该文档内的块序号，从 0 开始。
        file_hash: 原始文件内容的 SHA-256；手工构造文档缺失时用路径哈希兜底。
        chunk_id: ``{file_hash}-{chunk_index}``，全局唯一。
        created_at: 分块时间，ISO8601（UTC）。
        kind: ``text`` / ``table``；表格块整表保留。
        tenant_id: 租户标识；本地单用户固定 ``"local"``（ADR-001）。
    """

    text: str
    source_file: str
    file_path: str
    doc_type: str
    page: int
    chunk_index: int
    file_hash: str
    chunk_id: str
    created_at: str
    kind: str = KIND_TEXT
    tenant_id: str = "local"

    @property
    def char_count(self) -> int:
        """该 chunk 的字符数。"""
        return len(self.text)

    def to_metadata(self) -> dict[str, str | int]:
        """转成 Chroma 的 metadata（需求文档 §6.2 字段 + 内部 ``kind``）。

        ``kind`` 不在 §6.2 的 8 个字段里，但表格块保护需要它；Chroma 允许附加字段。
        """
        return {
            "source_file": self.source_file,
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "doc_type": self.doc_type,
            "page": self.page,
            "chunk_index": self.chunk_index,
            "chunk_id": self.chunk_id,
            "created_at": self.created_at,
            "kind": self.kind,
            "tenant_id": self.tenant_id,
        }


def _build_splitter() -> RecursiveCharacterTextSplitter:
    """按配置构造分块器。

    每次调用都重新读配置（``get_settings`` 内部有缓存），便于测试里重置环境变量。
    """
    settings = get_settings()
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=list(SEPARATORS),
        # 分隔符留在块尾（如句号），比留在下一块开头更符合阅读与引用习惯
        keep_separator="end",
        strip_whitespace=True,
        length_function=len,
    )


def _split_segment(
    segment: Segment,
    splitter: RecursiveCharacterTextSplitter,
) -> list[str]:
    """把一个 Segment 切成文本片段。

    表格 Segment **永远整表返回**：不调用 splitter，哪怕长度超过 ``chunk_size``。
    表头和数据行一旦被切开，检索召回的会是缺列 / 缺行的残表，比召回不到更糟。

    Args:
        segment: 待切分的 Segment。
        splitter: 已按配置构造好的分块器。

    Returns:
        非空的文本片段列表；表格 Segment 只返回一个元素。
    """
    text = segment.text.strip()
    if not text:
        return []

    if segment.kind == KIND_TABLE:
        logger.debug("表格块整表保留: page={} 长度={}", segment.page, len(text))
        return [text]

    return [piece.strip() for piece in splitter.split_text(text) if piece.strip()]


def _check_table_chunks(doc: LoadedDocument, chunks: list[Chunk]) -> None:
    """校验表格保护的不变量：表格 Segment 与表格 chunk 一一对应且文本不变。

    这是一个「防御性断言」：正常情况下永远成立；一旦将来有人改动切分逻辑
    把表格切散，这里会留下 warning，而不是等检索质量下降后再回头排查。
    """
    expected = [
        seg.text.strip()
        for seg in doc.segments
        if seg.kind == KIND_TABLE and seg.text.strip()
    ]
    actual = [chunk.text for chunk in chunks if chunk.kind == KIND_TABLE]
    if expected != actual:
        logger.warning(
            "表格块保护校验失败: {} 个表格 Segment -> {} 个表格 chunk（文档 {}）",
            len(expected), len(actual), doc.source_file,
        )


def _resolve_file_hash(doc: LoadedDocument) -> str:
    """取文档内容哈希；手工构造的文档缺少哈希时用路径哈希兜底。

    Args:
        doc: 已解析文档。

    Returns:
        文件的 SHA-256。正常入库流程由 :func:`load_document` 预先算好；
        手工构造 ``LoadedDocument`` 且文件不可读时，退化为路径哈希。
    """
    if doc.file_hash:
        return doc.file_hash

    try:
        doc.file_hash = compute_file_hash(doc.file_path)
        return doc.file_hash
    except OSError:
        # 文件已被移动/删除，或调用方手工构造了 LoadedDocument。
        # 用路径哈希保证 chunk_id 稳定且不与其他文档冲突；这不是内容哈希，
        # 真正的入库流程一定会先算好 file_hash。
        logger.warning(
            "文档缺少 file_hash 且无法读取原文件，chunk_id 使用路径哈希兜底: {}",
            doc.source_file,
        )
        return hashlib.sha256(f"{doc.file_path}|{doc.source_file}".encode("utf-8")).hexdigest()


def _split_document(
    doc: LoadedDocument,
    splitter: RecursiveCharacterTextSplitter,
) -> list[Chunk]:
    """切分单个已解析文档（内部函数，调用方需保证 ``doc.ok``）。"""
    chunks: list[Chunk] = []
    file_hash = _resolve_file_hash(doc)
    # 同一文档的所有 chunk 共享一个导入时间，便于按批次筛选
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for segment in doc.segments:
        for piece in _split_segment(segment, splitter):
            index = len(chunks)
            chunks.append(
                Chunk(
                    text=piece,
                    source_file=doc.source_file,
                    file_path=doc.file_path,
                    doc_type=doc.doc_type,
                    page=segment.page,
                    chunk_index=index,
                    file_hash=file_hash,
                    chunk_id=f"{file_hash}-{index}",
                    created_at=created_at,
                    kind=segment.kind,
                )
            )

    _check_table_chunks(doc, chunks)
    logger.debug("分块完成: {} -> {} 个 chunk", doc.source_file, len(chunks))
    return chunks


def split_document(doc: LoadedDocument) -> list[Chunk]:
    """切分单个文档（对应 FR-03 / T-2.1）。

    Args:
        doc: :func:`app.core.loader.load_document` 的返回值。

    Returns:
        chunk 列表；解析失败的文档返回空列表。
    """
    if not doc.ok:
        logger.debug("跳过解析失败的文档: {} -> {}", doc.source_file, doc.error)
        return []
    return _split_document(doc, _build_splitter())


def split_documents(docs: Iterable[LoadedDocument]) -> list[Chunk]:
    """批量切分多个文档，跳过解析失败项（对应 T-2.1）。

    Args:
        docs: 已解析文档的可迭代对象（可直接传 ``BatchLoadResult.documents``）。

    Returns:
        所有文档的 chunk 列表；每个文档的 ``chunk_index`` 都从 0 重新计数。

    Note:
        只对 ``doc.ok`` 为真的文档切分；失败文档由批量解析层（T-1.6）记录，
        这里不重复报错，只在 debug 日志里说明跳过。
    """
    splitter = _build_splitter()
    chunks: list[Chunk] = []
    doc_count = 0

    for doc in docs:
        if not doc.ok:
            logger.debug("跳过解析失败的文档: {} -> {}", doc.source_file, doc.error)
            continue
        doc_count += 1
        chunks.extend(_split_document(doc, splitter))

    logger.info("分块完成: {} 个文档 -> {} 个 chunk", doc_count, len(chunks))
    return chunks


__all__ = ["Chunk", "SEPARATORS", "split_document", "split_documents"]
