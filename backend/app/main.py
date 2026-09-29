import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import agent, applications, auth, dashboard, jobs, profile, projects, resumes, runner, templates
from app.api import settings as settings_api
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.errors import AppError
from app.llm.router import configured
from app.resume.latex_compile import resolve_engine

# Importing registers the background task handlers.
from app.services import github_scan, prep  # noqa: F401
from app.tasks.queue import start_workers, stop_workers

log = logging.getLogger("careerpilot")


class _RedactingFilter(logging.Filter):
    """Keeps secrets out of logs even if a library logs a URL or header."""

    import re as _re

    _rx = _re.compile(r"(?i)(key|token|password|secret|authorization|cookie)([\"'=: ]+)([^\s&\"',]+)")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._rx.sub(r"\1\2<redacted>", record.msg)
        return True


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(_RedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _configure_logging()
    get_engine()
    start_workers()
    yield
    stop_workers()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CareerPilot API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        rid = uuid.uuid4().hex[:12]
        request.state.request_id = rid
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        ms = (time.perf_counter() - start) * 1000
        if ms > 2000:
            log.info("slow request %s %s %.0fms", request.method, request.url.path, ms)
        return response

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", [])[1:])
        msg = first.get("msg", "Invalid input").removeprefix("Value error, ")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": f"{loc}: {msg}" if loc else msg,
                    "hint": "",
                    "actions": [],
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        message = "Not found." if exc.status_code == 404 else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": message, "hint": "", "actions": []}},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.error(
            "unhandled error on %s %s (request %s): %s",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", "-"),
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Something went wrong on our side.",
                    "hint": f"Please retry. Reference: {getattr(request.state, 'request_id', '-')}",
                    "actions": ["retry"],
                }
            },
        )

    @app.get("/api/health")
    def health():
        db_ok = True
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        ai_ready = [p for p in configured(None)]
        body = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unavailable",
            "latex_engine": resolve_engine() or "missing",
            "ai_providers": ai_ready,
            "version": app.version,
        }
        return JSONResponse(status_code=200 if db_ok else 503, content=body)

    for module in (
        auth,
        profile,
        templates,
        projects,
        jobs,
        resumes,
        applications,
        runner,
        settings_api,
        dashboard,
        agent,
    ):
        app.include_router(module.router)
    return app


app = create_app()
