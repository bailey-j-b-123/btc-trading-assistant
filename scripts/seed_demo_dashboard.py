"""Seed an ISOLATED demo database with realistic synthetic candles for visual review.

⚠ DEMONSTRATION DATA — NOT MARKET HISTORY, NOT PERFORMANCE.
This generates a deterministic synthetic BTC/USDT 1H series (a downtrend into a
double bottom, an advance to a repeatedly tested resistance, a breakout, a
retest and a continuation) so the dashboard's five chart functions have real
stored evidence to draw during visual sign-off. Every value is generated here;
nothing is fetched, and nothing in the demo database may be presented as real
trading results. Use a disposable empty database only:

    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-demo.sqlite3 \
        .venv/bin/alembic upgrade head
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-demo.sqlite3 \
        .venv/bin/python scripts/seed_demo_dashboard.py
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-demo.sqlite3 \
        .venv/bin/python -m trading_assistant.web --port 8123

The script refuses to write if BTC/USDT candles already exist in the target DB.
If the real engine qualifies a setup and builds a plan from this series, the
plan is journaled (paper only) so the Trade Plan view can be reviewed; outcomes
are whatever the engine's own forward observation later records — this script
never invents one.
"""

from __future__ import annotations

import random
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from trading_assistant.database import create_database_engine
from trading_assistant.journaling import DecisionState, JournalService
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.pattern_liquidity import PatternLiquidityService
from trading_assistant.setup_qualification import QualificationService
from trading_assistant.setup_qualification.models import QualificationFrame
from trading_assistant.trade_planning import plan_trade

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

# (hour, price) control points: the story the detectors are given to read.
CONTROL = [
    (0, 85200), (55, 84000), (95, 82900), (120, 82400),       # decline, bottom 1
    (160, 84200),                                             # neckline peak
    (190, 82550),                                             # bottom 2
    (225, 84300), (265, 85600),                               # neckline break, advance
    (295, 84900), (330, 86200),                               # pullback, resistance test 1
    (355, 85400), (380, 86150),                               # dip, resistance test 2
    (405, 85300), (430, 86600),                               # dip, breakout close above
    (455, 85900),                                             # retest of the broken zone
    (500, 87800), (540, 87200), (570, 88200), (599, 88600),   # continuation, rising end
]


def build_closes() -> list[float]:
    rng = random.Random(20261013)
    closes: list[float] = []
    for (h0, p0), (h1, p1) in zip(CONTROL, CONTROL[1:]):
        span = h1 - h0
        for i in range(span):
            t = i / span
            base = p0 + (p1 - p0) * t
            closes.append(base + rng.uniform(-30, 30))
    closes.append(float(CONTROL[-1][1]) + rng.uniform(-10, 10))
    return closes


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

    closes = build_closes()
    last_closed = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    start = last_closed - timedelta(hours=len(closes) - 1)
    rng = random.Random(7)
    candles = []
    previous = closes[0]
    for i, close in enumerate(closes):
        open_ = previous
        high = max(open_, close) + rng.uniform(8, 70)
        low = min(open_, close) - rng.uniform(8, 70)
        volume = 900 + abs(close - open_) * 2.2 + rng.uniform(0, 420)
        candle = Candle(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME,
            timestamp=start + timedelta(hours=i),
            open=D(f"{open_:.1f}"), high=D(f"{high:.1f}"),
            low=D(f"{low:.1f}"), close=D(f"{close:.1f}"), volume=D(f"{volume:.1f}"),
        )
        candles.append(replace(candle, exchange=EXCHANGE))
        previous = close
    inserted, present = CandleRepository(engine).insert_unchanged_or_new(tuple(candles))
    print(f"[DEMO] synthetic candles inserted={inserted} already_present={present} "
          f"({start.isoformat()} .. {last_closed.isoformat()}) — DEMONSTRATION DATA, not market history")

    as_of = last_closed + timedelta(hours=1)
    qualification = QualificationService(engine)
    snapshot = qualification.snapshot(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=as_of)
    qualified = [s for s in snapshot.setups if s.state.value == "QUALIFIED"]
    print(f"[DEMO] setups={len(snapshot.setups)} qualified={len(qualified)}")
    if not qualified:
        print("[DEMO] no QUALIFIED setup at the newest boundary; chart evidence still renders. Nothing journaled.")
        engine.dispose()
        return
    setup = min(qualified, key=lambda s: (s.created_at, s.id))
    source = PatternLiquidityService(engine).snapshot(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=as_of
    )
    plan = plan_trade(snapshot=snapshot, frame=QualificationFrame(source, ()), setup_id=setup.id)
    print(f"[DEMO] plan state={plan.state.value}")
    if plan.state.value == "PLANNABLE":
        journal = JournalService(engine)
        record = journal.journal_plan(snapshot=snapshot, plan=plan)
        journal.record_decision(
            journal_id=record.journal_id, decision=DecisionState.ACCEPTED, decided_at=as_of,
        )
        print("[DEMO] paper plan journaled + ACCEPTED recorded (paper only; no outcome invented).")
    engine.dispose()
    print("[DEMO] done. Reminder: this database is synthetic demonstration data.")


if __name__ == "__main__":
    main()
