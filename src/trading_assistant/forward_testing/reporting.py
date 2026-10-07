"""Step 12 forward reporting: derived statistics and the historical comparison.

Both builders are pure functions over stored, immutable records: the same ledger
always produces the same report, and no builder writes, fetches, or recomputes a
forward decision. Everything is denominated: a percentage is only produced
together with its exact numerator and denominator, and ambiguous, incomplete,
unresolved, and entry-not-reached observations stay visible instead of being
folded into a win/loss rate.

Two clearly separated quantities exist and are never conflated:

* **raw observational R** — the OHLC distance from the proposed entry to the
  terminal proposed level, in proposed-risk units, of a clean paper outcome;
* **friction-adjusted hypothetical R** — the same endpoint after explicit
  adverse slippage and fees under the versioned Step 11 friction assumptions.

Neither is realised profit, and neither is a P&L statement.

Paper trading and historical performance do not establish future profitability.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from itertools import groupby
from decimal import Decimal
from statistics import median

from trading_assistant.market_structure.numeric import require_int

from trading_assistant.forward_testing.models import (
    ComparisonRow,
    ComparisonSide,
    ForwardBreakdown,
    ForwardCohortMetrics,
    ForwardComparison,
    ForwardCycle,
    ForwardLedger,
    ForwardObservation,
    ForwardReport,
    ForwardVersionCohort,
    PaperOutcome,
    PaperPlan,
    paper_outcome_is_settled,
)
from trading_assistant.forward_testing.parameters import (
    FORWARD_LEDGER_RULES_VERSION,
    DataHealth,
    ForwardParameters,
    VersionSeparation,
    fingerprint,
)
from trading_assistant.historical_validation.metrics import (
    CLEAN_OUTCOMES,
    counts,
    distribution,
    enum_text,
    friction_adjusted_r,
    rate,
    r_values,
)
from trading_assistant.historical_validation.models import (
    RDistribution,
    RateMetric,
    ValidationReport,
    ValueCount,
)
from trading_assistant.historical_validation.parameters import FrictionAssumptions
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.setup_qualification.models import SetupState

#: Stated limitations of every forward report.
FORWARD_REPORT_LIMITATIONS: tuple[str, ...] = (
    "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE. These are paper "
    "observations of closed candles; nothing was executed, filled, sized, or "
    "realised.",
    "PAPER OBSERVATION — NO REAL ORDER. The runner records what the "
    "deterministic engine proposed at each closed candle; being PLANNABLE is not "
    "a recommendation, a prediction, or a profitable trade.",
    "Paper trading and historical performance do not establish future "
    "profitability.",
    "One paper plan exists per setup instance; entry, target and stop rates count "
    "distinct paper plans, never candle-by-candle repeats.",
    "A proposed level is only counted as touched when the closed candle's range "
    "evidences it. Same-candle ambiguity is recorded as AMBIGUOUS and is never "
    "resolved in the favourable direction.",
    "Ambiguous, incomplete, entry-not-reached, and still-open observations are "
    "reported separately and are never converted into wins or losses.",
    "Raw observational R and friction-adjusted hypothetical R are unit-neutral "
    "market observations, never realised profit and never a P&L statement.",
    "Percentages are withheld below the configured reporting floor; the exact "
    "numerator and denominator are always shown.",
    "Historical validation and live forward observations describe different "
    "market periods and count different units; the comparison is descriptive and "
    "is not a statistical test.",
    "A poor forward result is reported as it is: no rule, threshold, or parameter "
    "is adjusted to make forward results resemble historical ones.",
)

#: Number of prior observations required before a volatility split is used.
VOLATILITY_WARMUP_OBSERVATIONS = 5

_LOWER_VOLATILITY = "LOWER_OR_EQUAL_VOLATILITY"
_HIGHER_VOLATILITY = "HIGHER_VOLATILITY"
_INSUFFICIENT_VOLATILITY = "INSUFFICIENT_VOLATILITY_HISTORY"
_VOLATILITY_UNAVAILABLE = "VOLATILITY_UNAVAILABLE"

_UNKNOWN = "UNKNOWN"


# ----------------------------------------------------------------------
# Forward report
# ----------------------------------------------------------------------


def build_forward_report(
    *,
    ledger: ForwardLedger,
    exchange: str,
    symbol: str,
    timeframe: str,
    parameters: ForwardParameters | None = None,
    friction: FrictionAssumptions | None = None,
) -> ForwardReport:
    """Derive one reproducible forward report from the stored ledger."""

    config = parameters if parameters is not None else ForwardParameters()
    assumptions = friction if friction is not None else config.friction
    if not isinstance(assumptions, FrictionAssumptions):
        raise TypeError("friction must be FrictionAssumptions")
    fingerprints = ledger.version_fingerprints
    separated = ledger.version_separation is VersionSeparation.SEPARATED

    cohorts: list[ForwardVersionCohort] = []
    for version_fingerprint in fingerprints:
        cohort_cycles = tuple(
            cycle
            for cycle in ledger.cycles
            if cycle.version_fingerprint == version_fingerprint
        )
        cohort_observations = tuple(
            item
            for item in ledger.observations
            if item.version_fingerprint == version_fingerprint
        )
        cohort_plans = tuple(
            plan
            for plan in ledger.paper_plans
            if plan.version_fingerprint == version_fingerprint
        )
        plan_ids = {plan.paper_plan_id for plan in cohort_plans}
        cohort_outcomes = tuple(
            outcome
            for outcome in ledger.latest_outcomes
            if outcome.paper_plan_id in plan_ids
        )
        cohorts.append(
            ForwardVersionCohort(
                version_fingerprint=version_fingerprint,
                strategy_versions=_strategy_versions_for(
                    version_fingerprint, cohort_cycles, cohort_plans
                ),
                first_as_of=cohort_cycles[0].as_of if cohort_cycles else None,
                last_as_of=cohort_cycles[-1].as_of if cohort_cycles else None,
                metrics=_metrics(
                    cycles=cohort_cycles,
                    observations=cohort_observations,
                    paper_plans=cohort_plans,
                    latest_outcomes=cohort_outcomes,
                    parameters=config,
                    friction=assumptions,
                ),
            )
        )

    if separated:
        combined = _empty_metrics()
        reason = (
            "recorded forward cycles carry "
            f"{len(fingerprints)} different strategy/config version fingerprints; "
            "results produced by different rules are never combined. Read the "
            "per-version cohorts instead."
        )
    else:
        combined = _metrics(
            cycles=ledger.cycles,
            observations=ledger.observations,
            paper_plans=ledger.paper_plans,
            latest_outcomes=ledger.latest_outcomes,
            parameters=config,
            friction=assumptions,
        )
        reason = None

    breakdowns = _breakdowns(
        cycles=ledger.cycles,
        observations=ledger.observations,
        paper_plans=ledger.paper_plans,
        latest_outcomes=ledger.latest_outcomes,
        parameters=config,
        friction=assumptions,
    )
    warnings = _warnings(
        metrics=combined,
        ledger=ledger,
        separated=separated,
        parameters=config,
        friction=assumptions,
    )
    cycles = ledger.cycles
    report_id = fingerprint(
        "forward-report",
        exchange,
        symbol,
        timeframe,
        tuple(cycle.cycle_id for cycle in cycles),
        tuple(plan.paper_plan_id for plan in ledger.paper_plans),
        tuple(outcome.outcome_id for outcome in ledger.latest_outcomes),
        config.fingerprint(),
        assumptions.fingerprint(),
    )
    return ForwardReport(
        report_id=report_id,
        rules_version=FORWARD_LEDGER_RULES_VERSION,
        parameters_fingerprint=config.fingerprint(),
        version_separation=ledger.version_separation,
        version_fingerprints=fingerprints,
        combined_metrics_available=not separated,
        combined_metrics_unavailable_reason=reason,
        friction=assumptions,
        friction_fingerprint=assumptions.fingerprint(),
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        first_cycle_as_of=cycles[0].as_of if cycles else None,
        last_cycle_as_of=cycles[-1].as_of if cycles else None,
        pending_catch_up_boundaries=ledger.pending_catch_up_boundaries,
        metrics=combined,
        version_cohorts=tuple(cohorts),
        breakdowns=breakdowns,
        warnings=warnings,
        limitations=FORWARD_REPORT_LIMITATIONS,
    )


def build_forward_metrics_payload(
    *,
    ledger: ForwardLedger,
    exchange: str,
    symbol: str,
    timeframe: str,
    parameters: ForwardParameters | None = None,
    friction: FrictionAssumptions | None = None,
) -> dict[str, object]:
    """The forward statistics payload used by the dashboard/API."""

    report = build_forward_report(
        ledger=ledger,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        parameters=parameters,
        friction=friction,
    )
    return report.to_json_dict()


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------


def _empty_metrics() -> ForwardCohortMetrics:
    return _metrics(
        cycles=(),
        observations=(),
        paper_plans=(),
        latest_outcomes=(),
        parameters=ForwardParameters(),
        friction=FrictionAssumptions(),
    )


def _outcome_for(
    latest_outcomes: Sequence[PaperOutcome], paper_plan_id: str
) -> PaperOutcome | None:
    """The newest stored outcome for one paper plan, if any."""

    for outcome in latest_outcomes:
        if outcome.paper_plan_id == paper_plan_id:
            return outcome
    return None


def _known_entry(outcome: PaperOutcome) -> bool:
    """Whether the entry-touch state of one paper observation is known yet."""

    observation = outcome.observation
    return observation.entry_reached or observation.status in {
        OutcomeStatus.ENTRY_NOT_REACHED,
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
    }


def _metrics(
    *,
    cycles: Sequence[ForwardCycle],
    observations: Sequence[ForwardObservation],
    paper_plans: Sequence[PaperPlan],
    latest_outcomes: Sequence[PaperOutcome],
    parameters: ForwardParameters,
    friction: FrictionAssumptions,
) -> ForwardCohortMetrics:
    """Denominated forward-paper metrics for one cohort."""

    plans_by_id = {plan.paper_plan_id: plan for plan in paper_plans}
    outcomes = [outcome.observation for outcome in latest_outcomes]
    settled = sum(
        paper_outcome_is_settled(outcome, plans_by_id[outcome.paper_plan_id])
        for outcome in latest_outcomes
        if outcome.paper_plan_id in plans_by_id
    )
    ambiguous = sum(item.status is OutcomeStatus.AMBIGUOUS for item in outcomes)
    incomplete_data = sum(
        item.status is OutcomeStatus.INCOMPLETE_DATA for item in outcomes
    )
    unresolved = 0
    for plan in paper_plans:
        latest = _outcome_for(latest_outcomes, plan.paper_plan_id)
        if latest is None or not paper_outcome_is_settled(latest, plan):
            unresolved += 1

    known_entry = [outcome for outcome in latest_outcomes if _known_entry(outcome)]
    entry_denominator = len(known_entry)
    entry_rate = rate(
        "entry_reached_rate",
        sum(outcome.observation.entry_reached for outcome in known_entry),
        entry_denominator,
        "paper observations whose proposed entry was touched so far / paper "
        "observations with a known entry-touch state so far (a still-open "
        "observation can change until its horizon completes)",
        parameters.minimum_sample_size,
    )
    entry_not_reached_rate = rate(
        "entry_not_reached_rate",
        sum(
            outcome.observation.entry_reached is False for outcome in known_entry
        ),
        entry_denominator,
        "paper observations whose observed window so far never touched the proposed "
        "entry (including plans invalidated before entry) / paper observations "
        "with a known entry-touch state so far (an interim value can change until "
        "the horizon completes)",
        parameters.minimum_sample_size,
    )

    clean_ordered = [
        outcome
        for outcome in latest_outcomes
        if outcome.observation.status in CLEAN_OUTCOMES
        and outcome.observation.entry_ordered
        and not outcome.observation.stop_pre_entry
    ]
    stopped_rate = rate(
        "stopped_rate_after_ordered_entry",
        sum(outcome.observation.stop_reached for outcome in clean_ordered),
        len(clean_ordered),
        "paper observations whose proposed stop was touched after an ordered "
        "entry / clean paper observations with an ordered entry",
        parameters.minimum_sample_size,
    )
    ambiguous_rate = rate(
        "ambiguous_rate",
        ambiguous,
        len(outcomes),
        "paper observations ending AMBIGUOUS / paper observations with any "
        "recorded outcome",
        parameters.minimum_sample_size,
    )
    incomplete_rate = rate(
        "incomplete_data_rate",
        incomplete_data,
        len(outcomes),
        "paper observations ending INCOMPLETE_DATA / paper observations with any "
        "recorded outcome",
        parameters.minimum_sample_size,
    )
    unresolved_rate = rate(
        "unresolved_rate",
        unresolved,
        len(paper_plans),
        "paper observations without a settled outcome / distinct paper plans",
        parameters.minimum_sample_size,
    )
    target_count = max(
        (len(outcome.observation.target_levels) for outcome in latest_outcomes),
        default=0,
    )
    target_rates = tuple(
        rate(
            f"target_{index + 1}_hit_rate",
            sum(
                index in outcome.observation.targets_reached
                for outcome in clean_ordered
                if index < len(outcome.observation.target_levels)
            ),
            sum(
                index < len(outcome.observation.target_levels)
                for outcome in clean_ordered
            ),
            f"ordered target T{index + 1} reaches / clean ordered-entry paper "
            f"observations proposing T{index + 1}",
            parameters.minimum_sample_size,
        )
        for index in range(target_count)
    )
    raw_values, excluded, endpoints = r_values(outcomes)
    friction_values = tuple(
        friction_adjusted_r(item, endpoint, friction) for item, endpoint in endpoints
    )
    return ForwardCohortMetrics(
        total_cycles=len(cycles),
        complete_cycles=sum(cycle.complete for cycle in cycles),
        incomplete_cycles=sum(not cycle.complete for cycle in cycles),
        no_setup_cycles=sum(
            cycle.snapshot_state is None
            or cycle.snapshot_state is SetupState.NO_SETUP
            for cycle in cycles
        ),
        data_health_counts=counts(enum_text(cycle.data_health) for cycle in cycles),
        distinct_boundaries=len({cycle.as_of for cycle in cycles}),
        observation_records=len(observations),
        distinct_setup_ids=len({item.setup_id for item in observations}),
        setup_state_counts=counts(enum_text(item.setup_state) for item in observations),
        plan_state_counts=counts(
            _UNKNOWN if item.plan_state is None else enum_text(item.plan_state)
            for item in observations
        ),
        paper_plan_count=len(paper_plans),
        paper_plans_with_outcome=len(latest_outcomes),
        unresolved_count=unresolved,
        completed_count=settled,
        ambiguous_count=ambiguous,
        incomplete_data_count=incomplete_data,
        outcome_status_counts=counts(enum_text(item.status) for item in outcomes),
        entry_reached_rate=entry_rate,
        entry_not_reached_rate=entry_not_reached_rate,
        stopped_rate=stopped_rate,
        ambiguous_rate=ambiguous_rate,
        incomplete_rate=incomplete_rate,
        unresolved_rate=unresolved_rate,
        target_hit_rates=target_rates,
        raw_observational_r=distribution(
            "raw_observational_R",
            raw_values,
            excluded,
            len(outcomes),
            parameters.minimum_sample_size,
            "Proposed-level OHLC observation of a clean terminal paper outcome: "
            "STOPPED uses the proposed stop, TARGETS_REACHED uses the furthest "
            "reached proposed target. It is not realised profit and not P&L.",
        ),
        friction_adjusted_hypothetical_r=distribution(
            "friction_adjusted_hypothetical_R",
            friction_values,
            excluded,
            len(outcomes),
            parameters.minimum_sample_size,
            "The same eligible paper endpoints after the configured adverse entry "
            "slippage, exit slippage, and two-sided fees. Hypothetical only: never "
            "realised profit and never a P&L statement.",
        ),
    )


# ----------------------------------------------------------------------
# Breakdowns
# ----------------------------------------------------------------------


def _breakdowns(
    *,
    cycles: Sequence[ForwardCycle],
    observations: Sequence[ForwardObservation],
    paper_plans: Sequence[PaperPlan],
    latest_outcomes: Sequence[PaperOutcome],
    parameters: ForwardParameters,
    friction: FrictionAssumptions,
) -> tuple[ForwardBreakdown, ...]:
    """Descriptive breakdowns over distinct paper observations.

    Every value here is read from the recorded observation at paper-plan time.
    No breakdown value is ever fed back into Steps 3–6: rules are not tunable
    from this report.
    """

    if not paper_plans:
        return ()
    outcomes_by_plan = {outcome.paper_plan_id: outcome for outcome in latest_outcomes}
    plan_created_by: dict[str, ForwardObservation] = {}
    for observation in observations:
        if observation.paper_plan_id is None:
            continue
        plan_created_by.setdefault(observation.paper_plan_id, observation)
    volatility_labels = _volatility_labels(observations)
    dimensions: dict[str, dict[str, list[PaperPlan]]] = {}
    for plan in paper_plans:
        observation = plan_created_by.get(plan.paper_plan_id)
        values = {
            "setup_family": enum_text(plan.family),
            "direction": enum_text(plan.direction),
            "timeframe": plan.timeframe,
            "market_trend": (
                _UNKNOWN if observation is None else observation.market_trend
            ),
            "volatility_context": volatility_labels.get(
                None if observation is None else observation.observation_id,
                _VOLATILITY_UNAVAILABLE,
            ),
            "calendar_period": (
                _UNKNOWN if observation is None else observation.calendar_period
            ),
            "data_health_at_plan": enum_text(plan.data_health),
            "snapshot_state_at_plan": (
                _UNKNOWN if observation is None else enum_text(observation.snapshot_state)
            ),
        }
        for dimension, value in values.items():
            dimensions.setdefault(dimension, {}).setdefault(value, []).append(plan)

    rows: list[ForwardBreakdown] = []
    for dimension in sorted(dimensions):
        for value in sorted(dimensions[dimension]):
            group = dimensions[dimension][value]
            group_ids = {plan.paper_plan_id for plan in group}
            rows.append(
                ForwardBreakdown(
                    dimension=dimension,
                    value=value,
                    metrics=_metrics(
                        cycles=(),
                        observations=tuple(
                            item for item in observations if item.paper_plan_id in group_ids
                        ),
                        paper_plans=tuple(group),
                        latest_outcomes=tuple(
                            outcomes_by_plan[paper_plan_id]
                            for paper_plan_id in group_ids
                            if paper_plan_id in outcomes_by_plan
                        ),
                        parameters=parameters,
                        friction=friction,
                    ),
                )
            )
    return tuple(rows)


def _volatility_labels(
    observations: Sequence[ForwardObservation],
) -> dict[str, str]:
    """Causal volatility context per observation: each value uses only earlier ones."""

    labels: dict[str, str] = {}
    history: list[Decimal] = []
    # Observations share one close: siblings at the same close are labelled
    # against the pre-close history together, and only join the history once
    # the whole close is labelled. A same-close value can therefore never move
    # the median its own close is judged against.
    for _, group in groupby(observations, key=lambda item: item.as_of):
        batch = list(group)
        for observation in batch:
            value = observation.atr_percent_of_price
            if value is None:
                labels[observation.observation_id] = _VOLATILITY_UNAVAILABLE
            elif len(history) < VOLATILITY_WARMUP_OBSERVATIONS:
                labels[observation.observation_id] = _INSUFFICIENT_VOLATILITY
            else:
                middle = median(history)
                labels[observation.observation_id] = (
                    _HIGHER_VOLATILITY if value > middle else _LOWER_VOLATILITY
                )
        history.extend(
            observation.atr_percent_of_price
            for observation in batch
            if observation.atr_percent_of_price is not None
        )
    return labels


def _strategy_versions_for(
    version_fingerprint: str,
    cycles: Sequence[ForwardCycle],
    plans: Sequence[PaperPlan],
) -> tuple[tuple[str, str], ...]:
    for cycle in cycles:
        if cycle.version_fingerprint == version_fingerprint:
            return cycle.strategy_versions
    for plan in plans:
        if plan.version_fingerprint == version_fingerprint:
            return plan.strategy_versions
    return ()


# ----------------------------------------------------------------------
# Warnings
# ----------------------------------------------------------------------


def _warnings(
    *,
    metrics: ForwardCohortMetrics,
    ledger: ForwardLedger,
    separated: bool,
    parameters: ForwardParameters,
    friction: FrictionAssumptions,
) -> tuple[str, ...]:
    warnings: list[str] = []
    if not ledger.cycles:
        warnings.append("no_forward_cycles_recorded")
    if separated:
        warnings.append(
            f"combined_metrics_withheld_multiple_version_fingerprints:{len(ledger.version_fingerprints)}"
        )
        if ledger.paper_plans:
            # The per-dimension breakdowns below still describe the whole
            # ledger, so when versions are separated they span rule sets whose
            # results are never combined elsewhere. Stated, never silent.
            warnings.append(
                "breakdowns_span_multiple_version_fingerprints:"
                f"{len(ledger.version_fingerprints)}"
            )
    if metrics.paper_plan_count < parameters.minimum_sample_size:
        warnings.append(
            f"insufficient_paper_observation_sample:{metrics.paper_plan_count}/"
            f"{parameters.minimum_sample_size}"
        )
    if metrics.raw_observational_r.sample_size < parameters.minimum_sample_size:
        warnings.append(
            "insufficient_raw_observational_R_sample:"
            f"{metrics.raw_observational_r.sample_size}/"
            f"{parameters.minimum_sample_size}"
        )
    if metrics.ambiguous_count:
        warnings.append(f"ambiguous_outcomes_separate:{metrics.ambiguous_count}")
    if metrics.incomplete_data_count:
        warnings.append(f"incomplete_outcomes_separate:{metrics.incomplete_data_count}")
    if metrics.unresolved_count:
        warnings.append(f"unresolved_paper_plans:{metrics.unresolved_count}")
    incomplete_cycles = metrics.incomplete_cycles
    if incomplete_cycles:
        warnings.append(f"cycles_without_a_complete_conclusion:{incomplete_cycles}")
    stale_cycles = sum(
        count
        for item in metrics.data_health_counts
        for count in [item.count]
        if item.value in {DataHealth.STALE.value, DataHealth.UNKNOWN.value}
    )
    if stale_cycles:
        warnings.append(f"cycles_recorded_with_stale_or_unknown_data:{stale_cycles}")
    health_counts = {item.value: item.count for item in metrics.data_health_counts}
    incomplete_health = health_counts.get(DataHealth.INCOMPLETE.value, 0)
    if incomplete_health:
        warnings.append(f"cycles_recorded_on_incomplete_windows:{incomplete_health}")
    if ledger.pending_catch_up_boundaries:
        warnings.append(
            f"catch_up_incomplete_pending_boundaries:{ledger.pending_catch_up_boundaries}"
        )
    if (
        friction.fee_bps
        or friction.entry_slippage_bps
        or friction.exit_slippage_bps
    ):
        raw = metrics.raw_observational_r.average
        adjusted = metrics.friction_adjusted_hypothetical_r.average
        if raw is not None and adjusted is not None and raw != adjusted:
            warnings.append("configured_friction_changes_hypothetical_R")
    return tuple(sorted(set(warnings)))


# ----------------------------------------------------------------------
# Historical-versus-forward comparison
# ----------------------------------------------------------------------


def build_forward_comparison(
    *,
    forward: ForwardReport,
    historical: ValidationReport | None,
    generated_at: datetime,
    limitations: Sequence[str] = FORWARD_REPORT_LIMITATIONS,
    historical_minimum_sample_size: int | None = None,
) -> ForwardComparison:
    """Side-by-side HISTORICAL VALIDATION vs LIVE FORWARD PAPER OBSERVATIONS.

    The two sides are deliberately *not* merged into a single number. The
    historical side counts Step 11 replay records; the forward side counts
    distinct paper plans, so denominators differ by construction and every row
    carries both. Where an upstream rule version differs, the comparison is
    flagged as not comparable instead of being quietly presented as one result.

    ``historical_minimum_sample_size`` is the Step 11 reporting floor the
    historical side was computed with. The status-share rates recomputed here
    (entry-not-reached, ambiguous, incomplete, unresolved) are judged against
    it, so a small historical sample can never show SUFFICIENT next to a
    forward side judged against its own floor. ``None`` preserves the legacy
    floor of 1; callers that know the Step 11 floor must pass it.
    """

    if historical_minimum_sample_size is None:
        historical_floor = 1
    else:
        historical_floor = require_int(
            historical_minimum_sample_size,
            name="historical_minimum_sample_size",
            minimum=1,
            maximum=1_000_000,
        )

    # When recorded cycles span incompatible version fingerprints the combined
    # forward metrics are withheld, so the side is reported as unavailable rather
    # than presenting an emptied contract as a live zero sample.
    forward_available = forward.combined_metrics_available
    forward_side = ComparisonSide(
        label="LIVE FORWARD PAPER OBSERVATIONS",
        disclaimer=(
            "PAPER OBSERVATION — NO REAL ORDER. Forward figures describe recorded "
            "closed-candle observations of paper plans produced by the live "
            "runner. They are not executed trades and not real performance."
        ),
        available=forward_available,
        unavailable_reason=(
            None if forward_available else forward.combined_metrics_unavailable_reason
        ),
        sample_size=(
            forward.metrics.paper_plan_count
            if forward_available
            else sum(
                cohort.metrics.paper_plan_count
                for cohort in forward.version_cohorts
            )
        ),
        metrics=forward.metrics,
    )
    if historical is None:
        historical_side = ComparisonSide(
            label="HISTORICAL VALIDATION",
            disclaimer="HISTORICAL VALIDATION — NOT LIVE PERFORMANCE.",
            available=False,
            unavailable_reason=(
                "no historical validation report was computed for this request"
            ),
            sample_size=0,
            metrics=_empty_metrics(),
        )
        strategy_versions_match = None
        version_note = (
            "the historical side is unavailable, so versions could not be compared"
        )
    else:
        historical_metrics = _historical_metrics(
            historical, minimum_sample_size=historical_floor
        )
        historical_side = ComparisonSide(
            label="HISTORICAL VALIDATION",
            disclaimer=(
                "HISTORICAL VALIDATION — NOT LIVE PERFORMANCE. Derived replay over "
                "stored candles; not fills and not realised profit."
            ),
            available=True,
            unavailable_reason=None,
            sample_size=historical_metrics.paper_plan_count,
            metrics=historical_metrics,
        )
        strategy_versions_match, version_note = _versions_match(forward, historical)

    rows = _comparison_rows(
        forward=forward_side,
        historical=historical_side,
        comparable=strategy_versions_match,
    )
    warnings: list[str] = []
    if strategy_versions_match is False:
        warnings.append("strategy_versions_differ_comparison_is_descriptive_only")
    warnings.append(
        "denominators_differ_historical_counts_replay_records_forward_counts_paper_plans"
    )
    if not forward.combined_metrics_available:
        warnings.append("forward_combined_metrics_withheld_version_separation")
    if forward.metrics.paper_plan_count < forward.metrics.raw_observational_r.sample_size:
        warnings.append("forward_paper_sample_smaller_than_eligible_R_sample")
    warnings.extend(forward.warnings)
    return ForwardComparison(
        generated_at=generated_at,
        exchange=forward.exchange,
        symbol=forward.symbol,
        timeframe=forward.timeframe,
        strategy_versions_match=strategy_versions_match,
        version_comparability_note=version_note,
        historical=historical_side,
        forward=forward_side,
        rows=rows,
        warnings=tuple(sorted(set(warnings))),
        limitations=tuple(limitations)
        + (
            "Historical and forward samples describe different market periods and "
            "different units of analysis; the comparison is descriptive and is not "
            "a statistical test.",
        ),
    )


def _historical_metrics(
    report: ValidationReport, *, minimum_sample_size: int
) -> ForwardCohortMetrics:
    """Map a Step 11 cohort onto the shared forward metric contract."""

    cohort = report.out_of_sample or report.development
    metrics = cohort.metrics
    plan_counts = {item.value: item.count for item in metrics.plan_state_counts}
    return ForwardCohortMetrics(
        total_cycles=metrics.total_records,
        complete_cycles=metrics.total_records,
        incomplete_cycles=0,
        no_setup_cycles=0,
        data_health_counts=(),
        distinct_boundaries=cohort.decision_boundary_count,
        observation_records=metrics.setup_records,
        distinct_setup_ids=metrics.distinct_setup_ids,
        setup_state_counts=metrics.setup_state_counts,
        plan_state_counts=metrics.plan_state_counts,
        paper_plan_count=plan_counts.get("PLANNABLE", 0),
        paper_plans_with_outcome=metrics.outcomes_observed_count,
        unresolved_count=metrics.unresolved_count,
        completed_count=metrics.completed_count,
        ambiguous_count=metrics.ambiguous_count,
        incomplete_data_count=metrics.incomplete_count,
        outcome_status_counts=metrics.outcome_status_counts,
        entry_reached_rate=metrics.entry_reached_rate,
        entry_not_reached_rate=_historical_status_rate(
            metrics.outcome_status_counts,
            "ENTRY_NOT_REACHED",
            minimum_sample_size,
        ),
        stopped_rate=metrics.stop_rate_after_ordered_entry,
        ambiguous_rate=_historical_status_rate(
            metrics.outcome_status_counts, "AMBIGUOUS", minimum_sample_size
        ),
        incomplete_rate=_historical_status_rate(
            metrics.outcome_status_counts, "INCOMPLETE_DATA", minimum_sample_size
        ),
        unresolved_rate=_historical_status_rate(
            metrics.outcome_status_counts, "OPEN_AT_CUTOFF", minimum_sample_size
        ),
        target_hit_rates=metrics.target_hit_rates,
        raw_observational_r=metrics.raw_observational_r,
        friction_adjusted_hypothetical_r=metrics.friction_adjusted_hypothetical_r,
    )


def _historical_status_rate(
    status_counts: Sequence[ValueCount], status: str, minimum_sample_size: int
) -> RateMetric:
    """A denominated rate computed from a historical cohort's status counts."""

    counts_by_status = {item.value: item.count for item in status_counts}
    numerator = counts_by_status.get(status, 0)
    denominator = sum(counts_by_status.values())
    return rate(
        status.lower(),
        numerator,
        denominator,
        f"historical replay records ending {status} / replay records with an "
        "outcome status",
        minimum_sample_size,
    )


def _versions_match(
    forward: ForwardReport, historical: ValidationReport
) -> tuple[bool | None, str]:
    """Compare only the upstream rule versions the two sides share.

    Parameter fingerprints are not comparable: each step fingerprints its own
    configuration contract. Rule versions are shared strings, so they are the
    only honest comparison point.
    """

    forward_versions = {}
    for cohort in forward.version_cohorts:
        forward_versions.update(dict(cohort.strategy_versions))
    historical_versions = dict(historical.strategy_versions)
    shared = {
        "setup_qualification_rules",
        "trade_planning_rules",
    }
    mismatched = sorted(
        key
        for key in shared
        if key in forward_versions
        and key in historical_versions
        and forward_versions[key] != historical_versions[key]
    )
    if mismatched:
        return (
            False,
            "rule versions differ between the two sides: " + ", ".join(mismatched),
        )
    missing = sorted(
        key
        for key in shared
        if key not in forward_versions or key not in historical_versions
    )
    if missing:
        return (
            None,
            "rule versions could not be compared for: " + ", ".join(missing),
        )
    return (
        True,
        "setup-qualification and trade-planning rule versions match; each side's "
        "parameter fingerprints are separate contracts and are not compared",
    )


def _count_value(rows: Sequence[ValueCount], value: str) -> int:
    for item in rows:
        if item.value == value:
            return item.count
    return 0


def _percentage_text(metric) -> str | None:
    if metric.denominator == 0:
        return None
    share = (Decimal(metric.numerator) / Decimal(metric.denominator)) * Decimal(100)
    return f"{share.quantize(Decimal('0.01'))}%"


def _comparison_rows(
    *,
    forward: ComparisonSide,
    historical: ComparisonSide,
    comparable: bool | None,
) -> tuple[ComparisonRow, ...]:
    rows: list[ComparisonRow] = []
    forward_metrics = forward.metrics
    historical_metrics = historical.metrics

    withheld_note = (
        None
        if forward.available
        else "forward metrics withheld: "
        + (forward.unavailable_reason or "the forward side is unavailable")
    )

    def add(
        metric: str,
        definition: str,
        historical_value,
        forward_value,
        *,
        note: str | None = None,
    ) -> None:
        if withheld_note is not None:
            note = withheld_note if note is None else f"{note}; {withheld_note}"
        rows.append(
            ComparisonRow(
                metric=metric,
                definition=definition,
                historical=historical_value,
                forward=forward_value,
                historical_denominator=historical_metrics.paper_plan_count,
                forward_denominator=forward_metrics.paper_plan_count,
                comparable=comparable,
                note=note,
            )
        )

    add(
        "sample_size",
        "distinct paper plans with a tracked outcome (the unit both sides are "
        "denominated in)",
        historical_metrics.paper_plan_count,
        forward.sample_size,
    )
    add(
        "setup_observations",
        "candidate-state records (historical replay records vs forward "
        "per-close candidate records)",
        historical_metrics.observation_records,
        forward_metrics.observation_records,
        note=(
            "the forward side records one row per live candidate per closed "
            "candle, so this count grows with observation time"
        ),
    )
    add(
        "distinct_setup_ids",
        "distinct setup instances observed",
        historical_metrics.distinct_setup_ids,
        forward_metrics.distinct_setup_ids,
    )
    add(
        "watch_count",
        "candidates in WATCH state",
        _count_value(historical_metrics.setup_state_counts, "WATCH"),
        _count_value(forward_metrics.setup_state_counts, "WATCH"),
        note=(
            "candidate-count rows use the observation denominator, not the "
            "paper-plan denominator"
        ),
    )
    add(
        "qualified_count",
        "candidates in QUALIFIED state",
        _count_value(historical_metrics.setup_state_counts, "QUALIFIED"),
        _count_value(forward_metrics.setup_state_counts, "QUALIFIED"),
    )
    add(
        "plannable_count",
        "candidates whose Step 6 result was PLANNABLE",
        _count_value(historical_metrics.plan_state_counts, "PLANNABLE"),
        _count_value(forward_metrics.plan_state_counts, "PLANNABLE"),
    )
    add(
        "paper_plan_count",
        "distinct paper plans tracked (forward freezes exactly one per setup "
        "instance)",
        historical_metrics.paper_plan_count,
        forward_metrics.paper_plan_count,
    )
    for label, metric_name in (
        ("entry_reached_rate", "entry_reached_rate"),
        ("entry_not_reached_rate", "entry_not_reached_rate"),
        ("stopped_rate", "stopped_rate"),
        ("ambiguous_rate", "ambiguous_rate"),
        ("incomplete_data_rate", "incomplete_rate"),
        ("unresolved_or_open_rate", "unresolved_rate"),
    ):
        historical_metric = getattr(historical_metrics, metric_name)
        forward_metric = getattr(forward_metrics, metric_name)
        add(
            label,
            f"historical: {historical_metric.denominator_definition} | forward: "
            f"{forward_metric.denominator_definition}",
            {
                "numerator": historical_metric.numerator,
                "denominator": historical_metric.denominator,
                "percentage": _percentage_text(historical_metric),
                "status": enum_text(historical_metric.status),
            },
            {
                "numerator": forward_metric.numerator,
                "denominator": forward_metric.denominator,
                "percentage": _percentage_text(forward_metric),
                "status": enum_text(forward_metric.status),
            },
            note=(
                None
                if historical_metric.denominator == forward_metric.denominator
                else "denominators differ: each side states its own"
            ),
        )
    target_count = max(
        len(historical_metrics.target_hit_rates),
        len(forward_metrics.target_hit_rates),
    )
    for index in range(target_count):
        historical_target = (
            historical_metrics.target_hit_rates[index]
            if index < len(historical_metrics.target_hit_rates)
            else None
        )
        forward_target = (
            forward_metrics.target_hit_rates[index]
            if index < len(forward_metrics.target_hit_rates)
            else None
        )
        add(
            f"target_{index + 1}_hit_rate",
            "ordered target reaches / clean ordered-entry observations proposing "
            "that target",
            None
            if historical_target is None
            else {
                "numerator": historical_target.numerator,
                "denominator": historical_target.denominator,
                "percentage": _percentage_text(historical_target),
                "status": enum_text(historical_target.status),
            },
            None
            if forward_target is None
            else {
                "numerator": forward_target.numerator,
                "denominator": forward_target.denominator,
                "percentage": _percentage_text(forward_target),
                "status": enum_text(forward_target.status),
            },
        )
    for label, metric_name in (
        ("raw_observational_R", "raw_observational_r"),
        ("friction_adjusted_hypothetical_R", "friction_adjusted_hypothetical_r"),
    ):
        historical_distribution: RDistribution = getattr(
            historical_metrics, metric_name
        )
        forward_distribution: RDistribution = getattr(forward_metrics, metric_name)
        add(
            label,
            forward_distribution.definition,
            {
                "sample_size": historical_distribution.sample_size,
                "average": historical_distribution.average,
                "median": historical_distribution.median,
                "status": enum_text(historical_distribution.status),
            },
            {
                "sample_size": forward_distribution.sample_size,
                "average": forward_distribution.average,
                "median": forward_distribution.median,
                "status": enum_text(forward_distribution.status),
            },
            note=(
                "unit-neutral market observation; never realised profit and never "
                "a P&L statement"
            ),
        )
    return tuple(rows)


__all__ = [
    "FORWARD_REPORT_LIMITATIONS",
    "VOLATILITY_WARMUP_OBSERVATIONS",
    "build_forward_comparison",
    "build_forward_metrics_payload",
    "build_forward_report",
]
