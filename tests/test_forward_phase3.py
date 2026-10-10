"""Phase 3 — trustworthy paper-trade outcome evaluation over the forward ledger.

End-to-end regression tests for the journal-outcome-v2 evaluation policy in
Step 12: genuine, confirmed stored 1-minute candles may order events inside an
ambiguous higher-timeframe candle, downloaded on demand through the unchanged
Step 2 pipeline (closed candles only, validated, raw-archived). The original
higher-timeframe plans, the mandatory 1R floor, PR #34's one-active
occupancy/same-cycle policy, and PR #35's Binance-only market-data isolation
are all preserved. Missing, incomplete, or still-ambiguous 1-minute evidence
keeps an explicitly unscored AMBIGUOUS; historical v1 rows are never
re-observed or rewritten.

Everything runs offline against temporary migrated SQLite databases.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from forward_fixtures import (
    EPOCH,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    bar,
    labelled_series,
    make_harness,
    minute_composition,
    two_target_series,
)

from trading_assistant.forward_testing import (
    FORWARD_LEDGER_RULES_VERSION,
    PAPER_TRADE_ACTIVE_REASON,
    ForwardParameters,
    HeartbeatStatus,
)
from trading_assistant.journaling import (
    OUTCOME_RESOLUTION_RULES_VERSION,
    OUTCOME_RULES_VERSION,
    OutcomeParameters,
    ProposedPlanLevels,
    observe_outcome,
)
from trading_assistant.journaling.observation import (
    RESOLUTION_NO_CANDLES,
    RESOLUTION_SAME_MINUTE_AMBIGUOUS,
    RESOLUTION_USED,
)
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.market_data.types import Candle
from trading_assistant.trade_planning import PLANNING_RULES_VERSION

HORIZON = 4


def make_resolution_harness(series=None, **kwargs):
    kwargs.setdefault("series", series if series is not None else labelled_series())
    kwargs.setdefault("ledger_start", QUALIFYING_BOUNDARY)
    kwargs.setdefault(
        "parameters", ForwardParameters(observation_horizon_candles=HORIZON)
    )
    harness = make_harness(**kwargs)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 1
    return harness


def step_with_minutes(harness, htf_bars: tuple, minutes: tuple = ()) -> None:
    """Store HTF candles, expose them plus 1-minute evidence on the exchange
    (never pre-storing the minutes), then run one pass."""

    harness.series = harness.series + tuple(htf_bars)
    harness.source.set_candles(tuple(harness.series) + tuple(minutes))
    harness.store(tuple(htf_bars))
    harness.advance_to(htf_bars[-1].timestamp + INTERVAL)
    result = harness.run()
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.market_data_error is None


def ambiguous_candle_and_resolving_minutes():
    """The labelled plan (entry 124 / stop 117 / target 138) meets a candle
    touching entry and stop; the 1-minute evidence orders entry first."""

    candle = bar(21, 124, low=116)
    minutes = minute_composition(
        candle,
        path=tuple(["124"] + ["122"] * 39 + ["121"] + ["122"] * 19 + ["124"]),
        extra_lows={40: "116"},
        extra_highs={10: "125"},
    )
    return candle, minutes


# ----------------------------------------------------------------------
# Resolution end-to-end
# ----------------------------------------------------------------------


def test_resolved_entry_before_stop_becomes_stopped_and_releases_the_slot() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    assert plan.ledger_rules_version == FORWARD_LEDGER_RULES_VERSION
    assert plan.planning_rules_version == PLANNING_RULES_VERSION

    candle, minutes = ambiguous_candle_and_resolving_minutes()
    step_with_minutes(harness, (candle,), minutes)

    # The runner fetched exactly the ambiguous candle's closed 1-minute span.
    stored = harness.service.candles.get_candles(
        exchange="binance", symbol="BTC/USDT", timeframe="1m"
    )
    assert len(stored.candles) == 60
    assert stored.candles[0].timestamp == candle.timestamp

    outcome = harness.latest_outcomes()[0]
    observation = outcome.observation
    assert outcome.observation_rules_version == OUTCOME_RESOLUTION_RULES_VERSION
    assert observation.status is OutcomeStatus.STOPPED
    assert observation.entry_ordered is True
    assert observation.resolution_used is True
    assert observation.resolution_reason == RESOLUTION_USED
    assert observation.entry_timestamp == candle.timestamp  # minute 0 open
    assert observation.stop_timestamp is not None
    # The frozen plan levels are untouched by the resolution evidence.
    assert observation.entry_level == D("124")
    assert observation.stop_level == D("117")
    assert observation.target_levels == (D("138"),)

    # A clean terminal releases the one-active slot immediately.
    assert harness.service.status()["unresolved_paper_plan_count"] == 0
    report = harness.report(parameters=ForwardParameters(minimum_sample_size=1))
    assert report.metrics.ambiguous_count == 0
    assert report.metrics.stopped_rate.numerator == 1
    assert report.metrics.stopped_rate.denominator == 1
    assert report.metrics.entry_reached_rate.numerator == 1


def test_missing_1m_evidence_keeps_ambiguous_and_occupies_the_slot() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    candle = bar(21, 124, low=116)
    # The exchange serves no 1-minute candles at all.
    step_with_minutes(harness, (candle,))

    outcome = harness.latest_outcomes()[0]
    observation = outcome.observation
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.observation_rules_version == OUTCOME_RESOLUTION_RULES_VERSION
    assert observation.resolution_attempted is True
    assert observation.resolution_used is False
    assert observation.resolution_reason == RESOLUTION_NO_CANDLES
    assert observation.resolution_missing_candles == 60
    assert observation.entry_ordered is False

    # Phase 2 occupancy preserved: the unscored ambiguity keeps the slot and
    # blocks a second paper trade for another plannable setup.
    assert harness.service.status()["unresolved_paper_plan_count"] == 1
    step_with_minutes(harness, (bar(22, 124, low=123),))
    assert len(harness.plans()) == 1
    other_later = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY
        and item.setup_id != plan.setup_id
        and item.paper_plan_id is None
        and item.no_trade_reason is not None
    ]
    assert other_later
    assert all(
        item.no_trade_reason == PAPER_TRADE_ACTIVE_REASON for item in other_later
    )
    # The missing-evidence ambiguity is re-checkable: evidence may still arrive.
    assert harness.service._resolution_recheck_due(outcome, plan) is True


def test_same_minute_evidence_is_final_and_never_rechecked() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    candle = bar(21, 124, low=116)
    # Entry and stop are both touched inside minute 0: unknowable at 1m too.
    minutes = minute_composition(
        candle,
        path=tuple(["124"] + ["120"] + ["119"] * 58 + ["124"]),
        extra_lows={0: "116"},
        extra_highs={0: "125"},
    )
    step_with_minutes(harness, (candle,), minutes)
    outcome = harness.latest_outcomes()[0]
    observation = outcome.observation
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.resolution_reason == RESOLUTION_SAME_MINUTE_AMBIGUOUS
    assert observation.resolution_used is False
    assert observation.entry_ordered is False
    assert harness.service._resolution_recheck_due(outcome, plan) is False

    # Later passes append no further versions: the evidence was present and
    # insufficient, so the outcome is final.
    versions_before = len(harness.outcome_versions(plan.paper_plan_id))
    step_with_minutes(harness, (bar(22, 124, low=123),))
    assert len(harness.outcome_versions(plan.paper_plan_id)) == versions_before


def test_late_evidence_appends_a_superseding_version_without_rewriting() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    candle, minutes = ambiguous_candle_and_resolving_minutes()

    # Pass 1: no 1-minute evidence exists yet -> unscored AMBIGUOUS.
    step_with_minutes(harness, (candle,))
    first = harness.latest_outcomes()[0]
    assert first.observation.status is OutcomeStatus.AMBIGUOUS
    assert first.observation.resolution_reason == RESOLUTION_NO_CANDLES

    # Pass 2: the evidence becomes available; a new version supersedes.
    step_with_minutes(harness, (bar(22, 124, low=123),), minutes)
    versions = harness.outcome_versions(plan.paper_plan_id)
    assert len(versions) == 2
    assert versions[0].outcome_id == first.outcome_id  # untouched
    assert versions[1].supersedes_outcome_id == first.outcome_id
    assert versions[1].observation.status is OutcomeStatus.STOPPED
    assert versions[1].observation.resolution_used is True
    latest = harness.latest_outcomes()[0]
    assert latest.outcome_id == versions[1].outcome_id

    # The historical v1-era ambiguity row is still exactly as recorded.
    assert versions[0].observation.status is OutcomeStatus.AMBIGUOUS
    assert versions[0].observation.observation_rules_version == (
        OUTCOME_RESOLUTION_RULES_VERSION
    )


def test_same_window_recheck_waits_for_evidence_until_it_arrives() -> None:
    harness = make_resolution_harness(
        parameters=ForwardParameters(observation_horizon_candles=1)
    )
    plan = harness.plans()[0]
    candle, minutes = ambiguous_candle_and_resolving_minutes()

    # Horizon 1: the observation window never grows past the first candle, so
    # this exercises the same-window re-check path specifically.
    step_with_minutes(harness, (candle,))
    first = harness.latest_outcomes()[0]
    assert first.observation.status is OutcomeStatus.AMBIGUOUS
    assert first.observation.resolution_reason == RESOLUTION_NO_CANDLES
    assert len(harness.outcome_versions(plan.paper_plan_id)) == 1

    # Still no evidence: the re-check reproduces the identical payload, so no
    # duplicate version is appended (deterministic dedupe).
    step_with_minutes(harness, (bar(22, 124, low=123),))
    assert len(harness.outcome_versions(plan.paper_plan_id)) == 1

    # Evidence arrives: exactly one new superseding version.
    step_with_minutes(harness, (bar(23, 124, low=123),), minutes)
    versions = harness.outcome_versions(plan.paper_plan_id)
    assert len(versions) == 2
    assert versions[1].observation.status is OutcomeStatus.STOPPED
    assert versions[1].observed_through == versions[0].observed_through


def test_restart_and_duplicate_processing_create_no_duplicate_outcomes() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    candle, minutes = ambiguous_candle_and_resolving_minutes()
    step_with_minutes(harness, (candle,), minutes)
    assert harness.latest_outcomes()[0].observation.status is OutcomeStatus.STOPPED
    versions = len(harness.outcome_versions(plan.paper_plan_id))
    cycles = len(harness.cycles())

    # Re-running the same boundary (a restart with no new candle) records
    # nothing new: identities are deterministic fingerprints.
    harness.advance_to(candle.timestamp + INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.cycles_recorded == 0
    assert result.outcomes_recorded == 0
    assert len(harness.outcome_versions(plan.paper_plan_id)) == versions
    assert len(harness.cycles()) == cycles


def test_two_ambiguous_candles_each_fetch_their_own_minute_window() -> None:
    harness = make_resolution_harness(series=two_target_series())
    plan = harness.plans()[0]
    assert plan.targets == (D("138"), D("145"))

    # Candle 21: entry + target 1 touched. Minutes order entry first, then
    # target 1: the trajectory continues to the next candle.
    first = bar(21, 130, high=139, low=123)
    first_minutes = minute_composition(
        first,
        path=tuple(["130", "128"] + ["128"] * 58 + ["130"]),
        extra_lows={2: "123"},
        extra_highs={30: "139"},
    )
    # Candle 22: stop + both targets touched after the ordered entry. Minutes
    # reach target 2 before the stop wick: TARGETS_REACHED.
    second = bar(22, 120, high=146, low=116)
    second_minutes = minute_composition(
        second,
        path=tuple(["120"] + ["121"] * 59 + ["120"]),
        extra_highs={10: "146"},
        extra_lows={40: "116"},
    )
    step_with_minutes(harness, (first, second), first_minutes + second_minutes)

    outcome = harness.latest_outcomes()[0]
    observation = outcome.observation
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.targets_reached == (0, 1)
    assert observation.resolution_used is True
    # Both ambiguous candles consulted a full 60-minute window each.
    assert observation.resolution_expected_candles == 120
    assert observation.resolution_present_candles == 120
    stored = harness.service.candles.get_candles(
        exchange="binance", symbol="BTC/USDT", timeframe="1m"
    )
    assert len(stored.candles) == 120


# ----------------------------------------------------------------------
# Version separation and historical immutability
# ----------------------------------------------------------------------


def test_new_cycles_stamp_the_v2_evaluation_policy() -> None:
    harness = make_resolution_harness()
    cycle = harness.cycles()[0]
    versions = dict(cycle.strategy_versions)
    assert (
        versions["outcome_observation_rules"] == OUTCOME_RESOLUTION_RULES_VERSION
    )
    assert versions["forward_ledger_rules"] == FORWARD_LEDGER_RULES_VERSION
    assert versions["trade_planning_rules"] == PLANNING_RULES_VERSION
    # The mandatory 1R floor is untouched: every retained target is >= 1R.
    plan = harness.plans()[0]
    assert all(
        value is None or value >= D("1") for value in plan.target_r_multiples
    )


def test_legacy_v1_ambiguous_outcomes_are_never_reobserved() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    candle = bar(21, 124, low=116)
    harness.series = harness.series + (candle,)
    harness.source.set_candles(tuple(harness.series))
    harness.store((candle,))
    harness.advance_to(candle.timestamp + INTERVAL)
    # Hand-record a journal-outcome-v1 AMBIGUOUS row exactly as the pre-Phase-3
    # runner would have stored it.
    observation_v1 = observe_outcome(
        journal_id=f"forward-paper:{plan.paper_plan_id}",
        levels=ProposedPlanLevels(
            plan_id=plan.plan_id,
            exchange=plan.exchange,
            symbol=plan.symbol,
            timeframe=plan.timeframe,
            direction=plan.direction,
            entry=plan.entry,
            stop=plan.stop,
            targets=plan.targets,
            risk_per_unit=plan.risk_per_unit,
            as_of=plan.plan_as_of,
            setup_id=plan.setup_id,
        ),
        candles=(candle,),
        observed_through=candle.timestamp,
        parameters=OutcomeParameters(),
    )
    assert observation_v1.status is OutcomeStatus.AMBIGUOUS
    legacy = harness.service._wrap_observation(
        plan=plan, observation=observation_v1, recorded_at=harness.clock["t"]
    )
    harness.service.ledger.append_outcome(legacy)
    assert len(harness.outcome_versions(plan.paper_plan_id)) == 1

    # The 1-minute evidence then becomes available, but the v1 row is never
    # re-observed, never rewritten, and never superseded.
    minutes = minute_composition(
        candle,
        path=tuple(["124"] + ["122"] * 39 + ["121"] + ["122"] * 19 + ["124"]),
        extra_lows={40: "116"},
        extra_highs={10: "125"},
    )
    step_with_minutes(harness, (bar(22, 124, low=123),), minutes)
    versions = harness.outcome_versions(plan.paper_plan_id)
    assert len(versions) == 1
    latest = harness.latest_outcomes()[0]
    assert latest.observation.observation_rules_version == OUTCOME_RULES_VERSION
    assert latest.observation.status is OutcomeStatus.AMBIGUOUS
    assert latest.payload_json == legacy.payload_json


def test_legacy_exchange_plans_never_receive_binance_minutes() -> None:
    """PR #35 isolation: a plan recorded under a legacy exchange identity can
    only be observed against that exchange's candles; Binance 1-minute
    evidence is never substituted for it."""

    harness = make_resolution_harness()
    binance_plan = harness.plans()[0]
    legacy_candle = replace(
        bar(21, 124, low=116), exchange="kraken", timeframe="1h"
    )
    harness.store((legacy_candle,))
    legacy_plan = SimpleNamespace(
        **{
            field: getattr(binance_plan, field)
            for field in (
                "plan_id",
                "symbol",
                "timeframe",
                "direction",
                "entry",
                "stop",
                "targets",
                "risk_per_unit",
                "plan_as_of",
                "setup_id",
                "observation_id",
                "observation_horizon_candles",
            )
        },
        exchange="kraken",
        paper_plan_id="legacy-kraken-plan",
    )
    calls_before = harness.source.calls
    outcome = harness.service._observe_with_resolution(
        plan=legacy_plan,
        candles=(legacy_candle,),
        observed_through=legacy_candle.timestamp,
        recorded_at=harness.clock["t"],
        boundary=legacy_candle.timestamp + INTERVAL,
    )
    observation = outcome.observation
    assert observation.status is OutcomeStatus.AMBIGUOUS
    assert observation.resolution_reason == RESOLUTION_NO_CANDLES
    assert observation.resolution_used is False
    # No 1-minute fetch was attempted for the legacy identity...
    assert harness.source.calls == calls_before
    stored = harness.service.candles.get_candles(
        exchange="kraken", symbol="BTC/USDT", timeframe="1m"
    )
    assert stored.candles == ()
    # ...and the outcome is never queued for re-checks it could not win.
    assert harness.service._resolution_recheck_due(outcome, legacy_plan) is False


# ----------------------------------------------------------------------
# Timeframe guard: 1m can never become a forward planning timeframe
# ----------------------------------------------------------------------


def test_1m_is_refused_as_a_forward_base_timeframe() -> None:
    harness = make_resolution_harness()
    with pytest.raises(ValueError, match="ordering evidence only"):
        harness.service.resolve_instrument(symbol=None, timeframe="1m")
    with pytest.raises(ValueError, match="ordering evidence only"):
        harness.service.run_once(timeframe="1m", refresh_market_data=False)


def test_forward_guard_does_not_disturb_supported_analytical_timeframes() -> None:
    harness = make_resolution_harness()
    for timeframe in ("15m", "1h", "4h"):
        exchange, symbol, resolved = harness.service.resolve_instrument(
            symbol=None, timeframe=timeframe
        )
        assert resolved == timeframe


# ----------------------------------------------------------------------
# Resolution re-check unit semantics
# ----------------------------------------------------------------------


def _v2_ambiguous(reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        observation=SimpleNamespace(
            status=OutcomeStatus.AMBIGUOUS,
            observation_rules_version=OUTCOME_RESOLUTION_RULES_VERSION,
            resolution_attempted=True,
            resolution_used=False,
            resolution_reason=reason,
        )
    )


def test_resolution_recheck_is_only_for_missing_evidence_on_active_exchange() -> None:
    harness = make_resolution_harness()
    plan = harness.plans()[0]
    pending = _v2_ambiguous(RESOLUTION_NO_CANDLES)
    assert harness.service._resolution_recheck_due(pending, plan) is True
    final_same_minute = _v2_ambiguous(RESOLUTION_SAME_MINUTE_AMBIGUOUS)
    assert harness.service._resolution_recheck_due(final_same_minute, plan) is False
    resolved = _v2_ambiguous(RESOLUTION_USED)
    resolved.observation.resolution_used = True
    assert harness.service._resolution_recheck_due(resolved, plan) is False
    legacy_plan = SimpleNamespace(exchange="kraken")
    assert harness.service._resolution_recheck_due(pending, legacy_plan) is False
    v1_row = SimpleNamespace(
        observation=SimpleNamespace(
            status=OutcomeStatus.AMBIGUOUS,
            observation_rules_version=OUTCOME_RULES_VERSION,
            resolution_attempted=False,
            resolution_used=False,
            resolution_reason=None,
        )
    )
    assert harness.service._resolution_recheck_due(v1_row, plan) is False
