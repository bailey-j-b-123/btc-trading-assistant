"""Validated Step 6 planning rules and canonical versioned configuration identity.

Every knob the planner actually consults lives in frozen ``PlanningParameters``.
Defaults are explicit, uncalibrated choices: they must not be read as validated
strategy parameters. The configuration fingerprint (plus the Step 5 source
fingerprint it records) is part of every plan identity, so changing any planning
setting intentionally changes plan identities rather than silently reusing them.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.snapshot import to_jsonable

PLANNING_RULES_VERSION = "trade-planning-v1"

#: Target lists longer than this add no planning value; keep identities bounded.
MAX_TARGETS_LIMIT = 10


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


class EntryMode(StrEnum):
    """Which as-of-known price anchors the proposed entry.

    ``PLAN_CLOSE``
        The latest closed candle close at the planning frame, i.e. the actual
        market print when the plan is generated. No fill or better price is
        assumed or searched for.
    ``FROZEN_CONFIRMATION``
        The family-specific frozen confirmation close already recorded by
        Steps 4–5 (held-retest close, reversal confirmation breakout close, or
        seed reclaim/re-entry close).
    """

    PLAN_CLOSE = "plan_close"
    FROZEN_CONFIRMATION = "frozen_confirmation"


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
    """Every rule input the Step 6 planner consults. Nothing else is tunable."""

    entry_mode: EntryMode = EntryMode.PLAN_CLOSE
    stop_buffer_mode: StopBufferMode = StopBufferMode.NONE
    stop_buffer_atr_multiple: Decimal = Decimal(1)
    stop_buffer_percentage: Decimal = Decimal("0.1")
    max_structural_targets: int = 2
    include_equal_levels_as_targets: bool = True
    r_multiple_fallbacks: tuple[Decimal, ...] = (Decimal(2),)
    min_r_multiple: Decimal | None = None

    def __post_init__(self) -> None:
        for name, enum_type in (
            ("entry_mode", EntryMode),
            ("stop_buffer_mode", StopBufferMode),
        ):
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
        if not isinstance(self.r_multiple_fallbacks, tuple):
            raise TypeError("r_multiple_fallbacks must be an immutable tuple")
        multiples = []
        for index, raw in enumerate(self.r_multiple_fallbacks):
            value = as_decimal(raw, name=f"r_multiple_fallbacks[{index}]")
            if value <= 0:
                raise ValueError("r_multiple_fallbacks entries must be positive")
            multiples.append(canonical_decimal(value))
        if len(set(multiples)) != len(multiples):
            raise ValueError("r_multiple_fallbacks must be unique after normalization")
        # Canonical order: ascending R, so targets and identity never depend on
        # the spelling order supplied by a caller.
        object.__setattr__(self, "r_multiple_fallbacks", tuple(sorted(multiples)))
        if self.min_r_multiple is not None:
            minimum = as_decimal(self.min_r_multiple, name="min_r_multiple")
            if minimum <= 0:
                raise ValueError("min_r_multiple must be positive or None")
            object.__setattr__(self, "min_r_multiple", canonical_decimal(minimum))

    def fingerprint(self) -> str:
        """Versioned identity of this exact configuration."""
        return fingerprint(self)
