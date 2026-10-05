"""Potential sweeps are OHLC evidence, not evidence of actual resting orders."""

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.numeric import percentage_of, tolerance_band
from trading_assistant.pattern_liquidity.breakouts import (
    atr_ratio,
    boundary,
    signed_distance,
)
from trading_assistant.pattern_liquidity.events import (
    Direction,
    Reference,
    Sweep,
    identity,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def detect_sweep(
    reference: Reference,
    direction: Direction,
    previous: Candle,
    candle: Candle,
    context: TimeframeStructureAnalysis,
    parameters: PatternLiquidityParameters,
) -> Sweep | None:
    if reference.known_at > candle.timestamp:
        return None
    if signed_distance(previous.close, reference, direction) > 0:
        return None
    level = boundary(reference, direction)
    extreme = candle.high if direction == "bullish" else candle.low
    penetration = signed_distance(extreme, reference, direction)
    reclaim = signed_distance(candle.close, reference, direction)
    if penetration <= tolerance_band(level, parameters.sweep_penetration_pct):
        return None
    if reclaim > -tolerance_band(level, parameters.sweep_reclaim_pct):
        return None
    side = "above" if direction == "bullish" else "below"
    return Sweep(
        identity(reference.id, "sweep", side, candle.timestamp),
        side,
        reference,
        previous,
        candle,
        context.as_of,
        extreme,
        penetration,
        percentage_of(penetration, level),
        atr_ratio(penetration, context.volatility),
        candle.close,
        context.volatility,
        context.volume,
        parameters,
    )
