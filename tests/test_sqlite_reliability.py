"""File-backed SQLite runtime reliability: transactions, locks, and recovery.

These tests exist because of a real runtime failure: the forward runner started
against an existing file-backed SQLite database and died with

    sqlalchemy.exc.OperationalError
    sqlite3.OperationalError: database is locked

Everything below uses the real runtime path end to end - a temporary file-backed
SQLite database migrated by the real Alembic chain, the real repositories, the
real Step 2-9 services, the real forward service, the real forward runner and
the real dashboard endpoints - and nothing here mocks ``OperationalError``.

Covered:

* the documented SQLite runtime configuration (WAL, finite busy timeout,
  foreign keys) is really applied to a file database's connections;
* a forward pass completes while a dashboard-style reader holds a read
  transaction on the same database (the pre-repair failure: the reader's SHARED
  lock blocked the writer's commit, so the pass died with "database is locked"
  once the busy timeout expired);
* a writer and a reader run together on one file database with the real
  dashboard endpoints in the loop, coordinated by events rather than sleeps;
* no exchange access, analysis, planning or explanation step runs while a
  database transaction is open;
* after a controlled failure inside a real write transaction the failure is
  rolled back, the database is released, the original error stays identifiable,
  the lifecycle heartbeats are written safely, and the next pass records the
  ledger exactly once;
* a genuine SQLite lock failure stops the runner after a single attempt, keeps
  the original root exception, and never lets a heartbeat write replace it.
"""

from __future__ import annotations

import threading
from decimal import Decimal as D

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    labelled_series,
    make_harness,
)

from trading_assistant.database import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    create_database_engine,
    describe_sqlite_configuration,
    is_sqlite_lock_error,
)
from trading_assistant.forward_testing import (
    ForwardRunner,
    ForwardTestService,
    HeartbeatStatus,
    RunnerSettings,
    run_single_pass,
)
from trading_assistant.forward_testing.repository import ForwardLedgerRepository
from trading_assistant.forward_testing.tables import (
    ForwardCycleRow,
    ForwardObservationRow,
    ForwardPaperPlanRow,
    ForwardRunnerHeartbeatRow,
)
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.setup_qualification.service import QualificationService
from web_fixtures import make_client, migrated_engine

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ready_harness(**kwargs):
    """A migrated file database whose labelled close is ready to be recorded."""

    harness = make_harness(
        series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY, **kwargs
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    return harness


def _rows(engine, model) -> tuple:
    with Session(engine) as session:
        return tuple(session.scalars(select(model)).all())


class _HeldReadTransaction:
    """A real, explicitly held read transaction on its own connection.

    This models a dashboard reader whose read statement or transaction is still
    in flight - the condition under which the rollback-journal mode blocks a
    writer until its busy timeout expires.
    """

    def __init__(self, engine) -> None:
        self._connection = engine.connect()
        self._connection.execute(text("BEGIN"))
        self.rows = int(
            self._connection.execute(text("SELECT count(*) FROM ohlcv_candles")).scalar()
            or 0
        )

    def __enter__(self) -> _HeldReadTransaction:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def release(self) -> None:
        self._connection.rollback()
        self._connection.close()


class _HeldWriteTransaction:
    """A real second writer holding the SQLite write lock (``BEGIN IMMEDIATE``)."""

    def __init__(self, engine) -> None:
        self._connection = engine.connect()
        self._connection.execute(text("BEGIN IMMEDIATE"))
        self._connection.execute(
            text(
                "INSERT INTO forward_runner_heartbeats (heartbeat_id, "
                "runner_rules_version, recorded_at, status, exchange, symbol, "
                "timeframe, detail, cycles_processed, observations_recorded, "
                "paper_plans_created, outcomes_recorded, pending_boundaries, "
                "last_error, error_type, market_data_json) VALUES "
                "('held-lock', 'test', '2024-01-01 00:00:00', 'IDLE', 'x', 'y', "
                "'1h', 'test lock holder', 0, 0, 0, 0, 0, NULL, NULL, '{}')"
            )
        )

    def __enter__(self) -> _HeldWriteTransaction:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()

    def release(self) -> None:
        self._connection.rollback()
        self._connection.close()


# ----------------------------------------------------------------------
# 1. The SQLite runtime configuration itself
# ----------------------------------------------------------------------


def test_file_database_runs_in_wal_with_an_explicit_finite_busy_timeout(tmp_path) -> None:
    engine, _url = migrated_engine(tmp_path, "runtime.sqlite3")
    try:
        configuration = describe_sqlite_configuration(engine)
        assert configuration["journal_mode"] == "wal"
        assert configuration["busy_timeout_ms"] == str(DEFAULT_SQLITE_BUSY_TIMEOUT_MS)
        # Foreign keys stay on: the ledger's RESTRICT references are only
        # enforced when the pragma is set.
        assert configuration["foreign_keys"] == "1"
    finally:
        engine.dispose()


def test_busy_timeout_is_finite_and_configurable(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'busy.sqlite3'}"

    explicit = create_database_engine(database_url, sqlite_busy_timeout_ms=1_234)
    try:
        assert describe_sqlite_configuration(explicit)["busy_timeout_ms"] == "1234"
    finally:
        explicit.dispose()

    monkeypatch.setenv("TRADING_ASSISTANT_SQLITE_BUSY_TIMEOUT_MS", "2500")
    configured = create_database_engine(database_url)
    try:
        assert describe_sqlite_configuration(configured)["busy_timeout_ms"] == "2500"
    finally:
        configured.dispose()

    # A "huge timeout" is not a substitute for a correct configuration: the
    # value is clamped to the documented finite cap.
    clamped = create_database_engine(database_url, sqlite_busy_timeout_ms=10_000_000)
    try:
        assert describe_sqlite_configuration(clamped)["busy_timeout_ms"] == "60000"
    finally:
        clamped.dispose()


def test_in_memory_databases_are_configured_without_requesting_wal() -> None:
    engine = create_database_engine("sqlite://", sqlite_busy_timeout_ms=750)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 750
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
            # WAL does not exist for an in-memory database; requesting it must
            # never fail the connection.
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "memory"
    finally:
        engine.dispose()


def test_releasing_connections_drops_the_pool_without_touching_stored_rows() -> None:
    harness = _ready_harness()
    engine = harness.engine
    try:
        latest = harness.service.candles.latest_timestamp(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )
        # The newest stored candle is the one that closes at the boundary.
        assert latest == QUALIFYING_BOUNDARY - INTERVAL
        assert engine.pool.checkedin() == 1

        harness.service.release_database_connections()
        # Pooled connections are closed (that is the point: nothing can still
        # hold a lock) ...
        assert engine.pool.checkedin() == 0
        # ... and not a single stored row was touched: the engine stays usable
        # and the next connection carries the documented configuration again.
        assert (
            harness.service.candles.latest_timestamp(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
            )
            == latest
        )
        assert describe_sqlite_configuration(engine)["journal_mode"] == "wal"
    finally:
        engine.dispose()


def test_releasing_connections_never_destroys_an_in_memory_database() -> None:
    """Cleanup must be a no-op where disposing would delete the whole database."""

    engine = create_database_engine("sqlite://")
    try:
        service = ForwardTestService(engine)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE sentinel (value TEXT NOT NULL)"))
            connection.execute(text("INSERT INTO sentinel (value) VALUES ('keep')"))

        service.release_database_connections()

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT value FROM sentinel")).scalar_one()
                == "keep"
            )
    finally:
        engine.dispose()


def test_lock_error_classification_is_narrow() -> None:
    harness = _ready_harness(sqlite_busy_timeout_ms=150)
    observed: OperationalError | None = None
    try:
        with _HeldWriteTransaction(harness.engine):
            try:
                run_single_pass(
                    harness.service,
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    refresh_market_data=False,
                )
            except OperationalError as exc:
                observed = exc
    finally:
        harness.engine.dispose()

    assert observed is not None
    assert "database is locked" in str(observed).lower()
    assert is_sqlite_lock_error(observed) is True
    # Ordinary failures - including one whose message merely says "locked" -
    # are never treated as database locks.
    assert is_sqlite_lock_error(ValueError("resource locked by policy")) is False
    assert is_sqlite_lock_error(RuntimeError("boom")) is False


# ----------------------------------------------------------------------
# 2. The regression: a dashboard read must not kill a forward pass
# ----------------------------------------------------------------------


def test_forward_pass_survives_a_dashboard_reader_holding_a_read_transaction() -> None:
    """The exact pre-repair failure: reader SHARED lock -> writer SQLITE_BUSY."""

    harness = _ready_harness()
    try:
        # Nothing here mocks the failure: the reader is a second real
        # connection on the same real file, holding a real read transaction.
        with _HeldReadTransaction(harness.engine) as reader:
            assert reader.rows == len(labelled_series())
            runner = ForwardRunner(harness.service, runner_id="lock-regression")
            result = runner.run(
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                once=True,
                refresh_market_data=False,
            )
            assert result is not None
            assert result.status is HeartbeatStatus.PROCESSED
            assert result.paper_plans_created == 1

        # The run recorded its lifecycle and its ledger rows *while* the reader
        # held the database read.
        statuses = [
            row.status for row in _rows(harness.engine, ForwardRunnerHeartbeatRow)
        ]
        assert statuses[0] == HeartbeatStatus.STARTED.value
        assert HeartbeatStatus.PROCESSED.value in statuses
        assert statuses[-1] == HeartbeatStatus.STOPPED.value
        assert len(_rows(harness.engine, ForwardCycleRow)) == 1
        assert len(_rows(harness.engine, ForwardObservationRow)) == 6
        assert len(_rows(harness.engine, ForwardPaperPlanRow)) == 1

        # The reader's own view stays valid and consistent afterwards.
        with _HeldReadTransaction(harness.engine) as reader:
            assert reader.rows == len(labelled_series())
    finally:
        harness.engine.dispose()


def test_forward_pass_survives_a_dashboard_request_in_flight() -> None:
    """The same guarantee exercised through the real dashboard endpoints."""

    harness = _ready_harness()
    client = make_client(harness.engine, harness.settings, clock=harness.clock["t"])
    try:
        with _HeldReadTransaction(harness.engine):
            assert client.get("/api/dashboard").status_code == 200

            result = run_single_pass(
                harness.service,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                refresh_market_data=False,
            )
            assert result.paper_plans_created == 1

            forward = client.get("/api/forward").json()
            assert forward["status"]["sample"]["cycles"] == result.cycles_recorded
    finally:
        client.close()
        harness.engine.dispose()


# ----------------------------------------------------------------------
# 3. Reader and writer together on one file database
# ----------------------------------------------------------------------


def test_dashboard_reads_and_forward_writer_coexist_without_locking() -> None:
    """Reader and writer overlap deterministically - no sleeps, no guessing.

    The dashboard reads through its own engine and its own pool, exactly like
    the separate dashboard process, while the forward runner writes. The reader
    additionally holds a read transaction across a whole pass, which is the
    strongest "in-flight dashboard read" this test can create without relying
    on timing.
    """

    harness = _ready_harness()
    dashboard_engine = create_database_engine(harness.settings.database_url)
    client = make_client(dashboard_engine, harness.settings, clock=harness.clock["t"])

    requests: list[tuple[str, int]] = []
    failures: list[BaseException] = []
    reader_holding = threading.Event()
    stop_reading = threading.Event()

    def dashboard_reader() -> None:
        connection = None
        try:
            connection = dashboard_engine.connect()
            connection.execute(text("BEGIN"))
            connection.execute(text("SELECT count(*) FROM ohlcv_candles")).scalar()
            reader_holding.set()
            while not stop_reading.is_set():
                for path in (
                    "/api/dashboard",
                    "/api/forward",
                    "/api/market/structure",
                ):
                    requests.append((path, client.get(path).status_code))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            failures.append(exc)
        finally:
            if connection is not None:
                connection.rollback()
                connection.close()

    thread = threading.Thread(target=dashboard_reader, name="dashboard-reader")
    thread.start()
    try:
        assert reader_holding.wait(timeout=30), "the dashboard reader never started"
        first = run_single_pass(
            harness.service,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            refresh_market_data=False,
        )
        assert first.status is HeartbeatStatus.PROCESSED
        assert first.paper_plans_created == 1

        # A repeated pass while the reader still holds its transaction finds
        # nothing new and records nothing new.
        second = run_single_pass(
            harness.service,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            refresh_market_data=False,
        )
        assert second.status is HeartbeatStatus.IDLE
        assert second.processed_boundaries == ()
    finally:
        stop_reading.set()
        thread.join(timeout=60)
        client.close()
        dashboard_engine.dispose()

    assert not thread.is_alive()
    assert failures == []
    assert requests, "the dashboard reader completed no request at all"
    assert {code for _path, code in requests} == {200}
    assert {path for path, _code in requests} == {
        "/api/dashboard",
        "/api/forward",
        "/api/market/structure",
    }

    # Overlapping reader and writer produced no duplicate ledger rows.
    engine = harness.engine
    try:
        assert len(_rows(engine, ForwardCycleRow)) == 1
        assert len(_rows(engine, ForwardObservationRow)) == 6
        assert len(_rows(engine, ForwardPaperPlanRow)) == 1

        # A restart (fresh engine and repositories over the same file) is
        # idempotent: the same rows, never more.
        restarted_engine = create_database_engine(harness.settings.database_url)
        try:
            restarted = ForwardLedgerRepository(restarted_engine)
            arguments = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}
            assert len(restarted.cycles(**arguments)) == 1
            assert len(restarted.observations(**arguments)) == 6
            assert len(restarted.paper_plans(**arguments)) == 1
        finally:
            restarted_engine.dispose()
    finally:
        engine.dispose()


# ----------------------------------------------------------------------
# 4. Transaction lifetime: analysis never runs inside an open transaction
# ----------------------------------------------------------------------


def test_analysis_and_exchange_access_never_run_inside_an_open_transaction(
    monkeypatch,
) -> None:
    """Deterministic proof of the "short write transaction" invariant.

    ``Engine.pool.checkedout()`` is zero exactly when this engine holds no
    connection - and therefore no transaction and no SQLite lock. Every probe
    records that count at the moment a slow stage starts and then calls through,
    so the pass still completes normally and no stage is ever skipped silently.
    """

    harness = _ready_harness()
    engine = harness.engine
    service = harness.service
    checked_out_at_entry: dict[str, int] = {}

    def probe(name: str, original):
        def wrapper(*args, **kwargs):
            checked_out_at_entry[name] = engine.pool.checkedout()
            return original(*args, **kwargs)

        return wrapper

    market_data_update = MarketDataService.update_history
    monkeypatch.setattr(
        MarketDataService,
        "update_history",
        probe("exchange_fetch", market_data_update),
    )
    qualification_frames = QualificationService.build_frames
    monkeypatch.setattr(
        QualificationService,
        "build_frames",
        probe("qualification_replay", qualification_frames),
    )
    import trading_assistant.forward_testing.service as forward_service_module

    real_plan_trade = forward_service_module.plan_trade
    monkeypatch.setattr(
        forward_service_module,
        "plan_trade",
        probe("planning", real_plan_trade),
    )
    real_explain = service.explanations.explain
    monkeypatch.setattr(
        service.explanations,
        "explain",
        probe("explanation", real_explain),
    )

    result = service.run_once(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=True,
        runner_id="transaction-lifetime",
    )
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.paper_plans_created == 1

    # Every slow stage ran with no connection checked out, i.e. with no open
    # write transaction and nothing holding a SQLite lock.
    assert set(checked_out_at_entry) == {
        "exchange_fetch",
        "qualification_replay",
        "planning",
        "explanation",
    }
    assert all(count == 0 for count in checked_out_at_entry.values()), (
        checked_out_at_entry
    )
    engine.dispose()


# ----------------------------------------------------------------------
# 5. Failure recovery: rollback, release, original error, next pass
# ----------------------------------------------------------------------


def test_failed_write_transaction_is_rolled_back_released_and_recovered(
    monkeypatch,
) -> None:
    """A controlled failure inside a real write transaction, end to end."""

    harness = _ready_harness()
    engine = harness.engine

    def _boom(*_args: object, **_kwargs: object) -> None:
        # Raised *inside* the repository's ``with session.begin()`` block, after
        # the INSERT has already executed, so a real write transaction is open
        # and must be rolled back by the failure path.
        raise RuntimeError("simulated ledger failure")

    monkeypatch.setattr(
        "trading_assistant.forward_testing.repository._verify_unchanged", _boom
    )

    runner = ForwardRunner(harness.service, runner_id="recovery-runner")
    with pytest.raises(RuntimeError, match="simulated ledger failure"):
        runner.run(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            once=True,
            refresh_market_data=False,
        )

    # 1. The failed transaction left nothing behind and released its connection:
    #    no partial cycle row, and no SQLite lock held by this engine.
    assert _rows(engine, ForwardCycleRow) == ()
    assert _rows(engine, ForwardObservationRow) == ()
    assert engine.pool.checkedout() == 0

    # 2. The original error stays identifiable: it is recorded on exactly one
    #    ERROR heartbeat, and the run still closed with a STOPPED heartbeat.
    heartbeats = _rows(engine, ForwardRunnerHeartbeatRow)
    error_rows = [row for row in heartbeats if row.status == HeartbeatStatus.ERROR.value]
    assert len(error_rows) == 1
    assert error_rows[0].error_type == "RuntimeError"
    assert error_rows[0].last_error is not None
    assert "simulated ledger failure" in error_rows[0].last_error
    assert heartbeats[-1].status == HeartbeatStatus.STOPPED.value

    # 3. A subsequent valid pass writes successfully and records the ledger.
    monkeypatch.undo()
    recovered = run_single_pass(
        harness.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert recovered.status is HeartbeatStatus.PROCESSED
    assert len(_rows(engine, ForwardCycleRow)) == 1
    assert len(_rows(engine, ForwardObservationRow)) == 6
    assert len(_rows(engine, ForwardPaperPlanRow)) == 1

    # 4. Repeating the pass creates no duplicate cycle/observation/plan row.
    again = run_single_pass(
        harness.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert again.paper_plans_created == 0
    assert again.observations_recorded == 0
    assert len(_rows(engine, ForwardCycleRow)) == 1
    assert len(_rows(engine, ForwardObservationRow)) == 6
    assert len(_rows(engine, ForwardPaperPlanRow)) == 1
    engine.dispose()


# ----------------------------------------------------------------------
# 6. A genuine lock failure: one attempt, original error, no storm
# ----------------------------------------------------------------------


def test_locked_database_fails_once_with_the_original_error(monkeypatch) -> None:
    """Error reporting must not turn one lock failure into repeated ones."""

    harness = _ready_harness(sqlite_busy_timeout_ms=200)
    engine = harness.engine
    attempts = {"count": 0}
    real_run_once = harness.service.run_once

    def counting_run_once(*args, **kwargs):
        attempts["count"] += 1
        return real_run_once(*args, **kwargs)

    monkeypatch.setattr(harness.service, "run_once", counting_run_once)

    holder_engine = create_database_engine(
        harness.settings.database_url, sqlite_busy_timeout_ms=200
    )
    runner = ForwardRunner(
        harness.service,
        settings=RunnerSettings(
            interval_seconds=D("1"),
            fetch_max_attempts=1,
            fetch_retry_backoff_seconds=D("0"),
            stop_after_errors=10,
        ),
        runner_id="locked-runner",
    )
    captured: DBAPIError | None = None
    try:
        with _HeldWriteTransaction(holder_engine):
            try:
                runner.run(
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    once=False,
                    refresh_market_data=False,
                    max_passes=3,
                )
            except DBAPIError as exc:
                captured = exc
    finally:
        holder_engine.dispose()

    # The exception that escaped is the original database lock - not a heartbeat
    # error raised while trying to report the lock.
    assert captured is not None
    assert is_sqlite_lock_error(captured) is True
    assert "database is locked" in str(captured).lower()

    # A locked database is not a transient market-data failure: the runner made
    # exactly one pass attempt (no retry storm on top of the held lock) even
    # though ``stop_after_errors`` allowed ten.
    assert attempts["count"] == 1

    # Nothing was fabricated: the lock holder's own row rolled back with it, and
    # no forward cycle was concluded from the failed attempt.
    assert _rows(engine, ForwardCycleRow) == ()
    heartbeat_rows = _rows(engine, ForwardRunnerHeartbeatRow)
    assert all(
        row.status != HeartbeatStatus.PROCESSED.value for row in heartbeat_rows
    )

    # Once the lock is released the very next pass succeeds and records the
    # ledger exactly once: the failure released the database correctly.
    recovered = run_single_pass(
        harness.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert recovered.status is HeartbeatStatus.PROCESSED
    assert len(_rows(engine, ForwardCycleRow)) == 1
    assert len(_rows(engine, ForwardObservationRow)) == 6
    engine.dispose()
