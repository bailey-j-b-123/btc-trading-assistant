"""The five converging families: three triangles and two wedges.

All five share one component template — four consecutive confirmed swings, two
highs and two lows — and one boundary construction: the upper line is anchored on
the two swing highs and the lower line on the two swing lows, in chronological
order. They differ only in the slope conditions of those two lines, which the
parameter validation makes mutually exclusive:

``ascending_triangle``   upper flat, lower rising
``descending_triangle``  lower flat, upper falling
``symmetrical_triangle`` upper falling, lower rising
``rising_wedge``         both rising, upper rising more slowly (converging)
``falling_wedge``        both falling, upper falling more slowly (converging)

Every one of them additionally requires genuine convergence: distinct slopes, an
apex strictly after the last component, and a strictly decreasing gap. A
rectangle (both boundaries flat) matches no family here and is reported as a
rejection with its exact reason, not as a triangle.

These families describe a contraction and the direction actually observed when a
closed candle leaves it. They are not forecasts: chart-geometry-v1 never claims
that a rising wedge resolves down, that a falling wedge resolves up, or that any
triangle resolves in the direction of its flat side.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from trading_assistant.chart_geometry.checks import (
    REASON_ANCHOR_CANDLE_MISSING,
    REASON_BOUNDARIES_NOT_ORDERED,
    REASON_NOT_ALTERNATING,
    REASON_SPAN_OUTSIDE_BOUNDS,
    REASON_SLOPES_MATCH_NO_FAMILY,
    CandleIndex,
    RejectionTally,
    build_component,
    build_geometry,
    candle_metric,
    converging_roles,
    line_from_components,
    make_candidate,
    percent_metric,
    price_metric,
    slope_pct_per_candle,
    window_rejection,
)
from trading_assistant.chart_geometry.families import (
    DEFINITION_BY_FAMILY,
    ChartGeometryFamily,
)
from trading_assistant.chart_geometry.lines import BoundaryLine, gap_at
from trading_assistant.chart_geometry.models import (
    CandidateFormation,
    GeometryComponent,
    GeometryMetric,
)
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_structure.numeric import percentage_of
from trading_assistant.market_structure.swings import SwingKind, SwingPoint

#: Exact minimum component count for every converging family: two anchor pivots
#: per boundary line, alternating.
CONVERGING_WINDOW_SIZE = 4


def classify(
    upper_travel_pct: Decimal,
    lower_travel_pct: Decimal,
    parameters: ChartGeometryParameters,
) -> ChartGeometryFamily | None:
    """Return the one converging family these two boundary travels describe.

    ``None`` means the window is a genuine four-swing geometry that no
    chart-geometry-v1 family defines (a rectangle, a boundary that travels too
    little to be sloped but too much to be flat, or a mixed combination).
    """

    flat = parameters.flat_boundary_max_travel_pct
    sloped = parameters.min_boundary_travel_pct
    upper_flat = abs(upper_travel_pct) <= flat
    lower_flat = abs(lower_travel_pct) <= flat
    upper_rising = upper_travel_pct >= sloped
    upper_falling = upper_travel_pct <= -sloped
    lower_rising = lower_travel_pct >= sloped
    lower_falling = lower_travel_pct <= -sloped
    if upper_flat and lower_rising:
        return ChartGeometryFamily.ASCENDING_TRIANGLE
    if lower_flat and upper_falling:
        return ChartGeometryFamily.DESCENDING_TRIANGLE
    if upper_falling and lower_rising:
        return ChartGeometryFamily.SYMMETRICAL_TRIANGLE
    if upper_rising and lower_rising:
        return ChartGeometryFamily.RISING_WEDGE
    if upper_falling and lower_falling:
        return ChartGeometryFamily.FALLING_WEDGE
    return None


def _metrics(
    upper: BoundaryLine, lower: BoundaryLine, gaps: tuple[Decimal, ...]
) -> tuple[GeometryMetric, ...]:
    """Exact measured numbers for a converging candidate, in a fixed order."""

    return (
        percent_metric(
            "upper_travel_pct",
            upper.travel_pct,
            "upper boundary travel over its own two-pivot span, percent of the anchors' mean price",
        ),
        percent_metric(
            "lower_travel_pct",
            lower.travel_pct,
            "lower boundary travel over its own two-pivot span, percent of the anchors' mean price",
        ),
        percent_metric(
            "upper_slope_pct_per_candle",
            slope_pct_per_candle(upper),
            "upper boundary slope per candle, percent of its first anchor price",
        ),
        percent_metric(
            "lower_slope_pct_per_candle",
            slope_pct_per_candle(lower),
            "lower boundary slope per candle, percent of its first anchor price",
        ),
        percent_metric(
            "contraction_pct",
            percentage_of(gaps[0] - gaps[-1], gaps[0]),
            "share of the gap at the first component removed by the last component",
        ),
        price_metric(
            "gap_at_first_component",
            gaps[0],
            "exact price distance between the frozen boundaries at the first component",
        ),
        price_metric(
            "gap_at_last_component",
            gaps[-1],
            "exact price distance between the frozen boundaries at the last component",
        ),
        candle_metric(
            "upper_span_candles",
            upper.span_candles,
            "candles between the upper boundary's two anchor pivots",
        ),
        candle_metric(
            "lower_span_candles",
            lower.span_candles,
            "candles between the lower boundary's two anchor pivots",
        ),
    )


def converging_candidates(
    swings: Sequence[SwingPoint],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> list[CandidateFormation]:
    """Return every converging candidate in one confirmed-swing sequence.

    Windows are evaluated left to right over *consecutive* confirmed swings. No
    swing is ever skipped to make a window fit, and no window is evaluated across
    a candle gap.
    """

    interval = index.interval
    results: list[CandidateFormation] = []
    for offset in range(len(swings) - CONVERGING_WINDOW_SIZE + 1):
        window = tuple(swings[offset : offset + CONVERGING_WINDOW_SIZE])
        tally.windows_evaluated += 1
        reason = window_rejection(window, index)
        if reason is not None:
            tally.reject(None, reason)
            continue

        seen_highs = 0
        seen_lows = 0
        components: list[GeometryComponent] = []
        for swing in window:
            components.append(
                build_component(swing, converging_roles(swing, seen_highs, seen_lows))
            )
            if swing.kind is SwingKind.HIGH:
                seen_highs += 1
            else:
                seen_lows += 1
        if seen_highs != 2 or seen_lows != 2:  # pragma: no cover - alternation guarantees 2/2
            tally.reject(None, REASON_NOT_ALTERNATING)
            continue

        times: tuple[datetime, ...] = tuple(swing.timestamp for swing in window)
        span = index.span_candles(times[0], times[-1])
        if not parameters.min_span_candles <= span <= parameters.max_span_candles:
            tally.reject(None, REASON_SPAN_OUTSIDE_BOUNDS)
            continue

        by_role = {component.role: component for component in components}
        upper = line_from_components("upper", by_role["upper_a"], by_role["upper_b"], interval)
        lower = line_from_components("lower", by_role["lower_a"], by_role["lower_b"], interval)
        gaps = tuple(gap_at(upper, lower, when) for when in times)
        if any(gap <= 0 for gap in gaps):
            tally.reject(None, REASON_BOUNDARIES_NOT_ORDERED)
            continue

        family = classify(upper.travel_pct, lower.travel_pct, parameters)
        if family is None:
            tally.reject(None, REASON_SLOPES_MATCH_NO_FAMILY)
            continue
        definition = DEFINITION_BY_FAMILY[family]
        geometry, geometry_reason = build_geometry(
            upper=upper,
            lower=lower,
            components=tuple(components),
            gap_times=times,
            parameters=parameters,
            metrics=_metrics(upper, lower, gaps),
            require_convergence=True,
        )
        if geometry is None:
            tally.reject(family, geometry_reason or REASON_BOUNDARIES_NOT_ORDERED)
            continue
        candidate = make_candidate(
            family=family,
            components=tuple(components),
            geometry=geometry,
            breakout_side=definition.breakout_side,
            failure_side=definition.failure_side,
            index=index,
        )
        if candidate is None:
            tally.reject(family, REASON_ANCHOR_CANDLE_MISSING)
            continue
        tally.accepted += 1
        results.append(candidate)
    return results
