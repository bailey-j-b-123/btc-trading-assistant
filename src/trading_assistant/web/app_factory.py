"""Import-light factory used by ``uvicorn`` / ``python -m trading_assistant.web``."""

from __future__ import annotations

from fastapi import FastAPI

from trading_assistant.web.app import create_app as _create_app


def create_app() -> FastAPI:
    return _create_app()
