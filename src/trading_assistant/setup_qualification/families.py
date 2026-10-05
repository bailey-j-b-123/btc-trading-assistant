"""The three candidate definitions; routing and lifecycle are explicit, not scored."""

from dataclasses import dataclass

from trading_assistant.pattern_liquidity.events import (
    Breakout,
    Direction,
    FailedBreakout,
    Reference,
    Sweep,
)
from trading_assistant.setup_qualification.models import SetupFamily

Seed = Breakout | FailedBreakout | Sweep


@dataclass(frozen=True, slots=True)
class FamilyDefinition:
    family: SetupFamily
    expiry_parameter: str
    confirmation_rule: str


FAMILIES = (
    FamilyDefinition(
        SetupFamily.BREAKOUT_RETEST, "continuation_max_bars", "held_retest"
    ),
    FamilyDefinition(
        SetupFamily.LIQUIDITY_REVERSAL, "reversal_max_bars", "reversal_breakout"
    ),
    FamilyDefinition(
        SetupFamily.RANGE_REVERSAL, "range_max_bars", "range_followthrough"
    ),
)


def reference_for(seed: Seed) -> Reference:
    return (
        seed.breakout.reference if isinstance(seed, FailedBreakout) else seed.reference
    )


def family_for(seed: Seed) -> FamilyDefinition:
    if isinstance(seed, Breakout):
        return FAMILIES[0]
    if reference_for(seed).type in ("range_high", "range_low"):
        return FAMILIES[2]
    return FAMILIES[1]


def direction_for(seed: Seed) -> Direction:
    if isinstance(seed, Breakout):
        return seed.direction
    if isinstance(seed, FailedBreakout):
        return "bearish" if seed.breakout.direction == "bullish" else "bullish"
    return "bearish" if seed.direction == "above" else "bullish"
