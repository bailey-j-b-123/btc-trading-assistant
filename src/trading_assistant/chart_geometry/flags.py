"""``bull_flag`` and ``bear_flag``: a real pole followed by a constrained flag.

A flag is **five consecutive confirmed swings**:

``bull_flag``   low, high, low, high, low
``bear_flag``   high, low, high, low, high

The first two swings are the *pole*; the last three are the *flag body*. The
definition of the pole is explicit and conservative:

1. the pole must actually move in the family's direction (a bull flag's pole ends
   above where it started; a bear flag's ends below);
2. the move must be at least ``pole_min_change_pct`` of the pole's starting pivot
   price — a flag can never be described on a drift;
3. the pole leg must be between ``pole_min_candles`` and ``pole_max_candles`` long.

The flag body must be *constrained*:

4. every body pivot stays inside the pole's own price range (strictly beyond the
   pole's start, never beyond the pole's end), so the body is a pullback inside
   the move and not a continuation or a reversal;
5. the retracement — the share of the pole move given back by the deepest body
   pivot — is between ``flag_min_retrace_pct`` and ``flag_max_retrace_pct``. A
   body that gives nothing back is not a flag; one that gives back more than the
   configured maximum is a reversal, not a flag;
6. the body spans between ``flag_min_consolidation_candles`` and
   ``flag_max_consolidation_candles``;
7. the two body boundary lines do not travel in the pole's direction by more than
   ``flat_boundary_max_travel_pct`` (a flag drifts flat or against its pole);
8. the body never widens: the gap between the two lines at the last body pivot is
   at most the gap at the first.

Boundary lines are anchored on real pivots only. For a bull flag the upper line
runs from the pole's high pivot to the body's high pivot and the lower line
through the body's two low pivots; a bear flag mirrors this. Neither line is ever
extended from a single pivot, copied from another detector, or treated as a zone.

The pole's direction names which crossing this package calls a confirmation
(``above`` for a bull flag, ``below`` for a bear flag) and which it calls a
failure. That is a statement about the geometry's own composition, not a
prediction: the observed direction of the actual closed candle is always recorded
alongside it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from trading_assistant.chart_geometry.checks import (
    REASON_ANCHOR_CANDLE_MISSING,
    REASON_BOUNDARIES_NOT_ORDERED,
    REASON_BOUNDARY_TRAVELS_WITH_POLE,
    REASON_CONSOLIDATION_EXPANDING,
    REASON_CONSOLIDATION_OUTSIDE_POLE,
    REASON_CONSOLIDATION_SPAN_OUTSIDE_BOUNDS,
    REASON_POLE_SPAN_OUTSIDE_BOUNDS,
    REASON_POLE_TOO_SMALL,
    REASON_RETRACE_OUTSIDE_BOUNDS,
    REASON_SPAN_OUTSIDE_BOUNDS,
    CandleIndex,
    RejectionTally,
    build_component,
    build_geometry,
    candle_metric,
    line_from_components,
    make_candidate,
    percent_metric,
    price_metric,
    window_rejection,
)
from trading_assistant.chart_geometry.families import (
    DEFINITION_BY_FAMILY,
    FLAG_FAMILIES,
    ChartGeometryFamily,
)
from trading_assistant.chart_geometry.lines import gap_at
from trading_assistant.chart_geometry.models import CandidateFormation, GeometryComponent
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_structure.numeric import percentage_of
from trading_assistant.market_structure.swings import SwingPoint

#: Exact minimum component count for both flag families: two pole pivots plus
#: three body pivots.
FLAG_WINDOW_SIZE = 5


def _bull_flag(
    components: tuple[GeometryComponent, ...],
    times: tuple[datetime, ...],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> CandidateFormation | None:
    family = ChartGeometryFamily.BULL_FLAG
    pole_start, pole_end, low_a, high, low_b = components
    pole_move = pole_end.price - pole_start.price
    if pole_move <= 0:
        tally.reject(family, REASON_POLE_TOO_SMALL)
        return None
    pole_change_pct = percentage_of(pole_move, pole_start.price)
    if pole_change_pct < parameters.pole_min_change_pct:
        tally.reject(family, REASON_POLE_TOO_SMALL)
        return None
    pole_span = index.span_candles(pole_start.timestamp, pole_end.timestamp)
    if not parameters.pole_min_candles <= pole_span <= parameters.pole_max_candles:
        tally.reject(family, REASON_POLE_SPAN_OUTSIDE_BOUNDS)
        return None

    body = (low_a, high, low_b)
    if any(p.price <= pole_start.price or p.price > pole_end.price for p in body):
        tally.reject(family, REASON_CONSOLIDATION_OUTSIDE_POLE)
        return None
    deepest = min(low_a.price, low_b.price)
    retraction = pole_end.price - deepest
    retrace_pct = percentage_of(retraction, pole_move)
    if not parameters.flag_min_retrace_pct <= retrace_pct <= parameters.flag_max_retrace_pct:
        tally.reject(family, REASON_RETRACE_OUTSIDE_BOUNDS)
        return None
    body_span = index.span_candles(low_a.timestamp, low_b.timestamp)
    if not (
        parameters.flag_min_consolidation_candles
        <= body_span
        <= parameters.flag_max_consolidation_candles
    ):
        tally.reject(family, REASON_CONSOLIDATION_SPAN_OUTSIDE_BOUNDS)
        return None

    upper = line_from_components("upper", pole_end, high, index.interval)
    lower = line_from_components("lower", low_a, low_b, index.interval)
    flat = parameters.flat_boundary_max_travel_pct
    if upper.travel_pct > flat or lower.travel_pct > flat:
        tally.reject(family, REASON_BOUNDARY_TRAVELS_WITH_POLE)
        return None

    metrics = (
        percent_metric(
            "pole_change_pct",
            pole_change_pct,
            "pole move from the pole's low pivot, percent of that pivot's price",
        ),
        candle_metric("pole_span_candles", pole_span, "candles between the pole's two pivots"),
        price_metric("pole_move", pole_move, "exact price distance of the pole"),
        percent_metric(
            "flag_retrace_pct",
            retrace_pct,
            "share of the pole move given back by the deepest body pivot",
        ),
        candle_metric("consolidation_span_candles", body_span, "candles spanned by the flag body"),
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
    )
    return _finish(
        family,
        components,
        times,
        upper=upper,
        lower=lower,
        metrics=metrics,
        body_times=(low_a.timestamp, high.timestamp, low_b.timestamp),
        index=index,
        parameters=parameters,
        tally=tally,
    )


def _bear_flag(
    components: tuple[GeometryComponent, ...],
    times: tuple[datetime, ...],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> CandidateFormation | None:
    family = ChartGeometryFamily.BEAR_FLAG
    pole_start, pole_end, high_a, low, high_b = components
    pole_move = pole_start.price - pole_end.price
    if pole_move <= 0:
        tally.reject(family, REASON_POLE_TOO_SMALL)
        return None
    pole_change_pct = percentage_of(pole_move, pole_start.price)
    if pole_change_pct < parameters.pole_min_change_pct:
        tally.reject(family, REASON_POLE_TOO_SMALL)
        return None
    pole_span = index.span_candles(pole_start.timestamp, pole_end.timestamp)
    if not parameters.pole_min_candles <= pole_span <= parameters.pole_max_candles:
        tally.reject(family, REASON_POLE_SPAN_OUTSIDE_BOUNDS)
        return None

    body = (high_a, low, high_b)
    if any(p.price < pole_end.price or p.price >= pole_start.price for p in body):
        tally.reject(family, REASON_CONSOLIDATION_OUTSIDE_POLE)
        return None
    highest = max(high_a.price, high_b.price)
    retraction = highest - pole_end.price
    retrace_pct = percentage_of(retraction, pole_move)
    if not parameters.flag_min_retrace_pct <= retrace_pct <= parameters.flag_max_retrace_pct:
        tally.reject(family, REASON_RETRACE_OUTSIDE_BOUNDS)
        return None
    body_span = index.span_candles(high_a.timestamp, high_b.timestamp)
    if not (
        parameters.flag_min_consolidation_candles
        <= body_span
        <= parameters.flag_max_consolidation_candles
    ):
        tally.reject(family, REASON_CONSOLIDATION_SPAN_OUTSIDE_BOUNDS)
        return None

    upper = line_from_components("upper", high_a, high_b, index.interval)
    lower = line_from_components("lower", pole_end, low, index.interval)
    flat = parameters.flat_boundary_max_travel_pct
    if upper.travel_pct < -flat or lower.travel_pct < -flat:
        tally.reject(family, REASON_BOUNDARY_TRAVELS_WITH_POLE)
        return None

    metrics = (
        percent_metric(
            "pole_change_pct",
            pole_change_pct,
            "pole move from the pole's high pivot, percent of that pivot's price",
        ),
        candle_metric("pole_span_candles", pole_span, "candles between the pole's two pivots"),
        price_metric("pole_move", pole_move, "exact price distance of the pole"),
        percent_metric(
            "flag_retrace_pct",
            retrace_pct,
            "share of the pole move retraced by the highest body pivot",
        ),
        candle_metric("consolidation_span_candles", body_span, "candles spanned by the flag body"),
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
    )
    return _finish(
        family,
        components,
        times,
        upper=upper,
        lower=lower,
        metrics=metrics,
        body_times=(high_a.timestamp, low.timestamp, high_b.timestamp),
        index=index,
        parameters=parameters,
        tally=tally,
    )


def _finish(
    family: ChartGeometryFamily,
    components: tuple[GeometryComponent, ...],
    times: tuple[datetime, ...],
    *,
    upper,
    lower,
    metrics,
    body_times: tuple[datetime, ...],
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> CandidateFormation | None:
    """Apply the shared flag gap rules and freeze the candidate."""

    definition = DEFINITION_BY_FAMILY[family]
    body_gaps = tuple(gap_at(upper, lower, when) for when in body_times)
    if any(gap <= 0 for gap in body_gaps):
        tally.reject(family, REASON_BOUNDARIES_NOT_ORDERED)
        return None
    if body_gaps[-1] > body_gaps[0]:
        tally.reject(family, REASON_CONSOLIDATION_EXPANDING)
        return None
    geometry, reason = build_geometry(
        upper=upper,
        lower=lower,
        components=components,
        gap_times=body_times,
        parameters=parameters,
        metrics=metrics,
        require_convergence=False,
        require_non_expanding=True,
        non_expanding_reason=REASON_CONSOLIDATION_EXPANDING,
    )
    if geometry is None:
        tally.reject(family, reason or REASON_CONSOLIDATION_EXPANDING)
        return None
    span = index.span_candles(times[0], times[-1])
    if not parameters.min_span_candles <= span <= parameters.max_span_candles:
        tally.reject(family, REASON_SPAN_OUTSIDE_BOUNDS)
        return None
    candidate = make_candidate(
        family=family,
        components=components,
        geometry=geometry,
        breakout_side=definition.breakout_side,
        failure_side=definition.failure_side,
        index=index,
    )
    if candidate is None:
        tally.reject(family, REASON_ANCHOR_CANDLE_MISSING)
        return None
    tally.accepted += 1
    return candidate


def flag_candidates(
    swings: Sequence[SwingPoint],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> list[CandidateFormation]:
    """Return every flag candidate in one confirmed-swing sequence."""

    results: list[CandidateFormation] = []
    for offset in range(len(swings) - FLAG_WINDOW_SIZE + 1):
        window = tuple(swings[offset : offset + FLAG_WINDOW_SIZE])
        tally.windows_evaluated += 1
        reason = window_rejection(window, index)
        if reason is not None:
            tally.reject(None, reason)
            continue
        kinds = tuple(swing.kind for swing in window)
        times = tuple(swing.timestamp for swing in window)
        for family in FLAG_FAMILIES:
            definition = DEFINITION_BY_FAMILY[family]
            roles = definition.roles
            if definition.pivot_kinds is None or roles is None or kinds != definition.pivot_kinds:
                continue
            components = tuple(
                build_component(swing, role)
                for swing, role in zip(window, roles)
            )
            builder = _bull_flag if family is ChartGeometryFamily.BULL_FLAG else _bear_flag
            candidate = builder(
                components, times, index=index, parameters=parameters, tally=tally
            )
            if candidate is not None:
                results.append(candidate)
    return results
