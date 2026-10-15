"""Verified public archive acquisition into the lab's separate candle storage."""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import re
from urllib.request import Request, urlopen
from sqlalchemy import create_engine
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.public_archive import decode_archive
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from .models import canonical, identity
from .store import exclusive


def candle_fingerprint(candles):
    digest = sha256()
    for candle in candles:
        digest.update((canonical(asdict(candle)) + "\n").encode())
    return digest.hexdigest()


def repository(store):
    engine = create_engine("sqlite:///" + str(store.root / "research.sqlite3"))
    OHLCVCandleRecord.__table__.create(engine, checkfirst=True)
    return engine, CandleRepository(engine)


def fetch(url):
    if not url.startswith(
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/"
    ):
        raise ValueError("only fixed public Binance BTCUSDT archives are supported")
    with urlopen(
        Request(url, headers={"User-Agent": "BRAIN-isolated-strategy-lab/1"}),
        timeout=30,
    ) as response:
        if not response.url.startswith("https://data.binance.vision/"):
            raise ValueError("archive redirect left the public Binance host")
        raw = response.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("download exceeds bounded archive size")
    return raw


@exclusive
def acquire(store, months, timeframe="1h", *, download=fetch, stop=lambda: False):
    """Idempotent verified source receipts. Existing candles cannot be rewritten."""
    if timeframe not in {"1h", "4h", "15m", "5m"}:
        raise ValueError("unsupported research timeframe")
    engine, repo = repository(store)
    receipts = []
    try:
        for month in months:
            if stop():
                break
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise ValueError("invalid month")
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            if end > datetime.now(UTC):
                raise ValueError("only complete historical months may be acquired")
            name = f"BTCUSDT-{timeframe}-{month}.zip"
            directory = store.root / "archives"
            directory.mkdir(exist_ok=True)
            if directory.is_symlink():
                raise ValueError("redirected archive directory")
            path = directory / name
            checksum_path = directory / (name + ".CHECKSUM")
            if path.is_symlink() or checksum_path.is_symlink():
                raise ValueError("redirected archive file")
            url = f"https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/{timeframe}/{name}"
            checksum = (
                checksum_path.read_text()
                if checksum_path.exists()
                else download(url + ".CHECKSUM").decode("ascii")
            )
            data = path.read_bytes() if path.exists() else download(url)
            quality_issues = []
            try:
                candles = decode_archive(
                    data,
                    checksum=checksum,
                    month=month,
                    timeframe=timeframe,
                    allow_gaps=True,
                    quality_issues=quality_issues,
                )
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from exc
            # Verify BOTH bytes and complete candle geometry before persisting.
            for destination, payload in (
                (path, data),
                (checksum_path, checksum.encode()),
            ):
                if not destination.exists():
                    temporary = destination.with_name(destination.name + ".part")
                    if temporary.is_symlink():
                        raise ValueError("redirected temporary archive file")
                    with temporary.open("wb") as stream:
                        import os

                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(destination)
            inserted, repeated = repo.insert_unchanged_or_new(candles)
            receipt = {
                "source_url": url,
                "sha256": sha256(data).hexdigest(),
                "month": month,
                "timeframe": timeframe,
                "exchange": "binance",
                "symbol": "BTC/USDT",
                "excluded_source_rows": quality_issues,
                "declared_missing_candles": sum(
                    int((b.timestamp - a.timestamp).total_seconds())
                    // (
                        3600
                        if timeframe == "1h"
                        else 14400
                        if timeframe == "4h"
                        else 900
                        if timeframe == "15m"
                        else 300
                    )
                    - 1
                    for a, b in zip(candles, candles[1:])
                ),
                "candles": len(candles),
                "first_open": candles[0].timestamp,
                "last_close": candles[-1].timestamp
                + timedelta(
                    hours=1
                    if timeframe == "1h"
                    else 4
                    if timeframe == "4h"
                    else 0.25
                    if timeframe == "15m"
                    else 1 / 12
                ),
            }
            rid = identity(receipt)
            store.db.execute(
                "INSERT OR IGNORE INTO datasets VALUES(?,?)", (rid, canonical(receipt))
            )
            receipts.append(
                {
                    **json.loads(canonical(receipt)),
                    "inserted": inserted,
                    "already_present": repeated,
                }
            )
            store.heartbeat(
                "archive_acquisition",
                month=month,
                timeframe=timeframe,
                candles=len(candles),
            )
    finally:
        engine.dispose()
    return receipts


def dataset(store, start, end, timeframe="1h"):
    engine, repo = repository(store)
    try:
        step = timedelta(milliseconds=timeframe_to_milliseconds(timeframe))
        result = repo.get_candles(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe=timeframe,
            start_time=start,
            end_time=end - step,
        )
        candles = result.candles
        if not candles:
            raise ValueError("empty research dataset")
        if candles[0].timestamp != start or candles[-1].timestamp + step != end:
            raise ValueError("missing dataset boundary candles")
        receipts = [
            json.loads(r[0])
            for r in store.db.execute("SELECT manifest FROM datasets ORDER BY id")
        ]
        # Old receipts stay immutable; one archive is one source in a new
        # dataset, preferring the explicit newer gap description.
        unique = {}
        for receipt in sorted(
            receipts, key=lambda r: ("declared_missing_candles" in r, r["source_url"])
        ):
            unique[receipt["source_url"]] = receipt
        receipts = sorted(unique.values(), key=lambda r: r["source_url"])
        manifest = {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "candles": len(candles),
            "candle_sha256": candle_fingerprint(candles),
            "missing_candles": result.missing_candle_count,
            "gaps": [
                {
                    "start": g.start.isoformat(),
                    "end": g.end.isoformat(),
                    "missing_count": g.missing_count,
                }
                for g in result.gaps
            ],
            "source_receipts": [
                r
                for r in receipts
                if r["timeframe"] == timeframe
                and start.strftime("%Y-%m") <= r["month"] <= end.strftime("%Y-%m")
            ],
        }
        if not manifest["source_receipts"]:
            raise ValueError("dataset has no verified public archive provenance")
        return candles, manifest
    finally:
        engine.dispose()
