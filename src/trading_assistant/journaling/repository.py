"""Durable append-only journal reads and writes over SQLite.

The repository only ever inserts. A repeated insert of the exact same immutable
content is a verified no-op; a repeated identity with different content raises
``JournalConflict`` instead of overwriting history. Decisions and outcome
observations are appended as new rows chained through their ``supersedes_*``
references, so every earlier historical value stays readable forever.

Models and storage types come from Step 1-2 (``Base``, ``DecimalText``,
``UTCDateTime``); this layer adds no schema logic of its own — migrations own
the schema.
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.journaling.errors import (
    JournalConflict,
    JournalError,
    JournalNotFound,
)
from trading_assistant.journaling.models import (
    JournalDecisionRow,
    JournalOutcomeEventRow,
    JournalOutcomeRow,
    JournalRecordRow,
)
from trading_assistant.journaling.parameters import canonical_json, fingerprint
from trading_assistant.journaling.types import (
    DecisionRecord,
    DecisionState,
    JournalRecord,
    OutcomeObservation,
    OutcomeStatus,
    OutcomeVersion,
    RecordKind,
)
from trading_assistant.setup_qualification.models import SetupFamily, SetupState
from trading_assistant.trade_planning.models import PlanState

#: Records ordered oldest first, ties broken by identity for full determinism.
_RECORD_ORDER = (JournalRecordRow.setup_as_of, JournalRecordRow.journal_id)


class JournalRepository:
    """SQLite-backed immutable journal store."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError(
                "JournalRepository currently requires a SQLite SQLAlchemy engine"
            )
        self._sessions = sessionmaker(
            bind=engine, class_=Session, expire_on_commit=False
        )

    # ------------------------------------------------------------------
    # Journal records
    # ------------------------------------------------------------------

    def insert_record(self, record: JournalRecord) -> JournalRecord:
        """Insert one immutable record, or return the identical stored one."""

        values = _record_values(record)
        with self._sessions.begin() as session:
            session.execute(
                sqlite_insert(JournalRecordRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["journal_id"])
            )
            row = session.get(JournalRecordRow, record.journal_id)
            if row is None:
                raise JournalError(f"journal record {record.journal_id} was not stored")
            _verify_unchanged(row, values, what=f"journal record {record.journal_id}")
            return _record_from_row(row)

    def find_record(self, journal_id: str) -> JournalRecord | None:
        with self._sessions() as session:
            row = session.get(JournalRecordRow, journal_id)
            return None if row is None else _record_from_row(row)

    def get_record(self, journal_id: str) -> JournalRecord:
        record = self.find_record(journal_id)
        if record is None:
            raise JournalNotFound(f"journal record {journal_id} is not stored")
        return record

    def records_for_setup(self, setup_id: str) -> tuple[JournalRecord, ...]:
        statement = (
            select(JournalRecordRow)
            .where(JournalRecordRow.setup_id == setup_id)
            .order_by(*_RECORD_ORDER)
        )
        return self._records(statement)

    def records_for_snapshot(self, setup_snapshot_id: str) -> tuple[JournalRecord, ...]:
        statement = (
            select(JournalRecordRow)
            .where(JournalRecordRow.setup_snapshot_id == setup_snapshot_id)
            .order_by(*_RECORD_ORDER)
        )
        return self._records(statement)

    def _records(self, statement: Any) -> tuple[JournalRecord, ...]:
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_record_from_row(row) for row in rows)

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def append_decision(
        self,
        *,
        journal_id: str,
        decision_id: str,
        decision: DecisionState,
        decided_at: datetime,
        reason: str | None,
        decision_rules_version: str,
    ) -> DecisionRecord:
        """Append one decision row, or return an identical already-stored one.

        ``sequence`` is the append order and ``supersedes_decision_id`` the
        previously effective row, both assigned from current stored history in
        the same transaction. Traceability is copied from the immutable record,
        never supplied by the caller.
        """

        with self._sessions.begin() as session:
            existing = session.get(JournalDecisionRow, decision_id)
            if existing is not None:
                _verify_decision_matches(
                    existing,
                    decision_id=decision_id,
                    journal_id=journal_id,
                    decision=decision,
                    decided_at=decided_at,
                    reason=reason,
                    decision_rules_version=decision_rules_version,
                )
                return _decision_from_row(existing)

            record_row = session.get(JournalRecordRow, journal_id)
            if record_row is None:
                raise JournalNotFound(f"journal record {journal_id} is not stored")
            last = session.scalars(
                select(JournalDecisionRow)
                .where(JournalDecisionRow.journal_id == journal_id)
                .order_by(JournalDecisionRow.sequence.desc())
                .limit(1)
            ).first()
            row = JournalDecisionRow(
                decision_id=decision_id,
                journal_id=journal_id,
                sequence=1 if last is None else last.sequence + 1,
                supersedes_decision_id=None if last is None else last.decision_id,
                decision=decision.value,
                decided_at=decided_at,
                reason=reason,
                decision_rules_version=decision_rules_version,
                record_kind=record_row.record_kind,
                setup_id=record_row.setup_id,
                plan_id=record_row.plan_id,
                setup_family=record_row.setup_family,
                direction=record_row.setup_direction,
                setup_state=record_row.setup_state,
                exchange=record_row.exchange,
                symbol=record_row.symbol,
                timeframe=record_row.timeframe,
                source_timeframes_json=record_row.source_timeframes_json,
                setup_as_of=record_row.setup_as_of,
                planning_as_of=record_row.planning_as_of,
                setup_config_fingerprint=record_row.setup_config_fingerprint,
                plan_config_fingerprint=record_row.plan_config_fingerprint,
                setup_rules_version=record_row.setup_rules_version,
                planning_rules_version=record_row.planning_rules_version,
                setup_snapshot_id=record_row.setup_snapshot_id,
            )
            session.add(row)
            session.flush()
            return _decision_from_row(row)

    def find_decision(self, decision_id: str) -> DecisionRecord | None:
        with self._sessions() as session:
            row = session.get(JournalDecisionRow, decision_id)
            return None if row is None else _decision_from_row(row)

    def get_decision(self, decision_id: str) -> DecisionRecord:
        decision = self.find_decision(decision_id)
        if decision is None:
            raise JournalNotFound(f"journal decision {decision_id} is not stored")
        return decision

    def decisions_for_record(self, journal_id: str) -> tuple[DecisionRecord, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(JournalDecisionRow)
                .where(JournalDecisionRow.journal_id == journal_id)
                .order_by(JournalDecisionRow.sequence.asc())
            ).all()
            return tuple(_decision_from_row(row) for row in rows)

    def latest_decision(self, journal_id: str) -> DecisionRecord | None:
        with self._sessions() as session:
            row = session.scalars(
                select(JournalDecisionRow)
                .where(JournalDecisionRow.journal_id == journal_id)
                .order_by(JournalDecisionRow.sequence.desc())
                .limit(1)
            ).first()
            return None if row is None else _decision_from_row(row)

    # ------------------------------------------------------------------
    # Outcome observations
    # ------------------------------------------------------------------

    def append_outcome(self, observation: OutcomeObservation) -> OutcomeObservation:
        """Append one outcome observation, or return an identical stored one.

        An identical observation (same content identity) is a verified no-op, so
        replaying a historical T1 cutoff reproduces the T1 row. A different
        observation for the same journal record is appended as a new version
        that supersedes the latest row; earlier rows are never modified.
        """

        values = _outcome_values(observation)
        with self._sessions.begin() as session:
            existing = session.get(JournalOutcomeRow, observation.id)
            if existing is not None:
                _verify_unchanged(
                    existing, values, what=f"outcome observation {observation.id}"
                )
                return _observation_from_row(existing)

            last = session.scalars(
                select(JournalOutcomeRow)
                .where(JournalOutcomeRow.journal_id == observation.journal_id)
                .order_by(JournalOutcomeRow.sequence.desc())
                .limit(1)
            ).first()
            row = JournalOutcomeRow(
                **values,
                sequence=1 if last is None else last.sequence + 1,
                supersedes_outcome_id=None if last is None else last.outcome_id,
            )
            session.add(row)
            # Flush the parent row first so the event rows satisfy their foreign key.
            session.flush()
            for event in observation.events:
                session.add(
                    JournalOutcomeEventRow(
                        event_id=fingerprint(
                            "outcome-event",
                            observation.id,
                            event.sequence,
                            event.kind.value,
                            event.target_index,
                        ),
                        outcome_id=observation.id,
                        sequence=event.sequence,
                        kind=event.kind.value,
                        target_index=(
                            -1 if event.target_index is None else event.target_index
                        ),
                        level_value=event.level,
                        candle_timestamp=event.candle_timestamp,
                        candle_index=event.candle_index,
                        ordering=event.ordering.value,
                        co_touched=event.co_touched,
                    )
                )
            session.flush()
            return _observation_from_row(row)

    def find_outcome(self, outcome_id: str) -> OutcomeObservation | None:
        with self._sessions() as session:
            row = session.get(JournalOutcomeRow, outcome_id)
            return None if row is None else _observation_from_row(row)

    def get_outcome(self, outcome_id: str) -> OutcomeObservation:
        observation = self.find_outcome(outcome_id)
        if observation is None:
            raise JournalNotFound(f"outcome observation {outcome_id} is not stored")
        return observation

    def outcome_versions(self, journal_id: str) -> tuple[OutcomeVersion, ...]:
        """Every stored observation version, oldest first (append-only audit)."""

        with self._sessions() as session:
            rows = session.scalars(
                select(JournalOutcomeRow)
                .where(JournalOutcomeRow.journal_id == journal_id)
                .order_by(JournalOutcomeRow.sequence.asc())
            ).all()
            return tuple(
                OutcomeVersion(
                    sequence=row.sequence,
                    supersedes_outcome_id=row.supersedes_outcome_id,
                    observation=_observation_from_row(row),
                )
                for row in rows
            )

    def latest_outcome(self, journal_id: str) -> OutcomeObservation | None:
        with self._sessions() as session:
            row = session.scalars(
                select(JournalOutcomeRow)
                .where(JournalOutcomeRow.journal_id == journal_id)
                .order_by(JournalOutcomeRow.sequence.desc())
                .limit(1)
            ).first()
            return None if row is None else _observation_from_row(row)


# ----------------------------------------------------------------------
# Row <-> domain mapping
# ----------------------------------------------------------------------


def _record_values(record: JournalRecord) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields(record):
        value = getattr(record, field.name)
        if field.name == "source_timeframes":
            values["source_timeframes_json"] = canonical_json(value)
        elif field.name == "record_kind":
            values["record_kind"] = RecordKind(value).value
        else:
            values[field.name] = value
    return values


def _record_from_row(row: JournalRecordRow) -> JournalRecord:
    return JournalRecord(
        journal_id=row.journal_id,
        record_kind=RecordKind(row.record_kind),
        journal_rules_version=row.journal_rules_version,
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        source_timeframes=_text_tuple(row.source_timeframes_json, "source_timeframes"),
        setup_id=row.setup_id,
        setup_family=_enum_or_none(SetupFamily, row.setup_family),
        setup_direction=row.setup_direction,
        setup_state=SetupState(row.setup_state),
        setup_created_at=row.setup_created_at,
        setup_as_of=row.setup_as_of,
        setup_seed_event_id=row.setup_seed_event_id,
        setup_reference_id=row.setup_reference_id,
        setup_config_fingerprint=row.setup_config_fingerprint,
        setup_rules_version=row.setup_rules_version,
        setup_snapshot_id=row.setup_snapshot_id,
        setup_snapshot_json=row.setup_snapshot_json,
        plan_id=row.plan_id,
        plan_state=_enum_or_none(PlanState, row.plan_state),
        planning_as_of=row.planning_as_of,
        plan_json=row.plan_json,
        plan_config_fingerprint=row.plan_config_fingerprint,
        planning_rules_version=row.planning_rules_version,
    )


def _decision_from_row(row: JournalDecisionRow) -> DecisionRecord:
    return DecisionRecord(
        decision_id=row.decision_id,
        journal_id=row.journal_id,
        sequence=row.sequence,
        supersedes_decision_id=row.supersedes_decision_id,
        decision=DecisionState(row.decision),
        decided_at=row.decided_at,
        reason=row.reason,
        decision_rules_version=row.decision_rules_version,
        record_kind=RecordKind(row.record_kind),
        setup_id=row.setup_id,
        plan_id=row.plan_id,
        setup_family=_enum_or_none(SetupFamily, row.setup_family),
        direction=row.direction,
        setup_state=SetupState(row.setup_state),
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        source_timeframes=_text_tuple(row.source_timeframes_json, "source_timeframes"),
        setup_as_of=row.setup_as_of,
        planning_as_of=row.planning_as_of,
        setup_config_fingerprint=row.setup_config_fingerprint,
        plan_config_fingerprint=row.plan_config_fingerprint,
        setup_rules_version=row.setup_rules_version,
        planning_rules_version=row.planning_rules_version,
        setup_snapshot_id=row.setup_snapshot_id,
    )


def _outcome_values(observation: OutcomeObservation) -> dict[str, Any]:
    return {
        "outcome_id": observation.id,
        "journal_id": observation.journal_id,
        "observation_rules_version": observation.observation_rules_version,
        "config_fingerprint": observation.config_fingerprint,
        "plan_id": observation.plan_id,
        "setup_id": observation.setup_id,
        "exchange": observation.exchange,
        "symbol": observation.symbol,
        "timeframe": observation.timeframe,
        "direction": observation.direction,
        "entry_level": observation.entry_level,
        "stop_level": observation.stop_level,
        "risk_per_unit": observation.risk_per_unit,
        "target_levels_json": canonical_json(observation.target_levels),
        "window_start": observation.window_start,
        "observed_through": observation.observed_through,
        "evaluated_through": observation.evaluated_through,
        "status": OutcomeStatus(observation.status).value,
        "entry_reached": observation.entry_reached,
        "entry_ordered": observation.entry_ordered,
        "entry_timestamp": observation.entry_timestamp,
        "entry_candle_index": observation.entry_candle_index,
        "stop_reached": observation.stop_reached,
        "stop_pre_entry": observation.stop_pre_entry,
        "stop_timestamp": observation.stop_timestamp,
        "targets_reached_json": canonical_json(observation.targets_reached),
        "targets_pre_entry_json": canonical_json(observation.targets_pre_entry),
        "first_touch_order_json": canonical_json(observation.first_touch_order),
        "ambiguous": observation.ambiguous,
        "ambiguity_kind": observation.ambiguity_kind,
        "ambiguity_timestamp": observation.ambiguity_timestamp,
        "incomplete": observation.incomplete,
        "missing_candle_count": observation.missing_candle_count,
        "missing_ranges_json": canonical_json(observation.missing_ranges),
        "expected_candle_count": observation.expected_candle_count,
        "observed_candle_count": observation.observed_candle_count,
        "evaluated_low": observation.evaluated_low,
        "evaluated_low_timestamp": observation.evaluated_low_timestamp,
        "evaluated_high": observation.evaluated_high,
        "evaluated_high_timestamp": observation.evaluated_high_timestamp,
        "post_entry_low": observation.post_entry_low,
        "post_entry_high": observation.post_entry_high,
        "mfe_price_move": observation.mfe_price_move,
        "mae_price_move": observation.mae_price_move,
        "mfe_r": observation.mfe_r,
        "mae_r": observation.mae_r,
        "payload_json": canonical_json(observation),
    }


def _observation_from_row(row: JournalOutcomeRow) -> OutcomeObservation:
    try:
        payload = json.loads(row.payload_json)
    except (TypeError, ValueError) as exc:
        raise JournalError(
            f"stored outcome payload for {row.outcome_id} is not valid JSON"
        ) from exc
    observation = OutcomeObservation.from_json_dict(payload)
    if observation.id != row.outcome_id or observation.journal_id != row.journal_id:
        raise JournalError(
            f"stored outcome payload for {row.outcome_id} contradicts its row identity"
        )
    return observation


def _verify_unchanged(row: Any, values: dict[str, Any], *, what: str) -> None:
    for name, expected in values.items():
        stored = getattr(row, name)
        if stored != expected:
            raise JournalConflict(
                f"{what} already exists with different stored {name!r}; "
                "journal history is never overwritten"
            )


def _verify_decision_matches(
    row: JournalDecisionRow,
    *,
    decision_id: str,
    journal_id: str,
    decision: DecisionState,
    decided_at: datetime,
    reason: str | None,
    decision_rules_version: str,
) -> None:
    expected = {
        "decision_id": decision_id,
        "journal_id": journal_id,
        "decision": decision.value,
        "decided_at": decided_at,
        "reason": reason,
        "decision_rules_version": decision_rules_version,
    }
    _verify_unchanged(row, expected, what=f"journal decision {decision_id}")


def _text_tuple(text: str, field_name: str) -> tuple[str, ...]:
    try:
        values = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise JournalError(f"stored {field_name} is not valid JSON") from exc
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise JournalError(f"stored {field_name} must be a JSON list of strings")
    return tuple(values)


def _enum_or_none(enum_type: Any, value: str | None) -> Any:
    return None if value is None else enum_type(value)
