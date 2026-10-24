"""RAG 知识库 Web 应用入口（对应 FR-12 / T-5.1）。

当前阶段只提供：
- `/` 占位首页；
- `/api/v1/health` 健康检查；
- `/docs` Swagger；
- `/static` 静态资源挂载。

文档接口 / 聊天 SSE 会在 T-5.3 / T-5.4 接入；首页模板在 UI 定稿后再替换。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.index import router as index_router
from app.config import PROJECT_ROOT
from app.logging_config import setup_logging

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
