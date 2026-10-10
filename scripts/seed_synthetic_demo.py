"""Seed a LOCAL database with clearly synthetic demo material.

⚠ This script generates SYNTHETIC candles and journal entries so the Step 10
dashboard has something to display during local development. It never fetches
real market data and must never be mistaken for real history: every value is
produced from small deterministic fixtures reused from the test suite.

Usage:
    alembic upgrade head
    python scripts/seed_synthetic_demo.py
    python -m trading_assistant.web

The seeded series naturally produces a QUALIFIED setup with a PLANNABLE plan
at its newest boundary through the real Steps 2-5 replay. It refuses to write
if BTC/USDT candles already exist under any exchange identity or timeframe in
the selected DB; use a disposable empty local database for a preview.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from market_structure_fixtures import candle_at

from trading_assistant.database import create_database_engine
from trading_assistant.journaling import DecisionState, JournalService
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.pattern_liquidity import PatternLiquidityService
from trading_assistant.setup_qualification import QualificationService
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
)
from trading_assistant.trade_planning import plan_trade

EXCHANGE = "binance"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)

#: The same labelled synthetic series used by the Step 10 tests.
QUALIFYING_ROWS = (100, 104, 109, 104, 103) + (
    112,
    114,
    116,
    115,
    113,
    "114.2",
    "114.1",
    "115.5",
    117,
    119,
    121,
    120,
    118,
    "119.5",
    "121.5",
    124,
)


def demo_bar(index: int, close) -> Candle:
    price = D(str(close))
    return candle_at(
        index,
        open_=str(price),
        close=str(price),
        high=str(price + 1),
        low=str(price - 1),
    )


def _has_existing_btc_usdt_history(engine) -> bool:
    """Protect any existing BTC/USDT history from synthetic demo writes."""

    statement = (
        select(OHLCVCandleRecord.timestamp)
        .where(
            OHLCVCandleRecord.symbol == SYMBOL,
        )
        .limit(1)
    )
    with engine.connect() as connection:
        return connection.scalar(statement) is not None


def main() -> None:
    engine = create_database_engine()
    if _has_existing_btc_usdt_history(engine):
        print(
            "[synthetic demo] refusing to seed: BTC/USDT candles already exist "
            "in this database. Use a disposable empty local database."
        )
        engine.dispose()
        return

    repository = CandleRepository(engine)
    candles = tuple(
        replace(demo_bar(i, price), exchange=EXCHANGE)
        for i, price in enumerate(QUALIFYING_ROWS)
    )
    inserted, present = repository.insert_unchanged_or_new(candles)
    print(f"[synthetic demo] candles inserted={inserted} already_present={present}")

    qualification = QualificationService(engine)
    as_of = EPOCH + len(QUALIFYING_ROWS) * timedelta(hours=1)
    snapshot = qualification.snapshot(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=as_of
    )
    qualified = [s for s in snapshot.setups if s.state.value == "QUALIFIED"]
    if not qualified:
        print("[synthetic demo] no QUALIFIED setup present; skipping journal seed")
        engine.dispose()
        return

    setup = min(qualified, key=lambda s: (s.created_at, s.id))
    patterns = PatternLiquidityService(engine)
    source = patterns.snapshot(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=as_of
    )
    frame = QualificationFrame(source, ())
    plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
    journal = JournalService(engine)
    if plan.state.value == "PLANNABLE":
        record = journal.journal_plan(snapshot=snapshot, plan=plan)
        journal.record_decision(
            journal_id=record.journal_id,
            decision=DecisionState.ACCEPTED,
            decided_at=as_of,
            reason="synthetic demo seed — not a real decision",
        )
        print(
            f"[synthetic demo] journaled PLANNABLE proposal {record.journal_id[:12]}…"
        )
    else:
        print(f"[synthetic demo] plan state {plan.state.value}; nothing journaled")

    print(
        "[synthetic demo] done. Reminder: this data is synthetic, not market history."
    )
    engine.dispose()


if __name__ == "__main__":
    main()
