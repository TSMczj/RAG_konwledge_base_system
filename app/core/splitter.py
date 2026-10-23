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

from dataclasses import dataclass
from typing import Iterable

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.core.loader import KIND_TABLE, KIND_TEXT, LoadedDocument
from app.logging_config import logger

#: 中文优先的分隔符：先按段落，再按句号 / 分号 / 叹号 / 问号 / 逗号降级；
#: 最后两个是兜底：空格与空串，保证没有任何标点的长文本也能被切开。
#: 空串必须放最后——它会把文本拆成单字符，只有前面全部失败时才会用到。
SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "；", "！", "？", "，", " ", "")


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
        kind: ``text`` / ``table``；表格块整表保留。
    """

    text: str
    source_file: str
    file_path: str
    doc_type: str
    page: int
    chunk_index: int
    kind: str = KIND_TEXT

    @property
    def char_count(self) -> int:
        """该 chunk 的字符数。"""
        return len(self.text)


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


def _split_document(
    doc: LoadedDocument,
    splitter: RecursiveCharacterTextSplitter,
) -> list[Chunk]:
    """切分单个已解析文档（内部函数，调用方需保证 ``doc.ok``）。"""
    chunks: list[Chunk] = []

    for segment in doc.segments:
        text = segment.text.strip()
        if not text:
            continue

        # 表格 Segment 整表保留：切散后表头与数据行分离，检索会拿到残缺表格。
        # T-2.2 会在此之上补显式验收，保护逻辑本身先落在这里。
        pieces = [text] if segment.kind == KIND_TABLE else splitter.split_text(text)

        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            chunks.append(
                Chunk(
                    text=piece,
                    source_file=doc.source_file,
                    file_path=doc.file_path,
                    doc_type=doc.doc_type,
                    page=segment.page,
                    chunk_index=len(chunks),
                    kind=segment.kind,
                )
            )

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
