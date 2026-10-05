"""Immutable, JSON-stable result contracts for Step 8 reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from trading_assistant.market_structure.snapshot import to_jsonable


class MetricStatus(StrEnum):
    """Whether an explicitly counted sample meets the configured reporting floor."""

    SUFFICIENT_DATA = "SUFFICIENT_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class ValueCount:
    """A deterministic count for one categorical value; ``None`` is explicit."""

    value: str | None
    count: int


@dataclass(frozen=True, slots=True)
class DecimalValueCount:
    """A deterministic count for one exact Decimal observation value."""

    value: Decimal
    count: int


@dataclass(frozen=True, slots=True)
class DimensionCounts:
    """One journal-record count table for a requested immutable dimension."""

    dimension: str
    total_records: int
    values: tuple[ValueCount, ...]


@dataclass(frozen=True, slots=True)
class RateSummary:
    """A thresholded percentage with its exact numerator and denominator."""

    metric: str
    status: MetricStatus
    records_considered: int
    eligible_records: int
    excluded_records: int
    exclusion_reasons: tuple[ValueCount, ...]
    sample_size: int
    numerator: int
    denominator: int
    percentage: Decimal | None
    denominator_definition: str


@dataclass(frozen=True, slots=True)
class QuantileValue:
    """One nearest-rank quantile and the value selected by that rule."""

    probability: Decimal
    value: Decimal


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    """Raw value frequencies plus sample-gated descriptive summaries."""

    metric: str
    status: MetricStatus
    records_considered: int
    eligible_records: int
    excluded_records: int
    exclusion_reasons: tuple[ValueCount, ...]
    sample_size: int
    positive_count: int
    negative_count: int
    zero_count: int
    distribution: tuple[DecimalValueCount, ...]
    average: Decimal | None
    median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    quantiles: tuple[QuantileValue, ...]
    definition: str


@dataclass(frozen=True, slots=True)
class TargetSummary:
    """Independent reach analysis for one target ordinal (T1, T2, ...)."""

    target_index: int
    label: str
    records_with_target_proposed: int
    reach_rate: RateSummary


@dataclass(frozen=True, slots=True)
class ReportDataQuality:
    """Top-level denominator coverage and explicit outcome exclusions."""

    total_journal_records_considered: int
    setup_records_eligible: int
    distinct_setup_ids: int
    snapshot_records: int
    plannable_plan_records: int
    non_plannable_or_missing_plan_records: int
    outcome_records_eligible: int
    outcome_records_without_observation_by_cutoff: int
    outcome_records_determinate: int
    ambiguous_count: int
    incomplete_unknown_count: int
    observations_with_any_gap_count: int
    entry_not_reached_count: int
    excluded_from_determinate_outcome_analysis: int
    exclusion_reasons: tuple[ValueCount, ...]


@dataclass(frozen=True, slots=True)
class VersionProfile:
    """A distinct recorded rule/configuration profile in the report cohort."""

    journal_rules_version: str
    setup_rules_version: str
    setup_config_fingerprint: str
    planning_rules_version: str | None
    planning_config_fingerprint: str | None
    decision_rules_version: str | None
    observation_rules_version: str | None
    observation_config_fingerprint: str | None
    record_count: int


@dataclass(frozen=True, slots=True)
class GroupStatistics:
    """Counts, eligibility and deterministic metrics for one exact group."""

    key: tuple[tuple[str, str | None], ...]
    total_journal_records_considered: int
    setup_state_counts: tuple[ValueCount, ...]
    decision_state_counts: tuple[ValueCount, ...]
    plan_state_counts: tuple[ValueCount, ...]
    outcome_status_counts: tuple[ValueCount, ...]
    outcome_status_rates: tuple[RateSummary, ...]
    data_quality: ReportDataQuality
    setup_qualification_rate: RateSummary
    entry_reach_rate: RateSummary
    stop_touch_rate_after_ordered_entry: RateSummary
    target_summaries: tuple[TargetSummary, ...]
    hypothetical_proposed_plan_outcome_r: DistributionSummary
    observational_mfe_price_move: DistributionSummary
    observational_mae_price_move: DistributionSummary
    observational_mfe_r: DistributionSummary
    observational_mae_r: DistributionSummary


@dataclass(frozen=True, slots=True)
class StatisticsReport:
    """Pure, immutable statistical report over one explicit journal dataset."""

    report_id: str
    rules_version: str
    config_fingerprint: str
    source_dataset_fingerprint: str
    input_journal_ids: tuple[str, ...]
    input_decision_ids: tuple[str, ...]
    input_outcome_ids: tuple[str, ...]
    filters: tuple[tuple[str, tuple[str | None, ...]], ...]
    window_start: datetime | None
    as_of: datetime
    requested_group_by: tuple[str, ...]
    effective_group_by: tuple[str, ...]
    version_policy: str
    mixed_versions: bool
    version_profiles: tuple[VersionProfile, ...]
    data_quality: ReportDataQuality
    dimension_counts: tuple[DimensionCounts, ...]
    overall: GroupStatistics | None
    groups: tuple[GroupStatistics, ...]

    def to_json_dict(self) -> dict[str, Any]:
        """Return a fresh, JSON-compatible deterministic representation."""

        return to_jsonable(self)

    def to_json(self) -> str:
        """Canonical byte-stable compact JSON; Decimal values remain strings."""

        return json.dumps(self.to_json_dict(), sort_keys=True, separators=(",", ":"))
