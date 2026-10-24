"""索引管理 CLI（对应 T-3.5 / FR-05）。

用法（在项目根目录执行）::

    .venv\\Scripts\\python.exe scripts/manage_index.py --list
    .venv\\Scripts\\python.exe scripts/manage_index.py --delete <file_hash 或文件名> --yes
    .venv\\Scripts\\python.exe scripts/manage_index.py --rebuild --yes

说明
----
- `--list` 按 `file_hash` 聚合展示文件名、chunk 数、页数、入库时间；
- `--delete` 的值是 64 位十六进制时按 `file_hash` 删除，否则按原始文件名删除；
- `--rebuild` 先清空 collection，再复用 T-3.4 的 `build_index` 重新建库；
- 删除/重建默认需要确认，`--yes` 可跳过（便于脚本调用）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 让脚本能 import 到 app 包（脚本位于 <root>/scripts/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, Settings, get_settings  # noqa: E402
from app.core.vectorstore import IndexedFile, VectorStore  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402
from scripts.build_index import build_index, print_summary  # noqa: E402

#: 64 位十六进制 → 按 file_hash 删除；否则按文件名删除
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _print_files(files: list[IndexedFile]) -> None:
    """打印已入库文件表。"""
    if not files:
        print("知识库为空。")
        return

    print(f"{'FILE_HASH':<66}{'CHUNKS':>7}{'PAGES':>7}  {'TYPE':<7}{'CREATED_AT':<22}SOURCE")
    print("-" * 134)
    for item in files:
        print(
            f"{item.file_hash:<66}{item.chunks:>7}{item.pages:>7}  "
            f"{item.doc_type:<7}{item.created_at[:19]:<22}{item.source_file}"
        )
    print()
    print(f"共 {len(files)} 个文件，{sum(item.chunks for item in files)} 个 chunk。")


def _confirm(prompt: str, assume_yes: bool) -> bool:
    """交互确认；EOF 或非 y 视为取消。"""
    if assume_yes:
        return True
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _delete(store: VectorStore, target: str) -> int:
    """按 hash 或文件名删除，返回实际删除条数。"""
    if _HASH_RE.match(target):
        count = store.delete_by_file_hash(target.lower())
        print(f"按 file_hash 删除 {target[:12]}… -> {count} 个 chunk")
    else:
        count = store.delete_by_source(target)
        print(f"按文件名删除 {target} -> {count} 个 chunk（同名不同内容会被一起删除）")
    return count


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="RAG 知识库索引管理：列出 / 删除 / 重建",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出已入库文件（默认动作）")
    action.add_argument(
        "--delete",
        metavar="HASH_OR_FILE",
        help="按 file_hash（64 位十六进制）或原始文件名删除",
    )
    action.add_argument("--rebuild", action="store_true", help="清空后重新建库")

    parser.add_argument("--dir", type=Path, default=None, help="重建时的目录（默认 UPLOAD_DIR）")
    parser.add_argument("--recursive", action="store_true", help="重建时递归子目录")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过删除/重建确认")
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

    try:
        store = VectorStore(settings=settings)
    except Exception as exc:  # noqa: BLE001
        print(f"向量库初始化失败: {type(exc).__name__}: {exc}")
        return 1

    try:
        if args.delete:
            if not _confirm(f"确认删除 {args.delete}？[y/N] ", args.yes):
                print("已取消。")
                return 1
            deleted = _delete(store, args.delete)
            print(f"库内总量: {store.count()}")
            return 0 if deleted else 1

        if args.rebuild:
            current = store.count()
            if not _confirm(f"重建会清空当前 {current} 个 chunk，确认？[y/N] ", args.yes):
                print("已取消。")
                return 1

            deleted = store.delete_all()
            print(f"已清空 {deleted} 个 chunk，开始重建…")
            stats = build_index(
                args.dir or settings.upload_dir,
                recursive=args.recursive,
                store=store,
                settings=settings,
            )
            print_summary(stats, store)
            return 0 if not stats.failures and stats.index_failed == 0 else 1

        _print_files(store.list_files())
        return 0
    finally:
        # 保证 HNSW 索引落盘，避免出现只有 metadata 的坏库
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
