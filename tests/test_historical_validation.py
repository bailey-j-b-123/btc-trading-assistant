"""Offline deterministic tests for Step 11 historical replay/validation.

These tests deliberately exercise the existing Steps 3–7 pipeline rather than
changing any candidate, qualification, planning, or observation rule. Synthetic
candles are labelled test data and all databases are temporary SQLite files.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace

import pytest
from forward_fixtures import (
    DEFAULT_COST_SCENARIO,
    QUALIFYING_BOUNDARY,
    ForwardParameters,
    labelled_series,
    make_harness,
)
from market_structure_fixtures import EPOCH, EXCHANGE, SYMBOL
from sqlalchemy import func, select
from web_fixtures import (
    INTERVAL,
    bar,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualifying_candles,
)

from trading_assistant.historical_validation import (
    REPLAY_POLICY_ECONOMIC_GATE,
    REPLAY_POLICY_UNVERIFIED_V2,
    ChronologicalSplit,
    FrictionAssumptions,
    HistoricalValidationService,
    ValidationConfig,
)
from trading_assistant.historical_validation.models import (
    RegimeLabel,
    ValidationPhase,
    ValidationRecord,
    ValidationRecordKind,
)
from trading_assistant.journaling import ProposedPlanLevels, observe_outcome
from trading_assistant.journaling.models import (
    JournalDecisionRow,
    JournalOutcomeRow,
    JournalRecordRow,
)
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.setup_qualification import (
    QualificationParameters,
    SetupFamily,
    SetupResult,
    SetupState,
)
from trading_assistant.trade_planning import PlanningParameters, PlanState


def _split() -> ChronologicalSplit:
    return ChronologicalSplit(
        development_start=EPOCH + INTERVAL,
        development_end=EPOCH + 14 * INTERVAL,
        out_of_sample_start=EPOCH + 15 * INTERVAL,
        out_of_sample_end=EPOCH + 21 * INTERVAL,
    )


def _config(**changes) -> ValidationConfig:
    base = {
        "split": _split(),
        "observation_horizon_candles": 3,
        "minimum_sample_size": 1,
    }
    base.update(changes)
    return ValidationConfig(**base)


def _report(tmp_path: Path, *, config: ValidationConfig | None = None):
    engine, _url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    service = HistoricalValidationService(engine)
    report = service.validate(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        config=config or _config(),
    )
    return engine, service, report


def _levels(*, direction: str = "bullish") -> ProposedPlanLevels:
    if direction == "bullish":
        entry, stop, targets = D(100), D(90), (D(120),)
    else:
        entry, stop, targets = D(100), D(110), (D(80),)
    return ProposedPlanLevels(
        plan_id=f"{direction}-plan",
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        direction=direction,
        entry=entry,
        stop=stop,
        targets=targets,
        risk_per_unit=D(10),
        as_of=EPOCH + INTERVAL,
        setup_id=f"{direction}-setup",
    )


def _outcome(*, direction: str, candles):
    levels = _levels(direction=direction)
    return observe_outcome(
        journal_id=f"journal-{direction}",
        levels=levels,
        candles=candles,
        observed_through=candles[-1].timestamp,
    )


def _setup(*, family: SetupFamily, direction: str, ident: str) -> SetupResult:
    return SetupResult(
        id=ident,
        family=family,
        direction=direction,
        state=SetupState.QUALIFIED,
        seed_event_id=f"seed-{ident}",
        reference_id=f"reference-{ident}",
        created_at=EPOCH + INTERVAL,
        as_of=EPOCH + 2 * INTERVAL,
        source_timeframes=("1h",),
        rules=(),
    )


def _validation_record(
    *, setup: SetupResult, observation, period: str
) -> ValidationRecord:
    return ValidationRecord(
        id=f"record-{setup.id}",
        phase=ValidationPhase.DEVELOPMENT,
        kind=ValidationRecordKind.SETUP,
        as_of=setup.as_of,
        snapshot_state=SetupState.QUALIFIED,
        setup=setup,
        plan=SimpleNamespace(state=PlanState.PLANNABLE),
        observation=observation,
        observation_unavailable_reason=None,
        regime=RegimeLabel("BULLISH", "LOWER_OR_EQUAL_VOLATILITY", period),
    )


def test_replay_is_strictly_chronological_and_reports_split_ranges(tmp_path):
    engine, _service, report = _report(tmp_path)
    try:
        assert report.split.source == "EXPLICIT"
        assert report.split.development_end < report.split.out_of_sample_start
        assert report.development.decision_boundary_count == 14
        assert report.out_of_sample is not None
        assert report.out_of_sample.decision_boundary_count == 7
        assert all(
            record.as_of <= report.split.development_end
            for record in report.development and report.records
            if record.phase is ValidationPhase.DEVELOPMENT
        )
        assert any(
            record.snapshot_state is SetupState.WATCH for record in report.records
        )
        assert any(
            record.snapshot_state is SetupState.QUALIFIED for record in report.records
        )
    finally:
        engine.dispose()


def test_explicit_split_boundaries_must_align_to_the_base_timeframe(tmp_path):
    engine, _url = migrated_engine(tmp_path)
    try:
        insert_candles(engine, qualifying_candles())
        misaligned = ChronologicalSplit(
            development_start=EPOCH + INTERVAL,
            development_end=EPOCH + 14 * INTERVAL + timedelta(minutes=30),
            out_of_sample_start=EPOCH + 15 * INTERVAL,
            out_of_sample_end=EPOCH + 21 * INTERVAL,
        )
        with pytest.raises(
            ValueError, match="development_end must be a 1h candle-close boundary"
        ):
            HistoricalValidationService(engine).validate(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="1h",
                config=ValidationConfig(split=misaligned),
            )
    finally:
        engine.dispose()


def test_existing_setup_and_plan_are_invariant_when_future_candles_are_added(tmp_path):
    engine, service, first = _report(tmp_path)
    try:
        insert_candles(engine, (bar(21, 126), bar(22, 127), bar(23, 128)))
        second = service.validate(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", config=_config()
        )
        early_first = [
            (
                r.as_of,
                r.setup.id if r.setup else None,
                r.setup.state if r.setup else None,
                r.plan.to_json_dict() if r.plan else None,
            )
            for r in first.records
            if r.as_of <= EPOCH + 14 * INTERVAL
        ]
        early_second = [
            (
                r.as_of,
                r.setup.id if r.setup else None,
                r.setup.state if r.setup else None,
                r.plan.to_json_dict() if r.plan else None,
            )
            for r in second.records
            if r.as_of <= EPOCH + 14 * INTERVAL
        ]
        assert early_second == early_first
        # The explicit OOS endpoint also prevents later DB rows from changing
        # the bounded dataset/report fingerprints.
        assert second.dataset_fingerprint == first.dataset_fingerprint
        assert second.report_id == first.report_id
    finally:
        engine.dispose()


def test_higher_timeframe_context_is_not_used_before_its_close(tmp_path):
    engine, _url = migrated_engine(tmp_path)
    try:
        # Base history is continuous; the 00:00 4h candle is present in the DB
        # but must remain unavailable to an as_of at 03:00.
        insert_candles(engine, tuple(bar(i, 100 + i) for i in range(8)))
        # Fixture candle_at uses hour offsets even when a timeframe label is
        # changed, so set genuine 4h-aligned timestamps explicitly here.
        insert_candles(
            engine,
            (
                replace(bar(0, 100, timeframe="4h"), timestamp=EPOCH),
                replace(bar(0, 104, timeframe="4h"), timestamp=EPOCH + 4 * INTERVAL),
            ),
        )
        service = HistoricalValidationService(engine)
        qp = QualificationParameters(higher_timeframes=("4h",))
        frames = service._frames(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            end_as_of=EPOCH + 8 * INTERVAL,
            base_candles=tuple(
                service.candles.get_candles(
                    exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h"
                ).candles
            ),
            qualification_parameters=qp,
            pattern_parameters=__import__(
                "trading_assistant.pattern_liquidity",
                fromlist=["PatternLiquidityParameters"],
            ).PatternLiquidityParameters(),
            structure_parameters=__import__(
                "trading_assistant.market_structure",
                fromlist=["MarketStructureParameters"],
            ).MarketStructureParameters(),
        )
        at_three = next(
            frame for frame in frames if frame.patterns.as_of == EPOCH + 3 * INTERVAL
        )
        at_four = next(
            frame for frame in frames if frame.patterns.as_of == EPOCH + 4 * INTERVAL
        )
        assert (
            at_three.higher_timeframes[0].completeness.expected_latest_closed_open_time
            < EPOCH
        )
        assert (
            at_four.higher_timeframes[0].completeness.expected_latest_closed_open_time
            == EPOCH
        )
    finally:
        engine.dispose()


def test_development_observations_cannot_consume_out_of_sample_candles(tmp_path):
    engine, _service, report = _report(
        tmp_path, config=_config(observation_horizon_candles=100)
    )
    try:
        for record in report.records:
            if (
                record.phase is ValidationPhase.DEVELOPMENT
                and record.observation is not None
            ):
                assert (
                    record.observation.observed_through
                    <= report.split.development_end - INTERVAL
                )
            if (
                record.phase is ValidationPhase.OUT_OF_SAMPLE
                and record.observation is not None
            ):
                assert (
                    record.observation.observed_through
                    <= report.split.out_of_sample_end - INTERVAL
                )
    finally:
        engine.dispose()


def test_identical_inputs_produce_identical_report_id_and_json(tmp_path):
    engine, service, first = _report(tmp_path)
    try:
        second = service.validate(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", config=_config()
        )
        assert second.report_id == first.report_id
        assert second.dataset_fingerprint == first.dataset_fingerprint
        assert second.to_json() == first.to_json()
    finally:
        engine.dispose()


def test_friction_changes_adjusted_results_and_fingerprint_not_raw_replay(tmp_path):
    engine, service, zero = _report(tmp_path)
    try:
        friction = FrictionAssumptions(
            fee_bps=D(5), entry_slippage_bps=D(2), exit_slippage_bps=D(2)
        )
        adjusted_config = _config(friction=friction)
        adjusted = service.validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=adjusted_config,
        )
        assert adjusted.config_fingerprint != zero.config_fingerprint
        assert adjusted.report_id != zero.report_id

        # Use an unambiguous target-hit observation so the scenario difference
        # is observable even when the real replay's development cohort has no
        # terminal plan outcome.
        outcome = _outcome(
            direction="bullish", candles=(bar(1, 100), bar(2, 120, high=121, low=119))
        )
        record = _validation_record(
            setup=_setup(
                family=SetupFamily.BREAKOUT_RETEST,
                direction="bullish",
                ident="friction",
            ),
            observation=outcome,
            period="2024-01",
        )
        raw_metrics = service._metrics((record,), _config())
        adjusted_metrics = service._metrics((record,), adjusted_config)
        assert (
            raw_metrics.raw_observational_r.values
            == adjusted_metrics.raw_observational_r.values
        )
        assert (
            raw_metrics.friction_adjusted_hypothetical_r.values
            != adjusted_metrics.friction_adjusted_hypothetical_r.values
        )
    finally:
        engine.dispose()


def test_same_candle_entry_and_exit_is_ambiguous_not_a_favourable_win():
    candle = bar(1, 100, high=121, low=89)
    outcome = _outcome(direction="bullish", candles=(candle,))
    assert outcome.status is OutcomeStatus.AMBIGUOUS
    assert outcome.ambiguous is True
    assert outcome.entry_reached is True


def test_missing_candle_and_open_trajectory_remain_separate_from_completed():
    incomplete = _outcome(direction="bullish", candles=(bar(1, 100), bar(3, 101)))
    open_ = _outcome(direction="bearish", candles=(bar(1, 100, high=101, low=99),))
    assert incomplete.status is OutcomeStatus.INCOMPLETE_DATA
    assert incomplete.missing_candle_count == 1
    assert open_.status is OutcomeStatus.OPEN_AT_CUTOFF


def test_empty_dataset_is_a_deterministic_insufficient_report(tmp_path):
    engine, _url = migrated_engine(tmp_path)
    try:
        report = HistoricalValidationService(engine).validate(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h"
        )
        assert report.records == ()
        assert report.development.metrics.total_records == 0
        assert report.out_of_sample is None
        assert "out_of_sample_period_unavailable" in report.warnings
    finally:
        engine.dispose()


def test_long_short_multiple_families_and_regime_breakdowns_are_diagnostic_only(
    tmp_path,
):
    engine, _url = migrated_engine(tmp_path)
    try:
        long_outcome = _outcome(
            direction="bullish", candles=(bar(1, 100), bar(2, 120, high=121, low=119))
        )
        short_outcome = _outcome(
            direction="bearish", candles=(bar(1, 100), bar(2, 80, high=81, low=79))
        )
        records = (
            _validation_record(
                setup=_setup(
                    family=SetupFamily.BREAKOUT_RETEST,
                    direction="bullish",
                    ident="long",
                ),
                observation=long_outcome,
                period="2024-01",
            ),
            _validation_record(
                setup=_setup(
                    family=SetupFamily.LIQUIDITY_REVERSAL,
                    direction="bearish",
                    ident="short",
                ),
                observation=short_outcome,
                period="2024-02",
            ),
        )
        service = HistoricalValidationService(engine)
        cohort = service._cohort(
            phase=ValidationPhase.DEVELOPMENT,
            start=EPOCH,
            end=EPOCH + 3 * INTERVAL,
            records=records,
            decision_boundaries=(EPOCH + INTERVAL, EPOCH + 2 * INTERVAL),
            config=ValidationConfig(minimum_sample_size=1),
        )
        dimensions = {(item.dimension, item.value) for item in cohort.breakdowns}
        assert ("direction", "bullish") in dimensions
        assert ("direction", "bearish") in dimensions
        assert ("setup_family", SetupFamily.BREAKOUT_RETEST.value) in dimensions
        assert ("setup_family", SetupFamily.LIQUIDITY_REVERSAL.value) in dimensions
        assert ("market_trend", "BULLISH") in dimensions
        assert ("calendar_period", "2024-01") in dimensions
    finally:
        engine.dispose()


def test_validation_reads_candles_but_never_mutates_journal_or_raw_market_data(
    tmp_path,
):
    engine, _service, _report_value = _report(tmp_path)
    try:
        with engine.connect() as connection:
            before = (
                connection.scalar(select(func.count()).select_from(OHLCVCandleRecord)),
                connection.scalar(select(func.count()).select_from(JournalRecordRow)),
                connection.scalar(select(func.count()).select_from(JournalDecisionRow)),
                connection.scalar(select(func.count()).select_from(JournalOutcomeRow)),
            )
        HistoricalValidationService(engine).validate(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", config=_config()
        )
        with engine.connect() as connection:
            after = (
                connection.scalar(select(func.count()).select_from(OHLCVCandleRecord)),
                connection.scalar(select(func.count()).select_from(JournalRecordRow)),
                connection.scalar(select(func.count()).select_from(JournalDecisionRow)),
                connection.scalar(select(func.count()).select_from(JournalOutcomeRow)),
            )
        assert after == before
    finally:
        engine.dispose()


def test_validation_does_not_modify_existing_rule_objects(tmp_path):
    engine, _url = migrated_engine(tmp_path)
    try:
        insert_candles(engine, qualifying_candles())
        qualification = QualificationParameters()
        planning = PlanningParameters()
        service = HistoricalValidationService(engine)
        service.validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=_config(),
            qualification_parameters=qualification,
            planning_parameters=planning,
        )
        assert qualification == QualificationParameters()
        assert planning == PlanningParameters()
    finally:
        engine.dispose()


def test_validation_api_and_ui_are_explicitly_historical_and_read_only(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    client = make_client(engine, make_settings(url), clock=EPOCH + 30 * INTERVAL)
    try:
        response = client.get("/api/validation", params={"minimum_sample_size": 1})
        assert response.status_code == 200
        payload = response.json()
        assert (
            payload["limitations"][0] == "HISTORICAL VALIDATION — NOT LIVE PERFORMANCE."
        )
        assert "records" in payload
        source = (
            Path(__file__).parents[1]
            / "src/trading_assistant/web/static/js/views/validation.js"
        ).read_text()
        assert "HISTORICAL VALIDATION — NOT LIVE PERFORMANCE" in source
        assert "realised profit" in source
        assert (
            "/orders" not in source
            and "balance" not in source
            and "leverage" not in source
        )
    finally:
        engine.dispose()


# ----------------------------------------------------------------------
# trade-planning-v3 consistency: the replay plans under the same declared
# cost scenario the forward path certifies paper plans under
# ----------------------------------------------------------------------

#: The exact scenario of the independent-review regression: at
#: QUALIFYING_BOUNDARY of the labelled series the forward path creates zero
#: paper plans (economic_nonviability), while the pre-fix historical replay
#: returned PLANNABLE records for the identical geometry and costs.
HEAVY_SCENARIO = FrictionAssumptions(
    fee_bps=D(500), entry_slippage_bps=D(50), exit_slippage_bps=D(50)
)


def _boundary_plans(report) -> tuple:
    return tuple(
        record
        for record in report.records
        if record.as_of == QUALIFYING_BOUNDARY and record.plan is not None
    )


def test_historical_replay_applies_the_same_economic_gate_as_forward(tmp_path):
    """The confirmed regression: identical series, boundary and 500 bps costs.

    Forward refuses both QUALIFIED setups (``economic_nonviability``, zero
    paper plans); the gated historical replay must refuse the same boundary
    plans instead of returning PLANNABLE records.
    """

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(friction=HEAVY_SCENARIO),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    try:
        forward_refused = [
            obs
            for obs in harness.observations()
            if obs.plan_state is not None and obs.plan_state.value == "NO_PLAN"
        ]
        assert forward_refused
        assert all(
            "economic_nonviability" in (obs.no_trade_reason or "")
            for obs in forward_refused
        )
        assert harness.plans() == ()

        engine, _url = migrated_engine(tmp_path)
        try:
            insert_candles(engine, labelled_series())
            report = HistoricalValidationService(engine).validate(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="1h",
                config=_config(friction=HEAVY_SCENARIO),
            )
            boundary = _boundary_plans(report)
            assert boundary, "the labelled series qualifies at its last close"
            for record in boundary:
                assert record.plan.state is not PlanState.PLANNABLE
                assert record.plan.state.value == "NO_PLAN"
                assert record.plan.reasons == ("economic_nonviability",)
                economic = next(
                    rule for rule in record.plan.rules
                    if rule.rule_id == "economic_viability"
                )
                assert economic.outcome.value == "failed"
        finally:
            engine.dispose()
    finally:
        harness.engine.dispose()


def test_historical_replay_and_forward_agree_when_the_scenario_is_affordable(
    tmp_path,
):
    """The consistency is two-sided: an affordable 1 bp scenario plans on both paths."""

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(friction=DEFAULT_COST_SCENARIO),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    harness.run(refresh_market_data=False)
    try:
        assert len(harness.plans()) >= 1
        engine, _url = migrated_engine(tmp_path)
        try:
            insert_candles(engine, labelled_series())
            report = HistoricalValidationService(engine).validate(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="1h",
                config=_config(friction=DEFAULT_COST_SCENARIO),
            )
            boundary = _boundary_plans(report)
            assert boundary
            assert all(record.plan.state is PlanState.PLANNABLE for record in boundary)
        finally:
            engine.dispose()
    finally:
        harness.engine.dispose()


def test_default_zero_friction_replay_runs_the_pre_v3_pass_without_certifying(
    tmp_path,
):
    """A zero scenario declares no costs: the replay diagnoses, never certifies.

    The default (zero friction) replay runs the pre-v3 planning pass (rule not
    evaluated) — the same planning output a v2-era report produced — and the
    report states that the forward path withholds every paper plan under the
    same scenario, so the two surfaces are not comparable.
    """

    engine, _service, report = _report(tmp_path)
    try:
        assert report.rules_version == "historical-validation-v2"
        with_plans = [record for record in report.records if record.plan is not None]
        assert with_plans
        for record in with_plans:
            assert record.plan.state is PlanState.PLANNABLE
            economic = next(
                rule for rule in record.plan.rules
                if rule.rule_id == "economic_viability"
            )
            assert economic.outcome.value == "pending"
        assert any(
            "Zero-scenario replay" in limitation
            and "never a certification" in limitation
            and "economic_viability_unverifiable" in limitation
            for limitation in report.limitations
        )
        assert not any(
            "UNVERIFIED PLANNING POLICY REPLAY" in limitation
            for limitation in report.limitations
        )
    finally:
        engine.dispose()


def test_unverified_replay_policy_preserves_pre_v3_plans_as_distinguishable_cohort(
    tmp_path,
):
    """The explicit pre-v3 cohort replays old behaviour, clearly labelled."""

    engine, _url = migrated_engine(tmp_path)
    service = HistoricalValidationService(engine)
    try:
        insert_candles(engine, labelled_series())
        gated = service.validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=_config(friction=HEAVY_SCENARIO),
        )
        unverified = service.validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=_config(
                friction=HEAVY_SCENARIO, replay_policy=REPLAY_POLICY_UNVERIFIED_V2
            ),
        )
        # The pre-v3 planning behaviour is preserved under the explicit opt-in:
        # the same boundary plans that the gate refuses are PLANNABLE here,
        # with the economic_viability rule not evaluated.
        unverified_boundary = _boundary_plans(unverified)
        assert unverified_boundary
        for record in unverified_boundary:
            assert record.plan.state is PlanState.PLANNABLE
            economic = next(
                rule for rule in record.plan.rules
                if rule.rule_id == "economic_viability"
            )
            assert economic.outcome.value == "pending"

        # Explicit version/config provenance: the cohort is named in the
        # report and mechanically distinguishable in every fingerprint.
        assert (
            "replay_policy_cohort",
            REPLAY_POLICY_UNVERIFIED_V2,
        ) in unverified.strategy_versions
        assert (
            "replay_policy_cohort",
            REPLAY_POLICY_ECONOMIC_GATE,
        ) in gated.strategy_versions
        assert gated.config_fingerprint != unverified.config_fingerprint
        assert gated.report_id != unverified.report_id
        assert (
            "unverified_planning_policy_replay" in unverified.warnings
        )
        assert "unverified_planning_policy_replay" not in gated.warnings

        # The unverified cohort is reported as non-comparable with gated
        # forward decisions; the gated report carries no such label.
        assert any(
            "NOT comparable with gated forward decisions" in limitation
            for limitation in unverified.limitations
        )
        assert not any(
            "NOT comparable with gated forward decisions" in limitation
            for limitation in gated.limitations
        )
    finally:
        engine.dispose()


def test_declared_scenario_is_forced_into_planning_even_when_parameters_supplied(
    tmp_path,
):
    """A caller's v2-equivalent planning object cannot bypass the declared gate."""

    engine, _url = migrated_engine(tmp_path)
    try:
        insert_candles(engine, labelled_series())
        supplied = PlanningParameters()  # cost_assumptions=None (v2-equivalent)
        report = HistoricalValidationService(engine).validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=_config(friction=HEAVY_SCENARIO),
            planning_parameters=supplied,
        )
        # The object is used for its other fields but never mutated, and the
        # declared scenario still wins for the cost model (forward behaviour).
        assert supplied == PlanningParameters()
        boundary = _boundary_plans(report)
        assert boundary
        assert all(record.plan.state is not PlanState.PLANNABLE for record in boundary)
        assert all(
            record.plan.reasons == ("economic_nonviability",) for record in boundary
        )
    finally:
        engine.dispose()


def test_unknown_replay_policy_is_rejected():
    with pytest.raises(ValueError, match="replay_policy"):
        ValidationConfig(replay_policy="whenever")
