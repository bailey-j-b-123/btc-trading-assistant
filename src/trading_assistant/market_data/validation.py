"""Deterministic parsing and validation for CCXT OHLCV rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    milliseconds_to_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle, CandleGap


@dataclass(frozen=True, slots=True)
class CandleValidationIssue:
    """One explicitly reported validation failure."""

    code: str
    message: str
    row_index: int | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class CandleValidationReport:
    """Accepted candles and all detected row/range quality problems."""

    candles: tuple[Candle, ...]
    issues: tuple[CandleValidationIssue, ...]
    gaps: tuple[CandleGap, ...]
    rejected_count: int
    excluded_open_count: int
    excluded_range_count: int
    received_count: int

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def complete(self) -> bool:
        return not self.gaps

    @property
    def missing_candle_count(self) -> int:
        return sum(gap.missing_count for gap in self.gaps)


class CandleValidationError(ValueError):
    """Raised when downloaded or persisted candle values fail validation."""

    def __init__(self, report: CandleValidationReport) -> None:
        self.report = report
        counts = Counter(issue.code for issue in report.issues)
        summary = ", ".join(f"{code}={count}" for code, count in sorted(counts.items()))
        super().__init__(
            f"OHLCV validation rejected {report.rejected_count} of "
            f"{report.received_count} rows ({summary})"
        )


def parse_timestamp_milliseconds(value: Any) -> int:
    """Parse a CCXT Unix-millisecond timestamp without binary-float math."""

    if value is None or isinstance(value, bool):
        raise ValueError("timestamp is missing or invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("timestamp is missing or invalid") from exc
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ValueError("timestamp must be a finite integer number of milliseconds")
    try:
        return int(parsed)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp is outside the supported range") from exc


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is missing or invalid")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} is missing or invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _gap_ranges(
    timestamps_ms: Iterable[int],
    *,
    start_ms: int,
    end_ms: int,
    interval_ms: int,
) -> tuple[CandleGap, ...]:
    gaps: list[CandleGap] = []
    cursor = start_ms
    for timestamp_ms in sorted(set(timestamps_ms)):
        if timestamp_ms < cursor:
            continue
        if timestamp_ms > cursor:
            missing_count = (timestamp_ms - cursor) // interval_ms
            if missing_count:
                gaps.append(
                    CandleGap(
                        start=milliseconds_to_datetime(cursor),
                        end=milliseconds_to_datetime(timestamp_ms - interval_ms),
                        missing_count=missing_count,
                    )
                )
        cursor = timestamp_ms + interval_ms
    if cursor <= end_ms:
        gaps.append(
            CandleGap(
                start=milliseconds_to_datetime(cursor),
                end=milliseconds_to_datetime(end_ms),
                missing_count=((end_ms - cursor) // interval_ms) + 1,
            )
        )
    return tuple(gaps)


def validate_ohlcv_rows(
    rows: Iterable[Any],
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
    closed_through: datetime | None = None,
) -> CandleValidationReport:
    """Parse and validate a sequence of CCXT rows without sorting or repairing it.

    ``expected_start`` and ``expected_end`` define an inclusive open-time range
    for gap reporting. ``closed_through`` excludes any still-forming candle.
    Missing timestamps are reported as gaps; no values are synthesized.
    """

    interval_ms = timeframe_to_milliseconds(timeframe)
    start_ms = (
        datetime_to_milliseconds(expected_start, field_name="expected_start")
        if expected_start is not None
        else None
    )
    end_ms = (
        datetime_to_milliseconds(expected_end, field_name="expected_end")
        if expected_end is not None
        else None
    )
    closed_ms = (
        datetime_to_milliseconds(closed_through, field_name="closed_through")
        if closed_through is not None
        else None
    )
    if (start_ms is None) != (end_ms is None):
        raise ValueError("expected_start and expected_end must be provided together")
    if start_ms is not None and end_ms is not None:
        if expected_start.microsecond or expected_end.microsecond:
            raise ValueError("expected range timestamps must have whole-second precision")
        if start_ms > end_ms:
            raise ValueError("expected_start must not be after expected_end")
        if not is_timeframe_aligned(start_ms, timeframe) or not is_timeframe_aligned(end_ms, timeframe):
            raise ValueError("expected range must align to the requested timeframe")

    try:
        row_list = list(rows)
    except TypeError:
        row_list = [rows]

    issues: list[CandleValidationIssue] = []
    accepted: list[Candle] = []
    rejected_rows: set[int] = set()
    seen_timestamps: set[int] = set()
    previous_timestamp: int | None = None
    excluded_open_count = 0
    excluded_range_count = 0

    def reject(index: int, code: str, message: str, timestamp: datetime | None = None) -> None:
        issues.append(CandleValidationIssue(code, message, index, timestamp))
        rejected_rows.add(index)

    for row_index, row in enumerate(row_list):
        if not isinstance(row, (list, tuple)) or isinstance(row, (str, bytes)) or len(row) < 6:
            reject(row_index, "malformed_row", "row must contain timestamp and five OHLCV values")
            continue

        try:
            timestamp_ms = parse_timestamp_milliseconds(row[0])
            timestamp = milliseconds_to_datetime(timestamp_ms)
        except (ValueError, OverflowError, OSError):
            reject(row_index, "invalid_timestamp", "timestamp is not a supported Unix-millisecond value")
            continue

        if not is_timeframe_aligned(timestamp_ms, timeframe):
            reject(row_index, "unaligned_timestamp", "timestamp is not aligned to the timeframe", timestamp)
            continue

        row_has_error = False
        if timestamp_ms in seen_timestamps:
            reject(row_index, "duplicate_timestamp", "timestamp appears more than once", timestamp)
            row_has_error = True
        if previous_timestamp is not None and timestamp_ms < previous_timestamp:
            reject(row_index, "out_of_order", "candle is earlier than the preceding response row", timestamp)
            row_has_error = True
        seen_timestamps.add(timestamp_ms)
        previous_timestamp = timestamp_ms

        if closed_ms is not None and timestamp_ms > closed_ms:
            excluded_open_count += 1
            continue
        if start_ms is not None and end_ms is not None and not start_ms <= timestamp_ms <= end_ms:
            excluded_range_count += 1
            continue

        names = ("open", "high", "low", "close", "volume")
        parsed_values: dict[str, Decimal] = {}
        for name, raw_value in zip(names, row[1:6]):
            try:
                parsed_values[name] = _parse_decimal(raw_value, name)
            except ValueError as exc:
                reject(row_index, "malformed_value", str(exc), timestamp)
                row_has_error = True

        if len(parsed_values) == len(names):
            open_price = parsed_values["open"]
            high_price = parsed_values["high"]
            low_price = parsed_values["low"]
            close_price = parsed_values["close"]
            volume = parsed_values["volume"]
            invalid_relations = []
            if high_price < open_price:
                invalid_relations.append("high < open")
            if high_price < close_price:
                invalid_relations.append("high < close")
            if high_price < low_price:
                invalid_relations.append("high < low")
            if low_price > open_price:
                invalid_relations.append("low > open")
            if low_price > close_price:
                invalid_relations.append("low > close")
            if invalid_relations:
                reject(row_index, "invalid_ohlc", "; ".join(invalid_relations), timestamp)
                row_has_error = True
            non_positive = [
                name
                for name, value in (
                    ("open", open_price),
                    ("high", high_price),
                    ("low", low_price),
                    ("close", close_price),
                )
                if value <= 0
            ]
            if non_positive:
                reject(
                    row_index,
                    "non_positive_price",
                    f"OHLC prices must be positive ({', '.join(non_positive)} <= 0)",
                    timestamp,
                )
                row_has_error = True
            if volume < 0:
                reject(row_index, "negative_volume", "volume must not be negative", timestamp)
                row_has_error = True

        if not row_has_error and len(parsed_values) == len(names):
            accepted.append(
                Candle(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=timestamp,
                    open=parsed_values["open"],
                    high=parsed_values["high"],
                    low=parsed_values["low"],
                    close=parsed_values["close"],
                    volume=parsed_values["volume"],
                )
            )

    gaps: tuple[CandleGap, ...] = ()
    if start_ms is not None and end_ms is not None:
        gaps = _gap_ranges(
            (datetime_to_milliseconds(candle.timestamp) for candle in accepted),
            start_ms=start_ms,
            end_ms=end_ms,
            interval_ms=interval_ms,
        )

    return CandleValidationReport(
        candles=tuple(accepted),
        issues=tuple(issues),
        gaps=gaps,
        rejected_count=len(rejected_rows),
        excluded_open_count=excluded_open_count,
        excluded_range_count=excluded_range_count,
        received_count=len(row_list),
    )


def parse_ohlcv_row(
    row: Any,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> Candle:
    """Parse one row and raise a detailed validation error if it is not a candle."""

    report = validate_ohlcv_rows(
        [row],
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
    )
    if report.issues:
        raise CandleValidationError(report)
    if len(report.candles) != 1:
        raise CandleValidationError(report)
    return report.candles[0]


def validate_stored_candle(candle: Candle) -> CandleValidationReport:
    """Revalidate one persisted candle before returning it to a caller."""

    timestamp_ms = datetime_to_milliseconds(candle.timestamp, field_name="candle.timestamp")
    row = [
        timestamp_ms,
        candle.open,
        candle.high,
        candle.low,
        candle.close,
        candle.volume,
    ]
    return validate_ohlcv_rows(
        [row],
        exchange=candle.exchange,
        symbol=candle.symbol,
        timeframe=candle.timeframe,
    )
