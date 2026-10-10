"""Single-instance advisory lock so only one ingestion process writes at a time."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType


class IngestionLockError(RuntimeError):
    """Another ingestion process already holds the lock."""


class SingleInstanceLock:
    """Non-blocking exclusive ``flock`` on a lock file, released on exit or crash.

    The kernel drops the lock when the process dies, so a crashed ingestion never
    leaves a stale lock that needs manual deletion.
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._handle = None

    def __enter__(self) -> SingleInstanceLock:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise IngestionLockError(
                f"another ingestion process holds {self.path}; stop it before starting "
                "a new one (only one ingestion process may write market data)"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._handle is not None:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
