"""Offline deterministic Step 7 persistence, service and migration tests.

Everything runs against temporary SQLite databases created by the real Alembic
migrations and synthetic candles; nothing contacts a network, an exchange, or
project-persistent data. Step 5/6 fixtures come from the existing Step 5 and
Step 6 test modules so the journal is exercised against real upstream objects.
"""

import json
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL
from sqlalchemy.exc import IntegrityError
from test_setup_qualification import (
    at,
    breakout,
    candidate,
    frame,
    result,
)
from test_trade_planning import qualified

from trading_assistant.database import create_database_engine
from trading_assistant.journaling import (
    DECISION_RULES_VERSION,
    JOURNAL_RULES_VERSION,
    MAX_NOTE_LENGTH,
    DecisionState,
    JournalConflict,
    JournalError,
    JournalNotFound,
    JournalRepository,
    JournalService,
    OutcomeStatus,
    ProposedPlanLevels,
    canonical_json,
    fingerprint,
    normalize_note,
    observe_outcome,
    snapshot_identity,
)
from trading_assistant.journaling.models import (
    JournalDecisionRow,
    JournalOutcomeEventRow,
    JournalOutcomeRow,
    JournalRecordRow,
)
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.setup_qualification import SetupState
from trading_assistant.trade_planning import (
    PlanningParameters,
    PlanState,
    TradePlanResult,
    plan_trade,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JOURNAL_TABLES = {
    "journal_records",
    "journal_decisions",
    "journal_outcomes",
    "journal_outcome_events",
}
JOURNAL_INDEXES = {
    "ix_journal_records_instrument",
    "ix_journal_records_setup_id",
    "ix_journal_records_setup_as_of",
    "ix_journal_records_setup_snapshot_id",
    "ix_journal_decisions_record_time",
    "ix_journal_decisions_decision",
    "ix_journal_decisions_setup_id",
    "ix_journal_decisions_plan_id",
    "ix_journal_outcomes_status",
    "ix_journal_outcomes_cutoff",
    "ix_journal_outcomes_plan_id",
    "ix_journal_outcomes_setup_id",
    "ix_journal_outcome_events_ordering",
}
JOURNAL_TRIGGERS = {
    f"trg_{table}_no_{operation.lower()}"
    for table in sorted(JOURNAL_TABLES)
    for operation in ("UPDATE", "DELETE")
}


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _migrated_engine(path: Path, revision: str = "head"):
    url = _database_url(path)
    command.upgrade(_alembic_config(url), revision)
    return create_database_engine(url), url


@pytest.fixture
def journal_engine(tmp_path):
    engine, _url = _migrated_engine(tmp_path / "journal.sqlite3")
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Builders and helpers
# ---------------------------------------------------------------------------


def plan_long(parameters: PlanningParameters | None = None):
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=parameters,
    )
    return snapshot, planning_frame, setup, plan


def two_target_plan():
    """The long continuation plan with two R-derived targets (115 and 118)."""

    return plan_long(PlanningParameters(r_multiple_fallbacks=(D("1.5"), D(3))))


def retarget(value, symbol: str):
    """Deep copy any Step 3-5 structure with every symbol retargeted.

    Mirrors the Step 6 test ``relabel`` helper, except symbol strings on
    containers (not only candles) are retargeted too, so a complete non-BTC
    instrument can be planned and journaled end to end.
    """

    if is_dataclass(value) and not isinstance(value, type):
        updated = (
            replace(value, symbol=symbol)
            if any(field.name == "symbol" for field in fields(value))
            else value
        )
        return replace(
            updated,
            **{
                field.name: retarget(getattr(updated, field.name), symbol)
                for field in fields(updated)
            },
        )
    if isinstance(value, tuple):
        return tuple(retarget(item, symbol) for item in value)
    return value


def candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    symbol: str = SYMBOL,
    exchange: str = EXCHANGE,
) -> Candle:
    return Candle(
        exchange=exchange,
        symbol=symbol,
        timeframe="1h",
        timestamp=EPOCH + INTERVAL * index,
        open=D(open_),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(10),
    )


def store(engine, candles) -> None:
    CandleRepository(engine).insert_unchanged_or_new(candles)


def count_rows(engine, model) -> int:
    with engine.connect() as connection:
        return connection.execute(
            sa.select(sa.func.count()).select_from(model)
        ).scalar_one()


def long_outcome_candles(symbol: str = SYMBOL, *, through: int = 8):
    """Entry at 112 touched, then target 116 reached at index 8."""

    return (
        candle(
            7,
            open_="112",
            high="113",
            low="111",
            close="112",
            symbol=symbol,
        ),
        candle(
            8,
            open_="112",
            high="117",
            low="112",
            close="116",
            symbol=symbol,
        ),
    )[: through - 6]


def journaled_long_plan(engine, *, decisions=(DecisionState.ACCEPTED,)):
    snapshot, planning_frame, setup, plan = plan_long()
    service = JournalService(engine)
    record = service.journal_plan(snapshot=snapshot, plan=plan)
    for index, decision in enumerate(decisions):
        service.record_decision(
            journal_id=record.journal_id,
            decision=decision,
            decided_at=at(7 + index),
            reason=None,
        )
    return service, record, snapshot, planning_frame, setup, plan


# ---------------------------------------------------------------------------
# Journaling Step 5 / Step 6 output
# ---------------------------------------------------------------------------


def test_journal_qualified_setup_preserves_the_exact_snapshot(journal_engine):
    snapshot, planning_frame, setup = qualified()
    service = JournalService(journal_engine)
    record = service.journal_setup(snapshot=snapshot, setup_id=setup.id)

    assert record.record_kind.value == "setup"
    assert record.journal_rules_version == JOURNAL_RULES_VERSION
    assert record.setup_id == setup.id
    assert record.setup_family is setup.family
    assert record.setup_direction == setup.direction
    assert record.setup_state is setup.state
    assert record.setup_created_at == setup.created_at
    assert record.setup_as_of == setup.as_of
    assert record.setup_seed_event_id == setup.seed_event_id
    assert record.setup_reference_id == setup.reference_id
    assert record.setup_config_fingerprint == snapshot.config_fingerprint
    assert record.setup_rules_version == snapshot.rules_version
    assert (record.exchange, record.symbol, record.timeframe) == (
        snapshot.exchange,
        snapshot.symbol,
        snapshot.timeframe,
    )
    assert record.source_timeframes == snapshot.source_timeframes
    assert record.setup_snapshot_id == snapshot_identity(snapshot)
    assert record.setup_snapshot_json == canonical_json(snapshot.to_json_dict())
    assert record.setup_snapshot_payload == snapshot.to_json_dict()
    assert not record.has_plan
    assert record.plan_id is None and record.plan_json is None

    # Journaling records; it never decides.
    assert service.latest_decision(journal_id=record.journal_id) is None
    assert count_rows(journal_engine, JournalDecisionRow) == 0
    assert count_rows(journal_engine, JournalRecordRow) == 1
    assert planning_frame.patterns.symbol == snapshot.symbol


def test_journal_snapshot_records_audit_observations_without_fake_trades(
    journal_engine,
):
    quiet = result([frame(6, ())])
    assert quiet.state.value == "NO_SETUP" and not quiet.setups
    service = JournalService(journal_engine)
    record = service.journal_snapshot(snapshot=quiet)

    assert record.record_kind.value == "snapshot"
    assert record.setup_id is None
    assert record.setup_family is None and record.setup_direction is None
    assert record.setup_state is quiet.state
    assert record.plan_id is None
    assert record.setup_snapshot_payload == quiet.to_json_dict()

    decision = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.SKIPPED,
        decided_at=at(7),
        reason="no actionable setup",
    )
    assert decision.record_kind.value == "snapshot"
    assert decision.setup_id is None and decision.plan_id is None
    assert decision.setup_family is None and decision.direction is None
    assert decision.setup_as_of == quiet.as_of
    assert decision.setup_snapshot_id == record.setup_snapshot_id


def test_a_watch_setup_is_journaled_for_audit_but_never_observed(journal_engine):
    """Watch-only evidence is recorded for replay, without inventing a trade."""

    seed = breakout()
    snapshot = result([frame(6, (seed,))])
    setup = candidate(snapshot, seed)
    assert setup.state is SetupState.WATCH
    service = JournalService(journal_engine)

    record = service.journal_setup(snapshot=snapshot, setup_id=setup.id)
    assert record.setup_state is SetupState.WATCH
    assert record.plan_id is None and record.plan_payload is None
    assert record.setup_snapshot_payload == snapshot.to_json_dict()

    decision = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.SKIPPED,
        decided_at=at(7),
        reason="not confirmed yet",
    )
    assert (decision.setup_state, decision.plan_id) == (SetupState.WATCH, None)
    assert decision.setup_id == setup.id

    # No plan exists, so no outcome can be observed and no trade is implied.
    with pytest.raises(ValueError, match="no Step 6 plan"):
        service.observe_outcome(journal_id=record.journal_id, observed_through=at(8))
    assert service.outcome_history(journal_id=record.journal_id) == ()


def test_records_are_traceable_by_snapshot_identity(journal_engine):
    """Records carry the content identity of the snapshot they were built from."""

    snapshot, _planning_frame, setup = qualified()
    service = JournalService(journal_engine)
    snapshot_record = service.journal_snapshot(snapshot=snapshot)
    setup_record = service.journal_setup(snapshot=snapshot, setup_id=setup.id)
    identity = snapshot_identity(snapshot)
    assert snapshot_record.setup_snapshot_id == identity
    assert setup_record.setup_snapshot_id == identity

    related = service.records_for_snapshot(snapshot_id=identity)
    assert [record.journal_id for record in related] == sorted(
        [snapshot_record.journal_id, setup_record.journal_id]
    )
    assert service.records_for_snapshot(snapshot_id=snapshot) == related
    assert service.records_for_snapshot(snapshot_id="0" * 64) == ()
    with pytest.raises(ValueError, match="snapshot_id"):
        service.records_for_snapshot(snapshot_id="   ")


def test_journal_plannable_plan_preserves_the_exact_plan(journal_engine):
    _service, record, snapshot, _planning_frame, setup, plan = journaled_long_plan(
        journal_engine, decisions=()
    )
    assert plan.state is PlanState.PLANNABLE
    assert record.plan_id == plan.id
    assert record.plan_state is PlanState.PLANNABLE
    assert record.planning_as_of == plan.as_of == snapshot.as_of
    assert record.plan_json == canonical_json(plan.to_json_dict())
    assert record.plan_payload == plan.to_json_dict()
    assert record.plan_config_fingerprint == plan.config_fingerprint
    assert record.planning_rules_version == plan.planning_rules_version
    assert record.setup_snapshot_id == snapshot_identity(snapshot)
    assert record.setup_id == setup.id


def test_journal_plan_records_no_plan_refusals_too(journal_engine):
    seed = breakout()
    watch_frames = [frame(6, (seed,), close=112)]
    watch = result(watch_frames)
    setup = candidate(watch, seed)
    plan = plan_trade(snapshot=watch, frame=watch_frames[-1], setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN

    service = JournalService(journal_engine)
    record = service.journal_plan(snapshot=watch, plan=plan)
    assert record.plan_state is PlanState.NO_PLAN
    assert record.plan_payload is not None and record.plan_payload["reasons"]
    with pytest.raises(ValueError, match="PLANNABLE"):
        service.observe_outcome(journal_id=record.journal_id, observed_through=at(7))


def test_journaling_is_idempotent_and_creates_no_duplicate_history(journal_engine):
    snapshot, _planning_frame, setup, plan = plan_long()
    service = JournalService(journal_engine)

    first = service.journal_plan(snapshot=snapshot, plan=plan)
    again = service.journal_plan(snapshot=snapshot, plan=plan)
    direct = service.journal_setup(snapshot=snapshot, setup_id=setup.id, plan=plan)
    snapshot_record = service.journal_snapshot(snapshot=snapshot)
    snapshot_again = service.journal_snapshot(snapshot=snapshot)

    assert first.journal_id == again.journal_id == direct.journal_id
    assert snapshot_record.journal_id == snapshot_again.journal_id
    assert first.journal_id != snapshot_record.journal_id
    assert count_rows(journal_engine, JournalRecordRow) == 2
    assert service.records_for_setup(setup_id=setup.id) == (first,)
    assert service.get_record(journal_id=first.journal_id) == first


def test_conflicting_content_under_the_same_identity_is_refused(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    repository = JournalRepository(journal_engine)
    tampered = replace(record, symbol="ETH/USD")
    with pytest.raises(JournalConflict, match="never overwritten"):
        repository.insert_record(tampered)
    assert service.get_record(journal_id=record.journal_id).symbol == SYMBOL


def test_journal_requires_a_setup_that_the_snapshot_actually_contains(journal_engine):
    snapshot, _planning_frame, _setup, plan = plan_long()
    service = JournalService(journal_engine)
    with pytest.raises(ValueError, match="is not present in the snapshot"):
        service.journal_setup(snapshot=snapshot, setup_id="not-a-real-setup")

    _other_snapshot, _other_frame, _other_setup, _ = plan_long()
    mismatched = replace(plan, symbol="ETH/USD")
    with pytest.raises(ValueError, match="does not match the snapshot"):
        service.journal_plan(snapshot=snapshot, plan=mismatched)
    with pytest.raises(ValueError, match="coherent system moment"):
        service.journal_plan(
            snapshot=snapshot, plan=replace(plan, as_of=plan.as_of + INTERVAL)
        )
    with pytest.raises(ValueError, match="different Step 5 configuration"):
        service.journal_plan(
            snapshot=snapshot, plan=replace(plan, setup_config_fingerprint="other")
        )


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_no_decision_defaults_to_accepted(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    assert service.latest_decision(journal_id=record.journal_id) is None
    assert service.decision_history(journal_id=record.journal_id) == ()
    assert count_rows(journal_engine, JournalDecisionRow) == 0

    with pytest.raises(TypeError):
        service.record_decision(journal_id=record.journal_id)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="PENDING"):
        service.record_decision(
            journal_id=record.journal_id, decision="MAYBE", decided_at=at(7)
        )
    with pytest.raises(TypeError, match="DecisionState"):
        service.record_decision(
            journal_id=record.journal_id, decision=42, decided_at=at(7)
        )
    # A rejected attempt appends nothing.
    assert service.latest_decision(journal_id=record.journal_id) is None


@pytest.mark.parametrize("state", list(DecisionState))
def test_each_explicit_decision_state_is_stored_with_full_traceability(
    journal_engine, state
):
    service, record, snapshot, _planning_frame, setup, plan = journaled_long_plan(
        journal_engine, decisions=()
    )
    decision = service.record_decision(
        journal_id=record.journal_id,
        decision=state,
        decided_at=at(8),
        reason="  explicit human judgement  ",
    )
    assert decision.decision is state
    assert decision.sequence == 1
    assert decision.supersedes_decision_id is None
    assert decision.decided_at == at(8)
    assert decision.reason == "explicit human judgement"
    assert decision.decision_rules_version == DECISION_RULES_VERSION
    assert decision.journal_id == record.journal_id
    assert decision.record_kind is record.record_kind
    assert decision.setup_id == setup.id
    assert decision.plan_id == plan.id
    assert decision.setup_family is setup.family
    assert decision.direction == setup.direction
    assert decision.setup_state is setup.state
    assert (decision.exchange, decision.symbol, decision.timeframe) == (
        snapshot.exchange,
        snapshot.symbol,
        snapshot.timeframe,
    )
    assert decision.source_timeframes == snapshot.source_timeframes
    assert decision.setup_as_of == setup.as_of
    assert decision.planning_as_of == plan.as_of
    assert decision.setup_config_fingerprint == snapshot.config_fingerprint
    assert decision.plan_config_fingerprint == plan.config_fingerprint
    assert decision.setup_rules_version == snapshot.rules_version
    assert decision.planning_rules_version == plan.planning_rules_version
    assert decision.setup_snapshot_id == record.setup_snapshot_id
    assert service.latest_decision(journal_id=record.journal_id) == decision
    assert count_rows(journal_engine, JournalDecisionRow) == 1


def test_decisions_are_append_only_with_a_complete_correction_trail(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    accepted = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.ACCEPTED,
        decided_at=at(7),
        reason="first judgement",
    )
    rejected = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.REJECTED,
        decided_at=at(8),
        reason="corrected: evidence flipped",
    )
    corrected = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.ACCEPTED,
        decided_at=at(9),
        reason="final decision",
    )

    history = service.decision_history(journal_id=record.journal_id)
    assert [item.decision for item in history] == [
        DecisionState.ACCEPTED,
        DecisionState.REJECTED,
        DecisionState.ACCEPTED,
    ]
    assert [item.sequence for item in history] == [1, 2, 3]
    assert history[0].supersedes_decision_id is None
    assert history[1].supersedes_decision_id == accepted.decision_id
    assert history[2].supersedes_decision_id == rejected.decision_id
    assert service.latest_decision(journal_id=record.journal_id) == corrected
    assert service.get_decision(decision_id=accepted.decision_id) == accepted

    # The original row is physically unchanged in the database.
    with journal_engine.connect() as connection:
        stored = connection.execute(
            sa.select(
                JournalDecisionRow.decision,
                JournalDecisionRow.reason,
                JournalDecisionRow.decided_at,
            ).where(JournalDecisionRow.decision_id == accepted.decision_id)
        ).one()
    assert stored.decision == "ACCEPTED"
    assert stored.reason == "first judgement"
    assert stored.decided_at == at(7)


def test_recording_an_identical_decision_again_is_a_verified_no_op(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    first = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.PENDING,
        decided_at=at(7),
        reason="awaiting review",
    )
    second = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.PENDING,
        decided_at=at(7),
        reason="awaiting review",
    )
    assert first == second
    assert service.decision_history(journal_id=record.journal_id) == (first,)
    assert count_rows(journal_engine, JournalDecisionRow) == 1

    # A different instant or reason is a genuinely new historical decision.
    third = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.PENDING,
        decided_at=at(8),
        reason="awaiting review",
    )
    assert third.sequence == 2
    assert third.supersedes_decision_id == first.decision_id


def test_decisions_require_a_journaled_record(journal_engine):
    service = JournalService(journal_engine)
    with pytest.raises(JournalNotFound, match="is not stored"):
        service.record_decision(
            journal_id="missing", decision=DecisionState.PENDING, decided_at=at(7)
        )
    with pytest.raises(JournalNotFound, match="is not stored"):
        service.latest_decision(journal_id="missing")
    with pytest.raises(JournalNotFound, match="is not stored"):
        service.get_decision(decision_id="missing")


def test_decision_timestamp_defaults_to_current_utc(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    before = datetime.now(UTC)
    decision = service.record_decision(
        journal_id=record.journal_id, decision=DecisionState.PENDING
    )
    after = datetime.now(UTC)
    assert decision.decided_at.tzinfo is not None
    assert before <= decision.decided_at <= after


def test_notes_are_bounded_metadata_that_cannot_alter_outcomes(journal_engine):
    service, record, _snapshot, _planning_frame, _setup, _plan = journaled_long_plan(
        journal_engine, decisions=()
    )
    store(journal_engine, long_outcome_candles())
    before_note = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    decision = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.ACCEPTED,
        decided_at=at(8),
        reason="target 116 definitely reached, 999 profit",
    )
    after_note = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    assert decision.reason == "target 116 definitely reached, 999 profit"
    assert after_note == before_note  # notes never touch deterministic outcomes

    with pytest.raises(ValueError, match=str(MAX_NOTE_LENGTH)):
        service.record_decision(
            journal_id=record.journal_id,
            decision=DecisionState.ACCEPTED,
            decided_at=at(9),
            reason="x" * (MAX_NOTE_LENGTH + 1),
        )
    with pytest.raises(ValueError, match="NUL"):
        service.record_decision(
            journal_id=record.journal_id,
            decision=DecisionState.ACCEPTED,
            decided_at=at(9),
            reason="bad\x00note",
        )
    with pytest.raises(TypeError, match="string"):
        service.record_decision(
            journal_id=record.journal_id,
            decision=DecisionState.ACCEPTED,
            decided_at=at(9),
            reason=5,  # type: ignore[arg-type]
        )
    assert normalize_note("   ") is None


# ---------------------------------------------------------------------------
# Outcome observations
# ---------------------------------------------------------------------------


def test_accepted_plan_outcome_observation_is_stored_with_events(journal_engine):
    service, record, snapshot, _planning_frame, setup, plan = journaled_long_plan(
        journal_engine
    )
    store(journal_engine, long_outcome_candles())
    observation = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.entry_reached and observation.entry_ordered
    assert observation.targets_reached == (0,)
    assert observation.first_touch_order == (("entry",), ("target_1",))
    assert observation.plan_id == plan.id
    assert observation.setup_id == setup.id
    assert (observation.exchange, observation.symbol, observation.timeframe) == (
        snapshot.exchange,
        snapshot.symbol,
        snapshot.timeframe,
    )

    assert count_rows(journal_engine, JournalOutcomeRow) == 1
    assert count_rows(journal_engine, JournalOutcomeEventRow) == 2
    versions = service.outcome_history(journal_id=record.journal_id)
    assert len(versions) == 1
    assert versions[0].sequence == 1
    assert versions[0].supersedes_outcome_id is None
    assert versions[0].observation == observation
    assert service.latest_outcome(journal_id=record.journal_id) == observation

    with journal_engine.connect() as connection:
        row = connection.execute(
            sa.select(JournalOutcomeRow).where(
                JournalOutcomeRow.outcome_id == observation.id
            )
        ).one()
        events = connection.execute(
            sa.select(JournalOutcomeEventRow)
            .where(JournalOutcomeEventRow.outcome_id == observation.id)
            .order_by(JournalOutcomeEventRow.sequence)
        ).all()
    assert row.status == "TARGETS_REACHED"
    assert row.entry_reached == 1 and row.entry_ordered == 1
    assert json.loads(row.targets_reached_json) == [0]
    assert json.loads(row.first_touch_order_json) == [["entry"], ["target_1"]]
    assert json.loads(row.target_levels_json) == ["116"]
    assert row.observed_through == at(8)
    assert [(event.kind, event.target_index, event.ordering) for event in events] == [
        ("entry", -1, "ordered"),
        ("target", 0, "ordered"),
    ]


@pytest.mark.parametrize(
    "decision",
    [
        None,
        DecisionState.PENDING,
        DecisionState.ACCEPTED,
        DecisionState.REJECTED,
        DecisionState.SKIPPED,
    ],
)
def test_decisions_never_change_deterministic_outcomes(journal_engine, decision):
    """Rejected and skipped plans are observable exactly like accepted ones."""

    service, record, *_rest = journaled_long_plan(journal_engine, decisions=())
    if decision is not None:
        service.record_decision(
            journal_id=record.journal_id, decision=decision, decided_at=at(7)
        )
    store(journal_engine, long_outcome_candles())
    observation = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    assert observation.status is OutcomeStatus.TARGETS_REACHED
    assert observation.targets_reached == (0,)
    assert service.latest_outcome(journal_id=record.journal_id) == observation


def test_outcome_observation_is_idempotent(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine)
    store(journal_engine, long_outcome_candles())
    first = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    second = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    assert first == second and first.id == second.id
    assert count_rows(journal_engine, JournalOutcomeRow) == 1
    assert count_rows(journal_engine, JournalOutcomeEventRow) == 2


def test_t1_observation_stays_recoverable_after_a_later_t2_version(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine)
    store(journal_engine, long_outcome_candles())
    t1 = service.observe_outcome(journal_id=record.journal_id, observed_through=at(7))
    assert t1.status is OutcomeStatus.OPEN_AT_CUTOFF
    t2 = service.observe_outcome(journal_id=record.journal_id, observed_through=at(8))
    assert t2.status is OutcomeStatus.TARGETS_REACHED
    assert t1.id != t2.id

    versions = service.outcome_history(journal_id=record.journal_id)
    assert [version.sequence for version in versions] == [1, 2]
    assert versions[0].observation == t1
    assert versions[0].supersedes_outcome_id is None
    assert versions[1].observation == t2
    assert versions[1].supersedes_outcome_id == t1.id
    # The historical T1 row is unchanged and still readable by identity.
    recovered = service.get_outcome(outcome_id=t1.id)
    assert recovered == t1
    assert recovered.observed_through == at(7)
    assert recovered.targets_reached == ()
    # Replaying the T1 cutoff reproduces T1 rather than appending a duplicate.
    assert (
        service.observe_outcome(journal_id=record.journal_id, observed_through=at(7))
        == t1
    )
    assert count_rows(journal_engine, JournalOutcomeRow) == 2
    assert service.latest_outcome(journal_id=record.journal_id) == t2


def test_observation_requires_a_plannable_plan(journal_engine):
    snapshot, _planning_frame, setup = qualified()
    service = JournalService(journal_engine)
    setup_record = service.journal_setup(snapshot=snapshot, setup_id=setup.id)
    with pytest.raises(ValueError, match="no Step 6 plan"):
        service.observe_outcome(
            journal_id=setup_record.journal_id, observed_through=at(8)
        )
    with pytest.raises(JournalNotFound):
        service.observe_outcome(journal_id="missing", observed_through=at(8))


def test_a_malformed_stored_plan_is_refused_not_observed(journal_engine):
    """A record written by other code cannot be observed against silently."""

    service, record, *_rest = journaled_long_plan(journal_engine)
    repository = JournalRepository(journal_engine)
    tampered = replace(
        record,
        journal_id="f" * 64,
        plan_json='{"state": "NO_PLAN", "id": "plan-1"}',
    )
    repository.insert_record(tampered)

    with pytest.raises(JournalError, match="stored plan payload for f+ is malformed"):
        service.observe_outcome(journal_id=tampered.journal_id, observed_through=at(8))
    assert repository.outcome_versions(tampered.journal_id) == ()


def test_future_candles_cannot_change_a_stored_historical_observation(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine)
    store(journal_engine, long_outcome_candles()[:1])  # entry candle only
    t1 = service.observe_outcome(journal_id=record.journal_id, observed_through=at(7))
    assert t1.status is OutcomeStatus.OPEN_AT_CUTOFF

    store(journal_engine, long_outcome_candles()[1:])  # later candle completes it
    replayed = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(7)
    )
    assert replayed == t1
    assert service.get_outcome(outcome_id=t1.id) == t1
    assert service.latest_outcome(journal_id=record.journal_id) == t1
    later = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    assert later.status is OutcomeStatus.TARGETS_REACHED
    assert later.id != t1.id


def test_backfilled_candle_appends_a_new_version_instead_of_rewriting_history(
    journal_engine,
):
    service, record, *_rest = journaled_long_plan(journal_engine)
    # A hole at index 8 means the trajectory is unknown at the cutoff.
    store(
        journal_engine,
        (
            long_outcome_candles()[0],
            candle(9, open_="112", high="117", low="112", close="116"),
        ),
    )
    unknown = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(9)
    )
    assert unknown.status is OutcomeStatus.INCOMPLETE_DATA
    assert unknown.missing_candle_count == 1

    # Backfilling the missing candle creates a new appended version; the
    # earlier UNKNOWN row is preserved byte-for-byte.
    store(journal_engine, (long_outcome_candles()[1],))
    resolved = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(9)
    )
    assert resolved.status is OutcomeStatus.TARGETS_REACHED
    versions = service.outcome_history(journal_id=record.journal_id)
    assert [version.sequence for version in versions] == [1, 2]
    assert versions[0].observation == unknown
    assert versions[1].supersedes_outcome_id == unknown.id
    assert (
        service.get_outcome(outcome_id=unknown.id).status
        is OutcomeStatus.INCOMPLETE_DATA
    )


def test_persistence_survives_a_new_engine_and_service(tmp_path):
    database_path = tmp_path / "restart.sqlite3"
    engine, url = _migrated_engine(database_path)
    service, record, _snapshot, _planning_frame, _setup, _plan = journaled_long_plan(
        engine
    )
    store(engine, long_outcome_candles())
    decision = service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.ACCEPTED,
        decided_at=at(8),
        reason="survives restarts",
    )
    observation = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    engine.dispose()

    reopened = create_database_engine(url)
    fresh = JournalService(reopened)
    assert fresh.get_record(journal_id=record.journal_id) == record
    assert fresh.latest_decision(journal_id=record.journal_id) == decision
    assert fresh.latest_outcome(journal_id=record.journal_id) == observation
    assert fresh.get_outcome(outcome_id=observation.id) == observation
    reopened.dispose()


def test_observation_is_symbol_generic(journal_engine):
    service = JournalService(journal_engine)
    observations = {}
    for symbol in ("ETH/USD", "SOL/USDC"):
        snapshot, planning_frame, setup = qualified()
        relabeled_snapshot = retarget(snapshot, symbol)
        relabeled_frame = retarget(planning_frame, symbol)
        plan = plan_trade(
            snapshot=relabeled_snapshot,
            frame=relabeled_frame,
            setup_id=setup.id,
        )
        assert plan.state is PlanState.PLANNABLE
        assert plan.symbol == symbol
        record = service.journal_plan(snapshot=relabeled_snapshot, plan=plan)
        assert record.symbol == symbol
        store(
            journal_engine,
            (
                candle(
                    7, open_="112", high="113", low="111", close="112", symbol=symbol
                ),
                candle(
                    8, open_="112", high="117", low="112", close="116", symbol=symbol
                ),
            ),
        )
        observation = service.observe_outcome(
            journal_id=record.journal_id,
            observed_through=at(8),
        )
        assert observation.symbol == symbol
        assert observation.status in (
            OutcomeStatus.TARGETS_REACHED,
            OutcomeStatus.OPEN_AT_CUTOFF,
        )
        observations[symbol] = observation

    # The same numbers on different instruments are different observations.
    assert observations["ETH/USD"].id != observations["SOL/USDC"].id


# ---------------------------------------------------------------------------
# Immutability, constraints and schema
# ---------------------------------------------------------------------------


def test_append_only_triggers_block_updates_and_deletes(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine)
    store(journal_engine, long_outcome_candles())
    observation = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )

    statements = (
        ("UPDATE journal_records SET symbol = 'ETH/USD'", "append-only"),
        ("DELETE FROM journal_records", "append-only"),
        ("UPDATE journal_decisions SET decision = 'REJECTED'", "append-only"),
        ("DELETE FROM journal_decisions", "append-only"),
        ("UPDATE journal_outcomes SET status = 'STOPPED'", "append-only"),
        ("DELETE FROM journal_outcomes", "append-only"),
        ("UPDATE journal_outcome_events SET ordering = 'ambiguous'", "append-only"),
        ("DELETE FROM journal_outcome_events", "append-only"),
    )
    for statement, message in statements:
        with (
            pytest.raises(IntegrityError, match=message),
            journal_engine.begin() as connection,
        ):
            connection.execute(sa.text(statement))

    assert service.get_record(journal_id=record.journal_id).symbol == SYMBOL
    assert service.get_outcome(outcome_id=observation.id) == observation


def test_database_constraints_reject_invalid_rows(journal_engine):
    service, record, *_rest = journaled_long_plan(journal_engine)
    store(journal_engine, long_outcome_candles())
    observation = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )

    def decision_values(**overrides):
        # Start from a real stored decision row so every column is covered and a
        # newly added NOT NULL column cannot make these cases fail for the wrong
        # reason.
        with journal_engine.connect() as connection:
            row = connection.execute(
                sa.select(JournalDecisionRow)
                .where(JournalDecisionRow.journal_id == record.journal_id)
                .order_by(JournalDecisionRow.sequence)
            ).first()
        values = {
            column.name: getattr(row, column.name)
            for column in JournalDecisionRow.__table__.columns
        }
        values["decision_id"] = "d" * 64
        values.update(overrides)
        return values

    def outcome_values(**overrides):
        with journal_engine.connect() as connection:
            row = connection.execute(
                sa.select(JournalOutcomeRow).where(
                    JournalOutcomeRow.outcome_id == observation.id
                )
            ).one()
        values = {
            column.name: getattr(row, column.name)
            for column in JournalOutcomeRow.__table__.columns
        }
        values["outcome_id"] = "o" * 64
        values["sequence"] = 2
        values["supersedes_outcome_id"] = None
        values.update(overrides)
        return values

    cases = (
        decision_values(decision="WIN"),
        decision_values(sequence=0),
        decision_values(journal_id="missing"),
        decision_values(supersedes_decision_id="d" * 64),
        decision_values(sequence=1),
    )
    for values in cases:
        with pytest.raises(IntegrityError), journal_engine.begin() as connection:
            connection.execute(sa.insert(JournalDecisionRow).values(**values))

    outcome_cases = (
        outcome_values(status="WIN"),
        outcome_values(ambiguous=True, ambiguity_kind=None),
        outcome_values(incomplete=True, missing_candle_count=0),
        outcome_values(entry_ordered=True, entry_reached=False),
        outcome_values(stop_pre_entry=True, stop_reached=False),
    )
    for values in outcome_cases:
        with pytest.raises(IntegrityError), journal_engine.begin() as connection:
            connection.execute(sa.insert(JournalOutcomeRow).values(**values))

    # Record-level consistency constraints.
    with journal_engine.connect() as connection:
        stored_record = connection.execute(
            sa.select(JournalRecordRow).where(
                JournalRecordRow.journal_id == record.journal_id
            )
        ).one()
    record_values = {
        column.name: getattr(stored_record, column.name)
        for column in JournalRecordRow.__table__.columns
    }
    record_cases = (
        {**record_values, "record_kind": "setup", "setup_id": None},
        {**record_values, "record_kind": "snapshot", "setup_id": None},
        {
            **record_values,
            "planning_as_of": None,
            "plan_id": record.plan_id,
            "plan_state": record.plan_state.value,
        },
        {**record_values, "record_kind": "sideways"},
    )
    for index, values in enumerate(record_cases):
        values = dict(values)
        values["journal_id"] = f"r{index}" + "0" * 63
        if index == 2:
            values["journal_id"] = record.journal_id
            values["plan_id"] = "p" * 64
        with pytest.raises(IntegrityError), journal_engine.begin() as connection:
            connection.execute(sa.insert(JournalRecordRow).values(**values))

    # Event-kind and target-index constraints.
    with pytest.raises(IntegrityError), journal_engine.begin() as connection:
        connection.execute(
            sa.insert(JournalOutcomeEventRow).values(
                event_id="x" * 64,
                outcome_id=observation.id,
                sequence=9,
                kind="entry",
                target_index=0,
                level_value=D(1),
                candle_timestamp=at(7),
                candle_index=0,
                ordering="ordered",
                co_touched=False,
            )
        )


def test_schema_has_the_expected_tables_indexes_and_triggers(journal_engine):
    inspector = sa.inspect(journal_engine)
    tables = set(inspector.get_table_names())
    assert JOURNAL_TABLES <= tables
    assert "ohlcv_candles" in tables  # the Step 2 archive is untouched
    index_names = set()
    for table in JOURNAL_TABLES:
        index_names.update(index["name"] for index in inspector.get_indexes(table))
    assert JOURNAL_INDEXES <= index_names
    with journal_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert JOURNAL_TRIGGERS <= triggers


def test_migration_is_additive_and_preserves_the_candle_archive(tmp_path):
    database_path = tmp_path / "additive.sqlite3"
    url = _database_url(database_path)
    config = _alembic_config(url)
    command.upgrade(config, "0002_ohlcv_candles")
    engine = create_database_engine(url)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO ohlcv_candles "
                "(exchange, symbol, timeframe, timestamp, open, high, low, close, volume) "
                "VALUES ('mock-exchange', 'BTC/USDT', '1h', '2024-01-01 00:00:00.000000', "
                "'100', '101', '99', '100', '5')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        revision = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        candles = connection.execute(
            sa.text("SELECT COUNT(*) FROM ohlcv_candles")
        ).scalar_one()
    assert revision == "0003_journal"
    assert candles == 1
    assert JOURNAL_TABLES <= set(sa.inspect(engine).get_table_names())

    # Downgrading with stored journal history refuses to destroy it.
    service = JournalService(engine)
    snapshot, _planning_frame, _setup, plan = plan_long()
    service.journal_plan(snapshot=snapshot, plan=plan)
    with pytest.raises(RuntimeError, match="Refusing to downgrade"):
        command.downgrade(config, "0002_ohlcv_candles")
    with engine.connect() as connection:
        assert (
            connection.execute(
                sa.text("SELECT COUNT(*) FROM ohlcv_candles")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            == "0003_journal"
        )
    engine.dispose()

    # With no journal rows the downgrade removes only Step 7 objects.
    empty_path = tmp_path / "empty.sqlite3"
    empty_url = _database_url(empty_path)
    empty_config = _alembic_config(empty_url)
    command.upgrade(empty_config, "head")
    command.downgrade(empty_config, "0002_ohlcv_candles")
    empty_engine = create_database_engine(empty_url)
    tables = set(sa.inspect(empty_engine).get_table_names())
    empty_engine.dispose()
    assert JOURNAL_TABLES.isdisjoint(tables)
    assert "ohlcv_candles" in tables


def test_journaling_never_mutates_step5_or_step6_objects(journal_engine):
    snapshot, planning_frame, setup, plan = plan_long()
    snapshot_json = snapshot.to_json_dict()
    frame_json = planning_frame.patterns.to_json_dict()
    plan_json = plan.to_json_dict()

    service = JournalService(journal_engine)
    record = service.journal_plan(snapshot=snapshot, plan=plan)
    service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.ACCEPTED,
        decided_at=at(8),
        reason="accepted",
    )
    store(journal_engine, long_outcome_candles())
    service.observe_outcome(journal_id=record.journal_id, observed_through=at(8))

    assert snapshot.to_json_dict() == snapshot_json
    assert planning_frame.patterns.to_json_dict() == frame_json
    assert plan.to_json_dict() == plan_json
    assert len(snapshot.setups) == 1
    assert snapshot.setups[0].state.value == "QUALIFIED"
    with pytest.raises(FrozenInstanceError):
        plan.state = PlanState.NO_PLAN
    with pytest.raises(FrozenInstanceError):
        setup.state = setup.state
    assert isinstance(plan, TradePlanResult)


def test_pure_observation_and_service_agree(journal_engine):
    """The service is a thin adapter: same plan, candles and cutoff agree."""

    service, record, snapshot, _planning_frame, _setup, plan = journaled_long_plan(
        journal_engine
    )
    store(journal_engine, long_outcome_candles())
    stored = service.observe_outcome(
        journal_id=record.journal_id, observed_through=at(8)
    )
    pure = observe_outcome(
        journal_id=record.journal_id,
        levels=ProposedPlanLevels.from_plan(plan),
        candles=CandleRepository(journal_engine)
        .get_candles(
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            start_time=plan.as_of,
            end_time=at(8),
        )
        .candles,
        observed_through=at(8),
    )
    assert pure == stored
    assert pure.id == stored.id


def test_fingerprint_and_canonical_json_are_stable():
    assert fingerprint("x", {"a": 1}) == fingerprint("x", {"a": 1})
    assert fingerprint("x", {"a": 1}) != fingerprint("y", {"a": 1})
    assert len(fingerprint("x")) == 64
    assert canonical_json({"b": D("1.50"), "a": at(0)}) == (
        '{"a":"2024-01-01T00:00:00Z","b":"1.50"}'
    )


def test_an_unmigrated_database_is_never_implicitly_created(tmp_path):
    """Schema stays explicit: the journal never creates tables on its own."""

    engine = create_database_engine(_database_url(tmp_path / "unmigrated.sqlite3"))
    service = JournalService(engine)
    with pytest.raises(sa.exc.OperationalError):
        service.get_record(journal_id="anything")
    assert sa.inspect(engine).get_table_names() == []
    engine.dispose()
