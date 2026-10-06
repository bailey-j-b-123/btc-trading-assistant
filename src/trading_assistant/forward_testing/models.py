"""Immutable Step 12 forward-ledger contracts and report types.

Everything here is an observation record or a derived report. There is no
order, fill, position, balance, leverage, size, or profit object anywhere in
this module, and nothing in it can execute, submit, or authorise anything.

A ``ForwardCycle`` is one processed base-candle close: exactly what the
deterministic Steps 2–6 produced at that boundary, with the data-health verdict
and the version fingerprints that produced it. A ``ForwardObservation`` is one
candidate (setup) state inside such a cycle. A ``PaperPlan`` is the frozen
projection of the first PLANNABLE Step 6 plan for one setup instance — nothing
is written for WATCH or NO_SETUP, and being PLANNABLE is not a recommendation.
A ``PaperOutcome`` version is a deterministic Step 7-style market observation of
a paper plan's proposed levels, appended (never overwritten) as candles close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_assistant.historical_validation.parameters import (
    FrictionAssumptions,
)
from trading_assistant.forward_testing.parameters import (
    CycleStatus,
    DataHealth,
    HeartbeatStatus,
    VersionSeparation,
)
from trading_assistant.historical_validation.models import (
    RateMetric,
    RDistribution,
    ValueCount,
)
from trading_assistant.journaling.types import OutcomeObservation, OutcomeStatus
from trading_assistant.market_data.types import CandleGap
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import Direction
from trading_assistant.setup_qualification.models import SetupFamily, SetupState
from trading_assistant.trade_planning.models import PlanState


@dataclass(frozen=True, slots=True)
class ForwardCycle:
    """One immutable record of a processed base-candle close.

    ``status`` says whether a real conclusion was reached; when it is anything
    other than ``COMPLETE`` the analytical fields stay ``None`` and the reason is
    recorded in ``notes``/``data_health_detail`` — a missing candle never
    becomes a trading conclusion.
    """

    cycle_id: str
    rules_version: str
    exchange: str
    symbol: str
    timeframe: str
    as_of: datetime
    candle_open_time: datetime
    recorded_at: datetime
    status: CycleStatus
    data_health: DataHealth
    data_health_detail: str
    staleness_intervals: int | None
    missing_candle_count: int
    latest_stored_candle: datetime | None
    snapshot_state: SetupState | None
    snapshot_id: str | None
    snapshot_json: str | None
    observation_count: int
    paper_plan_count: int
    setup_state_counts: tuple[tuple[str, int], ...]
    plan_state_counts: tuple[tuple[str, int], ...]
    structure_fingerprint: str | None
    pattern_fingerprint: str | None
    source_candle_count: int
    strategy_versions: tuple[tuple[str, str], ...]
    version_fingerprint: str
    explanation_context_fingerprint: str | None
    explanation_manifest_fingerprint: str | None
    explanation_id: str | None
    explanation_headline: str | None
    explanation_renderer_version: str | None
    market_data_json: str
    notes: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.status is CycleStatus.COMPLETE

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardObservation:
    """One candidate-state record inside one forward cycle.

    The row carries the exact Step 5 setup projection and (when a plan exists)
    the exact Step 6 plan projection as canonical JSON, so the record proves
    what the deterministic engine knew at that close even after later code or
    configuration changes. ``paper_plan_id`` is set only for a PLANNABLE plan.
    """

    observation_id: str
    cycle_id: str
    observation_rules_version: str
    exchange: str
    symbol: str
    timeframe: str
    source_timeframes: tuple[str, ...]
    as_of: datetime
    candle_open_time: datetime
    recorded_at: datetime
    snapshot_state: SetupState
    snapshot_id: str
    setup_id: str
    setup_family: SetupFamily
    setup_direction: Direction
    setup_state: SetupState
    setup_created_at: datetime
    setup_seed_event_id: str
    setup_reference_id: str
    setup_terminal_reason: str | None
    setup_ended_at: datetime | None
    passed_rules: tuple[str, ...]
    failed_rules: tuple[str, ...]
    pending_rules: tuple[str, ...]
    veto_rules: tuple[str, ...]
    setup_json: str
    plan_id: str | None
    plan_state: PlanState | None
    plan_json: str | None
    plan_entry: Decimal | None
    plan_invalidation: Decimal | None
    plan_stop: Decimal | None
    plan_risk_per_unit: Decimal | None
    plan_targets: tuple[Decimal, ...]
    plan_target_r_multiples: tuple[Decimal | None, ...]
    plan_config_fingerprint: str | None
    planning_rules_version: str | None
    paper_plan_id: str | None
    data_health: DataHealth
    missing_candle_count: int
    market_trend: str
    atr_percent_of_price: Decimal | None
    calendar_period: str
    structure_fingerprint: str | None
    pattern_fingerprint: str | None
    version_fingerprint: str

    @property
    def is_paper_observation(self) -> bool:
        """True only for the record that produced a paper plan."""

        return self.paper_plan_id is not None

    @property
    def plan_levels_complete(self) -> bool:
        """Whether every proposed level needed for observation is present."""

        return (
            self.plan_state is PlanState.PLANNABLE
            and self.plan_entry is not None
            and self.plan_stop is not None
            and self.plan_risk_per_unit is not None
            and bool(self.plan_targets)
        )

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class PaperPlan:
    """One frozen PLANNABLE plan projection whose market outcome is tracked.

    Exactly one paper plan exists per (exchange, symbol, timeframe, setup id):
    a setup instance that stays plannable for many candles does not become many
    observations, so entry/target rates keep honest denominators. Later cycles
    may record *observations* of that setup with a newer Step 6 plan id, but they
    never modify this frozen projection or create a second paper plan for it.
    """

    paper_plan_id: str
    observation_id: str
    cycle_id: str
    ledger_rules_version: str
    exchange: str
    symbol: str
    timeframe: str
    setup_id: str
    snapshot_id: str
    family: SetupFamily
    direction: Direction
    plan_id: str
    plan_as_of: datetime
    recorded_at: datetime
    entry: Decimal
    stop: Decimal
    invalidation: Decimal
    risk_per_unit: Decimal
    targets: tuple[Decimal, ...]
    target_r_multiples: tuple[Decimal | None, ...]
    plan_json: str
    plan_config_fingerprint: str
    planning_rules_version: str
    strategy_versions: tuple[tuple[str, str], ...]
    version_fingerprint: str
    friction: FrictionAssumptions
    friction_fingerprint: str
    forward_parameters_fingerprint: str
    observation_horizon_candles: int
    data_health: DataHealth

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class PaperOutcome:
    """One append-only outcome version of one paper plan's proposed levels.

    ``observation`` is the unchanged Step 7 deterministic market observation;
    the surrounding fields are the version chain (``sequence``,
    ``supersedes_outcome_id``) that keeps every earlier observation recoverable.
    Nothing here is an executed trade or a realised result.
    """

    outcome_id: str
    paper_plan_id: str
    observation_id: str
    sequence: int
    supersedes_outcome_id: str | None
    ledger_rules_version: str
    observation_rules_version: str
    config_fingerprint: str
    recorded_at: datetime
    payload_json: str
    observation: OutcomeObservation

    @property
    def status(self):
        return self.observation.status

    @property
    def observed_through(self) -> datetime:
        return self.observation.observed_through

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


#: Outcome statuses whose trajectory can never change, whatever candles arrive.
FINAL_OUTCOME_STATUSES = frozenset(
    {
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        OutcomeStatus.STOPPED,
        OutcomeStatus.STOPPED_AFTER_TARGETS,
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.AMBIGUOUS,
    }
)


def paper_outcome_is_settled(outcome: PaperOutcome, plan: PaperPlan) -> bool:
    """Whether a paper observation's outcome can still change as candles close.

    ``ENTRY_NOT_REACHED`` is only final once the configured observation horizon
    has been fully observed: before that, the entry may still be reached, so
    forward tracking keeps observing instead of freezing a premature verdict.
    ``AMBIGUOUS``, ``INCOMPLETE_DATA`` and ``OPEN_AT_CUTOFF`` are never
    reinterpreted as a favourable result.
    """

    status = outcome.observation.status
    if status in FINAL_OUTCOME_STATUSES:
        return True
    if status is OutcomeStatus.ENTRY_NOT_REACHED:
        interval = interval_for_timeframe(plan.timeframe)
        horizon_last_open = plan.plan_as_of + interval * (
            plan.observation_horizon_candles - 1
        )
        return outcome.observation.observed_through >= horizon_last_open
    return False


@dataclass(frozen=True, slots=True)
class ForwardHeartbeat:
    """One append-only statement about the runner process itself."""

    heartbeat_id: str
    runner_rules_version: str
    recorded_at: datetime
    status: HeartbeatStatus
    exchange: str
    symbol: str
    timeframe: str
    detail: str
    cycles_processed: int
    observations_recorded: int
    paper_plans_created: int
    outcomes_recorded: int
    pending_boundaries: int
    latest_cycle_as_of: datetime | None
    last_error: str | None
    error_type: str | None
    market_data_json: str

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardLedger:
    """One read-only snapshot of the whole forward ledger for reporting."""

    cycles: tuple[ForwardCycle, ...]
    observations: tuple[ForwardObservation, ...]
    paper_plans: tuple[PaperPlan, ...]
    latest_outcomes: tuple[PaperOutcome, ...]
    version_fingerprints: tuple[str, ...]
    pending_catch_up_boundaries: int

    @property
    def version_separation(self) -> VersionSeparation:
        """Whether recorded cycles may be combined, or must stay separated."""

        return (
            VersionSeparation.SINGLE_VERSION
            if len(self.version_fingerprints) <= 1
            else VersionSeparation.SEPARATED
        )


@dataclass(frozen=True, slots=True)
class ForwardCohortMetrics:
    """Denominated forward-paper metrics for one version cohort or breakdown.

    Rates whose denominator is 0 report an explicit INSUFFICIENT_DATA status with
    numerator 0 and denominator 0: a rate is never invented from no sample.
    """

    total_cycles: int
    complete_cycles: int
    incomplete_cycles: int
    no_setup_cycles: int
    data_health_counts: tuple[ValueCount, ...]
    distinct_boundaries: int
    observation_records: int
    distinct_setup_ids: int
    setup_state_counts: tuple[ValueCount, ...]
    plan_state_counts: tuple[ValueCount, ...]
    paper_plan_count: int
    paper_plans_with_outcome: int
    unresolved_count: int
    completed_count: int
    ambiguous_count: int
    incomplete_data_count: int
    outcome_status_counts: tuple[ValueCount, ...]
    entry_reached_rate: RateMetric
    entry_not_reached_rate: RateMetric
    stopped_rate: RateMetric
    ambiguous_rate: RateMetric
    incomplete_rate: RateMetric
    unresolved_rate: RateMetric
    target_hit_rates: tuple[RateMetric, ...]
    raw_observational_r: RDistribution
    friction_adjusted_hypothetical_r: RDistribution

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardBreakdown:
    dimension: str
    value: str
    metrics: ForwardCohortMetrics

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardVersionCohort:
    """One explicit version cohort of forward cycles (never silently merged)."""

    version_fingerprint: str
    strategy_versions: tuple[tuple[str, str], ...]
    first_as_of: datetime | None
    last_as_of: datetime | None
    metrics: ForwardCohortMetrics

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardReport:
    """A canonical, reproducible derived Step 12 forward statistics artifact."""

    report_id: str
    rules_version: str
    parameters_fingerprint: str
    version_separation: VersionSeparation
    version_fingerprints: tuple[str, ...]
    combined_metrics_available: bool
    combined_metrics_unavailable_reason: str | None
    friction: FrictionAssumptions
    friction_fingerprint: str
    exchange: str
    symbol: str
    timeframe: str
    first_cycle_as_of: datetime | None
    last_cycle_as_of: datetime | None
    pending_catch_up_boundaries: int
    metrics: ForwardCohortMetrics
    version_cohorts: tuple[ForwardVersionCohort, ...]
    breakdowns: tuple[ForwardBreakdown, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ComparisonSide:
    """One side of the historical-versus-forward comparison, with its labels."""

    label: str
    disclaimer: str
    available: bool
    unavailable_reason: str | None
    sample_size: int
    metrics: ForwardCohortMetrics

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    metric: str
    definition: str
    historical: Decimal | int | None
    forward: Decimal | int | None
    historical_denominator: int
    forward_denominator: int
    comparable: bool | None
    note: str | None

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ForwardComparison:
    """Explicitly labelled HISTORICAL VALIDATION vs LIVE FORWARD comparison."""

    generated_at: datetime
    exchange: str
    symbol: str
    timeframe: str
    strategy_versions_match: bool | None
    version_comparability_note: str
    historical: ComparisonSide
    forward: ComparisonSide
    rows: tuple[ComparisonRow, ...]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


__all__ = [
    "CandleGap",
    "FINAL_OUTCOME_STATUSES",
    "ForwardBreakdown",
    "ForwardCohortMetrics",
    "ForwardComparison",
    "ForwardCycle",
    "ForwardHeartbeat",
    "ForwardLedger",
    "ForwardObservation",
    "ForwardReport",
    "ForwardVersionCohort",
    "PaperOutcome",
    "PaperPlan",
    "paper_outcome_is_settled",
    "ComparisonRow",
    "ComparisonSide",
]
