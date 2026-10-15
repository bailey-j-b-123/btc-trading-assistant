"""Verified Binance Spot archive decoding; no network or database side effects.

Archive timestamps changed from milliseconds to microseconds on 2025-01-01.
The declared archive month determines the unit; malformed timestamps never get
guessed into a plausible date. Volume is BTC, not the separate quote-volume column.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_assistant.market_data.timeframes import (
    milliseconds_to_datetime,
    timeframe_anchor_milliseconds,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle

ARCHIVE_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")


def decode_archive(
    data: bytes,
    *,
    checksum: str,
    month: str,
    timeframe: str,
    allow_gaps: bool = False,
    quality_issues: list | None = None,
) -> tuple[Candle, ...]:
    """Return exact closed OHLCV records from one checksum-verified monthly ZIP.

    Duplicates and gaps fail by default. Isolated research may explicitly admit
    timestamp gaps, which its dataset/position pipeline must report. No caller
    can admit duplicates, disorder or off-grid rows, or fill missing candles.
    """
    if type(allow_gaps) is not bool:
        raise TypeError("allow_gaps must be boolean")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must be YYYY-MM")
    if timeframe not in ARCHIVE_TIMEFRAMES:
        raise ValueError("unsupported archive timeframe")
    name = f"BTCUSDT-{timeframe}-{month}"
    tokens = checksum.strip().split()
    if len(tokens) != 2 or tokens[1].lstrip("*") != name + ".zip":
        raise ValueError("checksum must name this exact archive")
    if (
        not re.fullmatch(r"[a-fA-F0-9]{64}", tokens[0])
        or hashlib.sha256(data).hexdigest() != tokens[0].lower()
    ):
        raise ValueError("archive SHA-256 mismatch")
    unit = 1_000 if month >= "2025-01" else 1
    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    candles: list[Candle] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.namelist() != [name + ".csv"]:
            raise ValueError("archive must contain its one expected CSV")
        if archive.getinfo(name + ".csv").file_size > 64 * 1024 * 1024:
            raise ValueError("archive exceeds the supported import size")
        last_opened = None
        for row_index, row in enumerate(
            csv.reader(io.StringIO(archive.read(name + ".csv").decode("utf-8")))
        ):
            if len(row) != 12:
                raise ValueError("expected twelve Binance Spot kline columns")
            opened, closed = int(row[0]), int(row[6])
            nominal_close = opened + interval_ms * unit - 1
            if (
                opened % unit
                or (not allow_gaps or quality_issues is None)
                and closed != nominal_close
            ):
                raise ValueError(
                    "invalid archive timestamp unit or closed-candle boundary"
                )
            timestamp_ms = opened // unit
            if (timestamp_ms - anchor_ms) % interval_ms:
                raise ValueError("candle is not aligned to its UTC timeframe")
            instant = milliseconds_to_datetime(timestamp_ms)
            if not start <= instant < end:
                raise ValueError("candle does not belong to the declared archive month")
            if last_opened is not None and opened <= last_opened:
                raise ValueError("duplicate or unordered source rows")
            last_opened = opened
            if closed != nominal_close:
                if not allow_gaps or quality_issues is None:
                    raise ValueError(
                        "invalid archive timestamp unit or closed-candle boundary"
                    )
                quality_issues.append(
                    {
                        "row_index": row_index,
                        "open_timestamp": opened,
                        "recorded_close": closed,
                        "nominal_close": nominal_close,
                        "reason": "partial_interval_excluded"
                        if opened <= closed < nominal_close
                        else "invalid_close_boundary_excluded",
                        "raw_row": row,
                    }
                )
                continue
            open_, high, low, close, volume = map(Decimal, row[1:6])
            if (
                not all(
                    value.is_finite() for value in (open_, high, low, close, volume)
                )
                or low <= 0
                or high < max(open_, close)
                or low > min(open_, close)
                or volume < 0
            ):
                raise ValueError("invalid OHLCV geometry or BTC volume")
            if candles:
                delta = instant - candles[-1].timestamp
                if (
                    delta <= timedelta(0)
                    or delta % timedelta(milliseconds=interval_ms)
                    or not allow_gaps
                    and delta != timedelta(milliseconds=interval_ms)
                ):
                    raise ValueError("duplicate, unordered or missing archive candles")
            candles.append(
                Candle(
                    exchange="binance",
                    symbol="BTC/USDT",
                    timeframe=timeframe,
                    timestamp=instant,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )
    if not candles:
        raise ValueError("archive contains no candles")
    if timeframe != "1w" and (
        candles[0].timestamp != start
        or candles[-1].timestamp + timedelta(milliseconds=interval_ms) != end
    ):
        raise ValueError("archive does not cover the complete declared month")
    return tuple(candles)
