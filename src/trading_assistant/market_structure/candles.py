"""Shared, read-only candle-window helpers for market-structure calculations.

These helpers never modify, reorder, or invent candles. They validate the
integrity of an input sequence and select only the candles whose full interval
had already closed at a requested ``as_of`` instant, which is the single
anti-lookahead rule every market-structure component relies on.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from trading_assistant.market_data.timeframes import (
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle


def interval_for_timeframe(timeframe: str) -> timedelta:
    """Return the fixed duration of a supported timeframe.

    The conversion itself lives in the Step 2 market-data module so that a
    timeframe can never mean two different durations in this project.
    """

    return timedelta(milliseconds=timeframe_to_milliseconds(timeframe))


def require_positive_interval(interval: timedelta) -> timedelta:
    """Reject a missing, zero, or negative candle interval."""

    if not isinstance(interval, timedelta):
        raise TypeError("interval must be a datetime.timedelta")
    if interval <= timedelta(0):
        raise ValueError("interval must be a positive duration")
    return interval


def ordered_candles(candles: Iterable[Candle], *, interval: timedelta) -> tuple[Candle, ...]:
    """Return candles unchanged after validating order, spacing, and identity.

    Raises ``ValueError`` when candles are out of order, duplicated, spaced by a
    non-multiple of ``interval``, or belong to different instruments. Nothing is
    sorted, repaired, or synthesized; callers always receive the original objects.
    """

    resolved_interval = require_positive_interval(interval)
    ordered = tuple(candles)
    previous: Candle | None = None
    for candle in ordered:
        if not isinstance(candle, Candle):
            raise TypeError("market-structure input must consist of Candle instances")
        if previous is not None:
            if (
                candle.exchange,
                candle.symbol,
                candle.timeframe,
            ) != (previous.exchange, previous.symbol, previous.timeframe):
                raise ValueError("all candles in one sequence must share exchange, symbol, and timeframe")
            delta = candle.timestamp - previous.timestamp
            if delta <= timedelta(0):
                raise ValueError("candles must be strictly ordered by ascending open time")
            if delta % resolved_interval != timedelta(0):
                raise ValueError(
                    "consecutive candles must be spaced by a whole multiple of the timeframe interval"
                )
        previous = candle
    return ordered


def candles_closed_by(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
) -> tuple[Candle, ...]:
    """Return the leading candles whose full interval ended at or before ``as_of``.

    A candle opened at ``t`` is only knowable from ``t + interval``; any candle
    that had not fully closed at ``as_of`` is excluded. The input sequence must
    be chronological, so the result is always a prefix (never a re-sorted subset).
    """

    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    closed: list[Candle] = []
    for candle in ordered_candles(candles, interval=resolved_interval):
        if candle.timestamp + resolved_interval <= as_of_utc:
            closed.append(candle)
    return tuple(closed)


def candles_since(candles: Iterable[Candle], *, start: datetime) -> tuple[Candle, ...]:
    """Return the candles whose open time is at or after ``start``."""

    start_utc = require_utc_datetime(start, field_name="start")
    return tuple(candle for candle in candles if candle.timestamp >= start_utc)


def candle_span_count(start: datetime, end: datetime, *, interval: timedelta) -> int:
    """Return how many intervals separate two open times (never negative)."""

    resolved_interval = require_positive_interval(interval)
    delta = require_utc_datetime(end, field_name="end") - require_utc_datetime(start, field_name="start")
    if delta <= timedelta(0):
        return 0
    return int(delta // resolved_interval)
