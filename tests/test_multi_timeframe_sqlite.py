"""Step 13 SQLite reliability: the multi-timeframe runner on a file database.

This is the Step 13 counterpart of the PR #22 regression suite: the SAME
file-backed SQLite database is written by the multi-timeframe hierarchy runner
and read by the dashboard (which evaluates the hierarchy live), proving the
preserved runtime contract end to end:

* WAL, a bounded busy timeout and ``foreign_keys=ON`` really apply;
* the hierarchy runner records observations while dashboard reads are in
  flight on the same file - no "database is locked";
* a repeated boundary is idempotent and an advanced boundary records a new
  state without mutating the old record;
* the temp-database smoke test walks the whole lifecycle: migrate, acquire
  closed 4H/1H/15M/5M candles through ONE client, record one hierarchy
  snapshot, read it through the dashboard, repeat the boundary (idempotent),
  advance the boundary (new state, old record untouched), and hammer the
  dashboard with concurrent reads while a pass writes.

No Bailey database is touched: every test uses a temporary file database.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from trading_assistant.database import (
    DEFAULT_SQLITE_BUSY_TIMEOUT_MS,
    create_database_engine,
    describe_sqlite_configuration,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.multi_timeframe.service import (
    MultiTimeframeService,
    RunnerStatus,
)
from trading_assistant.web import create_app

from forward_fixtures import FakeExchange
from multi_timeframe_fixtures import (
    DECISION_TIME,
    EPOCH,
    EXCHANGE,
    SCENARIO_DECISION_TIME,
    SYMBOL,
    hierarchy_candles,
    one_hour_candles,
    scenario_candles,
)
from web_fixtures import make_client, make_settings, migrated_engine

HOUR = timedelta(hours=1)
BAND_LOW = Decimal("117")
BAND_HIGH = Decimal("117")


def _hierarchy_service(engine, *, clock=None, market_data_service=None):
    return MultiTimeframeService(
        engine,
        settings=make_settings(str(engine.url)),
        clock=clock if clock is not None else (lambda: DECISION_TIME),
        market_data_service=market_data_service,
    )


# ---------------------------------------------------------------------------
# Runtime configuration (PR #22 contract preserved)
# ---------------------------------------------------------------------------


def test_sqlite_runtime_configuration_is_really_applied(tmp_path):
    engine, _url = migrated_engine(tmp_path, "config.sqlite3")
    try:
        configuration = describe_sqlite_configuration(engine)
        assert configuration["journal_mode"] == "wal"
        assert configuration["busy_timeout_ms"] == str(DEFAULT_SQLITE_BUSY_TIMEOUT_MS)
        assert configuration["foreign_keys"] == "1"
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
            assert (
                connection.execute(text("PRAGMA busy_timeout")).scalar()
                == DEFAULT_SQLITE_BUSY_TIMEOUT_MS
            )
            assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        engine.dispose()


def test_hierarchy_ledger_table_exists_with_immutability_triggers(tmp_path):
    engine, _url = migrated_engine(tmp_path, "triggers.sqlite3")
    try:
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                        "AND tbl_name = 'forward_hierarchy_observations'"
                    )
                )
            }
        assert "trg_forward_hierarchy_observations_no_update" in triggers
        assert "trg_forward_hierarchy_observations_no_delete" in triggers
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# PR #22 regression: hierarchy runner + concurrent dashboard reads
# ---------------------------------------------------------------------------


class _HeldReadTransaction:
    """A real, explicitly held read transaction on its own connection."""

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


def _seeded_engine(tmp_path, *, aligned: bool = True):
    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    from multi_timeframe_fixtures import insert_hierarchy

    insert_hierarchy(engine, hierarchy_candles(aligned=aligned))
    return engine, url


def test_multi_timeframe_pass_survives_a_held_dashboard_read_transaction(tmp_path):
    """The pre-PR-#22 failure shape, replayed against the hierarchy runner."""

    engine, url = _seeded_engine(tmp_path)
    service = _hierarchy_service(engine)
    held = _HeldReadTransaction(engine)
    try:
        result = service.run_once(now=DECISION_TIME, refresh_market_data=False)
        assert result.status is RunnerStatus.PROCESSED
        assert result.observations_created == 1
    finally:
        held.release()
        engine.dispose()


def test_multi_timeframe_runner_and_dashboard_reads_coexist_without_locking(tmp_path):
    """Dashboard reads (which evaluate the hierarchy) overlap hierarchy writes.

    The dashboard reads through its OWN engine and pool, exactly like the
    separate dashboard process, while the hierarchy runner writes the ledger.
    The reader also holds a read transaction across whole passes - the strongest
    "in-flight dashboard read" this test can create without relying on timing.
    """

    engine, url = _seeded_engine(tmp_path)
    settings = make_settings(url)
    service = _hierarchy_service(engine)
    dashboard_engine = create_database_engine(url)
    client = make_client(dashboard_engine, settings, clock=DECISION_TIME)

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
                for path in ("/api/dashboard", "/api/market/structure"):
                    response = client.get(path)
                    requests.append((path, response.status_code))
                    if path == "/api/dashboard":
                        payload = response.json()
                        assert "multi_timeframe" in payload
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
        first = service.run_once(now=DECISION_TIME, refresh_market_data=False)
        assert first.status is RunnerStatus.PROCESSED
        assert first.observations_created == 1

        second = service.run_once(now=DECISION_TIME, refresh_market_data=False)
        assert second.status is RunnerStatus.IDLE
        assert second.observations_recorded == 0
    finally:
        stop_reading.set()
        thread.join(timeout=30)
        dashboard_engine.dispose()
        engine.dispose()
    assert not failures
    assert requests
    assert all(status == 200 for _path, status in requests)


# ---------------------------------------------------------------------------
# The temp-database smoke test (the full Step 13 lifecycle, end to end)
# ---------------------------------------------------------------------------


def test_temp_database_smoke_test_full_hierarchy_lifecycle(tmp_path):
    """Nine steps, one temporary file database, no Bailey data anywhere.

    1. create a temp file DB and migrate it with the real Alembic chain;
    2. acquire closed 4H/1H/15M/5M candles through ONE fake public source;
    3. build and record one hierarchy snapshot at the first closed boundary;
    4. read it through the dashboard (the ladder payload);
    5. repeat the same boundary: idempotent, nothing new recorded;
    6. advance to a new closed boundary: a new state is recorded and the old
       record is byte-for-byte unchanged;
    7. concurrent dashboard reads while a pass writes: no SQLite lock;
    8. the ledger history is complete, readable and append-only;
    9. the WAL/busy-timeout/foreign-keys configuration held throughout.
    """

    # --- Step 1: temp file database, migrated -----------------------------
    engine, url = migrated_engine(tmp_path, "smoke.sqlite3")
    settings = make_settings(url)

    # --- Step 2: acquire closed 4H/1H/15M/5M through ONE client -------------
    source = FakeExchange()
    source.exchange_id = EXCHANGE
    source.set_candles(
        list(one_hour_candles())
        + list(hierarchy_candles()["4h"])
        + list(hierarchy_candles()["15m"])
        + list(hierarchy_candles()["5m"])
    )
    market_data = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(tmp_path / "raw"),
        clock=lambda: DECISION_TIME,
    )
    try:
        acquired = market_data.download_history_all(
            start_time=EPOCH - 128 * HOUR,  # 4H-aligned; the source serves what it has
            timeframes=("4h", "1h", "15m", "5m"),
            as_of=DECISION_TIME,
        )
        assert set(acquired) == {"4h", "1h", "15m", "5m"}
        for timeframe, update in acquired.items():
            assert update.inserted_count > 0, timeframe
            assert update.excluded_open_count == 0, timeframe
    finally:
        market_data.close()

    from trading_assistant.market_data.repository import CandleRepository

    repository = CandleRepository(engine)
    for timeframe, expected in (("4h", 36), ("1h", 21), ("15m", 84), ("5m", 252)):
        result = repository.get_candles(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=timeframe
        )
        assert len(result.candles) == expected, timeframe
        assert result.missing_candle_count == 0, timeframe

    # --- Step 3: build and record one hierarchy snapshot -------------------
    service = _hierarchy_service(engine)
    first = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert first.status is RunnerStatus.PROCESSED
    assert first.observations_created == 1
    counts = service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL)
    assert counts["observations"] == 1
    assert counts["by_decision"] == {"awaiting_execution": 1}

    # --- Step 4: read it through the dashboard ------------------------------
    app = create_app(engine=engine, settings=settings, clock=lambda: DECISION_TIME)
    client = TestClient(app)
    try:
        response = client.get("/api/dashboard")
        assert response.status_code == 200
        ladder = response.json()["multi_timeframe"]
        assert ladder["available"] is True
        assert ladder["decision"] == "awaiting_execution"
        assert [row["label"] for row in ladder["ladder"]] == [
            "4H CONTEXT",
            "1H SETUP",
            "15M CONFIRMATION",
            "5M EXECUTION",
        ]
        assert ladder["latest_recorded"]["decision"] == "awaiting_execution"
        assert ladder["latest_recorded"]["decision_time"] == "2024-01-01T21:00:00Z"

        # --- Step 5: repeat the same boundary: idempotent -------------------
        repeated = service.run_once(now=DECISION_TIME, refresh_market_data=False)
        assert repeated.status is RunnerStatus.IDLE
        assert repeated.observations_recorded == 0
        assert service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL)["observations"] == 1

        # --- Step 6: advance the boundary: new state, old record untouched --
        scenario = scenario_candles(
            band_low=BAND_LOW,
            band_high=BAND_HIGH,
            closes_15m=("120", "119", "118", "117.5"),
            closes_5m=(
                "123", "122", "121", "120", "119", "118",
                "117.5", "117", "117.2", "117.5", "117.8", "118",
            ),
        )
        repository.insert_unchanged_or_new(scenario["5m"])
        repository.insert_unchanged_or_new(scenario["15m"])
        repository.insert_unchanged_or_new((one_hour_candles(extra_hour=True)[-1],))

        old_record = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)[0]
        advanced = service.run_once(now=SCENARIO_DECISION_TIME, refresh_market_data=False)
        assert advanced.status is RunnerStatus.PROCESSED
        assert advanced.observations_created == 12  # every closed 5M boundary
        observations = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
        assert len(observations) == 13
        unchanged = [
            row for row in observations if row.observation_id == old_record.observation_id
        ]
        assert len(unchanged) == 1
        assert unchanged[0].snapshot_json == old_record.snapshot_json
        newest = service.ledger.observations(
            exchange=EXCHANGE, symbol=SYMBOL, newest_first=True
        )[0]
        assert newest.decision == "plannable"
        assert json.loads(newest.snapshot_json)["execution"]["state"] == "triggered"
        assert json.loads(old_record.snapshot_json)["execution"]["state"] == "waiting"

        # --- Step 7: concurrent dashboard reads while a pass writes ---------
        errors: list[BaseException] = []
        stop_reading = threading.Event()
        read_count = {"n": 0}

        def reader() -> None:
            try:
                while not stop_reading.is_set():
                    response = client.get("/api/dashboard")
                    assert response.status_code == 200
                    read_count["n"] += 1
            except BaseException as exc:  # noqa: BLE001 - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for thread in threads:
            thread.start()
        try:
            # A pass that records nothing new still runs alongside the readers.
            again = service.run_once(now=SCENARIO_DECISION_TIME, refresh_market_data=False)
            assert again.status is RunnerStatus.IDLE
        finally:
            stop_reading.set()
            for thread in threads:
                thread.join(timeout=30)
        assert not errors
        assert read_count["n"] > 0
    finally:
        client.close()

    # --- Step 8: history is complete, readable and append-only --------------
    observations = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
    assert len(observations) == 13
    with engine.connect() as connection:
        # The immutability triggers still guard every recorded row.
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text("UPDATE forward_hierarchy_observations SET decision = 'no_setup'")
            )
        row_count = connection.execute(
            text("SELECT COUNT(*) FROM forward_hierarchy_observations")
        ).scalar_one()
    assert row_count == 13

    # --- Step 9: the configuration held throughout --------------------------
    configuration = describe_sqlite_configuration(engine)
    assert configuration["journal_mode"] == "wal"
    assert configuration["busy_timeout_ms"] == str(DEFAULT_SQLITE_BUSY_TIMEOUT_MS)
    assert configuration["foreign_keys"] == "1"
    engine.dispose()
