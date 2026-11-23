# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2025-2026  Philipp Emanuel Weidmann <pew@worldwidemann.com> + contributors

"""FastAPI application factory.

``create_app`` is importable without torch. The server can therefore run on a
small CPU-only host that only manages models, plans resources and serves the
dashboard, while model modification is delegated to a machine with the engine.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ... import PLUGIN_API_VERSION, __platform_name__, __version__
from ...config import PlatformSettings
from ...context import PlatformContext
from .errors import register_exception_handlers
from .jobs import JobManager
from .middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from .routes import evaluation, experiments, jobs, misc, models, system

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _frontend_dist(settings: PlatformSettings) -> Path | None:
    candidates: list[Path] = []
    if settings.frontend_dist:
        candidates.append(Path(settings.frontend_dist).expanduser())
    repository_root = Path(__file__).resolve().parents[4]
    candidates.append(repository_root / "frontend" / "dist")
    candidates.append(Path.cwd() / "frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def create_app(
    settings: PlatformSettings | None = None,
    *,
    context: PlatformContext | None = None,
) -> FastAPI:
    ctx = context or PlatformContext(settings)
    settings = ctx.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.ctx = ctx
        app.state.jobs = JobManager(ctx.store, ctx.logger, max_workers=2)
        ctx.log(
            f"{__platform_name__} API started on {settings.api_host}:{settings.api_port}.",
            category="api",
            level="audit",
        )
        try:
            yield
        finally:
            app.state.jobs.shutdown(wait=False)
            ctx.log("API stopped.", category="api", level="audit")
            ctx.close()

    docs_enabled = bool(settings.api_docs_enabled)
    app = FastAPI(
        title=f"{__platform_name__} API",
        description=(
            "Model engineering platform API. All endpoints validate input with "
            "Pydantic; jobs are cancellable and persisted in SQLite."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
    )
    app.state.ctx = ctx

    register_exception_handlers(app)

    # Last added = outermost. Host validation runs before anything else.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.api_max_upload_bytes)
    app.add_middleware(
        RateLimitMiddleware, limit_per_minute=settings.api_rate_limit_per_minute
    )
    app.add_middleware(
        OriginCheckMiddleware, allowed_origins=list(settings.api_cors_origins)
    )
    # Added last among the inner middlewares so its headers also decorate
    # OriginCheck and rate-limit rejections.
    app.add_middleware(RequestContextMiddleware)
    allowed_hosts = set(LOOPBACK_HOSTS)
    if settings.api_host not in {"0.0.0.0", "::"}:
        allowed_hosts.add(settings.api_host)
    allowed_hosts.update(settings.api_allowed_hosts)
    allow_any = "*" in allowed_hosts
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"] if allow_any else sorted(allowed_hosts),
        www_redirect=False,
    )

    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.api_cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "X-API-Key",
                "Authorization",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
            max_age=600,
        )

    from .auth import require_auth

    app.include_router(system.public_router, prefix="/api/v1")
    for router in (
        system.router,
        models.router,
        jobs.router,
        experiments.router,
        evaluation.router,
        misc.router,
    ):
        app.include_router(
            router, prefix="/api/v1", dependencies=[Depends(require_auth)]
        )

    dist = _frontend_dist(settings) if settings.serve_frontend else None
    if dist:
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request):
            if path.startswith(("api/", "api")):
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": {
                            "code": "not_found",
                            "message": f"No API route for /{path}",
                        }
                    },
                )
            candidate = (dist / path).resolve()
            if dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {
                "platform": __platform_name__,
                "version": __version__,
                "plugin_api_version": PLUGIN_API_VERSION,
                "api": "/api/v1",
                "health": "/api/v1/health",
                "dashboard": (
                    "Frontend build not found. Run `npm install && npm run build` in frontend/."
                    if settings.serve_frontend
                    else "Dashboard serving disabled."
                ),
            }

    if settings.api_host not in LOOPBACK_HOSTS and not settings.api_key:
        logging.getLogger("fsociety").warning(
            "API is bound to %s without an API key. Remote requests will be refused "
            "until FSOCIETY_API_KEY is set.",
            settings.api_host,
        )

    return app
