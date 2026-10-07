"""Controlled internet-outage recovery for the Step 12 forward runner.

Deterministic, offline regression suite for the runtime failure in which the
live forward runner stayed alive but stopped completing passes for about an
hour after the machine lost internet connectivity. The proven blocking path
was one CCXT network request that could hang indefinitely (CCXT's timeout is
per socket operation and cannot bound DNS resolution); the runner's retry and
stop logic can only act after a call returns or raises.

The suite drives the real runner, the real forward service, the real market
data service and a fake/stub public source - no real internet, no real
exchange, no credentials, a temporary file-backed SQLite database - through the
documented outage sequence:

    PASS 1  network available  -> normal pass completes, results recorded;
    PASS 2  request fails (simulated timeout / network error) -> the failure
            is bounded, classified transient, recorded, and NO new market
            conclusion is produced; the runner enters a recoverable retry
            state and the database stays usable;
    PASS 3  network still down -> bounded backoff, no busy loop, no
            fabricated data;
    PASS 4  network returns   -> automatic retry, the candles that closed
            during the outage are ingested, chronological catch-up happens,
            and normal "pass completed" behaviour resumes - no restart.

It also pins the runner's failure policy: transient external network failures
are recoverable indefinitely with bounded backoff and never stop the runner;
local/structural failures (including a SQLite lock, per PR #22) keep their
deliberate stop semantics; a successful pass resets the backoff; and shutdown
is never delayed by more than one finite network deadline.
"""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal as D

import ccxt
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    FakeExchange,
    bar,
    labelled_series,
    make_harness,
)

from trading_assistant.database import (
    create_database_engine,
    describe_sqlite_configuration,
)
from trading_assistant.forward_testing import (
    ForwardRunner,
    HeartbeatStatus,
    RunnerSettings,
    run_single_pass,
)
from trading_assistant.forward_testing.errors import ForwardError
from trading_assistant.forward_testing.parameters import CycleStatus, DataHealth
from trading_assistant.forward_testing.tables import ForwardRunnerHeartbeatRow
from trading_assistant.market_data.exchange import CCXTMarketDataSource
from trading_assistant.market_data.timeframes import datetime_to_milliseconds

#: Candles that close on the exchange while the runner is offline (opens at
#: QUALIFYING_BOUNDARY .. +3h).
OFFLINE_CANDLES = (
    bar(21, 126, low=123),
    bar(22, 128, low=125),
    bar(23, 130, low=127),
    bar(24, 132, low=129),
)


@dataclass
class ScriptedOutageExchange(FakeExchange):
    """A fake public source that follows a per-call script.

    Each script entry is either ``"ok"`` (serve the candles) or an exception
    instance to raise (a simulated outage failure). The clock is advanced by
    the runner's (patched) poll wait, which is where time passes between two
    polls. After the script is exhausted the source serves candles again.
    """

    script: tuple = ()
    clock: dict | None = None

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        # ``self.calls`` counts completed fetches (FakeExchange increments it);
        # the failing branch increments it manually to stay consistent.
        behavior = self.script[self.calls] if self.calls < len(self.script) else "ok"
        if behavior != "ok":
            self.calls += 1
            raise behavior
        return super().fetch_ohlcv(symbol, timeframe=timeframe, since_ms=since_ms, limit=limit)


def make_outage_harness(
    *,
    series=None,
    script=(),
    ledger_start=None,
    runner_settings=None,
):
    """A harness whose source follows ``script`` and whose clock the (patched)
    poll wait advances by one interval per pass.

    The service's in-pass fetch retry is disabled by default (one network
    attempt per pass): the script maps one-to-one onto passes, and pacing
    between passes is the runner's bounded backoff under test.
    """

    source = ScriptedOutageExchange()
    source.set_candles(series if series is not None else labelled_series() + OFFLINE_CANDLES)
    source.script = tuple(script)
    harness = make_harness(
        series=labelled_series(),
        ledger_start=ledger_start if ledger_start is not None else QUALIFYING_BOUNDARY + INTERVAL,
        source=source,
        runner_settings=(
            runner_settings
            if runner_settings is not None
            else RunnerSettings(
                interval_seconds=D("1"),
                fetch_max_attempts=1,
                fetch_retry_backoff_seconds=D(0),
            )
        ),
    )
    source.clock = harness.clock
    return harness


def make_outage_runner(harness, **overrides):
    """A runner whose waits are recorded (and the clock advanced) instead of slept."""

    values = {
        "interval_seconds": D("30"),
        "fetch_max_attempts": 1,
        "fetch_retry_backoff_seconds": D(0),
        "stop_after_errors": 10,
        "transient_backoff_min_seconds": D("5"),
        "transient_backoff_max_seconds": D("60"),
    }
    values.update(overrides)
    runner = ForwardRunner(
        harness.service,
        settings=RunnerSettings(**values),
        runner_id="outage-test",
    )
    retry_waits: list[float] = []
    poll_waits: list[float] = []

    def wait_next(timeframe):
        poll_waits.append(1.0)
        harness.clock["t"] = harness.clock["t"] + INTERVAL

    def wait_retry(delay):
        retry_waits.append(float(delay))
        # Time passes during the backoff too: the outage spans several
        # candle closes, which the recovered pass must catch up on.
        harness.clock["t"] = harness.clock["t"] + INTERVAL

    runner._wait_for_next_check = wait_next
    runner._wait_before_retry = wait_retry
    return runner, retry_waits, poll_waits


def record_passes(harness):
    """Wrap ``run_once`` so every pass result and stored candle count is captured."""

    passes: list = []
    real_run_once = harness.service.run_once

    def recording_run_once(**kwargs):
        result = real_run_once(**kwargs)
        passes.append((result, len(harness.candles().candles)))
        return result

    harness.service.run_once = recording_run_once
    return passes


def heartbeat_rows(harness):
    with Session(harness.engine) as session:
        from sqlalchemy import select

        return tuple(
            session.execute(
                select(ForwardRunnerHeartbeatRow).order_by(
                    ForwardRunnerHeartbeatRow.recorded_at,
                    ForwardRunnerHeartbeatRow.heartbeat_id,
                )
            ).scalars()
        )


# ----------------------------------------------------------------------
# The documented outage sequence, end to end
# ----------------------------------------------------------------------


def test_runner_survives_a_temporary_outage_and_catches_up_chronologically() -> None:
    """PASS 1-4: outage, bounded failure, backoff, automatic recovery."""

    harness = make_outage_harness(
        script=(
            "ok",                                    # PASS 1: network available
            ccxt.NetworkError("simulated outage"),   # PASS 2: request fails
            ccxt.RequestTimeout("simulated timeout"),  # PASS 3: still down
            "ok",                                    # PASS 4: network returns
        )
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, poll_waits = make_outage_runner(harness)
    passes = record_passes(harness)

    result = runner.run(refresh_market_data=True, max_passes=4)

    assert result is not None
    assert len(passes) == 4

    # ---- PASS 1: network available -> normal pass completes -----------------
    first, stored_after_pass1 = passes[0]
    assert first.market_data_error is None
    assert first.market_data_error_transient is False
    assert first.status is HeartbeatStatus.PROCESSED
    assert first.processed_boundaries == (QUALIFYING_BOUNDARY + INTERVAL,)
    assert first.data_health is DataHealth.CURRENT
    assert stored_after_pass1 == 22  # 21 stored + the one candle PASS 1 fetched

    # ---- PASS 2: the request fails; the failure is bounded and recorded -----
    second, stored_after_outage = passes[1]
    assert second.market_data_error is not None
    assert "NetworkError" in second.market_data_error
    assert second.market_data_error_type == "ExchangeDataError"
    assert second.market_data_error_transient is True
    # No new market conclusion from unavailable data: the boundary whose
    # candle could not be fetched is recorded as MISSING_CANDLE, never as a
    # fresh analysis, and no candle was fabricated.
    assert second.processed_boundaries == (QUALIFYING_BOUNDARY + 2 * INTERVAL,)
    assert second.data_health is DataHealth.STALE
    assert stored_after_outage == 22  # nothing new could be stored

    # ---- PASS 3: still down -> bounded backoff, no busy loop, no data -------
    third, stored_still_down = passes[2]
    assert third.market_data_error is not None
    assert "RequestTimeout" in third.market_data_error
    assert third.market_data_error_transient is True
    assert third.data_health is DataHealth.STALE
    assert stored_still_down == stored_after_outage  # nothing fabricated

    # Exactly one network attempt per pass: no busy loop, no hidden retries.
    assert harness.source.calls == 4
    # The runner waited the bounded exponential backoff between the failed
    # passes (5s, then 10s) and the normal poll wait only after PASS 1.
    assert retry_waits == [5.0, 10.0]
    assert poll_waits == [1.0]

    # The database stayed usable throughout the outage.
    assert harness.counts()["cycles"] >= 1

    # ---- PASS 4: network returns -> automatic retry and chronological catch-up
    fourth, stored_after_recovery = passes[3]
    assert fourth.market_data_error is None
    assert fourth.market_data_error_transient is False
    assert fourth.status is HeartbeatStatus.PROCESSED
    assert fourth.processed_boundaries == (
        QUALIFYING_BOUNDARY + 2 * INTERVAL,
        QUALIFYING_BOUNDARY + 3 * INTERVAL,
        QUALIFYING_BOUNDARY + 4 * INTERVAL,
    )
    assert fourth.data_health is DataHealth.CURRENT
    # The candles that closed during the outage were ingested.
    assert stored_after_recovery == 25  # 21 + 4 offline candles
    stored = harness.candles().candles
    assert len(stored) == 25
    assert [candle.timestamp for candle in stored][-4:] == [
        candle.timestamp for candle in OFFLINE_CANDLES
    ]
    # No retry wait after the recovered pass: normal polling resumes.
    assert retry_waits == [5.0, 10.0]
    assert poll_waits == [1.0]

    # ---- Recovery invariants -------------------------------------------------
    cycles = harness.cycles()
    by_boundary: dict = {}
    for cycle in cycles:
        by_boundary.setdefault(cycle.as_of, []).append(cycle)

    # Every boundary from the ledger start through the recovered target has
    # exactly one COMPLETE cycle - the outage boundaries first recorded
    # MISSING_CANDLE (truthful) and were then re-analysed once their candle
    # arrived, recorded as their own row, never duplicated.
    for index in range(1, 5):
        boundary = QUALIFYING_BOUNDARY + index * INTERVAL
        statuses = [cycle.status for cycle in by_boundary.get(boundary, ())]
        assert statuses.count(CycleStatus.COMPLETE) == 1, boundary
    # The outage boundaries keep their truthful MISSING_CANDLE record too.
    assert CycleStatus.MISSING_CANDLE in [
        cycle.status for cycle in by_boundary[QUALIFYING_BOUNDARY + 2 * INTERVAL]
    ]
    assert CycleStatus.MISSING_CANDLE in [
        cycle.status for cycle in by_boundary[QUALIFYING_BOUNDARY + 3 * INTERVAL]
    ]
    # No duplicate cycles, observations, plans or outcomes anywhere.
    assert len({cycle.cycle_id for cycle in cycles}) == len(cycles)
    observations = harness.observations()
    assert len({obs.observation_id for obs in observations}) == len(observations)
    complete_cycle_ids = {cycle.cycle_id for cycle in cycles if cycle.complete}
    assert {obs.cycle_id for obs in observations} <= complete_cycle_ids
    plans = harness.plans()
    assert len({plan.paper_plan_id for plan in plans}) == len(plans)

    # A repeated pass at the same clock records nothing new (idempotent).
    counts_before = harness.counts()
    again = run_single_pass(
        harness.service, symbol=SYMBOL, timeframe=TIMEFRAME, refresh_market_data=True
    )
    assert again.status is HeartbeatStatus.IDLE
    assert again.observations_recorded == 0
    assert again.paper_plans_created == 0
    assert harness.counts() == counts_before

    # The dashboard can keep reading: data health is truthful again, the
    # newest heartbeat carries no error, and the outage errors are preserved
    # in the trail.
    status = harness.service.status()
    assert status["market_data"]["data_health"] == DataHealth.CURRENT.value
    rows = heartbeat_rows(harness)
    # The runner closed cleanly (STOPPED heartbeat) and the newest heartbeat
    # carries no error: the outage did not poison the dashboard after recovery.
    assert HeartbeatStatus.STOPPED.value in {row.status for row in rows}
    newest = harness.service.ledger.latest_heartbeat(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert newest.last_error is None
    # ... while the outage failures themselves stay recorded in the trail.
    assert any(
        row.last_error and "NetworkError" in row.last_error
        for row in rows
    )
    assert any(
        row.last_error and "RequestTimeout" in row.last_error
        for row in rows
    )

    # SQLite is not left locked and the documented runtime configuration
    # (WAL, finite busy timeout, foreign keys) is intact.
    assert harness.engine.pool.checkedout() == 0
    configuration = describe_sqlite_configuration(harness.engine)
    assert configuration["journal_mode"] == "wal"
    assert configuration["foreign_keys"] == "1"
    assert 0 < int(configuration["busy_timeout_ms"]) <= 60_000
    harness.engine.dispose()


def test_runner_pass_fails_within_the_bounded_network_deadline() -> None:
    """PASS 2 with a *blocking* request: the watchdog bounds the pass.

    A stub CCXT client hangs inside the network call (the proven outage
    shape: the request never returns). The source's finite watchdog deadline
    terminates the request, the failure is classified transient, no data is
    fabricated, and the pass completes instead of hanging forever.
    """

    release = threading.Event()

    class _BlockingClient:
        id = EXCHANGE
        timeframes = {"1h": "60"}
        markets = {"BTC/USDT": {"id": "XBTUSDT"}}
        number = float
        last_http_response = None

        def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
            release.wait(timeout=30)
            return []

        def close(self) -> None:
            return None

    source = CCXTMarketDataSource.__new__(CCXTMarketDataSource)
    source._exchange = _BlockingClient()
    source.exchange_id = EXCHANGE
    source.last_http_response = None
    source._timeout_ms = 1_000  # 3s watchdog deadline (3 x the 1s CCXT timeout)
    source._exchange_class = ccxt.kraken

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY + INTERVAL,
        source=source,
        runner_settings=RunnerSettings(
            interval_seconds=D("1"),
            fetch_max_attempts=1,  # one bounded attempt: the watchdog is the bound
            fetch_retry_backoff_seconds=D(0),
        ),
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, _poll_waits = make_outage_runner(harness)

    started = time.monotonic()
    try:
        result = runner.run(once=True, refresh_market_data=True)
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert result is not None
    # The request terminated within the configured bound (the 3s watchdog
    # deadline), not indefinitely.
    assert elapsed < 10.0
    assert result.market_data_error is not None
    assert result.market_data_error_type == "ExchangeDataError"
    assert result.market_data_error_transient is True
    assert "ExchangeNetworkTimeout" in result.market_data_error
    # No fabricated candles and no market conclusion from unavailable data.
    assert len(harness.candles().candles) == 21
    assert harness.counts()["cycles"] == 1
    cycle = harness.cycles()[0]
    assert cycle.status is CycleStatus.MISSING_CANDLE
    assert harness.counts()["observations"] == 0
    assert harness.counts()["paper_plans"] == 0
    assert harness.engine.pool.checkedout() == 0
    harness.engine.dispose()


# ----------------------------------------------------------------------
# Failure policy: transient vs structural
# ----------------------------------------------------------------------


def test_transient_network_failures_never_stop_the_runner() -> None:
    """A long outage must not kill the long-running process.

    Even with ``stop_after_errors=3``, twelve consecutive transient network
    failures are all retried with bounded backoff and none of them stops the
    runner: transient external failures are recoverable indefinitely.
    """

    harness = make_outage_harness(
        script=tuple(ccxt.NetworkError("simulated outage") for _ in range(12))
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, poll_waits = make_outage_runner(
        harness, stop_after_errors=3
    )
    passes = record_passes(harness)

    result = runner.run(refresh_market_data=True, max_passes=12)

    assert result is not None  # the runner did not raise or stop early
    assert len(passes) == 12
    assert all(p[0].market_data_error is not None for p in passes)
    assert all(p[0].market_data_error_transient is True for p in passes)
    # Bounded exponential backoff, capped: 5, 10, 20, 40, then capped at 60.
    assert retry_waits == [5.0, 10.0, 20.0, 40.0] + [60.0] * 7
    assert poll_waits == []  # every wait was a retry wait, never a busy loop
    # Nothing was fabricated and no conclusion was invented from the outage.
    assert len(harness.candles().candles) == 21
    assert harness.counts()["observations"] == 0
    assert harness.counts()["paper_plans"] == 0
    assert all(
        cycle.status is CycleStatus.MISSING_CANDLE for cycle in harness.cycles()
    )
    harness.engine.dispose()


@pytest.mark.parametrize(
    "error",
    [
        ccxt.RequestTimeout("kraken: request timed out"),
        ccxt.ExchangeNotAvailable("kraken: temporarily unavailable"),
        ccxt.NetworkError("kraken: connection lost"),
        ConnectionError("connection reset by peer"),
        socket.gaierror(-2, "temporary failure in name resolution"),
    ],
)
def test_realistic_network_exceptions_are_transient_and_recoverable(error) -> None:
    """Representative CCXT/OS network failures classify as transient.

    The actual CCXT exception hierarchy is used (RequestTimeout,
    ExchangeNotAvailable, NetworkError) plus the operating-system network
    errors, each through the real market-data wrapping and the real forward
    pass; after the failure the runner recovers automatically.
    """

    harness = make_outage_harness(script=(error, "ok"))
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, _poll_waits = make_outage_runner(harness)
    passes = record_passes(harness)

    runner.run(refresh_market_data=True, max_passes=2)

    assert len(passes) == 2
    assert passes[0][0].market_data_error is not None
    assert passes[0][0].market_data_error_transient is True
    assert passes[1][0].market_data_error is None
    assert passes[1][0].status is HeartbeatStatus.PROCESSED
    assert retry_waits == [5.0]
    harness.engine.dispose()


def test_non_transient_market_data_failures_stop_after_the_configured_limit() -> None:
    """A structural market-data failure is not retried like an outage.

    The exchange returning invalid rows is a local/structural failure: it is
    recorded on the completed pass, counted toward ``stop_after_errors``, and
    stops the runner loudly once the limit is reached - it never loops
    forever and it is never classified as a network outage.
    """

    class _InvalidRowsExchange(FakeExchange):
        def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
            self.calls += 1
            # high below open: rejected by the unchanged Step 2 validation.
            invalid = [
                datetime_to_milliseconds(QUALIFYING_BOUNDARY),
                "12",
                "11",
                "10",
                "10.5",
                "1",
            ]
            return [invalid]

    source = _InvalidRowsExchange()
    source.set_candles(labelled_series() + OFFLINE_CANDLES)
    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY + INTERVAL,
        source=source,
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, _poll_waits = make_outage_runner(harness, stop_after_errors=3)
    passes = record_passes(harness)

    with pytest.raises(ForwardError, match="non-transient market-data failures"):
        runner.run(refresh_market_data=True, max_passes=10)

    assert len(passes) == 3  # stopped at the limit, not after one, not forever
    assert all(p[0].market_data_error is not None for p in passes)
    assert all(p[0].market_data_error_transient is False for p in passes)
    assert passes[0][0].market_data_error_type == "CandleValidationError"
    assert retry_waits == []  # structural failures use the poll cadence, not backoff
    # Nothing was stored from the invalid rows.
    assert len(harness.candles().candles) == 21
    harness.engine.dispose()


def test_sqlite_lock_during_refresh_stops_the_runner_immediately() -> None:
    """PR #22 semantics preserved through the refresh path.

    A SQLite lock failure raised while the refresh writes newly fetched
    candles is never swallowed into the pass's error report and retried on
    the next pass (that would multiply blocked writes on top of the held
    lock): it propagates and the runner stops on the first attempt, with the
    original error, exactly like a lock failure in the ledger write path.
    """

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY + INTERVAL,
        source=FakeExchange(),
        sqlite_busy_timeout_ms=200,
    )
    harness.source.set_candles(labelled_series() + (bar(21, 126, low=123),))
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner = ForwardRunner(
        harness.service,
        settings=RunnerSettings(
            interval_seconds=D("1"),
            fetch_max_attempts=1,
            fetch_retry_backoff_seconds=D(0),
            stop_after_errors=10,
        ),
        runner_id="locked-refresh-runner",
    )
    attempts = {"count": 0}
    real_run_once = harness.service.run_once

    def counting_run_once(**kwargs):
        attempts["count"] += 1
        return real_run_once(**kwargs)

    harness.service.run_once = counting_run_once

    holder_engine = create_database_engine(
        harness.settings.database_url, sqlite_busy_timeout_ms=200
    )
    captured: DBAPIError | None = None
    try:
        with Session(holder_engine) as holder_session:
            holder_session.execute(text("BEGIN IMMEDIATE"))
            holder_session.execute(
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
            holder_session.flush()
            try:
                runner.run(
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    once=False,
                    refresh_market_data=True,
                    max_passes=3,
                )
            except DBAPIError as exc:
                captured = exc
            holder_session.rollback()
    finally:
        holder_engine.dispose()

    # The original lock error escaped - not a heartbeat error raised while
    # trying to report it - and the runner made exactly one pass attempt
    # (no retry storm on top of the held lock) even though stop_after_errors
    # allowed ten.
    assert captured is not None
    assert "database is locked" in str(captured).lower()
    assert attempts["count"] == 1
    # Nothing was fabricated and no conclusion was recorded from the attempt.
    assert harness.counts()["cycles"] == 0
    assert harness.counts()["observations"] == 0
    harness.engine.dispose()


# ----------------------------------------------------------------------
# Backoff policy, reset, and shutdown responsiveness
# ----------------------------------------------------------------------


def test_transient_backoff_is_bounded_exponential_and_capped() -> None:
    settings = RunnerSettings(
        transient_backoff_min_seconds=D("5"),
        transient_backoff_max_seconds=D("60"),
    )
    assert [settings.transient_backoff_seconds(n) for n in range(1, 9)] == [
        D(5),
        D(10),
        D(20),
        D(40),
        D(60),
        D(60),
        D(60),
        D(60),
    ]
    # Defaults: 5s growing to a 300s cap - never a busy loop, never hours.
    defaults = RunnerSettings()
    assert defaults.transient_backoff_seconds(1) == D(5)
    assert defaults.transient_backoff_seconds(100) == D(300)

    with pytest.raises(ValueError):
        RunnerSettings(transient_backoff_min_seconds=D("0"))
    with pytest.raises(ValueError):
        RunnerSettings(transient_backoff_min_seconds=D("-1"))
    with pytest.raises(ValueError):
        RunnerSettings(transient_backoff_min_seconds=D("601"))
    with pytest.raises(ValueError):
        RunnerSettings(
            transient_backoff_min_seconds=D("5"),
            transient_backoff_max_seconds=D("1"),
        )
    with pytest.raises(ValueError):
        RunnerSettings(transient_backoff_max_seconds=D("3601"))


def test_successful_pass_resets_the_transient_backoff() -> None:
    """A clean pass resets the transient failure count and its backoff."""

    harness = make_outage_harness(
        script=(
            ccxt.NetworkError("outage"),
            "ok",  # recovery: the backoff resets here
            ccxt.NetworkError("outage again"),
            "ok",
        )
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner, retry_waits, poll_waits = make_outage_runner(harness)
    passes = record_passes(harness)

    runner.run(refresh_market_data=True, max_passes=4)

    assert [p[0].market_data_error is None for p in passes] == [
        False,
        True,
        False,
        True,
    ]
    # The backoff restarted from the minimum after the recovered pass.
    assert retry_waits == [5.0, 5.0]
    assert poll_waits == [1.0]
    harness.engine.dispose()


def test_stop_during_retry_backoff_exits_promptly() -> None:
    """Ctrl+C/SIGTERM during the retry backoff stops the runner immediately."""

    harness = make_outage_harness(
        script=tuple(ccxt.NetworkError("simulated outage") for _ in range(50))
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    runner = ForwardRunner(
        harness.service,
        settings=RunnerSettings(
            interval_seconds=D("30"),
            fetch_max_attempts=1,
            fetch_retry_backoff_seconds=D(0),
            # A long backoff: if the stop event did not interrupt it, the
            # runner thread would sleep here for 30 seconds.
            transient_backoff_min_seconds=D("30"),
            transient_backoff_max_seconds=D("60"),
        ),
        runner_id="stop-test",
    )

    outcome: dict = {}

    def run() -> None:
        try:
            outcome["result"] = runner.run(refresh_market_data=True, max_passes=50)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        # Wait until the first failed pass recorded its error heartbeat, i.e.
        # the runner is now inside the 30s retry backoff.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            newest = harness.service.ledger.latest_heartbeat(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
            )
            if newest is not None and newest.last_error:
                break
            time.sleep(0.02)
        else:
            pytest.fail("the runner never recorded a failed pass")
        runner.request_stop()
        thread.join(timeout=10.0)
    finally:
        runner.request_stop()
        thread.join(timeout=10.0)
    elapsed = time.monotonic() - started

    assert not thread.is_alive(), "the runner must exit during the backoff"
    assert elapsed < 10.0, "shutdown waited on the backoff instead of the stop event"
    assert "error" not in outcome
    assert outcome.get("result") is not None
    assert outcome["result"].market_data_error is not None
    assert outcome["result"].market_data_error_transient is True
    harness.engine.dispose()


def test_in_pass_retry_backoff_is_interrupted_by_stop() -> None:
    """A stop during the *in-pass* fetch retry backoff ends the retry loop."""

    harness = make_outage_harness()
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.service.runner_settings = RunnerSettings(
        interval_seconds=D("1"),
        fetch_max_attempts=3,
        fetch_retry_backoff_seconds=D("30"),  # long in-pass backoff
    )
    source = harness.source
    source.fail_calls = (1, 2, 3)
    source.failing_exception = ccxt.NetworkError

    outcome: dict = {}

    def run() -> None:
        try:
            outcome["result"] = harness.service.run_once(refresh_market_data=True)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        # Wait for the first failed fetch, then stop while the pass is in its
        # 30s in-pass backoff sleep.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and source.calls < 1:
            time.sleep(0.02)
        assert source.calls >= 1
        harness.service.request_stop()
        thread.join(timeout=10.0)
    finally:
        harness.service.request_stop()
        thread.join(timeout=10.0)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < 10.0, "the in-pass backoff was not interrupted by the stop"
    assert "error" not in outcome
    result = outcome["result"]
    assert result.market_data_error is not None
    assert result.market_data_error_transient is True
    # The stop prevented further network attempts: exactly one fetch ran.
    assert source.calls == 1
    harness.engine.dispose()


# ----------------------------------------------------------------------
# Database safety during the outage path
# ----------------------------------------------------------------------


def test_no_database_transaction_is_held_during_network_io() -> None:
    """PR #22 guarantee on the outage path: no transaction spans network I/O.

    While the exchange request is in flight (a slow stub), no pooled
    connection is checked out - so no SQLAlchemy transaction or session is
    open across the network call, and a hung request can never hold a SQLite
    lock. This is why the process samples that showed both SQLite and
    network-related frames were pool housekeeping, not a lock held across a
    blocking call.
    """

    harness = make_outage_harness()
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    checked_out_during_fetch: list[int] = []

    real_fetch = harness.source.fetch_ohlcv

    def slow_fetch(symbol, *, timeframe, since_ms, limit):
        checked_out_during_fetch.append(harness.engine.pool.checkedout())
        time.sleep(0.1)  # the request is "in flight"
        return real_fetch(symbol, timeframe=timeframe, since_ms=since_ms, limit=limit)

    harness.source.fetch_ohlcv = slow_fetch

    result = harness.service.run_once(refresh_market_data=True)

    assert result.market_data_error is None
    assert result.status is HeartbeatStatus.PROCESSED
    assert checked_out_during_fetch and all(
        count == 0 for count in checked_out_during_fetch
    ), "a database connection was checked out during the network request"
    assert harness.engine.pool.checkedout() == 0
    harness.engine.dispose()


def test_dashboard_reader_coexists_with_an_offline_runner_pass() -> None:
    """While the runner is offline, a dashboard reader still reads (WAL)."""

    harness = make_outage_harness(
        script=(ccxt.NetworkError("simulated outage"), "ok")
    )
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    reader_engine = create_database_engine(harness.settings.database_url)
    try:
        reader_connection = reader_engine.connect()
        reader_connection.execute(text("BEGIN"))
        try:
            # The dashboard reads while the runner's pass cannot fetch.
            rows = reader_connection.execute(
                text("SELECT count(*) FROM ohlcv_candles")
            ).scalar()
            assert rows == 21
            result = harness.service.run_once(refresh_market_data=True)
            assert result.market_data_error is not None
            assert result.market_data_error_transient is True
            # The reader is unaffected by the failed pass (WAL: readers never
            # block and are never blocked by the writer).
            assert (
                reader_connection.execute(
                    text("SELECT count(*) FROM ohlcv_candles")
                ).scalar()
                == 21
            )
        finally:
            reader_connection.rollback()
            reader_connection.close()
        # The recovered pass writes normally next to a fresh reader.
        recovered = harness.service.run_once(refresh_market_data=True)
        assert recovered.market_data_error is None
        assert recovered.status is HeartbeatStatus.PROCESSED
        assert harness.engine.pool.checkedout() == 0
    finally:
        reader_engine.dispose()
        harness.engine.dispose()
