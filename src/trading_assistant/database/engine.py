"""SQLite-aware SQLAlchemy engine construction without implicit schema changes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine, URL

from trading_assistant.config import get_settings


def create_database_engine(
    database_url: str | URL | None = None,
    **engine_options: Any,
) -> Engine:
    """Create an engine without connecting, creating tables, or running migrations.

    A SQLite database file is created by SQLite only when a connection is first
    opened. Existing files are opened in place and are never removed or replaced.
    Schema changes are intentionally left to explicit Alembic commands.
    """

    resolved_url = database_url if database_url is not None else get_settings().database_url
    engine = sqlalchemy_create_engine(resolved_url, **engine_options)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

    return engine
