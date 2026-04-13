from __future__ import annotations

from fastapi import FastAPI

from api.routes.task import router as task_router
from api.routes.workflow import router as workflow_router
from services.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="MCN content workflow service",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(workflow_router, prefix=settings.api_prefix)
    app.include_router(task_router, prefix=settings.api_prefix)
    return app
