from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import ccxt
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data import (
    CandleValidationError,
    ExchangeDataError,
    HistoricalCandleConflict,
    MarketDataService,
    parse_ohlcv_row,
    validate_ohlcv_rows,
)
from trading_assistant.market_data.exchange import CCXTMarketDataSource
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    latest_closed_candle_open_time,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FIVE_MINUTES_MS = 5 * 60 * 1_000

#: The millisecond cursor the live Step 12 bootstrap derived on 2026-10-06
#: (2026-09-26T12:00:00Z).  It is a plain local cursor, not an exchange value.
SINCE_MS = 1_790_424_000_000


def candle_row(timestamp_ms: int, *, open_value="10.1", high="11.2", low="9.8", close="10.7", volume="3.25"):
    return [timestamp_ms, open_value, high, low, close, volume]


class FakeSource:
    exchange_id = "mock-exchange"

    def __init__(self, rows, *, fail_on_call: int | None = None):
        self.rows = list(rows)
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.last_http_response = None
        self.requests: list[int] = []
        self.request_limits: list[int] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        self.calls += 1
        self.requests.append(since_ms)
        self.request_limits.append(limit)
        if self.fail_on_call == self.calls:
            raise ConnectionError("simulated offline exchange")
        return [row for row in self.rows if int(row[0]) >= since_ms][:limit]


class RecordingCCXTClient:
    """Offline stand-in for the unified CCXT exchange client."""

    def __init__(self, exchange_id: str, rows=()):
        self.id = exchange_id
        self.timeframes = {"5m": "5m", "1h": "60"}
        self.last_http_response = None
        self.rows = list(rows)
        self.requests: list[tuple[str, str, int | None, int]] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
        self.requests.append((symbol, timeframe, since, limit))
        if since is None:
            # No date cursor: exactly like a rolling-window endpoint (Kraken's
            # public OHLC), only the newest entries can come back.
            return self.rows[-limit:]
        return [row for row in self.rows if int(row[0]) >= since][:limit]

    def close(self):  # pragma: no cover - test client owns no resources
        return None


def ccxt_source_for(exchange_id: str, client: RecordingCCXTClient) -> CCXTMarketDataSource:
    """Wrap the fake CCXT client without constructing or contacting an exchange."""

    source = CCXTMarketDataSource.__new__(CCXTMarketDataSource)
    source._exchange = client
    source.exchange_id = exchange_id
    source.last_http_response = None
    return source


def stub_kraken_market_endpoints(exchange, monkeypatch):
    """Return deterministic Kraken metadata/OHLC responses through real CCXT parsers."""

    calls = {"assets": [], "asset_pairs": [], "ohlc": []}
    assets = {
        "error": [],
        "result": {
            "XXBT": {
                "aclass": "currency",
                "altname": "XBT",
                "decimals": 8,
                "display_decimals": 5,
                "status": "enabled",
            },
            "USDT": {
                "aclass": "currency",
                "altname": "USDT",
                "decimals": 8,
                "display_decimals": 4,
                "status": "enabled",
            },
        },
    }
    asset_pairs = {
        "error": [],
        "result": {
            "XBTUSDT": {
                "altname": "XBTUSDT",
                "wsname": "XBT/USDT",
                "aclass_base": "currency",
                "base": "XXBT",
                "aclass_quote": "currency",
                "quote": "USDT",
                "lot": "unit",
                "pair_decimals": 2,
                "lot_decimals": 8,
                "lot_multiplier": 1,
                "leverage_buy": [],
                "leverage_sell": [],
                "fees": [[0, 0.26]],
                "fees_maker": [[0, 0.16]],
                "status": "online",
            }
        },
    }
    ohlc = {
        "error": [],
        "result": {
            "XBTUSDT": [
                [0, "10.1", "11.2", "9.8", "10.7", "10.4", "3.25", 1],
                [3_600, "10.7", "11.5", "10.2", "11.0", "10.8", "4.5", 1],
            ],
            "last": 3_600,
        },
    }

    def fetch_assets(params=None):
        calls["assets"].append((params, exchange.number))
        return assets

    def fetch_asset_pairs(params=None):
        calls["asset_pairs"].append((params, exchange.number))
        return asset_pairs

    def fetch_ohlc(params=None):
        calls["ohlc"].append((params, exchange.number))
        return ohlc

    monkeypatch.setattr(exchange, "publicGetAssets", fetch_assets)
    monkeypatch.setattr(exchange, "publicGetAssetPairs", fetch_asset_pairs)
    monkeypatch.setattr(exchange, "publicGetOHLC", fetch_ohlc)
    return calls


class KrakenRollingWindowCCXTClient:
    """Offline CCXT client that models the observed Kraken public OHLC behaviour.

    Kraken's OHLC route serves only its newest 720 entries, so a start timestamp
    can never retrieve older history from it, and the live runner request that
    carried Step 2's locally-derived cursor failed with ``EGeneral:Invalid
    arguments:since``.  This double reproduces that endpoint contract exactly:
    any request carrying a ``since`` argument is rejected with the same exchange
    error, while a request without one receives the newest entries — including
    the interval that has not closed yet.
    """

    def __init__(self, rows=(), *, max_entries: int = 720) -> None:
        self.id = "kraken"
        self.timeframes = {"5m": "5m", "1h": "60"}
        self.last_http_response = None
        self.rows = list(rows)
        self.max_entries = max_entries
        self.requests: list[tuple[str, str, int | None, int]] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
        self.requests.append((symbol, timeframe, since, limit))
        if since is not None:
            raise ccxt.BadRequest(
                "kraken GET https://api.kraken.com/0/public/OHLC: "
                "EGeneral:Invalid arguments:since"
            )
        newest = self.rows[-self.max_entries :]
        return newest[-limit:]

    def close(self):  # pragma: no cover - test client owns no resources
        return None


class CursorForwardingKrakenSource:
    """The pre-fix Step 2 boundary: the local cursor is forwarded unchanged."""

    exchange_id = "kraken"
    max_ohlcv_limit = 720
    ohlcv_is_rolling_window = True

    def __init__(self, client: KrakenRollingWindowCCXTClient) -> None:
        self._client = client
        self.last_http_response = None

    def fetch_ohlcv(self, symbol, *, timeframe, since_ms, limit):
        return self._client.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since_ms, limit=limit
        )


def configured_settings(
    tmp_path: Path,
    *,
    page_limit: int = 100,
    exchange: str = "mock-exchange",
    symbol: str = "ETH/USDT",
    base_asset: str = "ETH",
    quote_asset: str = "USDT",
) -> Settings:
    return Settings(
        _env_file=None,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        exchange=exchange,
        default_timeframe="5m",
        supported_timeframes=("5m", "15m", "1h", "4h", "1d"),
        raw_data_dir=tmp_path / "raw",
        market_data_page_limit=page_limit,
        market_data_max_pages=20,
    )


def migrate_database(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def create_service(
    tmp_path: Path,
    source,
    *,
    page_limit: int = 100,
    exchange: str = "mock-exchange",
    symbol: str = "ETH/USDT",
    base_asset: str = "ETH",
    quote_asset: str = "USDT",
):
    database_path = tmp_path / "market.sqlite3"
    engine = create_database_engine(f"sqlite:///{database_path}")
    migrate_database(f"sqlite:///{database_path}")
    settings = configured_settings(
        tmp_path,
        page_limit=page_limit,
        exchange=exchange,
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
    )
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: EPOCH + timedelta(days=10),
    )
    return engine, service


def test_ccxt_source_is_public_and_defers_decimal_parsing_until_after_market_load():
    source = CCXTMarketDataSource("kraken")
    try:
        assert source.exchange_id == "kraken"
        assert not source._exchange.apiKey
        # CCXT market discovery must retain its default parser; Decimal is
        # applied immediately after discovery and before OHLCV parsing.
        assert source._exchange.number is float
    finally:
        source.close()


def test_kraken_decimal_market_precision_failure_is_reproduced(monkeypatch):
    """The pre-fix Decimal configuration fails before Kraken's OHLC request.

    Keep the exact CCXT failure visible when the installed version includes
    Kraken's currencyPrecision guard; older supported CCXT versions predate it.
    """

    fetch_markets_source = inspect.getsource(ccxt.kraken.fetch_markets)
    if "method() missing currencyPrecision" not in fetch_markets_source:
        pytest.skip("installed CCXT version predates the Kraken currencyPrecision guard")

    broken_exchange = ccxt.kraken({"enableRateLimit": True})
    broken_calls = stub_kraken_market_endpoints(broken_exchange, monkeypatch)
    broken_exchange.number = Decimal
    try:
        with pytest.raises(
            ccxt.ExchangeError,
            match=r"kraken method\(\) missing currencyPrecision",
        ):
            broken_exchange.fetch_ohlcv(
                "BTC/USDT", timeframe="1h", since=None, limit=720
            )
        assert len(broken_calls["assets"]) == 1
        assert len(broken_calls["asset_pairs"]) == 1
        assert broken_calls["ohlc"] == []
    finally:
        broken_exchange.close()


def test_kraken_source_loads_markets_before_decimal_parsing_and_stores_closed_candles(
    tmp_path, monkeypatch
):
    """Apply Decimal after CCXT metadata loading and retain Step 2 protections."""

    source = CCXTMarketDataSource("kraken")
    calls = stub_kraken_market_endpoints(source._exchange, monkeypatch)
    engine, service = create_service(
        tmp_path,
        source,
        page_limit=720,
        exchange="kraken",
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
    )
    try:
        result = service.download_history(
            start_time=EPOCH,
            symbol="BTC/USDT",
            timeframe="1h",
            as_of=EPOCH + timedelta(hours=1, seconds=1),
        )

        assert len(calls["assets"]) == len(calls["asset_pairs"]) == 1
        assert calls["assets"][0][1] is float
        assert calls["asset_pairs"][0][1] is float
        assert calls["ohlc"] == [({"pair": "XBTUSDT", "interval": 60}, Decimal)]
        assert source._exchange.number is Decimal
        assert result.received_count == 2
        assert result.accepted_count == result.inserted_count == 1
        assert result.excluded_open_count == 1
        assert result.complete
        stored = service.get_candles(
            exchange="kraken", symbol="BTC/USDT", timeframe="1h"
        ).candles
        assert len(stored) == 1
        assert stored[0].timestamp == EPOCH
        assert stored[0].close == Decimal("10.7")
        assert stored[0].volume == Decimal("3.25")
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM ohlcv_candles")
            ).scalar_one()
            assert count == 1
    finally:
        source.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("exchange_id", "requested_limit", "expected_limit", "expected_since", "expected_cap"),
    [
        # A rolling-window endpoint never receives the local date cursor.
        ("kraken", 5_000, 720, None, 720),
        ("kraken", 5, 5, None, 720),
        # Date-bounded exchanges keep receiving the unchanged millisecond
        # cursor; a project-known per-request cap is still applied. Binance's
        # public spot klines endpoint serves at most 1000 candles per request.
        ("binance", 5_000, 1_000, 0, 1_000),
        ("binance", 500, 500, 0, 1_000),
        # Exchanges without a project-known cap pass the limit through.
        ("coinbase", 5_000, 5_000, 0, None),
    ],
)
def test_ccxt_source_uses_only_known_exchange_ohlcv_caps(
    exchange_id: str,
    requested_limit: int,
    expected_limit: int,
    expected_since: int | None,
    expected_cap: int | None,
):
    client = RecordingCCXTClient(exchange_id)
    source = ccxt_source_for(exchange_id, client)

    source.fetch_ohlcv(
        "BTC/USD",
        timeframe="1h",
        since_ms=0,
        limit=requested_limit,
    )

    assert client.requests == [("BTC/USD", "1h", expected_since, expected_limit)]
    assert source.max_ohlcv_limit == expected_cap


def test_kraken_ccxt_request_omits_the_local_ms_cursor():
    """Step 12's bootstrap cursor is not a Kraken OHLC argument.

    Regression for the live Mac failure ``EGeneral:Invalid arguments:since``: the
    runner derived a local millisecond cursor for the bootstrap window, the
    adapter forwarded it to CCXT and CCXT sent it as a bare seconds ``since`` to
    Kraken's rolling-window endpoint, which rejected the request.  Kraken can
    never serve older candles from that cursor, so it must not be sent at all.
    """

    client = RecordingCCXTClient("kraken")
    source = ccxt_source_for("kraken", client)

    source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=SINCE_MS, limit=720)

    assert client.requests == [("BTC/USDT", "1h", None, 720)]


@pytest.mark.parametrize("exchange_id", ["binance", "coinbase", "bitstamp"])
def test_non_kraken_ccxt_requests_keep_the_ms_cursor(exchange_id: str):
    """Only Kraken changes: date-bounded exchanges keep the cursor unchanged."""

    client = RecordingCCXTClient(exchange_id)
    source = ccxt_source_for(exchange_id, client)

    source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=SINCE_MS, limit=720)

    assert client.requests == [("BTC/USDT", "1h", SINCE_MS, 720)]


def test_kraken_bootstrap_stores_the_requested_window_without_a_since_cursor(tmp_path):
    """A Step 12 cold start succeeds against the rolling-window endpoint.

    Before the fix the adapter handed CCXT the same cursor the modelled Kraken
    endpoint rejects, so the whole pass failed with ``ExchangeDataError`` and
    stored nothing.  The requested bootstrap depth, closed-candle filtering, gap
    reporting and the incremental update path all have to keep behaving exactly
    as they do for date-bounded exchanges.
    """

    # 242 closed candles from EPOCH plus the interval still forming.
    client = KrakenRollingWindowCCXTClient(
        [candle_row(index * FIVE_MINUTES_MS) for index in range(243)]
    )
    source = ccxt_source_for("kraken", client)
    engine, service = create_service(
        tmp_path,
        source,
        page_limit=720,
        exchange="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    try:
        result = service.download_history(
            start_time=EPOCH,
            as_of=EPOCH + timedelta(minutes=242 * 5, seconds=30),
        )

        # The cursor was never sent; the known exchange cap still was.
        assert client.requests == [("BTC/USD", "5m", None, 720)]
        # Exactly the requested closed window is stored: the still-forming candle
        # is excluded and the requested range has no gaps.
        assert result.received_count == 243
        assert result.accepted_count == result.inserted_count == 242
        assert result.excluded_open_count == 1
        assert result.complete
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT COUNT(*) FROM ohlcv_candles")).scalar_one()
                == 242
            )

        # The incremental path keeps the same contract: no cursor on the wire and
        # only the newly closed candle is stored.
        updated = service.update_history(
            as_of=EPOCH + timedelta(minutes=243 * 5, seconds=30)
        )
        assert client.requests[-1] == ("BTC/USD", "5m", None, 720)
        assert updated.inserted_count == 1
        assert updated.complete
    finally:
        engine.dispose()


def test_forwarded_kraken_cursor_reproduces_the_live_failure(tmp_path):
    """The double above really models the live failure, diagnostics included.

    A source that still forwards the local cursor fails exactly like the live Mac
    runner did, and the underlying exchange message stays visible in
    ``ExchangeDataError`` (the PR #15 diagnostic improvement).
    """

    client = KrakenRollingWindowCCXTClient(
        [candle_row(index * FIVE_MINUTES_MS) for index in range(243)]
    )
    engine, service = create_service(
        tmp_path,
        CursorForwardingKrakenSource(client),
        page_limit=720,
        exchange="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    try:
        with pytest.raises(ExchangeDataError) as excinfo:
            service.download_history(
                start_time=EPOCH,
                as_of=EPOCH + timedelta(minutes=242 * 5, seconds=30),
            )

        assert client.requests == [("BTC/USD", "5m", 0, 720)]
        assert "EGeneral:Invalid arguments:since" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, ccxt.BadRequest)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM ohlcv_candles")).scalar_one() == 0
    finally:
        engine.dispose()


def test_kraken_oversized_setting_is_capped_before_ccxt_and_archiving(tmp_path):
    client = RecordingCCXTClient(
        "kraken",
        [
            candle_row(0),
            candle_row(FIVE_MINUTES_MS),
            candle_row(2 * FIVE_MINUTES_MS),  # current, still-forming candle
        ],
    )
    source = ccxt_source_for("kraken", client)
    engine, service = create_service(
        tmp_path,
        source,
        page_limit=5_000,
        exchange="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    try:
        result = service.download_history(
            start_time=EPOCH,
            as_of=EPOCH + timedelta(minutes=10),
        )

        # Kraken is asked without the local cursor; the archive still records the
        # requested range the response is validated against.
        assert client.requests == [("BTC/USD", "5m", None, 720)]
        assert result.received_count == 3
        assert result.accepted_count == result.inserted_count == 2
        assert result.excluded_open_count == 1
        assert result.complete
        assert json.loads(result.raw_files[0].read_text(encoding="utf-8"))["request"] == {
            "since_ms": 0,
            "limit": 720,
        }
    finally:
        engine.dispose()


def test_kraken_rolling_window_is_not_date_paginated(tmp_path):
    # The requested range is larger than the 720 entries a rolling-window
    # endpoint can return, so a generic date-paginated source would ask for a
    # second page.  Kraken must still be treated as one rolling-window request;
    # the unavailable tail remains an explicit gap instead of being fabricated.
    client = RecordingCCXTClient(
        "kraken",
        [candle_row(index * FIVE_MINUTES_MS) for index in range(1_441)],
    )
    source = ccxt_source_for("kraken", client)
    engine, service = create_service(
        tmp_path,
        source,
        page_limit=5_000,
        exchange="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(milliseconds=2_000 * FIVE_MINUTES_MS),
            as_of=EPOCH + timedelta(milliseconds=2_001 * FIVE_MINUTES_MS),
        )

        assert source.ohlcv_is_rolling_window is True
        # One cursor-less request only: the newest 720 entries are returned and
        # the 1_281 earlier candles of the requested range are never invented.
        assert client.requests == [("BTC/USD", "5m", None, 720)]
        assert result.inserted_count == 720
        assert not result.complete
        assert result.missing_candle_count == 1_281
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM ohlcv_candles")).scalar_one() == 720
    finally:
        engine.dispose()


def test_kraken_history_older_than_public_window_is_reported_incomplete(tmp_path):
    # Kraken serves only its latest 720 candles; request a larger range and make
    # the unavailable earlier part observable rather than treating it as complete.
    first_available_index = 281
    last_available_index = 1_000
    client = RecordingCCXTClient(
        "kraken",
        [
            candle_row(index * FIVE_MINUTES_MS)
            for index in range(first_available_index, last_available_index + 1)
        ],
    )
    source = ccxt_source_for("kraken", client)
    engine, service = create_service(
        tmp_path,
        source,
        page_limit=5_000,
        exchange="kraken",
        symbol="BTC/USD",
        base_asset="BTC",
        quote_asset="USD",
    )
    try:
        result = service.download_history(
            start_time=EPOCH,
            as_of=EPOCH + timedelta(milliseconds=(last_available_index + 1) * FIVE_MINUTES_MS),
        )

        assert client.requests == [("BTC/USD", "5m", None, 720)]
        assert result.inserted_count == 720
        assert not result.complete
        assert result.missing_candle_count == first_available_index
        assert result.gaps[0].start == EPOCH
        assert result.gaps[0].end == EPOCH + timedelta(
            milliseconds=(first_available_index - 1) * FIVE_MINUTES_MS
        )
    finally:
        engine.dispose()


def test_parse_ccxt_row_uses_utc_and_decimal_values():
    candle = parse_ohlcv_row(
        [0, 0.1, 0.3, 0.05, 0.2, 0.1],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert candle.timestamp == EPOCH
    assert candle.timestamp.tzinfo is UTC
    assert candle.open == Decimal("0.1")
    assert candle.high == Decimal("0.3")
    assert isinstance(candle.volume, Decimal)


def test_datetime_conversion_is_explicitly_utc():
    plus_two = datetime.fromisoformat("2024-01-01T02:00:00+02:00")
    utc = datetime.fromisoformat("2024-01-01T00:00:00+00:00")

    assert datetime_to_milliseconds(plus_two) == datetime_to_milliseconds(utc)
    assert datetime_to_milliseconds(utc) % FIVE_MINUTES_MS == 0


def test_weekly_timeframe_uses_monday_utc_alignment():
    monday_open = EPOCH + timedelta(days=4)

    assert is_timeframe_aligned(datetime_to_milliseconds(monday_open), "1w")
    assert not is_timeframe_aligned(0, "1w")
    assert latest_closed_candle_open_time(monday_open + timedelta(days=7), "1w") == monday_open


def test_closed_candle_filter_excludes_current_forming_candle():
    report = validate_ohlcv_rows(
        [candle_row(0), candle_row(FIVE_MINUTES_MS), candle_row(2 * FIVE_MINUTES_MS)],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
        expected_start=EPOCH,
        expected_end=EPOCH + timedelta(minutes=5),
        closed_through=EPOCH + timedelta(minutes=5),
    )

    assert report.is_valid
    assert report.complete
    assert [candle.timestamp for candle in report.candles] == [EPOCH, EPOCH + timedelta(minutes=5)]
    assert report.excluded_open_count == 1


def test_validation_reports_duplicate_out_of_order_and_unaligned_timestamps():
    report = validate_ohlcv_rows(
        [
            candle_row(FIVE_MINUTES_MS),
            candle_row(0),
            candle_row(0),
            candle_row(1),
        ],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    codes = {issue.code for issue in report.issues}
    assert {"duplicate_timestamp", "out_of_order", "unaligned_timestamp"} <= codes
    assert report.rejected_count >= 3


def test_validation_reports_missing_candle_gaps_without_fabricating_data():
    report = validate_ohlcv_rows(
        [candle_row(0), candle_row(2 * FIVE_MINUTES_MS)],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
        expected_start=EPOCH,
        expected_end=EPOCH + timedelta(minutes=10),
    )

    assert len(report.candles) == 2
    assert report.missing_candle_count == 1
    assert report.gaps[0].start == EPOCH + timedelta(minutes=5)
    assert report.gaps[0].end == EPOCH + timedelta(minutes=5)
    assert not report.complete


def test_missing_and_non_numeric_values_are_reported():
    report = validate_ohlcv_rows(
        [candle_row(0, close=None), candle_row(FIVE_MINUTES_MS, volume="not-a-number")],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert report.rejected_count == 2
    assert all(issue.code == "malformed_value" for issue in report.issues)


def test_invalid_ohlc_and_negative_volume_are_rejected():
    report = validate_ohlcv_rows(
        [
            candle_row(0, open_value="12", high="11", low="10", close="10.5"),
            candle_row(FIVE_MINUTES_MS, volume="-0.01"),
        ],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )

    assert report.rejected_count == 2
    assert {issue.code for issue in report.issues} >= {"invalid_ohlc", "negative_volume"}
    with pytest.raises(CandleValidationError):
        parse_ohlcv_row(
            candle_row(0, open_value="12", high="11", low="10", close="10.5"),
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
        )


def test_download_is_closed_decimal_idempotent_and_retrieval_is_chronological(tmp_path):
    rows = [candle_row(0), candle_row(FIVE_MINUTES_MS), candle_row(2 * FIVE_MINUTES_MS)]
    source = FakeSource(rows)
    engine, service = create_service(tmp_path, source)
    as_of = EPOCH + timedelta(minutes=10)

    try:
        first = service.download_history(start_time=EPOCH, as_of=as_of)
        assert first.received_count == 3
        assert first.accepted_count == 2
        assert first.excluded_open_count == 1
        assert first.inserted_count == 2
        assert first.already_present_count == 0
        assert first.complete
        assert len(first.raw_files) == 1
        assert json.loads(first.raw_files[0].read_text(encoding="utf-8"))["exchange"] == "mock-exchange"
        with engine.connect() as connection:
            stored_numeric_type, stored_numeric_text = connection.execute(
                text("SELECT typeof(open), open FROM ohlcv_candles LIMIT 1")
            ).one()
        assert stored_numeric_type == "text"
        assert stored_numeric_text == "10.1"

        repeated = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=5),
            as_of=as_of,
        )
        assert repeated.inserted_count == 0
        assert repeated.already_present_count == 2

        rows.extend(
            [
                candle_row(3 * FIVE_MINUTES_MS),
                candle_row(4 * FIVE_MINUTES_MS),
                candle_row(5 * FIVE_MINUTES_MS),
                candle_row(6 * FIVE_MINUTES_MS),
            ]
        )
        source.rows = list(rows)
        updated = service.update_history(as_of=EPOCH + timedelta(minutes=30))
        assert updated.inserted_count == 4
        assert updated.excluded_open_count == 1
        assert updated.complete

        candles = service.get_candles(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
        )
        assert [candle.timestamp for candle in candles.candles] == [
            EPOCH + timedelta(minutes=5 * index) for index in range(6)
        ]
        assert candles.complete
        assert all(candle.timestamp.tzinfo is UTC for candle in candles.candles)
        assert candles.candles[0].open == Decimal("10.1")

        no_op = service.update_history(as_of=EPOCH + timedelta(minutes=30))
        assert no_op.inserted_count == 0
        assert no_op.received_count == 0
    finally:
        engine.dispose()


def test_retrieval_reports_gaps_in_stored_candle_range(tmp_path):
    source = FakeSource([candle_row(0), candle_row(2 * FIVE_MINUTES_MS)])
    engine, service = create_service(tmp_path, source)
    try:
        updated = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=10),
            as_of=EPOCH + timedelta(minutes=15),
        )
        retrieved = service.get_candles(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=10),
        )

        assert not updated.complete
        assert updated.missing_candle_count == 1
        assert not retrieved.complete
        assert retrieved.missing_candle_count == 1
        assert len(retrieved.candles) == 2
    finally:
        engine.dispose()


def test_download_paginates_and_advances_cursor_by_one_candle(tmp_path):
    rows = [candle_row(index * FIVE_MINUTES_MS) for index in range(4)]
    source = FakeSource(rows)
    engine, service = create_service(tmp_path, source, page_limit=2)
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=15),
            as_of=EPOCH + timedelta(minutes=20),
        )

        assert result.received_count == 4
        assert result.inserted_count == 4
        assert result.complete
        assert source.requests == [0, 2 * FIVE_MINUTES_MS]
        assert len(result.raw_files) == 2
    finally:
        engine.dispose()


def test_exchange_page_cap_does_not_truncate_required_history(tmp_path):
    rows = [candle_row(index * FIVE_MINUTES_MS) for index in range(4)]
    source = FakeSource(rows)
    # Simulate a source with a small hard per-request cap. The configured limit
    # is intentionally oversized; the service must request multiple pages.
    source.max_ohlcv_limit = 2
    engine, service = create_service(tmp_path, source, page_limit=5_000)
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=15),
            as_of=EPOCH + timedelta(minutes=20),
        )

        assert result.received_count == result.inserted_count == 4
        assert result.complete
        assert source.request_limits == [2, 2]
        assert source.requests == [0, 2 * FIVE_MINUTES_MS]
        archived_limits = [
            json.loads(path.read_text(encoding="utf-8"))["request"]["limit"]
            for path in result.raw_files
        ]
        assert archived_limits == [2, 2]
    finally:
        engine.dispose()


def test_incremental_exchange_failure_preserves_existing_history(tmp_path):
    source = FakeSource([candle_row(0), candle_row(FIVE_MINUTES_MS)])
    engine, service = create_service(tmp_path, source, page_limit=2)
    try:
        initial = service.download_history(start_time=EPOCH, as_of=EPOCH + timedelta(minutes=10))
        assert initial.inserted_count == 2

        source.rows = [
            candle_row(2 * FIVE_MINUTES_MS),
            candle_row(3 * FIVE_MINUTES_MS),
            candle_row(4 * FIVE_MINUTES_MS),
            candle_row(5 * FIVE_MINUTES_MS),
        ]
        source.calls = 0
        source.requests.clear()
        source.fail_on_call = 2

        with pytest.raises(ExchangeDataError):
            service.update_history(as_of=EPOCH + timedelta(minutes=30))

        candles = service.get_candles(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
        )
        assert [candle.timestamp for candle in candles.candles] == [EPOCH, EPOCH + timedelta(minutes=5)]
        assert source.requests == [2 * FIVE_MINUTES_MS, 4 * FIVE_MINUTES_MS]
        assert len(list((tmp_path / "raw").rglob("*.json"))) == 2
    finally:
        engine.dispose()


def test_exchange_error_message_survives_wrapping_and_logging(tmp_path, caplog):
    source = FakeSource([], fail_on_call=1)
    engine, service = create_service(tmp_path, source)
    try:
        with caplog.at_level("ERROR", logger="trading_assistant.market_data.service"):
            with pytest.raises(ExchangeDataError) as captured:
                service.download_history(
                    start_time=EPOCH,
                    end_time=EPOCH,
                    as_of=EPOCH + timedelta(minutes=5),
                )

        assert "underlying ConnectionError: simulated offline exchange" in str(
            captured.value
        )
        assert isinstance(captured.value.__cause__, ConnectionError)
        assert str(captured.value.__cause__) == "simulated offline exchange"
        failure_record = next(
            record
            for record in caplog.records
            if record.getMessage() == "Market-data exchange request failed"
        )
        assert failure_record.fields["error_type"] == "ConnectionError"
        assert failure_record.fields["error"] == "simulated offline exchange"
    finally:
        engine.dispose()


def test_invalid_download_is_rejected_before_database_insert(tmp_path):
    invalid = candle_row(0, open_value="12", high="11", low="10", close="10.5")
    source = FakeSource([invalid])
    engine, service = create_service(tmp_path, source)
    try:
        with pytest.raises(CandleValidationError):
            service.download_history(
                start_time=EPOCH,
                end_time=EPOCH,
                as_of=EPOCH + timedelta(minutes=5),
            )

        assert service.get_candles(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
        ).candles == ()
    finally:
        engine.dispose()


def test_conflicting_exchange_values_do_not_overwrite_existing_candle(tmp_path):
    source = FakeSource([candle_row(0)])
    engine, service = create_service(tmp_path, source)
    try:
        service.download_history(start_time=EPOCH, end_time=EPOCH, as_of=EPOCH + timedelta(minutes=5))
        source.rows = [candle_row(0, close="10.8")]

        with pytest.raises(HistoricalCandleConflict):
            service.download_history(start_time=EPOCH, end_time=EPOCH, as_of=EPOCH + timedelta(minutes=5))

        candle = service.get_candles(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="5m",
        ).candles[0]
        assert candle.close == Decimal("10.7")
    finally:
        engine.dispose()


def test_empty_initial_download_requires_explicit_start_for_incremental_api(tmp_path):
    source = FakeSource([])
    engine, service = create_service(tmp_path, source)
    try:
        with pytest.raises(ValueError, match="provide start_time"):
            service.update_history()
    finally:
        engine.dispose()
