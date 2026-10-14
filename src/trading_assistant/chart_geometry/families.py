"""The eight chart-geometry-v1 families and their exact component templates.

Each family is defined here as a **geometric definition**: how many confirmed
swing components it needs, in which alternating pivot order, which two boundary
lines bound it, and which boundary crossing this package calls a confirmation
and which it calls a failure. Nothing in this table is a probability, a
direction forecast, or a profitability claim.

Two side conventions exist:

``breakout_side``
    ``"above"`` / ``"below"``
        The family's own geometry names one continuation side (flags after a
        directional pole, a cup and handle through its rim line). A close beyond
        that boundary is recorded as ``confirmed`` with the observed direction.
    ``"either"``
        Converging families (both wedges and all three triangles) describe
        contraction only. A close beyond *either* boundary is recorded as
        ``confirmed`` together with the direction actually observed; this
        package never asserts which side a wedge or triangle "should" break.

``failure_side``
    The opposite boundary for the directional families, or ``"none"`` for the
    converging families. Every family can still be invalidated by the
    family-agnostic geometric rule ``boundaries_converged_or_crossed``: once the
    two frozen boundary lines are no longer ordered (they meet at the apex or
    cross), the bounded region the formation describes no longer exists, and the
    formation is terminally invalidated without any directional claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from trading_assistant.market_structure.swings import SwingKind

BreakoutSide = Literal["above", "below", "either"]
FailureSide = Literal["above", "below", "none"]

_HIGH = SwingKind.HIGH
_LOW = SwingKind.LOW


class ChartGeometryFamily(StrEnum):
    """The eight families implemented by chart-geometry-v1."""

    BULL_FLAG = "bull_flag"
    BEAR_FLAG = "bear_flag"
    RISING_WEDGE = "rising_wedge"
    FALLING_WEDGE = "falling_wedge"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DESCENDING_TRIANGLE = "descending_triangle"
    SYMMETRICAL_TRIANGLE = "symmetrical_triangle"
    CUP_AND_HANDLE = "cup_and_handle"

    def definition(self) -> "FamilyDefinition":
        """Return this family's exact component template and side conventions."""

        return DEFINITION_BY_FAMILY[self]

    def definition_text(self) -> str:
        """Return this family's geometric definition, verbatim.

        The text is a definition. It never states or implies a probability, a
        likelihood, a target, or a profit expectation.
        """

        return DEFINITION_BY_FAMILY[self].definition


@dataclass(frozen=True, slots=True)
class FamilyDefinition:
    """One family's component template and boundary-side conventions."""

    family: ChartGeometryFamily
    component_count: int
    #: Exact alternating pivot kinds when the template fixes them, or ``None``
    #: when roles are assigned from the window's own highs and lows.
    pivot_kinds: tuple[SwingKind, ...] | None
    #: Role names parallel to ``pivot_kinds``; ``None`` for pair-assigned roles.
    roles: tuple[str, ...] | None
    breakout_side: BreakoutSide
    failure_side: FailureSide
    requires_convergence: bool
    definition: str


#: Roles for the converging families. Two boundary lines are always anchored on
#: the window's two highs (chronologically ``upper_a`` then ``upper_b``) and its
#: two lows (``lower_a`` then ``lower_b``), whichever pivot comes first.
CONVERGING_ROLES_HIGH = ("upper_a", "upper_b")
CONVERGING_ROLES_LOW = ("lower_a", "lower_b")

FAMILY_DEFINITIONS: tuple[FamilyDefinition, ...] = (
    FamilyDefinition(
        family=ChartGeometryFamily.BULL_FLAG,
        component_count=5,
        pivot_kinds=(_LOW, _HIGH, _LOW, _HIGH, _LOW),
        roles=("pole_start", "pole_end", "flag_low_a", "flag_high", "flag_low_b"),
        breakout_side="above",
        failure_side="below",
        requires_convergence=False,
        definition=(
            "A rising pole from a confirmed swing low to a later confirmed swing high, "
            "followed by three alternating swings that stay inside the pole range, retrace "
            "a bounded share of it, and are bounded by two non-expanding lines that do not "
            "rise with the pole."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.BEAR_FLAG,
        component_count=5,
        pivot_kinds=(_HIGH, _LOW, _HIGH, _LOW, _HIGH),
        roles=("pole_start", "pole_end", "flag_high_a", "flag_low", "flag_high_b"),
        breakout_side="below",
        failure_side="above",
        requires_convergence=False,
        definition=(
            "A falling pole from a confirmed swing high to a later confirmed swing low, "
            "followed by three alternating swings that stay inside the pole range, retrace "
            "a bounded share of it, and are bounded by two non-expanding lines that do not "
            "fall with the pole."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.ASCENDING_TRIANGLE,
        component_count=4,
        pivot_kinds=None,
        roles=None,
        breakout_side="either",
        failure_side="none",
        requires_convergence=True,
        definition=(
            "Two confirmed swing highs whose boundary line is flat within "
            "flat_boundary_max_travel_pct, and two confirmed swing lows whose boundary line "
            "rises by at least min_boundary_travel_pct, converging on an apex that lies "
            "after the last component."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.DESCENDING_TRIANGLE,
        component_count=4,
        pivot_kinds=None,
        roles=None,
        breakout_side="either",
        failure_side="none",
        requires_convergence=True,
        definition=(
            "Two confirmed swing lows whose boundary line is flat within "
            "flat_boundary_max_travel_pct, and two confirmed swing highs whose boundary line "
            "falls by at least min_boundary_travel_pct, converging on an apex that lies "
            "after the last component."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.SYMMETRICAL_TRIANGLE,
        component_count=4,
        pivot_kinds=None,
        roles=None,
        breakout_side="either",
        failure_side="none",
        requires_convergence=True,
        definition=(
            "Two confirmed swing highs whose boundary line falls and two confirmed swing lows "
            "whose boundary line rises, each by at least min_boundary_travel_pct, converging "
            "on an apex that lies after the last component."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.RISING_WEDGE,
        component_count=4,
        pivot_kinds=None,
        roles=None,
        breakout_side="either",
        failure_side="none",
        requires_convergence=True,
        definition=(
            "Two confirmed swing highs and two confirmed swing lows whose boundary lines both "
            "rise by at least min_boundary_travel_pct while the upper line rises more slowly, "
            "so the two lines converge on an apex after the last component. This is a "
            "geometric description of a rising contraction; it is not a bearish forecast."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.FALLING_WEDGE,
        component_count=4,
        pivot_kinds=None,
        roles=None,
        breakout_side="either",
        failure_side="none",
        requires_convergence=True,
        definition=(
            "Two confirmed swing highs and two confirmed swing lows whose boundary lines both "
            "fall by at least min_boundary_travel_pct while the upper line falls more slowly, "
            "so the two lines converge on an apex after the last component. This is a "
            "geometric description of a falling contraction; it is not a bullish forecast."
        ),
    ),
    FamilyDefinition(
        family=ChartGeometryFamily.CUP_AND_HANDLE,
        component_count=8,
        pivot_kinds=(_HIGH, _LOW, _HIGH, _LOW, _HIGH, _LOW, _HIGH, _LOW),
        roles=(
            "left_rim",
            "trough_low_a",
            "trough_high",
            "trough_low_b",
            "right_rim",
            "handle_low_a",
            "handle_high",
            "handle_low_b",
        ),
        breakout_side="above",
        failure_side="below",
        requires_convergence=False,
        definition=(
            "Two rim swing highs within cup_rim_tolerance_pct of each other, a rounded trough "
            "between them made of three interior swings whose two lows are comparable and "
            "whose interior high is prominent, descent and ascent legs of at least "
            "cup_min_leg_candles each, and a later three-swing handle that stays below the rim "
            "line and retraces a bounded share of the cup depth."
        ),
    ),
)

DEFINITION_BY_FAMILY: dict[ChartGeometryFamily, FamilyDefinition] = {
    definition.family: definition for definition in FAMILY_DEFINITIONS
}

#: The converging families share one four-swing window and are separated only by
#: their boundary slope conditions, which the parameter validation makes
#: mutually exclusive.
CONVERGING_FAMILIES: tuple[ChartGeometryFamily, ...] = (
    ChartGeometryFamily.ASCENDING_TRIANGLE,
    ChartGeometryFamily.DESCENDING_TRIANGLE,
    ChartGeometryFamily.SYMMETRICAL_TRIANGLE,
    ChartGeometryFamily.RISING_WEDGE,
    ChartGeometryFamily.FALLING_WEDGE,
)

FLAG_FAMILIES: tuple[ChartGeometryFamily, ...] = (
    ChartGeometryFamily.BULL_FLAG,
    ChartGeometryFamily.BEAR_FLAG,
)


def minimum_component_count(family: ChartGeometryFamily) -> int:
    """Return the exact minimum number of confirmed swings a family requires."""

    return DEFINITION_BY_FAMILY[family].component_count
