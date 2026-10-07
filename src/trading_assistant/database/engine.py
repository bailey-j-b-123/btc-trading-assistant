"""SQLite-aware SQLAlchemy engine construction without implicit schema changes.

One intentional SQLite runtime configuration
--------------------------------------------
Every SQLite engine created by this module is configured exactly once, in the
``connect`` event hook, so every pooled connection - including connections
created after a failure, a restart, or a pool reset - carries the same runtime
settings. There is deliberately no second place that configures SQLite.

``journal_mode=WAL`` (file-backed databases only)
    The documented operating model is *one forward writer plus one read-only
    dashboard* (``README``, "Two processes must be running"). In SQLite's
    default rollback-journal mode a reader holds a SHARED lock for as long as
    its read statement/transaction is active, and the writer needs an EXCLUSIVE
    lock to commit, so a single slow dashboard read (or any other connection
    with an open read transaction, in this process or another) blocks *every*
    ledger write. The writer waits for the busy timeout and then fails with
    ``sqlite3.OperationalError: database is locked`` - one dashboard read is
    enough to break a forward pass. WAL removes that coupling: readers never
    block the writer and the writer never blocks readers, so the two documented
    processes can run at the same time. WAL is a persistent property of the
    database file, so it survives restarts; it is requested by every connection
    (idempotent) and never rewrites or deletes rows.

``busy_timeout`` (finite, configurable)
    A second line of defence, not the fix. WAL serializes *writers*, and the
    dashboard also writes (an explicit journal decision), so two writers can
    overlap for a few milliseconds. A finite busy timeout lets the second writer
    wait for that short window instead of failing immediately. It is
    deliberately finite (``Settings.sqlite_busy_timeout_ms``, 5 seconds by
    default, capped at 60 seconds): a huge timeout is not a repair, it only
    makes a broken lock situation take longer to surface. Every write
    transaction in this codebase is a short insert-only transaction - exchange
    access, analysis, replay, planning, explanation and reporting all happen
    outside any open transaction - so no legitimate write ever needs a longer
    wait.

``foreign_keys=ON``
    Required by the schema: journal and forward rows reference their parents
    with ``ON DELETE RESTRICT`` foreign keys, and SQLite ignores them unless the
    pragma is enabled per connection.

Pooling
    SQLAlchemy's default ``QueuePool`` for file databases is kept. It is
    thread-safe (the dashboard serves synchronous endpoints from a thread pool),
    and it resets a connection when it is returned to the pool
    (``pool_reset_on_return='rollback'``), so a failed pass can never hand a
    connection with an open write transaction to the next caller. Neither
    ``NullPool`` (a fresh connection per query, and more chances to interleave
    writers) nor a shared ``StaticPool`` (the dashboard would queue behind the
    writer, defeating WAL) is appropriate here.

A database file is created by SQLite only when a connection is first opened.
Existing files are opened in place and are never removed or replaced. Schema
changes are intentionally left to explicit Alembic commands.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy import event, text as sql_text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import DBAPIError

from trading_assistant.config import get_settings

logger = logging.getLogger(__name__)

#: Finite default busy timeout in milliseconds. The pysqlite DBAPI default is
#: the same 5 seconds but implicit and invisible; it is stated here explicitly
#: so it is reviewable, configurable and documented (see the module docstring).
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5_000

#: Upper bound accepted for the configurable busy timeout. A large timeout is
#: not a repair for a lock problem, so it is intentionally not unbounded.
MAX_SQLITE_BUSY_TIMEOUT_MS = 60_000


def _is_memory_database(url: URL) -> bool:
    """True for in-memory databases, where WAL does not exist.

    Covers ``sqlite://``, ``sqlite:///:memory:`` and URI databases such as
    ``sqlite:///file:shared?mode=memory&cache=shared&uri=true``.
    """

    database = url.database
    if not database:
        return True
    if database == ":memory:":
        return True
    if database.startswith("file:"):
        return ":memory:" in database or "mode=memory" in database
    return False


def _resolve_busy_timeout_ms(explicit: int | None) -> int:
    """The finite busy timeout in milliseconds, clamped to the documented cap."""

    configured = (
        int(get_settings().sqlite_busy_timeout_ms) if explicit is None else int(explicit)
    )
    return max(0, min(configured, MAX_SQLITE_BUSY_TIMEOUT_MS))


def configure_sqlite_connection(
    dbapi_connection: Any,
    *,
    busy_timeout_ms: int,
    enable_wal: bool,
) -> None:
    """Apply the documented SQLite runtime configuration to one connection.

    Kept as a module-level function (not a nested closure) so the configuration
    can be exercised directly by tests and reused by any future engine factory.
    """

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        # Required by the schema's ON DELETE RESTRICT foreign keys; the pragma
        # is per connection, so it is enforced here for every pooled connection.
        cursor.execute("PRAGMA foreign_keys = ON")
    finally:
        cursor.close()

    if not enable_wal:
        # ``:memory:`` databases have no journal file to switch; requesting WAL
        # would only report "memory".
        return

    cursor = dbapi_connection.cursor()
    resolved_mode: str | None = None
    try:
        cursor.execute("PRAGMA journal_mode = WAL")
        row = cursor.fetchone()
        resolved_mode = None if row is None else str(row[0]).lower()
    except Exception as exc:  # noqa: BLE001 - a read-only or exotic database must
        # still open; the mode is reported below instead of failing the connection.
        logger.warning(
            "SQLite write-ahead logging could not be enabled (%s: %s); the "
            "database still works, but reader/writer contention is not removed",
            type(exc).__name__,
            exc,
        )
    finally:
        cursor.close()

    if resolved_mode is not None and resolved_mode != "wal":
        logger.warning(
            "SQLite journal mode is %r rather than 'wal'; a concurrent reader "
            "can therefore block ledger writes until the busy timeout expires. "
            "Close other connections to this database and restart so the mode "
            "can be switched",
            resolved_mode,
        )


def describe_sqlite_configuration(engine: Engine) -> dict[str, str]:
    """Read back the runtime SQLite settings for logging/diagnostics only."""

    if engine.dialect.name != "sqlite":
        return {}
    with engine.connect() as connection:
        return {
            "journal_mode": str(
                connection.execute(sql_text("PRAGMA journal_mode")).scalar()
            ),
            "busy_timeout_ms": str(
                connection.execute(sql_text("PRAGMA busy_timeout")).scalar()
            ),
            "foreign_keys": str(
                connection.execute(sql_text("PRAGMA foreign_keys")).scalar()
            ),
        }


#: SQLite lock/busy messages. ``database is locked`` is SQLITE_BUSY; the table
#: variant appears when a shared-cache or explicit table lock is involved.
_SQLITE_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """True when a failure is a SQLite lock/busy failure rather than a task error.

    Used by the forward runner to distinguish "the database was busy" - where
    retrying on top of the held lock only multiplies blocked writes - from an
    ordinary pass failure that is worth retrying. The check is intentionally
    narrow: the exception must be a database/DBAPI error *and* carry SQLite's
    lock message, so an unrelated ``ValueError("... locked ...")`` never stops
    the runner.
    """

    if not isinstance(exc, DBAPIError):
        return False
    messages: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current).lower())
        current = (
            getattr(current, "orig", None)
            or current.__cause__
            or current.__context__
        )
    return any(
        message in text
        for text in messages
        for message in _SQLITE_LOCK_MESSAGES
    )


def is_memory_database(engine: Engine) -> bool:
    """True when the engine's database lives only inside its own connections.

    Disposing (closing) the connections of such an engine destroys its schema and
    every row, so cleanup paths must skip them; the forward runner's production
    database is always a file.
    """

    return engine.dialect.name == "sqlite" and _is_memory_database(engine.url)


def create_database_engine(
    database_url: str | URL | None = None,
    *,
    sqlite_busy_timeout_ms: int | None = None,
    **engine_options: Any,
) -> Engine:
    """Create an engine without connecting, creating tables, or running migrations.

    For SQLite the documented runtime configuration (module docstring) is applied
    through a ``connect`` event hook, so it holds for every pooled connection
    including connections created after a failure or a restart. Non-SQLite
    databases are returned untouched.
    """

    resolved_url = database_url if database_url is not None else get_settings().database_url
    engine = sqlalchemy_create_engine(resolved_url, **engine_options)

    if engine.dialect.name == "sqlite":
        busy_timeout_ms = _resolve_busy_timeout_ms(sqlite_busy_timeout_ms)
        enable_wal = not _is_memory_database(engine.url)

        @event.listens_for(engine, "connect")
        def _configure_sqlite_connection(
            dbapi_connection: Any, _connection_record: Any
        ) -> None:
            configure_sqlite_connection(
                dbapi_connection,
                busy_timeout_ms=busy_timeout_ms,
                enable_wal=enable_wal,
            )

    return engine
