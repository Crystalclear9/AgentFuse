"""FastAPI application factory for the local AgentFuse dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..app import AgentFuseApp
from ..settings import RuntimeSettings, load_settings
from .routes import benchmarks as benchmark_routes
from .routes import executions as execution_routes
from .routes import overview as overview_routes
from .routes import tools as tool_routes
from .routes import workflows as workflow_routes

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: RuntimeSettings | None = None) -> FastAPI:
    """Create the local web application (used by ``agentfuse serve``)."""

    settings = settings or load_settings()
    runtime = AgentFuseApp(settings=settings)

    app = FastAPI(
        title="AgentFuse",
        description="面向多智能体工作流的共享执行与资源管理 Runtime - 本地管理界面",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.runtime = runtime
    app.state.benchmark_jobs: dict[str, dict[str, Any]] = {}

    app.include_router(overview_routes.router)
    app.include_router(workflow_routes.router)
    app.include_router(execution_routes.router)
    app.include_router(benchmark_routes.router)
    app.include_router(tool_routes.router)

    if WEB_ROOT.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_ROOT)), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(str(WEB_ROOT / "index.html"))

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - server lifecycle
        await runtime.close()

    return app


async def run_server(settings: RuntimeSettings | None = None) -> None:  # pragma: no cover
    import uvicorn

    settings = settings or load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.api_log_level,
    )
