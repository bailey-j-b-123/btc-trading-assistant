"""Offline deterministic Step 12 reporting, version-separation and comparison tests.

Everything runs against temporary SQLite databases built by the real Alembic
migrations, synthetic labelled candles, an injected fixed clock and a fake
public exchange. Nothing contacts a network, an exchange, or project data.

The comparison tests use a **real** Step 11 ``ValidationReport`` produced by the
unchanged ``HistoricalValidationService`` over the same stored candles, so the
HISTORICAL VALIDATION side is genuine Step 11 output rather than a stand-in.
"""

from decimal import Decimal as D

import pytest
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    Harness,
    bar,
    labelled_series,
    make_harness,
)

from trading_assistant.forward_testing import (
    FORWARD_REPORT_LIMITATIONS,
    FrictionAssumptions,
    ForwardParameters,
    VersionSeparation,
    build_forward_comparison,
    build_forward_metrics_payload,
    build_forward_report,
)
from trading_assistant.historical_validation import (
    HistoricalValidationService,
    ValidationConfig,
)
from trading_assistant.trade_planning import PlanningParameters

MINIMUM = D("124")
STOP = D("117")
HORIZON = 20


def harness_with_a_paper_plan() -> Harness:
    """A ledger holding one qualifying close and its one active paper plan.

    The labelled series qualifies two setups at that close; the one-active-
    paper-trade policy paper-trades the first and records the second as a
    refused observation, so every rate denominator below is one plan.
    """

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 1
    return harness


def harness_with_a_resolved_target() -> Harness:
    """The same ledger plus one ordered entry and one proved target."""

    harness = harness_with_a_paper_plan()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    return harness


def historical_report(harness: Harness, *, friction: FrictionAssumptions | None = None):
    """A genuine Step 11 report over the same stored candles."""

    return HistoricalValidationService(harness.engine).validate(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        config=ValidationConfig(
            observation_horizon_candles=HORIZON,
            minimum_sample_size=1,
            friction=friction if friction is not None else FrictionAssumptions(),
        ),
    )


# ----------------------------------------------------------------------
# Forward report
# ----------------------------------------------------------------------


def test_forward_report_counts_match_the_ledger_and_state_denominators() -> None:
    harness = harness_with_a_resolved_target()
    snapshot = harness.ledger()
    report = harness.report()

    assert report.exchange == EXCHANGE
    assert report.symbol == SYMBOL
    assert report.timeframe == TIMEFRAME
    assert report.metrics.total_cycles == len(snapshot.cycles)
    assert report.metrics.distinct_boundaries == len(snapshot.cycles)
    assert report.metrics.paper_plan_count == len(harness.plans()) == 1
    assert report.metrics.distinct_setup_ids == len(
        {item.setup_id for item in harness.observations()}
    )
    assert report.metrics.observation_records == len(harness.observations())

    # Every plan carries its own outcome and a denominator is always present.
    entry_reached = report.metrics.entry_reached_rate
    assert entry_reached.denominator == report.metrics.paper_plan_count
    assert entry_reached.numerator <= entry_reached.denominator
    assert entry_reached.denominator_definition

    target_rates = report.metrics.target_hit_rates
    assert target_rates
    assert all(rate.denominator == report.metrics.paper_plan_count for rate in target_rates)


def test_forward_report_keeps_raw_and_friction_adjusted_r_separate() -> None:
    harness = harness_with_a_resolved_target()
    friction = FrictionAssumptions(
        fee_bps=D("10"), entry_slippage_bps=D("5"), exit_slippage_bps=D("5")
    )
    raw = harness.report(
        parameters=ForwardParameters(minimum_sample_size=1, friction=FrictionAssumptions())
    )
    adjusted = harness.report(
        parameters=ForwardParameters(minimum_sample_size=1, friction=friction),
        friction=friction,
    )

    assert raw.metrics.raw_observational_r.sample_size == 1
    assert adjusted.metrics.raw_observational_r.sample_size == 1
    # Costs can only ever worsen the hypothesis; they never invent profit.
    assert (
        adjusted.metrics.friction_adjusted_hypothetical_r.average
        < raw.metrics.friction_adjusted_hypothetical_r.average
    )
    assert (
        adjusted.metrics.friction_adjusted_hypothetical_r.average
        <= adjusted.metrics.raw_observational_r.average
    )
    assert adjusted.friction_fingerprint != raw.friction_fingerprint
    assert adjusted.friction == friction
    payload = adjusted.to_json_dict()
    assert payload["friction_fingerprint"] == friction.fingerprint()
    assert "raw_observational_r" in payload["metrics"]
    assert "friction_adjusted_hypothetical_r" in payload["metrics"]


def test_forward_report_labels_limitations_and_never_claims_real_performance() -> None:
    harness = harness_with_a_resolved_target()
    report = harness.report()
    payload = build_forward_metrics_payload(
        ledger=harness.ledger(), exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )

    assert "PAPER OBSERVATION — NO REAL ORDER" in " ".join(FORWARD_REPORT_LIMITATIONS)
    assert (
        "Paper trading and historical performance do not establish future "
        "profitability." in FORWARD_REPORT_LIMITATIONS
    )
    assert report.limitations == FORWARD_REPORT_LIMITATIONS
    assert payload["limitations"] == list(FORWARD_REPORT_LIMITATIONS)
    for limitation in FORWARD_REPORT_LIMITATIONS:
        lowered = limitation.lower()
        if "profit" in lowered or "p&l" in lowered:
            assert "never" in lowered or "not " in lowered
    # The report is a real record of paper observations, not a performance claim.
    assert any(
        "not real performance" in limitation.lower()
        for limitation in FORWARD_REPORT_LIMITATIONS
    )


def test_forward_report_withholds_percentages_below_the_sample_floor() -> None:
    harness = harness_with_a_resolved_target()
    strict = harness.report(parameters=ForwardParameters(minimum_sample_size=50))

    assert strict.metrics.paper_plan_count == 1  # counts are never hidden
    assert strict.metrics.entry_reached_rate.percentage is None
    assert strict.metrics.entry_reached_rate.status.value == "INSUFFICIENT_DATA"
    assert strict.metrics.entry_reached_rate.numerator >= 0
    assert strict.metrics.entry_reached_rate.denominator == 1
    assert any(
        warning.startswith("insufficient_paper_observation_sample")
        for warning in strict.warnings
    )


def test_forward_report_never_hides_ambiguous_incomplete_or_open_observations() -> None:
    harness = harness_with_a_paper_plan()
    # One candle whose range proves entry and the proposed target at once: the
    # ordering is unknowable, so the outcome must be AMBIGUOUS.
    harness.step((bar(21, 140, high=141, low=124),), refresh_market_data=False)
    harness.step((bar(22, 100, high=141, low=99),), refresh_market_data=False)

    report = harness.report(parameters=ForwardParameters(minimum_sample_size=1))
    statuses = {item.value: item.count for item in report.metrics.outcome_status_counts}
    assert statuses
    assert report.metrics.paper_plan_count == 1
    # Every plan keeps exactly one recorded outcome status; nothing is dropped.
    assert sum(statuses.values()) == 1
    assert report.metrics.paper_plans_with_outcome == 1
    assert report.metrics.ambiguous_count >= 1
    # An unordered same-candle touch is visible but is not a confirmed fill.
    assert report.metrics.entry_reached_rate.numerator == 0
    assert report.metrics.entry_reached_rate.denominator == 1
    assert report.metrics.raw_observational_r.sample_size == 0

    payload = report.to_json_dict()
    assert payload["metrics"]["outcome_status_counts"]
    assert payload["metrics"]["ambiguous_rate"]["denominator"] == 1
    assert payload["metrics"]["incomplete_rate"]["denominator"] == 1
    assert payload["metrics"]["unresolved_rate"]["denominator"] == 1

    # Breakdowns reuse the same contract and keep their own denominators.
    dimensions = {breakdown.dimension for breakdown in report.breakdowns}
    assert {
        "setup_family",
        "direction",
        "timeframe",
        "market_trend",
        "volatility_context",
        "data_health_at_plan",
    } <= dimensions
    for breakdown in report.breakdowns:
        for metric in (
            breakdown.metrics.entry_reached_rate,
            breakdown.metrics.ambiguous_rate,
            breakdown.metrics.incomplete_rate,
        ):
            assert metric.denominator == breakdown.metrics.paper_plan_count


# ----------------------------------------------------------------------
# Version separation
# ----------------------------------------------------------------------


def test_multiple_versions_are_reported_separately_and_never_combined() -> None:
    harness = harness_with_a_paper_plan()
    first = harness.cycles()[0].version_fingerprint
    # A stricter floor than the v2 default (1) is a genuinely different
    # planning version; the old fixture used 1, which is now the default.
    harness.service.planning_parameters = PlanningParameters(min_r_multiple=D("2"))
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)

    snapshot = harness.ledger()
    assert snapshot.version_separation is VersionSeparation.SEPARATED
    report = harness.report()

    assert report.version_separation is VersionSeparation.SEPARATED
    assert report.combined_metrics_available is False
    assert report.combined_metrics_unavailable_reason
    assert len(report.version_fingerprints) == 2
    assert len(report.version_cohorts) == 2
    assert first in report.version_fingerprints
    for cohort in report.version_cohorts:
        assert cohort.metrics.total_cycles >= 1
        assert dict(cohort.strategy_versions)["forward_ledger_rules"]
    # The combined contract is emptied rather than blended.
    assert report.metrics.total_cycles == 0
    assert any("withheld" in warning for warning in report.warnings)
    assert any("never combined" in warning or "withheld" in warning for warning in report.warnings)


def test_separated_versions_make_the_comparison_side_explicitly_unavailable() -> None:
    harness = harness_with_a_paper_plan()
    # A stricter floor than the v2 default (1) is a genuinely different
    # planning version; the old fixture used 1, which is now the default.
    harness.service.planning_parameters = PlanningParameters(min_r_multiple=D("2"))
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)

    comparison = build_forward_comparison(
        forward=harness.report(),
        historical=historical_report(harness),
        generated_at=QUALIFYING_BOUNDARY + INTERVAL,
    )
    assert comparison.forward.available is False
    assert comparison.forward.unavailable_reason
    assert comparison.forward.label == "LIVE FORWARD PAPER OBSERVATIONS"
    # The observed plan count is still stated even though the blended metrics are
    # withheld, and every row says so explicitly.
    assert comparison.forward.sample_size == len(harness.plans())
    assert all(row.note and "withheld" in row.note for row in comparison.rows)
    assert any(
        "version_separation" in warning or "withheld" in warning
        for warning in comparison.warnings
    )


# ----------------------------------------------------------------------
# Historical vs forward comparison
# ----------------------------------------------------------------------


def test_comparison_labels_both_sides_and_keeps_both_denominators() -> None:
    harness = harness_with_a_resolved_target()
    report = harness.report(parameters=ForwardParameters(minimum_sample_size=1))
    historical = historical_report(harness)
    comparison = build_forward_comparison(
        forward=report,
        historical=historical,
        generated_at=QUALIFYING_BOUNDARY + 2 * INTERVAL,
    )

    assert comparison.historical.label == "HISTORICAL VALIDATION"
    assert comparison.forward.label == "LIVE FORWARD PAPER OBSERVATIONS"
    assert "NOT LIVE PERFORMANCE" in comparison.historical.disclaimer
    assert "NO REAL ORDER" in comparison.forward.disclaimer
    assert comparison.historical.available is True
    assert comparison.forward.available is True
    assert comparison.historical.sample_size == comparison.historical.metrics.paper_plan_count
    assert comparison.forward.sample_size == comparison.forward.metrics.paper_plan_count == 1
    assert comparison.rows
    for row in comparison.rows:
        assert row.metric
        assert row.definition
        assert row.historical_denominator is not None
        assert row.forward_denominator is not None
        assert row.comparable is comparison.strategy_versions_match
    assert any(
        "denominators_differ" in warning for warning in comparison.warnings
    )
    assert any(
        "not a statistical test" in limitation for limitation in comparison.limitations
    )
    assert (
        "Paper trading and historical performance do not establish future "
        "profitability." in comparison.limitations
    )
    payload = comparison.to_json_dict()
    assert payload["historical"]["label"] == "HISTORICAL VALIDATION"
    assert payload["forward"]["label"] == "LIVE FORWARD PAPER OBSERVATIONS"


def test_comparison_historical_side_is_the_real_step_11_report() -> None:
    harness = harness_with_a_paper_plan()
    historical = historical_report(harness)
    comparison = build_forward_comparison(
        forward=harness.report(parameters=ForwardParameters(minimum_sample_size=1)),
        historical=historical,
        generated_at=QUALIFYING_BOUNDARY,
    )

    # The historical side is a faithful projection of the Step 11 cohort it came
    # from: same setup-state counts, same PLANNABLE plan count, same outcome
    # buckets, same R distributions.
    cohort = historical.out_of_sample or historical.development
    side = comparison.historical.metrics
    plan_counts = {item.value: item.count for item in cohort.metrics.plan_state_counts}
    assert side.plan_state_counts == cohort.metrics.plan_state_counts
    assert side.setup_state_counts == cohort.metrics.setup_state_counts
    assert side.paper_plan_count == plan_counts.get("PLANNABLE", 0)
    assert side.completed_count == cohort.metrics.completed_count
    assert side.unresolved_count == cohort.metrics.unresolved_count
    assert side.ambiguous_count == cohort.metrics.ambiguous_count
    assert side.incomplete_data_count == cohort.metrics.incomplete_count
    assert (
        side.raw_observational_r.values == cohort.metrics.raw_observational_r.values
    )
    assert side.entry_reached_rate == cohort.metrics.entry_reached_rate
    # Status-derived rates are denominated by the recorded outcome statuses.
    status_total = sum(item.count for item in cohort.metrics.outcome_status_counts)
    assert side.ambiguous_rate.denominator == status_total or status_total == 0
    assert side.incomplete_rate.denominator == status_total or status_total == 0

    # The Step 11 sample is genuinely non-empty here, so the mapping is exercised
    # rather than silently passing on zeros.
    assert cohort.metrics.total_records > 0
    assert side.total_cycles == cohort.metrics.total_records


def test_comparison_without_a_historical_report_is_explicitly_unavailable() -> None:
    harness = harness_with_a_paper_plan()
    comparison = build_forward_comparison(
        forward=harness.report(),
        historical=None,
        generated_at=QUALIFYING_BOUNDARY,
    )
    assert comparison.historical.available is False
    assert comparison.historical.unavailable_reason
    assert comparison.historical.label == "HISTORICAL VALIDATION"
    assert comparison.strategy_versions_match is None
    assert comparison.forward.available is True
    assert comparison.forward.sample_size == 1
    assert comparison.rows
    for row in comparison.rows:
        assert row.comparable is None
        # An unavailable side reports an explicit zero denominator, never a guess.
        assert row.historical_denominator == 0
        value = row.historical
        if isinstance(value, dict):
            assert all(
                item is None or item == 0 or item in {"INSUFFICIENT_DATA", "UNAVAILABLE"}
                for item in value.values()
            )
        else:
            assert value is None or value == 0


def test_comparison_friction_assumptions_are_versioned_and_reported() -> None:
    harness = harness_with_a_resolved_target()
    friction = FrictionAssumptions(
        fee_bps=D("10"), entry_slippage_bps=D("5"), exit_slippage_bps=D("5")
    )
    comparison = build_forward_comparison(
        forward=harness.report(
            parameters=ForwardParameters(friction=friction), friction=friction
        ),
        historical=historical_report(harness, friction=friction),
        generated_at=QUALIFYING_BOUNDARY,
    )
    assert comparison.forward.metrics.friction_adjusted_hypothetical_r.sample_size >= 0
    payload = comparison.to_json_dict()
    assert payload["generated_at"]
    assert payload["version_comparability_note"]
    assert payload["rows"]
    # Neither side is ever presented as realised money.
    text = str(payload).lower()
    for banned in ("realised p&l", "realized p&l", "net profit", "account balance"):
        assert banned not in text


def test_report_parameters_and_generated_artifacts_are_deterministic() -> None:
    harness = harness_with_a_resolved_target()
    counts_before = ledger_counts(harness)
    parameters = ForwardParameters(minimum_sample_size=1)
    first = harness.report(parameters=parameters)
    second = harness.report(parameters=parameters)
    assert first.report_id == second.report_id
    assert first.to_json_dict() == second.to_json_dict()
    assert first.parameters_fingerprint == parameters.fingerprint()
    assert first.rules_version
    # The ledger was only read; nothing new was recorded by reporting.
    assert harness.counts() == counts_before


def ledger_counts(harness: Harness) -> dict:
    return harness.service.ledger.counts(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )


def test_empty_ledger_report_is_explicit_not_fabricated() -> None:
    harness = make_harness(series=labelled_series())
    report = build_forward_report(
        ledger=harness.ledger(), exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert report.metrics.total_cycles == 0
    assert report.metrics.paper_plan_count == 0
    assert report.first_cycle_as_of is None
    assert report.last_cycle_as_of is None
    assert report.metrics.entry_reached_rate.denominator in (0, 1)
    payload = report.to_json_dict()
    assert payload["metrics"]["paper_plan_count"] == 0
    assert payload["warnings"]
    with pytest.raises(Exception):
        # Empty ≠ profitable: the sample floor keeps every rate unstated.
        assert report.metrics.entry_reached_rate.percentage is not None
