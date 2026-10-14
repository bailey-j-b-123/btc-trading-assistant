"""Immutable chart-geometry-v1 evidence records.

Every record here is frozen, JSON-serializable through the existing Step 3
projection helper, and free of wall-clock time: the same closed candles, the same
``as_of``, and the same parameters always produce equal records with equal ids.
No record contains a confidence, a probability, a target price, a position size,
or a trade instruction — this package describes geometry that was actually
present in closed candles and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from trading_assistant.chart_geometry.families import (
    BreakoutSide,
    ChartGeometryFamily,
    FailureSide,
)
from trading_assistant.chart_geometry.lines import BoundaryLine, Convergence
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.market_structure.swings import SwingKind, SwingPoint

GeometryState = Literal["formed", "confirmed", "invalidated"]
ObservedDirection = Literal["above", "below"]

MetricUnit = Literal["percent", "candles", "price"]


@dataclass(frozen=True, slots=True)
class GeometryComponent:
    """One confirmed swing that is part of a formation.

    ``timestamp`` is the component's **original pivot candle open time** and
    ``confirmed_at`` is the **actual instant** Step 3 could first know that
    pivot (the close of its right-window candle). Both are retained verbatim, so
    a component can never be presented as known earlier than it was.
    """

    role: str
    pivot_kind: SwingKind
    timestamp: datetime
    price: Decimal
    confirmed_at: datetime
    confirmed_by_timestamp: datetime
    swing: SwingPoint


@dataclass(frozen=True, slots=True)
class GeometryMetric:
    """One exact measured number behind a family's definition."""

    name: str
    value: Decimal
    unit: MetricUnit
    #: What the number is measured against, in words (never an implied claim).
    basis: str


@dataclass(frozen=True, slots=True)
class FormationGeometry:
    """The frozen geometry of one formation.

    Computed once, at the first detection of the formation, and never re-fitted:
    later pivots cannot move these lines, and a formation whose components would
    be extended by later swings is suppressed rather than re-described.
    """

    upper: BoundaryLine
    lower: BoundaryLine
    first_component_time: datetime
    last_component_time: datetime
    span_candles: int
    #: Start/end of the region the two lines actually bound. For the converging
    #: families that is the whole component window; for flags it is the
    #: consolidation and for a cup and handle it is the handle.
    region_start_time: datetime
    region_end_time: datetime
    gap_at_region_start: Decimal
    gap_at_region_end: Decimal
    gap_pct_at_region_start: Decimal
    gap_pct_at_region_end: Decimal
    min_gap_pct: Decimal
    convergence: Convergence
    metrics: tuple[GeometryMetric, ...]

    def metric(self, name: str) -> GeometryMetric:
        """Return one named metric, or raise ``KeyError`` if it is absent."""

        for metric in self.metrics:
            if metric.name == name:
                return metric
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class CandidateFormation:
    """A detected geometry before its lifecycle is evaluated.

    Internal to the package: it carries the frozen geometry, the family's
    boundary-side conventions, the instant the geometry became knowable, and the
    real closed candle whose close confirmed the last component pivot.
    """

    family: ChartGeometryFamily
    components: tuple[GeometryComponent, ...]
    geometry: FormationGeometry
    breakout_side: BreakoutSide
    failure_side: FailureSide
    formed_at: datetime
    formation_anchor: Candle


@dataclass(frozen=True, slots=True)
class ChartGeometryEvent:
    """One lifecycle event: ``formed``, ``confirmed``, or ``invalidated``.

    ``known_at`` is causal: the instant the event could first have been known,
    which for a candle-anchored event is that candle's close time. Every event
    carries a **real closed candle** anchor — for ``formed`` it is the candle
    whose close confirmed the last component pivot.
    """

    event_id: str
    formation_id: str
    family: ChartGeometryFamily
    state: GeometryState
    known_at: datetime
    anchor_candle: Candle
    #: Direction actually observed at the anchor candle, never an expectation.
    observed_direction: ObservedDirection | None
    reason: str
    reason_detail: str
    upper_boundary_value: Decimal
    lower_boundary_value: Decimal
    penetration: Decimal | None
    penetration_pct: Decimal | None
    candles_since_formed: int
    gap_before_anchor: bool
    frozen_geometry_digest: str


@dataclass(frozen=True, slots=True)
class ChartGeometryFormation:
    """One formation with its frozen geometry and its chronological lifecycle."""

    formation_id: str
    family: ChartGeometryFamily
    state: GeometryState
    exchange: str
    symbol: str
    timeframe: str
    rules_version: str
    parameters_fingerprint: str
    components: tuple[GeometryComponent, ...]
    geometry: FormationGeometry
    breakout_side: BreakoutSide
    failure_side: FailureSide
    #: Instant the geometry itself became knowable (last component confirmation).
    formed_at: datetime
    #: Instant the current ``state`` became knowable (last event's ``known_at``).
    known_at: datetime
    first_component_time: datetime
    last_component_time: datetime
    span_candles: int
    observed_direction: ObservedDirection | None
    terminal_reason: str | None
    #: ``True`` when the formation's gap-free candle run ended before the end of
    #: the closed prefix, so later candles were never eligible to move its state.
    lifecycle_truncated: bool
    lifecycle_truncation_reason: str | None
    events: tuple[ChartGeometryEvent, ...]


@dataclass(frozen=True, slots=True)
class RejectionCount:
    """How many candidate windows one family rejected for one exact reason."""

    family: ChartGeometryFamily
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class ChartGeometryDiagnostics:
    """Deterministic accounting of what was evaluated and what was rejected.

    These counts describe the detection pass only. They are not evidence about
    the market beyond the closed candles that were supplied, and a rejection is
    reported as a rejection with its exact reason rather than being repaired,
    bridged, or silently dropped.
    """

    closed_candle_count: int
    excluded_future_candle_count: int
    required_candle_count: int
    insufficient_history: bool
    confirmed_swing_count: int
    unconfirmed_swing_count: int
    candidate_window_count: int
    accepted_candidate_count: int
    frozen_superseded_count: int
    formation_count: int
    formed_only_count: int
    #: Formations whose gap-free candle run ended before the end of the closed
    #: prefix, so no later candle was eligible to confirm or invalidate them.
    gap_truncated_count: int
    confirmed_count: int
    invalidated_count: int
    rejections: tuple[RejectionCount, ...]


@dataclass(frozen=True, slots=True)
class ChartGeometrySnapshot:
    """The complete read-only result of one ``detect_chart_geometry`` call."""

    exchange: str
    symbol: str
    timeframe: str
    as_of: datetime
    rules_version: str
    parameters_fingerprint: str
    interval_seconds: int
    parameters: ChartGeometryParameters
    formations: tuple[ChartGeometryFormation, ...]
    events: tuple[ChartGeometryEvent, ...]
    diagnostics: ChartGeometryDiagnostics

    def families(self) -> tuple[ChartGeometryFamily, ...]:
        """Return the distinct families present, in documented family order."""

        present = {formation.family for formation in self.formations}
        return tuple(family for family in ChartGeometryFamily if family in present)

    def formations_for(self, family: ChartGeometryFamily) -> tuple[ChartGeometryFormation, ...]:
        """Return every formation of one family, chronologically."""

        return tuple(formation for formation in self.formations if formation.family is family)

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable projection of this snapshot."""

        return {
            "rules_version": self.rules_version,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": to_jsonable(self.as_of),
            "interval_seconds": self.interval_seconds,
            "parameters_fingerprint": self.parameters_fingerprint,
            "parameters": to_jsonable(self.parameters),
            "formations": to_jsonable(self.formations),
            "events": to_jsonable(self.events),
            "diagnostics": to_jsonable(self.diagnostics),
        }
