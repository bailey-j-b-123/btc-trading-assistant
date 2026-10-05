"""Small JSON-lines logging setup using only the Python standard library."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from trading_assistant.config import get_settings


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)
        payload: dict[str, Any] = {
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        fields = getattr(record, "fields", None)
        if isinstance(fields, Mapping):
            payload["fields"] = dict(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | int | None = None) -> None:
    """Configure the root logger with an idempotent JSON stream handler."""

    configured_level: str | int = get_settings().log_level if level is None else level
    if isinstance(configured_level, str):
        resolved_level = logging.getLevelName(configured_level.upper())
        if not isinstance(resolved_level, int):
            raise ValueError(f"Unknown logging level: {configured_level!r}")
    else:
        resolved_level = configured_level

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)
    if not any(getattr(handler, "_trading_assistant_json", False) for handler in root_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._trading_assistant_json = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)
