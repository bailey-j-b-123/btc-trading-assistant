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

The shared cost model below is the single definition of "cost-adjusted R":
adverse entry/exit slippage move the fill in the adverse direction, the two
sided fee is charged on both fills, and the result is normalised by the
proposed unit risk. Step 6's economic-viability rule and Steps 11/12's
friction-adjusted R metrics all call into this one implementation.
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


def friction_adjusted_r_from_levels(
    *,
    entry: Decimal,
    exit_level: Decimal,
    risk_per_unit: Decimal,
    direction: str,
    assumptions: FrictionAssumptions,
) -> Decimal:
    """Cost-adjusted R of one proposed round trip, by the shared cost model.

    The exit fill is the declared adverse slippage applied to the proposed
    terminal level; the entry fill is the declared adverse slippage applied to
    the proposed entry; the two-sided fee is charged on both fills. The result
    is ``quantize_derived((gross - fees) / risk_per_unit)`` — the same
    normalisation the raw (cost-free) R multiple uses, so a cost-adjusted R of
    1 means "the target still covers the full proposed risk *after* costs".

    This is a scenario calculation over proposed levels only. It models no
    size, no account, no leverage, no margin, no fill probability and no
    realised profit.
    """

    scale = Decimal(10_000)
    entry_slippage = assumptions.entry_slippage_bps / scale
    exit_slippage = assumptions.exit_slippage_bps / scale
    fee_rate = assumptions.fee_bps / scale
    if direction == "bullish":
        fill_entry = entry * (Decimal(1) + entry_slippage)
        fill_exit = exit_level * (Decimal(1) - exit_slippage)
        gross = fill_exit - fill_entry
    else:
        fill_entry = entry * (Decimal(1) - entry_slippage)
        fill_exit = exit_level * (Decimal(1) + exit_slippage)
        gross = fill_entry - fill_exit
    fees = (fill_entry + fill_exit) * fee_rate
    return quantize_derived((gross - fees) / risk_per_unit)


__all__ = [
    "FRICTION_ASSUMPTIONS_VERSION",
    "FrictionAssumptions",
    "friction_adjusted_r_from_levels",
    "fingerprint",
]
