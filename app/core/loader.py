"""文档加载器抽象层（对应 FR-02 文档解析 / T-1.1 加载器抽象层）。

职责
----
把不同格式的文件统一抽成「分段列表 + 文档元数据」，供下游分块（T-2.x）使用。
本模块**只负责按扩展名分发**，各格式的具体解析逻辑由 T-1.2 ~ T-1.5 逐步补上
（TXT、PDF 正文与表格、Word 均已实现）。批量入口 :func:`load_documents` /
:func:`load_directory`（T-1.6）负责逐文件解析并把失败汇总成清单，单个坏文件不中断整批。

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
import re
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
# 批量解析（T-1.6）
# ---------------------------------------------------------------------------
@dataclass
class BatchLoadResult:
    """一次批量解析的汇总结果。

    Attributes:
        documents: 解析成功的文档（``ok=True``）；可能包含 0 段的空文本文档。
        failures: 解析失败的文档（``ok=False``），``error`` 字段里是失败原因。
        skipped: 未尝试解析的路径（扩展名不支持、Office 临时锁文件等）。
    """

    documents: list[LoadedDocument] = field(default_factory=list)
    failures: list[LoadedDocument] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        """本次批量涉及的文件总数（成功 + 失败 + 跳过）。"""
        return len(self.documents) + len(self.failures) + len(self.skipped)

    @property
    def total_segments(self) -> int:
        """成功文档的 Segment 总数。"""
        return sum(len(doc.segments) for doc in self.documents)

    @property
    def total_chars(self) -> int:
        """成功文档的正文字符总数。"""
        return sum(doc.char_count for doc in self.documents)

    @property
    def empty_documents(self) -> list[LoadedDocument]:
        """解析成功但一段都没抽到的文档（疑似扫描件 / 空文件）。"""
        return [doc for doc in self.documents if not doc.segments]

    def failure_lines(self) -> list[str]:
        """把失败清单整理成「路径: 原因」的行，供 CLI / 日志输出。"""
        return [f"{doc.file_path}: {doc.error}" for doc in self.failures]

    def summary(self) -> str:
        """一行汇总，便于 CLI 打印和日志打点。"""
        text = (
            f"成功 {len(self.documents)} 个（{self.total_segments} 段 / "
            f"{self.total_chars} 字符），失败 {len(self.failures)} 个，"
            f"跳过 {len(self.skipped)} 个"
        )
        empty = len(self.empty_documents)
        if empty:
            text += f"；其中 {empty} 个解析结果为空（疑似扫描件或缺文本层）"
        return text


def load_documents(paths: Iterable[str | Path]) -> BatchLoadResult:
    """批量解析一组文件，单文件失败只记录、不中断整批（对应 T-1.6 / FR-02）。

    对每个路径：

    1. 跳过 Office 临时锁文件（``~$xxx.docx``）与不支持的扩展名，计入 ``skipped``；
    2. 调用 :func:`load_document`；它本身设计为不抛异常，这里仍加一层 try/except 兜底；
    3. 成功进 ``documents``，失败进 ``failures``，最后打印一行汇总。

    Args:
        paths: 文件路径的可迭代对象；也接受单个 ``str`` / ``Path``（会自动包成列表）。

    Returns:
        :class:`BatchLoadResult`，含成功文档、失败清单和跳过清单。

    Note:
        这里**不**对路径去重，也不改变调用方给的顺序；目录扫描由
        :func:`load_directory` 负责排序，保证同一目录多次运行结果稳定。
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]

    result = BatchLoadResult()

    for raw in paths:
        p = Path(raw)

        # Word/Office 的临时锁文件以 ~$ 开头，扩展名可能仍是 .docx。
        # 必须显式跳过，否则会被当成损坏文档记进失败清单，污染导入统计。
        if p.name.startswith("~$"):
            result.skipped.append(str(p))
            logger.debug("跳过 Office 临时锁文件: {}", p.name)
            continue

        if not is_supported(p):
            result.skipped.append(str(p))
            logger.debug("跳过不支持的格式: {}", p.name)
            continue

        try:
            doc = load_document(p)
        except Exception as exc:  # noqa: BLE001 —— 双保险：load_document 设计上不抛
            doc = LoadedDocument(
                source_file=p.name,
                file_path=str(p),
                doc_type=_normalize_ext(p.suffix).lstrip("."),
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning("批量解析出现意外异常: {} -> {}", p.name, doc.error)

        if doc.ok:
            result.documents.append(doc)
        else:
            result.failures.append(doc)
            # load_document 内部已经 warn 过一次，这里降为 debug，避免重复刷屏
            logger.debug("批量解析失败: {} -> {}", p.name, doc.error)

    logger.info("批量解析完成: {}", result.summary())
    return result


def load_directory(directory: str | Path, recursive: bool = False) -> BatchLoadResult:
    """扫描目录并批量解析其中的文档（对应 T-1.6 / FR-01 目录导入）。

    Args:
        directory: 目录路径。
        recursive: 是否递归子目录，默认只扫描一层。

    Returns:
        :class:`BatchLoadResult`。目录不存在时返回一个只含失败项的汇总，
        而不是抛异常，方便 CLI 统一处理。

    Note:
        文件按路径字典序排序，保证同一目录多次扫描的块序稳定；
        非目标扩展名由 :func:`load_documents` 静默跳过。
    """
    base = Path(directory)
    if not base.is_dir():
        result = BatchLoadResult()
        result.failures.append(
            LoadedDocument(
                source_file=base.name,
                file_path=str(base),
                doc_type="",
                error=f"目录不存在或不是目录: {base}",
            )
        )
        logger.warning("批量解析目录无效: {}", base)
        return result

    pattern = "**/*" if recursive else "*"
    candidates = sorted(
        (p for p in base.glob(pattern) if p.is_file()),
        key=lambda p: str(p).lower(),
    )
    logger.info(
        "开始批量解析目录: {}（递归={}，候选文件 {} 个）",
        base, recursive, len(candidates),
    )
    return load_documents(candidates)


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


# ---------------------------------------------------------------------------
# 表格文本辅助（T-1.4 PDF / T-1.5 Word 共用）
#
# 设计取舍
# --------
# 正文仍由 PyMuPDF 抽取（T-1.3），表格交给 pdfplumber（T-1.4）：
#   - 两家库都使用「页面左上角为原点」的 PDF 点坐标，bbox 可以直接对齐；
#   - 先用 pdfplumber 找到表格区域，再把与表格重叠的 PyMuPDF 文本块从正文中剔除，
#     避免同一张表既进正文、又进表格 Segment（重复入库会拉低检索质量）。
#
# 只认「有框线」的表格：pdfplumber 默认按线条检测。无框线表格 V1 不识别，
# 其文字会原样留在正文里，至少不会丢内容。
# ---------------------------------------------------------------------------
_TABLE_MIN_ROWS = 2             # 少于 2 行多半是版式框，不是表格
_TABLE_MIN_COLS = 2             # 单列布局对问答没有价值，且容易误判
_TABLE_MIN_CELLS = 4            # 至少要有 4 个非空单元格
_TABLE_OVERLAP_THRESHOLD = 0.5  # 文本块与表格重叠超过一半才剔除，避免误删相邻段落

#: pdfplumber 的表格 bbox：(x0, top, x1, bottom)，单位是 PDF 点
TableBox = tuple[float, float, float, float]

# 注意：下面的单元格清洗与 Markdown 生成是格式无关的，Word 解析（T-1.5）直接复用；
# 上面的阈值与 _is_useful_table 只用于过滤 PDF 的版式框，Word 表格无需这套过滤。


def _clean_table_cell(cell: str | None) -> str:
    """把表格单元格清洗成能放进 Markdown 表格的文本。

    Args:
        cell: 原始单元格；pdfplumber 的空单元格返回 ``None``，python-docx 返回字符串。

    Returns:
        去掉首尾空白、换行转 ``<br>``、竖线已转义的单元格文本。
    """
    if cell is None:
        return ""
    text = str(cell).replace("\r\n", "\n").replace("\r", "\n").strip()
    # Markdown 表格的单元格不能有裸换行，用 <br> 保留换行语义
    text = text.replace("\n", "<br>")
    # 竖线是列分隔符，不转义会让整行错列
    return text.replace("|", "\\|")


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """把行列数据转成 Markdown 表格（pdfplumber / python-docx 共用）。

    首行按表头处理（两家库都不区分表头，这是最通用的做法）。

    Args:
        rows: 每行一个单元格列表；单元格可能为 ``None``（pdfplumber）。

    Returns:
        Markdown 表格文本；全空时返回空串。单行数据会生成只有表头的表格，
        这样 Word 里的单行表格也不会丢内容。
    """
    cleaned: list[list[str]] = []
    for row in rows:
        cells = [_clean_table_cell(cell) for cell in row]
        if any(cells):  # 丢掉纯空行（解析库偶尔会产出）
            cleaned.append(cells)

    if not cleaned:
        return ""

    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    lines = [
        "| " + " | ".join(padded[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def _is_useful_table(rows: list[list[str | None]]) -> bool:
    """过滤版式框、单列布局等假表格，避免把页面装饰当表格入库。"""
    if len(rows) < _TABLE_MIN_ROWS:
        return False
    width = max((len(row) for row in rows), default=0)
    if width < _TABLE_MIN_COLS:
        return False
    non_empty = sum(1 for row in rows for cell in row if cell and str(cell).strip())
    return non_empty >= _TABLE_MIN_CELLS


def _rect_overlap_ratio(block: tuple[float, float, float, float], box: TableBox) -> float:
    """计算文本块落在表格区域内的面积比例。"""
    ix0, iy0 = max(block[0], box[0]), max(block[1], box[1])
    ix1, iy1 = min(block[2], box[2]), min(block[3], box[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    block_area = (block[2] - block[0]) * (block[3] - block[1])
    if block_area <= 0:
        return 0.0
    return ((ix1 - ix0) * (iy1 - iy0)) / block_area


def _is_block_in_table(block: tuple[float, float, float, float], box: TableBox) -> bool:
    """判断文本块是否属于表格。

    只看重叠面积不够：表格角落的小文本块可能只压到表格一小部分；
    反过来只看中心点也不够：跨表格边界的长段落中心可能在表格外。
    两个条件取「或」，兼顾这两种情况。
    """
    if _rect_overlap_ratio(block, box) >= _TABLE_OVERLAP_THRESHOLD:
        return True
    cx = (block[0] + block[2]) / 2
    cy = (block[1] + block[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _extract_page_tables(plumber_page) -> list[tuple[TableBox, list[list[str | None]]]]:
    """用 pdfplumber 抽取一页里的表格（默认只识别有框线的表格）。"""
    try:
        found = plumber_page.find_tables()
    except Exception as exc:  # noqa: BLE001 —— 表格失败不能拖垮整页正文
        logger.warning("PDF 表格检测失败（第 {} 页）: {}", plumber_page.page_number, exc)
        return []

    tables: list[tuple[TableBox, list[list[str | None]]]] = []
    for table in found:
        try:
            rows = table.extract()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 表格读取失败（第 {} 页）: {}", plumber_page.page_number, exc)
            continue
        if _is_useful_table(rows):
            tables.append((tuple(table.bbox), rows))
    return tables


def _page_text_excluding_tables(pdf_page, boxes: list[TableBox]) -> str:
    """取一页正文，剔除落在表格区域内的文本块。

    Args:
        pdf_page: PyMuPDF 的 Page。
        boxes: 本页所有表格的 bbox；为空时直接返回整页文本。

    Returns:
        不含表格文字的正文。
    """
    if not boxes:
        return pdf_page.get_text("text")

    kept: list[str] = []
    for block in pdf_page.get_text("blocks"):
        # blocks 元素：(x0, y0, x1, y1, text, block_no, block_type)
        if len(block) < 7 or block[6] != 0:  # 只处理文本块，跳过图片块
            continue
        rect = (block[0], block[1], block[2], block[3])
        if any(_is_block_in_table(rect, box) for box in boxes):
            continue
        text = block[4].strip()
        if text:
            kept.append(text)
    return "\n\n".join(kept)


def _scale_table_boxes(
    pdf_page,
    plumber_page,
    tables: list[tuple[TableBox, list[list[str | None]]]],
) -> list[tuple[TableBox, list[list[str | None]]]]:
    """把 pdfplumber 的 bbox 换算到 PyMuPDF 的页面坐标系。

    两者都使用「左上角为原点」的 PDF 点，正常情况下比例就是 1；
    这里仍做一次缩放，兼容页面尺寸声明不一致的 PDF。
    """
    width = getattr(plumber_page, "width", 0) or 0
    height = getattr(plumber_page, "height", 0) or 0
    if width <= 0 or height <= 0:
        return tables

    sx = pdf_page.rect.width / width
    sy = pdf_page.rect.height / height
    return [
        ((box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy), rows)
        for box, rows in tables
    ]


# ---------------------------------------------------------------------------
# PDF 解析器
# ---------------------------------------------------------------------------
@register_loader(".pdf")
def _load_pdf(path: Path) -> list[Segment]:
    """解析 PDF 正文与表格，按页产出 Segment（对应 T-1.3 / T-1.4 / FR-02）。

    输出结构
    --------
    每页最多产出两类 Segment：

    - 正文：``kind=KIND_TEXT``，已剔除表格区域，避免与表格内容重复；
    - 表格：``kind=KIND_TABLE``，Markdown 文本，整表一个 Segment（T-2.2 不再切分）。

    为什么一页一个正文 Segment
    --------------------------
    FR-06 / FR-09 要求回答能标注「第 x 页」。页码信息一旦在解析阶段被拍平，
    下游无论怎么切块都补不回来，所以这里按页保留边界，页码从 1 开始。

    已知限制（V1 明确接受）
    ----------------------
    - **只取文本层**：扫描件 / 纯图片页会得到空文本，由 :func:`load_document`
      统一告警（扫描件检测见 T-1.7），V1 不做 OCR；
    - **只识别有框线的表格**：pdfplumber 默认按线条判断；无框线表格会留在正文里；
    - **同页顺序不还原**：正文 Segment 在前，表格 Segment 在后，不按实际版式位置交错；
    - **不识别分栏**：双栏论文可能按页面几何顺序而非人类阅读顺序输出；
    - **图片公式会丢失**：PDF 中公式若为图片形式，V1 不做公式识别（FR-02 已注明）；
    - **旋转页面跳过表格抽取**：/Rotate 非 0 时两库坐标原点可能不一致，为避免误删正文，
      该页退化为纯文本（表格文字仍保留在正文中）。

    Args:
        path: PDF 文件路径。

    Returns:
        正文 Segment + 表格 Segment（``page`` 从 1 开始）。

    Raises:
        ValueError: PDF 需要密码才能打开（不尝试破解）。
        pymupdf.FileDataError: 文件损坏或不是合法的 PDF。

    Note:
        ``pymupdf`` 与 ``pdfplumber`` 都延迟导入：只做格式判断或处理 TXT 时
        不必加载 PDF 引擎。表格引擎不可用时自动退化为纯文本，不影响正文入库。
    """
    import pymupdf

    segments: list[Segment] = []
    with pymupdf.open(path) as doc:
        # 有密码的 PDF 直接给出可读错误，由 load_document 收敛到 error 字段。
        # 先试空密码：部分 PDF 只设了「所有者密码」（仅限制编辑/打印），正文仍可读。
        if doc.needs_pass and not doc.authenticate(""):
            raise ValueError("PDF 已加密，需先解除密码保护再导入")

        plumber_doc = None
        try:
            import pdfplumber

            plumber_doc = pdfplumber.open(path)
        except Exception as exc:  # noqa: BLE001 —— 表格引擎不可用时仍要能读正文
            logger.warning("PDF 表格抽取不可用，退化为纯文本: {} ({})", path.name, exc)

        try:
            for page in doc:
                page_no = page.number + 1
                tables: list[tuple[TableBox, list[list[str | None]]]] = []

                # /Rotate 非 0 时两库坐标原点可能不一致；跳过表格抽取，
                # 宁可把表格文字留在正文里，也不要把正文误判成表格内容删掉。
                can_read_tables = (
                    plumber_doc is not None
                    and page.rotation % 360 == 0
                    and page.number < len(plumber_doc.pages)
                )
                if can_read_tables:
                    try:
                        plumber_page = plumber_doc.pages[page.number]
                        tables = _extract_page_tables(plumber_page)
                        tables = _scale_table_boxes(page, plumber_page, tables)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("PDF 表格解析失败（第 {} 页）: {}", page_no, exc)

                body = _page_text_excluding_tables(page, [box for box, _ in tables])
                if body.strip():
                    segments.append(Segment(text=body, page=page_no, kind=KIND_TEXT))

                for _, rows in tables:
                    markdown = _table_to_markdown(rows)
                    if markdown:
                        segments.append(Segment(text=markdown, page=page_no, kind=KIND_TABLE))

                if tables:
                    logger.debug("PDF 第 {} 页抽取到 {} 个表格", page_no, len(tables))
        finally:
            if plumber_doc is not None:
                plumber_doc.close()

    logger.debug("PDF 解析完成: {} -> {} 段", path.name, len(segments))
    return segments


# ---------------------------------------------------------------------------
# Word 解析器（T-1.5）
# ---------------------------------------------------------------------------
#: Word 内置标题样式：英文 ``Heading N`` / 中文「标题 N」；``Title`` 归为一级
_HEADING_STYLE_RE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)


def _heading_level(paragraph) -> int | None:
    """返回段落的标题级别（1~6）；正文返回 ``None``。

    Args:
        paragraph: python-docx 的 Paragraph。

    Returns:
        标题级别；不是标题样式时返回 ``None``。
    """
    style = getattr(paragraph, "style", None)
    name = (getattr(style, "name", "") or "").strip()

    match = _HEADING_STYLE_RE.match(name)
    if match:
        # Word 允许 1~9 级标题，Markdown 最多 6 个井号，多出来的按 6 级处理
        return min(int(match.group(1)), 6)
    if name.lower() == "title":
        return 1
    return None


def _docx_table_rows(table) -> list[list[str | None]]:
    """把 python-docx 的表格转成行列数据，复用 :func:`_table_to_markdown`。

    Args:
        table: python-docx 的 Table。

    Returns:
        每行一个单元格文本列表。合并单元格会被 python-docx 按行重复返回。
    """
    return [[cell.text for cell in row.cells] for row in table.rows]


@register_loader(".docx")
def _load_docx(path: Path) -> list[Segment]:
    """解析 Word 文档的段落与表格（对应 T-1.5 / FR-02）。

    输出结构
    --------
    - 正文：``kind=KIND_TEXT``，连续段落攒成一个 Segment；
    - 表格：``kind=KIND_TABLE``，Markdown 文本，整表一个 Segment（T-2.2 不切分）。

    遍历用 ``Document.iter_inner_content()`` 按文档真实顺序拿到段落和表格；
    遇到表格时先把前面的段落落盘，因此正文与表格的相对顺序不会被拆散。
    如果分开取 ``doc.paragraphs`` 和 ``doc.tables``，两者的先后关系就丢了。

    标题层级
    --------
    用 Markdown 井号表达：Heading 1 → ``# ...``，Heading 2 → ``## ...``，
    这样下游分块和检索也能看到层级信息。Word 的页码依赖排版，python-docx
    拿不到可靠页码，所以统一 ``page=0``（与 TXT 一致）。

    已知限制（V1 明确接受）
    ----------------------
    - 只支持 ``.docx``；旧二进制 ``.doc`` 不在支持范围；
    - 页眉/页脚、脚注、批注、文本框、图片不抽取；
    - 合并单元格会被 python-docx 按行重复返回，Markdown 中可能出现重复文字；
    - 嵌套表格只取外层单元格的段落文本，内层表格不单独展开；
    - 只认内置 Heading 样式（含中文“标题 N”）与 Title，手工加粗的“标题”没有层级。

    Args:
        path: Word 文件路径。

    Returns:
        正文 Segment + 表格 Segment（``page=0``）。

    Raises:
        docx.opc.exceptions.PackageNotFoundError: 不是合法的 docx / 已损坏。
    """
    from docx import Document
    from docx.table import Table

    document = Document(str(path))
    segments: list[Segment] = []
    buffer: list[str] = []

    def flush() -> None:
        """把缓冲区里的连续段落落成一个正文 Segment。"""
        nonlocal buffer
        if buffer:
            segments.append(Segment(text="\n\n".join(buffer), page=0, kind=KIND_TEXT))
            buffer = []

    for item in document.iter_inner_content():
        if isinstance(item, Table):
            flush()  # 先落盘表格之前的段落，保证正文/表格顺序
            markdown = _table_to_markdown(_docx_table_rows(item))
            if markdown:
                segments.append(Segment(text=markdown, page=0, kind=KIND_TABLE))
            continue

        text = item.text.strip()
        if not text:
            continue
        level = _heading_level(item)
        buffer.append(f"{'#' * level} {text}" if level else text)

    flush()
    logger.debug("DOCX 解析完成: {} -> {} 段", path.name, len(segments))
    return segments


__all__ = [
    "BatchLoadResult",
    "KIND_TABLE",
    "KIND_TEXT",
    "LoadedDocument",
    "LoaderFunc",
    "Segment",
    "decode_text",
    "is_supported",
    "load_directory",
    "load_document",
    "load_documents",
    "normalize_text",
    "register_loader",
    "supported_extensions",
]
