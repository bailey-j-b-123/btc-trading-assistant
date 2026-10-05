"""Later closed-candle re-entry, never a revision to the original breakout."""

from datetime import timedelta
from decimal import Decimal

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.numeric import tolerance_band
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    FailedBreakout,
    identity,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def reentry_distance(breakout: Breakout, candle: Candle) -> Decimal:
    ref = breakout.reference
    return (
        ref.band_low - candle.close
        if breakout.direction == "bullish"
        else candle.close - ref.band_high
    )


def detect_failure(
    breakout: Breakout,
    candles: tuple[Candle, ...],
    interval: timedelta,
    parameters: PatternLiquidityParameters,
) -> FailedBreakout | None:
    candle = candles[-1]
    elapsed = int((candle.timestamp + interval - breakout.known_at) // interval)
    if (
        candle.timestamp < breakout.known_at
        or not 1 <= elapsed <= parameters.failure_window_candles
    ):
        return None
    level = (
        breakout.reference.band_low
        if breakout.direction == "bullish"
        else breakout.reference.band_high
    )
    distance = reentry_distance(breakout, candle)
    if distance <= tolerance_band(level, parameters.failure_reentry_pct):
        return None
    return FailedBreakout(
        identity(breakout.id, "failed"),
        breakout,
        candle,
        candle.timestamp + interval,
        distance,
        elapsed,
        int((candle.timestamp + interval - breakout.known_at).total_seconds()),
        candles,
        parameters,
    )
