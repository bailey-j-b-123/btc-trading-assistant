"""Offline synthetic candle fixtures for the chart-geometry detector tests.

**These fixtures are synthetic and offline.** They are built from explicit
numbers written in this file; nothing here contacts an exchange, reads
``data/``, ``backups/``, or any database, or generates a display shape. They are
not real market history and must never be described as such, and no test uses
them to fabricate a trading decision, a plan, or an outcome.

Construction, stated exactly so every expected number in the tests can be
checked by hand
-------------------------------------------------------------------------------
``synthetic_series(pivots, leg=4)`` places pivot ``k`` at candle index
``k * leg`` and interpolates closes linearly between consecutive pivots:

* ``open[i]  = close[i - 1]`` (the first candle opens at the first pivot price);
* ``close[i]`` is the linear interpolation of the pivot prices;
* ordinary candles carry a wick of ``WICK`` on both sides:
  ``high = max(open, close) + WICK``, ``low = min(open, close) - WICK``;
* a candle that *is* a pivot carries ``WICK + PIVOT_BUMP`` on both sides.

The pivot bump is what makes every intended pivot a **strict** local extreme
under the Step 3 strict tie policy: without it, the candle immediately after a
peak would share the peak's high (``max(open, close)`` is the peak price on both
candles) and Step 3 would reject both. With it, the swing prices are exactly

    swing high at a pivot = pivot price + (WICK + PIVOT_BUMP)
    swing low  at a pivot = pivot price - (WICK + PIVOT_BUMP)

which is ``pivot +/- SWING_OFFSET`` — a single documented constant, so a test can
state the exact expected boundary anchor prices in plain arithmetic.

Pivot ``0`` sits at candle index ``0`` and therefore has no left window: it can
never be a confirmed swing. Fixtures use it as a lead-in that creates the first
real pivot, and it is documented as such wherever it appears.

``tail`` rows are appended verbatim as explicit ``(open, high, low, close)``
candles after the pivot series, so a test can place an exact breakout, an exact
invalidation, or an exact do-nothing candle at a known index.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_assistant.chart_geometry import (
    ChartGeometryFamily,
    ChartGeometryParameters,
    ChartGeometrySnapshot,
    detect_chart_geometry,
)
from trading_assistant.market_data.types import Candle

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
INTERVAL = timedelta(hours=1)
EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

#: Ordinary-candle wick, in price units.
WICK = Decimal("0.05")
#: Extra wick on a pivot candle, so the pivot is a strict local extreme.
PIVOT_BUMP = Decimal("0.05")
#: Exact distance between a pivot price and its confirmed swing price.
SWING_OFFSET = WICK + PIVOT_BUMP

#: Default leg length in candles between two pivots. Four candles keep the
#: Step 3 default 2/2 swing windows of neighbouring pivots disjoint.
LEG = 4

#: Default swing windows used by the detectors under test (Step 3 defaults).
SWING_LEFT = 2
SWING_RIGHT = 2


def open_time(index: int, *, start: datetime = EPOCH, interval: timedelta = INTERVAL) -> datetime:
    """Return the candle open time at a fixture index."""

    return start + interval * index


def close_time(index: int, *, start: datetime = EPOCH, interval: timedelta = INTERVAL) -> datetime:
    """Return the instant the candle at a fixture index has fully closed."""

    return start + interval * (index + 1)


def _candle(
    index: int,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    *,
    start: datetime = EPOCH,
    interval: timedelta = INTERVAL,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> Candle:
    return Candle(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=timeframe,
        timestamp=start + interval * index,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=Decimal(volume),
    )


def synthetic_series(
    pivots: Sequence[str | Decimal],
    *,
    leg: int = LEG,
    wick: Decimal = WICK,
    pivot_bump: Decimal = PIVOT_BUMP,
    tail: Sequence[Sequence[str | Decimal]] = (),
    start: datetime = EPOCH,
    interval: timedelta = INTERVAL,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build an explicit synthetic OHLCV series from pivot prices.

    ``tail`` rows are ``(open, high, low, close)`` candles appended immediately
    after the last pivot candle, verbatim.
    """

    if len(pivots) < 2:
        raise ValueError("synthetic_series needs at least two pivots")
    prices = [Decimal(str(pivots[0]))]
    pivot_indices = {0}
    for pivot_number in range(1, len(pivots)):
        pivot_indices.add(pivot_number * leg)
        begin = Decimal(str(pivots[pivot_number - 1]))
        end = Decimal(str(pivots[pivot_number]))
        step = (end - begin) / Decimal(leg)
        prices.extend(begin + step * Decimal(offset) for offset in range(1, leg + 1))

    candles: list[Candle] = []
    previous_close = prices[0]
    for index, close in enumerate(prices):
        body_wick = wick + pivot_bump if index in pivot_indices else wick
        candles.append(
            _candle(
                index,
                previous_close,
                max(previous_close, close) + body_wick,
                min(previous_close, close) - body_wick,
                close,
                start=start,
                interval=interval,
                volume=volume,
                timeframe=timeframe,
            )
        )
        previous_close = close

    for offset, row in enumerate(tail):
        if len(row) != 4:
            raise ValueError("each tail row must be exactly (open, high, low, close)")
        index = len(prices) + offset
        open_, high, low, close = (Decimal(str(value)) for value in row)
        candles.append(
            _candle(
                index,
                open_,
                high,
                low,
                close,
                start=start,
                interval=interval,
                volume=volume,
                timeframe=timeframe,
            )
        )
    return tuple(candles)


def last_close_time(candles: Sequence[Candle], interval: timedelta = INTERVAL) -> datetime:
    """Return the instant the final candle in a series has closed."""

    return candles[-1].timestamp + interval


def detect(
    candles: Sequence[Candle],
    *,
    as_of: datetime | None = None,
    parameters: ChartGeometryParameters | None = None,
    exchange: str = EXCHANGE,
    symbol: str = SYMBOL,
    timeframe: str = TIMEFRAME,
) -> ChartGeometrySnapshot:
    """Run the detector over one synthetic series with the fixture instrument."""

    return detect_chart_geometry(
        candles,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of if as_of is not None else last_close_time(candles, INTERVAL),
        parameters=parameters,
    )


def families_of(snapshot: ChartGeometrySnapshot) -> tuple[ChartGeometryFamily, ...]:
    """Return the distinct families a snapshot describes."""

    return tuple(sorted({formation.family for formation in snapshot.formations}, key=str))


def with_gap(
    candles: Sequence[Candle],
    *,
    drop_index: int,
    interval: timedelta = INTERVAL,
) -> tuple[Candle, ...]:
    """Return a copy of a series with one candle removed, creating a real gap.

    The remaining candles keep their original open times, so the hole is genuine
    missing history rather than a re-spaced series.
    """

    return tuple(candle for index, candle in enumerate(candles) if index != drop_index)


def replacing(
    candles: Sequence[Candle], index: int, *, close: str, high: str | None = None, low: str | None = None
) -> tuple[Candle, ...]:
    """Return a copy of a series with one candle's close (and optionally high/low) replaced."""

    from dataclasses import replace

    result = list(candles)
    target = result[index]
    new_close = Decimal(close)
    result[index] = replace(
        target,
        close=new_close,
        high=Decimal(high) if high is not None else max(target.high, new_close),
        low=Decimal(low) if low is not None else min(target.low, new_close),
    )
    return tuple(result)
