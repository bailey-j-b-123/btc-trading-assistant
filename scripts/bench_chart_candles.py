"""Benchmark bounded chart reads against a full-series read on a large SYNTHETIC database.

Every candle written here is a deterministic synthetic fixture, labelled as such. Timings describe
this machine and this synthetic layout only. They are not evidence about live Binance data.

Usage (from the repository root):
    .venv/bin/python scripts/bench_chart_candles.py --rows 100000 --repeat 5

The database is created under the system temp directory and removed afterwards unless --keep is
given. Nothing is written under data/ and no existing database is touched.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from web_fixtures import insert_candles, make_settings, migrated_engine  # noqa: E402

from trading_assistant.market_data.repository import CandleRepository  # noqa: E402
from trading_assistant.market_data.types import Candle  # noqa: E402
from trading_assistant.web.dashboard_service import DashboardService  # noqa: E402
from trading_assistant.web.state import AppState  # noqa: E402

SYMBOL = "BTC/USDT"
EXCHANGE = "binance"
TIMEFRAME = "5m"
START = datetime(2026, 1, 1, tzinfo=UTC)
INTERVAL = timedelta(minutes=5)
CHUNK = 5000


def synthetic_rows(count: int):
    for index in range(count):
        price = D("60000") + D(index % 997)
        yield Candle(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            timestamp=START + index * INTERVAL,
            open=price,
            high=price + D("9"),
            low=price - D("9"),
            close=price + D("2"),
            volume=D("4"),
        )


def timed(fn, repeat: int):
    samples = []
    value = None
    for _ in range(repeat):
        started = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples[len(samples) // 2], value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="chart-bench-"))
    try:
        engine, url = migrated_engine(workdir, "bench.sqlite3")
        build_started = time.perf_counter()
        batch: list[Candle] = []
        for candle in synthetic_rows(args.rows):
            batch.append(candle)
            if len(batch) == CHUNK:
                insert_candles(engine, batch)
                batch = []
        if batch:
            insert_candles(engine, batch)
        build_seconds = time.perf_counter() - build_started

        boundary = START + args.rows * INTERVAL  # clock one interval after the newest candle
        settings = make_settings(url)
        state = AppState(settings=settings, engine=engine, clock=lambda: boundary)
        service = DashboardService(state)
        repo = CandleRepository(engine)
        earliest = repo.earliest_timestamp(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME)
        latest = repo.latest_timestamp(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME)
        deep_before = START + (args.rows // 2) * INTERVAL

        print(f"SYNTHETIC database: {args.rows} x {TIMEFRAME} candles, build {build_seconds:.1f}s")
        print(f"stored range: {earliest.isoformat()} .. {latest.isoformat()}")
        print(f"repeats per measurement: {args.repeat} (median reported)")
        print()

        # Baseline: the old chart path read the whole stored series, then kept the last 500 rows.
        full_ms, full_rows = timed(
            lambda: repo.get_candles(
                exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME,
                start_time=earliest, end_time=boundary - INTERVAL,
            ),
            max(1, min(args.repeat, 3)),
        )
        print(f"baseline full-series read : {full_ms:9.1f} ms  rows loaded {len(full_rows.candles)}")

        latest_ms, latest_page = timed(
            lambda: service.candles(timeframe=TIMEFRAME, limit=500, end_time=boundary), args.repeat
        )
        print(f"bounded latest page (500) : {latest_ms:9.1f} ms  rows returned {latest_page['returned_count']}")

        deep_ms, deep_page = timed(
            lambda: service.candles(timeframe=TIMEFRAME, limit=500, end_time=boundary, before=deep_before),
            args.repeat,
        )
        print(f"bounded page at midpoint  : {deep_ms:9.1f} ms  rows returned {deep_page['returned_count']}")

        oldest_ms, oldest_page = timed(
            lambda: service.candles(
                timeframe=TIMEFRAME, limit=500, end_time=boundary, before=earliest + 500 * INTERVAL
            ),
            args.repeat,
        )
        print(f"bounded page at earliest  : {oldest_ms:9.1f} ms  rows returned {oldest_page['returned_count']}")
        print()
        print(f"speed-up, latest page vs full read: {full_ms / latest_ms:.1f}x")
        print(f"series_missing_count on latest page: {latest_page['series_missing_count']}")
        print(f"earliest_reached on earliest page : {oldest_page['earliest_reached']}")
    finally:
        if args.keep:
            print(f"kept {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
