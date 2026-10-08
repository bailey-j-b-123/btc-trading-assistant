"""Read-only filtered listing adapter over Step 7 journal rows.

This adapter only issues SELECTs against the immutable Step 7 tables and never
writes. Filtering by latest decision/outcome is done after the record page is
fetched through the existing repository methods, so the adapter reuses Step 7
semantics instead of reinterpreting stored rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from trading_assistant.journaling.models import JournalRecordRow
from trading_assistant.journaling.repository import (
    JournalRepository,
    _record_from_row,  # row decoding reused exactly as Step 8's dataset reader does
)
from trading_assistant.journaling.types import JournalRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    latest_closed_candle_open_time,
    timeframe_to_milliseconds,
)
from trading_assistant.market_structure.snapshot import to_jsonable

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
JOURNAL_CHART_CANDLE_LIMIT = 160

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
    def __init__(
        self,
        engine: Engine,
        repository: JournalRepository,
        candles: CandleRepository | None = None,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.candles = candles

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
        latest_outcome = None if not outcomes else outcomes[-1].observation
        return {
            "record": record.to_json_dict(),
            "setup_snapshot": record.setup_snapshot_payload,
            "plan": record.plan_payload,
            "decisions": [decision.to_json_dict() for decision in decisions],
            "latest_decision": None if not decisions else decisions[-1].to_json_dict(),
            "outcome_versions": [version.to_json_dict() for version in outcomes],
            "latest_outcome": (
                None if latest_outcome is None else latest_outcome.to_json_dict()
            ),
            "market_snapshots": self._market_snapshots(record, latest_outcome),
        }

    def _market_snapshots(self, record: JournalRecord, latest_outcome) -> dict[str, object]:
        """Bounded before/after chart rows from stored candles, with no replay/fill."""

        base = {
            "exchange": record.exchange,
            "symbol": record.symbol,
            "timeframe": record.timeframe,
            "candle_limit": JOURNAL_CHART_CANDLE_LIMIT,
            "source": "stored_closed_candles_only",
            "note": (
                "The before window is a bounded query of stored closed candles up to "
                "the record's setup_as_of boundary. The existing candle store is not "
                "versioned by ingestion time, so this cannot prove which backfilled "
                "historical rows were available when the record was first made. The "
                "after window is strictly limited to the latest recorded outcome's "
                "observed_through boundary. Missing rows remain gaps; no candles are "
                "replayed, filled, or inferred."
            ),
        }
        if self.candles is None:
            return {
                **base,
                "available": False,
                "unavailable_reason": "The stored market-candle repository is not attached.",
                "before": None,
                "after": None,
            }
        try:
            interval_ms = timeframe_to_milliseconds(record.timeframe)
            before_end = latest_closed_candle_open_time(
                record.setup_as_of, record.timeframe
            )
            before_start = before_end - timedelta(
                milliseconds=interval_ms * (JOURNAL_CHART_CANDLE_LIMIT - 1)
            )
            before = self._stored_candle_window(
                record,
                start_time=before_start,
                end_time=before_end,
                label="before",
            )
            after = None
            if latest_outcome is not None:
                observed_through = latest_outcome.observed_through
                # Journal outcomes store an aligned candle OPEN timestamp as
                # observed_through (inclusive), not the candle's close boundary.
                # Reusing it directly keeps the visible AFTER window identical
                # to the market rows the observation actually evaluated.
                after_end = observed_through
                after_start = before_end + timedelta(milliseconds=interval_ms)
                if after_end >= after_start:
                    span_count = (after_end - after_start) // timedelta(milliseconds=interval_ms) + 1
                    bounded_start = max(
                        after_start,
                        after_end - timedelta(
                            milliseconds=interval_ms * (JOURNAL_CHART_CANDLE_LIMIT - 1)
                        ),
                    )
                    after = self._stored_candle_window(
                        record,
                        start_time=bounded_start,
                        end_time=after_end,
                        label="after",
                        observed_through=observed_through,
                        truncated=span_count > JOURNAL_CHART_CANDLE_LIMIT,
                    )
                else:
                    after = {
                        "label": "after",
                        "available": True,
                        "start_time": to_jsonable(after_start),
                        "end_time": to_jsonable(after_end),
                        "observed_through": to_jsonable(observed_through),
                        "candles": [],
                        "gaps": [],
                        "missing_candle_count": 0,
                        "complete": True,
                        "returned_count": 0,
                        "truncated": False,
                    }
            return {
                **base,
                "available": True,
                "unavailable_reason": None,
                "before": before,
                "after": after,
            }
        except (TypeError, ValueError) as error:
            return {
                **base,
                "available": False,
                "unavailable_reason": f"Stored candle window is unavailable: {error}",
                "before": None,
                "after": None,
            }

    def _stored_candle_window(
        self,
        record: JournalRecord,
        *,
        start_time: datetime,
        end_time: datetime,
        label: str,
        observed_through: datetime | None = None,
        truncated: bool = False,
    ) -> dict[str, object]:
        if self.candles is None:
            raise RuntimeError("stored candle repository is unavailable")
        result = self.candles.get_candles(
            exchange=record.exchange,
            symbol=record.symbol,
            timeframe=record.timeframe,
            start_time=start_time,
            end_time=end_time,
        )
        rows = [
            [
                datetime_to_milliseconds(candle.timestamp),
                format(candle.open, "f"),
                format(candle.high, "f"),
                format(candle.low, "f"),
                format(candle.close, "f"),
                format(candle.volume, "f"),
            ]
            for candle in result.candles
        ]
        return {
            "label": label,
            "available": True,
            "start_time": to_jsonable(start_time),
            "end_time": to_jsonable(end_time),
            "observed_through": (
                None if observed_through is None else to_jsonable(observed_through)
            ),
            "candles": rows,
            "gaps": to_jsonable(result.gaps),
            "missing_candle_count": result.missing_candle_count,
            "complete": result.complete,
            "returned_count": len(rows),
            "truncated": truncated,
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
