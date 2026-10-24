"""统一日志配置（基于 loguru）。

- 控制台输出带颜色，方便本地调试；
- 文件输出到 ``logs/app.log``，按大小滚动、自动清理旧文件；
- 把标准库 ``logging`` 的日志（如 uvicorn、chromadb）也接管进来，避免两套日志格式。

用法
----
在程序入口调用一次即可（重复调用是安全的）::

    from app.logging_config import setup_logging, logger

    setup_logging()
    logger.info("服务启动")
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import PROJECT_ROOT

#: 日志目录与文件
LOG_DIR: Path = PROJECT_ROOT / "logs"
LOG_FILE: Path = LOG_DIR / "app.log"

#: 控制台格式（带颜色）
_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

#: 文件格式（纯文本，含毫秒与完整路径）
_FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "{name}:{function}:{line} - {message}"
)

_configured = False


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发给 loguru，并保留原始来源信息。

    loguru 的 ``opt(depth=...)`` 很难稳定算准层级，因此改为直接用原记录的
    ``name`` / ``funcName`` / ``lineno`` 覆盖 loguru 的记录字段，
    这样日志里显示的就是真正的调用位置（而不是 ``logging:callHandlers``）。
    """

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 由日志框架调用
        # 找到与标准库 level 对应的 loguru level 名
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        def _patch(rec: Any, _src: logging.LogRecord = record) -> None:
            # 用标准库记录里的真实来源覆盖 loguru 字段
            rec.update(name=_src.name, function=_src.funcName, line=_src.lineno)

        logger.patch(_patch).opt(exception=record.exc_info).log(level, record.getMessage())


def setup_logging(level: str | None = None) -> None:
    """初始化全局日志。

    Args:
        level: 控制台日志级别，缺省时读环境变量 ``LOG_LEVEL``，再缺省为 ``INFO``。

    Note:
        重复调用只会生效一次，可安全地在入口与测试中调用。
    """
    global _configured
    if _configured:
        return

    console_level = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 移除 loguru 默认的 stderr handler，避免重复输出
    logger.remove()

    # 控制台：带颜色
    logger.add(
        sys.stderr,
        level=console_level,
        format=_CONSOLE_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
    )

    # 文件：滚动 + 保留 + UTF-8
    logger.add(
        str(LOG_FILE),
        level="DEBUG",
        format=_FILE_FORMAT,
        encoding="utf-8",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        # 单进程本地应用不需要跨进程日志队列；enqueue=True 会启用 multiprocessing
        # 队列（Windows 命名管道），在受限沙箱/部分环境下创建管道会被拒绝。
        enqueue=False,
        backtrace=True,
        diagnose=False,
    )

    # 接管标准库 logging（uvicorn / chromadb / openai 等）
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    # 压制噪音：这些库的 INFO 日志（如每次 HTTP 请求）对本地开发没用
    # 注意：openai 3.x 依赖的是新版 httpx2 / httpcore2，日志名与旧版不同，两个都要压
    # pdfminer 尤其重要：DEBUG 级别会把 PDF 每个 token 都写进文件，解析 100 页 PDF
    # 会慢到不可用（实测 10 分钟仍卡在解析，日志涨到 8 MB+）。
    for noisy, noisy_level in (
        ("uvicorn.access", logging.WARNING),
        ("chromadb", logging.WARNING),
        ("httpx", logging.WARNING),
        ("httpx2", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("httpcore2", logging.WARNING),
        ("openai", logging.WARNING),
        ("urllib3", logging.WARNING),
        ("asyncio", logging.WARNING),
        ("huggingface_hub", logging.WARNING),
        ("langchain", logging.WARNING),
        ("pdfminer", logging.WARNING),
        ("pdfplumber", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)

    _configured = True
    logger.debug("日志初始化完成：控制台级别={} 文件={}", console_level, LOG_FILE)


def get_logger(name: str | None = None) -> Any:
    """获取绑定了模块名的 logger（便于过滤日志）。"""
    return logger.bind(module=name) if name else logger


__all__ = ["setup_logging", "get_logger", "logger", "LOG_DIR", "LOG_FILE"]
