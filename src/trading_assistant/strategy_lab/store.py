"""A separate schema with atomic checkpoint/event batches and OS worker locking."""

from contextlib import contextmanager
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from functools import wraps
from .models import canonical, identity

MARKER = "BRAIN-STRATEGY-LAB-ONLY-v1\n"


def exclusive(function):
    @wraps(function)
    def call(store, *args, **kwargs):
        with store.writer():
            return function(store, *args, **kwargs)

    return call


class Store:
    def __init__(self, root):
        path = Path(root).absolute()
        for p in (path, *path.parents):
            if p.is_symlink() or p.name in {"data", "backups", ".env"}:
                raise ValueError("refusing redirected or operating-bot storage")
        marker = path / "LAB-ONLY.txt"
        if path.exists() and not marker.exists() and any(path.iterdir()):
            raise ValueError(
                "refusing a nonempty directory without the lab-only marker"
            )
        path.mkdir(parents=True, exist_ok=True)
        if marker.exists() and marker.read_text() != MARKER:
            raise ValueError("not a strategy-lab workspace")
        if not marker.exists():
            marker.write_text(MARKER)
        self.root = path
        for name in ("research.sqlite3", "worker.lock", "LAB-ONLY.txt"):
            if (path / name).is_symlink():
                raise ValueError("redirected lab file")
        self.db = sqlite3.connect(
            path / "research.sqlite3", timeout=10, isolation_level=None
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, manifest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, definition TEXT NOT NULL,
          state TEXT NOT NULL, result TEXT, status TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(experiment TEXT NOT NULL REFERENCES experiments(id),
          id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(experiment,id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS worker(singleton INTEGER PRIMARY KEY CHECK(singleton=1), health TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def writer(self):
        # File descriptor locking survives crashes and is held for the entire
        # worker, not just an admission SELECT. A second process refuses work.
        if (
            getattr(self, "_owner_pid", None) == os.getpid()
            and getattr(self, "_writer_fd", None) is not None
        ):
            yield
            return
        descriptor = os.open(self.root / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("another lab worker owns this workspace") from None
            self._writer_fd = descriptor
            self._owner_pid = os.getpid()
            yield
        finally:
            self._writer_fd = None
            self._owner_pid = None
            os.close(descriptor)

    def heartbeat(self, stage, **values):
        payload = {
            "at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "stage": stage,
            **values,
        }
        self.db.execute(
            "INSERT INTO worker VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET health=excluded.health",
            (canonical(payload),),
        )

    def error(self, operation, exc, *, retryable=False):
        # Exceptions contain only fixed public sources or lab paths, never keys.
        self.db.execute(
            "INSERT INTO audit(at,kind,payload) VALUES(?,?,?)",
            (
                datetime.now(UTC).isoformat(),
                "error",
                canonical(
                    {
                        "operation": operation,
                        "type": type(exc).__name__,
                        "detail": str(exc)[:2000],
                        "retryable": retryable,
                    }
                ),
            ),
        )

    def worker_locked(self):
        if (
            getattr(self, "_owner_pid", None) == os.getpid()
            and getattr(self, "_writer_fd", None) is not None
        ):
            return True
        fd = os.open(self.root / "worker.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(fd)

    def experiment(self, definition, initial):
        key = identity(definition)
        self.db.execute(
            "INSERT OR IGNORE INTO experiments VALUES(?,?,?,NULL,?,?)",
            (
                key,
                canonical(definition),
                canonical(initial),
                "pending",
                datetime.now(UTC).isoformat(),
            ),
        )
        row = self.db.execute("SELECT * FROM experiments WHERE id=?", (key,)).fetchone()
        if row["definition"] != canonical(definition):
            raise ValueError("experiment identity collision")
        return key, dict(row)

    def checkpoint(self, key, state, events, *, result=None, fault=None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT state,result FROM experiments WHERE id=?", (key,)
            ).fetchone()
            if row["result"] is not None:
                raise ValueError("completed experiment is immutable")
            if state["index"] < json.loads(row["state"])["index"]:
                raise ValueError("checkpoint cannot move backwards")
            for event in events:
                encoded = canonical(event)
                eid = identity(event)
                self.db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                    (key, eid, event["kind"], encoded),
                )
            if fault:
                fault()
            self.db.execute(
                "UPDATE experiments SET state=?,result=?,status=?,updated=? WHERE id=?",
                (
                    canonical(state),
                    None if result is None else canonical(result),
                    "running" if result is None else result["status"],
                    datetime.now(UTC).isoformat(),
                    key,
                ),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def records(self, key, kind=None):
        query = "SELECT payload FROM events WHERE experiment=?"
        args = [key]
        if kind:
            query += " AND kind=?"
            args.append(kind)
        return [
            json.loads(row[0]) for row in self.db.execute(query + " ORDER BY id", args)
        ]

    def status(self):
        owns_read = not self.db.in_transaction
        if owns_read:
            self.db.execute("BEGIN")
        try:
            counts = dict(
                self.db.execute(
                    "SELECT status,COUNT(*) FROM experiments GROUP BY status"
                ).fetchall()
            )
            last = self.db.execute(
                "SELECT id,status,updated FROM experiments ORDER BY updated DESC LIMIT 1"
            ).fetchone()
            health = self.db.execute("SELECT health FROM worker").fetchone()
            tables = {
                r[0]
                for r in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            worker = json.loads(health[0]) if health else None
            locked = self.worker_locked()
            errors = [
                dict(r)
                for r in self.db.execute(
                    "SELECT at,payload FROM audit WHERE kind='error' ORDER BY id DESC LIMIT 10"
                )
            ]
            campaigns = (
                [
                    json.loads(r[0])
                    for r in self.db.execute("SELECT state FROM campaigns")
                ]
                if "campaigns" in tables
                else []
            )
            jobs = [j for c in campaigns for j in c["jobs"].values()]
            rejected = {j["candidate"] for j in jobs if not j["gate"]["passed"]}
            promoted = (
                {r[0] for r in self.db.execute("SELECT candidate FROM promotions")}
                if "promotions" in tables
                else set()
            )
            waiting = (
                {j["candidate"] for j in jobs if j["gate"]["passed"]}
                - rejected
                - promoted
            )
            brain = (
                [
                    {
                        "id": r["id"],
                        "processed_boundaries": json.loads(r["state"])["next"],
                        "complete": r["result"] is not None,
                    }
                    for r in self.db.execute("SELECT id,state,result FROM brain_runs")
                ]
                if "brain_runs" in tables
                else []
            )
            return {
                "experiments_attempted": sum(counts.values()),
                "experiments_completed": sum(
                    counts.get(k, 0) for k in ("rejected", "awaiting_validation")
                ),
                "candidates_rejected": len(rejected),
                "candidates_awaiting_validation": len(waiting),
                "provisional_experiments_awaiting_validation": counts.get(
                    "awaiting_validation", 0
                ),
                "brain_comparisons": brain,
                "candidates_in_forward_testing": self.db.execute(
                    "SELECT count(*) FROM promotions"
                ).fetchone()[0]
                if "promotions" in tables
                else 0,
                "campaigns_finished": sum(c["finished"] for c in campaigns),
                "campaigns_in_progress": sum(not c["finished"] for c in campaigns),
                "recent_errors": errors,
                "worker_lock_held": locked,
                "worker_health": "no_active_worker"
                if not locked
                else "worker_lock_held_check_heartbeat_age",
                "stages": counts,
                "last_checkpoint": dict(last) if last else None,
                "worker": worker,
                "heartbeat_age_seconds": max(
                    0,
                    (
                        datetime.now(UTC) - datetime.fromisoformat(worker["at"])
                    ).total_seconds(),
                )
                if worker
                else None,
                "integrity": self.db.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_errors": len(
                    self.db.execute("PRAGMA foreign_key_check").fetchall()
                ),
                "paper_only": True,
            }
        finally:
            if owns_read:
                self.db.execute("COMMIT")
