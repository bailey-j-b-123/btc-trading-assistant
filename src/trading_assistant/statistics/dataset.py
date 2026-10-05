"""Immutable Step 7 journal datasets and a SELECT-only SQLite reader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading_assistant.journaling.models import (
    JournalDecisionRow,
    JournalOutcomeRow,
    JournalRecordRow,
)
from trading_assistant.journaling.repository import (
    _decision_from_row,
    _observation_from_row,
    _record_from_row,
)
from trading_assistant.journaling.types import (
    DecisionRecord,
    JournalRecord,
    OutcomeVersion,
)
from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.trade_planning.models import PlanState

if TYPE_CHECKING:
    from collections.abc import Iterable


class StatisticsDatasetError(ValueError):
    """The supplied immutable journal dataset is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class JournalDataset:
    """One explicit, immutable read snapshot of Step 7 journal rows.

    The dataset contains the immutable journal records and every decision and
    outcome version returned by the reader. Statistics select effective
    decisions/outcomes by their recorded event-time cutoffs; they never ask
    Steps 5/6 to replay or re-create historical state.
    """

    records: tuple[JournalRecord, ...]
    decisions: tuple[DecisionRecord, ...] = ()
    outcomes: tuple[OutcomeVersion, ...] = ()

    def __post_init__(self) -> None:
        for name in ("records", "decisions", "outcomes"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise TypeError(f"{name} must be an immutable tuple")
        if any(not isinstance(row, JournalRecord) for row in self.records):
            raise TypeError("records must contain Step 7 JournalRecord values")
        if any(not isinstance(row, DecisionRecord) for row in self.decisions):
            raise TypeError("decisions must contain Step 7 DecisionRecord values")
        if any(not isinstance(row, OutcomeVersion) for row in self.outcomes):
            raise TypeError("outcomes must contain Step 7 OutcomeVersion values")

        record_ids = [record.journal_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise StatisticsDatasetError("journal record identities must be unique")
        record_by_id = {record.journal_id: record for record in self.records}
        for record in self.records:
            require_utc_datetime(record.setup_as_of, field_name="setup_as_of")
            if record.setup_created_at is not None:
                require_utc_datetime(
                    record.setup_created_at, field_name="setup_created_at"
                )
            if record.planning_as_of is not None:
                require_utc_datetime(record.planning_as_of, field_name="planning_as_of")

        decision_ids = [decision.decision_id for decision in self.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise StatisticsDatasetError("decision identities must be unique")
        decision_sequences: set[tuple[str, int]] = set()
        for decision in self.decisions:
            if decision.journal_id not in record_by_id:
                raise StatisticsDatasetError(
                    f"decision {decision.decision_id} references a missing journal record"
                )
            if decision.sequence < 1:
                raise StatisticsDatasetError("decision sequence must be >= 1")
            require_utc_datetime(decision.decided_at, field_name="decided_at")
            key = (decision.journal_id, decision.sequence)
            if key in decision_sequences:
                raise StatisticsDatasetError(
                    f"duplicate decision sequence {decision.sequence} for "
                    f"{decision.journal_id}"
                )
            decision_sequences.add(key)

        outcome_ids: list[str] = []
        outcome_sequences: set[tuple[str, int]] = set()
        for version in self.outcomes:
            observation = version.observation
            outcome_ids.append(observation.id)
            record = record_by_id.get(observation.journal_id)
            if record is None:
                raise StatisticsDatasetError(
                    f"outcome {observation.id} references a missing journal record"
                )
            if record.plan_state is not PlanState.PLANNABLE:
                raise StatisticsDatasetError(
                    f"outcome {observation.id} is attached to a non-PLANNABLE journal plan"
                )
            if record.plan_id != observation.plan_id:
                raise StatisticsDatasetError(
                    f"outcome {observation.id} plan identity contradicts its journal record"
                )
            if (
                observation.exchange != record.exchange
                or observation.symbol != record.symbol
                or observation.timeframe != record.timeframe
                or observation.direction != record.setup_direction
                or observation.setup_id != record.setup_id
            ):
                raise StatisticsDatasetError(
                    f"outcome {observation.id} dimensions contradict its journal record"
                )
            if record.planning_as_of is not None and require_utc_datetime(
                observation.window_start
            ) != require_utc_datetime(record.planning_as_of):
                raise StatisticsDatasetError(
                    f"outcome {observation.id} window start contradicts its journal plan"
                )
            if version.sequence < 1:
                raise StatisticsDatasetError("outcome sequence must be >= 1")
            require_utc_datetime(
                observation.window_start, field_name="outcome.window_start"
            )
            require_utc_datetime(
                observation.observed_through, field_name="outcome.observed_through"
            )
            key = (observation.journal_id, version.sequence)
            if key in outcome_sequences:
                raise StatisticsDatasetError(
                    f"duplicate outcome sequence {version.sequence} for "
                    f"{observation.journal_id}"
                )
            outcome_sequences.add(key)
        if len(outcome_ids) != len(set(outcome_ids)):
            raise StatisticsDatasetError("outcome identities must be unique")

        object.__setattr__(
            self,
            "records",
            tuple(
                sorted(
                    self.records,
                    key=lambda row: (
                        require_utc_datetime(row.setup_as_of).isoformat(),
                        row.journal_id,
                    ),
                )
            ),
        )
        object.__setattr__(
            self,
            "decisions",
            tuple(
                sorted(
                    self.decisions,
                    key=lambda row: (row.journal_id, row.sequence, row.decision_id),
                )
            ),
        )
        object.__setattr__(
            self,
            "outcomes",
            tuple(
                sorted(
                    self.outcomes,
                    key=lambda row: (
                        row.observation.journal_id,
                        row.sequence,
                        row.observation.id,
                    ),
                )
            ),
        )

    @classmethod
    def from_rows(
        cls,
        *,
        records: Iterable[JournalRecord],
        decisions: Iterable[DecisionRecord] = (),
        outcomes: Iterable[OutcomeVersion] = (),
    ) -> JournalDataset:
        """Convenience constructor which freezes caller-supplied row sequences."""

        return cls(tuple(records), tuple(decisions), tuple(outcomes))


class JournalDatasetReader:
    """Read all Step 7 rows using SELECT statements only; never persists data."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError(
                "JournalDatasetReader currently requires a SQLite SQLAlchemy engine"
            )
        database = engine.url.database
        if (
            database
            and database not in (":memory:",)
            and not database.startswith("file:")
        ):
            if not Path(database).exists():
                raise FileNotFoundError(
                    "statistics source database does not exist; analysis will not "
                    "create a database file"
                )
        self._engine = engine

    def read(self) -> JournalDataset:
        """Return one immutable dataset assembled using journal-only SELECTs."""

        with Session(self._engine) as session:
            records = tuple(
                _record_from_row(row)
                for row in session.scalars(
                    select(JournalRecordRow).order_by(
                        JournalRecordRow.setup_as_of, JournalRecordRow.journal_id
                    )
                ).all()
            )
            decisions = tuple(
                _decision_from_row(row)
                for row in session.scalars(
                    select(JournalDecisionRow).order_by(
                        JournalDecisionRow.journal_id,
                        JournalDecisionRow.sequence,
                    )
                ).all()
            )
            outcome_rows = session.scalars(
                select(JournalOutcomeRow).order_by(
                    JournalOutcomeRow.journal_id, JournalOutcomeRow.sequence
                )
            ).all()
            outcomes = tuple(
                OutcomeVersion(
                    sequence=row.sequence,
                    supersedes_outcome_id=row.supersedes_outcome_id,
                    observation=_observation_from_row(row),
                )
                for row in outcome_rows
            )
        return JournalDataset(records, decisions, outcomes)
