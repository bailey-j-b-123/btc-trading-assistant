"""SQLAlchemy tables for the Step 12 forward-testing ledger.

Five additive tables, entirely separate from the Step 2 candle archive, the
Step 7 Bailey journal, and the derived Step 11 validation reports:

``forward_cycles``
    One immutable row per processed base-candle close (what the deterministic
    Steps 2–6 produced at that boundary, with data health and versions).
``forward_observations``
    One immutable row per candidate (setup) state inside such a cycle, with the
    exact Step 5 snapshot/setup JSON and any Step 6 plan projection.
``forward_paper_plans``
    One immutable frozen PLANNABLE plan projection per setup instance; the only
    place a paper observation can come from. Unique per setup, so a setup that
    stays plannable for many candles is counted once.
``forward_paper_outcomes``
    Append-only deterministic market-observation versions of one paper plan's
    proposed levels, chained through ``supersedes_outcome_id``.
``forward_runner_heartbeats``
    Append-only statements about the runner process (never about the market).

Decimal and UTC storage types are the existing Step 2 ``DecimalText`` and
``UTCDateTime`` decorators, so forward numerics and timestamps are stored
exactly like market data. SQLite immutability triggers for every table are
created by migration ``0004_forward_testing``: a correction is a new row, never
an overwrite. No table here can place an order, hold a balance, or hold a
position — none of those concepts exist in this schema.
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

#: Exact stored cycle vocabulary (mirrors ``CycleStatus``).
CYCLE_STATUS_VALUES = (
    "COMPLETE",
    "MISSING_CANDLE",
    "MISSING_FRAME",
    "INSUFFICIENT_HISTORY",
)

#: Exact stored data-health vocabulary (mirrors ``DataHealth``).
DATA_HEALTH_VALUES = ("CURRENT", "STALE", "INCOMPLETE", "HISTORICAL", "UNKNOWN")

#: Exact stored heartbeat vocabulary (mirrors ``HeartbeatStatus``).
HEARTBEAT_STATUS_VALUES = (
    "STARTED",
    "PROCESSED",
    "IDLE",
    "NO_DATA",
    "ERROR",
    "STOPPED",
)

#: Exact stored setup-state vocabulary (mirrors ``SetupState``).
SETUP_STATE_VALUES = ("NO_SETUP", "WATCH", "QUALIFIED")

#: Exact stored plan-state vocabulary (mirrors ``PlanState``).
PLAN_STATE_VALUES = ("PLANNABLE", "NO_PLAN", "INVALID")

#: Exact stored direction vocabulary (mirrors ``Direction``).
DIRECTION_VALUES = ("bullish", "bearish")

#: Exact stored outcome vocabulary (mirrors Step 7 ``OutcomeStatus``).
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


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return column + " IN (" + ", ".join(f"'{value}'" for value in values) + ")"


class ForwardCycleRow(Base):
    """Immutable record of one processed closed candle."""

    __tablename__ = "forward_cycles"
    __table_args__ = (
        CheckConstraint(
            _in_clause("status", CYCLE_STATUS_VALUES),
            name="ck_forward_cycles_status",
        ),
        CheckConstraint(
            _in_clause("data_health", DATA_HEALTH_VALUES),
            name="ck_forward_cycles_data_health",
        ),
        CheckConstraint(
            "status <> 'COMPLETE' OR snapshot_id IS NOT NULL",
            name="ck_forward_cycles_complete_has_snapshot",
        ),
        CheckConstraint(
            "missing_candle_count >= 0", name="ck_forward_cycles_missing_count"
        ),
        CheckConstraint(
            "staleness_intervals IS NULL OR staleness_intervals >= 0",
            name="ck_forward_cycles_staleness",
        ),
        UniqueConstraint("cycle_id", name="uq_forward_cycles_id"),
        Index(
            "ix_forward_cycles_instrument_time",
            "exchange",
            "symbol",
            "timeframe",
            "as_of",
        ),
        Index("ix_forward_cycles_version", "version_fingerprint"),
    )

    cycle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_health: Mapped[str] = mapped_column(String(16), nullable=False)
    data_health_detail: Mapped[str] = mapped_column(Text(), nullable=False)
    staleness_intervals: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    missing_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    latest_stored_candle: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    snapshot_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    paper_plan_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    setup_state_counts_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_state_counts_json: Mapped[str] = mapped_column(Text(), nullable=False)
    structure_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    pattern_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    strategy_versions_json: Mapped[str] = mapped_column(Text(), nullable=False)
    version_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation_context_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    explanation_manifest_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    explanation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation_headline: Mapped[str | None] = mapped_column(Text(), nullable=True)
    explanation_renderer_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    market_data_json: Mapped[str] = mapped_column(Text(), nullable=False)
    notes_json: Mapped[str] = mapped_column(Text(), nullable=False)


class ForwardObservationRow(Base):
    """Immutable record of one candidate state inside one forward cycle."""

    __tablename__ = "forward_observations"
    __table_args__ = (
        CheckConstraint(
            _in_clause("setup_state", SETUP_STATE_VALUES),
            name="ck_forward_observations_setup_state",
        ),
        CheckConstraint(
            _in_clause("snapshot_state", SETUP_STATE_VALUES),
            name="ck_forward_observations_snapshot_state",
        ),
        CheckConstraint(
            _in_clause("setup_direction", DIRECTION_VALUES),
            name="ck_forward_observations_direction",
        ),
        CheckConstraint(
            "plan_state IS NULL OR " + _in_clause("plan_state", PLAN_STATE_VALUES),
            name="ck_forward_observations_plan_state",
        ),
        CheckConstraint(
            "plan_state IS NULL OR plan_id IS NOT NULL",
            name="ck_forward_observations_plan_id",
        ),
        CheckConstraint(
            "paper_plan_id IS NULL OR plan_state = 'PLANNABLE'",
            name="ck_forward_observations_paper_requires_plannable",
        ),
        CheckConstraint(
            "paper_plan_id IS NULL OR setup_state = 'QUALIFIED'",
            name="ck_forward_observations_paper_requires_qualified",
        ),
        # A recorded no-trade reason means exactly that: no paper trade was
        # created for this observation (a second candidate while one trade is
        # active, or a missed opportunity).
        CheckConstraint(
            "no_trade_reason IS NULL OR paper_plan_id IS NULL",
            name="ck_forward_observations_no_trade_reason_no_paper",
        ),
        Index(
            "ix_forward_observations_instrument_time",
            "exchange",
            "symbol",
            "timeframe",
            "as_of",
        ),
        Index("ix_forward_observations_setup", "setup_id", "as_of"),
        Index("ix_forward_observations_cycle", "cycle_id"),
        Index("ix_forward_observations_paper_plan", "paper_plan_id"),
    )

    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("forward_cycles.cycle_id", ondelete="RESTRICT"),
        nullable=False,
    )
    observation_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    source_timeframes_json: Mapped[str] = mapped_column(Text(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    candle_open_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    snapshot_state: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_id: Mapped[str] = mapped_column(String(256), nullable=False)
    setup_family: Mapped[str] = mapped_column(String(64), nullable=False)
    setup_direction: Mapped[str] = mapped_column(String(16), nullable=False)
    setup_state: Mapped[str] = mapped_column(String(32), nullable=False)
    setup_created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    setup_seed_event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    setup_reference_id: Mapped[str] = mapped_column(String(256), nullable=False)
    setup_terminal_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    setup_ended_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    passed_rules_json: Mapped[str] = mapped_column(Text(), nullable=False)
    failed_rules_json: Mapped[str] = mapped_column(Text(), nullable=False)
    pending_rules_json: Mapped[str] = mapped_column(Text(), nullable=False)
    veto_rules_json: Mapped[str] = mapped_column(Text(), nullable=False)
    setup_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    plan_json: Mapped[str | None] = mapped_column(Text(), nullable=True)
    plan_entry: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    plan_invalidation: Mapped[object | None] = mapped_column(
        DecimalText(), nullable=True
    )
    plan_stop: Mapped[object | None] = mapped_column(DecimalText(), nullable=True)
    plan_risk_per_unit: Mapped[object | None] = mapped_column(
        DecimalText(), nullable=True
    )
    plan_targets_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_target_r_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_config_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    planning_rules_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    paper_plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    no_trade_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    data_health: Mapped[str] = mapped_column(String(16), nullable=False)
    missing_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    market_trend: Mapped[str] = mapped_column(String(32), nullable=False)
    atr_percent_of_price: Mapped[object | None] = mapped_column(
        DecimalText(), nullable=True
    )
    calendar_period: Mapped[str] = mapped_column(String(16), nullable=False)
    structure_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    pattern_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class ForwardPaperPlanRow(Base):
    """One frozen PLANNABLE plan projection per setup instance."""

    __tablename__ = "forward_paper_plans"
    __table_args__ = (
        CheckConstraint(
            _in_clause("direction", DIRECTION_VALUES),
            name="ck_forward_paper_plans_direction",
        ),
        CheckConstraint(
            "risk_per_unit > 0", name="ck_forward_paper_plans_risk_positive"
        ),
        CheckConstraint(
            "observation_horizon_candles >= 1",
            name="ck_forward_paper_plans_horizon",
        ),
        UniqueConstraint(
            "exchange",
            "symbol",
            "timeframe",
            "setup_id",
            name="uq_forward_paper_plans_setup_instance",
        ),
        Index(
            "ix_forward_paper_plans_instrument_time",
            "exchange",
            "symbol",
            "timeframe",
            "plan_as_of",
        ),
        Index("ix_forward_paper_plans_setup", "setup_id"),
    )

    paper_plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    observation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("forward_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    cycle_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("forward_cycles.cycle_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ledger_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    setup_id: Mapped[str] = mapped_column(String(256), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_as_of: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    entry_level: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    stop_level: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    invalidation_level: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    risk_per_unit: Mapped[object] = mapped_column(DecimalText(), nullable=False)
    targets_json: Mapped[str] = mapped_column(Text(), nullable=False)
    target_r_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    planning_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_versions_json: Mapped[str] = mapped_column(Text(), nullable=False)
    version_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    friction_assumptions_json: Mapped[str] = mapped_column(Text(), nullable=False)
    friction_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    forward_parameters_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    observation_horizon_candles: Mapped[int] = mapped_column(Integer(), nullable=False)
    data_health: Mapped[str] = mapped_column(String(16), nullable=False)


class ForwardPaperOutcomeRow(Base):
    """One append-only outcome version of one paper plan's proposed levels."""

    __tablename__ = "forward_paper_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "paper_plan_id", "sequence", name="uq_forward_paper_outcomes_sequence"
        ),
        CheckConstraint("sequence >= 1", name="ck_forward_paper_outcomes_sequence"),
        CheckConstraint(
            "supersedes_outcome_id IS NULL OR "
            "supersedes_outcome_id <> outcome_id",
            name="ck_forward_paper_outcomes_supersedes",
        ),
        CheckConstraint(
            _in_clause("status", OUTCOME_STATUS_VALUES),
            name="ck_forward_paper_outcomes_status",
        ),
        CheckConstraint(
            "incomplete = (missing_candle_count > 0)",
            name="ck_forward_paper_outcomes_incomplete_count",
        ),
        CheckConstraint(
            "ambiguous = 1 OR ambiguity_kind IS NULL",
            name="ck_forward_paper_outcomes_ambiguity_kind",
        ),
        Index("ix_forward_paper_outcomes_status", "status"),
        Index("ix_forward_paper_outcomes_cutoff", "observed_through"),
        Index("ix_forward_paper_outcomes_plan", "paper_plan_id", "sequence"),
    )

    outcome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_plan_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("forward_paper_plans.paper_plan_id", ondelete="RESTRICT"),
        nullable=False,
    )
    observation_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("forward_observations.observation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer(), nullable=False)
    supersedes_outcome_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("forward_paper_outcomes.outcome_id", ondelete="RESTRICT"),
        nullable=True,
    )
    ledger_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    observed_through: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    evaluated_through: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_reached: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    entry_ordered: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    entry_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    stop_reached: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    stop_pre_entry: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    targets_reached_json: Mapped[str] = mapped_column(Text(), nullable=False)
    ambiguous: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    ambiguity_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incomplete: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    missing_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    expected_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    observed_candle_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text(), nullable=False)


class ForwardRunnerHeartbeatRow(Base):
    """One append-only runner-process statement."""

    __tablename__ = "forward_runner_heartbeats"
    __table_args__ = (
        CheckConstraint(
            _in_clause("status", HEARTBEAT_STATUS_VALUES),
            name="ck_forward_runner_heartbeats_status",
        ),
        Index("ix_forward_runner_heartbeats_recorded", "recorded_at"),
        Index(
            "ix_forward_runner_heartbeats_instrument",
            "exchange",
            "symbol",
            "timeframe",
        ),
    )

    heartbeat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    runner_rules_version: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(128), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text(), nullable=False)
    cycles_processed: Mapped[int] = mapped_column(Integer(), nullable=False)
    observations_recorded: Mapped[int] = mapped_column(Integer(), nullable=False)
    paper_plans_created: Mapped[int] = mapped_column(Integer(), nullable=False)
    outcomes_recorded: Mapped[int] = mapped_column(Integer(), nullable=False)
    pending_boundaries: Mapped[int] = mapped_column(Integer(), nullable=False)
    latest_cycle_as_of: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    market_data_json: Mapped[str] = mapped_column(Text(), nullable=False)


__all__ = [
    "CYCLE_STATUS_VALUES",
    "DATA_HEALTH_VALUES",
    "DIRECTION_VALUES",
    "ForwardCycleRow",
    "ForwardObservationRow",
    "ForwardPaperOutcomeRow",
    "ForwardPaperPlanRow",
    "ForwardRunnerHeartbeatRow",
    "HEARTBEAT_STATUS_VALUES",
    "OUTCOME_STATUS_VALUES",
    "PLAN_STATE_VALUES",
    "SETUP_STATE_VALUES",
]
