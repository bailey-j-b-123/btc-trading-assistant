"""Deterministic, read-only analysis of an explicit immutable Step 7 dataset."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum
from hashlib import sha256
from typing import Any

from trading_assistant.journaling.types import (
    DecisionRecord,
    JournalRecord,
    OutcomeObservation,
    OutcomeStatus,
    OutcomeVersion,
    RecordKind,
)
from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.setup_qualification.models import SetupState
from trading_assistant.statistics.config import StatisticsConfig
from trading_assistant.statistics.dataset import JournalDataset, StatisticsDatasetError
from trading_assistant.statistics.models import (
    DecimalValueCount,
    DimensionCounts,
    DistributionSummary,
    GroupStatistics,
    MetricStatus,
    QuantileValue,
    RateSummary,
    ReportDataQuality,
    StatisticsReport,
    TargetSummary,
    ValueCount,
    VersionProfile,
)
from trading_assistant.trade_planning.models import PlanState

NO_DECISION = "NO_DECISION"
NO_PLAN_ATTACHED = "NO_PLAN_ATTACHED"
PLAN_NOT_YET_VISIBLE = "PLAN_NOT_YET_VISIBLE"
NO_OUTCOME_BY_CUTOFF = "NO_OUTCOME_BY_CUTOFF"

DEFAULT_GROUP_BY = (
    "setup_family",
    "direction",
    "decision_state",
    "symbol",
    "exchange",
    "timeframe",
)
VERSION_GROUP_BY = (
    "journal_rules_version",
    "setup_rules_version",
    "setup_config_fingerprint",
    "planning_rules_version",
    "plan_config_fingerprint",
    "decision_rules_version",
    "observation_rules_version",
    "observation_config_fingerprint",
)
COUNT_DIMENSIONS = (
    "setup_family",
    "direction",
    "symbol",
    "exchange",
    "timeframe",
    "setup_state",
    "decision_state",
    "plan_state",
    "record_kind",
    "outcome_status",
)
FILTERABLE_DIMENSIONS = frozenset(
    {
        *COUNT_DIMENSIONS,
        "setup_id",
        "journal_rules_version",
        "decision_rules_version",
        "setup_rules_version",
        "setup_config_fingerprint",
        "planning_rules_version",
        "plan_config_fingerprint",
        "observation_rules_version",
        "observation_config_fingerprint",
        "period_day",
        "period_week",
        "period_month",
    }
)
GROUPABLE_DIMENSIONS = FILTERABLE_DIMENSIONS

_SETUP_RATE_DEFINITION = (
    "Qualified SETUP journal records divided by all SETUP journal records in the "
    "group. Each immutable SETUP journal row is one recorded setup observation; "
    "repeated rows for the same setup id remain repeated observations. Aggregate "
    "SNAPSHOT records are not expanded or double-counted."
)
_STATUS_RATE_DEFINITION = (
    "Count of this exact stored OutcomeStatus divided by all PLANNABLE journal "
    "records with an outcome observation visible at the report cutoff. This is "
    "a status-distribution denominator; AMBIGUOUS and INCOMPLETE_DATA remain "
    "explicit categories and are not classified as wins or losses."
)
_ENTRY_RATE_DEFINITION = (
    "Recorded entry touches divided by outcomes whose entry-touch state is "
    "known: entry_reached=True is a directly observed touch (including an "
    "ambiguous/incomplete observation that explicitly recorded the touch); "
    "ENTRY_NOT_REACHED and INVALIDATED_BEFORE_ENTRY are known non-touches. "
    "An incomplete/ambiguous observation with no recorded entry touch is excluded."
)
_STOP_RATE_DEFINITION = (
    "Stored proposed-stop level touches after an ordered entry divided by clean, "
    "non-ambiguous, non-INCOMPLETE_DATA outcomes with an ordered entry. A stop "
    "touch is an OHLC observation, never an executed stop or a realized loss."
)
_TARGET_RATE_DEFINITION = (
    "Ordered post-entry reaches of target Tn divided by clean, non-ambiguous, "
    "non-INCOMPLETE_DATA observations that proposed Tn and had an ordered entry. "
    "Each target ordinal has its own denominator; pre-entry touches do not count."
)
_R_DEFINITION = (
    "HYPOTHETICAL_PROPOSED_LEVEL_R: for an unambiguous STOPPED outcome with an "
    "ordered entry, directional proposed-entry-to-stored-proposed-stop price "
    "move divided by stored risk_per_unit; for TARGETS_REACHED, the largest "
    "directional R among the ordered target levels reached, divided by the same "
    "recorded risk. STOPPED_AFTER_TARGETS is excluded because Step 7 specifies "
    "no partial-exit policy. These are proposed-level observations, not fills, "
    "executed trades, realized P&L, fees, or slippage."
)
_MFE_MAE_DEFINITION = (
    "Uses Step 7 OutcomeObservation.mfe_price_move, mae_price_move, mfe_r and "
    "mae_r exactly as stored: proposed-entry-relative extremes over Step 7's "
    "evaluated window from plan.as_of through its terminal candle or observation "
    "cutoff. The window can include candles before entry; values are not realized "
    "trade excursions. AMBIGUOUS and INCOMPLETE_DATA statuses are excluded; a "
    "terminal outcome established before a later gap remains eligible."
)


@dataclass(frozen=True, slots=True)
class StatisticsFilters:
    """Canonical immutable exact-value filters over recorded dimensions."""

    criteria: tuple[tuple[str, tuple[str | None, ...]], ...] = ()

    def __post_init__(self) -> None:
        normalized = _normalize_filter_criteria(self.criteria)
        object.__setattr__(self, "criteria", normalized)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, object] | None = None,
        **criteria: object,
    ) -> StatisticsFilters:
        """Build filters from a mapping and/or keyword dimensions.

        A scalar selects one exact value; a tuple/list selects any listed value;
        ``None`` selects the explicit null/not-applicable dimension value.
        """

        combined = dict(values or {})
        overlap = combined.keys() & criteria.keys()
        if overlap:
            raise ValueError(
                "filter dimensions were supplied twice: " + ", ".join(sorted(overlap))
            )
        combined.update(criteria)
        raw = []
        for name, values_for_name in combined.items():
            if isinstance(values_for_name, (tuple, list, set, frozenset)):
                choices = tuple(values_for_name)
            else:
                choices = (values_for_name,)
            raw.append((name, choices))
        return cls(tuple(raw))

    def to_json_dict(self) -> dict[str, object]:
        return {name: choices for name, choices in self.criteria}


@dataclass(frozen=True, slots=True)
class _SelectedRecord:
    record: JournalRecord
    decision: DecisionRecord | None
    outcome_version: OutcomeVersion | None
    plan_visible: bool

    @property
    def outcome(self) -> OutcomeObservation | None:
        return (
            None if self.outcome_version is None else self.outcome_version.observation
        )

    @property
    def is_plannable(self) -> bool:
        return (
            self.plan_visible
            and self.record.plan_state is PlanState.PLANNABLE
            and self.record.plan_id is not None
        )


def _normalize_filter_criteria(
    criteria: Sequence[tuple[str, Sequence[object]]],
) -> tuple[tuple[str, tuple[str | None, ...]], ...]:
    normalized: list[tuple[str, tuple[str | None, ...]]] = []
    seen: set[str] = set()
    for name, raw_values in criteria:
        if not isinstance(name, str) or name not in FILTERABLE_DIMENSIONS:
            raise ValueError(
                f"unsupported statistics filter dimension {name!r}; available: "
                + ", ".join(sorted(FILTERABLE_DIMENSIONS))
            )
        if name in seen:
            raise ValueError(f"duplicate statistics filter dimension {name!r}")
        seen.add(name)
        values: list[str | None] = []
        for raw in raw_values:
            value = raw.value if isinstance(raw, Enum) else raw
            if value is not None and not isinstance(value, str):
                raise TypeError(f"filter values for {name!r} must be strings or None")
            if value not in values:
                values.append(value)
        if not values:
            raise ValueError(f"filter {name!r} must select at least one value")
        normalized.append((name, tuple(sorted(values, key=_optional_text_key))))
    return tuple(sorted(normalized, key=lambda pair: pair[0]))


def _optional_text_key(value: str | None) -> tuple[int, str]:
    return (0, "") if value is None else (1, value)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _utc_time(value: datetime) -> datetime:
    return require_utc_datetime(value)


def _period_value(instant: datetime, period: str) -> str:
    time = _utc_time(instant)
    if period == "period_day":
        return time.date().isoformat()
    if period == "period_month":
        return f"{time.year:04d}-{time.month:02d}"
    iso_year, iso_week, _weekday = time.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def _dimension_value(selected: _SelectedRecord, dimension: str) -> str | None:
    record = selected.record
    if dimension == "record_kind":
        return _text(record.record_kind)
    if dimension == "setup_id":
        return record.setup_id
    if dimension == "setup_family":
        return _text(record.setup_family)
    if dimension == "direction":
        return _text(record.setup_direction)
    if dimension == "symbol":
        return record.symbol
    if dimension == "exchange":
        return record.exchange
    if dimension == "timeframe":
        return record.timeframe
    if dimension == "setup_state":
        return _text(record.setup_state)
    if dimension == "decision_state":
        return (
            NO_DECISION
            if selected.decision is None
            else _text(selected.decision.decision)
        )
    if dimension == "plan_state":
        if not selected.plan_visible:
            return (
                PLAN_NOT_YET_VISIBLE if record.plan_id is not None else NO_PLAN_ATTACHED
            )
        return (
            NO_PLAN_ATTACHED if record.plan_state is None else _text(record.plan_state)
        )
    if dimension == "outcome_status":
        if not selected.is_plannable:
            return "NOT_APPLICABLE"
        return (
            NO_OUTCOME_BY_CUTOFF
            if selected.outcome is None
            else _text(selected.outcome.status)
        )
    if dimension == "journal_rules_version":
        return record.journal_rules_version
    if dimension == "setup_rules_version":
        return record.setup_rules_version
    if dimension == "setup_config_fingerprint":
        return record.setup_config_fingerprint
    if dimension == "planning_rules_version":
        return record.planning_rules_version if selected.plan_visible else None
    if dimension == "plan_config_fingerprint":
        return record.plan_config_fingerprint if selected.plan_visible else None
    if dimension == "decision_rules_version":
        return (
            None
            if selected.decision is None
            else selected.decision.decision_rules_version
        )
    if dimension == "observation_rules_version":
        return (
            None
            if selected.outcome is None
            else selected.outcome.observation_rules_version
        )
    if dimension == "observation_config_fingerprint":
        return None if selected.outcome is None else selected.outcome.config_fingerprint
    if dimension in ("period_day", "period_week", "period_month"):
        return _period_value(record.setup_as_of, dimension)
    raise ValueError(f"unsupported statistics dimension {dimension!r}")


def _normalize_group_by(group_by: Sequence[str] | None) -> tuple[str, ...]:
    requested = DEFAULT_GROUP_BY if group_by is None else tuple(group_by)
    if any(not isinstance(name, str) for name in requested):
        raise TypeError("group_by dimensions must be strings")
    if len(set(requested)) != len(requested):
        raise ValueError("group_by dimensions must be unique")
    unknown = set(requested) - GROUPABLE_DIMENSIONS
    if unknown:
        raise ValueError(
            "unsupported group_by dimensions: " + ", ".join(sorted(unknown))
        )
    return tuple(requested)


def _choose_decision(
    decisions: Sequence[DecisionRecord], cutoff: datetime
) -> DecisionRecord | None:
    eligible = [row for row in decisions if _utc_time(row.decided_at) <= cutoff]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            _utc_time(row.decided_at),
            row.sequence,
            row.decision_id,
        ),
    )


def _choose_outcome(
    outcomes: Sequence[OutcomeVersion], cutoff: datetime
) -> OutcomeVersion | None:
    eligible = [
        version
        for version in outcomes
        if _utc_time(version.observation.observed_through) <= cutoff
        and _utc_time(version.observation.window_start) <= cutoff
    ]
    if not eligible:
        return None
    # The latest observed-through horizon is used. If multiple immutable rows
    # share that exact horizon, the earliest sequence is chosen: a later replay
    # or changed observation at the same horizon cannot silently rewrite the
    # historical report.
    return max(
        eligible,
        key=lambda version: (
            _utc_time(version.observation.observed_through),
            -version.sequence,
            version.observation.id,
        ),
    )


def _selected_records(
    dataset: JournalDataset,
    *,
    cutoff: datetime,
    window_start: datetime | None,
    filters: StatisticsFilters,
) -> tuple[_SelectedRecord, ...]:
    decisions_by_id: dict[str, list[DecisionRecord]] = defaultdict(list)
    outcomes_by_id: dict[str, list[OutcomeVersion]] = defaultdict(list)
    for decision in dataset.decisions:
        decisions_by_id[decision.journal_id].append(decision)
    for version in dataset.outcomes:
        outcomes_by_id[version.observation.journal_id].append(version)

    selected: list[_SelectedRecord] = []
    for record in dataset.records:
        setup_as_of = _utc_time(record.setup_as_of)
        if setup_as_of > cutoff:
            continue
        if window_start is not None and setup_as_of < window_start:
            continue
        plan_visible = (
            record.plan_id is not None
            and record.planning_as_of is not None
            and _utc_time(record.planning_as_of) <= cutoff
        )
        selection = _SelectedRecord(
            record=record,
            decision=_choose_decision(decisions_by_id[record.journal_id], cutoff),
            outcome_version=(
                _choose_outcome(outcomes_by_id[record.journal_id], cutoff)
                if plan_visible and record.plan_state is PlanState.PLANNABLE
                else None
            ),
            plan_visible=plan_visible,
        )
        if all(
            _dimension_value(selection, name) in values
            for name, values in filters.criteria
        ):
            selected.append(selection)
    return tuple(selected)


def _counter_values(counter: Counter[str | None]) -> tuple[ValueCount, ...]:
    return tuple(
        ValueCount(value, count)
        for value, count in sorted(
            counter.items(), key=lambda pair: _optional_text_key(pair[0])
        )
    )


def _categorical_counts(
    selected: Sequence[_SelectedRecord], dimension: str
) -> tuple[ValueCount, ...]:
    counts: Counter[str | None] = Counter(
        _dimension_value(row, dimension) for row in selected
    )
    return _counter_values(counts)


def _all_enum_counts(
    selected: Sequence[_SelectedRecord],
    *,
    dimension: str,
    enum_values: Sequence[str],
) -> tuple[ValueCount, ...]:
    counts: Counter[str | None] = Counter(
        _dimension_value(row, dimension) for row in selected
    )
    return tuple(
        ValueCount(value, counts.pop(value, 0)) for value in enum_values
    ) + _counter_values(counts)


def _metric_status(sample_size: int, config: StatisticsConfig) -> MetricStatus:
    return (
        MetricStatus.SUFFICIENT_DATA
        if sample_size >= config.minimum_sample_size
        else MetricStatus.INSUFFICIENT_DATA
    )


def _reason_values(counter: Counter[str]) -> tuple[ValueCount, ...]:
    return tuple(ValueCount(reason, counter[reason]) for reason in sorted(counter))


def _rate_summary(
    *,
    metric: str,
    selected: Sequence[_SelectedRecord],
    evaluator: Any,
    denominator_definition: str,
    config: StatisticsConfig,
) -> RateSummary:
    eligible = 0
    numerator = 0
    excluded: Counter[str] = Counter()
    for row in selected:
        is_eligible, positive, reason = evaluator(row)
        if is_eligible:
            eligible += 1
            numerator += int(positive)
        else:
            excluded[reason] += 1
    status = _metric_status(eligible, config)
    if eligible >= config.minimum_sample_size:
        with localcontext() as context:
            context.prec = config.decimal_precision
            context.rounding = ROUND_HALF_EVEN
            percentage = _quantize(
                Decimal(numerator) * Decimal(100) / Decimal(eligible), config
            )
    else:
        percentage = None
    return RateSummary(
        metric=metric,
        status=status,
        records_considered=len(selected),
        eligible_records=eligible,
        excluded_records=len(selected) - eligible,
        exclusion_reasons=_reason_values(excluded),
        sample_size=eligible,
        numerator=numerator,
        denominator=eligible,
        percentage=percentage,
        denominator_definition=denominator_definition,
    )


def _is_performance_status_eligible(observation: OutcomeObservation) -> bool:
    return observation.status not in (
        OutcomeStatus.AMBIGUOUS,
        OutcomeStatus.INCOMPLETE_DATA,
    )


def _outcome_rate_evaluator(
    positive_status: OutcomeStatus | None = None,
):
    def evaluate(row: _SelectedRecord) -> tuple[bool, bool, str]:
        if not row.is_plannable:
            return False, False, "NO_PLANNABLE_PLAN"
        outcome = row.outcome
        if outcome is None:
            return False, False, "NO_OUTCOME_BY_CUTOFF"
        return True, outcome.status is positive_status, ""

    return evaluate


def _entry_evaluator(row: _SelectedRecord) -> tuple[bool, bool, str]:
    if not row.is_plannable:
        return False, False, "NO_PLANNABLE_PLAN"
    outcome = row.outcome
    if outcome is None:
        return False, False, "NO_OUTCOME_BY_CUTOFF"
    if outcome.entry_reached:
        return True, True, ""
    if outcome.status in (
        OutcomeStatus.ENTRY_NOT_REACHED,
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
    ):
        return True, False, ""
    if outcome.status is OutcomeStatus.AMBIGUOUS:
        return False, False, "AMBIGUOUS_ENTRY_STATE"
    if outcome.status is OutcomeStatus.INCOMPLETE_DATA:
        return False, False, "INCOMPLETE_ENTRY_STATE"
    # Step 7 terminal states with an ordered entry should carry entry_reached.
    # If a synthetic/foreign payload contradicts that contract, preserve it as
    # excluded data rather than silently treating the missing flag as a miss.
    return False, False, "ENTRY_STATE_NOT_ESTABLISHED"


def _stop_evaluator(row: _SelectedRecord) -> tuple[bool, bool, str]:
    if not row.is_plannable:
        return False, False, "NO_PLANNABLE_PLAN"
    outcome = row.outcome
    if outcome is None:
        return False, False, "NO_OUTCOME_BY_CUTOFF"
    if not _is_performance_status_eligible(outcome):
        return (
            False,
            False,
            "AMBIGUOUS_OUTCOME"
            if outcome.status is OutcomeStatus.AMBIGUOUS
            else "INCOMPLETE_OUTCOME",
        )
    if not outcome.entry_ordered:
        return False, False, "ENTRY_NOT_ORDERED"
    if outcome.stop_pre_entry:
        return False, False, "STOP_WAS_PRE_ENTRY"
    return True, outcome.stop_reached, ""


def _planned_target_count(record: JournalRecord) -> int:
    """Read the target count from the exact journaled Step 6 plan projection."""

    payload = record.plan_payload
    if not isinstance(payload, dict):
        raise StatisticsDatasetError(
            f"PLANNABLE journal record {record.journal_id} has no plan payload"
        )
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets:
        raise StatisticsDatasetError(
            f"PLANNABLE journal record {record.journal_id} has no stored target list"
        )
    return len(targets)


def _target_count(row: _SelectedRecord) -> int:
    if not row.is_plannable:
        return 0
    return (
        len(row.outcome.target_levels)
        if row.outcome is not None
        else _planned_target_count(row.record)
    )


def _target_evaluator(target_index: int):
    def evaluate(row: _SelectedRecord) -> tuple[bool, bool, str]:
        if not row.is_plannable:
            return False, False, "NO_PLANNABLE_PLAN"
        outcome = row.outcome
        if target_index >= _target_count(row):
            return False, False, "TARGET_NOT_PROPOSED"
        if outcome is None:
            return False, False, "NO_OUTCOME_BY_CUTOFF"
        if not _is_performance_status_eligible(outcome):
            return (
                False,
                False,
                "AMBIGUOUS_OUTCOME"
                if outcome.status is OutcomeStatus.AMBIGUOUS
                else "INCOMPLETE_OUTCOME",
            )
        if not outcome.entry_ordered:
            return False, False, "ENTRY_NOT_ORDERED"
        return True, target_index in outcome.targets_reached, ""

    return evaluate


def _quantize(value: Decimal, config: StatisticsConfig) -> Decimal:
    quantum = Decimal(1).scaleb(-config.decimal_places)
    with localcontext() as context:
        context.prec = config.decimal_precision
        context.rounding = ROUND_HALF_EVEN
        return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _mean(values: Sequence[Decimal], config: StatisticsConfig) -> Decimal:
    with localcontext() as context:
        context.prec = config.decimal_precision
        context.rounding = ROUND_HALF_EVEN
        total = sum(values, Decimal(0))
        return _quantize(total / Decimal(len(values)), config)


def _median(values: Sequence[Decimal], config: StatisticsConfig) -> Decimal:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return _quantize(ordered[middle], config)
    with localcontext() as context:
        context.prec = config.decimal_precision
        context.rounding = ROUND_HALF_EVEN
        return _quantize((ordered[middle - 1] + ordered[middle]) / Decimal(2), config)


def _nearest_rank_quantile(ordered: Sequence[Decimal], probability: Decimal) -> Decimal:
    numerator, denominator = probability.as_integer_ratio()
    rank = (numerator * len(ordered) + denominator - 1) // denominator
    return ordered[max(1, rank) - 1]


def _distribution_summary(
    *,
    metric: str,
    selected: Sequence[_SelectedRecord],
    extractor: Any,
    definition: str,
    config: StatisticsConfig,
) -> DistributionSummary:
    values: list[Decimal] = []
    excluded: Counter[str] = Counter()
    for row in selected:
        if not row.is_plannable:
            excluded["NO_PLANNABLE_PLAN"] += 1
            continue
        outcome = row.outcome
        if outcome is None:
            excluded["NO_OUTCOME_BY_CUTOFF"] += 1
            continue
        if outcome.status is OutcomeStatus.AMBIGUOUS:
            excluded["AMBIGUOUS_OUTCOME"] += 1
            continue
        if outcome.status is OutcomeStatus.INCOMPLETE_DATA:
            excluded["INCOMPLETE_OUTCOME"] += 1
            continue
        value = extractor(outcome)
        if value is None:
            excluded["METRIC_NOT_RECORDED"] += 1
            continue
        if not isinstance(value, Decimal) or not value.is_finite():
            raise StatisticsDatasetError(
                f"{metric} contains a non-finite or non-Decimal observation"
            )
        values.append(value)

    value_counts = Counter(values)
    ordered_values = sorted(values)
    sample_size = len(ordered_values)
    status = _metric_status(sample_size, config)
    positive = sum(value > 0 for value in ordered_values)
    negative = sum(value < 0 for value in ordered_values)
    zero = sample_size - positive - negative
    sufficiently_sampled = sample_size >= config.minimum_sample_size
    quantiles = (
        tuple(
            QuantileValue(
                probability=probability,
                value=_quantize(
                    _nearest_rank_quantile(ordered_values, probability), config
                ),
            )
            for probability in config.quantiles
        )
        if sufficiently_sampled
        else ()
    )
    return DistributionSummary(
        metric=metric,
        status=status,
        records_considered=len(selected),
        eligible_records=sample_size,
        excluded_records=len(selected) - sample_size,
        exclusion_reasons=_reason_values(excluded),
        sample_size=sample_size,
        positive_count=positive,
        negative_count=negative,
        zero_count=zero,
        distribution=tuple(
            DecimalValueCount(value, value_counts[value])
            for value in sorted(value_counts)
        ),
        average=_mean(ordered_values, config) if sufficiently_sampled else None,
        median=_median(ordered_values, config) if sufficiently_sampled else None,
        minimum=(
            _quantize(ordered_values[0], config) if sufficiently_sampled else None
        ),
        maximum=(
            _quantize(ordered_values[-1], config) if sufficiently_sampled else None
        ),
        quantiles=quantiles,
        definition=definition,
    )


def _directional_r(
    *,
    price: Decimal,
    observation: OutcomeObservation,
    config: StatisticsConfig,
) -> Decimal:
    direction = observation.direction
    if direction not in ("bullish", "bearish"):
        raise StatisticsDatasetError(
            f"outcome {observation.id} has invalid direction {direction!r}"
        )
    if observation.risk_per_unit <= 0:
        raise StatisticsDatasetError(
            f"outcome {observation.id} has non-positive stored risk_per_unit"
        )
    with localcontext() as context:
        context.prec = config.decimal_precision
        context.rounding = ROUND_HALF_EVEN
        move = (
            price - observation.entry_level
            if direction == "bullish"
            else observation.entry_level - price
        )
        return _quantize(move / observation.risk_per_unit, config)


def _proposed_outcome_r(
    observation: OutcomeObservation, config: StatisticsConfig
) -> Decimal | None:
    if not observation.entry_ordered:
        return None
    if observation.status is OutcomeStatus.STOPPED:
        if not observation.stop_reached or observation.stop_pre_entry:
            return None
        return _directional_r(
            price=observation.stop_level,
            observation=observation,
            config=config,
        )
    if observation.status is OutcomeStatus.TARGETS_REACHED:
        if not observation.targets_reached:
            return None
        reached_levels = [
            observation.target_levels[index]
            for index in observation.targets_reached
            if 0 <= index < len(observation.target_levels)
        ]
        if len(reached_levels) != len(observation.targets_reached):
            return None
        return max(
            _directional_r(price=level, observation=observation, config=config)
            for level in reached_levels
        )
    return None


def _explain_r_exclusion(row: _SelectedRecord) -> str:
    if not row.is_plannable:
        return "NO_PLANNABLE_PLAN"
    if row.outcome is None:
        return "NO_OUTCOME_BY_CUTOFF"
    observation = row.outcome
    if observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS:
        return "STOPPED_AFTER_TARGETS_NO_EXIT_POLICY"
    if observation.status is OutcomeStatus.AMBIGUOUS:
        return "AMBIGUOUS_OUTCOME"
    if observation.status is OutcomeStatus.INCOMPLETE_DATA:
        return "INCOMPLETE_OUTCOME"
    if (
        observation.status
        in (
            OutcomeStatus.ENTRY_NOT_REACHED,
            OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        )
        or not observation.entry_ordered
    ):
        return "ENTRY_NOT_ORDERED"
    if observation.status is OutcomeStatus.OPEN_AT_CUTOFF:
        return "NO_TERMINAL_PROPOSED_LEVEL_R"
    return "TERMINAL_LEVEL_NOT_ESTABLISHED"


def _specialized_distribution_summary(
    *,
    metric: str,
    selected: Sequence[_SelectedRecord],
    extractor: Any,
    exclusion_reason: Any,
    definition: str,
    config: StatisticsConfig,
) -> DistributionSummary:
    values: list[Decimal] = []
    excluded: Counter[str] = Counter()
    for row in selected:
        if not row.is_plannable or row.outcome is None:
            excluded[exclusion_reason(row)] += 1
            continue
        observation = row.outcome
        if observation.status in (
            OutcomeStatus.AMBIGUOUS,
            OutcomeStatus.INCOMPLETE_DATA,
        ):
            excluded[exclusion_reason(row)] += 1
            continue
        value = extractor(observation)
        if value is None:
            excluded[exclusion_reason(row)] += 1
            continue
        if not isinstance(value, Decimal) or not value.is_finite():
            raise StatisticsDatasetError(
                f"{metric} contains a non-finite or non-Decimal observation"
            )
        values.append(value)
    return _finish_distribution(
        metric=metric,
        selected_count=len(selected),
        values=values,
        excluded=excluded,
        definition=definition,
        config=config,
    )


def _finish_distribution(
    *,
    metric: str,
    selected_count: int,
    values: Sequence[Decimal],
    excluded: Counter[str],
    definition: str,
    config: StatisticsConfig,
) -> DistributionSummary:
    ordered_values = sorted(values)
    value_counts = Counter(ordered_values)
    sample_size = len(ordered_values)
    sufficient = sample_size >= config.minimum_sample_size
    return DistributionSummary(
        metric=metric,
        status=_metric_status(sample_size, config),
        records_considered=selected_count,
        eligible_records=sample_size,
        excluded_records=selected_count - sample_size,
        exclusion_reasons=_reason_values(excluded),
        sample_size=sample_size,
        positive_count=sum(value > 0 for value in ordered_values),
        negative_count=sum(value < 0 for value in ordered_values),
        zero_count=sum(value == 0 for value in ordered_values),
        distribution=tuple(
            DecimalValueCount(value, value_counts[value])
            for value in sorted(value_counts)
        ),
        average=_mean(ordered_values, config) if sufficient else None,
        median=_median(ordered_values, config) if sufficient else None,
        minimum=_quantize(ordered_values[0], config) if sufficient else None,
        maximum=_quantize(ordered_values[-1], config) if sufficient else None,
        quantiles=(
            tuple(
                QuantileValue(
                    probability=probability,
                    value=_quantize(
                        _nearest_rank_quantile(ordered_values, probability), config
                    ),
                )
                for probability in config.quantiles
            )
            if sufficient
            else ()
        ),
        definition=definition,
    )


def _data_quality(selected: Sequence[_SelectedRecord]) -> ReportDataQuality:
    setup_rows = [row for row in selected if row.record.record_kind is RecordKind.SETUP]
    setup_ids = {row.record.setup_id for row in setup_rows if row.record.setup_id}
    plan_rows = [row for row in selected if row.is_plannable]
    outcomes = [row.outcome for row in plan_rows if row.outcome is not None]
    outcome_values = [
        observation for observation in outcomes if observation is not None
    ]
    ambiguous = sum(
        observation.status is OutcomeStatus.AMBIGUOUS for observation in outcome_values
    )
    incomplete = sum(
        observation.status is OutcomeStatus.INCOMPLETE_DATA
        for observation in outcome_values
    )
    determinate = sum(
        _is_performance_status_eligible(observation) for observation in outcome_values
    )
    not_observed = len(plan_rows) - len(outcome_values)
    not_plan = len(selected) - len(plan_rows)
    entry_not_reached = sum(
        observation.status
        in (
            OutcomeStatus.ENTRY_NOT_REACHED,
            OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        )
        and not observation.entry_reached
        for observation in outcome_values
    )
    excluded = Counter()
    if not_plan:
        excluded["NO_PLANNABLE_PLAN"] = not_plan
    if not_observed:
        excluded["NO_OUTCOME_BY_CUTOFF"] = not_observed
    if ambiguous:
        excluded["AMBIGUOUS_OUTCOME"] = ambiguous
    if incomplete:
        excluded["INCOMPLETE_OUTCOME"] = incomplete
    return ReportDataQuality(
        total_journal_records_considered=len(selected),
        setup_records_eligible=len(setup_rows),
        distinct_setup_ids=len(setup_ids),
        snapshot_records=sum(
            row.record.record_kind is RecordKind.SNAPSHOT for row in selected
        ),
        plannable_plan_records=len(plan_rows),
        non_plannable_or_missing_plan_records=not_plan,
        outcome_records_eligible=len(outcome_values),
        outcome_records_without_observation_by_cutoff=not_observed,
        outcome_records_determinate=determinate,
        ambiguous_count=ambiguous,
        incomplete_unknown_count=incomplete,
        observations_with_any_gap_count=sum(
            observation.incomplete for observation in outcome_values
        ),
        entry_not_reached_count=entry_not_reached,
        excluded_from_determinate_outcome_analysis=len(selected) - determinate,
        exclusion_reasons=_reason_values(excluded),
    )


def _summarize_group(
    selected: Sequence[_SelectedRecord],
    *,
    key: tuple[tuple[str, str | None], ...],
    config: StatisticsConfig,
) -> GroupStatistics:
    setup_state_counts = _all_enum_counts(
        selected,
        dimension="setup_state",
        enum_values=tuple(state.value for state in SetupState),
    )
    # Keep every Step 7 decision state visible, including zero counts.
    decision_state_counts = _all_enum_counts(
        selected,
        dimension="decision_state",
        enum_values=(NO_DECISION, "PENDING", "ACCEPTED", "REJECTED", "SKIPPED"),
    )
    plan_state_counts = _all_enum_counts(
        selected,
        dimension="plan_state",
        enum_values=(
            NO_PLAN_ATTACHED,
            PLAN_NOT_YET_VISIBLE,
            *(state.value for state in PlanState),
        ),
    )
    status_counts = _all_enum_counts(
        selected,
        dimension="outcome_status",
        enum_values=("NOT_APPLICABLE", NO_OUTCOME_BY_CUTOFF)
        + tuple(status.value for status in OutcomeStatus),
    )
    outcome_status_rates = tuple(
        _rate_summary(
            metric=f"outcome_status_{status.value.lower()}",
            selected=selected,
            evaluator=_outcome_rate_evaluator(status),
            denominator_definition=_STATUS_RATE_DEFINITION,
            config=config,
        )
        for status in OutcomeStatus
    )
    setup_rate = _rate_summary(
        metric="setup_qualification_rate",
        selected=selected,
        evaluator=lambda row: (
            (True, row.record.setup_state is SetupState.QUALIFIED, "")
            if row.record.record_kind is RecordKind.SETUP
            else (False, False, "SNAPSHOT_RECORD_NOT_SETUP")
        ),
        denominator_definition=_SETUP_RATE_DEFINITION,
        config=config,
    )
    entry_rate = _rate_summary(
        metric="entry_touch_rate",
        selected=selected,
        evaluator=_entry_evaluator,
        denominator_definition=_ENTRY_RATE_DEFINITION,
        config=config,
    )
    stop_rate = _rate_summary(
        metric="proposed_stop_touch_rate_after_ordered_entry",
        selected=selected,
        evaluator=_stop_evaluator,
        denominator_definition=_STOP_RATE_DEFINITION,
        config=config,
    )
    max_targets = max((_target_count(row) for row in selected), default=0)
    targets: list[TargetSummary] = []
    for target_index in range(max_targets):
        proposed_count = sum(
            row.is_plannable and target_index < _target_count(row) for row in selected
        )
        target_rate = _rate_summary(
            metric=f"target_{target_index + 1}_ordered_reach_rate",
            selected=selected,
            evaluator=_target_evaluator(target_index),
            denominator_definition=_TARGET_RATE_DEFINITION,
            config=config,
        )
        targets.append(
            TargetSummary(
                target_index=target_index,
                label=f"T{target_index + 1}",
                records_with_target_proposed=proposed_count,
                reach_rate=target_rate,
            )
        )

    r_summary = _specialized_distribution_summary(
        metric="hypothetical_proposed_plan_outcome_r",
        selected=selected,
        extractor=lambda observation: _proposed_outcome_r(observation, config),
        exclusion_reason=_explain_r_exclusion,
        definition=_R_DEFINITION,
        config=config,
    )

    def field_extractor(field: str):
        return lambda observation: getattr(observation, field)

    def field_exclusion(row: _SelectedRecord) -> str:
        if not row.is_plannable:
            return "NO_PLANNABLE_PLAN"
        if row.outcome is None:
            return "NO_OUTCOME_BY_CUTOFF"
        if row.outcome.status is OutcomeStatus.AMBIGUOUS:
            return "AMBIGUOUS_OUTCOME"
        if row.outcome.status is OutcomeStatus.INCOMPLETE_DATA:
            return "INCOMPLETE_OUTCOME"
        return "METRIC_NOT_RECORDED"

    return GroupStatistics(
        key=key,
        total_journal_records_considered=len(selected),
        setup_state_counts=setup_state_counts,
        decision_state_counts=decision_state_counts,
        plan_state_counts=plan_state_counts,
        outcome_status_counts=status_counts,
        outcome_status_rates=outcome_status_rates,
        data_quality=_data_quality(selected),
        setup_qualification_rate=setup_rate,
        entry_reach_rate=entry_rate,
        stop_touch_rate_after_ordered_entry=stop_rate,
        target_summaries=tuple(targets),
        hypothetical_proposed_plan_outcome_r=r_summary,
        observational_mfe_price_move=_distribution_summary(
            metric="observational_mfe_price_move",
            selected=selected,
            extractor=field_extractor("mfe_price_move"),
            definition=_MFE_MAE_DEFINITION,
            config=config,
        ),
        observational_mae_price_move=_distribution_summary(
            metric="observational_mae_price_move",
            selected=selected,
            extractor=field_extractor("mae_price_move"),
            definition=_MFE_MAE_DEFINITION,
            config=config,
        ),
        observational_mfe_r=_distribution_summary(
            metric="observational_mfe_r",
            selected=selected,
            extractor=field_extractor("mfe_r"),
            definition=_MFE_MAE_DEFINITION,
            config=config,
        ),
        observational_mae_r=_distribution_summary(
            metric="observational_mae_r",
            selected=selected,
            extractor=field_extractor("mae_r"),
            definition=_MFE_MAE_DEFINITION,
            config=config,
        ),
    )


def _dimension_counts(
    selected: Sequence[_SelectedRecord],
) -> tuple[DimensionCounts, ...]:
    result = []
    for dimension in COUNT_DIMENSIONS:
        result.append(
            DimensionCounts(
                dimension=dimension,
                total_records=len(selected),
                values=_categorical_counts(selected, dimension),
            )
        )
    return tuple(result)


def _profile(selected: _SelectedRecord) -> VersionProfile:
    record = selected.record
    return VersionProfile(
        journal_rules_version=record.journal_rules_version,
        setup_rules_version=record.setup_rules_version,
        setup_config_fingerprint=record.setup_config_fingerprint,
        planning_rules_version=(
            record.planning_rules_version if selected.plan_visible else None
        ),
        planning_config_fingerprint=(
            record.plan_config_fingerprint if selected.plan_visible else None
        ),
        decision_rules_version=(
            None
            if selected.decision is None
            else selected.decision.decision_rules_version
        ),
        observation_rules_version=(
            None
            if selected.outcome is None
            else selected.outcome.observation_rules_version
        ),
        observation_config_fingerprint=(
            None if selected.outcome is None else selected.outcome.config_fingerprint
        ),
        record_count=1,
    )


def _version_profiles(
    selected: Sequence[_SelectedRecord],
) -> tuple[VersionProfile, ...]:
    counts: Counter[tuple[object, ...]] = Counter()
    profiles: dict[tuple[object, ...], VersionProfile] = {}
    for row in selected:
        profile = _profile(row)
        key = (
            profile.journal_rules_version,
            profile.setup_rules_version,
            profile.setup_config_fingerprint,
            profile.planning_rules_version,
            profile.planning_config_fingerprint,
            profile.decision_rules_version,
            profile.observation_rules_version,
            profile.observation_config_fingerprint,
        )
        counts[key] += 1
        profiles[key] = profile
    result = []
    for key in sorted(
        counts, key=lambda item: json.dumps(to_jsonable(item), separators=(",", ":"))
    ):
        profile = profiles[key]
        result.append(
            VersionProfile(
                journal_rules_version=profile.journal_rules_version,
                setup_rules_version=profile.setup_rules_version,
                setup_config_fingerprint=profile.setup_config_fingerprint,
                planning_rules_version=profile.planning_rules_version,
                planning_config_fingerprint=profile.planning_config_fingerprint,
                decision_rules_version=profile.decision_rules_version,
                observation_rules_version=profile.observation_rules_version,
                observation_config_fingerprint=profile.observation_config_fingerprint,
                record_count=counts[key],
            )
        )
    return tuple(result)


def _has_mixed_versions(profiles: Sequence[VersionProfile]) -> bool:
    version_fields = (
        "journal_rules_version",
        "setup_rules_version",
        "setup_config_fingerprint",
        "planning_rules_version",
        "planning_config_fingerprint",
        "decision_rules_version",
        "observation_rules_version",
        "observation_config_fingerprint",
    )
    return any(
        len(
            {
                getattr(profile, name)
                for profile in profiles
                if getattr(profile, name) is not None
            }
        )
        > 1
        for name in version_fields
    )


def _source_identity(selected: Sequence[_SelectedRecord]) -> dict[str, object]:
    journal_ids = tuple(sorted(row.record.journal_id for row in selected))
    decision_rows = tuple(
        sorted(
            (row.decision.decision_id, row.decision.sequence)
            for row in selected
            if row.decision is not None
        )
    )
    outcome_rows = tuple(
        sorted(
            (
                row.outcome_version.observation.id,
                row.outcome_version.sequence,
                row.outcome_version.supersedes_outcome_id,
            )
            for row in selected
            if row.outcome_version is not None
        )
    )
    return {
        "journal_records": journal_ids,
        "decision_versions": decision_rows,
        "outcome_versions": outcome_rows,
    }


def _group_sort_key(
    key: tuple[tuple[str, str | None], ...],
) -> tuple[tuple[int, str], ...]:
    return tuple(_optional_text_key(value) for _name, value in key)


class StatisticsAnalyzer:
    """Pure deterministic statistics over a frozen :class:`JournalDataset`."""

    def __init__(self, config: StatisticsConfig | None = None) -> None:
        self.config = StatisticsConfig() if config is None else config
        if not isinstance(self.config, StatisticsConfig):
            raise TypeError("config must be StatisticsConfig or None")

    def analyze(
        self,
        dataset: JournalDataset,
        *,
        as_of: datetime,
        window_start: datetime | None = None,
        filters: StatisticsFilters | Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        allow_mixed_versions: bool = False,
    ) -> StatisticsReport:
        """Recompute a report without mutating or writing any journal data.

        ``as_of`` is an inclusive event-time cutoff: source records use
        ``setup_as_of``, decision versions use ``decided_at``, and outcome
        versions use ``observed_through``. The cohort ``window_start`` is an
        inclusive lower bound on each record's ``setup_as_of``. No system clock,
        current Step 5/6 state, candles, or mutable aggregate is consulted.
        """

        if not isinstance(dataset, JournalDataset):
            raise TypeError("dataset must be an immutable JournalDataset")
        if type(allow_mixed_versions) is not bool:
            raise TypeError("allow_mixed_versions must be boolean")
        cutoff = require_utc_datetime(as_of, field_name="as_of")
        lower = (
            None
            if window_start is None
            else require_utc_datetime(window_start, field_name="window_start")
        )
        if lower is not None and lower > cutoff:
            raise ValueError("window_start must be <= as_of")
        normalized_filters = (
            filters
            if isinstance(filters, StatisticsFilters)
            else StatisticsFilters.from_mapping(filters)
        )
        requested_group_by = _normalize_group_by(group_by)
        effective_group_by = requested_group_by
        if not allow_mixed_versions:
            effective_group_by += tuple(
                dimension
                for dimension in VERSION_GROUP_BY
                if dimension not in effective_group_by
            )
        selected = _selected_records(
            dataset,
            cutoff=cutoff,
            window_start=lower,
            filters=normalized_filters,
        )
        profiles = _version_profiles(selected)
        mixed = _has_mixed_versions(profiles)
        # A pooled overall metric would silently combine incompatible Step 5/6/7
        # rules. When such versions coexist, only the automatically version-keyed
        # groups carry metrics unless the caller explicitly opts into mixing.
        overall = (
            _summarize_group(selected, key=(), config=self.config)
            if allow_mixed_versions or not mixed
            else None
        )
        report_quality = (
            _data_quality(selected) if overall is None else overall.data_quality
        )

        grouped: dict[tuple[tuple[str, str | None], ...], list[_SelectedRecord]] = (
            defaultdict(list)
        )
        for row in selected:
            key = tuple(
                (dimension, _dimension_value(row, dimension))
                for dimension in effective_group_by
            )
            grouped[key].append(row)
        groups = tuple(
            _summarize_group(grouped[key], key=key, config=self.config)
            for key in sorted(grouped, key=_group_sort_key)
        )
        source_identity = _source_identity(selected)
        source_fingerprint = sha256(
            json.dumps(
                to_jsonable(source_identity),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        report_identity = {
            "rules_version": self.config.rules_version,
            "config": self.config,
            "config_fingerprint": self.config.fingerprint(),
            "source_identity": source_identity,
            "filters": normalized_filters,
            "window_start": lower,
            "as_of": cutoff,
            "requested_group_by": requested_group_by,
            "effective_group_by": effective_group_by,
            "version_policy": (
                "MIXED_VERSIONS_EXPLICITLY_ALLOWED"
                if allow_mixed_versions
                else "SEPARATE_BY_RECORDED_VERSION"
            ),
        }
        report_id = sha256(
            (
                self.config.rules_version
                + ":"
                + json.dumps(
                    to_jsonable(report_identity),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ).encode()
        ).hexdigest()
        decision_ids = tuple(
            sorted(row.decision.decision_id for row in selected if row.decision)
        )
        outcome_ids = tuple(
            sorted(
                row.outcome_version.observation.id
                for row in selected
                if row.outcome_version is not None
            )
        )
        return StatisticsReport(
            report_id=report_id,
            rules_version=self.config.rules_version,
            config_fingerprint=self.config.fingerprint(),
            source_dataset_fingerprint=source_fingerprint,
            input_journal_ids=tuple(sorted(row.record.journal_id for row in selected)),
            input_decision_ids=decision_ids,
            input_outcome_ids=outcome_ids,
            filters=normalized_filters.criteria,
            window_start=lower,
            as_of=cutoff,
            requested_group_by=requested_group_by,
            effective_group_by=effective_group_by,
            version_policy=(
                "MIXED_VERSIONS_EXPLICITLY_ALLOWED"
                if allow_mixed_versions
                else "SEPARATE_BY_RECORDED_VERSION"
            ),
            mixed_versions=mixed,
            version_profiles=profiles,
            data_quality=report_quality,
            dimension_counts=_dimension_counts(selected),
            overall=overall,
            groups=groups,
        )

    def rolling_reports(
        self,
        dataset: JournalDataset,
        *,
        cutoffs: Sequence[datetime],
        lookback: timedelta,
        filters: StatisticsFilters | Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        allow_mixed_versions: bool = False,
    ) -> tuple[StatisticsReport, ...]:
        """Return fixed-width, cutoff-bounded sample windows in chronological order."""

        if not isinstance(lookback, timedelta) or lookback <= timedelta(0):
            raise ValueError("lookback must be a positive timedelta")
        normalized_cutoffs = tuple(
            require_utc_datetime(cutoff, field_name="cutoff") for cutoff in cutoffs
        )
        if any(
            left >= right
            for left, right in zip(normalized_cutoffs, normalized_cutoffs[1:])
        ):
            raise ValueError("rolling cutoffs must be strictly increasing")
        return tuple(
            self.analyze(
                dataset,
                as_of=cutoff,
                window_start=cutoff - lookback,
                filters=filters,
                group_by=group_by,
                allow_mixed_versions=allow_mixed_versions,
            )
            for cutoff in normalized_cutoffs
        )
