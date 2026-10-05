"""SQLAlchemy models for Step 7 append-only journal persistence.

Four tables, all additive to the Step 1-2 schema:

``journal_records``
    One immutable record per journaled Step 5 snapshot/setup (and optional
    Step 6 plan), including canonical JSON projections of exactly what was seen.
``journal_decisions``
    Append-only decision rows referencing a journal record, chained through
    ``supersedes_decision_id``; earlier rows are never updated.
``journal_outcomes``
    Append-only deterministic outcome observations of one journaled plan,
    versioned by ``sequence``/``supersedes_outcome_id`` so an observation through
    T1 stays recoverable after one through T2.
``journal_outcome_events``
    The individual first-touch events (entry/stop/target/ambiguous groups)
    belonging to one stored observation.

Decimal and UTC storage types are the existing Step 2 ``DecimalText`` and
``UTCDateTime`` decorators, reused so journal numerics and timestamps are
stored exactly like market data. All tables are protected by SQLite
immutability triggers created in migration ``0003_journal``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trading_assistant.database.base import Base
from trading_assistant.market_data.models import DecimalText, UTCDateTime

#: Exact stored decision vocabulary (mirrors ``DecisionState``).
DECISION_VALUES = ("PENDING", "ACCEPTED", "REJECTED", "SKIPPED")

#: Exact stored outcome vocabulary (mirrors ``OutcomeStatus``).
OUTCOME_STATUS_VALUES = (
    "ENTRY_NOT_REACHED",
    "INVALIDATED_BEFORE_ENTRY",
    "STOPPED",
    "STOPPED_AFTER_TARGETS",
    "TARGETS_REACHED",
    "OPEN_AT_CUTOFF",
    "AMBIGUOUS",
    "INCOMPLETE_DATA",
)

#: Exact stored event vocabulary (mirrors ``OutcomeEventKind``).
EVENT_KIND_VALUES = ("entry", "stop", "target")

#: Exact stored event ordering vocabulary (mirrors ``OutcomeEventOrdering``).
EVENT_ORDERING_VALUES = ("pre_entry", "ordered", "ambiguous")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return column + " IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class JournalRecordRow(Base):
    """Immutable journaled source snapshot (and optional plan) row."""

    __tablename__ = "journal_records"
    __table_args__ = (
        CheckConstraint(
            "(record_kind = 'snapshot' AND setup_id IS NULL) OR "
            "(record_kind = 'setup' AND setup_id IS NOT NULL)",
            name="ck_journal_records_kind_setup",
        ),
        CheckConstraint(
            "record_kind <> 'snapshot' OR plan_id IS NULL",
            name="ck_journal_records_snapshot_has_no_plan",
        ),
        CheckConstraint(
            "(plan_id IS NULL AND plan_state IS NULL AND planning_as_of IS NULL "
            "AND plan_json IS NULL AND plan_config_fingerprint IS NULL "
            "AND planning_rules_version IS NULL) OR "
            "(plan_id IS NOT NULL AND plan_state IS NOT NULL "
            "AND planning_as_of IS NOT NULL AND plan_json IS NOT NULL "
            "AND plan_config_fingerprint IS NOT NULL "
            "AND planning_rules_version IS NOT NULL)",
            name="ck_journal_records_plan_complete",
        ),
        Index(
            "ix_journal_records_instrument",
            "exchange",
            "symbol",
            "timeframe",
        ),
        Index("ix_journal_records_setup_id", "setup_id"),
        Index("ix_journal_records_setup_as_of", "setup_as_of"),
        Index("ix_journal_records_setup_snapshot_id", "setup_snapshot_id"),
    )

    journal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    journal_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    source_timeframes_json: Mapped[str] = mapped_column(Text, nullable=False)
    setup_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    setup_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    setup_state: Mapped[str] = mapped_column(String(32), nullable=False)
    setup_created_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    setup_as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    setup_seed_event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    setup_reference_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    setup_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    planning_as_of: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    plan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_config_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    planning_rules_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


class JournalDecisionRow(Base):
    """One append-only decision row for one journal record."""

    __tablename__ = "journal_decisions"
    __table_args__ = (
        UniqueConstraint(
            "journal_id", "sequence", name="uq_journal_decisions_record_sequence"
        ),
        CheckConstraint(
            _in_clause("decision", DECISION_VALUES),
            name="ck_journal_decisions_decision",
        ),
        CheckConstraint("sequence >= 1", name="ck_journal_decisions_sequence"),
        CheckConstraint(
            "supersedes_decision_id IS NULL OR supersedes_decision_id <> decision_id",
            name="ck_journal_decisions_supersedes",
        ),
        Index("ix_journal_decisions_record_time", "journal_id", "decided_at"),
        Index("ix_journal_decisions_decision", "decision"),
        Index("ix_journal_decisions_setup_id", "setup_id"),
        Index("ix_journal_decisions_plan_id", "plan_id"),
    )

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    journal_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("journal_records.journal_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_decision_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("journal_decisions.decision_id", ondelete="RESTRICT"),
        nullable=True,
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    record_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    setup_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    setup_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    setup_state: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    source_timeframes_json: Mapped[str] = mapped_column(Text, nullable=False)
    setup_as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    planning_as_of: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    setup_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_config_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    setup_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    planning_rules_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    setup_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)


class JournalOutcomeRow(Base):
    """One append-only deterministic outcome observation row."""

    __tablename__ = "journal_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "journal_id", "sequence", name="uq_journal_outcomes_record_sequence"
        ),
        CheckConstraint(
            _in_clause("status", OUTCOME_STATUS_VALUES),
            name="ck_journal_outcomes_status",
        ),
        CheckConstraint("sequence >= 1", name="ck_journal_outcomes_sequence"),
        CheckConstraint(
            "supersedes_outcome_id IS NULL OR supersedes_outcome_id <> outcome_id",
            name="ck_journal_outcomes_supersedes",
        ),
        CheckConstraint(
            "incomplete = (missing_candle_count > 0)",
            name="ck_journal_outcomes_incomplete_count",
        ),
        CheckConstraint(
            "ambiguous = 1 OR ambiguity_kind IS NULL",
            name="ck_journal_outcomes_ambiguity_kind",
        ),
        CheckConstraint(
            "ambiguous = 0 OR ambiguity_kind IS NOT NULL",
            name="ck_journal_outcomes_ambiguity_required",
        ),
        CheckConstraint(
            "entry_ordered <= entry_reached",
            name="ck_journal_outcomes_entry_ordering",
        ),
        CheckConstraint(
            "stop_pre_entry <= stop_reached",
            name="ck_journal_outcomes_stop_ordering",
        ),
        Index("ix_journal_outcomes_status", "status"),
        Index("ix_journal_outcomes_cutoff", "observed_through"),
        Index("ix_journal_outcomes_plan_id", "plan_id"),
        Index("ix_journal_outcomes_setup_id", "setup_id"),
    )

    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    journal_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("journal_records.journal_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_outcome_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("journal_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=True,
    )
    observation_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_level: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    stop_level: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    risk_per_unit: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    target_levels_json: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observed_through: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    evaluated_through: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_reached: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_ordered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entry_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    entry_candle_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_reached: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_pre_entry: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    targets_reached_json: Mapped[str] = mapped_column(Text, nullable=False)
    targets_pre_entry_json: Mapped[str] = mapped_column(Text, nullable=False)
    first_touch_order_json: Mapped[str] = mapped_column(Text, nullable=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ambiguity_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ambiguity_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    incomplete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    missing_candle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_ranges_json: Mapped[str] = mapped_column(Text, nullable=False)
    expected_candle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_candle_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_low: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    evaluated_low_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    evaluated_high: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    evaluated_high_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    post_entry_low: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    post_entry_high: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    mfe_price_move: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    mae_price_move: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    mfe_r: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    mae_r: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class JournalOutcomeEventRow(Base):
    """One first-touch event belonging to a stored outcome observation."""

    __tablename__ = "journal_outcome_events"
    __table_args__ = (
        UniqueConstraint(
            "outcome_id",
            "sequence",
            "kind",
            "target_index",
            name="uq_journal_outcome_events_touch",
        ),
        CheckConstraint(
            _in_clause("kind", EVENT_KIND_VALUES),
            name="ck_journal_outcome_events_kind",
        ),
        CheckConstraint(
            _in_clause("ordering", EVENT_ORDERING_VALUES),
            name="ck_journal_outcome_events_ordering",
        ),
        CheckConstraint(
            "(kind = 'target' AND target_index >= 0) OR "
            "(kind <> 'target' AND target_index = -1)",
            name="ck_journal_outcome_events_target_index",
        ),
        CheckConstraint(
            "co_touched IN (0, 1)", name="ck_journal_outcome_events_co_touch"
        ),
        Index("ix_journal_outcome_events_ordering", "outcome_id", "ordering"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outcome_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("journal_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_index: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    level_value: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    candle_timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    candle_index: Mapped[int] = mapped_column(Integer, nullable=False)
    ordering: Mapped[str] = mapped_column(String(16), nullable=False)
    co_touched: Mapped[bool] = mapped_column(Boolean, nullable=False)
