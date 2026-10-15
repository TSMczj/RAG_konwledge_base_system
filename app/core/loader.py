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

import codecs
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from charset_normalizer import from_bytes

from app.logging_config import logger

# ---------------------------------------------------------------------------
# 内容类型常量
# ---------------------------------------------------------------------------
# 用常量而不是到处裸写 "text" / "table"：避免拼写不一致导致下游判断失效，
# 也让 IDE 能跳转、重构时不会漏改。
KIND_TEXT: str = "text"
KIND_TABLE: str = "table"

# ---------------------------------------------------------------------------
# 编码探测相关常量
# ---------------------------------------------------------------------------
# BOM（字节顺序标记）一旦命中就是铁证，不需要也不应该再去"猜"。
#
# ⚠️ 这里有两个坑，都是实测踩出来的：
#   1. 顺序不能乱 —— UTF-32 的 BOM 前两字节（FF FE / FE FF）与 UTF-16 相同，
#      必须先判 UTF-32，否则 UTF-32 文件会被误判成 UTF-16 解出乱码；
#   2. 必须用 "utf-16" / "utf-32"，**不能**用 "utf-16-le" / "utf-32-le" ——
#      前者会读取并剥离 BOM，后者会把 BOM 当正文，导致文本开头多出一个 \ufeff。
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# 探测失败时的兜底顺序：
#   - gb18030 是 GBK / GB2312 的超集，放最前，能覆盖绝大多数简体中文老文件；
#   - latin-1 是单字节且永不抛错，放最后，保证函数一定有返回。
_FALLBACK_ENCODINGS: tuple[str, ...] = ("gb18030", "big5", "shift_jis", "cp1252", "latin-1")


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
# 文本解码与规范化（供各解析器复用）
# ---------------------------------------------------------------------------
def _detect_encoding(raw: bytes) -> str | None:
    """用 charset-normalizer 探测编码；**探测结果不可信时返回 None**。

    如何判断"不可信"（依据 2026-10-16 的实测）
    ------------------------------------------
    对 4 字节的 GBK 文本 ``"中文"``，探测库给出的候选 big5 / cp949 / gb18030
    的 chaos **全是 0**，即库自己也无法区分，它最终选了 big5 并解出乱码 ``"笢恅"``。
    而 48 字节以上的 GBK 文本能正确选到 gb18030（因为次选 cp949 的 chaos 是 0.113，
    存在明显的信号差）。

    据此设定规则：**首选与次选的 chaos 同时为 0 时，说明库没有任何区分依据，
    此时它的排序等同于随机**，不采信；改由 :data:`_FALLBACK_ENCODINGS`
    按本项目的语料先验（以简体中文为主）选择。

    Args:
        raw: 原始字节内容。

    Returns:
        可采信的编码名；无区分信号或探测失败时返回 ``None``。
    """
    matches = from_bytes(raw)
    best = matches.best()
    if best is None:
        return None

    runner_up = next((m for m in matches if m.encoding != best.encoding), None)
    if best.chaos == 0.0 and runner_up is not None and runner_up.chaos == 0.0:
        logger.debug(
            "编码探测无区分信号（{} 与 {} 的 chaos 同为 0），改用语料先验",
            best.encoding, runner_up.encoding,
        )
        return None

    return best.encoding


def decode_text(raw: bytes) -> tuple[str, str]:
    """把字节流解码为文本，自动探测编码（对应 T-1.2）。

    探测顺序（从最可靠到最兜底）：

    1. **BOM**：文件自带字节顺序标记时直接采用，这是铁证；
    2. **严格 UTF-8**：先试一次。理由是不能完全依赖自动探测 ——
       对已经是合法 UTF-8 的文本，探测库偶尔会给出 cp1252 之类的误判；
       而 UTF-8 是当下绝大多数文件的编码，先试一次成本极低；
    3. **charset-normalizer 探测**：处理 GBK / Big5 等中文老编码，
       但仅在探测确实"有区分信号"时才采信（见 :func:`_detect_encoding`）；
    4. **固定兜底链**：见 :data:`_FALLBACK_ENCODINGS`。
       它同时承担"探测无信号时按语料先验选择"的职责。

    Args:
        raw: 原始字节内容。

    Returns:
        ``(文本, 实际使用的编码名)``。编码名会写进日志，便于排查乱码问题。

    Note:
        本函数**不会抛异常**：最坏情况由 latin-1 兜底，它不会失败。
    """
    if not raw:
        return "", "utf-8"

    # 1) BOM
    for bom, encoding in _BOM_ENCODINGS:
        if raw.startswith(bom):
            return raw.decode(encoding), encoding

    # 2) 严格 UTF-8
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # 3) charset-normalizer 探测（仅在确实有区分信号时才采信）
    detected = _detect_encoding(raw)
    if detected is not None:
        try:
            return raw.decode(detected), detected
        except (UnicodeDecodeError, LookupError) as exc:
            # 探测结果可能是 Python 不认识的编码名，或解码仍失败 —— 继续走兜底链
            logger.debug("编码探测结果 {} 不可用（{}），改用兜底链", detected, exc)

    # 4) 兜底链
    for encoding in _FALLBACK_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        logger.debug("使用兜底/先验编码 {} 解码", encoding)
        return text, encoding

    # 5) 理论上到不了这里（latin-1 不会失败），保留以防将来改动兜底链
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def normalize_text(text: str) -> str:
    """统一换行符为 ``\\n``，并去掉首尾空白。

    为什么必须做这一步
    ------------------
    Windows 上的文本换行是 ``\\r\\n``，而下游分块（T-2.1）依赖段落分隔符 ``"\\n\\n"``。
    ``"\\r\\n\\r\\n"`` 里并不包含连续的 ``"\\n\\n"``，会导致**整篇文档被当成一个段落切不开**。
    在加载层的统一出口做掉这件事，所有格式（TXT / PDF / Word）行为一致，
    不需要每个解析器各写一遍。

    Args:
        text: 原始文本。

    Returns:
        换行统一为 ``\\n``、首尾空白已去掉的文本。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


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
        raw_segments = list(loader(p))
    except Exception as exc:  # noqa: BLE001 —— 这里必须兜住一切，否则整批导入会中断
        doc.error = f"{type(exc).__name__}: {exc}"
        logger.warning("解析失败: {} -> {}", p.name, doc.error)
        return doc

    # 统一在出口做两件事，保证所有格式产出的文本行为一致：
    #   1) 换行符归一化为 \n —— 否则下游分块匹配不到段落分隔符 "\n\n"（见 normalize_text）；
    #   2) 丢掉空白段 —— 解析器可能因版面原因产出空串，留着会污染后续分块。
    segments: list[Segment] = []
    for seg in raw_segments:
        text = normalize_text(seg.text)
        if text:
            segments.append(Segment(text=text, page=seg.page, kind=seg.kind))

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


# ---------------------------------------------------------------------------
# 具体格式解析器
#
# 约定：每个解析器只负责「把文件变成若干 Segment」，不负责切分、不负责编码兜底
# （换行归一等通用处理由 load_document 统一完成）。新增格式时在这里加一个函数即可。
# ---------------------------------------------------------------------------
@register_loader(".txt")
def _load_txt(path: Path) -> list[Segment]:
    """解析纯文本文件（对应 T-1.2 / FR-02）。

    纯文本没有"页"的概念，因此整篇作为**一个** Segment 返回（``page=0``）。
    真正的切分交给下游分块器（T-2.1），本函数不做任何切分动作。

    Args:
        path: 文本文件路径。

    Returns:
        只含一个正文 Segment 的列表。

    Raises:
        可能抛 ``OSError``（文件读取失败）。这类异常由 :func:`load_document`
        统一捕获并转成 ``error`` 字段，解析器自身不必提前处理。
    """
    text, encoding = decode_text(path.read_bytes())
    # 记下探测结果：出现乱码时，这一行日志是排查的第一手线索
    logger.debug("TXT 编码探测结果: {} -> {}", path.name, encoding)
    return [Segment(text=text, page=0, kind=KIND_TEXT)]


__all__ = [
    "KIND_TABLE",
    "KIND_TEXT",
    "LoadedDocument",
    "LoaderFunc",
    "Segment",
    "decode_text",
    "is_supported",
    "load_document",
    "normalize_text",
    "register_loader",
    "supported_extensions",
]
