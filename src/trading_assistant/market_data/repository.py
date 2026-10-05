"""Durable candle writes and chronological queries."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.market_data.errors import HistoricalCandleConflict
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    milliseconds_to_datetime,
    require_utc_datetime,
    timeframe_anchor_milliseconds,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle, CandleQueryResult
from trading_assistant.market_data.validation import (
    CandleValidationError,
    validate_ohlcv_rows,
)

_INSERT_CHUNK_SIZE = 100


def _chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _identity(candle: Candle) -> tuple[str, str, str, datetime]:
    timestamp = require_utc_datetime(candle.timestamp, field_name="candle.timestamp")
    return candle.exchange, candle.symbol, candle.timeframe, timestamp


def _record_values(candle: Candle) -> dict[str, Any]:
    return {
        "exchange": candle.exchange,
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "timestamp": require_utc_datetime(candle.timestamp, field_name="candle.timestamp"),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _as_candle(record: OHLCVCandleRecord) -> Candle:
    return Candle(
        exchange=record.exchange,
        symbol=record.symbol,
        timeframe=record.timeframe,
        timestamp=require_utc_datetime(record.timestamp, field_name="stored timestamp"),
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
    )


def _aligned_bound(timestamp_ms: int, timeframe: str, *, round_up: bool) -> int:
    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    quotient, remainder = divmod(timestamp_ms - anchor_ms, interval_ms)
    if round_up and remainder:
        quotient += 1
    return quotient * interval_ms + anchor_ms


def _lower_bound_milliseconds(value: datetime) -> int:
    """Round a sub-millisecond lower bound up so no candle is included too early."""

    timestamp_ms = datetime_to_milliseconds(value)
    if value.microsecond % 1_000:
        timestamp_ms += 1
    return timestamp_ms


class CandleRepository:
    """Repository that inserts new candles and refuses to rewrite historical values."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError("CandleRepository currently requires a SQLite SQLAlchemy engine")
        self._sessions = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

    def latest_timestamp(self, *, exchange: str, symbol: str, timeframe: str) -> datetime | None:
        with self._sessions() as session:
            timestamp = session.scalar(
                select(OHLCVCandleRecord.timestamp)
                .where(
                    OHLCVCandleRecord.exchange == exchange,
                    OHLCVCandleRecord.symbol == symbol,
                    OHLCVCandleRecord.timeframe == timeframe,
                )
                .order_by(OHLCVCandleRecord.timestamp.desc())
                .limit(1)
            )
        return timestamp

    def insert_unchanged_or_new(self, candles: Iterable[Candle]) -> tuple[int, int]:
        """Insert missing keys, count exact repeats, and roll back value conflicts."""

        batch = list(candles)
        if not batch:
            return 0, 0

        identities = [_identity(candle) for candle in batch]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate candle identities in one database write batch")

        inserted_count = 0
        with self._sessions.begin() as session:
            for values in _chunks([_record_values(candle) for candle in batch], _INSERT_CHUNK_SIZE):
                statement = sqlite_insert(OHLCVCandleRecord).values(values).on_conflict_do_nothing(
                    index_elements=[
                        OHLCVCandleRecord.exchange,
                        OHLCVCandleRecord.symbol,
                        OHLCVCandleRecord.timeframe,
                        OHLCVCandleRecord.timestamp,
                    ]
                )
                result = session.execute(statement)
                inserted_count += int(result.rowcount or 0)

            stored_records: dict[tuple[str, str, str, datetime], OHLCVCandleRecord] = {}
            for key_chunk in _chunks(list(identities), _INSERT_CHUNK_SIZE):
                stored = session.scalars(
                    select(OHLCVCandleRecord).where(
                        tuple_(
                            OHLCVCandleRecord.exchange,
                            OHLCVCandleRecord.symbol,
                            OHLCVCandleRecord.timeframe,
                            OHLCVCandleRecord.timestamp,
                        ).in_(key_chunk)
                    )
                ).all()
                stored_records.update({_identity(_as_candle(record)): record for record in stored})

            for candle, identity in zip(batch, identities):
                existing = stored_records.get(identity)
                if existing is None:
                    raise RuntimeError("candle insert did not result in a stored row")
                if any(
                    getattr(existing, field) != getattr(candle, field)
                    for field in ("open", "high", "low", "close", "volume")
                ):
                    raise HistoricalCandleConflict(
                        "exchange returned different values for an existing candle key "
                        f"({identity[0]}, {identity[1]}, {identity[2]}, {identity[3].isoformat()}); "
                        "stored history was not changed"
                    )

        return inserted_count, len(batch) - inserted_count

    def get_candles(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> CandleQueryResult:
        """Return UTC candles in order and report missing opens in the requested range."""

        normalized_start = (
            require_utc_datetime(start_time, field_name="start_time") if start_time is not None else None
        )
        normalized_end = require_utc_datetime(end_time, field_name="end_time") if end_time is not None else None
        if normalized_start is not None and normalized_end is not None and normalized_start > normalized_end:
            raise ValueError("start_time must not be after end_time")

        statement = select(OHLCVCandleRecord).where(
            OHLCVCandleRecord.exchange == exchange,
            OHLCVCandleRecord.symbol == symbol,
            OHLCVCandleRecord.timeframe == timeframe,
        )
        if normalized_start is not None:
            statement = statement.where(OHLCVCandleRecord.timestamp >= normalized_start)
        if normalized_end is not None:
            statement = statement.where(OHLCVCandleRecord.timestamp <= normalized_end)
        statement = statement.order_by(OHLCVCandleRecord.timestamp.asc())

        with self._sessions() as session:
            records = session.scalars(statement).all()
        stored_candles = [_as_candle(record) for record in records]
        raw_rows = [
            [
                datetime_to_milliseconds(candle.timestamp),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            ]
            for candle in stored_candles
        ]

        if normalized_start is not None:
            expected_start_ms = _aligned_bound(
                _lower_bound_milliseconds(normalized_start), timeframe, round_up=True
            )
        elif stored_candles:
            expected_start_ms = datetime_to_milliseconds(stored_candles[0].timestamp)
        else:
            expected_start_ms = None

        if normalized_end is not None:
            expected_end_ms = _aligned_bound(
                datetime_to_milliseconds(normalized_end), timeframe, round_up=False
            )
        elif stored_candles:
            expected_end_ms = datetime_to_milliseconds(stored_candles[-1].timestamp)
        else:
            expected_end_ms = None

        expected_start = None
        expected_end = None
        if (
            expected_start_ms is not None
            and expected_end_ms is not None
            and expected_start_ms <= expected_end_ms
        ):
            expected_start = milliseconds_to_datetime(expected_start_ms)
            expected_end = milliseconds_to_datetime(expected_end_ms)
        elif expected_start_ms is not None and expected_end_ms is not None:
            return CandleQueryResult(exchange, symbol, timeframe, (), ())

        report = validate_ohlcv_rows(
            raw_rows,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        if report.issues:
            raise CandleValidationError(report)
        return CandleQueryResult(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            candles=report.candles,
            gaps=report.gaps,
        )
