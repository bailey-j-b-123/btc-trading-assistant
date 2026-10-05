"""SQLAlchemy model and exact persistence types for OHLCV candles."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from trading_assistant.database.base import Base


class DecimalText(TypeDecorator[Decimal]):
    """Persist Decimal values as exact base-10 text instead of SQLite REAL."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("OHLCV values must be finite Decimal instances")
        return format(value, "f")

    def process_result_value(self, value: str | None, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("stored OHLCV value must use exact decimal text")
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("stored OHLCV value is not a valid decimal string") from exc
        if not parsed.is_finite():
            raise ValueError("stored OHLCV value must be finite")
        return parsed


class UTCDateTime(TypeDecorator[datetime]):
    """Normalize aware datetimes to UTC for SQLite and return them as aware UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle timestamp must be timezone-aware")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class OHLCVCandleRecord(Base):
    """Persisted closed candle, identified by exchange, symbol, timeframe, and UTC open time."""

    __tablename__ = "ohlcv_candles"

    exchange: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(128), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(16), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), primary_key=True)
    open: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    high: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    low: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    close: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
    volume: Mapped[Decimal] = mapped_column(DecimalText(), nullable=False)
