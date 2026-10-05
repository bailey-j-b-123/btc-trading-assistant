"""Add the append-only Step 7 journal (records, decisions, outcomes, events).

Revision ID: 0003_journal
Revises: 0002_ohlcv_candles
Create Date: 2026-10-05

Purely additive: four new tables plus SQLite immutability triggers. Existing
tables (including the append-only ``ohlcv_candles`` archive) are never touched
or dropped, and the downgrade refuses to run while journal rows exist.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_journal"
down_revision: str | None = "0002_ohlcv_candles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOURNAL_TABLES = (
    "journal_outcome_events",
    "journal_outcomes",
    "journal_decisions",
    "journal_records",
)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _create_immutability_triggers() -> None:
    """Block UPDATE and DELETE on every journal table (SQLite only).

    Append-only design is enforced, not merely intended: a correction is a new
    row chained through ``supersedes_*``, never an overwrite.
    """

    if not _is_sqlite():
        return
    for table in _JOURNAL_TABLES:
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
    for table in _JOURNAL_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation.lower()}")


def upgrade() -> None:
    """Create only the new journal tables, indexes and append-only triggers."""

    op.create_table(
        "journal_records",
        sa.Column("journal_id", sa.String(length=64), nullable=False),
        sa.Column("record_kind", sa.String(length=16), nullable=False),
        sa.Column("journal_rules_version", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("source_timeframes_json", sa.Text(), nullable=False),
        sa.Column("setup_id", sa.String(length=256), nullable=True),
        sa.Column("setup_family", sa.String(length=64), nullable=True),
        sa.Column("setup_direction", sa.String(length=16), nullable=True),
        sa.Column("setup_state", sa.String(length=32), nullable=False),
        sa.Column("setup_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("setup_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_seed_event_id", sa.String(length=256), nullable=True),
        sa.Column("setup_reference_id", sa.String(length=256), nullable=True),
        sa.Column("setup_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("setup_rules_version", sa.String(length=64), nullable=False),
        sa.Column("setup_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("setup_snapshot_json", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("plan_state", sa.String(length=16), nullable=True),
        sa.Column("planning_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("plan_config_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("planning_rules_version", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "(record_kind = 'snapshot' AND setup_id IS NULL) OR "
            "(record_kind = 'setup' AND setup_id IS NOT NULL)",
            name="ck_journal_records_kind_setup",
        ),
        sa.CheckConstraint(
            "record_kind <> 'snapshot' OR plan_id IS NULL",
            name="ck_journal_records_snapshot_has_no_plan",
        ),
        sa.CheckConstraint(
            "(plan_id IS NULL AND plan_state IS NULL AND planning_as_of IS NULL "
            "AND plan_json IS NULL AND plan_config_fingerprint IS NULL "
            "AND planning_rules_version IS NULL) OR "
            "(plan_id IS NOT NULL AND plan_state IS NOT NULL "
            "AND planning_as_of IS NOT NULL AND plan_json IS NOT NULL "
            "AND plan_config_fingerprint IS NOT NULL "
            "AND planning_rules_version IS NOT NULL)",
            name="ck_journal_records_plan_complete",
        ),
        sa.PrimaryKeyConstraint("journal_id"),
    )
    op.create_index(
        "ix_journal_records_instrument",
        "journal_records",
        ["exchange", "symbol", "timeframe"],
    )
    op.create_index("ix_journal_records_setup_id", "journal_records", ["setup_id"])
    op.create_index(
        "ix_journal_records_setup_as_of", "journal_records", ["setup_as_of"]
    )
    op.create_index(
        "ix_journal_records_setup_snapshot_id",
        "journal_records",
        ["setup_snapshot_id"],
    )

    op.create_table(
        "journal_decisions",
        sa.Column("decision_id", sa.String(length=64), nullable=False),
        sa.Column("journal_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("supersedes_decision_id", sa.String(length=64), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decision_rules_version", sa.String(length=64), nullable=False),
        sa.Column("record_kind", sa.String(length=16), nullable=False),
        sa.Column("setup_id", sa.String(length=256), nullable=True),
        sa.Column("plan_id", sa.String(length=64), nullable=True),
        sa.Column("setup_family", sa.String(length=64), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("setup_state", sa.String(length=32), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("source_timeframes_json", sa.Text(), nullable=False),
        sa.Column("setup_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planning_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("setup_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("plan_config_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("setup_rules_version", sa.String(length=64), nullable=False),
        sa.Column("planning_rules_version", sa.String(length=64), nullable=True),
        sa.Column("setup_snapshot_id", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "decision IN ('PENDING', 'ACCEPTED', 'REJECTED', 'SKIPPED')",
            name="ck_journal_decisions_decision",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_journal_decisions_sequence"),
        sa.CheckConstraint(
            "supersedes_decision_id IS NULL OR supersedes_decision_id <> decision_id",
            name="ck_journal_decisions_supersedes",
        ),
        sa.ForeignKeyConstraint(
            ["journal_id"], ["journal_records.journal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["journal_decisions.decision_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint(
            "journal_id", "sequence", name="uq_journal_decisions_record_sequence"
        ),
    )
    op.create_index(
        "ix_journal_decisions_record_time",
        "journal_decisions",
        ["journal_id", "decided_at"],
    )
    op.create_index("ix_journal_decisions_decision", "journal_decisions", ["decision"])
    op.create_index("ix_journal_decisions_setup_id", "journal_decisions", ["setup_id"])
    op.create_index("ix_journal_decisions_plan_id", "journal_decisions", ["plan_id"])

    op.create_table(
        "journal_outcomes",
        sa.Column("outcome_id", sa.String(length=64), nullable=False),
        sa.Column("journal_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("supersedes_outcome_id", sa.String(length=64), nullable=True),
        sa.Column("observation_rules_version", sa.String(length=64), nullable=False),
        sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("setup_id", sa.String(length=256), nullable=True),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("entry_level", sa.Text(), nullable=False),
        sa.Column("stop_level", sa.Text(), nullable=False),
        sa.Column("risk_per_unit", sa.Text(), nullable=False),
        sa.Column("target_levels_json", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entry_reached", sa.Boolean(), nullable=False),
        sa.Column("entry_ordered", sa.Boolean(), nullable=False),
        sa.Column("entry_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_candle_index", sa.Integer(), nullable=True),
        sa.Column("stop_reached", sa.Boolean(), nullable=False),
        sa.Column("stop_pre_entry", sa.Boolean(), nullable=False),
        sa.Column("stop_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("targets_reached_json", sa.Text(), nullable=False),
        sa.Column("targets_pre_entry_json", sa.Text(), nullable=False),
        sa.Column("first_touch_order_json", sa.Text(), nullable=False),
        sa.Column("ambiguous", sa.Boolean(), nullable=False),
        sa.Column("ambiguity_kind", sa.String(length=64), nullable=True),
        sa.Column("ambiguity_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("incomplete", sa.Boolean(), nullable=False),
        sa.Column("missing_candle_count", sa.Integer(), nullable=False),
        sa.Column("missing_ranges_json", sa.Text(), nullable=False),
        sa.Column("expected_candle_count", sa.Integer(), nullable=False),
        sa.Column("observed_candle_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_low", sa.Text(), nullable=True),
        sa.Column("evaluated_low_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_high", sa.Text(), nullable=True),
        sa.Column(
            "evaluated_high_timestamp", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("post_entry_low", sa.Text(), nullable=True),
        sa.Column("post_entry_high", sa.Text(), nullable=True),
        sa.Column("mfe_price_move", sa.Text(), nullable=True),
        sa.Column("mae_price_move", sa.Text(), nullable=True),
        sa.Column("mfe_r", sa.Text(), nullable=True),
        sa.Column("mae_r", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ENTRY_NOT_REACHED', 'INVALIDATED_BEFORE_ENTRY', "
            "'STOPPED', 'STOPPED_AFTER_TARGETS', 'TARGETS_REACHED', "
            "'OPEN_AT_CUTOFF', 'AMBIGUOUS', 'INCOMPLETE_DATA')",
            name="ck_journal_outcomes_status",
        ),
        sa.CheckConstraint("sequence >= 1", name="ck_journal_outcomes_sequence"),
        sa.CheckConstraint(
            "supersedes_outcome_id IS NULL OR supersedes_outcome_id <> outcome_id",
            name="ck_journal_outcomes_supersedes",
        ),
        sa.CheckConstraint(
            "incomplete = (missing_candle_count > 0)",
            name="ck_journal_outcomes_incomplete_count",
        ),
        sa.CheckConstraint(
            "ambiguous = 1 OR ambiguity_kind IS NULL",
            name="ck_journal_outcomes_ambiguity_kind",
        ),
        sa.CheckConstraint(
            "ambiguous = 0 OR ambiguity_kind IS NOT NULL",
            name="ck_journal_outcomes_ambiguity_required",
        ),
        sa.CheckConstraint(
            "entry_ordered <= entry_reached",
            name="ck_journal_outcomes_entry_ordering",
        ),
        sa.CheckConstraint(
            "stop_pre_entry <= stop_reached",
            name="ck_journal_outcomes_stop_ordering",
        ),
        sa.ForeignKeyConstraint(
            ["journal_id"], ["journal_records.journal_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_outcome_id"],
            ["journal_outcomes.outcome_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("outcome_id"),
        sa.UniqueConstraint(
            "journal_id", "sequence", name="uq_journal_outcomes_record_sequence"
        ),
    )
    op.create_index("ix_journal_outcomes_status", "journal_outcomes", ["status"])
    op.create_index(
        "ix_journal_outcomes_cutoff", "journal_outcomes", ["observed_through"]
    )
    op.create_index("ix_journal_outcomes_plan_id", "journal_outcomes", ["plan_id"])
    op.create_index("ix_journal_outcomes_setup_id", "journal_outcomes", ["setup_id"])

    op.create_table(
        "journal_outcome_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("outcome_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("target_index", sa.Integer(), nullable=False),
        sa.Column("level_value", sa.Text(), nullable=False),
        sa.Column("candle_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candle_index", sa.Integer(), nullable=False),
        sa.Column("ordering", sa.String(length=16), nullable=False),
        sa.Column("co_touched", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('entry', 'stop', 'target')",
            name="ck_journal_outcome_events_kind",
        ),
        sa.CheckConstraint(
            "ordering IN ('pre_entry', 'ordered', 'ambiguous')",
            name="ck_journal_outcome_events_ordering",
        ),
        sa.CheckConstraint(
            "(kind = 'target' AND target_index >= 0) OR "
            "(kind <> 'target' AND target_index = -1)",
            name="ck_journal_outcome_events_target_index",
        ),
        sa.CheckConstraint(
            "co_touched IN (0, 1)", name="ck_journal_outcome_events_co_touch"
        ),
        sa.ForeignKeyConstraint(
            ["outcome_id"], ["journal_outcomes.outcome_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "outcome_id",
            "sequence",
            "kind",
            "target_index",
            name="uq_journal_outcome_events_touch",
        ),
    )
    op.create_index(
        "ix_journal_outcome_events_ordering",
        "journal_outcome_events",
        ["outcome_id", "ordering"],
    )

    _create_immutability_triggers()


def downgrade() -> None:
    """Refuse to drop journal history; otherwise remove only Step 7 objects."""

    connection = op.get_bind()
    for table in _JOURNAL_TABLES:
        has_rows = connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first()
        if has_rows is not None:
            raise RuntimeError(
                f"Refusing to downgrade: {table} contains historical journal "
                "rows. Preserve/export the journal and use a forward migration "
                "instead."
            )
    _drop_immutability_triggers()
    op.drop_table("journal_outcome_events")
    op.drop_table("journal_outcomes")
    op.drop_table("journal_decisions")
    op.drop_table("journal_records")
