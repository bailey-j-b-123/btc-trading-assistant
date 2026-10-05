"""Add append-only OHLCV candle storage.

Revision ID: 0002_ohlcv_candles
Revises: 0001_foundation
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_ohlcv_candles"
down_revision: str | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create only the new candle table; existing tables and rows are untouched."""

    op.create_table(
        "ohlcv_candles",
        sa.Column("exchange", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=128), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Text(), nullable=False),
        sa.Column("high", sa.Text(), nullable=False),
        sa.Column("low", sa.Text(), nullable=False),
        sa.Column("close", sa.Text(), nullable=False),
        sa.Column("volume", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("exchange", "symbol", "timeframe", "timestamp"),
    )


def downgrade() -> None:
    """Refuse to downgrade this data-bearing migration rather than delete history."""

    connection = op.get_bind()
    has_candles = connection.exec_driver_sql(
        "SELECT 1 FROM ohlcv_candles LIMIT 1"
    ).first()
    if has_candles is not None:
        raise RuntimeError(
            "Refusing to downgrade: ohlcv_candles contains historical data. "
            "Preserve/export the data and use a forward migration instead."
        )
    op.drop_table("ohlcv_candles")
