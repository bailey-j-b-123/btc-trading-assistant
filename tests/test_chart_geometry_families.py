"""Positive and negative geometry cases for each of the eight families.

Every fixture is offline and synthetic (see ``test_chart_geometry_fixtures``),
and every expected number below is calculated **independently of the detector**
from the fixture's own pivot prices, using the documented rule that a confirmed
swing sits exactly ``SWING_OFFSET = 0.10`` away from its pivot price (highs
above, lows below). Percentages use the Step 3 helpers' documented rounding
(28-digit division, ROUND_HALF_EVEN, 8 decimal places), so the literals here are
the exact values the arithmetic produces — not values copied from a run.

Fixture index arithmetic used throughout (``leg=4``, Step 3 default 2/2 swing
windows): pivot ``k`` sits at candle index ``4k``; a pivot at index ``i`` is
confirmed at ``i + 3`` hours, i.e. by the close of candle ``i + 2``; so a
four-swing formation ending at index 16 becomes knowable at the close of candle
18 and the first candle that can confirm or invalidate it is index 19.

These tests assert geometry, chronology, and exact rejection reasons. They never
assert that a described geometry was profitable, likely, or a good trade, and no
test fabricates a decision or an outcome.
"""

from __future__ import annotations

from decimal import Decimal as D

from test_chart_geometry_fixtures import (
    EPOCH,
    INTERVAL,
    SWING_OFFSET,
    close_time,
    detect,
    synthetic_series,
)

from trading_assistant.chart_geometry import (
    ChartGeometryFamily,
    ChartGeometryParameters,
    minimum_component_count,
)
from trading_assistant.market_structure.swings import SwingKind

# --- Shared helpers ----------------------------------------------------------


def hour(moment) -> int:
    """Return a fixture candle index for a timestamp (test readability only)."""

    return int((moment - EPOCH) // INTERVAL)


def rejection_reasons(snapshot, family=None) -> list[tuple[str, int]]:
    """Return ``(reason, count)`` pairs, optionally filtered to one family."""

    return [
        (rejection.reason, rejection.count)
        for rejection in snapshot.diagnostics.rejections
        if family is None or rejection.family is family
    ]


def only(snapshot, family: ChartGeometryFamily):
    formations = snapshot.formations_for(family)
    assert len(formations) == 1, [f.family.value for f in snapshot.formations]
    return formations[0]


# --- Fixture definitions, with the arithmetic each test relies on ------------
#
# SWING_OFFSET = 0.10, so a pivot priced P contributes a swing high of P + 0.10
# and a swing low of P - 0.10.

# ascending_triangle: pivots (lead 100), high 110 @4, low 104 @8, high 110.1 @12,
# low 108 @16.
#   upper 110.10 @4 -> 110.20 @12 : slope 0.10/8 = 0.0125
#                                   travel 0.10/110.15*100 = 0.09078529%  (flat)
#   lower 103.90 @8 -> 107.90 @16 : slope 4.00/8 = 0.5
#                                   travel 4/105.90*100 = 3.77714825%    (rising)
#   gap @4 = 110.10 - (103.90 - 0.5*4) = 8.20
#   gap @16 = (110.20 + 0.0125*4) - 107.90 = 2.35
#   contraction = (8.20 - 2.35)/8.20*100 = 71.34146341%
#   apex = (103.90 - 110.10 - 0.5*4)/(0.0125 - 0.5) = 16.82051282 candles after @4
ASCENDING_PIVOTS = [100, 110, 104, "110.1", 108]
ASCENDING_TAIL = [
    ("108", "109.2", "108.0", "109.0"),  # index 17, inside both boundaries
    ("109.0", "109.7", "109.0", "109.5"),  # index 18, confirms the last component
    ("109.5", "110.0", "109.6", "109.8"),  # index 19, first eligible candle, inside
    ("109.8", "111.7", "109.7", "111.5"),  # index 20, closes above the upper line
]

# descending_triangle: pivots (lead 110), low 100 @4, high 106 @8, low 99.9 @12,
# high 102 @16.
#   upper 106.10 @8 -> 102.10 @16 : slope -4/8 = -0.5
#                                   travel -4/104.10*100 = -3.84245917% (falling)
#   lower 99.90 @4 -> 99.80 @12   : slope -0.10/8 = -0.0125
#                                   travel -0.10/99.85*100 = -0.10015023% (flat)
#   gap @4 = (106.10 + 0.5*4) - 99.90 = 8.20 ; gap @16 = 102.10 - 99.75 = 2.35
#   apex = (99.90 - 106.10 - (-0.0125 * -4))/(-0.5 + 0.0125) = 12.82051282 after @8
DESCENDING_PIVOTS = [110, 100, 106, "99.9", 102]
DESCENDING_TAIL = [
    ("102", "102.05", "100.4", "100.5"),  # index 17
    ("100.5", "100.6", "99.95", "100.0"),  # index 18
    ("100.0", "100.1", "98.4", "98.5"),  # index 19, closes below the lower line
]

# symmetrical_triangle: pivots (lead 100), high 110 @4, low 100 @8, high 107 @12,
# low 103 @16.
#   upper 110.10 @4 -> 107.10 @12 : slope -3/8 = -0.375
#                                   travel -3/108.60*100 = -2.76243094%
#   lower 99.90 @8 -> 102.90 @16  : slope 3/8 = 0.375
#                                   travel 3/101.40*100 = 2.95857988%
#   gap @4 = 110.10 - (99.90 - 0.375*4) = 11.70 ; gap @16 = 105.60 - 102.90 = 2.70
#   apex = (99.90 - 110.10 - 0.375*4)/(-0.375 - 0.375) = 15.6 candles after @4,
#   i.e. at index 19.6: index 19 is still inside, index 20 is at/after the apex.
SYMMETRICAL_PIVOTS = [100, 110, 100, 107, 103]
SYMMETRICAL_CONFIRM_TAIL = [
    ("103", "104.4", "103.2", "104.2"),  # index 17
    ("104.2", "104.3", "103.7", "104.0"),  # index 18
    ("104.0", "105.6", "103.9", "105.5"),  # index 19, closes above the upper line
]
SYMMETRICAL_APEX_TAIL = [
    ("103", "104.4", "103.2", "104.2"),  # index 17
    ("104.2", "104.3", "103.7", "104.0"),  # index 18
    ("104.0", "104.4", "103.9", "104.2"),  # index 19, still inside (104.025..104.475)
    ("104.2", "104.5", "104.0", "104.3"),  # index 20, past the apex: lines crossed
    ("104.3", "108.0", "104.2", "107.5"),  # index 21, far above the upper line
    ("107.5", "111.0", "107.4", "110.5"),  # index 22, further above again
]

# rising_wedge: pivots (lead 100), high 108 @4, low 102 @8, high 110 @12, low 106 @16.
#   upper 108.10 @4 -> 110.10 @12 : slope 2/8 = 0.25
#                                   travel 2/109.10*100 = 1.83318057%   (rising)
#   lower 101.90 @8 -> 105.90 @16 : slope 4/8 = 0.5
#                                   travel 4/103.90*100 = 3.84985563%   (rising)
#   upper rises more slowly than the lower line, so the gap narrows:
#   gap @4 = 108.10 - (101.90 - 0.5*4) = 8.20 ; gap @16 = 111.10 - 105.90 = 5.20
#   apex = (101.90 - 108.10 - 0.5*4)/(0.25 - 0.5) = 32.8 candles after @4
RISING_PIVOTS = [100, 108, 102, 110, 106]
RISING_TAIL = [
    ("106", "107.2", "106.0", "107.0"),  # index 17
    ("107.0", "107.7", "106.8", "107.5"),  # index 18
    ("107.5", "107.6", "106.4", "106.5"),  # index 19, closes below the lower line
]

# falling_wedge: pivots (lead 100), high 108 @4, low 102 @8, high 104 @12, low 100 @16.
#   upper 108.10 @4 -> 104.10 @12 : slope -4/8 = -0.5
#                                   travel -4/106.10*100 = -3.77002828% (falling)
#   lower 101.90 @8 -> 99.90 @16  : slope -2/8 = -0.25
#                                   travel -2/100.90*100 = -1.98216056% (falling)
#   the upper line falls faster, so the gap narrows:
#   gap @4 = 108.10 - (101.90 + 0.25*4) = 5.20 ; gap @16 = 102.10 - 99.90 = 2.20
#   apex = (101.90 - 108.10 + 0.25*4)/(-0.5 + 0.25) = 20.8 candles after @4
FALLING_PIVOTS = [100, 108, 102, 104, 100]
FALLING_TAIL = [
    ("100", "100.7", "100.0", "100.5"),  # index 17
    ("100.5", "100.7", "100.1", "100.2"),  # index 18
    ("100.2", "101.6", "100.1", "101.5"),  # index 19, closes above the upper line
]

# bull_flag: pivots (lead 104), low 100 @4, high 110 @8, low 106 @12, high 108 @16,
# low 106.1 @20.
#   pole 99.90 @4 -> 110.10 @8 : move 10.20, 10.20/99.90*100 = 10.21021021%, span 4
#   body pivots 105.90 @12, 108.10 @16, 106.00 @20 all inside (99.90, 110.10]
#   deepest body low 105.90 -> retrace (110.10-105.90)/10.20*100 = 41.17647059%
#   upper 110.10 @8 -> 108.10 @16 : slope -0.25, travel -2/109.10*100 = -1.83318057%
#   lower 105.90 @12 -> 106.00 @20: slope 0.0125, travel 0.10/105.95*100 = 0.09438414%
#   body gaps @12/@16/@20 = 3.20 / 2.15 / 1.10 (non-expanding)
BULL_FLAG_PIVOTS = [104, 100, 110, 106, 108, "106.1"]
BULL_FLAG_TAIL = [
    ("106.1", "106.6", "106.2", "106.4"),  # index 21
    ("106.4", "106.6", "106.3", "106.5"),  # index 22, confirms the last component
    ("106.5", "106.9", "106.4", "106.7"),  # index 23, closes above the upper line
    ("106.7", "107.6", "106.6", "107.5"),  # index 24, further above
]

# bear_flag: pivots (lead 100), high 104 @4, low 94 @8, high 98 @12, low 96 @16,
# high 97.9 @20.
#   pole 104.10 @4 -> 93.90 @8 : move 10.20, 10.20/104.10*100 = 9.79827090%, span 4
#   highest body high 98.10 -> retrace (98.10-93.90)/10.20*100 = 41.17647059%
#   upper 98.10 @12 -> 98.00 @20 : slope -0.0125, travel -0.10/98.05*100 = -0.10198878%
#   lower 93.90 @8 -> 95.90 @16  : slope 0.25, travel 2/94.90*100 = 2.10748156%
#   body gaps @12/@16/@20 = 3.20 / 2.15 / 1.10 (non-expanding)
BEAR_FLAG_PIVOTS = [100, 104, 94, 98, 96, "97.9"]
BEAR_FLAG_TAIL = [
    ("97.9", "97.95", "97.5", "97.6"),  # index 21
    ("97.6", "97.9", "97.55", "97.7"),  # index 22, confirms the last component
    ("97.7", "97.85", "97.6", "97.75"),  # index 23, still inside
    ("97.75", "97.8", "96.4", "96.5"),  # index 24, closes below the lower line
]

# cup_and_handle: pivots (lead 100), left rim 110 @4, trough low 100 @8, interior
# high 104 @12, trough low 100.5 @16, right rim 109.5 @20, handle low 106 @24,
# handle high 108 @28, handle low 106.5 @32.
#   rims 110.10 @4 and 109.60 @20 : difference 0.50/110.10*100 = 0.45413261%
#   rim line slope -0.50/16 = -0.03125, travel -0.50/109.85*100 = -0.45516614%
#   deepest trough low 99.90 @8, rim there = 110.10 - 0.03125*4 = 109.975
#       depth 10.075, 10.075/109.975*100 = 9.16117299%
#   trough base difference 0.50/99.90*100 = 0.50050050%
#   interior prominence (104.10-100.40)/10.075*100 = 36.72456576%
#   descent leg 4 candles, ascent leg 12 candles
#   recovery (109.60-99.90)/(110.10-99.90)*100 = 95.09803922%
#   handle lowest 105.90, handle retrace (109.60-105.90)/9.70*100 = 38.14432990%
#   handle lower line 105.90 @24 -> 106.40 @32 : slope 0.0625,
#       travel 0.50/106.15*100 = 0.47103156%
#   handle gaps @24/@28/@32 = 3.575 / 3.20 / 2.825 (non-expanding)
CUP_PIVOTS = [100, 110, 100, 104, "100.5", "109.5", 106, 108, "106.5"]
CUP_TAIL = [
    ("106.5", "107.2", "106.6", "107.0"),  # index 33
    ("107.0", "107.7", "106.9", "107.5"),  # index 34, confirms the last component
    ("107.5", "108.2", "107.4", "108.0"),  # index 35, first eligible candle, inside
    ("108.0", "110.2", "107.9", "110.0"),  # index 36, closes above the rim line
]


# --- ascending_triangle ------------------------------------------------------


def test_ascending_triangle_positive_geometry_is_exact():
    candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.ASCENDING_TRIANGLE)

    assert [(c.role, hour(c.timestamp), c.pivot_kind) for c in formation.components] == [
        ("upper_a", 4, SwingKind.HIGH),
        ("lower_a", 8, SwingKind.LOW),
        ("upper_b", 12, SwingKind.HIGH),
        ("lower_b", 16, SwingKind.LOW),
    ]
    assert [c.price for c in formation.components] == [
        D("110") + SWING_OFFSET,
        D("104") - SWING_OFFSET,
        D("110.1") + SWING_OFFSET,
        D("108") - SWING_OFFSET,
    ]
    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price, upper.span_candles) == (D("110.10"), D("110.20"), 8)
    assert upper.slope_per_candle == D("0.0125")  # 0.10 / 8
    assert upper.travel_pct == D("0.09078529")  # 0.10 / 110.15 * 100
    assert (lower.start_price, lower.end_price, lower.span_candles) == (D("103.90"), D("107.90"), 8)
    assert lower.slope_per_candle == D("0.5")  # 4.00 / 8
    assert lower.travel_pct == D("3.77714825")  # 4 / 105.90 * 100
    assert formation.geometry.gap_at_region_start == D("8.20")
    assert formation.geometry.gap_at_region_end == D("2.35")
    assert formation.geometry.metric("contraction_pct").value == D("71.34146341")
    assert formation.geometry.convergence.converging is True
    assert formation.geometry.convergence.parallel is False
    assert formation.geometry.convergence.apex_offset_candles == D("16.82051282")
    assert formation.geometry.convergence.apex_after_last_component is True
    assert formation.span_candles == 12
    assert formation.breakout_side == "either"
    assert formation.failure_side == "none"


def test_ascending_triangle_confirms_on_a_later_closed_candle_with_exact_penetration():
    candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.ASCENDING_TRIANGLE)

    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    assert formation.formed_at == close_time(18)  # last component confirmed by candle 18
    assert [event.state for event in formation.events] == ["formed", "confirmed"]

    formed, confirmed = formation.events
    assert formed.anchor_candle is candles[18]
    assert formed.known_at == close_time(18)
    assert formed.observed_direction is None
    assert formed.reason == "minimum_components_satisfied"
    assert confirmed.anchor_candle is candles[20]
    assert confirmed.known_at == close_time(20)
    assert confirmed.reason == "close_beyond_upper_boundary"
    assert confirmed.observed_direction == "above"
    # upper at index 20 = 110.10 + 0.0125 * 16 = 110.30 ; close 111.5
    assert confirmed.upper_boundary_value == D("110.30")
    assert confirmed.lower_boundary_value == D("109.90")  # 103.90 + 0.5 * 12
    assert confirmed.penetration == D("1.20")
    assert confirmed.penetration_pct == D("1.08794198")  # 1.20 / 110.30 * 100
    assert confirmed.candles_since_formed == 1
    assert confirmed.gap_before_anchor is False


def test_ascending_triangle_requires_a_flat_top_and_a_rising_base():
    """A top that genuinely rises is a rising wedge, never an ascending triangle."""

    candles = synthetic_series(
        [100, 110, 104, 112, 108],
        tail=[
            ("108", "109.2", "108.0", "109.0"),
            ("109.0", "109.7", "109.0", "109.5"),
            ("109.5", "113.7", "109.4", "113.5"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE) == ()
    # upper travel = 2/111.10*100 = 1.80018002% (rising), lower travel = 3.77714825%
    wedge = only(snapshot, ChartGeometryFamily.RISING_WEDGE)
    assert wedge.geometry.upper.travel_pct == D("1.80018002")


def test_ascending_triangle_rejects_a_base_that_does_not_rise_enough():
    """Both boundaries flat is a rectangle: no v1 family describes it."""

    candles = synthetic_series(
        [100, 110, 104, "110.1", "104.1"],
        tail=[
            ("104.5", "105.0", "104.3", "104.8"),
            ("104.8", "105.2", "104.6", "105.0"),
            ("105.0", "105.4", "104.8", "105.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations == ()
    assert rejection_reasons(snapshot) == [("boundary_slopes_match_no_v1_family", 1)]


def test_ascending_triangle_rejects_a_gap_that_is_too_narrow_to_be_a_region():
    """The same geometry under a wider minimum gap is degenerate, not a triangle."""

    candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
    snapshot = detect(
        candles, parameters=ChartGeometryParameters(min_boundary_gap_pct=D("5"))
    )
    assert snapshot.formations == ()
    # narrowest gap is 2.35 at index 16: 2.35/109.075*100 = 2.15448086% < 5%
    assert rejection_reasons(snapshot, ChartGeometryFamily.ASCENDING_TRIANGLE) == [
        ("boundary_gap_below_minimum", 1)
    ]


# --- descending_triangle -----------------------------------------------------


def test_descending_triangle_positive_geometry_is_exact():
    candles = synthetic_series(DESCENDING_PIVOTS, tail=DESCENDING_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.DESCENDING_TRIANGLE)

    assert [(c.role, hour(c.timestamp)) for c in formation.components] == [
        ("lower_a", 4),
        ("upper_a", 8),
        ("lower_b", 12),
        ("upper_b", 16),
    ]
    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price) == (D("106.10"), D("102.10"))
    assert upper.slope_per_candle == D("-0.5")
    assert upper.travel_pct == D("-3.84245917")  # -4 / 104.10 * 100
    assert (lower.start_price, lower.end_price) == (D("99.90"), D("99.80"))
    assert lower.slope_per_candle == D("-0.0125")
    assert lower.travel_pct == D("-0.10015023")  # -0.10 / 99.85 * 100
    assert formation.geometry.gap_at_region_start == D("8.20")
    assert formation.geometry.gap_at_region_end == D("2.35")
    assert formation.geometry.convergence.converging is True
    assert formation.geometry.convergence.apex_offset_candles == D("12.82051282")

    assert formation.state == "confirmed"
    assert formation.observed_direction == "below"
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[19]
    assert confirmed.reason == "close_beyond_lower_boundary"
    # lower at index 19 = 99.90 - 0.0125 * 15 = 99.7125 ; close 98.5
    assert confirmed.lower_boundary_value == D("99.7125")
    assert confirmed.upper_boundary_value == D("100.60")  # 106.10 - 0.5 * 11
    assert confirmed.penetration == D("1.2125")
    assert confirmed.penetration_pct == D("1.21599599")


def test_descending_triangle_requires_a_flat_base():
    """A base that genuinely falls makes a falling wedge, not a descending triangle."""

    candles = synthetic_series(
        FALLING_PIVOTS,
        tail=FALLING_TAIL,
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.DESCENDING_TRIANGLE) == ()
    falling = only(snapshot, ChartGeometryFamily.FALLING_WEDGE)
    # lower travel = -2/100.90*100 = -1.98216056%, far beyond the flat tolerance
    assert falling.geometry.lower.travel_pct == D("-1.98216056")


def test_descending_triangle_rejects_parallel_boundaries():
    """Equal slopes have no apex: the geometry is rejected, not reported."""

    # upper 110.10 @4 -> 108.10 @12 (slope -0.25); lower 103.90 @8 -> 101.90 @16
    # (slope -0.25): identical slopes, constant 5.20 gap.
    candles = synthetic_series(
        [100, 110, 104, 108, 102],
        tail=[
            ("102.5", "103.0", "102.2", "102.8"),
            ("102.8", "103.2", "102.5", "103.0"),
            ("103.0", "103.4", "102.8", "103.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.FALLING_WEDGE) == [
        ("boundaries_parallel", 1)
    ]


# --- symmetrical_triangle ----------------------------------------------------


def test_symmetrical_triangle_positive_geometry_is_exact():
    candles = synthetic_series(SYMMETRICAL_PIVOTS, tail=SYMMETRICAL_CONFIRM_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.SYMMETRICAL_TRIANGLE)

    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price) == (D("110.10"), D("107.10"))
    assert upper.slope_per_candle == D("-0.375")
    assert upper.travel_pct == D("-2.76243094")  # -3 / 108.60 * 100
    assert (lower.start_price, lower.end_price) == (D("99.90"), D("102.90"))
    assert lower.slope_per_candle == D("0.375")
    assert lower.travel_pct == D("2.95857988")  # 3 / 101.40 * 100
    assert formation.geometry.gap_at_region_start == D("11.70")
    assert formation.geometry.gap_at_region_end == D("2.70")
    assert formation.geometry.convergence.apex_offset_candles == D("15.6")
    assert formation.geometry.convergence.apex_after_last_component is True

    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[19]
    # upper at index 19 = 110.10 - 0.375 * 15 = 104.475 ; close 105.5
    assert confirmed.upper_boundary_value == D("104.475")
    assert confirmed.lower_boundary_value == D("104.025")  # 99.90 + 0.375 * 11
    assert confirmed.penetration == D("1.025")
    assert confirmed.penetration_pct == D("0.98109596")


def test_symmetrical_triangle_records_the_direction_observed_not_an_expected_one():
    """The same family confirms downwards when that is what actually happened."""

    candles = synthetic_series(
        SYMMETRICAL_PIVOTS,
        tail=[
            ("103", "104.4", "103.2", "104.2"),
            ("104.2", "104.3", "103.7", "104.0"),
            # index 19: lower boundary is 104.025, tolerance 0.104025 -> 103.920975
            ("104.0", "104.1", "103.4", "103.5"),
        ],
    )
    formation = only(detect(candles), ChartGeometryFamily.SYMMETRICAL_TRIANGLE)
    assert formation.state == "confirmed"
    assert formation.observed_direction == "below"
    assert formation.events[-1].reason == "close_beyond_lower_boundary"
    assert formation.events[-1].penetration == D("0.525")  # 104.025 - 103.5


def test_symmetrical_triangle_is_terminally_invalidated_at_its_apex():
    """After the frozen lines meet, later breakouts can never confirm the same id."""

    candles = synthetic_series(SYMMETRICAL_PIVOTS, tail=SYMMETRICAL_APEX_TAIL)
    snapshot = detect(candles)
    formation = only(snapshot, ChartGeometryFamily.SYMMETRICAL_TRIANGLE)

    assert formation.state == "invalidated"
    assert formation.terminal_reason == "boundaries_converged_or_crossed"
    assert formation.observed_direction is None
    assert [event.state for event in formation.events] == ["formed", "invalidated"]

    invalidated = formation.events[-1]
    assert invalidated.anchor_candle is candles[20]
    assert invalidated.known_at == close_time(20)
    # upper at index 20 = 110.10 - 0.375*16 = 104.10 ; lower = 99.90 + 0.375*12 = 104.40
    assert invalidated.upper_boundary_value == D("104.10")
    assert invalidated.lower_boundary_value == D("104.40")
    assert invalidated.penetration is None
    assert "invalidation is terminal" in invalidated.reason_detail

    # Candles 21 and 22 close far above the frozen upper line and still produce
    # no event: an invalidated formation id can never be confirmed or resurrected.
    assert candles[21].close == D("107.5")
    assert candles[22].close == D("110.5")
    assert len(snapshot.events) == 2
    assert snapshot.diagnostics.confirmed_count == 0
    assert snapshot.diagnostics.invalidated_count == 1


def test_symmetrical_triangle_rejects_a_widening_geometry():
    """An apex behind the formation is divergence, not a triangle."""

    # upper 104.10 @4 -> 110.10 @12 (slope +0.75) rises faster than
    # lower 101.90 @8 -> 103.90 @16 (slope +0.25), so the lines met in the past.
    candles = synthetic_series(
        [100, 104, 102, 110, 104],
        tail=[
            ("104.5", "105.0", "104.2", "104.8"),
            ("104.8", "105.2", "104.5", "105.0"),
            ("105.0", "105.4", "104.8", "105.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.RISING_WEDGE) == [
        ("apex_not_after_last_component", 1)
    ]


def test_converging_families_reject_boundaries_that_are_not_ordered():
    """A window whose lines do not bound a region is rejected before any family."""

    # upper 105.10 @8 -> 116.10 @16 has slope 1.375; extrapolated back to index 4
    # it is 105.10 - 1.375*4 = 99.60, below the lower line's 99.90 anchor.
    candles = synthetic_series(
        [104, 100, 105, 101, 116],
        tail=[
            ("116", "116.05", "115.5", "115.8"),
            ("115.8", "116.0", "115.6", "115.9"),
            ("115.9", "116.05", "115.7", "116.0"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations == ()
    assert rejection_reasons(snapshot) == [("boundaries_not_ordered_at_a_component", 1)]


# --- rising_wedge ------------------------------------------------------------


def test_rising_wedge_positive_geometry_is_exact():
    candles = synthetic_series(RISING_PIVOTS, tail=RISING_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.RISING_WEDGE)

    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price) == (D("108.10"), D("110.10"))
    assert upper.slope_per_candle == D("0.25")
    assert upper.travel_pct == D("1.83318057")  # 2 / 109.10 * 100
    assert (lower.start_price, lower.end_price) == (D("101.90"), D("105.90"))
    assert lower.slope_per_candle == D("0.5")
    assert lower.travel_pct == D("3.84985563")  # 4 / 103.90 * 100
    assert formation.geometry.gap_at_region_start == D("8.20")
    assert formation.geometry.gap_at_region_end == D("5.20")
    assert formation.geometry.convergence.converging is True
    assert formation.geometry.convergence.apex_offset_candles == D("32.8")

    assert formation.state == "confirmed"
    assert formation.observed_direction == "below"
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[19]
    # lower at index 19 = 101.90 + 0.5 * 11 = 107.40 ; close 106.5
    assert confirmed.lower_boundary_value == D("107.40")
    assert confirmed.upper_boundary_value == D("111.85")  # 108.10 + 0.25 * 15
    assert confirmed.penetration == D("0.90")
    assert confirmed.penetration_pct == D("0.83798883")


def test_rising_wedge_never_claims_a_bearish_outcome():
    """A rising wedge that leaves upwards is confirmed upwards, with no forecast."""

    candles = synthetic_series(
        RISING_PIVOTS,
        tail=[
            ("106", "107.2", "106.0", "107.0"),
            ("107.0", "107.7", "106.8", "107.5"),
            # index 19: upper boundary 111.85, tolerance 0.11185 -> trigger 111.96185
            ("107.5", "113.0", "107.4", "112.5"),
        ],
    )
    formation = only(detect(candles), ChartGeometryFamily.RISING_WEDGE)
    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    assert formation.events[-1].reason == "close_beyond_upper_boundary"
    assert formation.events[-1].penetration == D("0.65")  # 112.5 - 111.85
    assert "not a bearish forecast" in ChartGeometryFamily.RISING_WEDGE.definition_text()


def test_rising_wedge_rejects_a_flat_lower_boundary():
    """A flat base with a rising top is an ascending triangle, not a wedge."""

    candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.RISING_WEDGE) == ()
    assert only(snapshot, ChartGeometryFamily.ASCENDING_TRIANGLE).geometry.lower.travel_pct == (
        D("3.77714825")
    )


# --- falling_wedge -----------------------------------------------------------


def test_falling_wedge_positive_geometry_is_exact():
    candles = synthetic_series(FALLING_PIVOTS, tail=FALLING_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.FALLING_WEDGE)

    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price) == (D("108.10"), D("104.10"))
    assert upper.slope_per_candle == D("-0.5")
    assert upper.travel_pct == D("-3.77002828")  # -4 / 106.10 * 100
    assert (lower.start_price, lower.end_price) == (D("101.90"), D("99.90"))
    assert lower.slope_per_candle == D("-0.25")
    assert lower.travel_pct == D("-1.98216056")  # -2 / 100.90 * 100
    assert formation.geometry.gap_at_region_start == D("5.20")
    assert formation.geometry.gap_at_region_end == D("2.20")
    assert formation.geometry.convergence.converging is True
    assert formation.geometry.convergence.apex_offset_candles == D("20.8")

    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[19]
    # upper at index 19 = 108.10 - 0.5 * 15 = 100.60 ; close 101.5
    assert confirmed.upper_boundary_value == D("100.60")
    assert confirmed.lower_boundary_value == D("99.15")  # 101.90 - 0.25 * 11
    assert confirmed.penetration == D("0.90")
    assert confirmed.penetration_pct == D("0.89463221")


def test_falling_wedge_never_claims_a_bullish_outcome():
    """The falling-wedge definition is geometry; the direction is only observed."""

    definition = ChartGeometryFamily.FALLING_WEDGE.definition_text()
    assert "not a bullish forecast" in definition
    candles = synthetic_series(
        FALLING_PIVOTS,
        tail=[
            ("100", "100.7", "100.0", "100.5"),
            ("100.5", "100.7", "100.1", "100.2"),
            # index 19: lower boundary 99.15, tolerance 0.09915 -> trigger 99.05085
            ("100.2", "100.3", "98.0", "98.5"),
        ],
    )
    formation = only(detect(candles), ChartGeometryFamily.FALLING_WEDGE)
    assert formation.state == "confirmed"
    assert formation.observed_direction == "below"
    assert formation.events[-1].penetration == D("0.65")  # 99.15 - 98.5


def test_falling_wedge_rejects_a_flat_upper_boundary():
    """A flat top with a falling base is a descending triangle, not a wedge."""

    candles = synthetic_series(DESCENDING_PIVOTS, tail=DESCENDING_TAIL)
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.FALLING_WEDGE) == ()
    assert only(snapshot, ChartGeometryFamily.DESCENDING_TRIANGLE).geometry.upper.travel_pct == (
        D("-3.84245917")
    )


# --- bull_flag ---------------------------------------------------------------


def test_bull_flag_positive_geometry_is_exact():
    candles = synthetic_series(BULL_FLAG_PIVOTS, tail=BULL_FLAG_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.BULL_FLAG)

    assert [(c.role, hour(c.timestamp)) for c in formation.components] == [
        ("pole_start", 4),
        ("pole_end", 8),
        ("flag_low_a", 12),
        ("flag_high", 16),
        ("flag_low_b", 20),
    ]
    metrics = {m.name: m.value for m in formation.geometry.metrics}
    assert metrics["pole_change_pct"] == D("10.21021021")  # 10.20 / 99.90 * 100
    assert metrics["pole_move"] == D("10.20")
    assert metrics["pole_span_candles"] == D(4)
    assert metrics["flag_retrace_pct"] == D("41.17647059")  # 4.20 / 10.20 * 100
    assert metrics["consolidation_span_candles"] == D(8)

    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_time, upper.start_price) == (candles[8].timestamp, D("110.10"))
    assert (upper.end_time, upper.end_price) == (candles[16].timestamp, D("108.10"))
    assert upper.slope_per_candle == D("-0.25")
    assert upper.travel_pct == D("-1.83318057")
    assert (lower.start_price, lower.end_price) == (D("105.90"), D("106.00"))
    assert lower.slope_per_candle == D("0.0125")
    assert lower.travel_pct == D("0.09438414")  # 0.10 / 105.95 * 100
    assert formation.geometry.gap_at_region_start == D("3.20")
    assert formation.geometry.gap_at_region_end == D("1.10")
    assert formation.span_candles == 16
    assert formation.breakout_side == "above"
    assert formation.failure_side == "below"

    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    assert formation.formed_at == close_time(22)
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[23]
    # upper at index 23 = 110.10 - 0.25 * 15 = 106.35 ; close 106.7
    assert confirmed.upper_boundary_value == D("106.35")
    assert confirmed.lower_boundary_value == D("106.0375")  # 105.90 + 0.0125 * 11
    assert confirmed.penetration == D("0.35")
    assert confirmed.penetration_pct == D("0.32910202")


def test_bull_flag_requires_a_real_directional_pole():
    """A drift of 1.2% is not a pole, so no flag is described."""

    # pole 99.90 @4 -> 101.10 @8: move 1.20, 1.20/99.90*100 = 1.20120120% < 3%
    candles = synthetic_series(
        [104, 100, 101, "100.2", "100.8", "100.4"],
        tail=[
            ("100.4", "100.9", "100.5", "100.8"),
            ("100.8", "101.0", "100.6", "100.9"),
            ("100.9", "101.2", "100.7", "101.0"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.BULL_FLAG) == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.BULL_FLAG) == [
        ("pole_change_below_minimum", 1)
    ]


def test_bull_flag_rejects_a_consolidation_that_leaves_the_pole_range():
    """A body pivot above the pole's high is continuation, not a flag."""

    # body high 112.10 > pole end 110.10
    candles = synthetic_series(
        [104, 100, 110, 106, 112, "106.1"],
        tail=[
            ("106.1", "107.0", "106.3", "106.8"),
            ("106.8", "107.2", "106.6", "107.0"),
            ("107.0", "107.4", "106.8", "107.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.BULL_FLAG) == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.BULL_FLAG) == [
        ("consolidation_outside_pole_range", 1)
    ]


def test_bull_flag_rejects_a_widening_consolidation():
    """A flag body must contract or stay parallel; it may never expand."""

    # upper 110.10 @8 -> 110.05 @16 (slope -0.00625), lower 105.90 @12 -> 100.10 @20
    # (slope -0.725): body gaps 4.175 / 7.05 / 9.925 widen. The retrace bound is
    # raised to 99% so the widening rule is the one that rejects.
    candles = synthetic_series(
        [104, 100, 110, 106, "109.95", "100.2"],
        tail=[
            ("100.2", "101.0", "100.4", "100.8"),
            ("100.8", "101.2", "100.6", "101.0"),
            ("101.0", "101.4", "100.8", "101.2"),
        ],
    )
    snapshot = detect(
        candles, parameters=ChartGeometryParameters(flag_max_retrace_pct=D("99"))
    )
    assert snapshot.formations_for(ChartGeometryFamily.BULL_FLAG) == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.BULL_FLAG) == [
        ("flag_consolidation_expanding", 1)
    ]


def test_bull_flag_rejects_a_retrace_outside_the_configured_bounds():
    candles = synthetic_series(BULL_FLAG_PIVOTS, tail=BULL_FLAG_TAIL)
    too_shallow = detect(
        candles,
        parameters=ChartGeometryParameters(
            flag_min_retrace_pct=D("60"), flag_max_retrace_pct=D("90")
        ),
    )
    too_deep = detect(
        candles, parameters=ChartGeometryParameters(flag_max_retrace_pct=D("20"))
    )
    for snapshot in (too_shallow, too_deep):
        assert snapshot.formations_for(ChartGeometryFamily.BULL_FLAG) == ()
        assert rejection_reasons(snapshot, ChartGeometryFamily.BULL_FLAG) == [
            ("flag_retrace_outside_bounds", 1)
        ]


def test_bull_flag_rejects_a_pole_shorter_than_the_configured_minimum():
    candles = synthetic_series(BULL_FLAG_PIVOTS, tail=BULL_FLAG_TAIL)
    snapshot = detect(candles, parameters=ChartGeometryParameters(pole_min_candles=6))
    assert snapshot.formations_for(ChartGeometryFamily.BULL_FLAG) == ()
    # the pole spans exactly 4 candles (index 4 to index 8)
    assert rejection_reasons(snapshot, ChartGeometryFamily.BULL_FLAG) == [
        ("pole_span_outside_bounds", 1)
    ]


def test_flag_families_never_share_a_formation_id_with_their_sub_geometry():
    """The same pivots may describe two families; the ids stay separate."""

    candles = synthetic_series(BULL_FLAG_PIVOTS, tail=BULL_FLAG_TAIL)
    snapshot = detect(candles)
    flag = only(snapshot, ChartGeometryFamily.BULL_FLAG)
    triangle = only(snapshot, ChartGeometryFamily.DESCENDING_TRIANGLE)
    assert flag.formation_id != triangle.formation_id
    assert len(flag.components) == minimum_component_count(ChartGeometryFamily.BULL_FLAG)
    assert len(triangle.components) == minimum_component_count(
        ChartGeometryFamily.DESCENDING_TRIANGLE
    )
    assert {event.event_id for event in snapshot.events} .isdisjoint(
        {flag.formation_id, triangle.formation_id}
    )


# --- bear_flag ---------------------------------------------------------------


def test_bear_flag_positive_geometry_is_exact():
    candles = synthetic_series(BEAR_FLAG_PIVOTS, tail=BEAR_FLAG_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.BEAR_FLAG)

    assert [(c.role, hour(c.timestamp)) for c in formation.components] == [
        ("pole_start", 4),
        ("pole_end", 8),
        ("flag_high_a", 12),
        ("flag_low", 16),
        ("flag_high_b", 20),
    ]
    metrics = {m.name: m.value for m in formation.geometry.metrics}
    assert metrics["pole_change_pct"] == D("9.79827089")  # 10.20 / 104.10 * 100
    assert metrics["pole_move"] == D("10.20")
    assert metrics["flag_retrace_pct"] == D("41.17647059")  # 4.20 / 10.20 * 100

    upper, lower = formation.geometry.upper, formation.geometry.lower
    assert (upper.start_price, upper.end_price) == (D("98.10"), D("98.00"))
    assert upper.slope_per_candle == D("-0.0125")
    assert upper.travel_pct == D("-0.10198878")  # -0.10 / 98.05 * 100
    assert (lower.start_price, lower.end_price) == (D("93.90"), D("95.90"))
    assert lower.slope_per_candle == D("0.25")
    assert lower.travel_pct == D("2.10748156")  # 2 / 94.90 * 100
    assert formation.geometry.gap_at_region_start == D("3.20")
    assert formation.geometry.gap_at_region_end == D("1.10")
    assert formation.breakout_side == "below"
    assert formation.failure_side == "above"

    assert formation.state == "confirmed"
    assert formation.observed_direction == "below"
    assert formation.formed_at == close_time(22)
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[24]
    assert confirmed.candles_since_formed == 1
    # lower at index 24 = 93.90 + 0.25 * 16 = 97.90 ; close 96.5
    assert confirmed.lower_boundary_value == D("97.90")
    assert confirmed.upper_boundary_value == D("97.95")  # 98.10 - 0.0125 * 12
    assert confirmed.penetration == D("1.40")
    assert confirmed.penetration_pct == D("1.43003064")


def test_bear_flag_rejects_a_body_that_breaks_the_pole_low():
    """A body pivot below the pole's end is a reversal, not a bear flag."""

    # body low 93.40 < pole end 93.90
    candles = synthetic_series(
        [100, 104, 94, 98, "93.5", 97],
        tail=[
            ("97", "97.5", "93.6", "93.8"),
            ("93.8", "94.2", "93.7", "94.0"),
            ("94.0", "94.4", "93.9", "94.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.BEAR_FLAG) == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.BEAR_FLAG) == [
        ("consolidation_outside_pole_range", 1)
    ]


def test_flag_family_never_appears_without_its_pole_direction():
    """An up-pole window can only ever be a bull flag, a down-pole only a bear flag."""

    bull = detect(synthetic_series(BULL_FLAG_PIVOTS, tail=BULL_FLAG_TAIL))
    bear = detect(synthetic_series(BEAR_FLAG_PIVOTS, tail=BEAR_FLAG_TAIL))
    assert bull.formations_for(ChartGeometryFamily.BEAR_FLAG) == ()
    assert bear.formations_for(ChartGeometryFamily.BULL_FLAG) == ()


# --- cup_and_handle ----------------------------------------------------------


def test_cup_and_handle_positive_geometry_is_exact():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.CUP_AND_HANDLE)

    assert [(c.role, hour(c.timestamp)) for c in formation.components] == [
        ("left_rim", 4),
        ("trough_low_a", 8),
        ("trough_high", 12),
        ("trough_low_b", 16),
        ("right_rim", 20),
        ("handle_low_a", 24),
        ("handle_high", 28),
        ("handle_low_b", 32),
    ]
    assert len(formation.components) == minimum_component_count(
        ChartGeometryFamily.CUP_AND_HANDLE
    )
    metrics = {m.name: m.value for m in formation.geometry.metrics}
    assert metrics["rim_difference_pct"] == D("0.45413261")  # 0.50 / 110.10 * 100
    assert metrics["cup_depth"] == D("10.075")  # 109.975 - 99.90
    assert metrics["cup_depth_pct"] == D("9.16117299")  # 10.075 / 109.975 * 100
    assert metrics["cup_recovery_pct"] == D("95.09803922")  # 9.70 / 10.20 * 100
    assert metrics["trough_base_difference_pct"] == D("0.50050050")  # 0.50 / 99.90 * 100
    assert metrics["trough_interior_prominence_pct"] == D("36.72456576")  # 3.70/10.075*100
    assert metrics["descent_leg_candles"] == D(4)
    assert metrics["ascent_leg_candles"] == D(12)
    assert metrics["cup_span_candles"] == D(16)
    assert metrics["handle_retrace_pct"] == D("38.14432990")  # 3.70 / 9.70 * 100
    assert metrics["handle_span_candles"] == D(8)

    rim, handle = formation.geometry.upper, formation.geometry.lower
    assert (rim.start_price, rim.end_price, rim.span_candles) == (D("110.10"), D("109.60"), 16)
    assert rim.slope_per_candle == D("-0.03125")
    assert rim.travel_pct == D("-0.45516614")
    assert (handle.start_price, handle.end_price) == (D("105.90"), D("106.40"))
    assert handle.slope_per_candle == D("0.0625")
    assert handle.travel_pct == D("0.47103156")  # 0.50 / 106.15 * 100
    assert formation.geometry.gap_at_region_start == D("3.575")
    assert formation.geometry.gap_at_region_end == D("2.825")
    assert formation.span_candles == 28
    assert formation.breakout_side == "above"
    assert formation.failure_side == "below"

    assert formation.state == "confirmed"
    assert formation.observed_direction == "above"
    assert formation.formed_at == close_time(34)
    confirmed = formation.events[-1]
    assert confirmed.anchor_candle is candles[36]
    assert confirmed.candles_since_formed == 1
    # rim at index 36 = 110.10 - 0.03125 * 32 = 109.10 ; close 110.0
    assert confirmed.upper_boundary_value == D("109.10")
    assert confirmed.lower_boundary_value == D("106.65")  # 105.90 + 0.0625 * 12
    assert confirmed.penetration == D("0.90")
    assert confirmed.penetration_pct == D("0.82493126")


def test_cup_and_handle_requires_recovery_to_the_rim():
    """A right rim far below the left rim is not a cup: no recovery happened."""

    # rims 110.10 @4 and 105.10 @20: difference 5/110.10*100 = 4.54132607% > 2%
    candles = synthetic_series(
        [100, 110, 100, 104, "100.5", 105, 103, "104.5", "103.5"],
        tail=[
            ("103.5", "104.3", "103.7", "104.1"),
            ("104.1", "104.5", "103.9", "104.3"),
            ("104.3", "104.7", "104.1", "104.5"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_rims_not_comparable", 1)
    ]


def test_cup_and_handle_requires_an_explicit_later_handle():
    """Seven swings (a cup with no handle) never describe this family."""

    candles = synthetic_series(
        [100, 110, 100, 104, "100.5", "109.5", 106],
        tail=[
            ("106", "107.0", "106.2", "106.5"),
            ("106.5", "107.2", "106.4", "106.8"),
            ("106.8", "107.5", "106.7", "107.2"),
        ],
    )
    snapshot = detect(candles)
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    assert snapshot.diagnostics.confirmed_swing_count == 6
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == []


def test_cup_and_handle_rejects_a_v_shaped_trough():
    """Legs shorter than the configured minimum are a spike, not a rounded base."""

    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(candles, parameters=ChartGeometryParameters(cup_min_leg_candles=6))
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # the descent leg spans exactly 4 candles (index 4 to index 8)
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_leg_shorter_than_minimum", 1)
    ]


def test_cup_and_handle_rejects_a_trough_that_is_not_rounded():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(
        candles, parameters=ChartGeometryParameters(cup_trough_base_tolerance_pct=D("0.2"))
    )
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # the two trough lows differ by 0.50050050% > 0.2%
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_trough_base_not_rounded", 1)
    ]


def test_cup_and_handle_rejects_a_trough_without_a_prominent_interior_pivot():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(
        candles,
        parameters=ChartGeometryParameters(cup_min_interior_prominence_pct=D("50")),
    )
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # prominence is 3.70, and 50% of the 10.075 depth is 5.0375
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_interior_high_not_prominent", 1)
    ]


def test_cup_and_handle_rejects_a_handle_that_retraces_too_much():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(
        candles, parameters=ChartGeometryParameters(handle_max_retrace_pct=D("20"))
    )
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # the handle retraces 38.14432990% of the cup depth, above the 20% bound
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("handle_retrace_outside_bounds", 1)
    ]


def test_cup_and_handle_rejects_a_shallow_cup():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(candles, parameters=ChartGeometryParameters(cup_min_depth_pct=D("15")))
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # the measured depth is 9.16117299% of the rim line, below the 15% definition
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_depth_below_minimum", 1)
    ]


def test_cup_and_handle_rejects_insufficient_recovery():
    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    snapshot = detect(
        candles, parameters=ChartGeometryParameters(cup_min_recovery_pct=D("99"))
    )
    assert snapshot.formations_for(ChartGeometryFamily.CUP_AND_HANDLE) == ()
    # recovery is 95.09803922%, below the 99% definition
    assert rejection_reasons(snapshot, ChartGeometryFamily.CUP_AND_HANDLE) == [
        ("cup_recovery_below_minimum", 1)
    ]


def test_cup_rim_line_is_never_presented_as_a_zone_or_a_neckline():
    """The rim line is one exact line through two rim pivots and nothing else."""

    candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
    formation = only(detect(candles), ChartGeometryFamily.CUP_AND_HANDLE)
    rim = formation.geometry.upper
    assert rim.role == "upper"
    assert (rim.start_price, rim.end_price) == (
        formation.components[0].price,
        formation.components[4].price,
    )
    assert (rim.start_time, rim.end_time) == (
        formation.components[0].timestamp,
        formation.components[4].timestamp,
    )
    text = " ".join(event.reason_detail for event in formation.events).lower()
    assert "zone" not in text
    assert "neckline" not in text
    assert "support" not in text and "resistance" not in text


# --- family table ------------------------------------------------------------


def test_every_family_declares_exact_minimum_components_and_sides():
    expected = {
        ChartGeometryFamily.BULL_FLAG: (5, "above", "below", False),
        ChartGeometryFamily.BEAR_FLAG: (5, "below", "above", False),
        ChartGeometryFamily.ASCENDING_TRIANGLE: (4, "either", "none", True),
        ChartGeometryFamily.DESCENDING_TRIANGLE: (4, "either", "none", True),
        ChartGeometryFamily.SYMMETRICAL_TRIANGLE: (4, "either", "none", True),
        ChartGeometryFamily.RISING_WEDGE: (4, "either", "none", True),
        ChartGeometryFamily.FALLING_WEDGE: (4, "either", "none", True),
        ChartGeometryFamily.CUP_AND_HANDLE: (8, "above", "below", False),
    }
    assert set(expected) == set(ChartGeometryFamily)
    for family, (count, breakout, failure, converging) in expected.items():
        definition = family.definition()
        assert minimum_component_count(family) == count
        assert definition.breakout_side == breakout
        assert definition.failure_side == failure
        assert definition.requires_convergence is converging
        assert count == len(definition.pivot_kinds or ()) or definition.pivot_kinds is None


def test_family_definitions_are_geometry_not_probability_or_profit():
    forbidden = ("probabilit", "likely", "profit", "win rate", "target", "signal", "% chance")
    for family in ChartGeometryFamily:
        text = family.definition_text().lower()
        assert text, family
        for word in forbidden:
            assert word not in text, (family.value, word)
