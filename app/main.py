"""FastAPI application factory: security middleware, static SPA, worker lifecycle."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api import auth as auth_api
from app.api import jobs as jobs_api
from app.api import settings as settings_api
from app.api.auth import ensure_bootstrap_admin
from app.config import get_settings
from app.db.database import db_session
from app.services import job_service

logger = logging.getLogger("tabledger")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response


def _run_migrations() -> None:
    """Apply Alembic migrations at startup; a failure aborts startup safely
    without touching or deleting any existing data."""

    from alembic import command
    from alembic.config import Config

    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "db" / "migrations"))
    try:
        command.upgrade(alembic_cfg, "head")
    except Exception:
        logger.exception("database migration failed; refusing to start")
        raise


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.version, docs_url=None, redoc_url=None, openapi_url=None)

    allowed_hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    if allowed_hosts and allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(auth_api.router)
    app.include_router(jobs_api.router)
    app.include_router(settings_api.router)

    @app.on_event("startup")
    def on_startup() -> None:
        settings.ensure_secret_key()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _run_migrations()
        with db_session() as db:
            ensure_bootstrap_admin(db)
            interrupted = job_service.mark_interrupted_jobs(db)
            if interrupted:
                logger.info("marked %s jobs as interrupted", interrupted)
        job_service.get_worker().start()
        logger.info("TabLedger %s started", settings.version)

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        job_service.get_worker().stop()

    @app.api_route("/api/v1/health", methods=["GET", "HEAD"])
    def health() -> dict:
        return {"ok": True, "version": settings.version}

    # SPA static files (built frontend); the API keeps precedence.
    configured_static = getattr(settings, "static_dir_override", None)
    candidates = [
        Path(configured_static) if configured_static else None,
        settings.data_dir / "web",
        Path(__file__).resolve().parent.parent / "web" / "dist",
    ]
    static_dir = next((c for c in candidates if c and c.is_dir()), None)
    if static_dir is not None:
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            candidate = (static_dir / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(static_dir.resolve())):
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend() -> JSONResponse:
            return JSONResponse({"detail": "前端未构建；请运行 pnpm build 后重启"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return app


app = create_app()
