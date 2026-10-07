"""Record the deterministic forward no-trade reasons (and the constraint they need).

Revision ID: 0006_forward_no_trade_reason
Revises: 0005_multi_timeframe_hierarchy
Create Date: 2026-10-08

Adds one nullable ``no_trade_reason`` column to ``forward_observations`` and
replaces the CHECK constraint that made it unwritable.

The old constraint ``ck_forward_observations_plannable_is_paper`` required every
``PLANNABLE`` observation to carry a ``paper_plan_id``. The approved policy is
the opposite for a refused candidate: at most one unresolved paper trade exists
per instrument, so a second plannable candidate at the same close is a
``PLANNABLE`` plan with *no* paper trade, recorded with its deterministic reason
("NO TRADE — BTC paper trade already active."), and a monitored setup whose
decision-time entry has moved below the mandatory reward-to-risk floor is
recorded as MISSED. SQLite cannot drop a constraint in place, so this revision
rebuilds ``forward_observations`` (Alembic batch mode) with the same columns and
the corrected constraint set:

* dropped: ``ck_forward_observations_plannable_is_paper``
* added: ``ck_forward_observations_paper_requires_plannable``
  (``paper_plan_id IS NULL OR plan_state = 'PLANNABLE'`` — a paper trade still
  only ever exists for a plannable plan)
* added: ``ck_forward_observations_no_trade_reason_no_paper``
  (``no_trade_reason IS NULL OR paper_plan_id IS NULL`` — a reason means no
  paper trade was created)

Every existing row is copied verbatim and no other table is touched. Because
``forward_paper_plans`` references ``forward_observations`` with
``ON DELETE RESTRICT``, the implicit delete of the old table performed by the
batch rebuild would fail on any ledger that already has a paper plan;
``PRAGMA foreign_keys`` is therefore switched off for the rebuild through an
autocommit window (SQLite only, see ``_set_foreign_keys``), and a scoped
``PRAGMA foreign_key_check`` then proves no paper plan was orphaned. The rebuild also drops the table's Step 12
append-only UPDATE/DELETE triggers, so this revision recreates them immediately
(SQLite only, exactly as ``0004_forward_testing`` created them): recorded
observations stay immutable.

The downgrade refuses while any recorded reason exists (that would destroy
information the ledger was written to keep) and otherwise restores the previous
column set and constraint, again recreating the triggers.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_forward_no_trade_reason"
down_revision: str | None = "0005_multi_timeframe_hierarchy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "forward_observations"
_COLUMN = "no_trade_reason"
_DROPPED_CONSTRAINT = "ck_forward_observations_plannable_is_paper"
_ADDED_CONSTRAINTS = (
    (
        "ck_forward_observations_paper_requires_plannable",
        "paper_plan_id IS NULL OR plan_state = 'PLANNABLE'",
    ),
    (
        "ck_forward_observations_no_trade_reason_no_paper",
        f"{_COLUMN} IS NULL OR paper_plan_id IS NULL",
    ),
)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _set_foreign_keys(enabled: bool) -> None:
    """Turn FK enforcement on/off outside the migration transaction (SQLite only).

    ``forward_paper_plans`` references ``forward_observations`` with
    ``ON DELETE RESTRICT``, so the implicit delete SQLite performs when the
    rebuilt table is dropped would fail on any ledger that already has a paper
    plan. SQLite ignores this pragma inside a transaction, so it is applied
    through an autocommit window; the setting then holds for the rebuild's own
    transaction on this one migration connection, and is restored immediately
    afterwards. The scoped ``PRAGMA foreign_key_check`` below proves the rebuild
    left no dangling reference, and the application's own connections always set
    ``foreign_keys=ON`` explicitly, so nothing is weakened for normal operation.
    """

    if not _is_sqlite():
        return
    with op.get_context().autocommit_block():
        op.execute(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def _assert_no_dangling_references() -> None:
    if not _is_sqlite():
        return
    connection = op.get_bind()
    violations = connection.exec_driver_sql(
        "PRAGMA foreign_key_check(forward_paper_plans)"
    ).fetchall()
    if violations:
        raise RuntimeError(
            "Refusing to finish the forward-observation rebuild: paper plans "
            f"reference missing observations: {violations[:5]}"
        )


def _recreate_immutability_triggers() -> None:
    """Restore the append-only UPDATE/DELETE triggers after the rebuild."""

    if not _is_sqlite():
        return
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_{_TABLE}_no_{operation.lower()} "
            f"BEFORE {operation} ON {_TABLE} "
            "BEGIN "
            f"SELECT RAISE(ABORT, '{_TABLE} is append-only; "
            "record a new row instead'); "
            "END"
        )


def upgrade() -> None:
    """Add the reason column and replace the plan-needs-paper constraint."""

    _set_foreign_keys(False)
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
        batch.drop_constraint(_DROPPED_CONSTRAINT, type_="check")
        for name, condition in _ADDED_CONSTRAINTS:
            batch.create_check_constraint(name, condition)
    _set_foreign_keys(True)
    _assert_no_dangling_references()
    _recreate_immutability_triggers()


def downgrade() -> None:
    """Refuse to destroy recorded reasons; otherwise restore the old shape."""

    connection = op.get_bind()
    has_reasons = connection.exec_driver_sql(
        f"SELECT 1 FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL LIMIT 1"
    ).first()
    if has_reasons is not None:
        raise RuntimeError(
            f"Refusing to downgrade: {_TABLE} contains recorded no-trade reasons. "
            "Preserve/export the forward ledger and use a forward migration instead."
        )
    _set_foreign_keys(False)
    with op.batch_alter_table(_TABLE) as batch:
        for name, _ in _ADDED_CONSTRAINTS:
            batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(
            _DROPPED_CONSTRAINT,
            "plan_state <> 'PLANNABLE' OR paper_plan_id IS NOT NULL",
        )
        batch.drop_column(_COLUMN)
    _set_foreign_keys(True)
    _assert_no_dangling_references()
    _recreate_immutability_triggers()
