"""Shared fixtures for Step 10 web-layer tests.

Everything is offline: temporary SQLite databases migrated by the real
Alembic migrations, synthetic labelled candles, injectable fixed clocks, and
Step 5/6 fixtures reused from the existing test modules. Nothing contacts a
network or an exchange.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

from alembic import command
from alembic.config import Config
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, candle_at

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.web import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: Synthetic 1h candle series that naturally reaches QUALIFIED through a real
#: DB replay of Steps 2-5 (breakout, hold, retest, continuation). Labelled as
#: a TEST FIXTURE: it is generated data, not real market history.
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
QUALIFIED_BOUNDARY_OFFSET = 21  # as_of = EPOCH + 21h

#: Candle index -> explicit high for the qualifying series. Index 3 carries the
#: one genuine, confirmed, unswept structural level above the qualifying close
#: (138): it is old enough to sit outside the volatility window, so the fixture
#: keeps its documented contract - entry 124, stop 117, risk 7, target 138,
#: 2 qualified setups - without any synthetic R-derived target and without
#: perturbing the later structure or the ATR facts.
QUALIFYING_HIGHS = {3: "138"}

#: Candles that produce a WATCH snapshot at EPOCH + 7h (Step 5 service test).
WATCH_ROWS = (100, 104, 109, 104, 103, 112, (111, 112, 109))


def bar(index: int, close, *, high=None, low=None, open_=None, timeframe="1h"):
    price = D(str(close))
    return candle_at(
        index,
        open_=str(open_ if open_ is not None else price),
        close=str(price),
        high=str(high if high is not None else price + 1),
        low=str(low if low is not None else price - 1),
        timeframe=timeframe,
    )


def qualifying_candles(symbol: str = SYMBOL) -> tuple:
    candles = tuple(
        bar(i, price, high=QUALIFYING_HIGHS.get(i))
        for i, price in enumerate(QUALIFYING_ROWS)
    )
    if symbol == SYMBOL:
        return candles
    from dataclasses import replace

    return tuple(replace(c, symbol=symbol) for c in candles)


def watch_candles() -> tuple:
    prefix = tuple(bar(i, price) for i, price in enumerate((100, 104, 109, 104, 103)))
    return prefix + (bar(5, 112), bar(6, 111, high=112, low=109))


def migrated_engine(
    tmp_path: Path,
    name: str = "web.sqlite3",
    *,
    sqlite_busy_timeout_ms: int | None = None,
):
    """A real file-backed SQLite database migrated by the real Alembic chain."""

    url = f"sqlite:///{tmp_path / name}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return create_database_engine(url, sqlite_busy_timeout_ms=sqlite_busy_timeout_ms), url


def make_settings(database_url: str, **overrides) -> Settings:
    return Settings(
        symbol=SYMBOL, exchange=EXCHANGE, database_url=database_url, **overrides
    )


def insert_candles(engine, candles) -> None:
    CandleRepository(engine).insert_unchanged_or_new(candles)


def make_client(engine, settings, *, clock: datetime):
    """TestClient over the real app with a fixed clock (deterministic freshness)."""

    from fastapi.testclient import TestClient

    app = create_app(engine=engine, settings=settings, clock=lambda: clock)
    return TestClient(app)


def qualified_clock() -> datetime:
    return EPOCH + QUALIFIED_BOUNDARY_OFFSET * INTERVAL


def later_clock(hours: int = 5) -> datetime:
    return qualified_clock() + timedelta(hours=hours)
