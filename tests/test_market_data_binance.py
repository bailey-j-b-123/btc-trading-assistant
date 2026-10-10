"""Binance Spot BTC/USDT market-data integration: offline, deterministic tests.

Everything here is offline: a fake date-bounded klines source models Binance's
public spot endpoint contract, and one test drives the REAL CCXT Binance
parsers through stubbed HTTP endpoints (no network). Nothing contacts Binance;
live connectivity is verified separately by operations, not by these tests.

Covered contract:

* the source advertises Binance's 1000-candle per-request cap and keeps the
  millisecond cursor (Binance klines are date-bounded, not a rolling window);
* historical closed candles for 5m / 15m / 1h / 4h with correct pagination,
  UTC-aligned open times, OHLCV validation and gap detection;
* the still-forming candle is excluded from storage (closed history only);
* Binance and Kraken histories coexist under separate exchange identities;
* re-downloads are idempotent and conflicting values for a stored key are
  refused without changing stored history.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data import (
    CandleValidationError,
    HistoricalCandleConflict,
    MarketDataService,
)
from trading_assistant.market_data.exchange import CCXTMarketDataSource
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    milliseconds_to_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: Binance spot klines endpoint: at most 1000 candles per request.
BINANCE_MAX_KLINES = 1000

HIERARCHY_TIMEFRAMES = ("5m", "15m", "1h", "4h")


def candle_row(timestamp_ms: int, *, open_value="10.1", high="11.2", low="9.8", close="10.7", volume="3.25"):
    return [timestamp_ms, open_value, high, low, close, volume]


def binance_rows(start_ms: int, count: int, interval_ms: int, *, forming: bool = True):
    """Ascending klines rows exactly like the endpoint serves them.

    The final row models the candle that has not closed yet when ``forming`` is
    set: the service must exclude it from stored history.
    """

    rows = [
        candle_row(start_ms + index * interval_ms)
        for index in range(count)
    ]
    if forming:
        rows.append(candle_row(start_ms + count * interval_ms))
    return rows


class FakeBinanceSource:
    """Deterministic date-bounded stand-in for Binance's public klines endpoint.

    Binance serves up to ``limit`` klines whose open time is at or after
    ``startTime`` — a normal date-bounded endpoint, so the generic cursor
    pagination applies and the millisecond cursor is always sent.
    """

    exchange_id = "binance"
    max_ohlcv_limit = BINANCE_MAX_KLINES
    ohlcv_is_rolling_window = False

    def __init__(self, rows):
        self.rows = sorted(rows, key=lambda row: int(row[0]))
        self.last_http_response = None
        self.requests: list[tuple[str, str, int, int]] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.requests.append((symbol, timeframe, since_ms, limit))
        rows = [row for row in self.rows if int(row[0]) >= since_ms]
        return rows[:limit]

    def close(self):
        return None


def configured_settings(tmp_path: Path, *, page_limit: int = 100, **overrides) -> Settings:
    base = dict(
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        exchange="binance",
        default_timeframe="1h",
        supported_timeframes=("5m", "15m", "1h", "4h"),
        raw_data_dir=tmp_path / "raw",
        market_data_page_limit=page_limit,
        market_data_max_pages=1_000,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def migrate_database(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def create_service(tmp_path: Path, source, *, page_limit: int = 100, as_of: datetime):
    database_path = tmp_path / "binance.sqlite3"
    url = f"sqlite:///{database_path}"
    engine = create_database_engine(url)
    migrate_database(url)
    settings = configured_settings(tmp_path, page_limit=page_limit)
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: as_of,
    )
    return engine, service


def stored_count(engine, exchange: str = "binance") -> int:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT COUNT(*) FROM ohlcv_candles WHERE exchange = :exchange"),
            {"exchange": exchange},
        ).scalar_one()


# ----------------------------------------------------------------------
# Source-level contract
# ----------------------------------------------------------------------


def test_binance_source_is_constructed_without_network_and_not_rolling_window():
    source = CCXTMarketDataSource("binance")
    try:
        assert source.exchange_id == "binance"
        assert not source._exchange.apiKey
        assert source.max_ohlcv_limit == BINANCE_MAX_KLINES
        # Date-bounded endpoint: the generic cursor pagination applies.
        assert source.ohlcv_is_rolling_window is False
    finally:
        source.close()


@pytest.mark.parametrize("exchange_id", ["kraken", "coinbase", ""])
def test_ccxt_source_rejects_non_binance_exchange_ids(exchange_id):
    with pytest.raises(ValueError, match="only Binance Spot"):
        CCXTMarketDataSource(exchange_id)


class RecordingBinanceCCXTClient:
    """Offline stand-in for the unified CCXT Binance client."""

    def __init__(self, rows=()):
        self.id = "binance"
        self.timeframes = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
        self.last_http_response = None
        self.rows = list(rows)
        self.requests: list[tuple[str, str, int | None, int]] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
        self.requests.append((symbol, timeframe, since, limit))
        return [row for row in self.rows if int(row[0]) >= since][:limit]

    def close(self):  # pragma: no cover - test client owns no resources
        return None


def test_binance_source_keeps_the_cursor_and_applies_the_1000_candle_cap():
    source = CCXTMarketDataSource.__new__(CCXTMarketDataSource)
    client = RecordingBinanceCCXTClient()
    source._exchange = client
    source.exchange_id = "binance"
    source.last_http_response = None

    source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=1_790_424_000_000, limit=5_000)

    assert client.requests == [("BTC/USDT", "1h", 1_790_424_000_000, BINANCE_MAX_KLINES)]
    assert source.max_ohlcv_limit == BINANCE_MAX_KLINES


# ----------------------------------------------------------------------
# Full-stack offline test through the REAL CCXT Binance parsers
# ----------------------------------------------------------------------

BINANCE_EXCHANGE_INFO = {
    "timezone": "UTC",
    "serverTime": 1_700_000_000_000,
    "rateLimits": [],
    "exchangeFilters": [],
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "status": "TRADING",
            "baseAsset": "BTC",
            "baseAssetPrecision": 8,
            "quoteAsset": "USDT",
            "quotePrecision": 8,
            "quoteAssetPrecision": 8,
            "orderTypes": ["LIMIT", "MARKET"],
            "icebergAllowed": True,
            "ocoAllowed": True,
            "isSpotTradingAllowed": True,
            "isMarginTradingAllowed": True,
            "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
                 "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "minQty": "0.00001000",
                 "maxQty": "9000.00000000", "stepSize": "0.00001000"},
                {"filterType": "MIN_NOTIONAL", "minNotional": "10.00000000"},
            ],
            "permissions": ["SPOT"],
        }
    ],
}


def stub_binance_market_endpoints(exchange, monkeypatch):
    """Return deterministic Binance exchangeInfo/klines responses through real CCXT parsers.

    CCXT's Binance ``fetch_markets`` loads spot, linear and inverse market
    lists; only the spot list carries the BTCUSDT symbol, the derivatives lists
    are empty. Without credentials ``fetch_currencies`` is skipped by CCXT.
    """

    calls = {"exchange_info": [], "klines": []}
    state = {"klines": []}

    def public_get_exchange_info(params=None):
        calls["exchange_info"].append((params, exchange.number))
        return BINANCE_EXCHANGE_INFO

    def empty_exchange_info(params=None):
        return {"symbols": []}

    def public_get_klines(params=None):
        calls["klines"].append((dict(params or {}), exchange.number))
        since = (params or {}).get("startTime")
        limit = (params or {}).get("limit")
        rows = [row for row in state["klines"] if since is None or row[0] >= since]
        return rows[:limit]

    monkeypatch.setattr(exchange, "publicGetExchangeInfo", public_get_exchange_info)
    monkeypatch.setattr(exchange, "fapiPublicGetExchangeInfo", empty_exchange_info)
    monkeypatch.setattr(exchange, "dapiPublicGetExchangeInfo", empty_exchange_info)
    monkeypatch.setattr(exchange, "publicGetKlines", public_get_klines)
    return calls, state


def test_binance_source_loads_markets_and_parses_klines_with_decimal_precision(
    tmp_path, monkeypatch
):
    """The real CCXT Binance path works with Decimal parsing and stores closed candles only.

    Binance's market loader tolerates ``number = Decimal`` directly, so no
    float detour is needed: markets lazy-load inside ``fetch_ohlcv`` under the
    same watchdog, and kline values keep their exact source precision.
    """

    # CCXT's since-filter treats timestamp 0 as missing, so start at a real,
    # timeframe-aligned UTC instant instead of the Unix epoch.
    start = datetime(2024, 1, 1, tzinfo=UTC)
    start_ms = datetime_to_milliseconds(start)
    source = CCXTMarketDataSource("binance")
    calls, state = stub_binance_market_endpoints(source._exchange, monkeypatch)
    # Two 1h klines: one closed, one still forming at the as_of boundary.
    state["klines"] = [
        [start_ms, "10.1", "11.2", "9.8", "10.7", "3.25", start_ms + 3_599_999, "34.775", 12, "1.5", "16.05", "0"],
        [start_ms + 3_600_000, "10.7", "11.5", "10.2", "11.0", "4.5", start_ms + 7_199_999, "49.5", 9, "2.0", "22.0", "0"],
    ]
    engine, service = create_service(
        tmp_path, source, page_limit=720, as_of=start + timedelta(hours=1, seconds=1)
    )
    try:
        result = service.download_history(
            start_time=start,
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=start + timedelta(hours=1, seconds=1),
        )

        # The klines request carried the cursor and the known page cap.
        assert len(calls["klines"]) == 1
        request, request_number = calls["klines"][0]
        assert request["symbol"] == "BTCUSDT"
        assert request["interval"] == "1h"
        assert request["startTime"] == start_ms
        assert request["limit"] == 720
        assert request_number is Decimal
        assert source._exchange.number is Decimal
        # Exactly the closed candle is stored; the forming kline is excluded.
        assert result.received_count == 2
        assert result.accepted_count == result.inserted_count == 1
        assert result.excluded_open_count == 1
        assert result.complete
        stored = service.get_candles(
            exchange="binance", symbol="BTC/USDT", timeframe="1h"
        ).candles
        assert len(stored) == 1
        assert stored[0].timestamp == start
        assert stored[0].exchange == "binance"
        assert stored[0].close == Decimal("10.7")
        assert stored[0].volume == Decimal("3.25")
    finally:
        source.close()
        engine.dispose()


# ----------------------------------------------------------------------
# Service-level behaviour for every hierarchy timeframe
# ----------------------------------------------------------------------


@pytest.mark.parametrize("timeframe", HIERARCHY_TIMEFRAMES)
def test_binance_download_paginates_utc_aligned_closed_candles(tmp_path, timeframe):
    interval_ms = timeframe_to_milliseconds(timeframe)
    candle_count = 20  # page_limit 7 forces several cursor-advanced pages
    rows = binance_rows(0, candle_count, interval_ms, forming=True)
    source = FakeBinanceSource(rows)
    as_of = datetime.fromtimestamp((candle_count * interval_ms + 30) / 1000, tz=UTC)
    engine, service = create_service(tmp_path, source, page_limit=7, as_of=as_of)
    try:
        result = service.download_history(
            start_time=EPOCH,
            symbol="BTC/USDT",
            timeframe=timeframe,
            as_of=as_of,
        )

        # Pagination advanced by the requested timeframe from the last page row.
        cursors = [request[2] for request in source.requests]
        assert cursors[0] == 0
        for previous, current in zip(cursors, cursors[1:]):
            assert current > previous
            assert (current - previous) % interval_ms == 0
        assert all(request[3] == 7 for request in source.requests)
        # Every requested page kept the millisecond cursor (date-bounded).
        assert all(request[2] is not None for request in source.requests)

        assert result.complete
        assert result.excluded_open_count == 1  # the forming kline is never stored
        assert result.inserted_count == candle_count
        assert result.received_count == candle_count + 1
        stored = service.get_candles(
            exchange="binance", symbol="BTC/USDT", timeframe=timeframe
        ).candles
        assert len(stored) == candle_count
        for index, candle in enumerate(stored):
            expected_ms = index * interval_ms
            assert candle.exchange == "binance"
            assert candle.symbol == "BTC/USDT"
            assert candle.timeframe == timeframe
            assert datetime_to_milliseconds(candle.timestamp) == expected_ms
            assert is_timeframe_aligned(expected_ms, timeframe)
            assert candle.timestamp.tzinfo is not None
        assert stored_count(engine) == candle_count
    finally:
        engine.dispose()


def test_binance_page_limit_respects_the_1000_candle_cap(tmp_path):
    interval_ms = timeframe_to_milliseconds("1h")
    rows = binance_rows(0, 3, interval_ms, forming=False)
    source = FakeBinanceSource(rows)
    as_of = EPOCH + timedelta(hours=4)
    engine, service = create_service(tmp_path, source, page_limit=5_000, as_of=as_of)
    try:
        result = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe="1h", as_of=as_of
        )
        assert result.inserted_count == 3
        # The configured page limit is clamped to Binance's per-request cap on
        # every request (the trailing empty page only confirms the range end).
        assert len(source.requests) >= 1
        assert all(request[3] == BINANCE_MAX_KLINES for request in source.requests)
    finally:
        engine.dispose()


@pytest.mark.parametrize("timeframe", HIERARCHY_TIMEFRAMES)
def test_binance_download_reports_gaps_and_never_fabricates_candles(tmp_path, timeframe):
    interval_ms = timeframe_to_milliseconds(timeframe)
    rows = binance_rows(0, 10, interval_ms, forming=False)
    del rows[4]  # one missing candle inside the requested range
    source = FakeBinanceSource(rows)
    as_of = EPOCH + timedelta(milliseconds=interval_ms * 10 + 1_000)
    engine, service = create_service(tmp_path, source, page_limit=100, as_of=as_of)
    try:
        result = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe=timeframe, as_of=as_of
        )

        assert not result.complete
        assert result.missing_candle_count == 1
        assert len(result.gaps) == 1
        gap = result.gaps[0]
        assert datetime_to_milliseconds(gap.start) == 4 * interval_ms
        assert datetime_to_milliseconds(gap.end) == 4 * interval_ms
        assert gap.missing_count == 1
        # The hole is stored as a hole: nine candles, none synthesized.
        assert result.inserted_count == 9
        stored = service.get_candles(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe=timeframe,
            start_time=EPOCH,
            end_time=EPOCH + timedelta(milliseconds=interval_ms * 9),
        )
        assert len(stored.candles) == 9
        assert not stored.complete
        assert stored.missing_candle_count == 1
        timestamps = [datetime_to_milliseconds(candle.timestamp) for candle in stored.candles]
        assert 4 * interval_ms not in timestamps
    finally:
        engine.dispose()


@pytest.mark.parametrize("timeframe", HIERARCHY_TIMEFRAMES)
def test_binance_download_rejects_invalid_ohlcv_and_leaves_storage_unchanged(tmp_path, timeframe):
    interval_ms = timeframe_to_milliseconds(timeframe)
    rows = binance_rows(0, 5, interval_ms, forming=False)
    rows[2] = candle_row(2 * interval_ms, high="9.0")  # high < low: invalid OHLC
    source = FakeBinanceSource(rows)
    as_of = EPOCH + timedelta(milliseconds=interval_ms * 6)
    engine, service = create_service(tmp_path, source, page_limit=100, as_of=as_of)
    try:
        with pytest.raises(CandleValidationError) as excinfo:
            service.download_history(
                start_time=EPOCH, symbol="BTC/USDT", timeframe=timeframe, as_of=as_of
            )
        assert any(issue.code == "invalid_ohlc" for issue in excinfo.value.report.issues)
        assert stored_count(engine) == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("timeframe", HIERARCHY_TIMEFRAMES)
def test_binance_download_rejects_unaligned_open_times_for_every_hierarchy_timeframe(
    tmp_path, timeframe
):
    interval_ms = timeframe_to_milliseconds(timeframe)
    rows = binance_rows(0, 5, interval_ms, forming=False)
    rows[2][0] += 1  # not a UTC-aligned candle open
    source = FakeBinanceSource(rows)
    as_of = EPOCH + timedelta(milliseconds=interval_ms * 6)
    engine, service = create_service(tmp_path, source, page_limit=100, as_of=as_of)
    try:
        with pytest.raises(CandleValidationError) as excinfo:
            service.download_history(
                start_time=EPOCH, symbol="BTC/USDT", timeframe=timeframe, as_of=as_of
            )
        assert any(issue.code == "unaligned_timestamp" for issue in excinfo.value.report.issues)
        assert stored_count(engine) == 0
    finally:
        engine.dispose()


def test_binance_and_kraken_histories_coexist_under_separate_exchange_identities(tmp_path):
    interval_ms = timeframe_to_milliseconds("1h")
    kraken_candles = tuple(
        Candle(
            exchange="kraken",
            symbol="BTC/USDT",
            timeframe="1h",
            timestamp=milliseconds_to_datetime(index * interval_ms),
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("10"),
        )
        for index in range(3)
    )
    binance_rows_data = binance_rows(0, 4, interval_ms, forming=False)
    source = FakeBinanceSource(binance_rows_data)
    as_of = EPOCH + timedelta(hours=5)
    engine, service = create_service(tmp_path, source, page_limit=100, as_of=as_of)
    try:
        CandleRepository(engine).insert_unchanged_or_new(kraken_candles)
        result = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe="1h", as_of=as_of
        )
        assert result.inserted_count == 4

        binance_view = service.get_candles(exchange="binance", symbol="BTC/USDT", timeframe="1h")
        kraken_view = service.get_candles(exchange="kraken", symbol="BTC/USDT", timeframe="1h")
        assert len(binance_view.candles) == 4
        assert len(kraken_view.candles) == 3
        assert {candle.exchange for candle in binance_view.candles} == {"binance"}
        assert {candle.exchange for candle in kraken_view.candles} == {"kraken"}
        # Identities are independent: same symbol/timeframe keys, separate series.
        assert stored_count(engine, "binance") == 4
        assert stored_count(engine, "kraken") == 3
        repository = CandleRepository(engine)
        assert repository.latest_timestamp(
            exchange="binance", symbol="BTC/USDT", timeframe="1h"
        ) == EPOCH + timedelta(hours=3)
        assert repository.latest_timestamp(
            exchange="kraken", symbol="BTC/USDT", timeframe="1h"
        ) == EPOCH + timedelta(hours=2)
    finally:
        engine.dispose()


def test_binance_update_history_continues_after_the_latest_stored_candle(tmp_path):
    interval_ms = timeframe_to_milliseconds("1h")
    rows = binance_rows(0, 5, interval_ms, forming=False)
    source = FakeBinanceSource(rows)
    clock = {"as_of": EPOCH + timedelta(hours=5)}  # latest closed candle: 4h
    database_path = tmp_path / "binance.sqlite3"
    url = f"sqlite:///{database_path}"
    engine = create_database_engine(url)
    migrate_database(url)
    settings = configured_settings(tmp_path, page_limit=100)
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: clock["as_of"],
    )
    try:
        initial = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe="1h"
        )
        assert initial.complete
        assert initial.inserted_count == 5

        # Three more closed candles appear on the exchange and the clock moves
        # past their closes (5h, 6h, 7h all closed by 8h).
        source.rows = binance_rows(0, 8, interval_ms, forming=False)
        clock["as_of"] = EPOCH + timedelta(hours=8)
        updated = service.update_history(symbol="BTC/USDT", timeframe="1h")

        assert updated.complete
        assert updated.inserted_count == 3
        assert updated.already_present_count == 0
        assert updated.range_start == EPOCH + timedelta(hours=5)
        stored = service.get_candles(exchange="binance", symbol="BTC/USDT", timeframe="1h")
        assert len(stored.candles) == 8
        assert stored.complete
    finally:
        engine.dispose()


def test_binance_redownload_is_idempotent_and_conflicting_values_are_refused(tmp_path):
    interval_ms = timeframe_to_milliseconds("4h")
    rows = binance_rows(0, 6, interval_ms, forming=False)
    source = FakeBinanceSource(rows)
    as_of = EPOCH + timedelta(hours=4 * 6)  # latest closed candle: 20h
    engine, service = create_service(tmp_path, source, page_limit=100, as_of=as_of)
    try:
        first = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe="4h", as_of=as_of
        )
        assert first.inserted_count == 6

        second = service.download_history(
            start_time=EPOCH, symbol="BTC/USDT", timeframe="4h", as_of=as_of
        )
        assert second.inserted_count == 0
        assert second.already_present_count == 6
        assert stored_count(engine) == 6

        # A different (still valid) value for an already-stored key is refused,
        # never rewritten.
        mutated = [list(row) for row in rows]
        mutated[2][4] = "10.9"  # valid close inside [low, high], different from stored
        source.rows = sorted(mutated, key=lambda row: int(row[0]))
        with pytest.raises(HistoricalCandleConflict):
            service.download_history(
                start_time=EPOCH, symbol="BTC/USDT", timeframe="4h", as_of=as_of
            )
        stored = service.get_candles(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="4h",
            start_time=EPOCH + timedelta(hours=8),
            end_time=EPOCH + timedelta(hours=8),
        )
        assert stored.candles[0].close == Decimal("10.7")  # original value preserved
    finally:
        engine.dispose()
