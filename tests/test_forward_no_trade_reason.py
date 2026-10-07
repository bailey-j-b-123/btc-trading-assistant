"""Step 12 policy repairs: one active BTC paper trade, and the MISSED reason.

Two approved rules are enforced and recorded here, using only the existing
Step 5/6 vocabulary (setup state, plan state and the planner's
``minimum_r_multiple_not_met`` refusal code):

* **At most one unresolved paper trade per instrument.** A second genuinely
  plannable candidate at the same close is still monitored and recorded in full,
  but it is refused a paper trade with the deterministic reason
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
from decimal import Decimal as D

import pytest
from alembic import command
from alembic.config import Config
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    TIMEFRAME,
    QUALIFYING_BOUNDARY,
    bar,
    labelled_series,
    make_harness,
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
    # The first candle after the qualifying close stops the first paper trade's
    # trajectory (its outcome can no longer change); the setups stay valid.
    series = labelled_series() + (bar(21, 124, low=116), bar(22, 124, low=123))
    harness = seeded_harness(series)
    first = harness.plans()[0]
    assert harness.service.status()["unresolved_paper_plan_count"] == 1

    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.run(refresh_market_data=False)
    assert harness.service.status()["unresolved_paper_plan_count"] == 0
    assert len(harness.plans()) == 1

    # With no active trade left, a later valid candidate may be paper-traded.
    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
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
