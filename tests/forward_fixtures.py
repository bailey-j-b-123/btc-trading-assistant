"""Shared offline fixtures for Step 12 forward-testing tests.

Everything here is deterministic and offline: temporary migrated SQLite
databases, synthetic labelled candles reused from the existing Step 5/10
fixtures, a fake public OHLCV source that serves rows from an in-memory list,
and injectable clocks. Nothing contacts a network or an exchange, and no test
touches project data.

The synthetic series is labelled TEST FIXTURE everywhere: it is generated data
that naturally reaches WATCH/QUALIFIED/PLANNABLE through the real Steps 2-6
replay, not real market history and not evidence about anything.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, candle_at

from trading_assistant.config import Settings
from trading_assistant.forward_testing import (
    ForwardParameters,
    ForwardTestService,
    RunnerSettings,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import datetime_to_milliseconds

from web_fixtures import QUALIFYING_ROWS, migrated_engine  # noqa: F401  (re-export)

TIMEFRAME = "1h"

#: Boundary (close instant) at which the labelled series reaches QUALIFIED.
QUALIFYING_BOUNDARY = EPOCH + 21 * INTERVAL

#: Row index of the candle whose close is the qualifying boundary.
QUALIFYING_INDEX = 20


def bar(
    index: int,
    close,
    *,
    high=None,
    low=None,
    open_=None,
    volume: str = "10",
    mirror: bool = False,
):
    """One labelled synthetic candle at a whole-hour offset from the epoch."""

    price = D(str(close))
    resolved_open = D(str(open_ if open_ is not None else price))
    resolved_high = D(str(high if high is not None else price + D(1)))
    resolved_low = D(str(low if low is not None else price - D(1)))
    if mirror:
        mirror_axis = D(200)
        price = mirror_axis - price
        resolved_open = mirror_axis - resolved_open
        resolved_low = mirror_axis - resolved_high
        resolved_high = mirror_axis - D(str(low if low is not None else D(str(close)) - D(1)))
    if resolved_high < resolved_low:
        raise ValueError("fixture candle has high below low")
    return candle_at(
        index,
        open_=str(resolved_open),
        high=str(resolved_high),
        low=str(resolved_low),
        close=str(price),
        volume=volume,
        timeframe=TIMEFRAME,
    )


def labelled_series(*, mirror: bool = False) -> tuple:
    """The 21-candle labelled series that reaches QUALIFIED at its last close."""

    return tuple(
        bar(index, price, mirror=mirror) for index, price in enumerate(QUALIFYING_ROWS)
    )


def extend(
    candles: tuple,
    rows: tuple[tuple[int, object], ...],
    *,
    mirror: bool = False,
) -> tuple:
    """Append labelled candles ``(index, close)`` to a series."""

    return candles + tuple(
        bar(index, close, mirror=mirror) for index, close in rows
    )


def moving_metrics_from(candles: tuple) -> tuple[int, datetime]:
    return len(candles), candles[-1].timestamp


@dataclass
class FakeExchange:
    """Minimal public OHLCV source: serves rows from an in-memory candle list."""

    exchange_id: str = EXCHANGE
    fail_calls: tuple[int, ...] = ()
    failing_exception: type[Exception] = ConnectionError

    def __post_init__(self) -> None:
        self.candles: list = []
        self.calls = 0
        self.requests: list[int] = []
        self.last_http_response = None

    def set_candles(self, candles) -> None:
        self.candles = list(candles)

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.calls += 1
        self.requests.append(since_ms)
        if self.calls in self.fail_calls:
            raise self.failing_exception("simulated offline exchange")
        rows = []
        for candle in self.candles:
            if candle.timeframe != timeframe:
                continue
            if candle.symbol != symbol:
                continue
            timestamp = datetime_to_milliseconds(candle.timestamp)
            if timestamp < since_ms:
                continue
            rows.append(
                [
                    timestamp,
                    format(candle.open, "f"),
                    format(candle.high, "f"),
                    format(candle.low, "f"),
                    format(candle.close, "f"),
                    format(candle.volume, "f"),
                ]
            )
        return rows[:limit]

    def close(self) -> None:  # pragma: no cover - nothing to close
        return None


def forward_settings(database_url: str, *, raw_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USDT",
        exchange=EXCHANGE,
        default_timeframe=TIMEFRAME,
        supported_timeframes=("15m", TIMEFRAME, "4h", "1d"),
        database_url=database_url,
        raw_data_dir=raw_dir,
        market_data_page_limit=1000,
        market_data_max_pages=50,
    )


def insert_candles(engine, candles) -> None:
    CandleRepository(engine).insert_unchanged_or_new(candles)


def make_service(
    engine,
    settings: Settings,
    source: FakeExchange | None,
    *,
    clock,
    parameters: ForwardParameters | None = None,
    ledger_start: datetime | None = None,
    backfill_start: datetime | None = None,
) -> ForwardTestService:
    """Build a forward service whose market data comes from a fake public source."""

    market_data = None
    if source is not None:
        market_data = MarketDataService(
            engine,
            source,
            settings=settings,
            raw_store=RawResponseStore(settings.raw_data_dir),
            clock=clock,
        )
    return ForwardTestService(
        engine,
        settings=settings,
        clock=clock,
        parameters=parameters,
        runner_settings=RunnerSettings(
            interval_seconds=D(1), fetch_max_attempts=2, fetch_retry_backoff_seconds=D(0)
        ),
        market_data_service=market_data,
        ledger_start=ledger_start,
        backfill_start=backfill_start,
    )


def clock_at(boundary: datetime, *, seconds: int = 30) -> datetime:
    """A clock instant just after a candle-close boundary."""

    return boundary + timedelta(seconds=seconds)


def sweep_reversal_series() -> tuple:
    """The labelled series plus a failed sweep that qualifies the second family."""

    return (
        labelled_series()
        + (bar(21, 110, high=127, low=108),)
        + (bar(22, 118, high=121, low=106),)
    )


def watch_only_series() -> tuple:
    """The labelled series plus one failed sweep: WATCH/NO_SETUP, never PLANNABLE."""

    return labelled_series() + (bar(21, 110, high=127, low=108),)


def bull_plan_boundary() -> datetime:
    return QUALIFYING_BOUNDARY


def bull_plan_levels() -> tuple:
    """The frozen levels the labelled series produces at its qualifying close."""

    return (
        _D("124"),  # entry
        _D("117"),  # stop / invalidation
        (_D("138"),),  # targets
        _D("7"),  # risk per unit
    )


def _D(value: str):
    from decimal import Decimal

    return Decimal(value)


@dataclass
class Harness:
    """A deterministic offline forward-testing harness over a temp database."""

    engine: Any
    settings: Settings
    clock: dict
    service: ForwardTestService
    source: FakeExchange | None = None
    series: tuple = ()

    def store(self, candles: tuple) -> None:
        """Put closed candles into the stored market history."""

        insert_candles(self.engine, candles)
        self.series = self.series + tuple(candles)

    def advance_to(self, boundary: datetime) -> None:
        """Move the injected clock just past one candle-close boundary."""

        self.clock["t"] = clock_at(boundary)

    def run(self, **kwargs):
        kwargs.setdefault("refresh_market_data", self.source is not None)
        return self.service.run_once(**kwargs)

    def step(self, candles: tuple, **kwargs):
        """Store newly closed candles, move the clock past the last close, run."""

        if candles:
            self.series = self.series + tuple(candles)
            if self.source is not None:
                self.source.set_candles(self.series)
            self.store(tuple(candles))
            self.advance_to(candles[-1].timestamp + INTERVAL)
        return self.run(**kwargs)

    def candles(self):
        return CandleRepository(self.engine).get_candles(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def counts(self) -> dict:
        return self.service.ledger.counts(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def observations(self):
        return self.service.ledger.observations(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def plans(self):
        return self.service.ledger.paper_plans(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def latest_outcomes(self):
        return self.service.ledger.latest_outcomes(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def cycles(self):
        return self.service.ledger.cycles(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def outcome_versions(self, paper_plan_id: str):
        return self.service.ledger.outcome_versions(paper_plan_id)

    def ledger(self):
        return self.service.ledger_snapshot(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
        )

    def report(self, **kwargs):
        from trading_assistant.forward_testing import build_forward_report

        kwargs.setdefault("ledger", self.ledger())
        kwargs.setdefault("exchange", EXCHANGE)
        kwargs.setdefault("symbol", SYMBOL)
        kwargs.setdefault("timeframe", TIMEFRAME)
        kwargs.setdefault("parameters", self.service.parameters)
        return build_forward_report(**kwargs)


def make_harness(
    *,
    series: tuple | None = None,
    parameters: ForwardParameters | None = None,
    ledger_start: datetime | None = None,
    backfill_start: datetime | None = None,
    source: FakeExchange | None | bool = None,
    store_series: bool = True,
) -> Harness:
    """Build an isolated harness: temp migrated DB, frozen clock, no network."""

    tmp = Path(tempfile.mkdtemp(prefix="forward-test-"))
    engine, database_url = migrated_engine(tmp, "forward.sqlite3")
    settings = forward_settings(database_url, raw_dir=tmp / "raw")
    clock = {"t": clock_at(ledger_start or EPOCH)}
    resolved_series = series if series is not None else ()
    if store_series and resolved_series:
        insert_candles(engine, resolved_series)
    fake = source if isinstance(source, FakeExchange) else None
    if source is None and resolved_series:
        fake = FakeExchange()
        fake.set_candles(resolved_series)
    service = make_service(
        engine,
        settings,
        fake,
        clock=lambda: clock["t"],
        parameters=parameters,
        ledger_start=ledger_start,
        backfill_start=backfill_start,
    )
    return Harness(
        engine=engine,
        settings=settings,
        clock=clock,
        service=service,
        source=fake,
        series=resolved_series,
    )


def mirrored(candles: tuple) -> tuple:
    """Mirror a labelled series so the same geometry appears on the short side."""

    axis = D(200)
    return tuple(
        replace(
            candle,
            open=axis - candle.open,
            high=axis - candle.low,
            low=axis - candle.high,
            close=axis - candle.close,
        )
        for candle in candles
    )


__all__ = [
    "EPOCH",
    "Harness",
    "EXCHANGE",
    "FakeExchange",
    "INTERVAL",
    "QUALIFYING_BOUNDARY",
    "QUALIFYING_INDEX",
    "SYMBOL",
    "TIMEFRAME",
    "bar",
    "clock_at",
    "extend",
    "forward_settings",
    "insert_candles",
    "labelled_series",
    "make_harness",
    "make_service",
    "migrated_engine",
    "mirrored",
    "moving_metrics_from",
    "sweep_reversal_series",
    "watch_only_series",
]
