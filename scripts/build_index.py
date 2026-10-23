"""建库 CLI（对应 T-3.4 / FR-04）。

一条命令完成：扫描目录 → 解析 → 分块 → 嵌入 → 写入 Chroma。

用法（在项目根目录执行）::

    .venv\\Scripts\\python.exe scripts/build_index.py
    .venv\\Scripts\\python.exe scripts/build_index.py --dir data/uploads --recursive
    .venv\\Scripts\\python.exe scripts/build_index.py --force   # 已入库文件也重新嵌入

说明
----
- 目录默认取 ``UPLOAD_DIR``（``.env``，默认 ``data/uploads``）；
- 已入库文件由 ``VectorStore.index_chunks`` 按 ``file_hash`` 自动跳过；
- 单文件解析/嵌入失败只记入失败清单，不中断整批；
- 真实运行需要系统环境变量里的 ``DASHSCOPE_API_KEY``。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 让脚本能 import 到 app 包（脚本位于 <root>/scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, Settings, get_settings  # noqa: E402
from app.core.loader import load_directory  # noqa: E402
from app.core.splitter import split_document  # noqa: E402
from app.core.vectorstore import VectorStore  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402


@dataclass
class BuildStats:
    """一次建库的统计。"""

    scanned: int = 0
    parsed_ok: int = 0
    parse_failed: int = 0
    skipped_files: int = 0
    empty_docs: int = 0
    indexed_files: int = 0
    already_indexed_files: int = 0
    index_failed: int = 0
    chunks_written: int = 0
    elapsed: float = 0.0
    failures: list[str] = field(default_factory=list)


def build_index(
    upload_dir: str | Path | None = None,
    *,
    recursive: bool = False,
    force: bool = False,
    store: VectorStore | None = None,
    settings: Settings | None = None,
) -> BuildStats:
    """扫描目录并建库，返回统计结果。

    Args:
        upload_dir: 待扫描目录；缺省用配置 ``UPLOAD_DIR``。
        recursive: 是否递归子目录。
        force: 已入库文件也重新嵌入/写入；默认跳过。
        store: 可注入的向量库（测试用）；缺省新建。
        settings: 配置；缺省用 :func:`get_settings`。

    Returns:
        :class:`BuildStats`；失败项同时写入 ``failures``。
    """
    settings = settings or get_settings()
    if store is None:
        store = VectorStore(settings=settings)
    if upload_dir is None:
        upload_dir = settings.upload_dir

    started = time.perf_counter()
    batch = load_directory(upload_dir, recursive=recursive)

    stats = BuildStats(
        scanned=batch.total_files,
        parsed_ok=len(batch.documents),
        parse_failed=len(batch.failures),
        skipped_files=len(batch.skipped),
        empty_docs=len(batch.empty_documents),
    )
    stats.failures.extend(batch.failure_lines())

    total = len(batch.documents)
    for position, doc in enumerate(batch.documents, start=1):
        prefix = f"[{position}/{total}]"

        if not doc.segments:
            print(f"{prefix} 空文本，跳过: {doc.source_file}")
            continue

        try:
            chunks = split_document(doc)
            if not chunks:
                print(f"{prefix} 分块为空，跳过: {doc.source_file}")
                continue
            result = store.index_chunks(chunks, skip_existing=not force)
        except Exception as exc:  # noqa: BLE001 —— 单文件失败不能中断整批
            stats.index_failed += 1
            message = f"{doc.file_path}: {type(exc).__name__}: {exc}"
            stats.failures.append(message)
            print(f"{prefix} 处理失败: {doc.source_file} -> {type(exc).__name__}: {exc}")
            continue

        if result.skipped:
            stats.already_indexed_files += 1
            print(
                f"{prefix} 已存在，跳过: {doc.source_file}"
                f"（{result.count_before} 个 chunk）"
            )
        else:
            stats.indexed_files += 1
            stats.chunks_written += result.indexed
            print(f"{prefix} 入库: {doc.source_file} -> {result.indexed} 个 chunk")

    stats.elapsed = time.perf_counter() - started
    return stats


def _print_summary(stats: BuildStats, store: VectorStore) -> None:
    """打印建库汇总与失败清单。"""
    print()
    print("=" * 64)
    print("建库完成")
    print(f"  扫描文件        : {stats.scanned}")
    print(f"  解析成功        : {stats.parsed_ok}（其中空文本 {stats.empty_docs}）")
    print(f"  解析失败        : {stats.parse_failed}")
    print(f"  跳过（非目标）  : {stats.skipped_files}")
    print(f"  新入库文件      : {stats.indexed_files}")
    print(f"  已存在跳过      : {stats.already_indexed_files}")
    print(f"  处理失败        : {stats.index_failed}")
    print(f"  写入 chunk      : {stats.chunks_written}")
    print(f"  库内总量        : {store.count()}")
    print(f"  耗时            : {stats.elapsed:.2f}s")

    if stats.failures:
        print()
        print("失败清单:")
        for line in stats.failures:
            print(f"  - {line}")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="RAG 知识库建库：扫描目录 -> 解析 -> 分块 -> 嵌入 -> Chroma",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="待建库目录（默认取 .env 的 UPLOAD_DIR）",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="递归扫描子目录",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="已入库文件也重新嵌入并写入（默认按 file_hash 跳过）",
    )
    args = parser.parse_args(argv)

    setup_logging()

    try:
        settings = get_settings()
    except ConfigError as err:
        print()
        print("配置检查未通过：")
        print(err)
        return 1

    settings.ensure_directories()
    upload_dir = args.dir or settings.upload_dir

    print()
    print("=" * 64)
    print("RAG 知识库建库")
    print(f"  目录      : {upload_dir}")
    print(f"  collection: {settings.collection_name}")
    print(f"  嵌入模型  : {settings.embedding_model}（{settings.embedding_dim} 维）")
    print(f"  chunk     : {settings.chunk_size} / overlap {settings.chunk_overlap}")
    print("=" * 64)

    try:
        store = VectorStore(settings=settings)
    except Exception as exc:  # noqa: BLE001 —— 初始化失败给出可读提示
        print(f"向量库初始化失败: {type(exc).__name__}: {exc}")
        return 1

    stats = build_index(
        upload_dir,
        recursive=args.recursive,
        force=args.force,
        store=store,
        settings=settings,
    )
    _print_summary(stats, store)

    return 0 if not stats.failures and stats.index_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
