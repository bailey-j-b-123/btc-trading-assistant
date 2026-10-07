"""Deterministic volatility context from closed candles.

True Range and Average True Range (ATR) are computed with exact decimal
arithmetic. ATR uses Wilder's smoothing (a recursive moving average with
``period`` as the weight denominator). When the requested lookback is not fully
available the result is reported as unavailable with the required and available
candle counts; the lookback is never silently shortened.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import (
    candles_closed_by,
    require_positive_interval,
)
from trading_assistant.market_structure.numeric import (
    divide,
    mean,
    percentage_of,
    quantize_derived,
    require_int,
)

#: Documented smoothing method identifier reported in every volatility result.
ATR_SMOOTHING = "wilder_rma"

#: Reason reported when fewer than ``period + 1`` closed candles are available.
INSUFFICIENT_CANDLES = "insufficient_candles"


@dataclass(frozen=True, slots=True)
class VolatilityParameters:
    """Lookback used for the Average True Range."""

    period: int = 14

    def __post_init__(self) -> None:
        require_int(self.period, name="period", minimum=1)


@dataclass(frozen=True, slots=True)
class VolatilityContext:
    """True Range and ATR context, including an explicit insufficiency state."""

    available: bool
    reason: str | None
    period: int
    smoothing: str
    candle_count: int
    required_candle_count: int
    latest_candle_timestamp: datetime | None
    latest_close: Decimal | None
    latest_true_range: Decimal | None
    atr: Decimal | None
    atr_percent_of_price: Decimal | None

    @property
    def sufficient(self) -> bool:
        """Alias for :attr:`available`."""

        return self.available

    @property
    def true_range_count(self) -> int:
        """Number of True Range values implied by the available candles."""

        return max(0, self.candle_count - 1)


def true_range(candle: Candle, previous_close: Decimal) -> Decimal:
    """Return the True Range of one candle given the previous closed close."""

    high_low = candle.high - candle.low
    high_previous = abs(candle.high - previous_close)
    low_previous = abs(candle.low - previous_close)
    return max(high_low, high_previous, low_previous)


def wilder_average(values: tuple[Decimal, ...], *, period: int) -> Decimal:
    """Return Wilder's smoothed average of a value series.

    The first average is the simple mean of the first ``period`` values; each
    later value updates it as ``previous + (value - previous) / period``.
    """

    require_int(period, name="period", minimum=1)
    if len(values) < period:
        raise ValueError("wilder_average requires at least `period` values")
    smoothed = mean(list(values[:period]))
    for value in values[period:]:
        smoothed = quantize_derived(smoothed + divide(value - smoothed, Decimal(period)))
    return smoothed


def calculate_volatility(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: VolatilityParameters | None = None,
) -> VolatilityContext:
    """Calculate True Range and Wilder ATR from candles closed by ``as_of``."""

    resolved = parameters if parameters is not None else VolatilityParameters()
    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = candles_closed_by(candles, interval=resolved_interval, as_of=as_of_utc)
    required = resolved.period + 1

    if not ordered:
        return VolatilityContext(
            available=False,
            reason=INSUFFICIENT_CANDLES,
            period=resolved.period,
            smoothing=ATR_SMOOTHING,
            candle_count=0,
            required_candle_count=required,
            latest_candle_timestamp=None,
            latest_close=None,
            latest_true_range=None,
            atr=None,
            atr_percent_of_price=None,
        )

    ranges: list[Decimal] = []
    for previous, current in pairwise(ordered):
        ranges.append(true_range(current, previous.close))
    latest_true_range = ranges[-1] if ranges else None
    latest_close = ordered[-1].close

    if len(ordered) < required:
        return VolatilityContext(
            available=False,
            reason=INSUFFICIENT_CANDLES,
            period=resolved.period,
            smoothing=ATR_SMOOTHING,
            candle_count=len(ordered),
            required_candle_count=required,
            latest_candle_timestamp=ordered[-1].timestamp,
            latest_close=latest_close,
            latest_true_range=latest_true_range,
            atr=None,
            atr_percent_of_price=None,
        )

    atr = wilder_average(tuple(ranges), period=resolved.period)
    return VolatilityContext(
        available=True,
        reason=None,
        period=resolved.period,
        smoothing=ATR_SMOOTHING,
        candle_count=len(ordered),
        required_candle_count=required,
        latest_candle_timestamp=ordered[-1].timestamp,
        latest_close=latest_close,
        latest_true_range=latest_true_range,
        atr=atr,
        atr_percent_of_price=percentage_of(atr, latest_close),
    )
