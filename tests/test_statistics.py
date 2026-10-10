"""Offline deterministic tests for Step 8 over synthetic immutable Step 7 rows."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D, localcontext

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from trading_assistant.database.base import Base
from trading_assistant.database.engine import create_database_engine
from trading_assistant.journaling import (
    DecisionRecord,
    DecisionState,
    JournalRecord,
    OutcomeObservation,
    OutcomeParameters,
    OutcomeStatus,
    OutcomeVersion,
    ProposedPlanLevels,
    RecordKind,
    observe_outcome,
)
from trading_assistant.journaling.models import (
    JournalDecisionRow,
    JournalOutcomeEventRow,
    JournalOutcomeRow,
    JournalRecordRow,
)
from trading_assistant.journaling.parameters import DECISION_RULES_VERSION
from trading_assistant.journaling.repository import JournalRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.pattern_liquidity.events import Direction
from trading_assistant.setup_qualification.models import SetupFamily, SetupState
from trading_assistant.statistics import (
    DEFAULT_GROUP_BY,
    JournalDataset,
    JournalStatisticsService,
    MetricStatus,
    StatisticsAnalyzer,
    StatisticsConfig,
    StatisticsDatasetError,
    StatisticsFilters,
)
from trading_assistant.trade_planning.models import PlanState

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
REPORT_CUTOFF = EPOCH + timedelta(hours=30)


def at(index: int) -> datetime:
    return EPOCH + HOUR * index


def journal_record(
    record_id: str,
    *,
    kind: RecordKind = RecordKind.SETUP,
    setup_state: SetupState = SetupState.QUALIFIED,
    family: SetupFamily | None = SetupFamily.BREAKOUT_RETEST,
    direction: Direction | None = "bullish",
    symbol: str = "BTC/USDT",
    exchange: str = "test-exchange",
    timeframe: str = "1h",
    setup_index: int = 0,
    setup_config: str = "setup-config-a",
    setup_rules: str = "setup-qualification-v1",
    planning_config: str = "plan-config-a",
    planning_rules: str = "trade-planning-v1",
    plan_state: PlanState | None = PlanState.PLANNABLE,
    planning_index: int = 0,
) -> JournalRecord:
    has_setup = kind is RecordKind.SETUP
    has_plan = has_setup and plan_state is not None
    moment = at(setup_index)
    return JournalRecord(
        journal_id=record_id,
        record_kind=kind,
        journal_rules_version="journal-v1",
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        source_timeframes=(timeframe,),
        setup_id=f"setup-{record_id}" if has_setup else None,
        setup_family=family if has_setup else None,
        setup_direction=direction if has_setup else None,
        setup_state=setup_state,
        setup_created_at=moment if has_setup else None,
        setup_as_of=moment,
        setup_seed_event_id=f"seed-{record_id}" if has_setup else None,
        setup_reference_id=f"reference-{record_id}" if has_setup else None,
        setup_config_fingerprint=setup_config,
        setup_rules_version=setup_rules,
        setup_snapshot_id=f"snapshot-{record_id}",
        setup_snapshot_json="{}",
        plan_id=f"plan-{record_id}" if has_plan else None,
        plan_state=plan_state if has_plan else None,
        planning_as_of=at(planning_index) if has_plan else None,
        plan_json=(
            '{"state":"PLANNABLE","targets":[{},{},{},{},{}]}' if has_plan else None
        ),
        plan_config_fingerprint=planning_config if has_plan else None,
        planning_rules_version=planning_rules if has_plan else None,
    )


def decision_row(
    record: JournalRecord,
    state: DecisionState,
    *,
    sequence: int = 1,
    decided_at: datetime | None = None,
    decision_id: str | None = None,
    rules_version: str = DECISION_RULES_VERSION,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id or f"decision-{record.journal_id}-{sequence}",
        journal_id=record.journal_id,
        sequence=sequence,
        supersedes_decision_id=(
            None if sequence == 1 else f"decision-{record.journal_id}-{sequence - 1}"
        ),
        decision=state,
        decided_at=record.setup_as_of if decided_at is None else decided_at,
        reason=None,
        decision_rules_version=rules_version,
        record_kind=record.record_kind,
        setup_id=record.setup_id,
        plan_id=record.plan_id,
        setup_family=record.setup_family,
        direction=record.setup_direction,
        setup_state=record.setup_state,
        exchange=record.exchange,
        symbol=record.symbol,
        timeframe=record.timeframe,
        source_timeframes=record.source_timeframes,
        setup_as_of=record.setup_as_of,
        planning_as_of=record.planning_as_of,
        setup_config_fingerprint=record.setup_config_fingerprint,
        plan_config_fingerprint=record.plan_config_fingerprint,
        setup_rules_version=record.setup_rules_version,
        planning_rules_version=record.planning_rules_version,
        setup_snapshot_id=record.setup_snapshot_id,
    )


def candle(
    record: JournalRecord,
    index: int,
    *,
    low: int | str,
    high: int | str,
    direction: Direction,
) -> Candle:
    low_value = D(str(low))
    high_value = D(str(high))
    reference = D(100)
    open_close = min(max(reference, low_value), high_value)
    return Candle(
        exchange=record.exchange,
        symbol=record.symbol,
        timeframe=record.timeframe,
        timestamp=at(index),
        open=open_close,
        high=high_value,
        low=low_value,
        close=open_close,
        volume=D(1),
    )


def observed(
    record: JournalRecord,
    scenario: str,
    *,
    direction: Direction | None = None,
    target_count: int = 3,
    parameters: OutcomeParameters | None = None,
) -> OutcomeObservation:
    actual_direction = record.setup_direction if direction is None else direction
    assert actual_direction in ("bullish", "bearish")
    assert record.plan_id is not None
    if actual_direction == "bullish":
        entry, stop = D(100), D(90)
        targets = tuple(D(110 + 10 * index) for index in range(target_count))
    else:
        entry, stop = D(100), D(110)
        targets = tuple(D(90 - 10 * index) for index in range(target_count))
    levels = ProposedPlanLevels(
        plan_id=record.plan_id,
        exchange=record.exchange,
        symbol=record.symbol,
        timeframe=record.timeframe,
        direction=actual_direction,
        entry=entry,
        stop=stop,
        targets=targets,
        risk_per_unit=D(10),
        as_of=at(0),
        setup_id=record.setup_id,
    )

    entry_bar = candle(record, 0, low=99, high=101, direction=actual_direction)
    candles: list[Candle]
    cutoff_index: int
    if scenario == "entry_not_reached":
        candles, cutoff_index = (
            [candle(record, 0, low=101, high=103, direction=actual_direction)],
            0,
        )
    elif scenario == "invalidated_before_entry":
        candles, cutoff_index = (
            [
                candle(
                    record,
                    0,
                    low=89 if actual_direction == "bullish" else 105,
                    high=95 if actual_direction == "bullish" else 111,
                    direction=actual_direction,
                )
            ],
            0,
        )
    elif scenario == "stopped":
        stop_bar = candle(
            record,
            1,
            low=89 if actual_direction == "bullish" else 105,
            high=95 if actual_direction == "bullish" else 111,
            direction=actual_direction,
        )
        candles, cutoff_index = [entry_bar, stop_bar], 1
    elif scenario == "stopped_after_targets":
        target_bar = candle(
            record,
            1,
            low=99 if actual_direction == "bullish" else 89,
            high=111 if actual_direction == "bullish" else 101,
            direction=actual_direction,
        )
        stop_bar = candle(
            record,
            2,
            low=89 if actual_direction == "bullish" else 105,
            high=95 if actual_direction == "bullish" else 111,
            direction=actual_direction,
        )
        candles, cutoff_index = [entry_bar, target_bar, stop_bar], 2
    elif scenario == "targets_reached":
        last_target = targets[-1]
        low, high = (
            (99, last_target + 1)
            if actual_direction == "bullish"
            else (last_target - 1, 101)
        )
        candles, cutoff_index = (
            [
                entry_bar,
                candle(record, 1, low=low, high=high, direction=actual_direction),
            ],
            1,
        )
    elif scenario == "through_t1":
        low, high = (99, 111) if actual_direction == "bullish" else (89, 101)
        candles, cutoff_index = (
            [
                entry_bar,
                candle(record, 1, low=low, high=high, direction=actual_direction),
            ],
            1,
        )
    elif scenario == "through_t2":
        low, high = (99, 121) if actual_direction == "bullish" else (79, 101)
        candles, cutoff_index = (
            [
                entry_bar,
                candle(record, 1, low=low, high=high, direction=actual_direction),
            ],
            1,
        )
    elif scenario == "open":
        candles, cutoff_index = [entry_bar], 0
    elif scenario == "ambiguous":
        low, high = (99, 111) if actual_direction == "bullish" else (89, 101)
        candles, cutoff_index = (
            [candle(record, 0, low=low, high=high, direction=actual_direction)],
            0,
        )
    elif scenario == "incomplete":
        candles, cutoff_index = (
            [candle(record, 0, low=101, high=103, direction=actual_direction)],
            2,
        )
    else:
        raise AssertionError(f"unknown scenario {scenario}")

    return observe_outcome(
        journal_id=record.journal_id,
        levels=levels,
        candles=tuple(candles),
        observed_through=at(cutoff_index),
        parameters=parameters,
    )


def outcome_version(
    observation: OutcomeObservation,
    *,
    sequence: int = 1,
    supersedes: str | None = None,
) -> OutcomeVersion:
    return OutcomeVersion(sequence, supersedes, observation)


def dataset(
    records: tuple[JournalRecord, ...],
    *,
    decisions: tuple[DecisionRecord, ...] = (),
    outcomes: tuple[OutcomeVersion, ...] = (),
) -> JournalDataset:
    return JournalDataset(records, decisions, outcomes)


def values_for(group, dimension: str) -> dict[str | None, int]:
    counts = next(item for item in group if item.dimension == dimension)
    return {item.value: item.count for item in counts.values}


def count_for(values, label: str) -> int:
    return next(item.count for item in values if item.value == label)


def test_empty_dataset_and_below_minimum_are_explicit_and_reproducible():
    config = StatisticsConfig(minimum_sample_size=2)
    analyzer = StatisticsAnalyzer(config)
    report = analyzer.analyze(dataset(()), as_of=REPORT_CUTOFF)

    assert report.data_quality.total_journal_records_considered == 0
    assert report.groups == ()
    assert (
        report.overall.setup_qualification_rate.status is MetricStatus.INSUFFICIENT_DATA
    )
    assert report.overall.setup_qualification_rate.percentage is None
    assert report.overall.hypothetical_proposed_plan_outcome_r.sample_size == 0
    assert report.overall.hypothetical_proposed_plan_outcome_r.average is None
    assert (
        report.report_id == analyzer.analyze(dataset(()), as_of=REPORT_CUTOFF).report_id
    )

    one = journal_record("only-one")
    only_outcome = observed(one, "targets_reached", target_count=1)
    small = analyzer.analyze(
        dataset((one,), outcomes=(outcome_version(only_outcome),)),
        as_of=REPORT_CUTOFF,
    )
    assert small.data_quality.total_journal_records_considered == 1
    assert small.overall.entry_reach_rate.numerator == 1
    assert small.overall.entry_reach_rate.denominator == 1
    assert small.overall.entry_reach_rate.percentage is None
    assert small.overall.hypothetical_proposed_plan_outcome_r.sample_size == 1
    assert small.overall.hypothetical_proposed_plan_outcome_r.positive_count == 1
    assert small.overall.hypothetical_proposed_plan_outcome_r.average is None
    assert len(small.overall.hypothetical_proposed_plan_outcome_r.distribution) == 1


def test_exact_minimum_sample_produces_rates_and_descriptive_summaries():
    records = (journal_record("min-1"), journal_record("min-2", setup_index=1))
    observations = (
        observed(records[0], "stopped"),
        observed(records[1], "targets_reached", target_count=1),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=2)).analyze(
        dataset(
            records,
            outcomes=tuple(outcome_version(item) for item in observations),
        ),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )
    result = report.overall
    assert result.setup_qualification_rate.status is MetricStatus.SUFFICIENT_DATA
    assert result.setup_qualification_rate.percentage == D("100.00000000")
    assert result.entry_reach_rate.status is MetricStatus.SUFFICIENT_DATA
    assert result.entry_reach_rate.percentage == D("100.00000000")
    assert (
        result.hypothetical_proposed_plan_outcome_r.status
        is MetricStatus.SUFFICIENT_DATA
    )
    assert result.hypothetical_proposed_plan_outcome_r.average == D("0.00000000")
    assert result.hypothetical_proposed_plan_outcome_r.negative_count == 1
    assert result.hypothetical_proposed_plan_outcome_r.positive_count == 1


def test_setup_decision_plan_and_no_decision_counts_are_separate():
    records = tuple(journal_record(f"decision-{index}") for index in range(5))
    states = (
        DecisionState.PENDING,
        DecisionState.ACCEPTED,
        DecisionState.REJECTED,
        DecisionState.SKIPPED,
    )
    decisions = tuple(
        decision_row(records[index], state) for index, state in enumerate(states)
    )
    scenarios = ("open", "stopped", "targets_reached", "stopped_after_targets", "open")
    outcomes = tuple(
        outcome_version(observed(record, scenario))
        for record, scenario in zip(records, scenarios, strict=True)
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset(records, decisions=decisions, outcomes=outcomes),
        as_of=REPORT_CUTOFF,
    )

    assert {row.value: row.count for row in report.overall.decision_state_counts} == {
        "NO_DECISION": 1,
        "PENDING": 1,
        "ACCEPTED": 1,
        "REJECTED": 1,
        "SKIPPED": 1,
    }
    assert report.data_quality.plannable_plan_records == 5
    assert report.data_quality.outcome_records_eligible == 5
    assert {group.key[2][1] for group in report.groups} == {
        "NO_DECISION",
        "PENDING",
        "ACCEPTED",
        "REJECTED",
        "SKIPPED",
    }
    assert DEFAULT_GROUP_BY == (
        "setup_family",
        "direction",
        "decision_state",
        "symbol",
        "exchange",
        "timeframe",
    )


def test_all_step7_outcome_states_and_unknown_exclusions_are_preserved():
    scenarios = (
        "entry_not_reached",
        "invalidated_before_entry",
        "stopped",
        "stopped_after_targets",
        "targets_reached",
        "open",
        "ambiguous",
        "incomplete",
    )
    records = tuple(journal_record(f"status-{name}") for name in scenarios)
    versions = tuple(
        outcome_version(observed(record, name, target_count=2))
        for record, name in zip(records, scenarios, strict=True)
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset(records, outcomes=versions), as_of=REPORT_CUTOFF, group_by=()
    )
    status_counts = {
        item.value: item.count for item in report.overall.outcome_status_counts
    }

    assert all(status_counts[status.value] == 1 for status in OutcomeStatus)
    assert report.data_quality.ambiguous_count == 1
    assert report.data_quality.incomplete_unknown_count == 1
    assert report.data_quality.entry_not_reached_count == 2
    assert report.data_quality.outcome_records_determinate == 6
    assert report.overall.hypothetical_proposed_plan_outcome_r.sample_size == 2
    assert (
        count_for(
            report.overall.hypothetical_proposed_plan_outcome_r.exclusion_reasons,
            "AMBIGUOUS_OUTCOME",
        )
        == 1
    )
    assert (
        count_for(
            report.overall.hypothetical_proposed_plan_outcome_r.exclusion_reasons,
            "INCOMPLETE_OUTCOME",
        )
        == 1
    )
    assert report.overall.outcome_status_rates[0].denominator == 8
    assert report.overall.outcome_status_rates[0].status is MetricStatus.SUFFICIENT_DATA


def test_entry_not_reached_is_not_conflated_with_unknown_data():
    records = (
        journal_record("entry-none"),
        journal_record("entry-invalidated"),
        journal_record("entry-gap"),
    )
    scenarios = ("entry_not_reached", "invalidated_before_entry", "incomplete")
    outcomes = tuple(
        outcome_version(observed(record, scenario))
        for record, scenario in zip(records, scenarios, strict=True)
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset(records, outcomes=outcomes), as_of=REPORT_CUTOFF, group_by=()
    )
    metric = report.overall.entry_reach_rate

    assert report.data_quality.entry_not_reached_count == 2
    assert metric.numerator == 0
    assert metric.denominator == 2
    assert metric.percentage == D("0E-8")
    assert count_for(metric.exclusion_reasons, "INCOMPLETE_ENTRY_STATE") == 1


def test_target_ordinals_are_independent_and_support_arbitrary_target_counts():
    records = (
        journal_record("targets-t1"),
        journal_record("targets-t2"),
        journal_record("targets-five"),
    )
    observations = (
        observed(records[0], "through_t1", target_count=3),
        observed(records[1], "through_t2", target_count=3),
        observed(records[2], "targets_reached", target_count=5),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset(
            records,
            outcomes=tuple(outcome_version(item) for item in observations),
        ),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )
    targets = report.overall.target_summaries

    assert [target.label for target in targets] == ["T1", "T2", "T3", "T4", "T5"]
    assert [
        (item.reach_rate.numerator, item.reach_rate.denominator) for item in targets
    ] == [
        (3, 3),
        (2, 3),
        (1, 3),
        (1, 1),
        (1, 1),
    ]
    assert targets[0].records_with_target_proposed == 3
    assert targets[4].records_with_target_proposed == 1


def test_target_proposals_without_outcomes_are_counted_and_explicitly_excluded():
    record = journal_record("unobserved-proposal")
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset((record,)), as_of=REPORT_CUTOFF, group_by=()
    )

    assert len(report.overall.target_summaries) == 5
    for target in report.overall.target_summaries:
        assert target.records_with_target_proposed == 1
        assert target.reach_rate.denominator == 0
        assert target.reach_rate.status is MetricStatus.INSUFFICIENT_DATA
        assert (
            count_for(target.reach_rate.exclusion_reasons, "NO_OUTCOME_BY_CUTOFF") == 1
        )
    assert report.data_quality.outcome_records_without_observation_by_cutoff == 1


def test_long_and_short_observations_use_directional_proposed_r():
    long = journal_record("long-r", direction="bullish")
    short = journal_record("short-r", direction="bearish")
    outcomes = (
        outcome_version(observed(long, "targets_reached", target_count=2)),
        outcome_version(observed(short, "targets_reached", target_count=2)),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=2)).analyze(
        dataset((long, short), outcomes=outcomes), as_of=REPORT_CUTOFF, group_by=()
    )

    r_summary = report.overall.hypothetical_proposed_plan_outcome_r
    # Both directional targets are two proposed risk units from entry.
    assert [(item.value, item.count) for item in r_summary.distribution] == [
        (D("2.00000000"), 2)
    ]
    assert r_summary.sample_size == 2
    assert r_summary.average == D("2.00000000")
    assert r_summary.minimum == D("2.00000000")
    assert r_summary.maximum == D("2.00000000")


def test_mfe_mae_are_read_from_step7_for_long_and_short():
    long = journal_record("long-mfe", direction="bullish")
    short = journal_record("short-mfe", direction="bearish")
    observations = (
        observed(long, "targets_reached", target_count=2),
        observed(short, "targets_reached", target_count=2),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=2)).analyze(
        dataset(
            (long, short),
            outcomes=tuple(outcome_version(item) for item in observations),
        ),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )

    assert report.overall.observational_mfe_price_move.average == D("21.00000000")
    assert report.overall.observational_mae_price_move.average == D("1.00000000")
    assert report.overall.observational_mfe_r.average == D("2.10000000")
    assert report.overall.observational_mae_r.average == D("0.10000000")
    assert report.overall.observational_mfe_price_move.definition.startswith(
        "Uses Step 7 OutcomeObservation"
    )


def test_mean_median_min_max_sign_counts_and_nearest_rank_quantiles():
    records = tuple(journal_record(f"r-dist-{index}") for index in range(4))
    observations = (
        observed(records[0], "stopped"),
        observed(records[1], "targets_reached", target_count=1),
        observed(records[2], "targets_reached", target_count=2),
        observed(records[3], "targets_reached", target_count=2),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=4)).analyze(
        dataset(
            records,
            outcomes=tuple(outcome_version(item) for item in observations),
        ),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )
    metric = report.overall.hypothetical_proposed_plan_outcome_r

    assert [(point.value, point.count) for point in metric.distribution] == [
        (D("-1.00000000"), 1),
        (D("1.00000000"), 1),
        (D("2.00000000"), 2),
    ]
    assert metric.average == D("1.00000000")
    assert metric.median == D("1.50000000")
    assert metric.minimum == D("-1.00000000")
    assert metric.maximum == D("2.00000000")
    assert (metric.positive_count, metric.negative_count, metric.zero_count) == (
        3,
        1,
        0,
    )
    assert [(row.probability, row.value) for row in metric.quantiles] == [
        (D("0.25"), D("-1.00000000")),
        (D("0.5"), D("1.00000000")),
        (D("0.75"), D("2.00000000")),
    ]


def test_r_calculation_is_independent_of_ambient_decimal_context():
    record = journal_record("decimal-context")
    base = observed(record, "stopped")
    precise_observation = replace(
        base,
        id="decimal-context-outcome",
        entry_level=D("100.12345678901234567890"),
        stop_level=D("100.12345678898765432109"),
        risk_per_unit=D("0.00000000003"),
    )
    data = dataset((record,), outcomes=(outcome_version(precise_observation),))
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))

    normal_context = analyzer.analyze(data, as_of=REPORT_CUTOFF, group_by=())
    with localcontext() as context:
        context.prec = 3
        reduced_context = analyzer.analyze(data, as_of=REPORT_CUTOFF, group_by=())

    assert normal_context.to_json() == reduced_context.to_json()
    assert normal_context.overall.hypothetical_proposed_plan_outcome_r.distribution[
        0
    ].value == D("-0.82304526")


def test_stopped_after_targets_has_no_invented_single_outcome_r():
    record = journal_record("no-scale-out-policy")
    observation = observed(record, "stopped_after_targets", target_count=3)
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset((record,), outcomes=(outcome_version(observation),)),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )
    metric = report.overall.hypothetical_proposed_plan_outcome_r

    assert observation.status is OutcomeStatus.STOPPED_AFTER_TARGETS
    assert metric.sample_size == 0
    assert (
        count_for(metric.exclusion_reasons, "STOPPED_AFTER_TARGETS_NO_EXIT_POLICY") == 1
    )
    assert "HYPOTHETICAL_PROPOSED_LEVEL_R" in metric.definition
    assert "realized P&L" in metric.definition


def test_setup_state_and_plan_state_counts_include_snapshots_without_double_counting():
    qualified = journal_record("setup-qualified", setup_state=SetupState.QUALIFIED)
    watch = journal_record(
        "setup-watch", setup_state=SetupState.WATCH, plan_state=PlanState.NO_PLAN
    )
    terminated = journal_record(
        "setup-no-setup",
        setup_state=SetupState.NO_SETUP,
        family=SetupFamily.RANGE_REVERSAL,
        plan_state=None,
    )
    snapshot = journal_record(
        "aggregate-snapshot",
        kind=RecordKind.SNAPSHOT,
        setup_state=SetupState.WATCH,
        family=None,
        direction=None,
        plan_state=None,
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset((qualified, watch, terminated, snapshot)),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )

    assert report.data_quality.total_journal_records_considered == 4
    assert report.data_quality.setup_records_eligible == 3
    assert report.data_quality.distinct_setup_ids == 3
    assert report.overall.setup_qualification_rate.numerator == 1
    assert report.overall.setup_qualification_rate.denominator == 3
    assert count_for(report.overall.setup_state_counts, "QUALIFIED") == 1
    assert count_for(report.overall.setup_state_counts, "WATCH") == 2
    assert count_for(report.overall.setup_state_counts, "NO_SETUP") == 1
    assert count_for(report.overall.plan_state_counts, "PLANNABLE") == 1
    assert count_for(report.overall.plan_state_counts, "NO_PLAN") == 1
    assert count_for(report.overall.plan_state_counts, "NO_PLAN_ATTACHED") == 2
    assert (
        count_for(
            report.overall.setup_qualification_rate.exclusion_reasons,
            "SNAPSHOT_RECORD_NOT_SETUP",
        )
        == 1
    )


def test_setup_and_market_dimensions_group_without_btc_hardcoding():
    records = (
        journal_record(
            "group-1",
            family=SetupFamily.BREAKOUT_RETEST,
            direction="bullish",
            symbol="DOGE/XYZ",
            exchange="venue-a",
            timeframe="15m",
        ),
        journal_record(
            "group-2",
            family=SetupFamily.RANGE_REVERSAL,
            direction="bearish",
            symbol="ETH/EUR",
            exchange="venue-b",
            timeframe="4h",
        ),
    )
    report = StatisticsAnalyzer().analyze(
        dataset(records),
        as_of=REPORT_CUTOFF,
        group_by=("setup_family", "direction", "symbol", "exchange", "timeframe"),
        allow_mixed_versions=True,
    )
    assert len(report.groups) == 2
    dimensions = {
        item.dimension: values_for(report.dimension_counts, item.dimension)
        for item in report.dimension_counts
    }
    assert dimensions["symbol"] == {"DOGE/XYZ": 1, "ETH/EUR": 1}
    assert dimensions["exchange"] == {"venue-a": 1, "venue-b": 1}
    assert dimensions["timeframe"] == {"15m": 1, "4h": 1}
    assert {group.key[2][1] for group in report.groups} == {"DOGE/XYZ", "ETH/EUR"}


def test_setup_decision_and_planning_fingerprints_are_separated_by_default():
    first = journal_record("version-a", setup_config="qual-a", planning_config="plan-a")
    second = journal_record(
        "version-b", setup_config="qual-b", planning_config="plan-b"
    )
    data = dataset((first, second))
    analyzer = StatisticsAnalyzer()

    separated = analyzer.analyze(data, as_of=REPORT_CUTOFF, group_by=("setup_family",))
    mixed = analyzer.analyze(
        data,
        as_of=REPORT_CUTOFF,
        group_by=("setup_family",),
        allow_mixed_versions=True,
    )

    assert separated.mixed_versions
    assert separated.overall is None
    assert "setup_config_fingerprint" in separated.effective_group_by
    assert "plan_config_fingerprint" in separated.effective_group_by
    assert len(separated.groups) == 2
    assert mixed.version_policy == "MIXED_VERSIONS_EXPLICITLY_ALLOWED"
    assert mixed.mixed_versions
    assert len(mixed.groups) == 1
    assert len(mixed.version_profiles) == 2
    assert sum(profile.record_count for profile in mixed.version_profiles) == 2


def test_step7_observation_rule_versions_are_separated_as_well():
    first = journal_record("observation-version-a")
    second = journal_record("observation-version-b")
    observations = (
        outcome_version(observed(first, "open")),
        outcome_version(
            observed(
                second,
                "open",
                parameters=OutcomeParameters(rules_version="journal-outcome-v3"),
            )
        ),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset((first, second), outcomes=observations),
        as_of=REPORT_CUTOFF,
        group_by=(),
    )

    assert report.mixed_versions
    assert report.overall is None
    assert "observation_rules_version" in report.effective_group_by
    assert len(report.groups) == 2
    assert {
        dict(group.key)["observation_rules_version"] for group in report.groups
    } == {"journal-outcome-v1", "journal-outcome-v3"}


def test_configuration_filters_and_no_decision_filter_are_exact():
    accepted = journal_record("filter-accepted", symbol="ALT/QUOTE")
    rejected = journal_record(
        "filter-rejected", symbol="ALT/QUOTE", setup_config="other"
    )
    decisions = (
        decision_row(accepted, DecisionState.ACCEPTED),
        decision_row(rejected, DecisionState.REJECTED),
    )
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))
    filtered = analyzer.analyze(
        dataset((accepted, rejected), decisions=decisions),
        as_of=REPORT_CUTOFF,
        filters={"symbol": "ALT/QUOTE", "decision_state": "REJECTED"},
        group_by=(),
    )
    no_decision = analyzer.analyze(
        dataset((accepted, rejected), decisions=decisions),
        as_of=REPORT_CUTOFF,
        filters=StatisticsFilters.from_mapping(decision_state="NO_DECISION"),
        group_by=(),
    )

    assert filtered.input_journal_ids == ("filter-rejected",)
    assert filtered.data_quality.total_journal_records_considered == 1
    assert no_decision.data_quality.total_journal_records_considered == 0
    assert filtered.filters == (
        ("decision_state", ("REJECTED",)),
        ("symbol", ("ALT/QUOTE",)),
    )


def test_stable_report_ids_and_json_ignore_input_order_but_change_with_filters():
    one = journal_record("stable-1")
    two = journal_record("stable-2", setup_index=1)
    decisions = (
        decision_row(one, DecisionState.ACCEPTED),
        decision_row(two, DecisionState.SKIPPED),
    )
    data_a = dataset((one, two), decisions=decisions)
    data_b = dataset((two, one), decisions=tuple(reversed(decisions)))
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))
    report_a = analyzer.analyze(data_a, as_of=REPORT_CUTOFF)
    report_b = analyzer.analyze(data_b, as_of=REPORT_CUTOFF)
    changed = analyzer.analyze(
        data_a,
        as_of=REPORT_CUTOFF,
        filters={"symbol": "BTC/USDT"},
    )

    assert report_a.report_id == report_b.report_id
    assert report_a.to_json() == report_b.to_json()
    assert report_a.report_id != changed.report_id
    assert report_a.source_dataset_fingerprint == report_b.source_dataset_fingerprint


def test_historical_cutoff_excludes_future_records_decisions_and_outcome_versions():
    record = journal_record("historic", setup_index=0)
    later_record = journal_record("future-record", setup_index=5)
    early_observation = observed(record, "open")
    later_observation = observed(record, "targets_reached", target_count=3)
    early_decision = decision_row(
        record, DecisionState.ACCEPTED, sequence=1, decided_at=at(0)
    )
    later_decision = decision_row(
        record,
        DecisionState.REJECTED,
        sequence=2,
        decided_at=at(5),
        decision_id="decision-historic-2",
    )
    earlier_dataset = dataset(
        (record,),
        decisions=(early_decision,),
        outcomes=(outcome_version(early_observation),),
    )
    future_complete_dataset = dataset(
        (record, later_record),
        decisions=(early_decision, later_decision),
        outcomes=(
            outcome_version(early_observation),
            outcome_version(
                later_observation,
                sequence=2,
                supersedes=early_observation.id,
            ),
        ),
    )
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))

    historical = analyzer.analyze(earlier_dataset, as_of=at(0))
    with_future_data = analyzer.analyze(future_complete_dataset, as_of=at(0))

    assert historical.report_id == with_future_data.report_id
    assert historical.to_json() == with_future_data.to_json()
    assert with_future_data.input_journal_ids == ("historic",)
    assert with_future_data.input_decision_ids == (early_decision.decision_id,)
    assert with_future_data.input_outcome_ids == (early_observation.id,)
    assert (
        next(
            item.count
            for item in with_future_data.overall.decision_state_counts
            if item.value == "ACCEPTED"
        )
        == 1
    )
    assert (
        next(
            item.count
            for item in with_future_data.overall.outcome_status_counts
            if item.value == OutcomeStatus.OPEN_AT_CUTOFF.value
        )
        == 1
    )


def test_rejected_and_skipped_observations_are_hypothetical_not_executed():
    rejected = journal_record("not-taken-rejected")
    skipped = journal_record("not-taken-skipped")
    decisions = (
        decision_row(rejected, DecisionState.REJECTED),
        decision_row(skipped, DecisionState.SKIPPED),
    )
    outcomes = (
        outcome_version(observed(rejected, "targets_reached", target_count=2)),
        outcome_version(observed(skipped, "stopped")),
    )
    report = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1)).analyze(
        dataset((rejected, skipped), decisions=decisions, outcomes=outcomes),
        as_of=REPORT_CUTOFF,
    )

    assert report.overall.hypothetical_proposed_plan_outcome_r.sample_size == 2
    assert "not fills" in report.overall.hypothetical_proposed_plan_outcome_r.definition
    assert all(group.total_journal_records_considered == 1 for group in report.groups)
    assert not hasattr(report.overall, "executed_trade_count")


def test_chronological_periods_and_rolling_windows_are_cutoff_bounded():
    records = (
        journal_record("period-old", setup_index=0),
        journal_record("period-recent", setup_index=30),
        journal_record("period-future", setup_index=50),
    )
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))
    period_report = analyzer.analyze(
        dataset(records),
        as_of=at(40),
        group_by=("period_day", "period_week", "period_month"),
        allow_mixed_versions=True,
    )
    rolling = analyzer.rolling_reports(
        dataset(records),
        cutoffs=(at(30), at(40)),
        lookback=timedelta(hours=15),
        group_by=(),
        allow_mixed_versions=True,
    )

    assert period_report.data_quality.total_journal_records_considered == 2
    assert len(period_report.groups) == 2
    assert rolling[0].window_start == at(15)
    assert rolling[0].as_of == at(30)
    assert rolling[0].input_journal_ids == ("period-recent",)
    assert rolling[1].input_journal_ids == ("period-recent",)
    assert rolling[0].report_id != rolling[1].report_id


def test_journal_rows_are_unchanged_by_repeated_statistics_calls():
    record = journal_record("immutable-source")
    decision = decision_row(record, DecisionState.ACCEPTED)
    observation = observed(record, "targets_reached")
    version = outcome_version(observation)
    original = (
        record.to_json_dict(),
        decision.to_json_dict(),
        observation.to_json_dict(),
        version.to_json_dict(),
    )
    data = dataset((record,), decisions=(decision,), outcomes=(version,))
    analyzer = StatisticsAnalyzer(StatisticsConfig(minimum_sample_size=1))

    first = analyzer.analyze(data, as_of=REPORT_CUTOFF)
    second = analyzer.analyze(data, as_of=REPORT_CUTOFF)

    assert first.to_json() == second.to_json()
    assert original == (
        record.to_json_dict(),
        decision.to_json_dict(),
        observation.to_json_dict(),
        version.to_json_dict(),
    )


def test_dataset_rejects_dangling_outcome_and_config_rejects_unstable_inputs():
    record = journal_record("dataset-validation")
    observation = observed(record, "open")
    with pytest.raises(StatisticsDatasetError, match="missing journal record"):
        JournalDataset((), outcomes=(outcome_version(observation),))
    with pytest.raises(ValueError, match="minimum_sample_size"):
        StatisticsConfig(minimum_sample_size=0)
    with pytest.raises(TypeError, match="immutable tuple"):
        StatisticsConfig(quantiles=[D("0.25")])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="decimal string"):
        StatisticsConfig(quantiles=(0.25,))  # type: ignore[arg-type]


def test_read_only_database_service_uses_selects_and_does_not_change_rows():
    engine = create_database_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            JournalRecordRow.__table__,
            JournalDecisionRow.__table__,
            JournalOutcomeRow.__table__,
            JournalOutcomeEventRow.__table__,
        ],
    )
    record = journal_record("read-only-db")
    observation = observed(record, "targets_reached")
    repository = JournalRepository(engine)
    repository.insert_record(record)
    repository.append_decision(
        journal_id=record.journal_id,
        decision_id="db-decision-1",
        decision=DecisionState.ACCEPTED,
        decided_at=record.setup_as_of,
        reason=None,
        decision_rules_version=DECISION_RULES_VERSION,
    )
    repository.append_outcome(observation)
    before = {}
    for model in (
        JournalRecordRow,
        JournalDecisionRow,
        JournalOutcomeRow,
        JournalOutcomeEventRow,
    ):
        with engine.connect() as connection:
            before[model.__tablename__] = connection.scalar(
                sa.select(sa.func.count()).select_from(model)
            )

    statements: list[str] = []

    def capture_sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        report = JournalStatisticsService(
            engine, config=StatisticsConfig(minimum_sample_size=1)
        ).analyze(as_of=REPORT_CUTOFF, group_by=())
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    after = {}
    for model in (
        JournalRecordRow,
        JournalDecisionRow,
        JournalOutcomeRow,
        JournalOutcomeEventRow,
    ):
        with engine.connect() as connection:
            after[model.__tablename__] = connection.scalar(
                sa.select(sa.func.count()).select_from(model)
            )
    engine.dispose()

    assert report.input_journal_ids == (record.journal_id,)
    assert statements and all(statement == "SELECT" for statement in statements)
    assert after == before


def test_read_only_analysis_does_not_create_a_missing_database_file(tmp_path):
    database_path = tmp_path / "not-created.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")

    with pytest.raises(FileNotFoundError, match="will not create"):
        JournalStatisticsService(engine).read_dataset()

    assert not database_path.exists()
    engine.dispose()


def test_statistics_filters_validate_dimensions_and_cutoff_requires_aware_time():
    analyzer = StatisticsAnalyzer()
    with pytest.raises(ValueError, match="unsupported statistics filter dimension"):
        StatisticsFilters.from_mapping(not_a_dimension="x")
    with pytest.raises(ValueError, match="timezone-aware"):
        analyzer.analyze(dataset(()), as_of=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="window_start must be <= as_of"):
        analyzer.analyze(dataset(()), as_of=at(0), window_start=at(1))
