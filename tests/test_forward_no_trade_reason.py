"""Step 12 policy repairs: one active BTC paper trade, and the MISSED reason.

Two approved rules are enforced and recorded here, using only the existing
Step 5/6 vocabulary (setup state, plan state and the planner's
``minimum_r_multiple_not_met`` refusal code):

* **At most one unresolved paper trade per instrument, on any timeframe.** A
  second genuinely plannable candidate (at the same close, or on another
  timeframe of the same exchange/symbol) is still monitored and recorded in
  full, but it is refused a paper trade with the deterministic reason
  ``NO TRADE — BTC paper trade already active.``; once the active trade settles,
  a later valid candidate may be paper-traded as usual.
* **MISSED.** A candidate that was genuinely plannable earlier (>= the mandatory
  1R floor), is still a valid QUALIFIED opportunity and is not paper-traded, but
  whose decision-time reward-to-risk has since deteriorated below the floor (at
  the price actually available now, no remaining genuine structural target
  reaches 1R) is recorded as
  ``MISSED — price moved before execution; remaining reward-to-risk is below 1R.``
  Nothing is chased, no old entry is reused, no stop is squeezed and no target is
  invented: no paper trade is created for it.

The 1R floor, the 1.5R preferred classification, structural-only targets,
decision-time entries, structural stops, the closed-candle rule, the
anti-lookahead guarantees and the timeframe hierarchy are all untouched.
Everything runs offline against temporary migrated SQLite databases.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest
from alembic import command
from alembic.config import Config
from forward_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    TIMEFRAME,
    QUALIFYING_BOUNDARY,
    bar,
    clock_at,
    labelled_series,
    make_harness,
    make_service,
)
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError
from web_fixtures import PROJECT_ROOT

import trading_assistant.forward_testing.repository as repository
from trading_assistant.database.engine import create_database_engine
from trading_assistant.forward_testing import (
    MISSED_OPPORTUNITY_REASON,
    PAPER_TRADE_ACTIVE_REASON,
    HeartbeatStatus,
)
from trading_assistant.forward_testing.models import paper_trade_occupies_slot
from trading_assistant.forward_testing.repository import (
    ForwardLedgerRepository,
    _cycle_values,
    _observation_values,
    _paper_plan_values,
)
from trading_assistant.forward_testing.tables import (
    ForwardCycleRow,
    ForwardObservationRow,
    ForwardPaperPlanRow,
)
from trading_assistant.setup_qualification import SetupState
from trading_assistant.trade_planning import PlanState

LONG_ENTRY = D("124")
LONG_STOP = D("117")


def seeded_harness(series=None):
    """The labelled series processed at its qualifying close (one paper trade)."""

    harness = make_harness(
        series=series if series is not None else labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    return harness


def refused_observations(harness):
    return [item for item in harness.observations() if item.no_trade_reason]


# ----------------------------------------------------------------------
# One active BTC paper trade
# ----------------------------------------------------------------------


def test_first_btc_paper_trade_is_recorded_and_a_second_is_refused() -> None:
    harness = seeded_harness()
    assert harness.service.status()["unresolved_paper_plan_count"] == 1
    plans = harness.plans()
    assert len(plans) == 1

    plannable = [
        item
        for item in harness.observations()
        if item.plan_state is PlanState.PLANNABLE
    ]
    # Both candidates are genuinely plannable at this close; exactly one is
    # paper-traded and the other is refused, deterministically.
    assert len(plannable) == 2
    taken = [item for item in plannable if item.paper_plan_id is not None]
    refused = [item for item in plannable if item.paper_plan_id is None]
    assert len(taken) == 1 and len(refused) == 1
    assert taken[0].setup_id != refused[0].setup_id
    assert refused[0].no_trade_reason == PAPER_TRADE_ACTIVE_REASON
    assert taken[0].no_trade_reason is None

    # The refused candidate is still monitored in full: the exact Step 6 plan
    # for that close is recorded (levels and R), only the paper trade is not.
    assert refused[0].plan_entry == LONG_ENTRY
    assert refused[0].plan_stop == LONG_STOP
    assert refused[0].plan_json
    assert refused[0].plan_targets == (D("138"),)

    # A recorded reason never coexists with a paper trade, on any row.
    assert all(
        not (item.no_trade_reason and item.paper_plan_id)
        for item in harness.observations()
    )


def test_a_refused_candidate_never_overwrites_the_active_trade() -> None:
    series = labelled_series() + (bar(21, 126, low=123),)
    harness = seeded_harness(series)
    active = harness.plans()[0]
    frozen = active.to_json_dict()
    refused_setup = refused_observations(harness)[0].setup_id

    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    result = harness.run(refresh_market_data=False)

    # The refusal created no plan, changed no stored plan, and left the active
    # trade frozen exactly as it was recorded.
    assert result.paper_plans_created == 0
    assert [plan.to_json_dict() for plan in harness.plans()] == [frozen]
    assert (
        harness.service.ledger.paper_plan_for_setup(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME,
            setup_id=refused_setup,
        )
        is None
    )
    # The refused candidate keeps being monitored at the later close, and is
    # refused again while the active trade is still unresolved.
    later = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == refused_setup
    ]
    assert later
    assert later[-1].paper_plan_id is None
    assert later[-1].no_trade_reason == PAPER_TRADE_ACTIVE_REASON


def test_a_later_valid_trade_is_recorded_once_the_first_settles() -> None:
    # Ordered entry, then a later stop: a clean STOPPED trajectory releases
    # the slot immediately. Same-candle entry+stop is AMBIGUOUS and occupies
    # until the horizon (covered separately); bar(21, 124, low=116) is that
    # trap because the fixture default high is close+1.
    series = labelled_series() + (
        bar(21, 126, low=123),
        bar(22, 124, high=125, low=116),
        bar(23, 124, low=123),
    )
    harness = seeded_harness(series)
    first = harness.plans()[0]
    assert harness.service.status()["unresolved_paper_plan_count"] == 1

    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.run(refresh_market_data=False)
    assert harness.latest_outcomes()[0].observation.status.value == "OPEN_AT_CUTOFF"
    assert harness.service.status()["unresolved_paper_plan_count"] == 1
    assert len(harness.plans()) == 1

    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    harness.run(refresh_market_data=False)
    assert harness.latest_outcomes()[0].observation.status.value == "STOPPED"
    assert harness.service.status()["unresolved_paper_plan_count"] == 0
    assert len(harness.plans()) == 1

    # With no active trade left, a later valid candidate may be paper-traded.
    harness.advance_to(QUALIFYING_BOUNDARY + 3 * INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 1
    plans = harness.plans()
    assert len(plans) == 2
    assert plans[1].setup_id != first.setup_id
    # Still never two unresolved paper trades at once.
    assert harness.service.status()["unresolved_paper_plan_count"] == 1


def test_a_refused_candidate_is_written_in_the_same_atomic_bundle(
    monkeypatch,
) -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    real_insert = repository.sqlite_insert

    def guarded(table, *args, **kwargs):  # noqa: ANN001 - mirrors sqlite_insert
        if table is ForwardPaperPlanRow:
            raise RuntimeError("injected paper-plan write failure")
        return real_insert(table, *args, **kwargs)

    monkeypatch.setattr(repository, "sqlite_insert", guarded)
    with pytest.raises(RuntimeError, match="injected paper-plan write failure"):
        harness.run(refresh_market_data=False)

    # The cycle, the refused candidate's observation (reason included) and the
    # paper plan are one transaction: a failure leaves no partial boundary.
    assert harness.counts() == {
        "cycles": 0,
        "observations": 0,
        "paper_plans": 0,
        "outcome_versions": 0,
    }

    # And the same boundary re-records identically once the failure clears.
    monkeypatch.setattr(repository, "sqlite_insert", real_insert)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 1
    assert harness.counts()["cycles"] == 1
    assert harness.counts()["paper_plans"] == 1
    assert any(
        item.no_trade_reason == PAPER_TRADE_ACTIVE_REASON
        for item in harness.observations()
    )


def test_the_ledger_refuses_a_reason_on_a_paper_traded_observation() -> None:
    harness = seeded_harness()
    plannable = next(
        item
        for item in harness.observations()
        if item.plan_state is PlanState.PLANNABLE and item.paper_plan_id is not None
    )
    violating = replace(
        plannable,
        observation_id="f" * 64,
        no_trade_reason=PAPER_TRADE_ACTIVE_REASON,
    )
    with pytest.raises(
        IntegrityError,
        match="ck_forward_observations_no_trade_reason_no_paper",
    ):
        harness.service.ledger.insert_observation(violating)


# ----------------------------------------------------------------------
# One active BTC paper trade — instrument-wide, across every timeframe
# ----------------------------------------------------------------------

#: A second supported timeframe of the same fixture instrument (5M, 15M and 1H
#: are all valid forward-runner timeframes; the fixture settings support 15m).
OTHER_TIMEFRAME = "15m"


def _timeframe_step(timeframe: str) -> timedelta:
    return INTERVAL if timeframe == TIMEFRAME else timedelta(minutes=15)


def candles_on(timeframe: str) -> tuple:
    """The labelled series on a timeframe's own grid.

    The shared fixtures author every candle on the hourly grid; each candle is
    re-tagged onto the requested timeframe's grid by its own fixture index, so
    the same price geometry replays on 1H and 15M with correct timestamps.
    """

    step = _timeframe_step(timeframe)
    return tuple(
        replace(
            candle,
            timeframe=timeframe,
            timestamp=EPOCH + step * ((candle.timestamp - EPOCH) // INTERVAL),
        )
        for candle in labelled_series()
    )


def harness_on(timeframe: str) -> tuple:
    """A fresh ledger whose first trade is entered on ``timeframe``."""

    candles = candles_on(timeframe)
    boundary = candles[-1].timestamp + _timeframe_step(timeframe)
    harness = make_harness(series=candles, ledger_start=boundary)
    harness.advance_to(boundary)
    return harness, boundary


def pass_on(harness, timeframe: str):
    """Run one real closed-candle pass for another timeframe of the same ledger."""

    candles = candles_on(timeframe)
    harness.store(candles)
    boundary = candles[-1].timestamp + _timeframe_step(timeframe)
    service = make_service(
        harness.engine,
        harness.settings,
        None,
        clock=lambda: clock_at(boundary),
        ledger_start=boundary,
    )
    result = service.run_once(timeframe=timeframe, refresh_market_data=False)
    return service, boundary, result


def plans_on(service, timeframe: str):
    return service.ledger.paper_plans(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=timeframe
    )


def unresolved_across_timeframes(service):
    """The ledger's own answer to "is a BTC paper trade active anywhere?"."""

    plans = service.ledger.paper_plans(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=None
    )
    latest = {
        outcome.paper_plan_id: outcome
        for outcome in service.ledger.latest_outcomes(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=None
        )
    }
    occupying = []
    for plan in plans:
        cycle = service.ledger.latest_cycle(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=plan.timeframe
        )
        now = cycle.as_of if cycle is not None else service._clock()
        if paper_trade_occupies_slot(latest.get(plan.paper_plan_id), plan, now=now):
            occupying.append(plan)
    return occupying


@pytest.mark.parametrize(
    ("first", "second"), [(TIMEFRAME, OTHER_TIMEFRAME), (OTHER_TIMEFRAME, TIMEFRAME)]
)
def test_a_paper_trade_on_another_timeframe_blocks_a_new_one(first, second) -> None:
    """One BTC paper trade means one trade, whichever timeframe recorded it."""

    harness, _ = harness_on(first)
    first_result = harness.run(timeframe=first, refresh_market_data=False)
    assert first_result.status is HeartbeatStatus.PROCESSED
    assert first_result.paper_plans_created == 1
    active = plans_on(harness.service, first)
    assert len(active) == 1
    frozen = active[0].to_json_dict()

    service, _, second_result = pass_on(harness, second)

    # The new timeframe is genuinely plannable, yet no second paper trade is
    # created while the first one is unresolved on another timeframe.
    assert second_result.status is HeartbeatStatus.PROCESSED
    assert second_result.paper_plans_created == 0
    observations = service.ledger.observations(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=second
    )
    plannable = [
        item for item in observations if item.plan_state is PlanState.PLANNABLE
    ]
    assert plannable
    assert all(item.paper_plan_id is None for item in plannable)
    assert all(
        item.no_trade_reason == PAPER_TRADE_ACTIVE_REASON for item in plannable
    )
    assert plans_on(service, second) == ()

    # The existing trade on the first timeframe is preserved byte for byte, and
    # the ledger still holds exactly one unresolved trade in total.
    assert [plan.to_json_dict() for plan in plans_on(service, first)] == [frozen]
    live = unresolved_across_timeframes(service)
    assert len(live) == 1 and live[0].timeframe == first


def test_a_settled_trade_on_another_timeframe_frees_the_instrument() -> None:
    """Settlement anywhere releases the instrument for later valid candidates."""

    # A 1H trade: ordered entry, then a later stop. Same-candle entry+stop
    # (bar(21, 124, low=116)) is AMBIGUOUS and occupies until the horizon.
    harness, _ = harness_on(TIMEFRAME)
    first_result = harness.run(timeframe=TIMEFRAME, refresh_market_data=False)
    assert first_result.paper_plans_created == 1
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    stopped = harness.step(
        (bar(22, 120, high=126, low=116),), refresh_market_data=False
    )
    assert stopped.status is HeartbeatStatus.PROCESSED
    assert harness.latest_outcomes()[0].observation.status.value == "STOPPED"
    assert len(unresolved_across_timeframes(harness.service)) == 0

    # A later 15M candidate may now be paper-traded: the settled 1H trade does
    # not block it, and the two plans coexist as *history*, never as two live
    # trades.
    service, _, later = pass_on(harness, OTHER_TIMEFRAME)
    assert later.paper_plans_created == 1
    created = plans_on(service, OTHER_TIMEFRAME)
    assert len(created) == 1
    assert created[0].timeframe == OTHER_TIMEFRAME
    live = unresolved_across_timeframes(service)
    assert [plan.paper_plan_id for plan in live] == [created[0].paper_plan_id]
    assert len(plans_on(service, TIMEFRAME)) == 1  # the old plan is preserved
    # The settled 1H trajectory keeps its recorded outcome versions, so the
    # instrument-wide answer is exactly the new 15M trade.
    assert service.ledger.latest_outcomes(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )


def test_the_repository_can_answer_the_instrument_wide_question() -> None:
    """``timeframe=None`` selects every stored timeframe, never just one."""

    harness, _ = harness_on(TIMEFRAME)
    harness.run(timeframe=TIMEFRAME, refresh_market_data=False)
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 120, high=126, low=116),), refresh_market_data=False)
    service, _, _ = pass_on(harness, OTHER_TIMEFRAME)
    ledger = service.ledger

    per_timeframe = {
        timeframe: ledger.paper_plans(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=timeframe
        )
        for timeframe in (TIMEFRAME, OTHER_TIMEFRAME)
    }
    assert all(per_timeframe.values())
    every = ledger.paper_plans(exchange=EXCHANGE, symbol=SYMBOL, timeframe=None)
    # Every stored timeframe, in one chronological (newest-last) sequence.
    chronological = sorted(
        (plan for plans in per_timeframe.values() for plan in plans),
        key=lambda plan: (plan.plan_as_of, plan.paper_plan_id),
    )
    assert [plan.paper_plan_id for plan in every] == [
        plan.paper_plan_id for plan in chronological
    ]
    assert {plan.timeframe for plan in every} == {TIMEFRAME, OTHER_TIMEFRAME}
    assert [
        plan.timeframe for plan in every
    ] == [OTHER_TIMEFRAME, TIMEFRAME]
    # The instrument-wide outcome question sees the recorded 1H version; the
    # just-created 15M plan has none yet, and a single-timeframe read would miss
    # the other trade entirely.
    assert ledger.latest_outcomes(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=None
    ) == ledger.latest_outcomes(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert ledger.latest_outcomes(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=OTHER_TIMEFRAME
    ) == ()
    assert unresolved_across_timeframes(service)


# ----------------------------------------------------------------------
# MISSED
# ----------------------------------------------------------------------


def test_a_monitored_candidate_becomes_missed_when_its_entry_moves_away() -> None:
    # The decision-time entry moves from 124 to 132 while the opportunity stays
    # QUALIFIED and no longer reaches 1R against its remaining structural level.
    series = labelled_series() + (bar(21, 132, low=129),)
    harness = seeded_harness(series)
    refused = refused_observations(harness)[0]
    assert refused.plan_state is PlanState.PLANNABLE  # it did offer >= 1R
    assert refused.paper_plan_id is None  # and was never taken
    assert refused.setup_state is SetupState.QUALIFIED

    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0

    missed = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == refused.setup_id
    ]
    assert missed
    latest = missed[-1]
    assert latest.setup_state is SetupState.QUALIFIED  # still valid, not failed
    assert latest.plan_state is PlanState.NO_PLAN
    assert latest.no_trade_reason == MISSED_OPPORTUNITY_REASON
    assert latest.paper_plan_id is None

    # The refusal is exactly the mandatory-floor refusal at the moved entry:
    # the same structural level was rejected for its R, not replaced.
    assert latest.plan_entry == D("132")
    assert latest.plan_risk_per_unit == D("15")
    assert latest.plan_stop == LONG_STOP  # structural stop, never squeezed
    assert latest.plan_targets == ()  # no target invented
    plan = json.loads(latest.plan_json)
    assert plan["state_detail"] == "minimum_r_multiple_not_met"
    assert (
        "138: R 0.40000000 < minimum_r_multiple 1" in plan["excluded_targets"]
    )

    # No paper trade came from the missed opportunity, and the active trade is
    # still the only one.
    assert len(harness.plans()) == 1
    assert harness.service.status()["unresolved_paper_plan_count"] == 1

    # A setup that is already paper-traded is never called missed: that
    # trajectory belongs to its paper plan's outcome versions.
    owner = harness.plans()[0].setup_id
    owner_observations = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY and item.setup_id == owner
    ]
    assert owner_observations
    assert owner_observations[-1].no_trade_reason is None


def test_missed_is_never_reported_without_an_earlier_plannable_opportunity() -> None:
    # Structurally targetless: the setup is QUALIFIED and refused, but it never
    # offered >= 1R, so nothing was missed.
    series = labelled_series(overhead_level=False) + (bar(21, 132, low=129),)
    harness = seeded_harness(series)
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.run(refresh_market_data=False)

    states = {item.plan_state for item in harness.observations()}
    assert PlanState.NO_PLAN in states
    assert all(item.no_trade_reason is None for item in harness.observations())
    assert harness.plans() == ()


def test_missed_is_never_reported_for_an_invalidated_or_failed_setup() -> None:
    # Price collapses through the invalidation: the setups fail (terminal
    # NO_SETUP with a terminal reason) rather than becoming a missed
    # opportunity, and no reason is recorded for a failed setup.
    series = labelled_series() + (bar(21, 110, high=126, low=105),)
    harness = seeded_harness(series)
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.run(refresh_market_data=False)

    later = [
        item
        for item in harness.observations()
        if item.as_of > QUALIFYING_BOUNDARY
    ]
    terminal = [item for item in later if item.setup_state is SetupState.NO_SETUP]
    assert terminal
    assert all(item.setup_terminal_reason for item in terminal)
    # A failed/invalidated setup is never a missed opportunity: no no-trade
    # reason at all is recorded for the collapsing close.
    assert all(item.no_trade_reason is None for item in later)


# ----------------------------------------------------------------------
# Storage: the migration that makes the refusal recordable
# -----------------------------------------------------------------------


def _bind(connection, column_type, value):  # noqa: ANN001 - test helper
    if value is None:
        return None
    processor = column_type.dialect_impl(connection.dialect).bind_processor(
        connection.dialect
    )
    return value if processor is None else processor(value)


def _insert_legacy_rows(connection, cycle, observations, plan) -> None:  # noqa: ANN001
    """Insert rows in the previous (0005) shape: no ``no_trade_reason`` column,
    and every plannable observation carrying the paper plan it created."""

    def insert(table, values: dict) -> None:
        params = {
            name: _bind(connection, table.c[name].type, value)
            for name, value in values.items()
        }
        connection.execute(
            text(
                f"INSERT INTO {table.name} ({', '.join(params)}) "
                f"VALUES ({', '.join(':' + name for name in params)})"
            ),
            params,
        )

    insert(ForwardCycleRow.__table__, _cycle_values(cycle))
    for observation in observations:
        values = _observation_values(observation)
        values.pop("no_trade_reason", None)
        unreserved_plannable = (
            values["plan_state"] == PlanState.PLANNABLE.value
            and not values["paper_plan_id"]
        )
        if unreserved_plannable:
            # 0005 required every PLANNABLE row to carry a paper plan.
            values["paper_plan_id"] = plan.paper_plan_id
        insert(ForwardObservationRow.__table__, values)
    insert(ForwardPaperPlanRow.__table__, _paper_plan_values(plan))


def test_the_migrated_schema_carries_exactly_the_declared_constraints() -> None:
    """The ORM metadata and the migration must agree about the new policy."""

    declared = {
        constraint.name
        for constraint in ForwardObservationRow.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    harness = seeded_harness()
    with harness.engine.connect() as connection:
        schema = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'forward_observations'"
        ).scalar_one()
    live = set(re.findall(r"CONSTRAINT (\w+) CHECK", schema))
    assert live == declared
    assert "ck_forward_observations_plannable_is_paper" not in live


def test_a_populated_v1_ledger_upgrades_through_the_constraint_rebuild(
    tmp_path,
) -> None:
    harness = seeded_harness()
    cycle = harness.cycles()[0]
    observations = harness.observations()
    plan = harness.plans()[0]
    harness.engine.dispose()

    # A previous-revision ledger that already holds the paper plan and its
    # observations: the rebuild's riskiest case (the child plan row references
    # the rebuilt table with ON DELETE RESTRICT).
    url = f"sqlite:///{tmp_path / 'legacy.sqlite3'}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0005_multi_timeframe_hierarchy")
    engine = create_database_engine(url)
    with engine.begin() as connection:
        _insert_legacy_rows(connection, cycle, observations, plan)
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_database_engine(url)
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM forward_observations"
            ).scalar_one()
            == len(observations)
        )
        # The plan still references an observation that exists.
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM forward_paper_plans AS p "
                "JOIN forward_observations AS o "
                "ON o.observation_id = p.observation_id"
            ).scalar_one()
            == 1
        )
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        # The append-only triggers were recreated after the rebuild.
        triggers = set(
            connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'forward_observations'"
            )
            .scalars()
            .all()
        )
        assert triggers == {
            "trg_forward_observations_no_update",
            "trg_forward_observations_no_delete",
        }
        schema = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE name = 'forward_observations'"
        ).scalar_one()
        assert "ck_forward_observations_plannable_is_paper" not in schema
        assert "ck_forward_observations_paper_requires_plannable" in schema
        assert "ck_forward_observations_no_trade_reason_no_paper" in schema
    # The newly legal shape writes: a plannable candidate refused a paper trade.
    plannable = next(
        item for item in observations if item.plan_state is PlanState.PLANNABLE
    )
    legacy_repository = ForwardLedgerRepository(engine)
    inserted, created = legacy_repository.insert_observation(
        replace(
            plannable,
            observation_id="a" * 64,
            paper_plan_id=None,
            no_trade_reason=PAPER_TRADE_ACTIVE_REASON,
        )
    )
    assert created is True
    assert inserted.no_trade_reason == PAPER_TRADE_ACTIVE_REASON
    # Immutability still holds at the database level after the rebuild.
    with pytest.raises(IntegrityError, match="append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE forward_observations SET no_trade_reason = NULL"
            )
    engine.dispose()

    # And the downgrade refuses while a recorded reason exists.
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(config, "0005_multi_timeframe_hierarchy")
