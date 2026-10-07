"""Add the append-only Step 13 multi-timeframe hierarchy ledger.

Revision ID: 0005_multi_timeframe_hierarchy
Revises: 0004_forward_testing
Create Date: 2026-10-07

Purely additive: one new table plus SQLite immutability triggers. Existing
tables (the append-only ``ohlcv_candles`` market archive, every Step 7 journal
table, and every Step 12 forward-ledger table) are never touched, altered, or
dropped, so all existing forward/journal/candle history remains valid and
readable. The downgrade refuses to run while hierarchy rows exist.

``forward_hierarchy_observations`` stores one immutable row per evaluated
hierarchy decision: the exact per-layer snapshots (4H context, 1H setup, 15M
confirmation, 5M execution) and their closed-candle boundaries, the
alignment/conflict state, and the one overall hierarchy decision. Identity is
a deterministic content fingerprint that includes the hierarchy
configuration fingerprint, so a restart or a repeated boundary can never
duplicate a row, and a different hierarchy can never collide with older rows.

The schema deliberately contains no order, fill, position, balance, leverage,
margin, or sizing concept: Step 13 records decisions, never execution.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_multi_timeframe_hierarchy"
down_revision: str | None = "0004_forward_testing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HIERARCHY_TABLES = ("forward_hierarchy_observations",)

_DECISIONS = (
    "no_setup",
    "watch",
    "awaiting_confirmation",
    "awaiting_execution",
    "plannable",
    "invalidated",
)
_ALIGNMENTS = (
    "aligned",
    "counter_trend",
    "neutral",
    "conflicting",
    "unknown",
)
_STATUSES = ("evaluated", "incomplete")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    return column + " IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _create_immutability_triggers() -> None:
    """Block UPDATE and DELETE on the hierarchy table (SQLite only).

    Append-only is enforced, not merely intended: a new market boundary is a
    new row, and no earlier hierarchy observation can ever be rewritten by
    later candles, a restart, or a code change.
    """

    if not _is_sqlite():
        return
    for table in _HIERARCHY_TABLES:
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
    for table in _HIERARCHY_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation.lower()}")


def upgrade() -> None:
    """Create only the new hierarchy-ledger table, indexes and triggers."""

    op.create_table(
        "forward_hierarchy_observations",
        sa.Column("observation_id", sa.String(length=64), nullable=False),
        sa.Column("rules_version", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("decision_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hierarchy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("hierarchy_json", sa.Text(), nullable=False),
        sa.Column("context_timeframe", sa.String(length=16), nullable=False),
        sa.Column("context_boundary_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_boundary_close", sa.DateTime(timezone=True), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("setup_timeframe", sa.String(length=16), nullable=False),
        sa.Column("setup_boundary_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_boundary_close", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup_json", sa.Text(), nullable=False),
        sa.Column("confirmation_timeframe", sa.String(length=16), nullable=False),
        sa.Column("confirmation_boundary_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_boundary_close", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmation_json", sa.Text(), nullable=False),
        sa.Column("execution_timeframe", sa.String(length=16), nullable=False),
        sa.Column("execution_boundary_open", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_boundary_close", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_json", sa.Text(), nullable=False),
        sa.Column("alignment", sa.String(length=32), nullable=False),
        sa.Column("counter_trend", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("strategy_versions_json", sa.Text(), nullable=False),
        sa.Column("waiting_for_json", sa.Text(), nullable=False),
        sa.Column("invalidated_if_json", sa.Text(), nullable=False),
        sa.Column("boundary_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            _in_clause("decision", _DECISIONS),
            name="ck_hierarchy_observations_decision",
        ),
        sa.CheckConstraint(
            _in_clause("alignment", _ALIGNMENTS),
            name="ck_hierarchy_observations_alignment",
        ),
        sa.CheckConstraint(
            _in_clause("status", _STATUSES),
            name="ck_hierarchy_observations_status",
        ),
        sa.CheckConstraint(
            "counter_trend IN (0, 1)",
            name="ck_hierarchy_observations_counter_trend",
        ),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint("observation_id", name="uq_hierarchy_observations_id"),
    )
    op.create_index(
        "ix_hierarchy_observations_instrument_time",
        "forward_hierarchy_observations",
        ["exchange", "symbol", "decision_time"],
    )
    op.create_index(
        "ix_hierarchy_observations_hierarchy",
        "forward_hierarchy_observations",
        ["exchange", "symbol", "hierarchy_fingerprint"],
    )
    op.create_index(
        "ix_hierarchy_observations_decision",
        "forward_hierarchy_observations",
        ["decision"],
    )
    op.create_index(
        "ix_hierarchy_observations_recorded",
        "forward_hierarchy_observations",
        ["exchange", "symbol", "recorded_at"],
    )

    _create_immutability_triggers()


def downgrade() -> None:
    """Refuse to drop hierarchy history; otherwise remove only Step 13 objects."""

    connection = op.get_bind()
    for table in _HIERARCHY_TABLES:
        has_rows = connection.exec_driver_sql(f"SELECT 1 FROM {table} LIMIT 1").first()
        if has_rows is not None:
            raise RuntimeError(
                f"Refusing to downgrade: {table} contains recorded hierarchy "
                "history. Preserve/export the hierarchy ledger and use a "
                "forward migration instead."
            )
    _drop_immutability_triggers()
    op.drop_table("forward_hierarchy_observations")
