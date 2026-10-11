"""Regression tests: the forward runner waits toward the NEXT candle close.

Bug fixed: ``_wait_for_next_check`` added one interval to the latest *closed* open time. That
gives the open of the candle still forming, which is already in the past, so the wait fell to its
1-second floor and the continuous runner recorded an IDLE pass every second instead of every
poll interval.

These tests use a fixed clock and a simulated wait. They never sleep for real (except the
interruption test, which uses a 0.1 s real stop request). No network, no project data.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from forward_fixtures import QUALIFYING_BOUNDARY, clock_at, labelled_series, make_harness

from trading_assistant.forward_testing import ForwardRunner, RunnerSettings
from trading_assistant.market_data.timeframes import next_candle_close_time

GRACE = 1.0  # CANDLE_CLOSE_GRACE_SECONDS in runner.py
INTERVAL_MINUTES = {"5m": 5, "15m": 15, "1h": 60}
BASE_CLOSE = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)  # aligned to 5m, 15m and 1h


def close_instant(timeframe: str, k: int) -> datetime:
    """The k-th candle close after BASE_CLOSE for a timeframe (aligned, UTC)."""

    return BASE_CLOSE + timedelta(minutes=INTERVAL_MINUTES[timeframe] * k)


def runner_with(clock_t: datetime, *, interval_seconds: str, timeframe_harness=None):
    harness = timeframe_harness or make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.clock["t"] = clock_t
    settings = RunnerSettings(
        interval_seconds=D(interval_seconds),
        fetch_max_attempts=1,
        fetch_retry_backoff_seconds=D(0),
    )
    runner = ForwardRunner(harness.service, settings=settings, runner_id="polling-test")
    return harness, runner


def captured_wait(runner, timeframe: str) -> float:
    """Run the real wait method and return the timeout it asked for (never really sleeps)."""

    waits: list[float] = []
    runner._stop_event.wait = lambda timeout=None: waits.append(timeout) or False  # noqa: SLF001
    runner._wait_for_next_check(timeframe)  # noqa: SLF001
    assert len(waits) == 1
    return waits[0]


# ----------------------------------------------------------------------
# The boundary helper
# ----------------------------------------------------------------------


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_next_close_is_the_forming_candles_close_before_at_and_after_a_boundary(timeframe: str) -> None:
    close = close_instant(timeframe, 3)
    interval = timedelta(minutes=INTERVAL_MINUTES[timeframe])
    # Before the close: the candle ending at ``close`` is still forming.
    assert next_candle_close_time(close - timedelta(seconds=30), timeframe) == close
    # Exactly at the close: that candle is complete; the next close is one interval later.
    assert next_candle_close_time(close, timeframe) == close + interval
    # Immediately after: still the next close one interval later.
    assert next_candle_close_time(close + timedelta(milliseconds=500), timeframe) == close + interval


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_next_close_is_always_strictly_in_the_future(timeframe: str) -> None:
    start = BASE_CLOSE
    for second in range(0, 3 * 60 * 60, 37):  # a sweep across several boundaries
        now = start + timedelta(seconds=second)
        assert next_candle_close_time(now, timeframe) > now


# ----------------------------------------------------------------------
# The wait toward that boundary
# ----------------------------------------------------------------------


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_wait_before_a_close_targets_that_close_plus_grace(timeframe: str) -> None:
    close = close_instant(timeframe, 3)
    _, runner = runner_with(close - timedelta(seconds=30), interval_seconds="3600")
    # Not capped by the (very long) poll interval: it wakes just after the close.
    assert captured_wait(runner, timeframe) == pytest.approx(30 + GRACE)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_wait_at_a_close_targets_the_next_close_not_the_one_just_passed(timeframe: str) -> None:
    close = close_instant(timeframe, 3)
    interval = INTERVAL_MINUTES[timeframe] * 60
    # Poll longer than the interval, so the poll cap cannot hide the boundary arithmetic.
    _, runner = runner_with(close, interval_seconds="7200")
    assert captured_wait(runner, timeframe) == pytest.approx(interval + GRACE)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_wait_immediately_after_a_close_targets_the_next_close(timeframe: str) -> None:
    close = close_instant(timeframe, 3)
    interval = INTERVAL_MINUTES[timeframe] * 60
    _, runner = runner_with(close + timedelta(milliseconds=500), interval_seconds="7200")
    assert captured_wait(runner, timeframe) == pytest.approx(interval - 0.5 + GRACE)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_wait_is_capped_by_the_poll_interval_far_from_a_close(timeframe: str) -> None:
    close = close_instant(timeframe, 3)
    _, runner = runner_with(close, interval_seconds="60")
    # The next close is minutes away, so the configured poll interval is the bound.
    assert captured_wait(runner, timeframe) == pytest.approx(60.0)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_wait_is_exactly_min_poll_and_time_to_close_across_a_full_hour(timeframe: str) -> None:
    interval_seconds = 60.0
    harness, runner = runner_with(BASE_CLOSE, interval_seconds="60")
    for second in range(0, 3600, 13):
        now = BASE_CLOSE + timedelta(seconds=second)
        harness.clock["t"] = now  # one migrated database, many instants
        wait = captured_wait(runner, timeframe)
        expected_close = next_candle_close_time(now, timeframe)
        to_close = (expected_close - now).total_seconds()
        assert wait == pytest.approx(min(interval_seconds, to_close + GRACE))
        assert 1.0 <= wait <= interval_seconds  # never a sub-second spin, never over the poll


def test_a_one_hour_poll_on_a_five_minute_timeframe_still_wakes_after_each_close() -> None:
    # Long poll, short timeframe: the wait must end one grace after the close, not at the poll.
    close = close_instant("5m", 2)
    _, runner = runner_with(close - timedelta(seconds=10), interval_seconds="3600")
    assert captured_wait(runner, "5m") == pytest.approx(10 + GRACE)


# ----------------------------------------------------------------------
# The continuous loop, simulated: one hour of passes
# ----------------------------------------------------------------------


class SimulatedWait(threading.Event):
    """Replaces the runner's stop event: each wait advances the harness clock by its timeout."""

    def __init__(self, clock: dict, end: datetime) -> None:
        super().__init__()
        self._clock = clock
        self._end = end
        self.timeouts: list[float] = []

    def wait(self, timeout=None) -> bool:  # type: ignore[override]
        self.timeouts.append(float(timeout))
        self._clock["t"] = self._clock["t"] + timedelta(seconds=float(timeout))
        if self._clock["t"] >= self._end:
            self.set()
        return self.is_set()


def test_continuous_loop_polls_about_once_per_interval_not_once_per_second(monkeypatch) -> None:
    start = close_instant("15m", 1) + timedelta(seconds=20)  # 20 s after a 15m close
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.clock["t"] = start
    settings = RunnerSettings(
        interval_seconds=D("60"),
        fetch_max_attempts=1,
        fetch_retry_backoff_seconds=D(0),
    )
    runner = ForwardRunner(harness.service, settings=settings, runner_id="polling-test")
    fake = SimulatedWait(harness.clock, end=start + timedelta(hours=1))
    monkeypatch.setattr(runner, "_stop_event", fake)
    passes = {"count": 0}
    real_run_once = harness.service.run_once

    def counted_run_once(**kwargs):
        passes["count"] += 1
        return real_run_once(**kwargs)

    monkeypatch.setattr(harness.service, "run_once", counted_run_once)

    # 15m is a timeframe the forward runner supports (its configured set is 1m, 15m, 1h, 4h, 1d).
    runner.run(symbol=None, timeframe="15m", once=False, refresh_market_data=False)

    passes_waits = fake.timeouts
    # One hour at a 60 s poll is about 60 waits. The bug would have produced roughly 3,600.
    assert 55 <= len(passes_waits) <= 75, len(passes_waits)
    assert min(passes_waits) >= 1.0  # never a sub-second spin
    assert max(passes_waits) <= 60.0  # never longer than the configured poll
    assert fake.is_set()  # the loop ended on the simulated stop, not on an error
    # One pass per wait: passes track the poll rate, not the one-second floor.
    assert 55 <= passes["count"] <= 76, passes["count"]


# ----------------------------------------------------------------------
# Interruptible shutdown and the retry wait (existing behaviour, re-verified)
# ----------------------------------------------------------------------


def test_stop_request_interrupts_a_long_wait_promptly_in_real_time() -> None:
    close = close_instant("1h", 2)
    _, runner = runner_with(close, interval_seconds="3600")
    finished = threading.Event()

    def wait_for_close() -> None:
        runner._wait_for_next_check("1h")  # noqa: SLF001 - real Event, real wait
        finished.set()

    worker = threading.Thread(target=wait_for_close, daemon=True)
    started = time.monotonic()
    worker.start()
    time.sleep(0.05)  # let the worker reach the wait (a short bound, not a sleep for the test's logic)
    runner.request_stop()
    assert finished.wait(timeout=5.0), "shutdown did not interrupt the wait"
    assert time.monotonic() - started < 5.0


def test_retry_wait_still_uses_the_stop_event_and_is_bounded() -> None:
    _, runner = runner_with(close_instant("5m", 2), interval_seconds="60")
    waits: list[float] = []
    runner._stop_event.wait = lambda timeout=None: waits.append(timeout) or False  # noqa: SLF001
    runner._wait_before_retry(7.5)  # noqa: SLF001
    assert waits == [7.5]
    runner._wait_before_retry(-3.0)  # noqa: SLF001 - a negative delay never sleeps backwards
    assert waits[-1] == 0.0
