"""RAG 知识库 Web 应用入口（对应 FR-12 / T-5.1）。

提供：
- `/` 定稿 UI（聊天 / 文档 / 设置 / 引导）；
- `/api/v1/health` 健康检查；
- 聊天 SSE、文档管理、索引重建、设置 Key API；
- `/docs` Swagger 与 `/static` 静态资源。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.index import router as index_router
from app.api.settings import router as settings_router
from app.config import PROJECT_ROOT
from app.logging_config import logger, setup_logging

TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "app" / "static"


def create_app() -> FastAPI:
    """构造 FastAPI 应用。

    不在 import 阶段读取 Key；健康检查/首页可以在未配置 Key 时启动，
    具体功能再按 CONFIG_MISSING 返回错误。
    """
    setup_logging()

    app = FastAPI(
        title="RAG 本地知识库",
        version=__version__,
        description="本地文档问答 API；仅监听 127.0.0.1。",
    )
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(index_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        """把未处理异常收敛成契约里的 JSON 错误，并留下可排查的 request_id。

        没有这层时，Starlette 会返回纯文本 ``Internal Server Error``；
        前端按 JSON 解析会失败，真实原因完全看不到（V1 调试中实际踩到）。
        本应用只监听回环地址，错误消息里带异常类型与原文，便于本机定位；
        服务器化时应改为只返回 request_id，详细信息只进日志。
        """
        request_id = f"req_{uuid4().hex[:16]}"
        logger.opt(exception=exc).error(
            "未处理异常 [{}] {} {}: {}",
            request_id, request.method, request.url.path, type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL",
                "message": f"{type(exc).__name__}: {exc}",
                "detail": None,
                "retryable": False,
                "request_id": request_id,
            },
        )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"version": __version__},
        )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # 本地单用户：只绑定回环地址，不监听 0.0.0.0。
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
