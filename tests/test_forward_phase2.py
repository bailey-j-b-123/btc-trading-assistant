"""Phase 2: occupancy vs settlement, same-cycle lock, honest fill rate.

An ``AMBIGUOUS`` paper trade is a terminal unscored outcome (never a win,
never re-observed, never a confirmed fill) but occupies the BTC/USDT paper
slot until the original observation horizon expires. Historical rows are
preserved; new rows stamp ``forward-ledger-v3``. Planning rules, including
the mandatory 1R floor, are unchanged.

Everything runs offline against temporary migrated SQLite databases.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace

from forward_fixtures import (
    EPOCH,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    bar,
    labelled_series,
    make_harness,
)

from trading_assistant.forward_testing import (
    FORWARD_LEDGER_RULES_VERSION,
    PAPER_TRADE_ACTIVE_REASON,
    ForwardParameters,
    HeartbeatStatus,
)
from trading_assistant.forward_testing.models import (
    paper_outcome_is_settled,
    paper_trade_occupies_slot,
)
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.trade_planning import PLANNING_RULES_VERSION, PlanState


HORIZON = 2

#: Same-candle entry+stop on the labelled long (entry 124 / stop 117). The
#: fixture ``bar`` default high is close+1, so ``bar(index, 124, low=116)`` is
#: AMBIGUOUS rather than a clean STOPPED. Close stays 124 so later candidates
#: can remain PLANNABLE.
AMBIGUOUS_ENTRY_AND_STOP = bar(21, 124, low=116)


def _plan(*, horizon: int = HORIZON, as_of: datetime = EPOCH):
    return SimpleNamespace(
        timeframe="1h",
        plan_as_of=as_of,
        observation_horizon_candles=horizon,
    )


def _outcome(status: OutcomeStatus, *, observed_through: datetime = EPOCH):
    return SimpleNamespace(
        observation=SimpleNamespace(status=status, observed_through=observed_through)
    )


# ----------------------------------------------------------------------
# Occupancy helper (unit)
# ----------------------------------------------------------------------


def test_ambiguous_occupies_until_horizon_and_never_converts_settlement() -> None:
    plan = _plan(horizon=2)
    outcome = _outcome(OutcomeStatus.AMBIGUOUS, observed_through=EPOCH)
    assert paper_outcome_is_settled(outcome, plan) is True
    assert paper_trade_occupies_slot(outcome, plan, now=EPOCH) is True
    assert paper_trade_occupies_slot(outcome, plan, now=EPOCH + 2 * INTERVAL) is True
    assert paper_trade_occupies_slot(outcome, plan, now=EPOCH + 3 * INTERVAL) is False


def test_stopped_and_targets_reached_release_the_slot_immediately() -> None:
    plan = _plan()
    now = EPOCH + INTERVAL
    for status in (
        OutcomeStatus.STOPPED,
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.STOPPED_AFTER_TARGETS,
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
    ):
        outcome = _outcome(status)
        assert paper_outcome_is_settled(outcome, plan) is True
        assert paper_trade_occupies_slot(outcome, plan, now=now) is False


def test_open_or_missing_outcome_occupies_until_horizon() -> None:
    plan = _plan(horizon=2)
    assert paper_trade_occupies_slot(None, plan, now=EPOCH + INTERVAL) is True
    open_outcome = _outcome(OutcomeStatus.OPEN_AT_CUTOFF)
    assert paper_outcome_is_settled(open_outcome, plan) is False
    assert paper_trade_occupies_slot(open_outcome, plan, now=EPOCH + INTERVAL) is True
    assert (
        paper_trade_occupies_slot(open_outcome, plan, now=EPOCH + 3 * INTERVAL)
        is False
    )


def test_entry_not_reached_occupies_until_the_horizon_settles() -> None:
    plan = _plan(horizon=2)
    interim = _outcome(OutcomeStatus.ENTRY_NOT_REACHED, observed_through=EPOCH)
    assert paper_outcome_is_settled(interim, plan) is False
    assert paper_trade_occupies_slot(interim, plan, now=EPOCH + INTERVAL) is True
    complete = _outcome(
        OutcomeStatus.ENTRY_NOT_REACHED, observed_through=EPOCH + INTERVAL
    )
    assert paper_outcome_is_settled(complete, plan) is True
    assert paper_trade_occupies_slot(complete, plan, now=EPOCH + 3 * INTERVAL) is False


# ----------------------------------------------------------------------
# Harness: AMBIGUOUS occupies; STOPPED frees; reused id cannot bypass
# ----------------------------------------------------------------------


def _seeded(*, horizon: int = HORIZON, extra=()):
    harness = make_harness(
        series=labelled_series() + tuple(extra),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(observation_horizon_candles=horizon),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.paper_plans_created == 1
    return harness


def test_new_paper_plans_stamp_ledger_v3_and_planning_v2() -> None:
    harness = _seeded()
    plan = harness.plans()[0]
    assert FORWARD_LEDGER_RULES_VERSION == "forward-ledger-v3"
    assert plan.ledger_rules_version == "forward-ledger-v3"
    assert plan.planning_rules_version == PLANNING_RULES_VERSION == "trade-planning-v2"
    assert plan.target_r_multiples
    assert max(value for value in plan.target_r_multiples if value is not None) >= 1
    dict_versions = dict(plan.strategy_versions)
    assert dict_versions["forward_ledger_rules"] == "forward-ledger-v3"
    assert dict_versions["trade_planning_rules"] == "trade-planning-v2"


def test_two_qualified_setups_at_the_same_close_create_at_most_one_paper_plan() -> None:
    harness = _seeded()
    assert len(harness.plans()) == 1
    plannable = [
        item
        for item in harness.observations()
        if item.plan_state is PlanState.PLANNABLE
    ]
    assert len(plannable) == 2
    taken = [item for item in plannable if item.paper_plan_id is not None]
    refused = [item for item in plannable if item.paper_plan_id is None]
    assert len(taken) == 1 and len(refused) == 1
    assert refused[0].no_trade_reason == PAPER_TRADE_ACTIVE_REASON


def test_ambiguous_first_bar_does_not_allow_a_second_paper_plan() -> None:
    """Same-candle entry+target occupies the slot; the other setup is refused."""

    harness = _seeded()
    first = harness.plans()[0]
    refused_setup = next(
        item.setup_id
        for item in harness.observations()
        if item.plan_state is PlanState.PLANNABLE and item.paper_plan_id is None
    )

    harness.step((AMBIGUOUS_ENTRY_AND_STOP,), refresh_market_data=False)
    outcome = harness.latest_outcomes()[0]
    assert outcome.observation.status is OutcomeStatus.AMBIGUOUS
    assert outcome.observation.entry_reached is True
    assert outcome.observation.entry_ordered is False
    assert outcome.observation.ambiguous is True
    assert paper_outcome_is_settled(outcome, first) is True
    assert harness.service.status()["unresolved_paper_plan_count"] == 1
    assert len(harness.plans()) == 1

    harness.step((bar(22, 124, low=123),), refresh_market_data=False)
    assert len(harness.plans()) == 1
    later = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == refused_setup
    ]
    assert later
    assert later[-1].paper_plan_id is None
    assert later[-1].no_trade_reason == PAPER_TRADE_ACTIVE_REASON
    # The original observation is still AMBIGUOUS: occupancy is not a conversion.
    assert harness.latest_outcomes()[0].observation.status is OutcomeStatus.AMBIGUOUS
    versions = harness.outcome_versions(first.paper_plan_id)
    assert all(item.observation.status is OutcomeStatus.AMBIGUOUS for item in versions)


def test_ambiguous_releases_the_slot_after_the_original_horizon() -> None:
    harness = _seeded(horizon=2)
    first = harness.plans()[0]
    harness.step((AMBIGUOUS_ENTRY_AND_STOP,), refresh_market_data=False)
    assert harness.latest_outcomes()[0].observation.status is OutcomeStatus.AMBIGUOUS
    assert harness.service.status()["unresolved_paper_plan_count"] == 1

    # Last horizon close still occupies (matches ENTRY_NOT_REACHED timing).
    harness.step((bar(22, 124, low=123),), refresh_market_data=False)
    assert harness.service.status()["unresolved_paper_plan_count"] == 1
    assert len(harness.plans()) == 1

    # The next close is past the horizon: slot free, original outcome unchanged.
    result = harness.step((bar(23, 124, low=123),), refresh_market_data=False)
    assert harness.latest_outcomes()[0].observation.status is OutcomeStatus.AMBIGUOUS
    versions = harness.outcome_versions(first.paper_plan_id)
    assert all(item.observation.status is OutcomeStatus.AMBIGUOUS for item in versions)
    assert first.paper_plan_id not in harness.service.status()["unresolved_paper_plan_ids"]
    assert paper_trade_occupies_slot(
        harness.latest_outcomes()[0],
        first,
        now=QUALIFYING_BOUNDARY + 3 * INTERVAL,
    ) is False
    # A new plan is allowed; never two occupying trades. Setups may have
    # left QUALIFIED by this close, so a second plan is not required.
    assert result.paper_plans_created in (0, 1)
    assert harness.service.status()["unresolved_paper_plan_count"] == result.paper_plans_created
    assert len(harness.plans()) == 1 + result.paper_plans_created


def test_reused_paper_plan_id_does_not_bypass_the_active_trade_check() -> None:
    """The owner setup stays PLANNABLE and reuses its id; the other is refused."""

    harness = _seeded()
    owner = harness.plans()[0].setup_id
    refused_setup = next(
        item.setup_id
        for item in harness.observations()
        if item.plan_state is PlanState.PLANNABLE and item.paper_plan_id is None
    )
    frozen = harness.plans()[0].paper_plan_id

    harness.step((AMBIGUOUS_ENTRY_AND_STOP,), refresh_market_data=False)
    harness.step((bar(22, 124, low=123),), refresh_market_data=False)

    owner_rows = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == owner
    ]
    assert owner_rows
    assert owner_rows[-1].paper_plan_id == frozen
    assert owner_rows[-1].no_trade_reason is None

    refused_rows = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == refused_setup
    ]
    assert refused_rows
    assert refused_rows[-1].paper_plan_id is None
    assert refused_rows[-1].no_trade_reason == PAPER_TRADE_ACTIVE_REASON
    assert len(harness.plans()) == 1


def test_entry_reached_rate_ignores_ambiguous_and_unordered_entries() -> None:
    harness = _seeded()
    harness.step((AMBIGUOUS_ENTRY_AND_STOP,), refresh_market_data=False)
    report = harness.report(parameters=ForwardParameters(minimum_sample_size=1))
    assert report.metrics.ambiguous_count == 1
    assert report.metrics.entry_reached_rate.numerator == 0
    assert report.metrics.entry_reached_rate.denominator == 1
    assert report.metrics.raw_observational_r.sample_size == 0
    excluded = {
        item.value: item.count for item in report.metrics.raw_observational_r.excluded
    }
    assert excluded.get("AMBIGUOUS_OUTCOME") == 1


# ----------------------------------------------------------------------
# End-to-end (audit section 9)
# ----------------------------------------------------------------------


def test_phase2_end_to_end_one_plan_ambiguous_no_second_trade() -> None:
    """Bootstrap → ≥1R plan → same-bar AMBIGUOUS → no second plan → journal empty."""

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(observation_horizon_candles=HORIZON),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.paper_plans_created == 1
    plan = harness.plans()[0]
    assert plan.ledger_rules_version == "forward-ledger-v3"
    assert plan.planning_rules_version == "trade-planning-v2"
    assert max(value for value in plan.target_r_multiples if value is not None) >= D(
        "1.2"
    )

    # Next bar trades through entry and target: ordering is unknowable.
    harness.step((bar(21, 140, high=141, low=124),), refresh_market_data=False)
    outcome = harness.latest_outcomes()[0]
    assert outcome.observation.status is OutcomeStatus.AMBIGUOUS
    assert outcome.observation.entry_ordered is False
    assert len(harness.plans()) == 1
    assert harness.service.status()["unresolved_paper_plan_count"] == 1

    # Another setup at the next close cannot open a second paper trade.
    harness.step((bar(22, 124, low=123),), refresh_market_data=False)
    assert len(harness.plans()) == 1
    owner_id = plan.setup_id
    other_later = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY
        and item.plan_state is PlanState.PLANNABLE
        and item.setup_id != owner_id
    ]
    assert all(item.paper_plan_id is None for item in other_later)
    assert all(
        item.no_trade_reason == PAPER_TRADE_ACTIVE_REASON for item in other_later
    )

    report = harness.report(parameters=ForwardParameters(minimum_sample_size=1))
    assert report.metrics.ambiguous_count == 1
    assert report.metrics.raw_observational_r.sample_size == 0
    assert report.metrics.entry_reached_rate.numerator == 0
    assert report.version_separation.value == "SINGLE_VERSION"
    fingerprints = {cycle.version_fingerprint for cycle in harness.cycles()}
    assert len(fingerprints) == 1

    tables = (
        "journal_records",
        "journal_decisions",
        "journal_outcomes",
        "journal_outcome_events",
    )
    with harness.engine.connect() as connection:
        counts = {
            table: connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed names
            ).scalar_one()
            for table in tables
        }
    assert counts == {table: 0 for table in tables}
