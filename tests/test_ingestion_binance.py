"""Binance Spot ingestion: backfill, incremental refresh, coverage, isolation, lock.

Every candle here comes from a deterministic fake of Binance's public klines
endpoint. No network is opened. The fake serves only closed, UTC-aligned
candles and a forming candle at the end, exactly like the real endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.ingestion import (
    IngestionLockError,
    SingleInstanceLock,
    coverage_for,
    ingest_once,
)
from trading_assistant.ingestion.service import (
    INSUFFICIENT_HISTORY,
    MISSING_CANDLES,
    NO_DATA,
    STALE,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import (
    milliseconds_to_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.multi_timeframe.hierarchy import default_hierarchy
from trading_assistant.multi_timeframe.service import MultiTimeframeService

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIMEFRAMES = ("5m", "15m", "1h", "4h")
AS_OF = datetime(2024, 3, 1, 12, 30, tzinfo=UTC)
HISTORY = 300  # closes per timeframe: comfortably above the 131-close gate depth


def _open_ms(timeframe: str, instant: datetime) -> int:
    interval = timeframe_to_milliseconds(timeframe)
    return int(instant.timestamp() * 1000) // interval * interval


def _expected_latest(timeframe: str, as_of: datetime) -> int:
    """Open ms of the newest candle whose full interval ended by ``as_of``."""

    interval = timeframe_to_milliseconds(timeframe)
    return _open_ms(timeframe, as_of) - interval


def _rows(timeframe: str, *, as_of: datetime, count: int = HISTORY, forming: bool = True):
    interval = timeframe_to_milliseconds(timeframe)
    newest = _expected_latest(timeframe, as_of)
    rows = []
    price = Decimal("60000")
    for index in range(count):
        opened = newest - (count - 1 - index) * interval
        close = price + Decimal(index % 7) - Decimal(3)
        rows.append(
            [
                opened,
                str(price),
                str(max(price, close) + Decimal(5)),
                str(min(price, close) - Decimal(5)),
                str(close),
                "2.5",
            ]
        )
        price = close
    if forming:
        # The candle that is still forming when the pass runs: must never be stored.
        rows.append([newest + interval, str(price), str(price + 9), str(price - 9), str(price), "1"])
    return rows


class BinanceKlinesFake:
    """Date-bounded public klines fake; can fail or drop rows per timeframe."""

    exchange_id = "binance"
    max_ohlcv_limit = 1000
    ohlcv_is_rolling_window = False

    def __init__(self, rows_by_timeframe, *, failing=(), exception=ConnectionError):
        self.rows_by_timeframe = {
            timeframe: sorted(rows, key=lambda row: int(row[0]))
            for timeframe, rows in rows_by_timeframe.items()
        }
        self.failing = set(failing)
        self.exception = exception
        self.last_http_response = None
        self.requests = []

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.requests.append((timeframe, since_ms, limit))
        if timeframe in self.failing:
            raise self.exception(f"simulated Binance outage for {timeframe}")
        rows = [row for row in self.rows_by_timeframe.get(timeframe, []) if int(row[0]) >= since_ms]
        return rows[:limit]

    def close(self):
        return None


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        exchange="binance",
        default_timeframe="1h",
        supported_timeframes=TIMEFRAMES,
        raw_data_dir=tmp_path / "raw",
        market_data_page_limit=1000,
        market_data_max_pages=1_000,
    )


def _service(tmp_path: Path, source, *, as_of: datetime = AS_OF, name: str = "ingest.sqlite3"):
    url = f"sqlite:///{tmp_path / name}"
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    settings = _settings(tmp_path)
    market = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: as_of,
    )
    service = MultiTimeframeService(
        engine,
        settings=settings,
        clock=lambda: as_of,
        hierarchy=default_hierarchy(),
        market_data_service=market,
    )
    return engine, service


def _all_timeframes_source(**overrides):
    rows = {timeframe: _rows(timeframe, as_of=AS_OF) for timeframe in TIMEFRAMES}
    rows.update(overrides)
    return BinanceKlinesFake(rows)


def _stored(engine, *, exchange="binance", timeframe="1h") -> list[datetime]:
    """Stored candle open times for one exchange and timeframe, oldest first."""

    return [
        candle.timestamp
        for candle in CandleRepository(engine).get_candles(
            exchange=exchange, symbol="BTC/USDT", timeframe=timeframe
        ).candles
    ]


def _as_datetime(open_ms: int) -> datetime:
    return milliseconds_to_datetime(open_ms)


# ----------------------------------------------------------------------
# Initial backfill and timeframe coverage
# ----------------------------------------------------------------------
def test_initial_backfill_populates_every_required_timeframe_with_complete_history(tmp_path):
    source = _all_timeframes_source()
    engine, service = _service(tmp_path, source)
    report = ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)

    assert report.error_count == 0
    assert {result.timeframe for result in report.results} == set(TIMEFRAMES)
    assert all(result.status == "OK" for result in report.results)
    for item in report.coverage:
        assert item.complete, item.describe()
        assert item.stored_in_window >= item.required_depth
        assert item.latest_stored_open == item.expected_latest_closed_open
    assert report.all_complete
    # Every timeframe is genuinely stored, not just the 1h the user had.
    for timeframe in TIMEFRAMES:
        assert len(_stored(engine, timeframe=timeframe)) >= service.required_depth()


def test_forming_candle_is_never_stored(tmp_path):
    engine, service = _service(tmp_path, _all_timeframes_source())
    ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    for timeframe in TIMEFRAMES:
        newest_open = _stored(engine, timeframe=timeframe)[-1]
        assert newest_open == _as_datetime(_expected_latest(timeframe, AS_OF))
        assert newest_open + timedelta(milliseconds=timeframe_to_milliseconds(timeframe)) <= AS_OF


# ----------------------------------------------------------------------
# Incremental refresh and restart recovery
# ----------------------------------------------------------------------
def test_incremental_refresh_appends_only_newly_closed_candles(tmp_path):
    later = AS_OF + timedelta(minutes=10)  # two more closed 5m candles, no new 15m/1h/4h
    source = _all_timeframes_source()
    engine, service = _service(tmp_path, source)
    ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    before = {timeframe: len(_stored(engine, timeframe=timeframe)) for timeframe in TIMEFRAMES}

    # The exchange now also serves the two 5m candles that closed since AS_OF.
    interval = timeframe_to_milliseconds("5m")
    newest = _expected_latest("5m", AS_OF)
    extra = [
        [newest + step * interval, "60000", "60010", "59990", "60005", "1"]
        for step in (1, 2)
    ]
    # Drop the forming row the base series carries (its open is one of the new
    # closes); the exchange now serves those candles as closed.
    closed_base = [row for row in source.rows_by_timeframe["5m"] if int(row[0]) <= newest]
    source.rows_by_timeframe["5m"] = sorted(closed_base + extra, key=lambda row: int(row[0]))
    report = ingest_once(service, symbol="BTC/USDT", as_of=later)

    assert report.error_count == 0
    five = next(result for result in report.results if result.timeframe == "5m")
    assert five.update["inserted_count"] == 2
    assert len(_stored(engine, timeframe="5m")) == before["5m"] + 2
    for timeframe in ("15m", "1h", "4h"):
        assert len(_stored(engine, timeframe=timeframe)) == before[timeframe]


def test_rerunning_ingestion_is_idempotent_across_a_restart(tmp_path):
    source = _all_timeframes_source()
    engine, service = _service(tmp_path, source)
    ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    counts = {timeframe: len(_stored(engine, timeframe=timeframe)) for timeframe in TIMEFRAMES}

    # Simulated restart: a fresh service over the same database and source.
    engine2, restarted = _service(tmp_path, source, name="ingest.sqlite3")
    report = ingest_once(restarted, symbol="BTC/USDT", as_of=AS_OF)
    assert report.error_count == 0
    assert all(result.update["inserted_count"] == 0 for result in report.results)
    assert {t: len(_stored(engine2, timeframe=t)) for t in TIMEFRAMES} == counts


# ----------------------------------------------------------------------
# Diagnostics: stale, insufficient, missing; never fabricated
# ----------------------------------------------------------------------
def test_stale_feed_is_reported_as_stale_not_complete(tmp_path):
    # The exchange stops serving 1h candles before AS_OF: 1h is stale, not complete.
    rows = {timeframe: _rows(timeframe, as_of=AS_OF) for timeframe in TIMEFRAMES}
    rows["1h"] = [row for row in rows["1h"] if int(row[0]) <= _expected_latest("1h", AS_OF) - 3_600_000 * 5]
    engine, service = _service(tmp_path, BinanceKlinesFake(rows))
    report = ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    one_hour = next(item for item in report.coverage if item.timeframe == "1h")
    assert one_hour.state == STALE
    assert not one_hour.complete
    assert "stale" in one_hour.describe()


def test_insufficient_history_is_reported_and_never_padded(tmp_path):
    rows = {timeframe: _rows(timeframe, as_of=AS_OF, count=40) for timeframe in TIMEFRAMES}
    engine, service = _service(tmp_path, BinanceKlinesFake(rows))
    ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    item = coverage_for(service, symbol="BTC/USDT", timeframe="1h", as_of=AS_OF)
    assert item.state == INSUFFICIENT_HISTORY
    assert item.stored_in_window == 40  # genuine candles only; nothing synthesized


def test_missing_candle_inside_the_window_is_reported_and_left_missing(tmp_path):
    rows = {timeframe: _rows(timeframe, as_of=AS_OF) for timeframe in TIMEFRAMES}
    hole = _expected_latest("1h", AS_OF) - 3_600_000 * 20
    rows["1h"] = [row for row in rows["1h"] if int(row[0]) != hole]
    engine, service = _service(tmp_path, BinanceKlinesFake(rows))
    report = ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    item = next(entry for entry in report.coverage if entry.timeframe == "1h")
    assert item.state == MISSING_CANDLES
    assert item.missing_count >= 1
    assert _as_datetime(hole) not in _stored(engine, timeframe="1h")


# ----------------------------------------------------------------------
# Failure isolation, exchange isolation, lock
# ----------------------------------------------------------------------
def test_one_failing_timeframe_does_not_stop_the_others(tmp_path):
    source = BinanceKlinesFake(
        {timeframe: _rows(timeframe, as_of=AS_OF) for timeframe in TIMEFRAMES},
        failing=("15m",),
    )
    engine, service = _service(tmp_path, source)
    report = ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    by_tf = {result.timeframe: result for result in report.results}
    assert by_tf["15m"].status == "ERROR"
    assert by_tf["15m"].transient is True
    assert by_tf["15m"].error_type == "ExchangeDataError"
    assert "ConnectionError" in by_tf["15m"].error
    for timeframe in ("5m", "1h", "4h"):
        assert by_tf[timeframe].status == "OK"
    assert report.error_count == 1
    assert {item.timeframe: item.state for item in report.coverage}["15m"] == NO_DATA


def test_kraken_rows_are_never_counted_as_binance_coverage(tmp_path):
    engine, service = _service(tmp_path, BinanceKlinesFake({}))
    interval = timeframe_to_milliseconds("1h")
    legacy = [
        Candle(
            exchange="kraken",
            symbol="BTC/USDT",
            timeframe="1h",
            timestamp=_as_datetime(_expected_latest("1h", AS_OF) - (HISTORY - 1 - step) * interval),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("1"),
        )
        for step in range(HISTORY)
    ]
    CandleRepository(engine).insert_unchanged_or_new(legacy)
    item = coverage_for(service, symbol="BTC/USDT", timeframe="1h", as_of=AS_OF)
    assert item.state == NO_DATA
    assert item.stored_in_window == 0
    assert _stored(engine, exchange="kraken", timeframe="1h")  # legacy rows untouched


def test_second_ingestion_process_is_refused_while_the_lock_is_held(tmp_path):
    lock_path = tmp_path / "ingestion.lock"
    with SingleInstanceLock(lock_path):
        with pytest.raises(IngestionLockError):
            with SingleInstanceLock(lock_path):
                pass  # pragma: no cover - must not be reached
    # Released on exit: a later process can take it.
    with SingleInstanceLock(lock_path):
        pass


def test_coverage_describes_the_gate_depth_and_timeframe(tmp_path):
    engine, service = _service(tmp_path, _all_timeframes_source())
    ingest_once(service, symbol="BTC/USDT", as_of=AS_OF)
    item = coverage_for(service, symbol="BTC/USDT", timeframe="4h", as_of=AS_OF)
    assert item.required_depth == service.required_depth()
    assert item.complete
    assert "4h" in item.describe()
