"""Seed an ISOLATED demo database with a synthetic series for Chart Patterns review.

⚠ DEMONSTRATION DATA — NOT MARKET HISTORY, NOT PERFORMANCE.

`scripts/seed_demo_dashboard.py` gives the dashboard a double bottom, a
breakout and a retest. This companion series exists so the Chart Patterns
screen can be reviewed against the OTHER pattern types BRAIN's real detector
records (`pattern_liquidity.classical_patterns`): an inverse head and
shoulders, a head and shoulders and an active trading range. It only inserts
candles. Nothing is labelled here: every pattern on screen is whatever the
unchanged detectors find in these candles when the dashboard asks for chart
evidence. If the detectors find nothing, nothing is drawn.

Use a disposable empty database only:

    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-patterns.sqlite3 \\
        .venv/bin/alembic upgrade head
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-patterns.sqlite3 \\
        .venv/bin/python scripts/seed_demo_chart_patterns.py
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-patterns.sqlite3 \\
        .venv/bin/python -m trading_assistant.web --port 8124

The script refuses to write if BTC/USDT candles already exist in the target DB.
No setup is qualified, no plan is journaled and no outcome is recorded.
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trading_assistant.database import create_database_engine  # noqa: E402 (after the sys.path insert)
from trading_assistant.market_data.models import OHLCVCandleRecord  # noqa: E402 (after the sys.path insert)
from trading_assistant.market_data.repository import CandleRepository  # noqa: E402 (after the sys.path insert)
from trading_assistant.market_data.types import Candle  # noqa: E402 (after the sys.path insert)

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

# (hour, price) control points: the story the detectors are given to read.
CONTROL = [
    (0, 86800), (60, 85600), (110, 85000),                     # decline
    (130, 84000),                                              # left shoulder (low)
    (150, 85200),                                              # neckline high 1
    (172, 82950),                                              # head (low)
    (194, 85250),                                              # neckline high 2
    (214, 84050),                                              # right shoulder (low)
    (240, 86300), (262, 87400),                                # breakout; left shoulder (high)
    (282, 86100),                                              # neckline low 1
    (304, 88450),                                              # head (high)
    (326, 86150),                                              # neckline low 2
    (346, 87400),                                              # right shoulder (high)
    (372, 85300), (395, 84800),                                # breakdown
    (420, 85900), (445, 85200), (470, 85950), (495, 85250),   # sideways
    (520, 85900), (545, 85300), (570, 85850), (599, 85500),
]


def build_closes() -> list[float]:
    rng = random.Random(20261014)
    closes: list[float] = []
    for (h0, p0), (h1, p1) in zip(CONTROL, CONTROL[1:]):
        span = h1 - h0
        for i in range(span):
            t = i / span
            base = p0 + (p1 - p0) * t
            closes.append(base + rng.uniform(-30, 30))
    closes.append(float(CONTROL[-1][1]) + rng.uniform(-10, 10))
    return closes


def build_candles(last_closed: datetime) -> tuple[Candle, ...]:
    """The synthetic 1H series ending at `last_closed` (deterministic for a given end)."""
    closes = build_closes()
    start = last_closed - timedelta(hours=len(closes) - 1)
    rng = random.Random(11)
    candles = []
    previous = closes[0]
    for i, close in enumerate(closes):
        open_ = previous
        high = max(open_, close) + rng.uniform(8, 70)
        low = min(open_, close) - rng.uniform(8, 70)
        volume = 900 + abs(close - open_) * 2.2 + rng.uniform(0, 420)
        candles.append(Candle(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME,
            timestamp=start + timedelta(hours=i),
            open=D(f"{open_:.1f}"), high=D(f"{high:.1f}"),
            low=D(f"{low:.1f}"), close=D(f"{close:.1f}"), volume=D(f"{volume:.1f}"),
        ))
        previous = close
    return tuple(candles)


def main() -> None:
    engine = create_database_engine()
    existing = select(OHLCVCandleRecord.timestamp).where(
        OHLCVCandleRecord.symbol == SYMBOL
    ).limit(1)
    with engine.connect() as connection:
        if connection.scalar(existing) is not None:
            print("[DEMO] refusing to seed: BTC/USDT candles already exist. Use a disposable empty database.")
            engine.dispose()
            return

    last_closed = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    candles = build_candles(last_closed)
    start = candles[0].timestamp
    inserted, present = CandleRepository(engine).insert_unchanged_or_new(candles)
    print(f"[DEMO] synthetic candles inserted={inserted} already_present={present} "
          f"({start.isoformat()} .. {last_closed.isoformat()}) — DEMONSTRATION DATA, not market history")
    engine.dispose()
    print("[DEMO] done. Patterns are whatever the unchanged detectors record; nothing is labelled here.")


if __name__ == "__main__":
    main()
