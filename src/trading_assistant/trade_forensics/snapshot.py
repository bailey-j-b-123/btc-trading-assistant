"""Read-only access to an existing BRAIN SQLite database.

The forensic report must never change the database it explains, so the source
file is never opened for writing and never handed to the application's own
engine factory (which configures WAL on connect and is therefore a writer).

Procedure:

1. The file must already exist. A missing path is an error; it is never created.
2. Its bytes (main file and ``-wal`` sidecar, when present) are fingerprinted.
3. The file is opened with SQLite ``mode=ro`` behind an authorizer that denies
   every action except reads, and copied **once** into an in-memory database
   with the SQLite backup API. The backup runs as a single step, so the copy is
   one consistent snapshot even while a runner writes the same file.
4. All analysis reads the in-memory copy, through a second write-denying
   authorizer. The copy is discarded when the report is finished.
5. The source files are fingerprinted again. A difference is reported and the
   CLI exits non-zero rather than presenting a report about a moving target.

SQLite may create or touch the ``-shm`` shared-memory index while a read-only
connection is open. That index is rebuildable and holds no ledger content; the
database and ``-wal`` contents are what the fingerprints prove unchanged.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

#: Actions a read-only analysis connection may perform. Everything else
#: (INSERT/UPDATE/DELETE, CREATE/DROP/ALTER, ATTACH/DETACH, ANALYZE, REINDEX,
#: VACUUM, ...) is denied by the authorizer even before SQLite would refuse it.
_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)

#: Pragmas that only report state. A pragma issued with a value is a write.
_READ_ONLY_PRAGMAS = frozenset(
    {
        "table_info",
        "table_xinfo",
        "table_list",
        "index_list",
        "index_info",
        "foreign_key_list",
        "database_list",
        "user_version",
        "schema_version",
        "page_count",
        "page_size",
        "encoding",
        "journal_mode",
        "foreign_keys",
        "busy_timeout",
        "read_uncommitted",
    }
)


class DatabaseUnavailable(RuntimeError):
    """The database file is missing, not a file, or not a SQLite database."""


def _authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    _db: str | None,
    _trigger: str | None,
) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg2 is None and arg1 in _READ_ONLY_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """Path, existence, size and SHA-256 of one file (or of its absence)."""

    path: str
    exists: bool
    size: int | None
    sha256: str | None


def _fingerprint_file(path: Path) -> FileFingerprint:
    if not path.is_file():
        return FileFingerprint(path=str(path), exists=False, size=None, sha256=None)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return FileFingerprint(
        path=str(path), exists=True, size=size, sha256=digest.hexdigest()
    )


def fingerprint_database(database_path: Path) -> tuple[FileFingerprint, ...]:
    """Fingerprints of the main database file and its WAL sidecar.

    An empty ``-wal`` counts as absent. SQLite creates a zero-byte WAL when a
    WAL database is opened read-only with no sidecar present; it holds no
    content, so creating it is not a change to the database.
    """

    wal_path = database_path.with_name(database_path.name + "-wal")
    wal = _fingerprint_file(wal_path)
    if wal.exists and wal.size == 0:
        wal = FileFingerprint(path=str(wal_path), exists=False, size=None, sha256=None)
    return (_fingerprint_file(database_path), wal)


def changed_files(
    before: tuple[FileFingerprint, ...], after: tuple[FileFingerprint, ...]
) -> tuple[str, ...]:
    """Paths whose existence, size or digest differ between two fingerprints."""

    return tuple(new.path for old, new in zip(before, after, strict=True) if old != new)


class ReadOnlySnapshot:
    """A consistent, write-denying in-memory copy of one SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.before: tuple[FileFingerprint, ...] = ()
        self.after: tuple[FileFingerprint, ...] | None = None
        self.engine: Engine | None = None
        self._memory: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        if not self.database_path.is_file():
            raise DatabaseUnavailable(
                f"database file not found: {self.database_path} "
                "(run from the repository root or pass --database-url; "
                "nothing was created)"
            )
        self.before = fingerprint_database(self.database_path)
        uri = self.database_path.as_uri() + "?mode=ro"
        source = sqlite3.connect(uri, uri=True, check_same_thread=False)
        try:
            source.set_authorizer(_authorizer)
            memory = sqlite3.connect(":memory:", check_same_thread=False)
            try:
                source.backup(memory)
            except sqlite3.DatabaseError as exc:
                memory.close()
                raise DatabaseUnavailable(
                    f"could not read {self.database_path} as SQLite: {exc}"
                ) from exc
        finally:
            source.close()
        memory.set_authorizer(_authorizer)
        self._memory = memory
        self.engine = create_engine(
            "sqlite://",
            creator=self._connection,
            poolclass=StaticPool,
        )
        return self

    def _connection(self) -> sqlite3.Connection:
        assert self._memory is not None
        return self._memory

    def __exit__(self, *_exc: object) -> None:
        if self.engine is not None:
            self.engine.dispose()
            self.engine = None
        if self._memory is not None:
            self._memory.close()
            self._memory = None

    def verify_source_unchanged(self) -> tuple[bool, tuple[str, ...]]:
        """Fingerprint the source files again. Call after the snapshot is closed."""

        self.after = fingerprint_database(self.database_path)
        changed = changed_files(self.before, self.after)
        return (not changed, changed)


__all__ = [
    "DatabaseUnavailable",
    "FileFingerprint",
    "ReadOnlySnapshot",
    "changed_files",
    "fingerprint_database",
]
