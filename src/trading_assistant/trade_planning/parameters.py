"""Validated Step 6 planning rules and canonical versioned configuration identity.

Every knob the planner actually consults lives in frozen ``PlanningParameters``.
Defaults are explicit, uncalibrated choices: they must not be read as validated
strategy parameters. The configuration fingerprint (plus the Step 5 source
fingerprint it records) is part of every plan identity, so changing any planning
setting intentionally changes plan identities rather than silently reusing them.

The reward-to-risk floor is policy, not preference:

* ``min_r_multiple`` defaults to 1 and can never be configured below 1, so a
  plan whose genuine structural targets offer less than 1R can never become
  actionable. A stricter value only tightens the same gate.
* ``preferred_r_multiple`` (default 1.5) classifies a plan whose nearest
  retained structural target already reaches the preferred distance. It never
  blocks anything: a plan at exactly 1R is fully actionable.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.snapshot import to_jsonable

PLANNING_RULES_VERSION = "trade-planning-v2"

#: Target lists longer than this add no planning value; keep identities bounded.
MAX_TARGETS_LIMIT = 10

#: The mandatory reward-to-risk floor. It is a policy constant, not a tuneable
#: preference: no configuration may plan a trade below this multiple.
MINIMUM_R_MULTIPLE_FLOOR = Decimal(1)


def fingerprint(*parts: object) -> str:
    """Canonical SHA-256 over JSON-safe planning material, version-prefixed."""
    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((PLANNING_RULES_VERSION + ":" + payload).encode()).hexdigest()


def decimal_text(value: Decimal | None) -> str:
    """Render a Decimal exactly like snapshot JSON projections do, or ``"?"``."""
    return "?" if value is None else format(value, "f")


def canonical_decimal(value: Decimal) -> Decimal:
    """Normalize equivalent decimal spellings without ambient-context rounding."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
        value = Decimal(text) if text not in ("", "-") else Decimal(0)
    return value


class StopBufferMode(StrEnum):
    """Deterministic offset between logical invalidation and the proposed stop.

    ``NONE``
        The stop equals the logical invalidation level; no buffer is invented.
    ``ATR``
        Buffer = ``quantize_derived(atr * stop_buffer_atr_multiple)`` using
        the Step 3 ATR already present at the planning as-of.
    ``PERCENTAGE``
        Buffer = ``tolerance_band(invalidation, stop_buffer_percentage)`` from
        the existing Step 3 percentage-tolerance helper.
    """

    NONE = "none"
    ATR = "atr"
    PERCENTAGE = "percentage"


@dataclass(frozen=True, slots=True)
class PlanningParameters:
    """Every rule input the Step 6 planner consults. Nothing else is tunable.

    Entry is fixed policy: the actual decision-time price (the latest closed
    candle's close at the planning ``as_of``). No older or better price is ever
    substituted, so a setup whose move already happened is measured against the
    price that is really available now.
    """

    stop_buffer_mode: StopBufferMode = StopBufferMode.NONE
    stop_buffer_atr_multiple: Decimal = Decimal(1)
    stop_buffer_percentage: Decimal = Decimal("0.1")
    max_structural_targets: int = 2
    include_equal_levels_as_targets: bool = True
    min_r_multiple: Decimal = MINIMUM_R_MULTIPLE_FLOOR
    preferred_r_multiple: Decimal = Decimal("1.5")

    def __post_init__(self) -> None:
        for name, enum_type in (("stop_buffer_mode", StopBufferMode),):
            current = getattr(self, name)
            if not isinstance(current, enum_type):
                try:
                    current = enum_type(current)
                except ValueError as exc:
                    raise ValueError(
                        f"{name} must be one of: "
                        + ", ".join(item.value for item in enum_type)
                    ) from exc
                object.__setattr__(self, name, current)
        atr_multiple = as_decimal(
            self.stop_buffer_atr_multiple, name="stop_buffer_atr_multiple"
        )
        if atr_multiple <= 0:
            raise ValueError("stop_buffer_atr_multiple must be positive")
        object.__setattr__(
            self, "stop_buffer_atr_multiple", canonical_decimal(atr_multiple)
        )
        percentage = as_decimal(
            self.stop_buffer_percentage, name="stop_buffer_percentage"
        )
        if not Decimal(0) < percentage < Decimal(100):
            raise ValueError(
                "stop_buffer_percentage must be greater than 0 and less than 100"
            )
        object.__setattr__(
            self, "stop_buffer_percentage", canonical_decimal(percentage)
        )
        require_int(
            self.max_structural_targets,
            name="max_structural_targets",
            minimum=1,
            maximum=MAX_TARGETS_LIMIT,
        )
        if type(self.include_equal_levels_as_targets) is not bool:
            raise TypeError("include_equal_levels_as_targets must be boolean")
        minimum = as_decimal(self.min_r_multiple, name="min_r_multiple")
        if minimum < MINIMUM_R_MULTIPLE_FLOOR:
            raise ValueError(
                "min_r_multiple must be >= 1: the mandatory reward-to-risk "
                "floor cannot be configured away (a stricter value is allowed)"
            )
        object.__setattr__(self, "min_r_multiple", canonical_decimal(minimum))
        preferred = as_decimal(
            self.preferred_r_multiple, name="preferred_r_multiple"
        )
        if preferred < MINIMUM_R_MULTIPLE_FLOOR:
            raise ValueError(
                "preferred_r_multiple must be >= 1; it classifies plans and "
                "never blocks them, and it is independent of a stricter "
                "min_r_multiple floor"
            )
        object.__setattr__(
            self, "preferred_r_multiple", canonical_decimal(preferred)
        )

    def fingerprint(self) -> str:
        """Versioned identity of this exact configuration."""
        return fingerprint(self)
