"""Validated, versioned ``chart-geometry-v1`` detector parameters.

Every threshold below is an explicit **definition** of what this package calls a
chart-geometry formation. None of them is a validated probability, a win rate, a
profit expectation, or a tuning result: chart-geometry-v1 makes no claim that a
described geometry resolves in any particular direction or that describing it is
profitable. Changing a value changes the definition, the parameter fingerprint,
and therefore every formation id — it never re-identifies a stored record.

Units
-----
``*_pct``
    Percent values (``Decimal("0.5")`` means one half of one percent), measured
    against the exact base named in the field's documentation. They are never
    fractions and never basis points.
``*_candles``
    Whole candle counts on the detection timeframe. A "candle" is one fixed
    interval of the timeframe given to :func:`detect_chart_geometry`.

Boundary geometry
-----------------
A formation is always bounded by exactly two **lines**, each anchored on two
actual confirmed swing pivots. ``travel_pct`` of a boundary is

    (end_price - start_price) / mean(start_price, end_price) * 100

over its own anchor span, so it is scale-free and sign-carrying: positive means
the boundary rises from its first to its second anchor. ``gap_pct`` between the
two boundaries at a time ``t`` is

    (upper.value_at(t) - lower.value_at(t)) / mean(upper, lower at t) * 100

Both are exact :class:`~decimal.Decimal` computations with the documented
Step 3 rounding (:mod:`trading_assistant.market_structure.numeric`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from decimal import Decimal
from hashlib import sha256

from trading_assistant.market_structure.numeric import as_decimal, require_int
from trading_assistant.market_structure.swings import SwingTiePolicy

#: Rules version pinned into every fingerprint and every identity in this
#: package. A definition change is a new version, never an edit of this one.
CHART_GEOMETRY_RULES_VERSION = "chart-geometry-v1"

_PERCENT_FIELDS = (
    "min_boundary_gap_pct",
    "min_boundary_travel_pct",
    "flat_boundary_max_travel_pct",
    "breakout_tolerance_pct",
    "pole_min_change_pct",
    "flag_min_retrace_pct",
    "flag_max_retrace_pct",
    "cup_min_depth_pct",
    "cup_rim_tolerance_pct",
    "cup_trough_base_tolerance_pct",
    "cup_min_interior_prominence_pct",
    "handle_min_retrace_pct",
    "handle_max_retrace_pct",
)

#: Percent fields whose documented range includes 100 (a full recovery).
_PERCENT_FIELDS_INCLUDING_100 = ("cup_min_recovery_pct",)

_INT_FIELDS = (
    "swing_left_window",
    "swing_right_window",
    "min_span_candles",
    "max_span_candles",
    "pole_min_candles",
    "pole_max_candles",
    "flag_min_consolidation_candles",
    "flag_max_consolidation_candles",
    "cup_min_leg_candles",
    "handle_min_candles",
    "handle_max_candles",
)


def canonical_decimal(value: Decimal) -> str:
    """Return a spelling-independent exact decimal string.

    ``Decimal("0.50")`` and ``Decimal("0.5")`` are the same number, so they must
    produce the same fingerprint and the same identity. ``normalize`` removes
    trailing zeros without changing the value; ``format(..., "f")`` then avoids
    exponent spelling.
    """

    normalized = value.normalize()
    return format(normalized, "f")


def chart_geometry_fingerprint(parameters: "ChartGeometryParameters") -> str:
    """Return the SHA-256 fingerprint of one exact parameter set.

    The payload is the rules version plus every field in declaration order with
    canonical spellings, so two parameter objects that describe the same rules
    fingerprint identically and any single threshold change fingerprints
    differently. The fingerprint is an input to every formation and event id.
    """

    payload: dict[str, str] = {"rules_version": CHART_GEOMETRY_RULES_VERSION}
    for field in fields(parameters):
        value = getattr(parameters, field.name)
        if isinstance(value, Decimal):
            payload[field.name] = canonical_decimal(value)
        elif isinstance(value, SwingTiePolicy):
            payload[field.name] = value.value
        elif isinstance(value, bool):
            payload[field.name] = "true" if value else "false"
        elif isinstance(value, int):
            payload[field.name] = str(value)
        else:  # pragma: no cover - guarded by __post_init__
            raise TypeError(f"unsupported parameter type: {type(value).__name__}")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256((CHART_GEOMETRY_RULES_VERSION + ":" + encoded).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChartGeometryParameters:
    """Chart-geometry-v1 definitions. Defaults are the documented v1 rules.

    Swing source (unchanged Step 3 rules)
        ``swing_left_window`` / ``swing_right_window`` / ``swing_tie_policy``
        are forwarded verbatim to :func:`trading_assistant.market_structure.
        swings.detect_swings`. This package never re-implements or reinterprets
        swing detection; it consumes confirmed swings exactly as Step 3 defines
        them, including their ``confirmed_at`` instants.

    Formation window
        ``min_span_candles`` / ``max_span_candles``
            Inclusive bounds on the candle distance between a formation's first
            and last component pivot. Both bounds are checked on every family.

    Boundary geometry (all families)
        ``min_boundary_gap_pct``
            Smallest allowed separation between the two boundary lines, as a
            percent of their mid price, evaluated at every component pivot. A
            geometry whose boundaries are equal, touching, or closer than this
            is degenerate and is rejected rather than reported.
        ``min_boundary_travel_pct``
            Smallest ``travel_pct`` that counts as a genuinely sloped boundary.
        ``flat_boundary_max_travel_pct``
            Largest absolute ``travel_pct`` that still counts as a flat
            boundary. Must be strictly below ``min_boundary_travel_pct`` so a
            boundary can never be both flat and sloped, which keeps the eight
            family definitions mutually exclusive.

    Lifecycle
        ``breakout_tolerance_pct``
            A closed candle only crosses a boundary when its close is beyond the
            frozen boundary value at that candle's own open time by more than
            this percent of that boundary value. Zero means "strictly beyond".

    Flags (``bull_flag`` / ``bear_flag``)
        ``pole_min_change_pct``
            Smallest pole move, as a percent of the pole's starting pivot price,
            for the preceding leg to count as a real directional pole.
        ``pole_min_candles`` / ``pole_max_candles``
            Inclusive bounds on the pole leg length in candles.
        ``flag_min_retrace_pct`` / ``flag_max_retrace_pct``
            Inclusive bounds on the consolidation retracement, as a percent of
            the pole move. A consolidation that does not pull back at all is not
            a flag; one that retraces the whole pole is not a flag either.
        ``flag_min_consolidation_candles`` / ``flag_max_consolidation_candles``
            Inclusive bounds on the consolidation span in candles.

    Cup and handle (``cup_and_handle``)
        ``cup_min_depth_pct``
            Smallest trough depth below the rim line, as a percent of the rim
            line value at the deepest trough pivot.
        ``cup_rim_tolerance_pct``
            Largest allowed difference between the two rim pivots, as a percent
            of the left rim price ("recovery back to the rim").
        ``cup_min_recovery_pct``
            Smallest share of the descent that the right rim must recover, as a
            percent of (left rim - deepest trough low).
        ``cup_trough_base_tolerance_pct``
            Largest allowed difference between the two trough lows, as a percent
            of the lower of the two. A rounded base has two comparable lows; a
            single spike low does not.
        ``cup_min_interior_prominence_pct``
            Smallest interior trough high above the higher trough low, as a
            percent of the cup depth, so the base is genuinely rounded rather
            than one pivot.
        ``cup_min_leg_candles``
            Smallest candle length for both the descent leg (left rim to the
            deepest trough low) and the ascent leg (deepest trough low to the
            right rim). This is what excludes a V-shaped trough.
        ``handle_min_retrace_pct`` / ``handle_max_retrace_pct``
            Inclusive bounds on the handle pullback, as a percent of the cup
            depth measured from the right rim.
        ``handle_min_candles`` / ``handle_max_candles``
            Inclusive bounds on the handle span in candles.
    """

    swing_left_window: int = 2
    swing_right_window: int = 2
    swing_tie_policy: SwingTiePolicy = SwingTiePolicy.STRICT

    min_span_candles: int = 4
    max_span_candles: int = 240

    min_boundary_gap_pct: Decimal = Decimal("0.05")
    min_boundary_travel_pct: Decimal = Decimal("0.5")
    flat_boundary_max_travel_pct: Decimal = Decimal("0.25")

    breakout_tolerance_pct: Decimal = Decimal("0.1")

    pole_min_change_pct: Decimal = Decimal("3")
    pole_min_candles: int = 3
    pole_max_candles: int = 60
    flag_min_retrace_pct: Decimal = Decimal("10")
    flag_max_retrace_pct: Decimal = Decimal("50")
    flag_min_consolidation_candles: int = 3
    flag_max_consolidation_candles: int = 60

    cup_min_depth_pct: Decimal = Decimal("5")
    cup_rim_tolerance_pct: Decimal = Decimal("2")
    cup_min_recovery_pct: Decimal = Decimal("90")
    cup_trough_base_tolerance_pct: Decimal = Decimal("1")
    cup_min_interior_prominence_pct: Decimal = Decimal("10")
    cup_min_leg_candles: int = 3
    handle_min_retrace_pct: Decimal = Decimal("5")
    handle_max_retrace_pct: Decimal = Decimal("60")
    handle_min_candles: int = 3
    handle_max_candles: int = 60

    def __post_init__(self) -> None:
        for name in _PERCENT_FIELDS:
            value = as_decimal(getattr(self, name), name=name)
            if not 0 <= value < 100:
                raise ValueError(f"{name} must be >= 0 and < 100")
            object.__setattr__(self, name, value)
        for name in _PERCENT_FIELDS_INCLUDING_100:
            value = as_decimal(getattr(self, name), name=name)
            if not 0 < value <= 100:
                raise ValueError(f"{name} must be > 0 and <= 100")
            object.__setattr__(self, name, value)
        for name in _INT_FIELDS:
            require_int(getattr(self, name), name=name, minimum=1)
        if not isinstance(self.swing_tie_policy, SwingTiePolicy):
            try:
                object.__setattr__(
                    self, "swing_tie_policy", SwingTiePolicy(self.swing_tie_policy)
                )
            except ValueError as exc:
                raise ValueError(
                    "swing_tie_policy must be one of: "
                    + ", ".join(policy.value for policy in SwingTiePolicy)
                ) from exc

        if self.min_span_candles > self.max_span_candles:
            raise ValueError("min_span_candles must be <= max_span_candles")
        if self.flat_boundary_max_travel_pct >= self.min_boundary_travel_pct:
            raise ValueError(
                "flat_boundary_max_travel_pct must be below min_boundary_travel_pct "
                "so a boundary can never be both flat and sloped"
            )
        if self.min_boundary_gap_pct == 0:
            raise ValueError("min_boundary_gap_pct must be positive (degenerate boundaries are rejected)")
        if self.pole_min_change_pct == 0:
            raise ValueError("pole_min_change_pct must be positive (a flag needs a real pole)")
        if self.pole_min_candles > self.pole_max_candles:
            raise ValueError("pole_min_candles must be <= pole_max_candles")
        if not 0 < self.flag_min_retrace_pct < self.flag_max_retrace_pct:
            raise ValueError("flag_min_retrace_pct must be positive and below flag_max_retrace_pct")
        if self.flag_min_consolidation_candles > self.flag_max_consolidation_candles:
            raise ValueError(
                "flag_min_consolidation_candles must be <= flag_max_consolidation_candles"
            )
        if self.cup_min_depth_pct == 0:
            raise ValueError("cup_min_depth_pct must be positive")
        if self.cup_min_leg_candles > self.max_span_candles:
            raise ValueError("cup_min_leg_candles must be <= max_span_candles")
        if not 0 <= self.handle_min_retrace_pct < self.handle_max_retrace_pct:
            raise ValueError(
                "handle_min_retrace_pct must be >= 0 and below handle_max_retrace_pct"
            )
        if self.handle_min_candles > self.handle_max_candles:
            raise ValueError("handle_min_candles must be <= handle_max_candles")

    def fingerprint(self) -> str:
        """Return this parameter set's ``chart-geometry-v1`` fingerprint."""

        return chart_geometry_fingerprint(self)

    def fingerprint_payload(self) -> dict[str, str]:
        """Return the exact canonical payload that is fingerprinted."""

        payload: dict[str, str] = {"rules_version": CHART_GEOMETRY_RULES_VERSION}
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, Decimal):
                payload[field.name] = canonical_decimal(value)
            elif isinstance(value, SwingTiePolicy):
                payload[field.name] = value.value
            else:
                payload[field.name] = str(value)
        return payload
