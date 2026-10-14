"""``cup_and_handle``: explicit rims, a rounded trough, recovery, and a handle.

This family needs **eight consecutive confirmed swings** in the exact order
high, low, high, low, high, low, high, low, with these roles:

``left_rim``      the first rim high
``trough_low_a``  first trough low
``trough_high``   the interior high between the two trough lows
``trough_low_b``  second trough low
``right_rim``     the second rim high, after the recovery
``handle_low_a``  first handle low
``handle_high``   the handle's bounce high
``handle_low_b``  second handle low

The definition, check by check:

1. **Rims.** The rim line is the exact line through the two rim pivots. The two
   rim prices must be within ``cup_rim_tolerance_pct`` of each other. This line
   is *not* a neckline from any other detector and is never equated with a Step 3
   support/resistance zone: it is one line through two named pivots.
2. **Interior below the rim.** Every interior pivot (both trough lows, the
   interior high, and all three handle pivots) must sit strictly below the rim
   line evaluated at that pivot's own time.
3. **Depth.** The deepest trough low must be at least ``cup_min_depth_pct`` below
   the rim line value at its own time.
4. **Rounded base.** The two trough lows must be within
   ``cup_trough_base_tolerance_pct`` of each other, and the interior high must
   rise above the higher of them by at least ``cup_min_interior_prominence_pct``
   of the cup depth. A single spike low with nothing beside it is not a rounded
   trough.
5. **Legs.** The descent (left rim to the deepest trough low) and the ascent
   (deepest trough low to the right rim) must each span at least
   ``cup_min_leg_candles`` candles. This is what excludes a V-shaped trough.
6. **Recovery.** The right rim must recover at least ``cup_min_recovery_pct`` of
   the descent, measured against (left rim - deepest trough low).
7. **Handle.** The handle must exist: its bounce high is strictly below the right
   rim, its pullback from the right rim is between ``handle_min_retrace_pct`` and
   ``handle_max_retrace_pct`` of the cup depth, and it spans between
   ``handle_min_candles`` and ``handle_max_candles``.
8. **Handle boundaries.** The upper boundary is the frozen rim line; the lower
   boundary is the exact line through the two handle lows. The handle must not
   widen: the gap at the last handle pivot is at most the gap at the first.

Confirmation is a closed candle whose close is above the frozen rim line at that
candle's own open time; failure is a closed candle whose close is below the frozen
handle lower line. Both are recorded with the direction actually observed. A cup
and handle is a geometric description of a rounded recovery followed by a shallow
pullback — it is not a forecast and carries no measured target.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from trading_assistant.chart_geometry.checks import (
    REASON_ANCHOR_CANDLE_MISSING,
    REASON_DEPTH_BELOW_MINIMUM,
    REASON_HANDLE_NOT_BELOW_RIM,
    REASON_HANDLE_RETRACE_OUTSIDE_BOUNDS,
    REASON_HANDLE_SPAN_OUTSIDE_BOUNDS,
    REASON_INTERIOR_ABOVE_RIM,
    REASON_INTERIOR_NOT_PROMINENT,
    REASON_HANDLE_EXPANDING,
    REASON_LEG_TOO_SHORT,
    REASON_NOT_ALTERNATING,
    REASON_RECOVERY_BELOW_MINIMUM,
    REASON_RIMS_NOT_COMPARABLE,
    REASON_SPAN_OUTSIDE_BOUNDS,
    REASON_TROUGH_NOT_ROUNDED,
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
    ChartGeometryFamily,
)
from trading_assistant.chart_geometry.models import CandidateFormation, GeometryComponent
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_structure.numeric import percentage_of, tolerance_band
from trading_assistant.market_structure.swings import SwingPoint

CUP_FAMILY = ChartGeometryFamily.CUP_AND_HANDLE
CUP_DEFINITION = DEFINITION_BY_FAMILY[CUP_FAMILY]

#: Exact minimum component count: two rims, three trough pivots, three handle
#: pivots.
CUP_WINDOW_SIZE = 8


def cup_and_handle_candidates(
    swings: Sequence[SwingPoint],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> list[CandidateFormation]:
    """Return every cup-and-handle candidate in one confirmed-swing sequence."""

    definition = CUP_DEFINITION
    pivot_kinds = definition.pivot_kinds
    roles = definition.roles
    if pivot_kinds is None or roles is None:  # pragma: no cover - table invariant
        raise ValueError("cup_and_handle requires an explicit component template")
    results: list[CandidateFormation] = []
    for offset in range(len(swings) - CUP_WINDOW_SIZE + 1):
        window = tuple(swings[offset : offset + CUP_WINDOW_SIZE])
        tally.windows_evaluated += 1
        reason = window_rejection(window, index)
        if reason is not None:
            tally.reject(None, reason)
            continue
        if tuple(swing.kind for swing in window) != pivot_kinds:
            tally.reject(None, REASON_NOT_ALTERNATING)
            continue
        times: tuple[datetime, ...] = tuple(swing.timestamp for swing in window)
        span = index.span_candles(times[0], times[-1])
        if not parameters.min_span_candles <= span <= parameters.max_span_candles:
            tally.reject(None, REASON_SPAN_OUTSIDE_BOUNDS)
            continue
        candidate = _evaluate(
            tuple(build_component(swing, role) for swing, role in zip(window, roles)),
            index=index,
            parameters=parameters,
            tally=tally,
        )
        if candidate is not None:
            results.append(candidate)
    return results


def _evaluate(
    components: tuple[GeometryComponent, ...],
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    tally: RejectionTally,
) -> CandidateFormation | None:
    (
        left_rim,
        trough_low_a,
        trough_high,
        trough_low_b,
        right_rim,
        handle_low_a,
        handle_high,
        handle_low_b,
    ) = components

    def reject(reason: str) -> None:
        tally.reject(CUP_FAMILY, reason)

    # 1. Rims.
    rim_difference_pct = percentage_of(abs(right_rim.price - left_rim.price), left_rim.price)
    if rim_difference_pct > parameters.cup_rim_tolerance_pct:
        reject(REASON_RIMS_NOT_COMPARABLE)
        return None
    rim = line_from_components("upper", left_rim, right_rim, index.interval)

    # 2. Every interior pivot strictly below the rim line at its own time.
    interior = (
        trough_low_a,
        trough_high,
        trough_low_b,
        handle_low_a,
        handle_high,
        handle_low_b,
    )
    for pivot in interior:
        if pivot.price >= rim.value_at(pivot.timestamp):
            reject(REASON_INTERIOR_ABOVE_RIM)
            return None

    # 3. Depth, measured against the rim line at the deepest trough low's time.
    deepest = (
        trough_low_a if trough_low_a.price <= trough_low_b.price else trough_low_b
    )
    rim_at_deepest = rim.value_at(deepest.timestamp)
    depth = rim_at_deepest - deepest.price
    if depth <= 0:  # pragma: no cover - guarded by the interior-below-rim rule
        reject(REASON_DEPTH_BELOW_MINIMUM)
        return None
    depth_pct = percentage_of(depth, rim_at_deepest)
    if depth_pct < parameters.cup_min_depth_pct:
        reject(REASON_DEPTH_BELOW_MINIMUM)
        return None

    # 4. Rounded base: two comparable lows with a prominent interior high.
    base_difference_pct = percentage_of(
        abs(trough_low_a.price - trough_low_b.price), min(trough_low_a.price, trough_low_b.price)
    )
    if base_difference_pct > parameters.cup_trough_base_tolerance_pct:
        reject(REASON_TROUGH_NOT_ROUNDED)
        return None
    prominence = trough_high.price - max(trough_low_a.price, trough_low_b.price)
    if prominence <= tolerance_band(depth, parameters.cup_min_interior_prominence_pct):
        reject(REASON_INTERIOR_NOT_PROMINENT)
        return None
    prominence_pct = percentage_of(prominence, depth)

    # 5. Legs long enough that the trough is rounded rather than a V.
    descent_candles = index.span_candles(left_rim.timestamp, deepest.timestamp)
    ascent_candles = index.span_candles(deepest.timestamp, right_rim.timestamp)
    if descent_candles < parameters.cup_min_leg_candles:
        reject(REASON_LEG_TOO_SHORT)
        return None
    if ascent_candles < parameters.cup_min_leg_candles:
        reject(REASON_LEG_TOO_SHORT)
        return None

    # 6. Recovery back towards the left rim.
    descent = left_rim.price - deepest.price
    if descent <= 0:
        reject(REASON_RECOVERY_BELOW_MINIMUM)
        return None
    recovery_pct = percentage_of(right_rim.price - deepest.price, descent)
    if recovery_pct < parameters.cup_min_recovery_pct:
        reject(REASON_RECOVERY_BELOW_MINIMUM)
        return None

    # 7. The handle must exist as a constrained pullback below the right rim.
    if handle_high.price >= right_rim.price:
        reject(REASON_HANDLE_NOT_BELOW_RIM)
        return None
    handle_depth = right_rim.price - deepest.price
    if handle_depth <= 0:
        reject(REASON_HANDLE_RETRACE_OUTSIDE_BOUNDS)
        return None
    handle_lowest = min(handle_low_a.price, handle_low_b.price)
    handle_retrace_pct = percentage_of(right_rim.price - handle_lowest, handle_depth)
    if not (
        parameters.handle_min_retrace_pct
        <= handle_retrace_pct
        <= parameters.handle_max_retrace_pct
    ):
        reject(REASON_HANDLE_RETRACE_OUTSIDE_BOUNDS)
        return None
    handle_span = index.span_candles(handle_low_a.timestamp, handle_low_b.timestamp)
    if not parameters.handle_min_candles <= handle_span <= parameters.handle_max_candles:
        reject(REASON_HANDLE_SPAN_OUTSIDE_BOUNDS)
        return None

    # 8. Handle boundaries: rim line above, handle-low line below, not widening.
    handle_lower = line_from_components("lower", handle_low_a, handle_low_b, index.interval)
    cup_span = index.span_candles(left_rim.timestamp, right_rim.timestamp)
    metrics = (
        percent_metric(
            "rim_difference_pct",
            rim_difference_pct,
            "difference between the two rim pivots, percent of the left rim price",
        ),
        percent_metric(
            "cup_depth_pct",
            depth_pct,
            "deepest trough low below the rim line, percent of the rim line at that time",
        ),
        price_metric("cup_depth", depth, "exact price depth of the trough below the rim line"),
        percent_metric(
            "cup_recovery_pct",
            recovery_pct,
            "share of the descent recovered by the right rim",
        ),
        percent_metric(
            "trough_base_difference_pct",
            base_difference_pct,
            "difference between the two trough lows, percent of the lower one",
        ),
        percent_metric(
            "trough_interior_prominence_pct",
            prominence_pct,
            "interior trough high above the higher trough low, percent of the cup depth",
        ),
        candle_metric("descent_leg_candles", descent_candles, "candles from the left rim to the deepest trough low"),
        candle_metric("ascent_leg_candles", ascent_candles, "candles from the deepest trough low to the right rim"),
        candle_metric("cup_span_candles", cup_span, "candles from the left rim to the right rim"),
        percent_metric(
            "handle_retrace_pct",
            handle_retrace_pct,
            "handle pullback from the right rim, percent of the cup depth above the trough",
        ),
        candle_metric("handle_span_candles", handle_span, "candles spanned by the handle"),
        percent_metric(
            "rim_travel_pct",
            rim.travel_pct,
            "rim line travel between the two rim pivots, percent of the rims' mean price",
        ),
    )
    handle_times = (handle_low_a.timestamp, handle_high.timestamp, handle_low_b.timestamp)
    geometry, reason = build_geometry(
        upper=rim,
        lower=handle_lower,
        components=components,
        gap_times=handle_times,
        parameters=parameters,
        metrics=metrics,
        require_convergence=False,
        require_non_expanding=True,
        non_expanding_reason=REASON_HANDLE_EXPANDING,
    )
    if geometry is None:
        reject(reason or REASON_HANDLE_EXPANDING)
        return None
    candidate = make_candidate(
        family=CUP_FAMILY,
        components=components,
        geometry=geometry,
        breakout_side=CUP_DEFINITION.breakout_side,
        failure_side=CUP_DEFINITION.failure_side,
        index=index,
    )
    if candidate is None:
        reject(REASON_ANCHOR_CANDLE_MISSING)
        return None
    tally.accepted += 1
    return candidate
