"""文档加载器抽象层（对应 FR-02 文档解析 / T-1.1 加载器抽象层）。

职责
----
把不同格式的文件统一抽成「分段列表 + 文档元数据」，供下游分块（T-2.x）使用。
本模块**只负责按扩展名分发**，各格式的具体解析逻辑由 T-1.2 ~ T-1.5 逐步补上。

为什么返回「分段(Segment)」而不是一整段纯文本
---------------------------------------------
下游有两条硬要求都依赖文档结构，拍平成一个字符串就再也拿不回来了：

1. **每个 chunk 必须携带页码**（FR-06 检索结果要能溯源到「第几页」）——
   所以必须按页/按段保留边界；
2. **表格不能被切散**（FR-03 的表格保护）—— 所以必须能把「表格」和「正文」区分开。

因此这里一开始就保留结构。虽然 T-1.1 阶段还看不出好处，但到 T-1.3 / T-1.4 会直接受益，
避免那时推倒重来。

为什么解析失败不抛异常
----------------------
FR-02 明确要求「单文件解析失败不阻断整批导入」。所以本模块把所有**可预期的失败**
都收敛到 :attr:`LoadedDocument.error` 字段上，由批量入口汇总成失败清单（T-1.6）。
反过来说：调用方看到 ``error is None`` 才能认为解析成功，不要只看 ``segments`` 是否为空。

如何新增一种格式（T-1.1 的验收标准）
------------------------------------
只需两步，**不需要改动本文件里的任何既有代码**：

1. 写一个「接收 Path、返回 Segment 可迭代对象」的函数；
2. 用 :func:`register_loader` 装饰它，声明它负责的扩展名。

示例::

    @register_loader(".txt")
    def _load_txt(path: Path) -> list[Segment]:
        return [Segment(text=path.read_text(encoding="utf-8"), page=0)]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from app.logging_config import logger

# ---------------------------------------------------------------------------
# 内容类型常量
# ---------------------------------------------------------------------------
# 用常量而不是到处裸写 "text" / "table"：避免拼写不一致导致下游判断失效，
# 也让 IDE 能跳转、重构时不会漏改。
KIND_TEXT: str = "text"
KIND_TABLE: str = "table"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Segment:
    """文档中的一段内容，是下游分块（T-2.1）的输入单元。

    Attributes:
        text: 该段的纯文本内容。
        page: 页码，从 1 开始；没有页码概念的格式（如 txt）填 0。
        kind: 内容类型，取值见 :data:`KIND_TEXT` / :data:`KIND_TABLE`。
            下游据此决定「是否允许被切分」——表格段必须整体保留（T-2.2）。
    """

    text: str
    page: int = 0
    kind: str = KIND_TEXT


@dataclass
class LoadedDocument:
    """单个文档的解析结果。

    Attributes:
        source_file: 原始文件名（不含目录），用于展示与溯源。
        file_path: 原始文件的绝对路径。
        doc_type: 文档类型，取扩展名去掉点，如 ``"pdf"`` / ``"docx"`` / ``"txt"``。
        segments: 解析出的分段列表；解析失败时为空列表。
        error: 失败原因；成功时为 ``None``。
    """

    source_file: str
    file_path: str
    doc_type: str
    segments: list[Segment] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """解析是否成功。"""
        return self.error is None

    @property
    def text(self) -> str:
        """全文：把各段按原顺序拼接。

        供「不需要结构」的场景使用（如整体统计、调试打印）。
        注意：**分块流程应当使用 segments 而不是 text**，否则页码信息会丢失。
        """
        return "\n\n".join(seg.text for seg in self.segments)

    @property
    def char_count(self) -> int:
        """正文字符总数（用于进度展示与成本估算）。"""
        return sum(len(seg.text) for seg in self.segments)

    @property
    def page_count(self) -> int:
        """文档页数。没有页码概念的格式统一按 1 页处理。"""
        pages = {seg.page for seg in self.segments if seg.page > 0}
        return max(pages) if pages else 1


# ---------------------------------------------------------------------------
# 解析器注册表
# ---------------------------------------------------------------------------
#: 解析函数的签名：接收文件路径，返回若干 Segment
LoaderFunc = Callable[[Path], Iterable[Segment]]

#: 扩展名 -> 解析函数。键一律是带点的规范形式（如 ".pdf"）
_LOADERS: dict[str, LoaderFunc] = {}


def _normalize_ext(ext: str) -> str:
    """把扩展名规整成统一形式（小写、带前导点）。

    Args:
        ext: 原始扩展名，``"PDF"`` / ``".pdf"`` / ``"pdf"`` 都接受。

    Returns:
        规范形式，如 ``".pdf"``。
    """
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else f".{ext}"


def register_loader(*extensions: str) -> Callable[[LoaderFunc], LoaderFunc]:
    """把解析函数注册到指定扩展名上（装饰器）。

    Args:
        *extensions: 该函数负责的扩展名，带不带点均可。

    Returns:
        装饰器；被装饰的函数会被原样返回，因此仍可当普通函数直接调用。

    Raises:
        ValueError: 某个扩展名已被别的解析器注册。
            这里**故意不做静默覆盖** —— 两个解析器抢同一个扩展名时，
            静默覆盖会让「为什么解析结果变了」变得极难排查。
    """

    def decorator(func: LoaderFunc) -> LoaderFunc:
        for ext in extensions:
            key = _normalize_ext(ext)
            if key in _LOADERS:
                raise ValueError(
                    f"扩展名 {key} 已被 {_LOADERS[key].__name__} 注册，"
                    f"不能再被 {func.__name__} 覆盖"
                )
            _LOADERS[key] = func
        return func

    return decorator


def supported_extensions() -> frozenset[str]:
    """当前已注册的扩展名集合（如 ``{'.pdf', '.txt'}``）。"""
    return frozenset(_LOADERS)


def is_supported(path: str | Path) -> bool:
    """判断某个文件是否已有对应的解析器。

    Args:
        path: 文件路径。

    Returns:
        有解析器返回 True。
    """
    return _normalize_ext(Path(path).suffix) in _LOADERS


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def load_document(path: str | Path) -> LoadedDocument:
    """解析单个文档，按扩展名分发到对应解析器（对应 FR-02）。

    Args:
        path: 文档路径。

    Returns:
        :class:`LoadedDocument`。**任何失败都通过 error 字段返回，不抛异常**，
        以便批量导入时跳过坏文件继续处理（FR-02 硬要求）。

    Raises:
        不抛异常。文件不存在、格式不支持、解析器内部崩溃，一律转成 ``error`` 字段。
        调用方通过 ``doc.ok`` 判断成败。
    """
    p = Path(path)
    doc_type = _normalize_ext(p.suffix).lstrip(".")

    doc = LoadedDocument(
        source_file=p.name,
        file_path=str(p),
        doc_type=doc_type,
    )

    # ---- 情况 1：文件不存在 ----
    if not p.is_file():
        doc.error = f"文件不存在或不是普通文件: {p}"
        logger.warning("加载失败（文件不存在）: {}", p)
        return doc

    # 文件真实存在时再解析成绝对路径，否则 resolve() 会基于 cwd 产生误导性的路径
    doc.file_path = str(p.resolve())

    # ---- 情况 2：没有对应的解析器 ----
    loader = _LOADERS.get(_normalize_ext(p.suffix))
    if loader is None:
        supported = "、".join(sorted(supported_extensions())) or "（尚未注册任何解析器）"
        doc.error = f"不支持的格式 {_normalize_ext(p.suffix) or '(无扩展名)'}；当前已支持: {supported}"
        # 用 debug 而不是 warning：批量导入时非目标文件很多，不该刷屏（FR-01 要求静默跳过）
        logger.debug("跳过不支持的格式: {} ({})", p.name, doc.error)
        return doc

    # ---- 情况 3：解析 ----
    try:
        # 过滤掉空白段：解析器可能因版面原因产出空串，留着会污染后续分块
        segments = [seg for seg in loader(p) if seg.text and seg.text.strip()]
    except Exception as exc:  # noqa: BLE001 —— 这里必须兜住一切，否则整批导入会中断
        doc.error = f"{type(exc).__name__}: {exc}"
        logger.warning("解析失败: {} -> {}", p.name, doc.error)
        return doc

    doc.segments = segments

    if not segments:
        # 解析「成功」但一段都没抽出来，绝大多数情况是扫描件或缺文本层（见 T-1.7）。
        # 这里只告警不判失败：因为是否算失败取决于业务判断，交给 T-1.7 的检测逻辑决定。
        logger.warning("解析结果为空，疑似扫描件或缺文本层: {}", p.name)
    else:
        logger.debug(
            "已解析 {} -> {} 段 / {} 字符 / {} 页",
            p.name, len(segments), doc.char_count, doc.page_count,
        )

    return doc


__all__ = [
    "KIND_TABLE",
    "KIND_TEXT",
    "LoadedDocument",
    "LoaderFunc",
    "Segment",
    "is_supported",
    "load_document",
    "register_loader",
    "supported_extensions",
]
