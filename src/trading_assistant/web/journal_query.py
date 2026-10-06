"""Read-only filtered listing adapter over Step 7 journal rows.

This adapter only issues SELECTs against the immutable Step 7 tables and never
writes. Filtering by latest decision/outcome is done after the record page is
fetched through the existing repository methods, so the adapter reuses Step 7
semantics instead of reinterpreting stored rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading_assistant.journaling.models import JournalRecordRow
from trading_assistant.journaling.repository import (
    JournalRepository,
    _record_from_row,  # row decoding reused exactly as Step 8's dataset reader does
)
from trading_assistant.journaling.types import JournalRecord
from trading_assistant.market_structure.snapshot import to_jsonable

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

RECORD_FILTER_COLUMNS = {
    "symbol": JournalRecordRow.symbol,
    "exchange": JournalRecordRow.exchange,
    "timeframe": JournalRecordRow.timeframe,
    "setup_family": JournalRecordRow.setup_family,
    "direction": JournalRecordRow.setup_direction,
    "setup_state": JournalRecordRow.setup_state,
    "plan_state": JournalRecordRow.plan_state,
    "record_kind": JournalRecordRow.record_kind,
    "setup_id": JournalRecordRow.setup_id,
}


@dataclass(frozen=True, slots=True)
class JournalListQuery:
    """Validated, exact-match filters plus deterministic ordering/paging."""

    symbol: str | None = None
    exchange: str | None = None
    timeframe: str | None = None
    setup_family: str | None = None
    direction: str | None = None
    setup_state: str | None = None
    plan_state: str | None = None
    record_kind: str | None = None
    setup_id: str | None = None
    decision_state: str | None = None
    outcome_status: str | None = None
    range_from: datetime | None = None
    range_to: datetime | None = None
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


class JournalQueryService:
    def __init__(self, engine: Engine, repository: JournalRepository) -> None:
        self.engine = engine
        self.repository = repository

    def list_records(self, query: JournalListQuery) -> dict[str, object]:
        limit = max(1, min(query.limit, MAX_PAGE_SIZE))
        offset = max(0, query.offset)
        filters = {
            name: getattr(query, name)
            for name in (
                "symbol",
                "exchange",
                "timeframe",
                "setup_family",
                "direction",
                "setup_state",
                "plan_state",
                "record_kind",
                "setup_id",
            )
            if getattr(query, name) is not None
        }
        with Session(
            self.engine
        ) as session:  # SELECT-only; rows converted via Step 7 helpers
            statement = select(JournalRecordRow)
            for name, value in filters.items():
                statement = statement.where(RECORD_FILTER_COLUMNS[name] == value)
            if query.range_from is not None:
                statement = statement.where(
                    JournalRecordRow.setup_as_of >= query.range_from
                )
            if query.range_to is not None:
                statement = statement.where(
                    JournalRecordRow.setup_as_of <= query.range_to
                )
            total = session.scalar(
                select(func.count()).select_from(statement.subquery())
            )
            rows = session.scalars(
                statement.order_by(
                    JournalRecordRow.setup_as_of.desc(),
                    JournalRecordRow.journal_id,
                )
                .limit(limit)
                .offset(offset)
            ).all()
            records = tuple(_record_from_row(row) for row in rows)

        items = []
        for record in records:
            latest_decision = self.repository.latest_decision(record.journal_id)
            latest_outcome = self.repository.latest_outcome(record.journal_id)
            if query.decision_state is not None and (
                latest_decision is None
                or latest_decision.decision.value != query.decision_state
            ):
                continue
            if query.outcome_status is not None and (
                latest_outcome is None
                or latest_outcome.status.value != query.outcome_status
            ):
                continue
            items.append(self._summary(record, latest_decision, latest_outcome))
        return {
            "total_matching_records": int(total or 0),
            "returned_count": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
            "note": (
                "total_matching_records counts record-table filters only; "
                "decision/outcome filters are applied to the returned page"
                if query.decision_state is not None or query.outcome_status is not None
                else None
            ),
        }

    def record_detail(self, journal_id: str) -> dict[str, object] | None:
        record = self.repository.find_record(journal_id)
        if record is None:
            return None
        decisions = self.repository.decisions_for_record(journal_id)
        outcomes = self.repository.outcome_versions(journal_id)
        return {
            "record": record.to_json_dict(),
            "setup_snapshot": record.setup_snapshot_payload,
            "plan": record.plan_payload,
            "decisions": [decision.to_json_dict() for decision in decisions],
            "latest_decision": None if not decisions else decisions[-1].to_json_dict(),
            "outcome_versions": [version.to_json_dict() for version in outcomes],
            "latest_outcome": None
            if not outcomes
            else outcomes[-1].observation.to_json_dict(),
        }

    @staticmethod
    def _summary(
        record: JournalRecord, latest_decision, latest_outcome
    ) -> dict[str, object]:
        plan_payload = record.plan_payload
        plan_levels = None
        if plan_payload is not None and record.plan_state is not None:
            entry = plan_payload.get("entry", {})
            stop = plan_payload.get("stop", {})
            plan_levels = {
                "entry": entry.get("value") if isinstance(entry, dict) else None,
                "stop": stop.get("value") if isinstance(stop, dict) else None,
                "targets": [
                    target.get("level", {}).get("value")
                    if isinstance(target, dict)
                    else None
                    for target in plan_payload.get("targets", [])
                ],
                "risk_per_unit": plan_payload.get("risk_per_unit"),
            }
        return {
            "journal_id": record.journal_id,
            "record_kind": to_jsonable(record.record_kind),
            "exchange": record.exchange,
            "symbol": record.symbol,
            "timeframe": record.timeframe,
            "source_timeframes": list(record.source_timeframes),
            "setup_id": record.setup_id,
            "setup_family": to_jsonable(record.setup_family),
            "direction": to_jsonable(record.setup_direction),
            "setup_state": to_jsonable(record.setup_state),
            "setup_created_at": to_jsonable(record.setup_created_at),
            "setup_as_of": to_jsonable(record.setup_as_of),
            "plan_id": record.plan_id,
            "plan_state": to_jsonable(record.plan_state),
            "planning_as_of": to_jsonable(record.planning_as_of),
            "plan_levels": plan_levels,
            "latest_decision": None
            if latest_decision is None
            else latest_decision.to_json_dict(),
            "latest_outcome": None
            if latest_outcome is None
            else _outcome_summary(latest_outcome),
        }


def _outcome_summary(observation) -> dict[str, object]:
    return {
        "id": observation.id,
        "status": observation.status.value,
        "observed_through": to_jsonable(observation.observed_through),
        "entry_reached": observation.entry_reached,
        "stop_reached": observation.stop_reached,
        "targets_reached": list(observation.targets_reached),
        "ambiguous": observation.ambiguous,
        "incomplete": observation.incomplete,
        "mfe_r": None if observation.mfe_r is None else format(observation.mfe_r, "f"),
        "mae_r": None if observation.mae_r is None else format(observation.mae_r, "f"),
    }
