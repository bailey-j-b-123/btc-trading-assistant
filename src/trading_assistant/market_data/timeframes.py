"""Fixed-duration timeframe and UTC timestamp helpers."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_DAY_MILLISECONDS = 86_400_000
_WEEK_ANCHOR_MILLISECONDS = 4 * _DAY_MILLISECONDS  # Monday 1970-01-05 00:00 UTC.
_TIMEFRAME_PATTERN = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_UNIT_MILLISECONDS = {
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": _DAY_MILLISECONDS,
    "w": 7 * _DAY_MILLISECONDS,
}


def _timeframe_parts(timeframe: str) -> tuple[int, str]:
    if not isinstance(timeframe, str):
        raise TypeError(f"Unsupported fixed-duration timeframe: {timeframe!r}")
    match = _TIMEFRAME_PATTERN.fullmatch(timeframe)
    if match is None:
        raise ValueError(f"Unsupported fixed-duration timeframe: {timeframe!r}")
    count, unit = match.groups()
    return int(count), unit


def timeframe_to_milliseconds(timeframe: str) -> int:
    """Convert a positive fixed-duration CCXT timeframe into milliseconds.

    Supported units are seconds, minutes, hours, days, and weeks. Calendar
    intervals such as months are intentionally excluded because their duration
    and alignment are not fixed.
    """

    count, unit = _timeframe_parts(timeframe)
    return count * _UNIT_MILLISECONDS[unit]


def timeframe_anchor_milliseconds(timeframe: str) -> int:
    """Return the UTC alignment anchor for a fixed-duration timeframe."""

    _, unit = _timeframe_parts(timeframe)
    return _WEEK_ANCHOR_MILLISECONDS if unit == "w" else 0


def is_timeframe_aligned(timestamp_ms: int, timeframe: str) -> bool:
    """Return whether a Unix-millisecond timestamp aligns to the timeframe."""

    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    return (timestamp_ms - anchor_ms) % interval_ms == 0


def require_utc_datetime(value: datetime, *, field_name: str = "timestamp") -> datetime:
    """Require an aware datetime and normalize it to UTC."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def datetime_to_milliseconds(value: datetime, *, field_name: str = "timestamp") -> int:
    """Convert an aware datetime to Unix milliseconds without local-time calls."""

    utc_value = require_utc_datetime(value, field_name=field_name)
    delta = utc_value - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000 + delta.microseconds // 1_000


def milliseconds_to_datetime(value: int) -> datetime:
    """Convert Unix milliseconds to an aware UTC datetime without float math."""

    seconds, milliseconds = divmod(value, 1_000)
    return _EPOCH + timedelta(seconds=seconds, milliseconds=milliseconds)


def latest_closed_candle_open_time(as_of: datetime, timeframe: str) -> datetime:
    """Return the latest candle open time whose full interval ended by ``as_of``."""

    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    as_of_ms = datetime_to_milliseconds(as_of, field_name="as_of")
    current_open_ms = ((as_of_ms - anchor_ms) // interval_ms) * interval_ms + anchor_ms
    return milliseconds_to_datetime(current_open_ms - interval_ms)
