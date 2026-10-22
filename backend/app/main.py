"""FastAPI application entry point.

The API is a thin HTTP layer over the application services; all scheduling
decisions live in ``app.domain`` and all persistence in ``app.services``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.core.csrf import CookieCsrfMiddleware
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.session import Base, engine
from app.services.bootstrap import seed_defaults

logger = logging.getLogger(__name__)

DESCRIPTION = """
Professor-facing adaptive syllabus and lecture planning.

* Upload a syllabus and confirm the extracted structure.
* Generate a deterministic, dependency-aware lecture schedule.
* Mark lectures completed, missed, or cancelled and let the remaining schedule
  be recalculated — as a proposal you approve before it is published.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    if settings.app_env.lower() in {"development", "dev", "test"}:
        # Local development runs on SQLite; production uses Alembic migrations.
        Base.metadata.create_all(bind=engine)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    seed_defaults()
    logger.info("Application started", extra={"environment": settings.app_env})
    yield
    logger.info("Application stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Registered before CORS so that CORS wraps this guard and decorates its
    # rejection responses with the headers a browser client needs to read them.
    app.add_middleware(CookieCsrfMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/health", tags=["meta"], summary="Liveness and configuration probe")
    def health() -> JSONResponse:
        # Unauthenticated probe: report liveness and coarse configuration only.
        # Filesystem paths and secrets are never exposed here.
        return JSONResponse(
            {
                "status": "ok",
                "environment": settings.app_env,
                "llm_provider": settings.llm_provider,
                "llm_enabled": settings.llm_enabled,
            }
        )

    frontend_dir = settings.resolved_frontend_dir
    if frontend_dir.is_dir():
        # The professor-facing UI is plain HTML/CSS/JS, served by the API itself.
        app.mount(
            "/app",
            StaticFiles(directory=str(frontend_dir), html=True),
            name="frontend",
        )

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            # The UI uses relative asset paths, so send browsers to the mount point.
            return RedirectResponse(url="/app/")

    return app


app = create_app()
