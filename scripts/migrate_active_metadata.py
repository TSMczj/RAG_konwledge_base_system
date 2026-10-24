"""一次性迁移：给旧索引补 `active=True` metadata（T-5.3）。

只更新 metadata，不重新计算 embedding，不消耗嵌入额度。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import ConfigError, get_settings
from app.core.vectorstore import VectorStore
from app.logging_config import setup_logging


def main() -> int:
    setup_logging()
    try:
        settings = get_settings()
    except ConfigError as err:
        print("配置检查未通过：")
        print(err)
        return 1

    store = VectorStore(settings=settings)
    try:
        before = store.count()
        updated = store.ensure_active_metadata()
        print(f"库内总量: {before}")
        print(f"补齐 active metadata: {updated}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
