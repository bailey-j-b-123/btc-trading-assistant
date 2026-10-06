"""Immutable report contracts for Step 11 historical validation.

Validation records are deliberately separate from the Step 7 user journal. They
are in-memory derived artifacts: the report may contain exact Step 5 snapshots,
Step 6 plans, and Step 7-style observations, but it never inserts any of them
into production journal tables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from trading_assistant.journaling.types import OutcomeObservation
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.setup_qualification.models import SetupResult, SetupState
from trading_assistant.trade_planning.models import TradePlanResult


class ValidationPhase(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"


class ValidationRecordKind(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    SETUP = "SETUP"


class MetricStatus(StrEnum):
    SUFFICIENT_DATA = "SUFFICIENT_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class DatasetRange:
    timeframe: str
    candle_count: int
    first_open: datetime | None
    last_close: datetime | None


@dataclass(frozen=True, slots=True)
class ResolvedSplit:
    development_start: datetime | None
    development_end: datetime | None
    out_of_sample_start: datetime | None
    out_of_sample_end: datetime | None
    source: str


@dataclass(frozen=True, slots=True)
class RegimeLabel:
    """Descriptive values known at the record's own decision timestamp."""

    trend: str
    volatility: str
    calendar_period: str


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    """One derived replay artifact, never a Bailey journal row or decision."""

    id: str
    phase: ValidationPhase
    kind: ValidationRecordKind
    as_of: datetime
    snapshot_state: SetupState
    setup: SetupResult | None
    plan: TradePlanResult | None
    observation: OutcomeObservation | None
    observation_unavailable_reason: str | None
    regime: RegimeLabel


@dataclass(frozen=True, slots=True)
class ValueCount:
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class RateMetric:
    metric: str
    numerator: int
    denominator: int
    percentage: Decimal | None
    status: MetricStatus
    denominator_definition: str


@dataclass(frozen=True, slots=True)
class RDistribution:
    """Distribution of unit-neutral hypothetical/observational R values only."""

    metric: str
    records_considered: int
    sample_size: int
    status: MetricStatus
    excluded: tuple[ValueCount, ...]
    values: tuple[Decimal, ...]
    average: Decimal | None
    median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    definition: str


@dataclass(frozen=True, slots=True)
class CohortMetrics:
    """Counts and denominated observations for one report cohort or breakdown."""

    total_records: int
    snapshot_records: int
    setup_records: int
    distinct_setup_ids: int
    setup_state_counts: tuple[ValueCount, ...]
    plan_state_counts: tuple[ValueCount, ...]
    completed_count: int
    unresolved_count: int
    ambiguous_count: int
    incomplete_count: int
    outcomes_observed_count: int
    outcomes_not_observed_count: int
    outcome_status_counts: tuple[ValueCount, ...]
    entry_reached_rate: RateMetric
    stop_rate_after_ordered_entry: RateMetric
    target_hit_rates: tuple[RateMetric, ...]
    raw_observational_r: RDistribution
    friction_adjusted_hypothetical_r: RDistribution


@dataclass(frozen=True, slots=True)
class Breakdown:
    dimension: str
    value: str
    metrics: CohortMetrics


@dataclass(frozen=True, slots=True)
class ValidationCohort:
    phase: ValidationPhase
    start: datetime | None
    end: datetime | None
    decision_boundary_count: int
    metrics: CohortMetrics
    breakdowns: tuple[Breakdown, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """A canonical, reproducible, derived Step 11 validation artifact."""

    report_id: str
    rules_version: str
    dataset_fingerprint: str
    config_fingerprint: str
    resolved_config_fingerprint: str
    strategy_versions: tuple[tuple[str, str], ...]
    exchange: str
    symbol: str
    timeframe: str
    dataset_ranges: tuple[DatasetRange, ...]
    split: ResolvedSplit
    records: tuple[ValidationRecord, ...]
    development: ValidationCohort
    out_of_sample: ValidationCohort | None
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), sort_keys=True, separators=(",", ":"))
