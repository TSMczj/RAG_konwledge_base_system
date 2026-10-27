"""命令行问答（对应 FR-08 / T-4.6）。

用法：
    .venv\\Scripts\\python.exe scripts/ask.py "LM358 的电源电压范围是多少？"
    .venv\\Scripts\\python.exe scripts/ask.py --k 3 "Transformer 的核心结构是什么？"

输出：问题、回答、引用来源（文件名 / 页码 / 分数）。回答没有有效引用时，
QAService 会返回固定兜底话术，不展示无出处内容。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, get_settings
from app.core.qa import Citation, QAResult, QAService
from app.logging_config import logger, setup_logging


def _print_citation(citation: Citation) -> None:
    page = f"第 {citation.page} 页" if citation.page > 0 else "无页码"
    print(f"  [{citation.n}] {citation.file_name} · {page} · {citation.kind} · {citation.score:.3f}")
    excerpt = citation.excerpt.replace("\n", " ").strip()
    if excerpt:
        print(f"      {excerpt[:180]}")


def _print_result(result: QAResult) -> None:
    print("=" * 68)
    print(f"问题：{result.question}")
    print(f"回答：{result.answer}")
    if result.citations:
        print("来源：")
        for citation in result.citations:
            _print_citation(citation)
    elif getattr(result, "chat_mode", False):
        print("来源：（闲聊，不需要引用）")
    elif result.no_context:
        print("来源：（知识库中没有可用依据）")
    else:
        print("来源：（回答未产生有效引用）")
    print(f"耗时：{result.elapsed_ms / 1000:.2f}s")
    print("=" * 68)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="RAG 知识库命令行问答")
    parser.add_argument("question", nargs="?", help="问题；缺省进入交互输入")
    parser.add_argument("--k", type=int, default=None, help="检索条数（默认配置 TOP_K）")
    args = parser.parse_args(argv)

    setup_logging()

    try:
        settings = get_settings()
    except ConfigError as err:
        print()
        print("配置检查未通过：")
        print(err)
        return 1

    question = (args.question or "").strip()
    if not question:
        try:
            question = input("问题：").strip()
        except EOFError:
            print("未输入问题。")
            return 1
    if not question:
        print("问题不能为空。")
        return 1

    service = QAService(settings=settings)
    try:
        result = service.answer(question, k=args.k)
    except Exception as exc:  # noqa: BLE001 —— CLI 需要给出可读错误
        logger.error("问答失败: {}: {}", type(exc).__name__, exc)
        print(f"问答失败：{type(exc).__name__}: {exc}")
        return 1

    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
