"""Small, explicit alternating-swing geometries with horizontal necklines only."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.numeric import (
    mean,
    percentage_of,
    tolerance_band,
)
from trading_assistant.market_structure.swings import SwingKind
from trading_assistant.pattern_liquidity.events import (
    ChartPattern,
    PatternGeometry,
    identity,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def detect_shapes(
    context: TimeframeStructureAnalysis,
    instrument: tuple[str, str, str],
    interval: timedelta,
    parameters: PatternLiquidityParameters,
) -> tuple[ChartPattern, ...]:
    swings = context.confirmed_swings
    result = []
    for size in (3, 5):
        # Only consecutive confirmed swings qualify. Never synthesize a ZigZag.
        for offset in range(len(swings) - size + 1):
            points = swings[offset : offset + size]
            if any(
                a.kind == b.kind or a.timestamp >= b.timestamp
                for a, b in pairwise(points)
            ):
                continue
            span = int((points[-1].timestamp - points[0].timestamp) // interval)
            if span > parameters.pattern_max_span_candles:
                continue
            top = points[0].kind == SwingKind.HIGH
            peaks, valleys = points[::2], points[1::2]
            # Mirror bottoms into the same distance rules without changing evidence prices.
            sign = Decimal(1) if top else Decimal(-1)
            shoulders = (peaks[0].price, peaks[-1].price)
            mismatch = percentage_of(abs(shoulders[0] - shoulders[1]), shoulders[0])
            if mismatch > parameters.pattern_tolerance_pct:
                continue
            neckline = mean([s.price for s in valleys])
            neck_difference = percentage_of(
                max(s.price for s in valleys) - min(s.price for s in valleys), neckline
            )
            if neck_difference > parameters.pattern_tolerance_pct:
                continue
            depth = min(
                sign * (peak.price - valley.price)
                for peak in peaks
                for valley in valleys
            )
            depth_pct = percentage_of(depth, neckline)
            if depth_pct < parameters.pattern_min_depth_pct:
                continue
            prominence = None
            if size == 5:
                prominence = percentage_of(
                    min(sign * (peaks[1].price - s) for s in shoulders),
                    mean(list(shoulders)),
                )
                if prominence < parameters.head_min_prominence_pct:
                    continue
            kind = (
                ("double_top" if top else "double_bottom")
                if size == 3
                else ("head_and_shoulders" if top else "inverse_head_and_shoulders")
            )
            formed_at = max(s.confirmed_at for s in points)
            pattern_id = identity(instrument, kind, points)
            invalidation = (
                max(s.price for s in peaks) if top else min(s.price for s in peaks)
            )
            result.append(
                ChartPattern(
                    identity(pattern_id, "formed"),
                    pattern_id,
                    kind,
                    "formed",
                    points,
                    neckline,
                    invalidation,
                    PatternGeometry(
                        mismatch, depth_pct, prominence, neck_difference, span
                    ),
                    points[-1].timestamp,
                    formed_at,
                    formed_at,
                    None,
                    (),
                    parameters,
                )
            )
    return tuple(result)


def advance_pattern(
    pattern: ChartPattern,
    candles: tuple[Candle, ...],
    interval: timedelta,
    parameters: PatternLiquidityParameters,
) -> ChartPattern | None:
    candle = candles[-1]
    if candle.timestamp < pattern.formed_at:
        return None
    top = pattern.type in ("double_top", "head_and_shoulders")
    sign = Decimal(1) if top else Decimal(-1)
    state = None
    if sign * (candle.close - pattern.invalidation_level) > tolerance_band(
        pattern.invalidation_level, parameters.pattern_invalidation_pct
    ):
        state = "invalidated"
    elif sign * (pattern.neckline - candle.close) > tolerance_band(
        pattern.neckline, parameters.neckline_tolerance_pct
    ):
        state = "confirmed"
    if state is None:
        return None
    return replace(
        pattern,
        id=identity(pattern.pattern_id, state),
        state=state,
        known_at=candle.timestamp + interval,
        confirmation_timestamp=candle.timestamp + interval
        if state == "confirmed"
        else None,
        evidence_candles=pattern.evidence_candles + candles,
    )
