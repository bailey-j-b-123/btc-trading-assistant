"""Add the append-only Step 12 forward-testing ledger.

Revision ID: 0004_forward_testing
Revises: 0003_journal
Create Date: 2026-10-06

Purely additive: five new tables plus SQLite immutability triggers. Existing
tables (including the append-only ``ohlcv_candles`` market archive and every
Step 7 journal table) are never touched, altered, or dropped, and the downgrade
refuses to run while forward rows exist.

The schema deliberately contains no order, fill, position, balance, leverage,
margin, or sizing concept: Step 12 records observations of public closed
candles, never execution.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_forward_testing"
down_revision: str | None = "0003_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORWARD_TABLES = (
    "forward_paper_outcomes",
    "forward_paper_plans",
    "forward_observations",
    "forward_cycles",
    "forward_runner_heartbeats",
)

_CYCLE_STATUSES = ("COMPLETE", "MISSING_CANDLE", "MISSING_FRAME", "INSUFFICIENT_HISTORY")
_DATA_HEALTH = ("CURRENT", "STALE", "INCOMPLETE", "HISTORICAL", "UNKNOWN")
_HEARTBEAT_STATUSES = ("STARTED", "PROCESSED", "IDLE", "NO_DATA", "ERROR", "STOPPED")
_SETUP_STATES = ("NO_SETUP", "WATCH", "QUALIFIED")
_PLAN_STATES = ("PLANNABLE", "NO_PLAN", "INVALID")
_DIRECTIONS = ("bullish", "bearish")
_OUTCOME_STATUSES = (
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


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _create_immutability_triggers() -> None:
    """Block UPDATE and DELETE on every forward table (SQLite only).

    Append-only is enforced, not merely intended: an outcome progresses through
    a new versioned row, never an overwrite, and no earlier forward record can
    ever be rewritten by later candles, a restart, or a code change.
    """

    if not _is_sqlite():
        return
    for table in _FORWARD_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_{operation.lower()} "
                f"BEFORE {operation} ON {table} "
                "BEGIN "
                f"SELECT RAISE(ABORT, '{table} is append-only; "
                "record a new row instead'); "
                "END"
            )


def _drop_immutability_triggers() -> None:
    if not _is_sqlite():
        return
    for table in _FORWARD_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation.lower()}")


def upgrade() -> None:
    """Create only the new forward-ledger tables, indexes and triggers."""

    op.create_table(
        "forward_cycles",
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("rules_version", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("data_health", sa.String(length=16), nullable=False),
        sa.Column("data_health_detail", sa.Text(), nullable=False),
        sa.Column("staleness_intervals", sa.Integer(), nullable=True),
        sa.Column("missing_candle_count", sa.Integer(), nullable=False),
        sa.Column("latest_stored_candle", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot_state", sa.String(length=32), nullable=True),
        sa.Column("snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("snapshot_json", sa.Text(), nullable=True),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("paper_plan_count", sa.Integer(), nullable=False),
        sa.Column("setup_state_counts_json", sa.Text(), nullable=False),
        sa.Column("plan_state_counts_json", sa.Text(), nullable=False),
        sa.Column("structure_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("pattern_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("source_candle_count", sa.Integer(), nullable=False),
        sa.Column("strategy_versions_json", sa.Text(), nullable=False),
        sa.Column("version_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "explanation_context_fingerprint", sa.String(length=64), nullable=True
        ),
        sa.Column(
            "explanation_manifest_fingerprint", sa.String(length=64), nullable=True
        ),
        sa.Column("explanation_id", sa.String(length=64), nullable=True),
        sa.Column("explanation_headline", sa.Text(), nullable=True),
        sa.Column("explanation_renderer_version", sa.String(length=64), nullable=True),
        sa.Column("market_data_json", sa.Text(), nullable=False),
        sa.Column("notes_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            _in_clause("status", _CYCLE_STATUSES), name="ck_forward_cycles_status"
        ),
        sa.CheckConstraint(
            _in_clause("data_health", _DATA_HEALTH),
            name="ck_forward_cycles_data_health",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR snapshot_id IS NOT NULL",
            name="ck_forward_cycles_complete_has_snapshot",
        ),
        sa.CheckConstraint(
            "missing_candle_count >= 0", name="ck_forward_cycles_missing_count"
        ),
        sa.CheckConstraint(
            "staleness_intervals IS NULL OR staleness_intervals >= 0",
            name="ck_forward_cycles_staleness",
        ),
        sa.PrimaryKeyConstraint("cycle_id"),
        sa.UniqueConstraint("cycle_id", name="uq_forward_cycles_id"),
    )
    op.create_index(
        "ix_forward_cycles_instrument_time",
        "forward_cycles",
        ["exchange", "symbol", "timeframe", "as_of"],
    )
    op.create_index(
        "ix_forward_cycles_version", "forward_cycles", ["version_fingerprint"]
    )

    op.create_table(
        "forward_observations",
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("observation_rules_version", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("source_timeframes_json", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_state", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("setup_id", sa.String(length=256), nullable=False),
        sa.Column("setup_family", sa.String(length=64), nullable=False),
        sa.Column("setup_direction", sa.String(length=16), nullable=False),
        sa.Column("setup_state", sa.String(length=32), nullable=False),
        sa.Column("setup_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_seed_event_id", sa.String(length=256), nullable=False),
        sa.Column("setup_reference_id", sa.String(length=256), nullable=False),
        sa.Column("setup_terminal_reason", sa.Text(), nullable=True),
        sa.Column("setup_ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("passed_rules_json", sa.Text(), nullable=False),
        sa.Column("failed_rules_json", sa.Text(), nullable=False),
        sa.Column("pending_rules_json", sa.Text(), nullable=False),
        sa.Column("veto_rules_json", sa.Text(), nullable=False),
        sa.Column("setup_json", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("plan_state", sa.String(length=16), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("plan_entry", sa.Text(), nullable=True),
        sa.Column("plan_invalidation", sa.Text(), nullable=True),
        sa.Column("plan_stop", sa.Text(), nullable=True),
        sa.Column("plan_risk_per_unit", sa.Text(), nullable=True),
        sa.Column("plan_targets_json", sa.Text(), nullable=False),
        sa.Column("plan_target_r_json", sa.Text(), nullable=False),
        sa.Column("plan_config_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("planning_rules_version", sa.String(length=64), nullable=True),
        sa.Column("paper_plan_id", sa.String(length=64), nullable=True),
        sa.Column("data_health", sa.String(length=16), nullable=False),
        sa.Column("missing_candle_count", sa.Integer(), nullable=False),
        sa.Column("market_trend", sa.String(length=32), nullable=False),
        sa.Column("atr_percent_of_price", sa.Text(), nullable=True),
        sa.Column("calendar_period", sa.String(length=16), nullable=False),
        sa.Column("structure_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("pattern_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("version_fingerprint", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            _in_clause("setup_state", _SETUP_STATES),
            name="ck_forward_observations_setup_state",
        ),
        sa.CheckConstraint(
            _in_clause("snapshot_state", _SETUP_STATES),
            name="ck_forward_observations_snapshot_state",
        ),
        sa.CheckConstraint(
            _in_clause("setup_direction", _DIRECTIONS),
            name="ck_forward_observations_direction",
        ),
        sa.CheckConstraint(
            "plan_state IS NULL OR " + _in_clause("plan_state", _PLAN_STATES),
            name="ck_forward_observations_plan_state",
        ),
        sa.CheckConstraint(
            "plan_state IS NULL OR plan_id IS NOT NULL",
            name="ck_forward_observations_plan_id",
        ),
        sa.CheckConstraint(
            "plan_state <> 'PLANNABLE' OR paper_plan_id IS NOT NULL",
            name="ck_forward_observations_plannable_is_paper",
        ),
        sa.CheckConstraint(
            "paper_plan_id IS NULL OR setup_state = 'QUALIFIED'",
            name="ck_forward_observations_paper_requires_qualified",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["forward_cycles.cycle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("observation_id"),
    )
    op.create_index(
        "ix_forward_observations_instrument_time",
        "forward_observations",
        ["exchange", "symbol", "timeframe", "as_of"],
    )
    op.create_index(
        "ix_forward_observations_setup", "forward_observations", ["setup_id", "as_of"]
    )
    op.create_index(
        "ix_forward_observations_cycle", "forward_observations", ["cycle_id"]
    )
    op.create_index(
        "ix_forward_observations_paper_plan", "forward_observations", ["paper_plan_id"]
    )

    op.create_table(
        "forward_paper_plans",
        sa.Column("paper_plan_id", sa.String(length=64), nullable=False),
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.String(length=64), nullable=False),
        sa.Column("ledger_rules_version", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("setup_id", sa.String(length=256), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("family", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("plan_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_level", sa.Text(), nullable=False),
        sa.Column("stop_level", sa.Text(), nullable=False),
        sa.Column("invalidation_level", sa.Text(), nullable=False),
        sa.Column("risk_per_unit", sa.Text(), nullable=False),
        sa.Column("targets_json", sa.Text(), nullable=False),
        sa.Column("target_r_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("plan_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("planning_rules_version", sa.String(length=64), nullable=False),
        sa.Column("strategy_versions_json", sa.Text(), nullable=False),
        sa.Column("version_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("friction_assumptions_json", sa.Text(), nullable=False),
        sa.Column("friction_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("forward_parameters_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observation_horizon_candles", sa.Integer(), nullable=False),
        sa.Column("data_health", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            _in_clause("direction", _DIRECTIONS),
            name="ck_forward_paper_plans_direction",
        ),
        sa.CheckConstraint(
            "risk_per_unit > 0", name="ck_forward_paper_plans_risk_positive"
        ),
        sa.CheckConstraint(
            "observation_horizon_candles >= 1",
            name="ck_forward_paper_plans_horizon",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["forward_observations.observation_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["forward_cycles.cycle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("paper_plan_id"),
        sa.UniqueConstraint(
            "exchange",
            "symbol",
            "timeframe",
            "setup_id",
            name="uq_forward_paper_plans_setup_instance",
        ),
    )
    op.create_index(
        "ix_forward_paper_plans_instrument_time",
        "forward_paper_plans",
        ["exchange", "symbol", "timeframe", "plan_as_of"],
    )
    op.create_index("ix_forward_paper_plans_setup", "forward_paper_plans", ["setup_id"])

    op.create_table(
        "forward_paper_outcomes",
        sa.Column("outcome_id", sa.String(length=64), nullable=False),
        sa.Column("paper_plan_id", sa.String(length=64), nullable=False),
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("supersedes_outcome_id", sa.String(length=64), nullable=True),
        sa.Column("ledger_rules_version", sa.String(length=64), nullable=False),
        sa.Column("observation_rules_version", sa.String(length=64), nullable=False),
        sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entry_reached", sa.Boolean(), nullable=False),
        sa.Column("entry_ordered", sa.Boolean(), nullable=False),
        sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reached", sa.Boolean(), nullable=False),
        sa.Column("stop_pre_entry", sa.Boolean(), nullable=False),
        sa.Column("targets_reached_json", sa.Text(), nullable=False),
        sa.Column("ambiguous", sa.Boolean(), nullable=False),
        sa.Column("ambiguity_kind", sa.String(length=64), nullable=True),
        sa.Column("incomplete", sa.Boolean(), nullable=False),
        sa.Column("missing_candle_count", sa.Integer(), nullable=False),
        sa.Column("expected_candle_count", sa.Integer(), nullable=False),
        sa.Column("observed_candle_count", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_forward_paper_outcomes_sequence"),
        sa.CheckConstraint(
            "supersedes_outcome_id IS NULL OR supersedes_outcome_id <> outcome_id",
            name="ck_forward_paper_outcomes_supersedes",
        ),
        sa.CheckConstraint(
            _in_clause("status", _OUTCOME_STATUSES),
            name="ck_forward_paper_outcomes_status",
        ),
        sa.CheckConstraint(
            "incomplete = (missing_candle_count > 0)",
            name="ck_forward_paper_outcomes_incomplete_count",
        ),
        sa.CheckConstraint(
            "ambiguous = 1 OR ambiguity_kind IS NULL",
            name="ck_forward_paper_outcomes_ambiguity_kind",
        ),
        sa.ForeignKeyConstraint(
            ["paper_plan_id"], ["forward_paper_plans.paper_plan_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["forward_observations.observation_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_outcome_id"],
            ["forward_paper_outcomes.outcome_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint(
            "paper_plan_id", "sequence", name="uq_forward_paper_outcomes_sequence"
        ),
    )
    op.create_index(
        "ix_forward_paper_outcomes_status", "forward_paper_outcomes", ["status"]
    )
    op.create_index(
        "ix_forward_paper_outcomes_cutoff", "forward_paper_outcomes", ["observed_through"]
    )
    op.create_index(
        "ix_forward_paper_outcomes_plan",
        "forward_paper_outcomes",
        ["paper_plan_id", "sequence"],
    )

    op.create_table(
        "forward_runner_heartbeats",
        sa.Column("heartbeat_id", sa.String(length=64), nullable=False),
        sa.Column("runner_rules_version", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("cycles_processed", sa.Integer(), nullable=False),
        sa.Column("observations_recorded", sa.Integer(), nullable=False),
        sa.Column("paper_plans_created", sa.Integer(), nullable=False),
        sa.Column("outcomes_recorded", sa.Integer(), nullable=False),
        sa.Column("pending_boundaries", sa.Integer(), nullable=False),
        sa.Column("latest_cycle_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("market_data_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            _in_clause("status", _HEARTBEAT_STATUSES),
            name="ck_forward_runner_heartbeats_status",
        ),
        sa.PrimaryKeyConstraint("heartbeat_id"),
    )
    op.create_index(
        "ix_forward_runner_heartbeats_recorded",
        "forward_runner_heartbeats",
        ["recorded_at"],
    )
    op.create_index(
        "ix_forward_runner_heartbeats_instrument",
        "forward_runner_heartbeats",
        ["exchange", "symbol", "timeframe"],
    )

    _create_immutability_triggers()


def downgrade() -> None:
    """Refuse to drop forward history; otherwise remove only Step 12 objects."""

    connection = op.get_bind()
    for table in _FORWARD_TABLES:
        has_rows = connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first()
        if has_rows is not None:
            raise RuntimeError(
                f"Refusing to downgrade: {table} contains recorded forward "
                "history. Preserve/export the forward ledger and use a forward "
                "migration instead."
            )
    _drop_immutability_triggers()
    op.drop_table("forward_paper_outcomes")
    op.drop_table("forward_paper_plans")
    op.drop_table("forward_observations")
    op.drop_table("forward_cycles")
    op.drop_table("forward_runner_heartbeats")
