"""Database primitives for explicit SQLite persistence and Alembic migrations."""

from trading_assistant.database.base import Base
from trading_assistant.database.engine import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    MAX_SQLITE_BUSY_TIMEOUT_MS,
    configure_sqlite_connection,
    create_database_engine,
    describe_sqlite_configuration,
    is_memory_database,
    is_sqlite_lock_error,
)

__all__ = [
    "Base",
    "DEFAULT_SQLITE_BUSY_TIMEOUT_MS",
    "MAX_SQLITE_BUSY_TIMEOUT_MS",
    "configure_sqlite_connection",
    "create_database_engine",
    "describe_sqlite_configuration",
    "is_memory_database",
    "is_sqlite_lock_error",
]
