"""健康检查接口（对应 T-5.2）。"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """最小健康检查。

    不读取 Key、不打开 Chroma；只要进程能响应就返回 ok。
    索引统计在 T-5.3 文档接口接入后再补充。
    """
    return {
        "status": "ok",
        "version": __version__,
        "mode": "local",
    }
