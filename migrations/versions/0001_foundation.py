"""Establish the initial schema-version baseline without application tables.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-10-05
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record the baseline; there are no domain tables in this foundation."""


def downgrade() -> None:
    """The baseline creates no application objects and has nothing to remove."""
