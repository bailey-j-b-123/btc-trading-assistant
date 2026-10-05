"""Close-only breakout rules against a frozen pre-existing reference."""

from decimal import Decimal

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.numeric import (
    divide,
    percentage_of,
    tolerance_band,
)
from trading_assistant.market_structure.volatility import VolatilityContext
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    Direction,
    Reference,
    identity,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def boundary(reference: Reference, direction: Direction) -> Decimal:
    return reference.band_high if direction == "bullish" else reference.band_low


def signed_distance(
    price: Decimal, reference: Reference, direction: Direction
) -> Decimal:
    level = boundary(reference, direction)
    return price - level if direction == "bullish" else level - price


def atr_ratio(distance: Decimal, volatility: VolatilityContext) -> Decimal | None:
    return divide(distance, volatility.atr) if volatility.atr else None


def detect_breakout(
    reference: Reference,
    direction: Direction,
    previous: Candle,
    candles: tuple[Candle, ...],
    context: TimeframeStructureAnalysis,
    parameters: PatternLiquidityParameters,
) -> Breakout | None:
    """Replay bounds candles/context by as_of; reference must exist at first open."""
    if len(candles) != parameters.breakout_confirmation_candles:
        return None
    first, last = candles[0], candles[-1]
    if reference.known_at > first.timestamp:
        return None
    # Require a fresh close crossing, not an already-outside newly discovered level.
    if signed_distance(previous.close, reference, direction) > 0:
        return None
    level = boundary(reference, direction)
    tolerance = tolerance_band(level, parameters.breakout_tolerance_pct)
    if not all(
        signed_distance(c.close, reference, direction) > tolerance for c in candles
    ):
        return None
    distance = signed_distance(last.close, reference, direction)
    return Breakout(
        identity(reference.id, direction, first.timestamp),
        direction,
        reference,
        previous,
        first,
        context.as_of,
        candles,
        last.close,
        distance,
        percentage_of(distance, level) if level else Decimal(0),
        atr_ratio(distance, context.volatility),
        context.volatility,
        context.volume,
        parameters,
    )
