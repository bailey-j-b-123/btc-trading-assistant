"""Offline synthetic archive-format fixtures; never a substitute for real-data sign-off."""
from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_assistant.market_data.public_archive import decode_archive


def archive(month="2023-01", timeframe="1d", *, mutate=None):
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    step = {"1h": 3600000, "1d": 86400000}[timeframe]
    unit = 1000 if month >= "2025-01" else 1
    rows = []
    for t in range(int(start.timestamp() * 1000), int(end.timestamp() * 1000), step):
        rows.append([t * unit, "100.12345678", "102", "99", "101", "7.50000001",
            (t + step) * unit - 1, "757.5", "10", "4.5", "454.5", "0"])
    if mutate:
        mutate(rows)
    name = f"BTCUSDT-{timeframe}-{month}"
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr(name + ".csv", stream.getvalue())
    data = body.getvalue()
    checksum = hashlib.sha256(data).hexdigest() + "  " + name + ".zip"
    return data, checksum


@pytest.mark.parametrize("month", ["2023-01", "2025-01"])
@pytest.mark.parametrize("timeframe,count", [("1h", 744), ("1d", 31)])
def test_exact_values_utc_time_units_and_base_volume(month, timeframe, count):
    data, checksum = archive(month, timeframe)
    rows = decode_archive(data, checksum=checksum, month=month, timeframe=timeframe)
    assert len(rows) == count
    assert rows[0].exchange == "binance" and rows[0].symbol == "BTC/USDT"
    assert rows[0].timestamp == datetime.fromisoformat(month + "-01T00:00:00+00:00")
    assert rows[0].open == Decimal("100.12345678")
    assert rows[0].volume == Decimal("7.50000001")  # not quote volume 757.5
    assert rows[-1].timestamp.month == 1


@pytest.mark.parametrize("mutate,reason", [
    (lambda rows: rows.pop(4), "missing"),
    (lambda rows: rows.insert(4, rows[3]), "duplicate"),
    (lambda rows: rows.reverse(), "unordered"),
    (lambda rows: rows[4].__setitem__(6, int(rows[4][6]) - 1), "closed-candle"),
    (lambda rows: rows[4].__setitem__(2, "99"), "OHLCV"),
    (lambda rows: rows[4].__setitem__(5, "NaN"), "OHLCV"),
    (lambda rows: rows[4].__setitem__(0, int(rows[4][0]) // 1000), "timestamp unit"),
    (lambda rows: rows.pop(), "complete declared month"),
])
def test_archive_defects_fail_without_filling_or_guessing(mutate, reason):
    data, checksum = archive(mutate=mutate)
    with pytest.raises(ValueError, match=reason):
        decode_archive(data, checksum=checksum, month="2023-01", timeframe="1d")


def test_checksums_and_declared_market_identity_are_not_assumed():
    data, checksum = archive()
    for wrong in ["0" * 64 + "  BTCUSDT-1d-2023-01.zip", checksum.replace("BTCUSDT", "ETHUSDT")]:
        with pytest.raises(ValueError):
            decode_archive(data, checksum=wrong, month="2023-01", timeframe="1d")
    with pytest.raises(ValueError):
        decode_archive(data, checksum=checksum, month="../2023-01", timeframe="1d")
