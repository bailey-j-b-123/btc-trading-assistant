"""Durable append-only hierarchy ledger reads and writes over SQLite.

The repository only ever inserts. Re-recording the exact same deterministic
evaluation is a verified no-op, so a restart, a repeated pass, or the same
decision boundary processed twice cannot create a duplicate observation, and a
different hierarchy configuration cannot collide with an older row (the
hierarchy fingerprint is part of the identity).

Nothing in this layer knows about orders, balances, positions, leverage, or
sizing: those concepts do not exist in the schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.multi_timeframe.errors import (
    HierarchyConflict,
    MultiTimeframeError,
)
from trading_assistant.multi_timeframe.models import HierarchyObservation
from trading_assistant.multi_timeframe.tables import ForwardHierarchyObservationRow

#: Columns that belong to the deterministic identity of one observation.
#: ``recorded_at`` is write-time audit metadata and is excluded on purpose.
_IDENTITY_FIELDS = tuple(
    name
    for name in ForwardHierarchyObservationRow.__table__.columns.keys()
    if name != "recorded_at"
)


def _values(observation: HierarchyObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "rules_version": observation.rules_version,
        "exchange": observation.exchange,
        "symbol": observation.symbol,
        "decision_time": observation.decision_time,
        "hierarchy_fingerprint": observation.hierarchy_fingerprint,
        "hierarchy_json": observation.hierarchy_json,
        "context_timeframe": observation.context_timeframe,
        "context_boundary_open": observation.context_boundary_open,
        "context_boundary_close": observation.context_boundary_close,
        "context_json": observation.context_json,
        "setup_timeframe": observation.setup_timeframe,
        "setup_boundary_open": observation.setup_boundary_open,
        "setup_boundary_close": observation.setup_boundary_close,
        "setup_json": observation.setup_json,
        "confirmation_timeframe": observation.confirmation_timeframe,
        "confirmation_boundary_open": observation.confirmation_boundary_open,
        "confirmation_boundary_close": observation.confirmation_boundary_close,
        "confirmation_json": observation.confirmation_json,
        "execution_timeframe": observation.execution_timeframe,
        "execution_boundary_open": observation.execution_boundary_open,
        "execution_boundary_close": observation.execution_boundary_close,
        "execution_json": observation.execution_json,
        "alignment": observation.alignment,
        "counter_trend": bool(observation.counter_trend),
        "decision": observation.decision,
        "status": observation.status,
        "reasons_json": observation.reasons_json,
        "strategy_versions_json": observation.strategy_versions_json,
        "waiting_for_json": observation.waiting_for_json,
        "invalidated_if_json": observation.invalidated_if_json,
        "boundary_json": observation.boundary_json,
        "snapshot_json": observation.snapshot_json,
        "recorded_at": observation.recorded_at,
    }


def _from_row(row: ForwardHierarchyObservationRow) -> HierarchyObservation:
    return HierarchyObservation(
        observation_id=row.observation_id,
        rules_version=row.rules_version,
        exchange=row.exchange,
        symbol=row.symbol,
        decision_time=row.decision_time,
        hierarchy_fingerprint=row.hierarchy_fingerprint,
        hierarchy_json=row.hierarchy_json,
        context_timeframe=row.context_timeframe,
        context_boundary_open=row.context_boundary_open,
        context_boundary_close=row.context_boundary_close,
        context_json=row.context_json,
        setup_timeframe=row.setup_timeframe,
        setup_boundary_open=row.setup_boundary_open,
        setup_boundary_close=row.setup_boundary_close,
        setup_json=row.setup_json,
        confirmation_timeframe=row.confirmation_timeframe,
        confirmation_boundary_open=row.confirmation_boundary_open,
        confirmation_boundary_close=row.confirmation_boundary_close,
        confirmation_json=row.confirmation_json,
        execution_timeframe=row.execution_timeframe,
        execution_boundary_open=row.execution_boundary_open,
        execution_boundary_close=row.execution_boundary_close,
        execution_json=row.execution_json,
        alignment=row.alignment,
        counter_trend=bool(row.counter_trend),
        decision=row.decision,
        status=row.status,
        reasons_json=row.reasons_json,
        strategy_versions_json=row.strategy_versions_json,
        waiting_for_json=row.waiting_for_json,
        invalidated_if_json=row.invalidated_if_json,
        boundary_json=row.boundary_json,
        snapshot_json=row.snapshot_json,
        recorded_at=row.recorded_at,
    )


def _verify_unchanged(
    row: ForwardHierarchyObservationRow,
    values: dict[str, Any],
    *,
    what: str,
) -> None:
    """Refuse to silently accept a different evaluation under one identity."""

    mismatches: list[str] = []
    for name in _IDENTITY_FIELDS:
        stored = getattr(row, name)
        incoming = values[name]
        if isinstance(stored, datetime) and isinstance(incoming, datetime):
            if stored != incoming:
                mismatches.append(name)
        elif isinstance(stored, bool) or isinstance(incoming, bool):
            if bool(stored) != bool(incoming):
                mismatches.append(name)
        elif stored != incoming:
            mismatches.append(name)
    if mismatches:
        raise HierarchyConflict(
            f"{what} is already stored with different deterministic content in "
            f"field(s): {', '.join(sorted(mismatches))}"
        )


class HierarchyLedgerRepository:
    """SQLite-backed immutable multi-timeframe hierarchy ledger."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError(
                "HierarchyLedgerRepository currently requires a SQLite SQLAlchemy engine"
            )
        self._sessions = sessionmaker(
            bind=engine, class_=Session, expire_on_commit=False
        )

    # ------------------------------------------------------------------
    # Writes (append-only)
    # ------------------------------------------------------------------

    def record_observation(
        self, observation: HierarchyObservation
    ) -> tuple[HierarchyObservation, bool]:
        """Insert one observation, or return the identical already-stored one.

        Returns ``(observation, created)``: ``created`` is ``False`` when this
        exact deterministic evaluation was already recorded (a restart, a
        repeated pass, or a duplicate boundary), which is what keeps hierarchy
        observations unique per decision boundary and hierarchy version.
        """

        values = _values(observation)
        with self._sessions.begin() as session:
            insert_result = session.execute(
                sqlite_insert(ForwardHierarchyObservationRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["observation_id"])
            )
            # ``rowcount`` is the write itself reporting whether the row
            # landed: comparing ``recorded_at`` instead would misreport a
            # re-recorded row as created whenever two passes share one clock
            # instant.
            created = insert_result.rowcount == 1
            row = session.get(ForwardHierarchyObservationRow, observation.observation_id)
            if row is None:
                raise MultiTimeframeError(
                    f"hierarchy observation {observation.observation_id} was not stored"
                )
            _verify_unchanged(
                row,
                values,
                what=f"hierarchy observation {observation.observation_id}",
            )
            return _from_row(row), created

    # ------------------------------------------------------------------
    # Reads (SELECT only)
    # ------------------------------------------------------------------

    def observations(
        self,
        *,
        exchange: str,
        symbol: str,
        limit: int | None = None,
        newest_first: bool = False,
        decision: str | None = None,
    ) -> tuple[HierarchyObservation, ...]:
        statement = select(ForwardHierarchyObservationRow).where(
            ForwardHierarchyObservationRow.exchange == exchange,
            ForwardHierarchyObservationRow.symbol == symbol,
        )
        if decision is not None:
            statement = statement.where(ForwardHierarchyObservationRow.decision == decision)
        statement = statement.order_by(
            ForwardHierarchyObservationRow.decision_time.desc()
            if newest_first
            else ForwardHierarchyObservationRow.decision_time.asc(),
            ForwardHierarchyObservationRow.observation_id.asc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_from_row(row) for row in rows)

    def latest_observation(
        self, *, exchange: str, symbol: str, hierarchy_fingerprint: str | None = None
    ) -> HierarchyObservation | None:
        statement = (
            select(ForwardHierarchyObservationRow)
            .where(
                ForwardHierarchyObservationRow.exchange == exchange,
                ForwardHierarchyObservationRow.symbol == symbol,
            )
            .order_by(
                ForwardHierarchyObservationRow.decision_time.desc(),
                ForwardHierarchyObservationRow.observation_id.desc(),
            )
            .limit(1)
        )
        if hierarchy_fingerprint is not None:
            statement = select(ForwardHierarchyObservationRow).where(
                ForwardHierarchyObservationRow.exchange == exchange,
                ForwardHierarchyObservationRow.symbol == symbol,
                ForwardHierarchyObservationRow.hierarchy_fingerprint == hierarchy_fingerprint,
            ).order_by(
                ForwardHierarchyObservationRow.decision_time.desc(),
                ForwardHierarchyObservationRow.observation_id.desc(),
            ).limit(1)
        with self._sessions() as session:
            row = session.scalars(statement).first()
            return None if row is None else _from_row(row)

    def observation_at(
        self,
        *,
        exchange: str,
        symbol: str,
        decision_time: datetime,
        hierarchy_fingerprint: str,
    ) -> HierarchyObservation | None:
        """The stored observation for one exact boundary and hierarchy."""

        with self._sessions() as session:
            row = session.scalars(
                select(ForwardHierarchyObservationRow).where(
                    ForwardHierarchyObservationRow.exchange == exchange,
                    ForwardHierarchyObservationRow.symbol == symbol,
                    ForwardHierarchyObservationRow.decision_time == decision_time,
                    ForwardHierarchyObservationRow.hierarchy_fingerprint == hierarchy_fingerprint,
                )
            ).first()
            return None if row is None else _from_row(row)

    def counts(
        self, *, exchange: str, symbol: str, hierarchy_fingerprint: str | None = None
    ) -> dict[str, int]:
        """Exact stored row counts (never a fabricated state)."""

        statement = select(func.count()).select_from(ForwardHierarchyObservationRow).where(
            ForwardHierarchyObservationRow.exchange == exchange,
            ForwardHierarchyObservationRow.symbol == symbol,
        )
        if hierarchy_fingerprint is not None:
            statement = statement.where(
                ForwardHierarchyObservationRow.hierarchy_fingerprint == hierarchy_fingerprint
            )
        with self._sessions() as session:
            total = int(session.scalar(statement) or 0)
            by_decision: dict[str, int] = {}
            rows = session.execute(
                select(
                    ForwardHierarchyObservationRow.decision,
                    func.count(),
                )
                .where(
                    ForwardHierarchyObservationRow.exchange == exchange,
                    ForwardHierarchyObservationRow.symbol == symbol,
                )
                .group_by(ForwardHierarchyObservationRow.decision)
            ).all()
            for decision, count in rows:
                by_decision[decision] = int(count)
        return {"observations": total, "by_decision": by_decision}


__all__ = ["HierarchyLedgerRepository"]
