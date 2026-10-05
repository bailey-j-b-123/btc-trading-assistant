"""First post-breakout band visit; observed and closed-candle outcomes are separate facts."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.numeric import tolerance_band
from trading_assistant.pattern_liquidity.breakouts import boundary, signed_distance
from trading_assistant.pattern_liquidity.events import Breakout, Retest, identity
from trading_assistant.pattern_liquidity.failures import reentry_distance
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def detect_retest(
    breakout: Breakout,
    candles: tuple[Candle, ...],
    interval: timedelta,
    parameters: PatternLiquidityParameters,
    observed: bool,
) -> tuple[Retest, ...]:
    candle = candles[-1]
    elapsed = int((candle.timestamp + interval - breakout.known_at) // interval)
    if (
        candle.timestamp < breakout.known_at
        or not 1 <= elapsed <= parameters.retest_window_candles
    ):
        return ()
    ref = breakout.reference
    level = boundary(ref, breakout.direction)
    tolerance = tolerance_band(level, parameters.retest_tolerance_pct)
    touches = (
        candle.low <= ref.band_high + tolerance
        and candle.high >= ref.band_low - tolerance
    )
    distance = max(ref.band_low - candle.high, candle.low - ref.band_high, Decimal(0))
    states: list[Literal["observed", "held", "failed"]] = []
    if not observed:
        if not touches:
            return ()
        states.append("observed")
    if reentry_distance(breakout, candle) > tolerance_band(
        ref.band_low if breakout.direction == "bullish" else ref.band_high,
        parameters.failure_reentry_pct,
    ):
        states.append("failed")
    elif signed_distance(candle.close, ref, breakout.direction) > tolerance_band(
        level, parameters.retest_hold_pct
    ):
        states.append("held")
    return tuple(
        Retest(
            identity(breakout.id, "retest", state),
            breakout,
            state,
            candle,
            candle.timestamp + interval,
            distance,
            elapsed,
            candles,
            parameters,
        )
        for state in states
    )
