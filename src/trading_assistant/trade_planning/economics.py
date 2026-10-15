"""Versioned optional research gate; mathematical costs reused from PR46.

No target/stop substitution. A genuine target must cover net stop loss under
the SAME declared scenario. Zero costs are diagnostic, never certification.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal
from trading_assistant.costs import (
    FrictionAssumptions,
    friction_net_loss_at_stop,
    friction_net_reward,
)
from trading_assistant.market_structure.numeric import quantize_derived

ECONOMIC_POLICY_VERSION = "economic-planning-research-v1"


def declared_scenario(friction):
    """Convert the existing recorded scenario without changing its identity."""
    return FrictionAssumptions(**asdict(friction))


@dataclass(frozen=True)
class Economics:
    reward: Decimal
    loss: Decimal
    net_r: Decimal
    passed: bool


def assess(*, entry, stop, target, direction, costs, floor=Decimal(1)):
    if not (
        entry.is_finite()
        and stop.is_finite()
        and target.is_finite()
        and min(entry, stop, target) > 0
        and floor.is_finite()
        and floor >= 1
    ):
        raise ValueError("finite positive levels and mandatory >=1 floor required")
    if direction not in {"bullish", "bearish"}:
        raise ValueError("invalid direction")
    long = direction == "bullish"
    if not (stop < entry < target if long else target < entry < stop):
        raise ValueError("wrong-side stop or target")
    reward = friction_net_reward(
        entry=entry, exit_level=target, direction=direction, assumptions=costs
    )
    loss = friction_net_loss_at_stop(
        entry=entry, stop_level=stop, direction=direction, assumptions=costs
    )
    # Compare exact amounts BEFORE display rounding; 0.999999999 is not 1R.
    return Economics(
        reward,
        loss,
        quantize_derived(reward / loss),
        not costs.is_zero and reward >= floor * loss,
    )
