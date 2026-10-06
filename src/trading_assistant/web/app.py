"""FastAPI application factory: typed JSON API plus the static dashboard UI.

Security posture for a private application:

* JSON-only error responses (no HTML error pages leaking internals),
* strict Content-Security-Policy with no inline script/style allowance,
* no secrets and no exchange credentials anywhere in the served bundle,
* decision endpoints validated by pydantic schemas with explicit enum values.

Authentication is intentionally NOT faked here: deployment behind a trusted
reverse proxy or an identity layer is documented as a prerequisite for any
internet-facing use.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine

from trading_assistant.config import Settings
from trading_assistant.statistics.config import StatisticsConfig
from trading_assistant.web.dashboard_service import DashboardError
from trading_assistant.web.routers import (
    dashboard as dashboard_router,
)
from trading_assistant.web.routers import (
    forward as forward_router,
)
from trading_assistant.web.routers import (
    journal as journal_router,
)
from trading_assistant.web.routers import (
    market as market_router,
)
from trading_assistant.web.routers import (
    meta as meta_router,
)
from trading_assistant.web.routers import (
    settings as settings_router,
)
from trading_assistant.web.routers import (
    statistics as statistics_router,
)
from trading_assistant.web.routers import (
    validation as validation_router,
)
from trading_assistant.web.state import AppState, create_default_state

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _error_body(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message}}


def create_app(
    *,
    engine: Engine | None = None,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    statistics_config: StatisticsConfig | None = None,
    state: AppState | None = None,
) -> FastAPI:
    """Build the dashboard application over an explicit (or configured) engine."""

    resolved_state = state
    if resolved_state is None:
        if engine is not None:
            resolved_state = AppState(
                engine,
                settings=settings,
                clock=clock,
                statistics_config=statistics_config,
            )
        else:
            resolved_state = create_default_state(
                settings=settings, clock=clock, statistics_config=statistics_config
            )

    app = FastAPI(
        title="BTC Trading Assistant Dashboard",
        description=(
            "Read-only presentation layer over deterministic Steps 1-12, "
            "including live forward paper observations. "
            "No order execution, no exchange authentication, no account access."
        ),
        version="12.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.services = resolved_state

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.exception_handler(DashboardError)
    async def dashboard_error_handler(request: Request, exc: DashboardError):
        if exc.code.endswith("not_found"):
            status = 404
        elif "not_decidable" in exc.code or "not_observable" in exc.code:
            status = 409
        else:
            status = 400
        return JSONResponse(
            status_code=status, content=_error_body(exc.code, exc.message)
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400, content=_error_body("validation_error", str(exc))
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            code = exc.detail.get("code", f"http_{exc.status_code}")
            message = exc.detail.get("message", str(exc.detail))
        else:
            code = f"http_{exc.status_code}"
            message = str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled web API error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_error",
                "an unexpected error occurred; details were logged server-side only",
            ),
        )

    app.include_router(meta_router.router)
    app.include_router(market_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(journal_router.router)
    app.include_router(statistics_router.router)
    app.include_router(validation_router.router)
    app.include_router(forward_router.router)
    app.include_router(settings_router.router)

    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
