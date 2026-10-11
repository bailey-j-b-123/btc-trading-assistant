"""Stored-decision provenance for independent 1H paper candidates.

Tests use the real multi-timeframe evaluator and immutable hierarchy ledger over
synthetic candles. They prove exact-boundary matching, pre-plan availability,
incomplete and stale data handling, and genuine four-timeframe confirmation
without changing any forward plan or outcome.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from multi_timeframe_fixtures import (
    DECISION_TIME,
    EPOCH,
    SCENARIO_DECISION_TIME,
    SCENARIO_HOUR,
    EXCHANGE,
    SYMBOL,
    hierarchy_candles,
    insert_hierarchy,
    make_service,
    scenario_candles,
)
from web_fixtures import migrated_engine

from trading_assistant.web.signal_provenance import paper_plan_signal_provenance

BAND_LOW = "117"
BAND_HIGH = "117"


def _plannable_scenario():
    return scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )


def _seed_hierarchy(tmp_path, *, missing_15m_open=None, record_at=None):
    engine, url = migrated_engine(tmp_path, "signal-provenance.sqlite3")
    data = hierarchy_candles(scenario=_plannable_scenario())
    if missing_15m_open is not None:
        data["15m"] = tuple(
            candle for candle in data["15m"] if candle.timestamp != missing_15m_open
        )
    insert_hierarchy(engine, data)
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    observation = None
    if record_at is not None:
        observation, created = service.record(snapshot, recorded_at=record_at)
        assert created is True
    return engine, service, snapshot, observation


def _paper_plan(snapshot, *, plan_as_of=None, recorded_at=None, setup_id=None):
    return SimpleNamespace(
        paper_plan_id="test-paper-plan",
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        setup_id=snapshot.setup.setup_id if setup_id is None else setup_id,
        family=snapshot.setup.family,
        direction=snapshot.setup.direction,
        plan_as_of=SCENARIO_DECISION_TIME if plan_as_of is None else plan_as_of,
        recorded_at=(
            SCENARIO_DECISION_TIME + timedelta(seconds=20)
            if recorded_at is None
            else recorded_at
        ),
    )


def test_missing_hierarchy_evidence_is_unavailable_not_inferred(tmp_path):
    engine, service, snapshot, _ = _seed_hierarchy(tmp_path)
    try:
        assert snapshot.decision.value == "plannable"
        provenance = paper_plan_signal_provenance(
            _paper_plan(snapshot), hierarchy_ledger=service.ledger
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert provenance["paper_candidate"]["label"] == "1H PAPER CANDIDATE"
        assert confirmation["status"] == "unavailable"
        assert confirmation["confirmed"] is False
        assert confirmation["matching_boundary"] is False
        assert "No stored hierarchy decision" in confirmation["reason"]
    finally:
        engine.dispose()


def test_later_hierarchy_boundary_never_confirms_an_earlier_candidate(tmp_path):
    engine, service, snapshot, observation = _seed_hierarchy(
        tmp_path,
        record_at=SCENARIO_DECISION_TIME + timedelta(seconds=1),
    )
    try:
        assert observation is not None and observation.decision == "plannable"
        provenance = paper_plan_signal_provenance(
            _paper_plan(snapshot, plan_as_of=DECISION_TIME),
            hierarchy_ledger=service.ledger,
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert confirmation["status"] == "unavailable"
        assert confirmation["confirmed"] is False
        assert confirmation["matching_decision_count"] == 0
        assert confirmation["excluded_late_decision_count"] == 0
        assert confirmation["decision_time"] == "2024-01-01T21:00:00Z"
    finally:
        engine.dispose()


def test_exact_but_non_hourly_boundary_is_not_a_valid_plan_match(tmp_path):
    engine, service, setup_snapshot, _ = _seed_hierarchy(tmp_path)
    invalid_boundary = SCENARIO_DECISION_TIME + timedelta(minutes=5)
    try:
        stored_snapshot = service.evaluate(decision_time=invalid_boundary)
        observation, created = service.record(
            stored_snapshot, recorded_at=invalid_boundary + timedelta(seconds=1)
        )
        assert created is True
        provenance = paper_plan_signal_provenance(
            _paper_plan(
                setup_snapshot,
                plan_as_of=invalid_boundary,
                recorded_at=invalid_boundary + timedelta(seconds=2),
            ),
            hierarchy_ledger=service.ledger,
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert observation.decision_time == invalid_boundary
        assert confirmation["status"] == "unavailable"
        assert confirmation["confirmed"] is False
        assert confirmation["matching_boundary"] is False
        assert "not a valid 1H candle-close" in confirmation["reason"]
    finally:
        engine.dispose()


def test_same_boundary_decision_recorded_after_plan_is_excluded(tmp_path):
    engine, service, snapshot, observation = _seed_hierarchy(
        tmp_path,
        record_at=SCENARIO_DECISION_TIME + timedelta(seconds=20),
    )
    try:
        assert observation is not None and observation.decision == "plannable"
        provenance = paper_plan_signal_provenance(
            _paper_plan(
                snapshot,
                recorded_at=SCENARIO_DECISION_TIME + timedelta(seconds=10),
            ),
            hierarchy_ledger=service.ledger,
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert confirmation["status"] == "unavailable"
        assert confirmation["confirmed"] is False
        assert confirmation["matching_boundary"] is True
        assert confirmation["available_by_plan"] is False
        assert confirmation["excluded_late_decision_count"] == 1
        assert "POST-DECISION" in confirmation["reason"]
        assert confirmation["records"] == []
        assert confirmation["excluded_late_records"][0]["observation_id"] == observation.observation_id
    finally:
        engine.dispose()


def test_incomplete_four_timeframe_window_is_never_confirmed(tmp_path):
    missing_open = EPOCH + SCENARIO_HOUR * timedelta(hours=1) + timedelta(minutes=15)
    engine, service, snapshot, observation = _seed_hierarchy(
        tmp_path,
        missing_15m_open=missing_open,
        record_at=SCENARIO_DECISION_TIME + timedelta(seconds=1),
    )
    try:
        assert snapshot.status == "incomplete"
        assert observation is not None and observation.status == "incomplete"
        provenance = paper_plan_signal_provenance(
            _paper_plan(snapshot), hierarchy_ledger=service.ledger
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert confirmation["status"] == "incomplete"
        assert confirmation["confirmed"] is False
        assert confirmation["available_by_plan"] is True
        assert confirmation["matching_boundary"] is True
        assert confirmation["evidence_complete"] is False
    finally:
        engine.dispose()


def test_stale_hierarchy_data_is_never_reported_as_confirmation(tmp_path):
    engine, service, setup_snapshot, _ = _seed_hierarchy(tmp_path)
    stale_boundary = SCENARIO_DECISION_TIME + timedelta(hours=1)
    try:
        stale_snapshot = service.evaluate(decision_time=stale_boundary)
        observation, created = service.record(
            stale_snapshot, recorded_at=stale_boundary + timedelta(seconds=1)
        )
        assert created is True
        assert stale_snapshot.status == "incomplete"
        provenance = paper_plan_signal_provenance(
            _paper_plan(
                setup_snapshot,
                plan_as_of=stale_boundary,
                recorded_at=stale_boundary + timedelta(seconds=2),
            ),
            hierarchy_ledger=service.ledger,
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert confirmation["status"] == "stale"
        assert confirmation["confirmed"] is False
        assert confirmation["matching_boundary"] is True
        assert confirmation["available_by_plan"] is True
        assert observation.observation_id == confirmation["observation_id"]
    finally:
        engine.dispose()


def test_genuine_stored_four_timeframe_decision_confirms_matching_setup(tmp_path):
    record_at = SCENARIO_DECISION_TIME + timedelta(seconds=1)
    engine, service, snapshot, observation = _seed_hierarchy(
        tmp_path, record_at=record_at
    )
    try:
        assert snapshot.status == "evaluated"
        assert snapshot.decision.value == "plannable"
        assert snapshot.confirmation.state.value == "confirming"
        assert snapshot.execution.state.value in {"armed", "triggered"}
        assert observation is not None
        plan = _paper_plan(
            snapshot,
            recorded_at=SCENARIO_DECISION_TIME + timedelta(seconds=2),
        )
        provenance = paper_plan_signal_provenance(
            plan, hierarchy_ledger=service.ledger
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert provenance["paper_candidate"]["label"] == "1H PAPER CANDIDATE"
        assert confirmation["status"] == "confirmed"
        assert confirmation["confirmed"] is True
        assert confirmation["label"] == "FOUR-TIMEFRAME CONFIRMED"
        assert confirmation["decision"] == "plannable"
        assert confirmation["matching_boundary"] is True
        assert confirmation["available_by_plan"] is True
        assert confirmation["evidence_complete"] is True
        assert confirmation["observation_id"] == observation.observation_id
        assert confirmation["recorded_at"] == "2024-01-01T22:00:01Z"
        assert [layer["timeframe"] for layer in confirmation["layers"]] == [
            "4h", "1h", "15m", "5m"
        ]
    finally:
        engine.dispose()


def test_hierarchy_for_a_different_setup_is_reported_as_conflicting(tmp_path):
    engine, service, snapshot, observation = _seed_hierarchy(
        tmp_path,
        record_at=SCENARIO_DECISION_TIME + timedelta(seconds=1),
    )
    try:
        assert observation is not None
        provenance = paper_plan_signal_provenance(
            _paper_plan(snapshot, setup_id="different-1h-setup"),
            hierarchy_ledger=service.ledger,
        )
        confirmation = provenance["four_timeframe_confirmation"]
        assert confirmation["status"] == "conflicting"
        assert confirmation["confirmed"] is False
        assert "different 1H setup" in confirmation["reason"]
    finally:
        engine.dispose()
