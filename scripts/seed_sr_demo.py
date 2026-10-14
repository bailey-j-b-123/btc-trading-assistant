"""Seed an ISOLATED demo database with multi-timeframe candles for Support & Resistance review.

⚠ DEMONSTRATION DATA — NOT MARKET HISTORY, NOT PERFORMANCE.
Every value here is generated in this file. Nothing is fetched, and nothing in the
demo database may be presented as real market history or as a trading result.

Why this script exists
----------------------
The Support & Resistance feature is multi-timeframe by construction: each
timeframe's zones are derived from that timeframe's own confirmed swings. The
other demo seeders write a single 1H series, which cannot show a 5M/15M/1H/4H/1D
zone set or a higher-timeframe band drawn against a lower-timeframe close. This
one writes the same deterministic price story at five resolutions, from one
minute-level path, so that:

* every timeframe has genuinely stored closed candles (none is synthesised by the
  dashboard at read time);
* the 1H series carries repeated tests of a resistance level, a level that price
  breaks and later returns to, and a level that ends up below price;
* the higher timeframes aggregate the same moves, so a 4H zone drawn on a 1H
  chart is a real 4H detection, not a copy of the 1H one.

Usage (disposable empty database only):

    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/sr-demo.sqlite3 \\
        .venv/bin/alembic upgrade head
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/sr-demo.sqlite3 \\
        .venv/bin/python scripts/seed_sr_demo.py
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/sr-demo.sqlite3 \\
        .venv/bin/python -m trading_assistant.web --port 8123

It refuses to write if BTC/USDT candles already exist, prints every timeframe it
stored, and never journals a plan or a decision.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trading_assistant.database import create_database_engine
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from trading_assistant.market_data.types import Candle

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
BASE_TIMEFRAME = "1m"
TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")

#: (fraction of the seeded span, price) control points of the story the detectors
#: are given to read: a base, an advance to a repeatedly tested resistance, a break
#: above it, a return to it from above, a continuation, and a decline back through
#: older levels so that at least one level ends up below the last close.
CONTROL = [
    (0.000, 84000), (0.023, 86500),                 # early advance
    (0.046, 85200), (0.069, 87600),                 # pullback, first test of 87.6k
    (0.093, 86100), (0.116, 87550),                 # dip, second test
    (0.139, 86000), (0.162, 87650),                 # dip, third test
    (0.185, 86400), (0.214, 88300),                 # dip, break above the level
    (0.231, 87400),                                 # return to the broken level (retest)
    (0.255, 89000), (0.301, 88400),                 # continuation, dip
    (0.347, 90200), (0.394, 89600),
    (0.440, 91000), (0.486, 90400),
    (0.532, 88600), (0.579, 87200),                 # a decline back through old levels
    (0.625, 88000), (0.671, 86800),
    (0.718, 87600), (0.764, 86900),
    (0.810, 87800), (0.856, 87100),
    (0.903, 88100), (0.949, 87450),
    (1.000, 87900),
]


def build_minute_path(total_minutes: int, *, wobble: float = 12.0) -> list[float]:
    """A deterministic 1-minute price path between the control points."""

    rng = random.Random(20261014)
    points = [(round(fraction * (total_minutes - 1)), price) for fraction, price in CONTROL]
    prices: list[float] = []
    for (m0, p0), (m1, p1) in zip(points, points[1:]):
        span = max(1, m1 - m0)
        for index in range(span):
            position = index / span
            prices.append(p0 + (p1 - p0) * position + rng.uniform(-wobble, wobble))
    prices.append(float(points[-1][1]))
    return prices


def aggregate(prices: list[float], minutes: int, start: datetime) -> tuple[Candle, ...]:
    """Aggregate the 1-minute path into closed candles of `minutes` minutes."""

    rng = random.Random(minutes)
    rows = []
    for offset in range(0, len(prices) - minutes + 1, minutes):
        window = prices[offset : offset + minutes]
        open_ = window[0]
        close = window[-1]
        high = max(window) + rng.uniform(4, 40)
        low = min(window) - rng.uniform(4, 40)
        volume = 40 * minutes + abs(close - open_) * 3 + rng.uniform(0, 600)
        rows.append(
            Candle(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="",
                timestamp=start + timedelta(minutes=offset),
                open=D(f"{open_:.1f}"),
                high=D(f"{high:.1f}"),
                low=D(f"{low:.1f}"),
                close=D(f"{close:.1f}"),
                volume=D(f"{volume:.1f}"),
            )
        )
    return tuple(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=60, help="days of 1-minute history to generate (default 60)"
    )
    parser.add_argument(
        "--timeframes",
        default=",".join(TIMEFRAMES),
        help=f"comma-separated timeframes to store (default {','.join(TIMEFRAMES)})",
    )
    parser.add_argument(
        "--wobble", type=float, default=12.0, help="per-minute noise in price units (default 12)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    arguments = parse_args(argv)
    total_minutes = arguments.days * 24 * 60
    timeframes = tuple(part.strip() for part in arguments.timeframes.split(",") if part.strip())
    engine = create_database_engine()
    existing = select(OHLCVCandleRecord.timestamp).where(
        OHLCVCandleRecord.symbol == SYMBOL
    ).limit(1)
    with engine.connect() as connection:
        if connection.scalar(existing) is not None:
            print("[SR DEMO] refusing to seed: BTC/USDT candles already exist. Use a disposable empty database.")
            engine.dispose()
            return

    one_minute = build_minute_path(total_minutes, wobble=arguments.wobble)
    last_closed_minute = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    start = last_closed_minute - timedelta(minutes=len(one_minute) - 1)
    # Align the start down to the 1d boundary so every timeframe's candles open on a boundary.
    start = start.replace(hour=0, minute=0)

    repository = CandleRepository(engine)
    print(
        f"[SR DEMO] DEMONSTRATION DATA — synthetic, not market history "
        f"({arguments.days} days of 1-minute path, {len(one_minute)} minutes)"
    )
    for timeframe in timeframes:
        minutes = timeframe_to_milliseconds(timeframe) // 60_000
        if minutes >= len(one_minute):
            print(f"[SR DEMO] {timeframe:>3}: skipped — the seeded span is shorter than one candle")
            continue
        rows = aggregate(one_minute, minutes, start)
        rows = tuple(
            Candle(
                exchange=row.exchange,
                symbol=row.symbol,
                timeframe=timeframe,
                timestamp=row.timestamp,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in rows
        )
        inserted, present = repository.insert_unchanged_or_new(rows)
        print(
            f"[SR DEMO] {timeframe:>3}: {len(rows):>5} candles "
            f"({rows[0].timestamp.isoformat()} .. {rows[-1].timestamp.isoformat()}) "
            f"inserted={inserted} already_present={present}"
        )
    engine.dispose()
    print("[SR DEMO] done. This database is synthetic demonstration data; no plan or decision was recorded.")


if __name__ == "__main__":
    main()
