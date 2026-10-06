"""Startup preflight checks for the dashboard CLI.

``create_app()`` intentionally performs no migrations or schema changes, so
starting the server on a fresh checkout used to fail with a low-level
database traceback. The ``python -m trading_assistant.web`` entry point runs
:func:`run_preflight_checks` first and reports the exact fix instead.

The checks are read-only: they never create files, connect-and-create a
SQLite database, or run migrations.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


class PreflightError(RuntimeError):
    """A startup problem with a known, actionable fix."""


@dataclass(frozen=True)
class PreflightReport:
    """Outcome of the read-only startup checks."""

    database_url: str
    schema_revision: str | None
    candle_count: int | None
    warnings: tuple[str, ...]


def _sqlite_file_path(database_url: str) -> Path | None:
    """Return the filesystem path for a SQLite URL, else ``None``."""

    try:
        url = make_url(database_url)
    except Exception:  # noqa: BLE001 - any unparseable URL skips the file check
        return None
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        return None
    database = url.database or ""
    if database == ":memory:":
        return None
    path = Path(database)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


@contextmanager
def _quiet_alembic_setup_logging():
    """Silence Alembic's noisy plugin-setup INFO logs during head lookup."""

    logger = logging.getLogger("alembic")
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _expected_heads() -> tuple[str, ...] | None:
    """Return migration head revisions, or ``None`` when unknowable."""

    if not (MIGRATIONS_DIR / "env.py").exists():
        return None
    try:
        # The import itself emits Alembic's plugin-setup INFO logs, so it
        # stays inside the quiet block together with the head lookup.
        with _quiet_alembic_setup_logging():
            from alembic.script import ScriptDirectory

            return tuple(ScriptDirectory(str(MIGRATIONS_DIR)).get_heads())
    except ImportError:  # pragma: no cover - alembic is a core dependency
        return None
    except Exception:  # noqa: BLE001 - fall back to table-presence checks
        return None


def run_preflight_checks(database_url: str) -> PreflightReport:
    """Verify the database exists and is migrated; raise :class:`PreflightError`.

    Emits warnings (returned in the report) for non-fatal conditions such as
    an empty candle store, which makes the dashboard show NO TRADE / UNKNOWN
    rather than fail.
    """

    sqlite_path = _sqlite_file_path(database_url)
    if sqlite_path is not None and not sqlite_path.exists():
        raise PreflightError(
            f"database file not found: {sqlite_path}\n"
            "The dashboard never creates or migrates the database by itself.\n"
            "From the repository root, run:\n"
            "  alembic upgrade head"
        )

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            try:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
            except Exception:  # noqa: BLE001 - missing table means unmigrated
                raise PreflightError(
                    "database exists but has no migration stamp "
                    "(missing alembic_version table).\n"
                    "From the repository root, run:\n"
                    "  alembic upgrade head"
                ) from None

            heads = _expected_heads()
            if heads and revision not in heads:
                raise PreflightError(
                    f"database schema is at revision {revision!r} but the "
                    f"migrations head is {', '.join(heads)}.\n"
                    "From the repository root, run:\n"
                    "  alembic upgrade head"
                ) from None

            inspector = inspect(connection)
            if "ohlcv_candles" not in inspector.get_table_names():
                raise PreflightError(
                    "database is missing the ohlcv_candles table.\n"
                    "From the repository root, run:\n"
                    "  alembic upgrade head"
                ) from None
            candle_count = connection.execute(
                text("SELECT COUNT(*) FROM ohlcv_candles")
            ).scalar()
    finally:
        engine.dispose()

    warnings: list[str] = []
    if candle_count == 0:
        warnings.append(
            "no stored candles: the dashboard will show NO TRADE with an "
            "UNKNOWN freshness badge until it has data. Download real history "
            "with `python scripts/download_history.py --all-timeframes` (needs "
            "internet), or preview the UI with labelled synthetic data via "
            "`python scripts/seed_synthetic_demo.py`."
        )
    return PreflightReport(
        database_url=database_url,
        schema_revision=str(revision) if revision is not None else None,
        candle_count=int(candle_count) if candle_count is not None else None,
        warnings=tuple(warnings),
    )
