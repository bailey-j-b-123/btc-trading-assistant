"""Journal service: the read/write surface of Step 7.

The service is the only public way to journal Step 5/6 output and human
decisions. It never replays qualification, never re-plans, and never mutates a
Step 5 or Step 6 object: it stores canonical JSON projections of exactly what it
was given and computes deterministic outcome observations from the immutable
plan projection plus stored candles.

Terminology is deliberate. A journal record is a historical system proposal, a
decision is a human decision about that proposal, and an outcome observation is
a deterministic statement about market prices relative to the proposed levels.
None of them is an order, fill, position, balance, or profit claim.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from trading_assistant.journaling.errors import JournalError
from trading_assistant.journaling.observation import observe_outcome
from trading_assistant.journaling.parameters import (
    DECISION_RULES_VERSION,
    JOURNAL_RULES_VERSION,
    OutcomeParameters,
    canonical_json,
    fingerprint,
    normalize_note,
)
from trading_assistant.journaling.repository import JournalRepository
from trading_assistant.journaling.types import (
    DecisionRecord,
    DecisionState,
    JournalRecord,
    OutcomeObservation,
    OutcomeVersion,
    ProposedPlanLevels,
    RecordKind,
    record_identity_material,
)
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.setup_qualification.models import (
    QualificationSnapshot,
    SetupResult,
)
from trading_assistant.trade_planning.models import PlanState, TradePlanResult


def snapshot_identity(snapshot: QualificationSnapshot) -> str:
    """Deterministic identity of the exact Step 5 snapshot content.

    The journal's own version-prefixed fingerprint of the snapshot projection;
    two byte-identical snapshots always produce the same identity and any
    difference (evidence, rules, configuration) produces a different one.
    """

    if not isinstance(snapshot, QualificationSnapshot):
        raise TypeError("snapshot must be a Step 5 QualificationSnapshot")
    return fingerprint("setup-snapshot", snapshot.to_json_dict())


class JournalService:
    """Durable, append-only journaling of proposals, decisions and outcomes."""

    def __init__(self, engine: Engine) -> None:
        self.repository = JournalRepository(engine)
        self.candles = CandleRepository(engine)

    # ------------------------------------------------------------------
    # Journaling Step 5 / Step 6 output
    # ------------------------------------------------------------------

    def journal_snapshot(self, *, snapshot: QualificationSnapshot) -> JournalRecord:
        """Journal one whole Step 5 snapshot for audit/replay.

        A snapshot record carries the aggregate state (NO_SETUP, WATCH or
        QUALIFIED) and the full projection of every setup it contains. It never
        carries a plan, and no trade is manufactured from it.
        """

        if not isinstance(snapshot, QualificationSnapshot):
            raise TypeError("snapshot must be a Step 5 QualificationSnapshot")
        identity = snapshot_identity(snapshot)
        journal_id = fingerprint(
            "journal-record",
            record_identity_material(
                record_kind=RecordKind.SNAPSHOT,
                setup_snapshot_id=identity,
                setup_id=None,
                plan_id=None,
            ),
        )
        record = JournalRecord(
            journal_id=journal_id,
            record_kind=RecordKind.SNAPSHOT,
            journal_rules_version=JOURNAL_RULES_VERSION,
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            source_timeframes=snapshot.source_timeframes,
            setup_id=None,
            setup_family=None,
            setup_direction=None,
            setup_state=snapshot.state,
            setup_created_at=None,
            setup_as_of=snapshot.as_of,
            setup_seed_event_id=None,
            setup_reference_id=None,
            setup_config_fingerprint=snapshot.config_fingerprint,
            setup_rules_version=snapshot.rules_version,
            setup_snapshot_id=identity,
            setup_snapshot_json=canonical_json(snapshot.to_json_dict()),
            plan_id=None,
            plan_state=None,
            planning_as_of=None,
            plan_json=None,
            plan_config_fingerprint=None,
            planning_rules_version=None,
        )
        return self.repository.insert_record(record)

    def journal_setup(
        self,
        *,
        snapshot: QualificationSnapshot,
        setup_id: str,
        plan: TradePlanResult | None = None,
    ) -> JournalRecord:
        """Journal one Step 5 setup candidate, optionally with its Step 6 result.

        Any setup state may be journaled (QUALIFIED, WATCH, or a terminated
        NO_SETUP candidate) and any plan state may be attached (PLANNABLE,
        NO_PLAN or INVALID) so refusals are recorded too. Nothing is inferred
        from the setup state: journaling never decides anything.
        """

        setup = self._require_setup(snapshot=snapshot, setup_id=setup_id)
        if plan is not None:
            _require_matching_plan(snapshot=snapshot, setup=setup, plan=plan)
        identity = snapshot_identity(snapshot)
        journal_id = fingerprint(
            "journal-record",
            record_identity_material(
                record_kind=RecordKind.SETUP,
                setup_snapshot_id=identity,
                setup_id=setup.id,
                plan_id=None if plan is None else plan.id,
            ),
        )
        record = JournalRecord(
            journal_id=journal_id,
            record_kind=RecordKind.SETUP,
            journal_rules_version=JOURNAL_RULES_VERSION,
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            timeframe=snapshot.timeframe,
            source_timeframes=snapshot.source_timeframes,
            setup_id=setup.id,
            setup_family=setup.family,
            setup_direction=setup.direction,
            setup_state=setup.state,
            setup_created_at=setup.created_at,
            setup_as_of=setup.as_of,
            setup_seed_event_id=setup.seed_event_id,
            setup_reference_id=setup.reference_id,
            setup_config_fingerprint=snapshot.config_fingerprint,
            setup_rules_version=snapshot.rules_version,
            setup_snapshot_id=identity,
            setup_snapshot_json=canonical_json(snapshot.to_json_dict()),
            plan_id=None if plan is None else plan.id,
            plan_state=None if plan is None else plan.state,
            planning_as_of=None if plan is None else plan.as_of,
            plan_json=None if plan is None else canonical_json(plan.to_json_dict()),
            plan_config_fingerprint=None if plan is None else plan.config_fingerprint,
            planning_rules_version=None
            if plan is None
            else plan.planning_rules_version,
        )
        return self.repository.insert_record(record)

    def journal_plan(
        self,
        *,
        snapshot: QualificationSnapshot,
        plan: TradePlanResult,
    ) -> JournalRecord:
        """Journal one Step 6 plan together with the exact snapshot behind it."""

        if not isinstance(plan, TradePlanResult):
            raise TypeError("plan must be a Step 6 TradePlanResult")
        if not isinstance(plan.setup_id, str) or not plan.setup_id.strip():
            raise ValueError(
                "plan.setup_id must name the Step 5 setup the plan belongs to"
            )
        return self.journal_setup(snapshot=snapshot, setup_id=plan.setup_id, plan=plan)

    def get_record(self, *, journal_id: str) -> JournalRecord:
        return self.repository.get_record(journal_id)

    def records_for_setup(self, *, setup_id: str) -> tuple[JournalRecord, ...]:
        """Every journal record that references one Step 5 setup id."""

        return self.repository.records_for_setup(setup_id)

    def records_for_snapshot(
        self, *, snapshot_id: str | QualificationSnapshot
    ) -> tuple[JournalRecord, ...]:
        """Every record produced from one immutable Step 5 snapshot identity."""

        if isinstance(snapshot_id, QualificationSnapshot):
            snapshot_id = snapshot_identity(snapshot_id)
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            raise ValueError("snapshot_id must be a content identity or snapshot")
        return self.repository.records_for_snapshot(snapshot_id)

    # ------------------------------------------------------------------
    # Decisions (append-only)
    # ------------------------------------------------------------------

    def record_decision(
        self,
        *,
        journal_id: str,
        decision: DecisionState | str,
        decided_at: datetime | None = None,
        reason: str | None = None,
    ) -> DecisionRecord:
        """Append one explicit decision about one immutable journal record.

        ``decision`` has no default and is always required: a journaled proposal
        is never assumed accepted. ``decided_at`` defaults to the current UTC
        instant; supplying it explicitly makes the append fully reproducible.
        Re-recording the identical decision (same state, instant and reason) is
        a no-op; any other change appends a new row that supersedes the previous
        effective decision, preserving the complete decision history.
        """

        record = self.repository.get_record(journal_id)
        state = _coerce_decision(decision)
        timestamp = (
            datetime.now(UTC)
            if decided_at is None
            else require_utc_datetime(decided_at, field_name="decided_at")
        )
        note = normalize_note(reason)
        decision_id = fingerprint(
            "journal-decision",
            {
                "journal_id": record.journal_id,
                "decision": state.value,
                "decided_at": timestamp,
                "reason": note,
                "decision_rules_version": DECISION_RULES_VERSION,
            },
        )
        return self.repository.append_decision(
            journal_id=record.journal_id,
            decision_id=decision_id,
            decision=state,
            decided_at=timestamp,
            reason=note,
            decision_rules_version=DECISION_RULES_VERSION,
        )

    def decision_history(self, *, journal_id: str) -> tuple[DecisionRecord, ...]:
        """Every decision ever recorded for one journal record, oldest first."""

        self.repository.get_record(journal_id)
        return self.repository.decisions_for_record(journal_id)

    def latest_decision(self, *, journal_id: str) -> DecisionRecord | None:
        """The effective decision, or ``None`` when none was ever recorded."""

        self.repository.get_record(journal_id)
        return self.repository.latest_decision(journal_id)

    def get_decision(self, *, decision_id: str) -> DecisionRecord:
        return self.repository.get_decision(decision_id)

    # ------------------------------------------------------------------
    # Outcome observations (append-only, versioned)
    # ------------------------------------------------------------------

    def observe_outcome(
        self,
        *,
        journal_id: str,
        observed_through: datetime,
        parameters: OutcomeParameters | None = None,
    ) -> OutcomeObservation:
        """Deterministically observe one journaled planned setup at a cutoff.

        Only candles stored for the plan's instrument/timeframe with an open time
        inside ``[plan.as_of, observed_through]`` are read; later candles cannot
        influence the result. The observation is computed from the exact plan
        JSON stored on the journal record, appended as a new version when its
        content identity is new, and returned unchanged when it already exists.
        """

        record = self.repository.get_record(journal_id)
        if record.plan_json is None or record.plan_id is None:
            raise ValueError(
                f"journal record {journal_id} has no Step 6 plan; outcome "
                "observation requires a PLANNABLE proposed plan"
            )
        if record.plan_state is not PlanState.PLANNABLE:
            raise ValueError(
                f"journal record {journal_id} stores plan state "
                f"{record.plan_state}; only PLANNABLE proposed plans carry levels "
                "that can be observed"
            )
        payload = record.plan_payload
        assert payload is not None  # plan_json was checked above
        try:
            levels = ProposedPlanLevels.from_payload(payload)
        except (TypeError, ValueError) as exc:
            # Reachable only for a record written by other code (the append path
            # stores exactly what Step 6 produced). Refuse loudly instead of
            # observing against a plan we cannot read.
            raise JournalError(
                f"stored plan payload for {record.journal_id} is malformed: {exc}"
            ) from exc
        cutoff = require_utc_datetime(observed_through, field_name="observed_through")
        candles = self.candles.get_candles(
            exchange=levels.exchange,
            symbol=levels.symbol,
            timeframe=levels.timeframe,
            start_time=levels.as_of,
            end_time=cutoff,
        )
        observation = observe_outcome(
            journal_id=record.journal_id,
            levels=levels,
            candles=candles.candles,
            observed_through=cutoff,
            parameters=parameters,
        )
        return self.repository.append_outcome(observation)

    def outcome_history(self, *, journal_id: str) -> tuple[OutcomeVersion, ...]:
        """Every stored observation version for one record, oldest first.

        The version chain (``sequence``, ``supersedes_outcome_id``) is the
        append-only audit trail: an observation through T1 remains recoverable
        by identity after a later observation through T2 is appended.
        """

        self.repository.get_record(journal_id)
        return self.repository.outcome_versions(journal_id)

    def latest_outcome(self, *, journal_id: str) -> OutcomeObservation | None:
        """The newest stored observation version, or ``None`` when never observed."""

        self.repository.get_record(journal_id)
        return self.repository.latest_outcome(journal_id)

    def get_outcome(self, *, outcome_id: str) -> OutcomeObservation:
        """Recover one historical observation exactly as it was stored."""

        return self.repository.get_outcome(outcome_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_setup(
        self, *, snapshot: QualificationSnapshot, setup_id: str
    ) -> SetupResult:
        if not isinstance(snapshot, QualificationSnapshot):
            raise TypeError("snapshot must be a Step 5 QualificationSnapshot")
        if not isinstance(setup_id, str) or not setup_id.strip():
            raise ValueError("setup_id must be a non-empty string")
        setup = next((item for item in snapshot.setups if item.id == setup_id), None)
        if setup is None:
            raise ValueError(
                f"setup {setup_id} is not present in the snapshot at "
                f"{snapshot.as_of}; the journal records only what the system "
                "actually produced"
            )
        return setup


def _coerce_decision(decision: DecisionState | str) -> DecisionState:
    if isinstance(decision, DecisionState):
        return decision
    if isinstance(decision, str):
        try:
            return DecisionState(decision)
        except ValueError as exc:
            raise ValueError(
                "decision must be one of: "
                + ", ".join(state.value for state in DecisionState)
            ) from exc
    raise TypeError("decision must be a DecisionState or its string value")


def _require_matching_plan(
    *,
    snapshot: QualificationSnapshot,
    setup: SetupResult,
    plan: TradePlanResult,
) -> None:
    if not isinstance(plan, TradePlanResult):
        raise TypeError("plan must be a Step 6 TradePlanResult")
    if (plan.exchange, plan.symbol, plan.timeframe) != (
        snapshot.exchange,
        snapshot.symbol,
        snapshot.timeframe,
    ):
        raise ValueError(
            f"plan instrument {plan.exchange}/{plan.symbol}/{plan.timeframe} does "
            f"not match the snapshot "
            f"{snapshot.exchange}/{snapshot.symbol}/{snapshot.timeframe}"
        )
    if plan.as_of != snapshot.as_of:
        raise ValueError(
            f"plan as_of {plan.as_of} does not match the snapshot as_of "
            f"{snapshot.as_of}; a journal record must be one coherent system moment"
        )
    if plan.setup_id not in (None, setup.id):
        raise ValueError(
            f"plan setup_id {plan.setup_id} does not match the journaled setup "
            f"{setup.id}"
        )
    if plan.setup_config_fingerprint not in (None, snapshot.config_fingerprint):
        raise ValueError(
            "plan was planned under a different Step 5 configuration fingerprint "
            f"({plan.setup_config_fingerprint}) than the snapshot "
            f"({snapshot.config_fingerprint})"
        )
    if plan.family is not None and plan.family is not setup.family:
        raise ValueError(
            f"plan family {plan.family} does not match the setup family {setup.family}"
        )
    if plan.direction is not None and plan.direction != setup.direction:
        raise ValueError(
            f"plan direction {plan.direction} does not match the setup direction "
            f"{setup.direction}"
        )


__all__ = ["JournalService", "snapshot_identity"]
