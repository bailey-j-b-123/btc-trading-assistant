"""Real SQLite fault boundaries in the unchanged offline paper engine.

All databases are temporary migrated fixtures. Faults execute invalid SQL on
the actual connection, not fabricated decisions or successful outcomes. A fresh
engine/service reopens the same temporary file for restart verification.
"""
from __future__ import annotations

from contextlib import contextmanager
import json

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from forward_fixtures import (
    EXCHANGE, INTERVAL, QUALIFYING_BOUNDARY, SYMBOL, TIMEFRAME,
    bar, labelled_series, make_harness, make_service,
)
from trading_assistant.database import create_database_engine
from trading_assistant.forward_testing import ForwardRunner
from trading_assistant.forward_testing import repository
from trading_assistant.forward_testing.tables import (
    ForwardCycleRow, ForwardObservationRow, ForwardPaperOutcomeRow,
    ForwardPaperPlanRow, ForwardRunnerHeartbeatRow,
)

SCOPE = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}
MISSING_TABLE = "endurance_deliberate_missing_table"
DECISION_TABLES = (
    (ForwardCycleRow, repository._CYCLE_IDENTITY_FIELDS),
    (ForwardObservationRow, repository._OBSERVATION_IDENTITY_FIELDS),
    (ForwardPaperPlanRow, repository._PAPER_PLAN_IDENTITY_FIELDS),
)


def _canonical_decisions(engine):
    result = {}
    with Session(engine) as session:
        for table, names in DECISION_TABLES:
            primary_key = tuple(table.__table__.primary_key.columns)
            rows = session.scalars(select(table).order_by(*primary_key)).all()
            result[table.__tablename__] = tuple(
                tuple((name, getattr(row, name)) for name in names) for row in rows
            )
    return result


def _raw_rows(engine, table):
    names = tuple(table.__table__.columns.keys())
    with Session(engine) as session:
        rows = session.scalars(
            select(table).order_by(*table.__table__.primary_key.columns)
        ).all()
        return tuple(tuple((name, getattr(row, name)) for name in names) for row in rows)


def _frozen_rows(engine):
    return {table.__tablename__: _raw_rows(engine, table) for table, _ in DECISION_TABLES}


def _outcome_rows(engine):
    names = tuple(name for name in ForwardPaperOutcomeRow.__table__.columns.keys() if name != "recorded_at")
    with Session(engine) as session:
        rows = session.scalars(select(ForwardPaperOutcomeRow).order_by(
            ForwardPaperOutcomeRow.paper_plan_id, ForwardPaperOutcomeRow.sequence,
        )).all()
        return tuple(tuple((name, getattr(row, name)) for name in names) for row in rows)


def _latest_payloads(service):
    return tuple(sorted((item.paper_plan_id, item.payload_json) for item in service.ledger.latest_outcomes(**SCOPE)))


def _genuine_sql_failure(engine):
    with engine.connect() as connection:
        connection.exec_driver_sql(f"SELECT * FROM {MISSING_TABLE}")


@contextmanager
def _fail_after_insert(engine, table):
    evidence = {"fired": False, "write_transaction_open": False}

    def fail(connection, _cursor, statement, _parameters, _context, _executemany):
        if evidence["fired"]:
            return
        normalized = statement.lower().replace('"', "").lstrip()
        if not normalized.startswith(f"insert into {table.__tablename__}"):
            return
        evidence["fired"] = True
        evidence["write_transaction_open"] = connection.in_transaction()
        # This fails after the real INSERT, inside the original write transaction.
        connection.exec_driver_sql(f"SELECT * FROM {MISSING_TABLE}")

    event.listen(engine, "after_cursor_execute", fail)
    try:
        yield evidence
    finally:
        event.remove(engine, "after_cursor_execute", fail)


def _prepare(stage, request):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    if stage != "entry":
        harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    candle = (
        bar(21, 126, low=123) if stage == "entry" else
        bar(22, 140, high=141, low=124) if stage == "target" else
        bar(22, 120, high=126, low=116)
    )
    harness.store((candle,))
    harness.advance_to(candle.timestamp + INTERVAL)
    return harness


def _run(service):
    return ForwardRunner(service, runner_id="endurance-synthetic-fault").run(
        once=True, refresh_market_data=False,
    )


def _reopen(harness, request):
    old = harness.service
    harness.engine.dispose()
    engine = create_database_engine(harness.settings.database_url)
    request.addfinalizer(engine.dispose)
    harness.engine = engine
    harness.service = make_service(
        engine, harness.settings, None, clock=lambda: harness.clock["t"],
        parameters=old.parameters, runner_settings=old.runner_settings,
        ledger_start=old.ledger_start, backfill_start=old.backfill_start,
    )
    return harness.service


def _check_database_and_audits(engine, *, errors=1):
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalars().all() == ["ok"]
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with Session(engine) as session:
        error_rows = session.scalars(select(ForwardRunnerHeartbeatRow).where(
            ForwardRunnerHeartbeatRow.status == "ERROR",
        )).all()
        assert len(error_rows) == errors
        assert all(row.error_type == "OperationalError" and MISSING_TABLE in row.last_error for row in error_rows)


@pytest.mark.parametrize("fault", ["outcome_transaction", "after_bundle_commit"])
@pytest.mark.parametrize("stage", ["entry", "target", "stop"])
def test_restart_reconciles_outcomes_after_a_committed_cycle_without_new_candles(
    monkeypatch, request, fault, stage,
):
    baseline = _prepare(stage, request)
    subject = _prepare(stage, request)
    _run(baseline.service)
    expected_latest = _latest_payloads(baseline.service)
    expected_versions = _outcome_rows(baseline.engine)
    before_versions = _raw_rows(subject.engine, ForwardPaperOutcomeRow)
    before_heartbeats = _raw_rows(subject.engine, ForwardRunnerHeartbeatRow)

    if fault == "outcome_transaction":
        with _fail_after_insert(subject.engine, ForwardPaperOutcomeRow) as evidence:
            with pytest.raises(OperationalError, match=MISSING_TABLE):
                _run(subject.service)
        assert evidence == {"fired": True, "write_transaction_open": True}
    else:
        original = subject.service.ledger.insert_cycle_bundle

        def fail_after_commit(*args, **kwargs):
            committed = original(*args, **kwargs)
            # The real boundary bundle has committed and released its session.
            assert subject.engine.pool.checkedout() == 0
            _genuine_sql_failure(subject.engine)
            return committed

        with monkeypatch.context() as patch:
            patch.setattr(subject.service.ledger, "insert_cycle_bundle", fail_after_commit)
            with pytest.raises(OperationalError, match=MISSING_TABLE):
                _run(subject.service)

    assert subject.engine.pool.checkedout() == 0
    _check_database_and_audits(subject.engine)
    assert _canonical_decisions(subject.engine) == _canonical_decisions(baseline.engine)
    assert _raw_rows(subject.engine, ForwardPaperOutcomeRow) == before_versions
    frozen = _frozen_rows(subject.engine)
    failed_heartbeats = _raw_rows(subject.engine, ForwardRunnerHeartbeatRow)
    assert set(before_heartbeats).issubset(failed_heartbeats)

    recovered = _run(_reopen(subject, request))
    latest_at_same_boundary = _latest_payloads(subject.service)
    versions_at_same_boundary = _outcome_rows(subject.engine)
    assert _frozen_rows(subject.engine) == frozen
    assert set(failed_heartbeats).issubset(_raw_rows(subject.engine, ForwardRunnerHeartbeatRow))
    assert set(before_versions).issubset(_raw_rows(subject.engine, ForwardPaperOutcomeRow))
    assert recovered.processed_boundaries == ()
    assert recovered.cycles_recorded == recovered.observations_recorded == recovered.paper_plans_created == 0
    _check_database_and_audits(subject.engine)

    # Capture the next-boundary consequence before asserting the same-boundary repair.
    next_counts = None
    if stage != "entry":
        following = bar(23, 121, low=118, high=122)
        baseline.step((following,), refresh_market_data=False)
        subject.step((following,), refresh_market_data=False)
        next_counts = {
            "baseline_plans": baseline.counts()["paper_plans"],
            "recovered_plans": subject.counts()["paper_plans"],
        }
    print("ENDURANCE_RECOVERY_TRACE=" + json.dumps({
        "fault": fault, "stage": stage,
        "same_boundary_outcomes_written": recovered.outcomes_recorded,
        "latest_payload_matches": latest_at_same_boundary == expected_latest,
        "outcome_versions_match": versions_at_same_boundary == expected_versions,
        "next_boundary": next_counts,
        "failed_cycle_observations_and_plan_unchanged": True,
        "existing_error_audits_preserved": True,
    }, sort_keys=True))
    assert latest_at_same_boundary == expected_latest, "a committed cycle must not strand missing or stale outcomes after restart"
    assert versions_at_same_boundary == expected_versions
    assert recovered.outcomes_recorded == 1
    if next_counts is not None:
        assert next_counts == {"baseline_plans": 2, "recovered_plans": 2}
        assert _canonical_decisions(subject.engine) == _canonical_decisions(baseline.engine)
        assert _outcome_rows(subject.engine) == _outcome_rows(baseline.engine)
    before_repeat = _frozen_rows(subject.engine), _outcome_rows(subject.engine)
    repeat = _run(subject.service)
    assert repeat.outcomes_recorded == 0
    assert (_frozen_rows(subject.engine), _outcome_rows(subject.engine)) == before_repeat


def test_real_sql_failure_after_plan_insert_rolls_back_the_entire_boundary(request):
    baseline = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    subject = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(baseline.engine.dispose)
    request.addfinalizer(subject.engine.dispose)
    baseline.advance_to(QUALIFYING_BOUNDARY)
    subject.advance_to(QUALIFYING_BOUNDARY)
    _run(baseline.service)
    with _fail_after_insert(subject.engine, ForwardPaperPlanRow) as evidence:
        with pytest.raises(OperationalError, match=MISSING_TABLE):
            _run(subject.service)
    assert evidence == {"fired": True, "write_transaction_open": True}
    assert subject.counts() == {"cycles": 0, "observations": 0, "paper_plans": 0, "outcome_versions": 0}
    assert subject.engine.pool.checkedout() == 0
    _check_database_and_audits(subject.engine)
    recovered = _run(_reopen(subject, request))
    assert recovered.paper_plans_created == 1
    assert _canonical_decisions(subject.engine) == _canonical_decisions(baseline.engine)
    before = _canonical_decisions(subject.engine)
    repeated = _run(subject.service)
    assert repeated.cycles_recorded == repeated.observations_recorded == repeated.paper_plans_created == 0
    assert _canonical_decisions(subject.engine) == before


def test_real_sql_failure_after_outcome_commit_does_not_duplicate_a_version(
    monkeypatch, request,
):
    baseline = _prepare("entry", request)
    subject = _prepare("entry", request)
    _run(baseline.service)
    original = subject.service.ledger.append_outcome

    def fail_after_commit(*args, **kwargs):
        committed = original(*args, **kwargs)
        assert subject.engine.pool.checkedout() == 0
        _genuine_sql_failure(subject.engine)
        return committed

    with monkeypatch.context() as patch:
        patch.setattr(subject.service.ledger, "append_outcome", fail_after_commit)
        with pytest.raises(OperationalError, match=MISSING_TABLE):
            _run(subject.service)
    _check_database_and_audits(subject.engine)
    assert _canonical_decisions(subject.engine) == _canonical_decisions(baseline.engine)
    assert _outcome_rows(subject.engine) == _outcome_rows(baseline.engine)
    before = _frozen_rows(subject.engine), _outcome_rows(subject.engine)
    recovered = _run(_reopen(subject, request))
    assert recovered.outcomes_recorded == 0
    assert (_frozen_rows(subject.engine), _outcome_rows(subject.engine)) == before
    _check_database_and_audits(subject.engine)


@pytest.mark.parametrize("stage", ["target", "stop"])
def test_restart_directly_at_next_close_reconciles_before_a_new_slot_decision(request, stage):
    baseline = _prepare(stage, request)
    subject = _prepare(stage, request)
    _run(baseline.service)
    with _fail_after_insert(subject.engine, ForwardPaperOutcomeRow):
        with pytest.raises(OperationalError, match=MISSING_TABLE):
            _run(subject.service)
    frozen_before_restart = _frozen_rows(subject.engine)
    error_audits = _raw_rows(subject.engine, ForwardRunnerHeartbeatRow)
    _reopen(subject, request)
    following = bar(23, 121, low=118, high=122)
    baseline.step((following,), refresh_market_data=False)
    subject.store((following,))
    subject.advance_to(following.timestamp + INTERVAL)
    recovered = _run(subject.service)
    # No intermediate retry took place at the interrupted close.
    assert recovered.processed_boundaries == (QUALIFYING_BOUNDARY + 3 * INTERVAL,)
    assert recovered.paper_plans_created == 1
    assert recovered.outcomes_recorded == 1
    assert baseline.counts()["paper_plans"] == subject.counts()["paper_plans"] == 2
    assert _canonical_decisions(subject.engine) == _canonical_decisions(baseline.engine)
    assert _outcome_rows(subject.engine) == _outcome_rows(baseline.engine)
    for table, rows in frozen_before_restart.items():
        assert set(rows).issubset(_frozen_rows(subject.engine)[table])
    assert set(error_audits).issubset(_raw_rows(subject.engine, ForwardRunnerHeartbeatRow))
    _check_database_and_audits(subject.engine)


def test_caught_up_idle_does_not_refetch_ambiguity_minutes_or_duplicate_outcomes(
    monkeypatch, request,
):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    harness.step((bar(21, 140, high=141, low=123),), refresh_market_data=False)
    first_plan = harness.plans()[0]
    first_outcome = harness.latest_outcomes()[0]
    assert first_outcome.status.value == "AMBIGUOUS"
    assert harness.service._resolution_recheck_due(first_outcome, first_plan) is True
    source_calls = harness.source.calls
    before = _frozen_rows(harness.engine), _outcome_rows(harness.engine)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("caught-up idle must not fetch new minute evidence or append outcomes")

    with monkeypatch.context() as patch:
        patch.setattr(harness.service, "_ensure_resolution_candles", forbidden)
        patch.setattr(harness.service.ledger, "append_outcome", forbidden)
        for _ in range(3):
            idle = _run(harness.service)
            assert idle.status.value == "IDLE"
            assert idle.outcomes_recorded == 0
            assert idle.processed_boundaries == ()
    assert harness.source.calls == source_calls
    assert (_frozen_rows(harness.engine), _outcome_rows(harness.engine)) == before


def test_failed_filled_hole_outcome_recovers_from_complete_cycle_at_the_same_asof(request):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    # The actual engine first records a missing next candle and its incomplete outcome.
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    missing_pass = harness.run(refresh_market_data=False)
    assert missing_pass.processed_boundaries == (QUALIFYING_BOUNDARY + INTERVAL,)
    incomplete = harness.latest_outcomes()[0]
    assert incomplete.status.value == "INCOMPLETE_DATA"
    original_cycles = _raw_rows(harness.engine, ForwardCycleRow)
    original_outcomes = _raw_rows(harness.engine, ForwardPaperOutcomeRow)
    # Only the synthetic missing candle arrives; no new close is added.
    harness.store((bar(21, 126, low=123),))
    with _fail_after_insert(harness.engine, ForwardPaperOutcomeRow) as evidence:
        with pytest.raises(OperationalError, match=MISSING_TABLE):
            _run(harness.service)
    assert evidence == {"fired": True, "write_transaction_open": True}
    same_boundary = [cycle for cycle in harness.cycles() if cycle.as_of == QUALIFYING_BOUNDARY + INTERVAL]
    assert sorted(cycle.status.value for cycle in same_boundary) == ["COMPLETE", "MISSING_CANDLE"]
    assert set(original_cycles).issubset(_raw_rows(harness.engine, ForwardCycleRow))
    assert _raw_rows(harness.engine, ForwardPaperOutcomeRow) == original_outcomes
    assert harness.latest_outcomes()[0].observation.observed_through == incomplete.observation.observed_through
    frozen = _frozen_rows(harness.engine)
    audits = _raw_rows(harness.engine, ForwardRunnerHeartbeatRow)
    latest_any_before_restart = harness.service.ledger.latest_cycle(**SCOPE).status.value
    recovered = _run(_reopen(harness, request))
    latest = harness.latest_outcomes()[0]
    print("ENDURANCE_FILLED_HOLE_TRACE=" + json.dumps({
        "coexisting_cycle_statuses": sorted(cycle.status.value for cycle in same_boundary),
        "latest_any_status": latest_any_before_restart,
        "before_status": incomplete.status.value,
        "before_candle_count": incomplete.observation.observed_candle_count,
        "after_status": latest.status.value,
        "after_candle_count": latest.observation.observed_candle_count,
        "same_boundary_outcomes_written": recovered.outcomes_recorded,
        "same_observed_through": latest.observation.observed_through == incomplete.observation.observed_through,
    }, sort_keys=True))
    assert recovered.processed_boundaries == ()
    assert recovered.cycles_recorded == recovered.observations_recorded == recovered.paper_plans_created == 0
    assert _frozen_rows(harness.engine) == frozen
    assert set(original_outcomes).issubset(_raw_rows(harness.engine, ForwardPaperOutcomeRow))
    assert set(audits).issubset(_raw_rows(harness.engine, ForwardRunnerHeartbeatRow))
    _check_database_and_audits(harness.engine)
    assert recovered.outcomes_recorded == 1
    assert latest.status.value == "OPEN_AT_CUTOFF"
    assert latest.observation.entry_reached is True
    assert latest.observation.observed_candle_count > incomplete.observation.observed_candle_count
    assert harness.outcome_versions(harness.plans()[0].paper_plan_id)[0] == incomplete


def test_committed_recovery_lookup_honors_scope_cutoff_and_ignores_missing_siblings(request):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
    harness.run(refresh_market_data=False)
    harness.store((bar(21, 126, low=123),))
    harness.run(refresh_market_data=False)
    boundary = QUALIFYING_BOUNDARY + INTERVAL
    assert sorted(cycle.status.value for cycle in harness.cycles() if cycle.as_of == boundary) == ["COMPLETE", "MISSING_CANDLE"]
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    assert harness.cycles()[-1].as_of > boundary
    before = (
        _frozen_rows(harness.engine),
        _raw_rows(harness.engine, ForwardPaperOutcomeRow),
        _raw_rows(harness.engine, ForwardRunnerHeartbeatRow),
    )
    chosen = harness.service.ledger.latest_complete_cycle(**SCOPE, as_of=boundary)
    assert chosen.complete is True
    assert chosen.as_of == boundary
    assert harness.service.ledger.latest_complete_cycle(**SCOPE, as_of=QUALIFYING_BOUNDARY - INTERVAL) is None
    for different_scope in (
        {**SCOPE, "exchange": "unused-fixture-exchange"},
        {**SCOPE, "symbol": "ETH/USDT"},
        {**SCOPE, "timeframe": "15m"},
    ):
        assert harness.service.ledger.latest_complete_cycle(**different_scope, as_of=boundary) is None
    assert (
        _frozen_rows(harness.engine),
        _raw_rows(harness.engine, ForwardPaperOutcomeRow),
        _raw_rows(harness.engine, ForwardRunnerHeartbeatRow),
    ) == before
    assert harness.engine.pool.checkedout() == 0
