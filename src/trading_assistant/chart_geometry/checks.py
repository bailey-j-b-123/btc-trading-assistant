"""Shared, exact geometry checks and the candidate record.

Everything here operates on closed candles and confirmed swings only. Checks
reject a candidate with an explicit machine-readable reason; they never repair a
gap, never bridge a missing candle, never relax a threshold, and never invent a
component so that a window fits a family.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from trading_assistant.chart_geometry.families import (
    CONVERGING_ROLES_HIGH,
    CONVERGING_ROLES_LOW,
    BreakoutSide,
    ChartGeometryFamily,
    FailureSide,
)
from trading_assistant.chart_geometry.lines import (
    BoundaryLine,
    boundary_line,
    convergence,
    gap_at,
    gap_pct_at,
)
from trading_assistant.chart_geometry.models import (
    CandidateFormation,
    FormationGeometry,
    GeometryComponent,
    GeometryMetric,
)
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.numeric import divide, quantize_derived
from trading_assistant.market_structure.swings import SwingKind, SwingPoint

# --- Exact rejection reasons -------------------------------------------------
# Every rejection a detector can emit is named here so tests and diagnostics can
# assert on the reason itself instead of on prose.

REASON_GAP_INSIDE_FORMATION = "candle_gap_inside_formation"
REASON_NOT_ALTERNATING = "component_swings_not_alternating"
REASON_SPAN_OUTSIDE_BOUNDS = "component_span_outside_bounds"
REASON_BOUNDARIES_NOT_ORDERED = "boundaries_not_ordered_at_a_component"
REASON_GAP_BELOW_MINIMUM = "boundary_gap_below_minimum"
REASON_BOUNDARIES_PARALLEL = "boundaries_parallel"
REASON_BOUNDARIES_NOT_CONVERGING = "boundaries_not_converging"
REASON_APEX_NOT_AHEAD = "apex_not_after_last_component"
REASON_GAPS_NOT_DECREASING = "boundary_gaps_not_decreasing"
REASON_SLOPES_MATCH_NO_FAMILY = "boundary_slopes_match_no_v1_family"
REASON_POLE_TOO_SMALL = "pole_change_below_minimum"
REASON_POLE_SPAN_OUTSIDE_BOUNDS = "pole_span_outside_bounds"
REASON_CONSOLIDATION_OUTSIDE_POLE = "consolidation_outside_pole_range"
REASON_RETRACE_OUTSIDE_BOUNDS = "flag_retrace_outside_bounds"
REASON_CONSOLIDATION_SPAN_OUTSIDE_BOUNDS = "consolidation_span_outside_bounds"
REASON_BOUNDARY_TRAVELS_WITH_POLE = "flag_boundary_travels_with_pole"
REASON_CONSOLIDATION_EXPANDING = "flag_consolidation_expanding"
REASON_RIMS_NOT_COMPARABLE = "cup_rims_not_comparable"
REASON_INTERIOR_ABOVE_RIM = "cup_interior_pivot_not_below_rim_line"
REASON_DEPTH_BELOW_MINIMUM = "cup_depth_below_minimum"
REASON_TROUGH_NOT_ROUNDED = "cup_trough_base_not_rounded"
REASON_INTERIOR_NOT_PROMINENT = "cup_interior_high_not_prominent"
REASON_LEG_TOO_SHORT = "cup_leg_shorter_than_minimum"
REASON_RECOVERY_BELOW_MINIMUM = "cup_recovery_below_minimum"
REASON_HANDLE_NOT_BELOW_RIM = "handle_high_not_below_right_rim"
REASON_HANDLE_RETRACE_OUTSIDE_BOUNDS = "handle_retrace_outside_bounds"
REASON_HANDLE_SPAN_OUTSIDE_BOUNDS = "handle_span_outside_bounds"
REASON_HANDLE_EXPANDING = "handle_expanding"
REASON_ANCHOR_CANDLE_MISSING = "component_confirming_candle_missing"
REASON_GAP_AFTER_FORMATION = "candle_gap_between_formation_and_event_anchor"


@dataclass(slots=True)
class RejectionTally:
    """Deterministic accounting of evaluated windows and exact rejection reasons."""

    windows_evaluated: int = 0
    accepted: int = 0
    _counts: dict[tuple[ChartGeometryFamily | None, str], int] = field(default_factory=dict)

    def reject(self, family: ChartGeometryFamily | None, reason: str) -> None:
        key = (family, reason)
        self._counts[key] = self._counts.get(key, 0) + 1

    def counts(self) -> tuple[tuple[ChartGeometryFamily | None, str, int], ...]:
        """Return counts sorted by (family name or empty, reason) for stability."""

        return tuple(
            (family, reason, self._counts[(family, reason)])
            for family, reason in sorted(
                self._counts, key=lambda key: (key[0].value if key[0] is not None else "", key[1])
            )
        )


@dataclass(frozen=True, slots=True)
class CandleIndex:
    """Read-only lookup over the validated closed-candle prefix."""

    candles: tuple[Candle, ...]
    interval: timedelta
    by_time: dict[datetime, Candle]

    @classmethod
    def build(cls, candles: Sequence[Candle], interval: timedelta) -> "CandleIndex":
        return cls(candles=tuple(candles), interval=interval, by_time={c.timestamp: c for c in candles})

    def at(self, when: datetime) -> Candle | None:
        return self.by_time.get(when)

    def previous(self, when: datetime) -> Candle | None:
        return self.by_time.get(when - self.interval)

    def span_candles(self, start: datetime, end: datetime) -> int:
        """Return the exact whole-candle distance between two grid-aligned times."""

        delta = end - start
        if delta % self.interval != timedelta(0):
            raise ValueError("candle span requires grid-aligned timestamps")
        return int(delta // self.interval)

    def is_contiguous(self, start: datetime, end: datetime) -> bool:
        """Return whether every candle between two times is actually present."""

        expected = self.span_candles(start, end) + 1
        present = sum(1 for candle in self.candles if start <= candle.timestamp <= end)
        return present == expected

    def closed_after(self, when: datetime) -> tuple[Candle, ...]:
        """Return closed candles whose close instant is strictly after ``when``."""

        return tuple(c for c in self.candles if c.timestamp + self.interval > when)

    def contiguous_run_from(self, start: datetime) -> tuple[Candle, ...]:
        """Return the maximal gap-free run of closed candles starting at ``start``.

        The run stops at the first missing candle: it never bridges a hole in the
        history and never re-spaced a series. A formation's lifecycle is
        evaluated only inside its own run, so no confirmation or invalidation is
        ever taken across unseen history.
        """

        first = self.by_time.get(start)
        if first is None:
            return ()
        position = self.candles.index(first)
        run = [first]
        for candle in self.candles[position + 1 :]:
            if candle.timestamp - run[-1].timestamp != self.interval:
                break
            run.append(candle)
        return tuple(run)


def build_component(swing: SwingPoint, role: str) -> GeometryComponent:
    """Wrap one confirmed swing as a formation component, keeping its timing."""

    return GeometryComponent(
        role=role,
        pivot_kind=swing.kind,
        timestamp=swing.timestamp,
        price=swing.price,
        confirmed_at=swing.confirmed_at,
        confirmed_by_timestamp=swing.confirmed_by_timestamp,
        swing=swing,
    )


def converging_roles(swing: SwingPoint, seen_highs: int, seen_lows: int) -> str:
    """Return the boundary role of one swing in a converging four-swing window."""

    if swing.kind is SwingKind.HIGH:
        return CONVERGING_ROLES_HIGH[seen_highs]
    return CONVERGING_ROLES_LOW[seen_lows]


def line_from_components(
    role: str,
    first: GeometryComponent,
    second: GeometryComponent,
    interval: timedelta,
) -> BoundaryLine:
    """Return the boundary line anchored on two actual component pivots."""

    return boundary_line(
        role=role,  # type: ignore[arg-type]
        start_time=first.timestamp,
        start_price=first.price,
        end_time=second.timestamp,
        end_price=second.price,
        interval=interval,
    )


def percent_metric(name: str, value: Decimal, basis: str) -> GeometryMetric:
    return GeometryMetric(name=name, value=value, unit="percent", basis=basis)


def candle_metric(name: str, value: int, basis: str) -> GeometryMetric:
    return GeometryMetric(name=name, value=Decimal(value), unit="candles", basis=basis)


def price_metric(name: str, value: Decimal, basis: str) -> GeometryMetric:
    return GeometryMetric(name=name, value=value, unit="price", basis=basis)


def slope_pct_per_candle(line: BoundaryLine) -> Decimal:
    """Return a boundary's slope as a percent of its start price per candle."""

    return quantize_derived(
        divide(line.slope_per_candle * Decimal(100), line.start_price)
    )


def window_rejection(
    window: Sequence[SwingPoint], index: CandleIndex
) -> str | None:
    """Return why a window of consecutive confirmed swings cannot be a formation.

    Two rules, both family-agnostic:

    * the pivots must strictly alternate in kind and strictly increase in time,
      so a window never contains two highs in a row, two lows in a row, or two
      pivots on the same candle (a candle can be both a swing high and a swing
      low; such a window is rejected rather than silently re-ordered);
    * every candle between the first and the last pivot must actually be
      present, so no formation is ever described across missing history.
    """

    for left, right in zip(window, window[1:]):
        if left.kind == right.kind:
            return REASON_NOT_ALTERNATING
        if left.timestamp >= right.timestamp:
            return REASON_NOT_ALTERNATING
    if not index.is_contiguous(window[0].timestamp, window[-1].timestamp):
        return REASON_GAP_INSIDE_FORMATION
    return None


def build_geometry(
    *,
    upper: BoundaryLine,
    lower: BoundaryLine,
    components: tuple[GeometryComponent, ...],
    gap_times: tuple[datetime, ...],
    parameters: ChartGeometryParameters,
    metrics: tuple[GeometryMetric, ...],
    require_convergence: bool,
    require_non_expanding: bool = False,
    non_expanding_reason: str = REASON_GAPS_NOT_DECREASING,
) -> tuple[FormationGeometry | None, str | None]:
    """Return the frozen geometry, or ``None`` with the exact rejection reason.

    Checks performed for every family, at each time in ``gap_times`` (the region
    the two lines actually bound):

    1. the upper line is strictly above the lower line (a crossed or touching
       geometry is degenerate, not a formation);
    2. the gap is at least ``min_boundary_gap_pct`` of the two lines' mid price,
       so nearby levels are never conflated into one bounded region.

    When ``require_convergence`` is true, additionally:

    3. the two slopes differ (parallel boundaries have no apex);
    4. the upper line rises more slowly / falls more quickly than the lower one,
       and the apex lies strictly after the last component;
    5. the gap strictly decreases across ``gap_times`` in chronological order.

    When ``require_non_expanding`` is true, the gap at the last ``gap_times``
    entry must not exceed the gap at the first (flags and handles contract or
    stay parallel; they never widen).
    """

    times = tuple(component.timestamp for component in components)
    first_time, last_time = times[0], times[-1]
    interval = timedelta(seconds=upper.interval_seconds)

    gaps = tuple(gap_at(upper, lower, when) for when in gap_times)
    if any(gap <= 0 for gap in gaps):
        return None, REASON_BOUNDARIES_NOT_ORDERED
    gap_pcts = tuple(gap_pct_at(upper, lower, when) for when in gap_times)
    if any(gap_pct < parameters.min_boundary_gap_pct for gap_pct in gap_pcts):
        return None, REASON_GAP_BELOW_MINIMUM

    converged = convergence(upper, lower, last_component_time=last_time)
    if require_convergence:
        if converged.parallel:
            return None, REASON_BOUNDARIES_PARALLEL
        if not converged.converging:
            if not converged.apex_after_last_component:
                return None, REASON_APEX_NOT_AHEAD
            return None, REASON_BOUNDARIES_NOT_CONVERGING
        if any(right >= left for left, right in zip(gaps, gaps[1:])):
            return None, REASON_GAPS_NOT_DECREASING
    elif require_non_expanding and gaps[-1] > gaps[0]:
        return None, non_expanding_reason

    span = int((last_time - first_time) // interval)
    geometry = FormationGeometry(
        upper=upper,
        lower=lower,
        first_component_time=first_time,
        last_component_time=last_time,
        span_candles=span,
        region_start_time=gap_times[0],
        region_end_time=gap_times[-1],
        gap_at_region_start=gaps[0],
        gap_at_region_end=gaps[-1],
        gap_pct_at_region_start=gap_pcts[0],
        gap_pct_at_region_end=gap_pcts[-1],
        min_gap_pct=min(gap_pcts),
        convergence=converged,
        metrics=metrics,
    )
    return geometry, None



def make_candidate(
    *,
    family: ChartGeometryFamily,
    components: tuple[GeometryComponent, ...],
    geometry: FormationGeometry,
    breakout_side: BreakoutSide,
    failure_side: FailureSide,
    index: CandleIndex,
) -> CandidateFormation | None:
    """Freeze one candidate formation, anchored on a real closed candle.

    The formation's ``formed_at`` is the latest component confirmation instant,
    and its anchor candle is the real closed candle whose close established that
    instant. If that candle is not in the closed prefix, the candidate is
    rejected rather than reported without an anchor.
    """

    formed_at = max(component.confirmed_at for component in components)
    anchor_time = formed_at - index.interval
    anchor = index.at(anchor_time)
    if anchor is None:
        return None
    return CandidateFormation(
        family=family,
        components=components,
        geometry=geometry,
        breakout_side=breakout_side,
        failure_side=failure_side,
        formed_at=formed_at,
        formation_anchor=anchor,
    )
