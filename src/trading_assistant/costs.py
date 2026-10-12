"""Explicit hypothetical cost scenarios shared by Steps 6, 11, and 12.

A :class:`FrictionAssumptions` value is a *declared scenario*, never a market
fact: hypothetical per-unit fees and adverse execution costs, stated in basis
points, that the deterministic engines apply to proposed levels for scenario
analysis. It is deliberately limited: no size, no account, no leverage, no
fill probability, no realised profit.

The class originally lived in ``historical_validation.parameters`` (Step 11).
It moved here so that the lower-layer Step 6 planner can consume the very same
scenario type without importing Step 11 (which depends on Step 6). Every
import path that used to work keeps working through the re-export.

The scenario identity is versioned under the historical-validation rules
version it was introduced with, so a scenario — and every paper plan recorded
under it — keeps the exact same fingerprint as before the move: stored forward
history is never re-identified.

The shared cost model is one implementation with two distinct metrics, and
the two must never be conflated:

* ``friction_net_reward_to_risk`` — the TRUE net reward-to-risk used by the
  Step 6 ``economic_viability`` gate: net potential reward after the declared
  fees and adverse execution costs, compared with net potential loss at the
  stop after the same costs. Both sides of the fraction are after costs and
  direction-aware (long and short use the same sign convention, so costs
  always reduce the ratio, never improve it). Invalid directions and a zero
  net stop loss raise ``ValueError`` rather than producing invented values.
* ``friction_adjusted_r_from_levels`` — the HISTORICAL friction-adjusted R
  reported by Steps 11/12: net reward after costs normalised by the original
  (cost-free) unit risk. Its denominator ignores what a stop hit would
  itself cost, so it is strictly more optimistic than the net metric and is
  retained as a reporting quantity only.

Both metrics share the same fill model: adverse entry/exit slippage moves
the fill in the adverse direction, and the two-sided fee is charged on both
fills. Both are scenario calculations over proposed levels only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256

from trading_assistant.market_structure.numeric import as_decimal, quantize_derived
from trading_assistant.market_structure.snapshot import to_jsonable

FRICTION_ASSUMPTIONS_VERSION = "validation-friction-v1"

#: Version prefix of scenario identities. Fixed at the historical-validation
#: rules version that introduced the class so that moving the class into this
#: shared module never changes a recorded ``friction_fingerprint``.
_FRICTION_IDENTITY_VERSION = "historical-validation-v1"


def fingerprint(*parts: object) -> str:
    """Canonical, version-prefixed identity for cost-scenario material."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((_FRICTION_IDENTITY_VERSION + ":" + payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FrictionAssumptions:
    """Explicit hypothetical per-unit friction assumptions in basis points.

    ``fee_bps`` applies at both the proposed entry and the observed terminal
    level. Entry/exit slippage move fills in the adverse direction.  The model
    is deliberately limited to a scenario analysis; it does not assert that an
    order was submitted, filled, sized, leveraged, or profitable.
    """

    version: str = FRICTION_ASSUMPTIONS_VERSION
    fee_bps: Decimal = Decimal(0)
    entry_slippage_bps: Decimal = Decimal(0)
    exit_slippage_bps: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("friction version must be a non-empty string")
        object.__setattr__(self, "version", self.version.strip())
        for name in ("fee_bps", "entry_slippage_bps", "exit_slippage_bps"):
            value = as_decimal(getattr(self, name), name=name)
            if value < 0 or value >= Decimal(10_000):
                raise ValueError(f"{name} must be >= 0 and < 10000 basis points")
            object.__setattr__(self, name, value.normalize() if value else Decimal(0))

    @property
    def is_zero(self) -> bool:
        """True when every component is zero.

        A zero scenario states no costs at all; it is *not* evidence that a
        market has no costs, so a zero scenario can never certify a trade as
        economically viable (see the Step 6 ``economic_viability`` rule).
        """

        return (
            self.fee_bps == 0
            and self.entry_slippage_bps == 0
            and self.exit_slippage_bps == 0
        )

    def fingerprint(self) -> str:
        return fingerprint("friction-assumptions", self)


def _round_trip_fills(
    entry: Decimal, exit_level: Decimal, direction: str, assumptions: FrictionAssumptions
) -> tuple[Decimal, Decimal, Decimal]:
    """Adverse entry fill, adverse exit fill and the two-sided fee on them.

    ``direction`` must be exactly ``"bullish"`` (long) or ``"bearish"``
    (short); any other value raises ``ValueError`` rather than being silently
    treated as one side.
    """

    if direction not in ("bullish", "bearish"):
        raise ValueError(
            f"direction must be 'bullish' or 'bearish', got {direction!r}"
        )
    scale = Decimal(10_000)
    entry_slippage = assumptions.entry_slippage_bps / scale
    exit_slippage = assumptions.exit_slippage_bps / scale
    fee_rate = assumptions.fee_bps / scale
    if direction == "bullish":
        fill_entry = entry * (Decimal(1) + entry_slippage)
        fill_exit = exit_level * (Decimal(1) - exit_slippage)
    else:
        fill_entry = entry * (Decimal(1) - entry_slippage)
        fill_exit = exit_level * (Decimal(1) + exit_slippage)
    fees = (fill_entry + fill_exit) * fee_rate
    return fill_entry, fill_exit, fees


def friction_net_reward(
    *,
    entry: Decimal,
    exit_level: Decimal,
    direction: str,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """Net potential reward, in units, if the trade ends at the proposed level.

    Direction-aware gross P&L minus the two-sided fee, with adverse fills and
    the fee on both fills (the shared cost model): for ``"bullish"`` (long)
    ``fill_exit - fill_entry - fees``; for ``"bearish"`` (short)
    ``fill_entry - fill_exit - fees``. Positive for a level genuinely on the
    profit side of the entry under the declared costs; negative when the
    level is on the wrong side (or costs exceed the gross distance) — an
    honest number, never a sign flipped to look viable. This is the numerator
    of the economic-viability gate: the reward that remains *after* costs,
    before any comparison with what the stop would cost.
    """

    fill_entry, fill_exit, fees = _round_trip_fills(
        entry, exit_level, direction, assumptions
    )
    if direction == "bullish":
        gross = fill_exit - fill_entry
    else:
        gross = fill_entry - fill_exit
    return gross - fees


def friction_net_loss_at_stop(
    *,
    entry: Decimal,
    stop_level: Decimal,
    direction: str,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """Net potential loss, in units, if the trade ends at the proposed stop.

    Direction-aware gross loss plus the two-sided fee, with adverse fills:
    for ``"bullish"`` (long, stop below entry) ``fill_entry - fill_exit +
    fees``; for ``"bearish"`` (short, stop above entry) ``fill_exit -
    fill_entry + fees``. Costs make this strictly larger than the raw unit
    risk for a correctly sided stop: a stop hit pays the same fees and
    adverse execution costs as any other exit. This is the denominator of the
    economic-viability gate.
    """

    fill_entry, fill_exit, fees = _round_trip_fills(
        entry, stop_level, direction, assumptions
    )
    if direction == "bullish":
        gross = fill_entry - fill_exit
    else:
        gross = fill_exit - fill_entry
    return gross + fees


def friction_net_reward_to_risk(
    *,
    entry: Decimal,
    exit_level: Decimal,
    stop_level: Decimal,
    direction: str,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """True net reward-to-risk: net reward after costs over net stop loss.

    ``quantize_derived(net_reward / net_loss)`` where both sides are after
    the declared hypothetical fees and adverse execution costs (see
    :func:`friction_net_reward` and :func:`friction_net_loss_at_stop`) and
    both use the direction-aware sign conventions of those functions, so the
    ratio behaves identically for longs and shorts: positive for a
    profit-side target, and strictly *reduced* by larger declared costs. A
    value of 1 means "the target's net reward after costs covers the stop's
    net loss after costs". This is the Step 6 ``economic_viability`` gate
    metric — deliberately NOT the historical friction-adjusted R below,
    which normalises by the *original* (cost-free) unit risk and therefore
    cannot see that the stop itself would cost more than its raw distance.

    Refuses rather than invents: an invalid ``direction`` raises
    ``ValueError`` (see :func:`_round_trip_fills`), and a zero net stop loss
    (e.g. stop exactly on the entry with a zero-cost scenario) raises
    ``ValueError`` because the ratio is undefined.
    """

    net_reward = friction_net_reward(
        entry=entry, exit_level=exit_level, direction=direction, assumptions=assumptions
    )
    net_loss = friction_net_loss_at_stop(
        entry=entry, stop_level=stop_level, direction=direction, assumptions=assumptions
    )
    if net_loss == 0:
        raise ValueError(
            "net stop loss after the declared costs is zero (the stop sits on "
            "the entry with no cost on either side); the net reward-to-risk "
            "ratio is undefined and is refused rather than invented"
        )
    return quantize_derived(net_reward / net_loss)


def friction_adjusted_r_from_levels(
    *,
    entry: Decimal,
    exit_level: Decimal,
    risk_per_unit: Decimal,
    direction: str,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """Historical friction-adjusted R: net reward normalised by ORIGINAL risk.

    The exit fill is the declared adverse slippage applied to the proposed
    terminal level; the entry fill is the declared adverse slippage applied
    to the proposed entry; the two-sided fee is charged on both fills. The
    result is ``quantize_derived((gross - fees) / risk_per_unit)`` where
    ``risk_per_unit`` is the raw (cost-free) distance between the proposed
    entry and stop.

    This is the historical Step 11/12 report metric, retained unchanged. It
    is NOT the true net reward-to-risk: the denominator ignores the costs a
    stop hit would itself pay, so it overstates viability relative to
    :func:`friction_net_reward_to_risk` (which compares net reward after
    costs with net stop loss after costs). The Step 6 ``economic_viability``
    gate uses the net metric; this one stays a reporting quantity only.

    A scenario calculation over proposed levels only. It models no size, no
    account, no leverage, no margin, no fill probability and no realised
    profit.
    """

    fill_entry, fill_exit, fees = _round_trip_fills(
        entry, exit_level, direction, assumptions
    )
    if direction == "bullish":
        gross = fill_exit - fill_entry
    else:
        gross = fill_entry - fill_exit
    return quantize_derived((gross - fees) / risk_per_unit)


__all__ = [
    "FRICTION_ASSUMPTIONS_VERSION",
    "FrictionAssumptions",
    "friction_adjusted_r_from_levels",
    "friction_net_loss_at_stop",
    "friction_net_reward",
    "friction_net_reward_to_risk",
    "fingerprint",
]
