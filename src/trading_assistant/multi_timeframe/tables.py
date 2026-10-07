"""SQLAlchemy table for the Step 13 multi-timeframe hierarchy ledger.

One additive table, entirely separate from the Step 2 candle archive, the
Step 7 journal, and the Step 12 forward ledger:

``forward_hierarchy_observations``
    One immutable row per evaluated hierarchy decision: the exact per-layer
    snapshots and closed-candle boundaries the hierarchy knew at the decision
    time, the alignment/conflict state, and the one overall decision. SQLite
    immutability triggers are created by migration ``0005``: a new market
    boundary is a new row, never an overwrite of what the system knew earlier.

No table here can place an order, hold a balance, or hold a position — none of
those concepts exist in this schema.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_assistant.database.base import Base
from trading_assistant.market_data.models import UTCDateTime

#: Exact stored decision vocabulary (mirrors ``HierarchyDecision``).
DECISION_VALUES = (
    "no_setup",
    "watch",
    "awaiting_confirmation",
    "awaiting_execution",
    "plannable",
    "invalidated",
)

#: Exact stored alignment vocabulary (mirrors ``HierarchyAlignment``).
ALIGNMENT_VALUES = (
    "aligned",
    "counter_trend",
    "neutral",
    "conflicting",
    "unknown",
)

#: Exact stored evaluation-status vocabulary.
STATUS_VALUES = ("evaluated", "incomplete")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return column + " IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class ForwardHierarchyObservationRow(Base):
    """Immutable record of one evaluated multi-timeframe hierarchy decision."""

    __tablename__ = "forward_hierarchy_observations"
    __table_args__ = (
        CheckConstraint(
            _in_clause("decision", DECISION_VALUES),
            name="ck_hierarchy_observations_decision",
        ),
        CheckConstraint(
            _in_clause("alignment", ALIGNMENT_VALUES),
            name="ck_hierarchy_observations_alignment",
        ),
        CheckConstraint(
            _in_clause("status", STATUS_VALUES),
            name="ck_hierarchy_observations_status",
        ),
        CheckConstraint(
            "counter_trend IN (0, 1)",
            name="ck_hierarchy_observations_counter_trend",
        ),
        UniqueConstraint("observation_id", name="uq_hierarchy_observations_id"),
        UniqueConstraint(
            "exchange",
            "symbol",
            "decision_time",
            "hierarchy_fingerprint",
            name="uq_hierarchy_observations_boundary",
        ),
        Index(
            "ix_hierarchy_observations_instrument_time",
            "exchange",
            "symbol",
            "decision_time",
        ),
        Index(
            "ix_hierarchy_observations_hierarchy",
            "exchange",
            "symbol",
            "hierarchy_fingerprint",
        ),
        Index("ix_hierarchy_observations_decision", "decision"),
        Index(
            "ix_hierarchy_observations_recorded",
            "exchange",
            "symbol",
            "recorded_at",
        ),
    )

    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    hierarchy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_json: Mapped[str] = mapped_column(Text(), nullable=False)
    context_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    context_boundary_open: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    context_boundary_close: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    context_json: Mapped[str] = mapped_column(Text(), nullable=False)
    setup_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    setup_boundary_open: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    setup_boundary_close: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    setup_json: Mapped[str] = mapped_column(Text(), nullable=False)
    confirmation_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmation_boundary_open: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    confirmation_boundary_close: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    confirmation_json: Mapped[str] = mapped_column(Text(), nullable=False)
    execution_timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_boundary_open: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    execution_boundary_close: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    execution_json: Mapped[str] = mapped_column(Text(), nullable=False)
    alignment: Mapped[str] = mapped_column(String(32), nullable=False)
    counter_trend: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons_json: Mapped[str] = mapped_column(Text(), nullable=False)
    strategy_versions_json: Mapped[str] = mapped_column(Text(), nullable=False)
    waiting_for_json: Mapped[str] = mapped_column(Text(), nullable=False)
    invalidated_if_json: Mapped[str] = mapped_column(Text(), nullable=False)
    boundary_json: Mapped[str] = mapped_column(Text(), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


__all__ = [
    "ALIGNMENT_VALUES",
    "DECISION_VALUES",
    "STATUS_VALUES",
    "ForwardHierarchyObservationRow",
]
