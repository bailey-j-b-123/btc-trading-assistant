"""Deterministic synthetic candle fixtures for market-structure tests.

Nothing in this module contacts an exchange or reads project data; every candle
is generated from explicit numbers so tests are reproducible and reviewable.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.swings import (
    SwingKind,
    SwingPoint,
    SwingTiePolicy,
)

EPOCH = datetime(2024, 1, 1, tzinfo=UTC)
INTERVAL = timedelta(hours=1)
EXCHANGE = "mock-exchange"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

#: Wick size decays with candle index. A strictly decreasing wick guarantees
#: that every generated pivot is a strict local extreme (no accidental ties),
#: which keeps the swing fixtures unambiguous.
WICK_BASE = Decimal("0.5")
WICK_DECAY = Decimal("0.005")


def wick_for(index: int) -> Decimal:
    wick = WICK_BASE - Decimal(index) * WICK_DECAY
    if wick <= 0:
        raise ValueError("fixture wick became non-positive; shorten the fixture")
    return wick


def candle_at(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> Candle:
    """Build one candle at a whole-hour offset from the fixture epoch."""

    return Candle(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=timeframe,
        timestamp=EPOCH + INTERVAL * index,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def candles_from_rows(
    rows: Sequence[tuple[str, str, str, str]],
    *,
    volumes: Sequence[str] | None = None,
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build a candle series from explicit ``(open, high, low, close)`` rows."""

    return tuple(
        candle_at(
            index,
            open_=row[0],
            high=row[1],
            low=row[2],
            close=row[3],
            volume=volumes[index] if volumes is not None else "10",
            timeframe=timeframe,
        )
        for index, row in enumerate(rows)
    )


def zigzag_candles_at(
    pivots: Sequence[str],
    *,
    leg: int = 5,
    step: timedelta = INTERVAL,
    timeframe: str = TIMEFRAME,
    start: datetime = EPOCH,
    volume: str = "10",
) -> tuple[Candle, ...]:
    """Build a zigzag candle series with an explicit candle step and timeframe.

    Pivot ``k`` sits at index ``k * leg``. Prices are linearly interpolated
    between pivots, so each pivot is a strict local extreme of the generated
    high/low series under the default strict tie policy.
    """

    if len(pivots) < 2:
        raise ValueError("zigzag_candles requires at least two pivots")
    prices = [Decimal(str(pivots[0]))]
    for leg_start, leg_end in pairwise(pivots):
        start_value = Decimal(str(leg_start))
        end_value = Decimal(str(leg_end))
        step_size = (end_value - start_value) / Decimal(leg)
        prices.extend(start_value + step_size * Decimal(step) for step in range(1, leg + 1))

    candles: list[Candle] = []
    previous_close = prices[0]
    for index, price in enumerate(prices):
        wick = wick_for(index)
        candles.append(
            Candle(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe=timeframe,
                timestamp=start + step * index,
                open=previous_close,
                high=max(previous_close, price) + wick,
                low=min(previous_close, price) - wick,
                close=price,
                volume=Decimal(volume),
            )
        )
        previous_close = price
    return tuple(candles)


def zigzag_candles(
    pivots: Sequence[str],
    *,
    leg: int = 5,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Hourly zigzag candles starting at the fixture epoch."""

    return zigzag_candles_at(pivots, leg=leg, volume=volume, timeframe=timeframe)


def flat_candles(
    count: int,
    *,
    start_index: int,
    price: str,
    volume: str = "10",
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build candles that never create a swing (all highs/lows are equal)."""

    value = Decimal(price)
    return tuple(
        candle_at(
            start_index + offset,
            open_=price,
            high=str(value + Decimal("0.2")),
            low=str(value - Decimal("0.2")),
            close=price,
            volume=volume,
            timeframe=timeframe,
        )
        for offset in range(count)
    )


def constant_volume_candles(
    closes: Sequence[str],
    *,
    volumes: Sequence[str] | None = None,
    wick: str = "1",
    open_offset: str = "0",
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build simple candles where only close and volume are meaningful."""

    candles: list[Candle] = []
    previous_close = Decimal(closes[0]) - Decimal(open_offset)
    wick_value = Decimal(wick)
    for index, close_text in enumerate(closes):
        close_value = Decimal(close_text)
        open_value = previous_close
        candles.append(
            Candle(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe=timeframe,
                timestamp=EPOCH + INTERVAL * index,
                open=open_value,
                high=max(open_value, close_value) + wick_value,
                low=min(open_value, close_value) - wick_value,
                close=close_value,
                volume=Decimal(volumes[index]) if volumes is not None else Decimal(10),
            )
        )
        previous_close = close_value
    return tuple(candles)


def analytic_candles(
    rows: Sequence[tuple[str, str, str, str, str]],
    *,
    timeframe: str = TIMEFRAME,
) -> tuple[Candle, ...]:
    """Build candles from explicit ``(open, high, low, close, volume)`` rows."""

    return tuple(
        candle_at(
            index,
            open_=row[0],
            high=row[1],
            low=row[2],
            close=row[3],
            volume=row[4],
            timeframe=timeframe,
        )
        for index, row in enumerate(rows)
    )


def sample_swing(
    kind: SwingKind,
    index: int,
    price: str,
    *,
    left_window: int = 2,
    right_window: int = 2,
) -> SwingPoint:
    """Build a confirmed swing point directly for component-level tests."""

    timestamp = EPOCH + INTERVAL * index
    confirmed_by = timestamp + INTERVAL * right_window
    return SwingPoint(
        kind=kind,
        timestamp=timestamp,
        price=Decimal(price),
        confirmed_at=confirmed_by + INTERVAL,
        confirmed_by_timestamp=confirmed_by,
        left_window=left_window,
        right_window=right_window,
        tie_policy=SwingTiePolicy.STRICT,
    )
