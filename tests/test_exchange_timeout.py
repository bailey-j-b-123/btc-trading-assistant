"""Finite exchange-network timeout, watchdog, and transient classification.

Regression suite for the runtime outage in which the live forward runner
stayed alive (``ps`` showed it running) but stopped logging completed passes
for about an hour after the machine lost internet connectivity.

The blocking path, traced through the current main code and proven below:

    ForwardRunner.run
      -> ForwardTestService.run_once
        -> ForwardTestService._refresh_market_data
          -> MarketDataService.update_history / download_history
            -> CCXTMarketDataSource.fetch_ohlcv
              -> ccxt.Exchange.fetch
                -> requests.Session.request(timeout=self.timeout / 1000)

The project constructed the exchange with only ``{"enableRateLimit": True}``:
no project-controlled timeout, and CCXT's implicit default (10s) is only a
per-socket-operation timeout passed to ``requests``. ``socket.create_connection``
resolves DNS (``getaddrinfo``) *outside* that timeout, so during a connectivity
loss with an unresponsive resolver a single request can block a forward pass
indefinitely - and the runner's retry/stop logic can only act after the call
returns or raises. That is exactly why the process stayed alive with no
"Forward runner pass completed" logs: one pass was stuck inside one network
call. (The SQLite frames in the process samples were pool housekeeping, not
the blocking call.)

These tests pin, without any real internet:

* the configurable, validated, finite timeout and its CCXT wiring (CCXT
  timeout semantics are milliseconds; the same exchange instance - and
  therefore the same timeout - covers ``load_markets`` and ``fetch_ohlcv``);
* the watchdog that bounds one network call even when DNS resolution hangs,
  which a CCXT timeout alone cannot do;
* the transient/structural classification the runner's retry policy uses.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time

import ccxt
import pytest
from sqlalchemy.exc import OperationalError

from trading_assistant.config import (
    DEFAULT_EXCHANGE_TIMEOUT_MS,
    MAX_EXCHANGE_TIMEOUT_MS,
    MIN_EXCHANGE_TIMEOUT_MS,
    Settings,
)
from trading_assistant.database import create_database_engine
from trading_assistant.market_data.errors import (
    ExchangeDataError,
    ExchangeNetworkTimeout,
    PaginationError,
    RawDataWriteError,
    is_transient_network_error,
)
from trading_assistant.market_data.exchange import (
    NETWORK_CALL_DEADLINE_MULTIPLIER,
    CCXTMarketDataSource,
    resolve_exchange_timeout_ms,
)
from trading_assistant.market_data.service import create_market_data_service


# ----------------------------------------------------------------------
# Configuration: default, override, validation, bounds
# ----------------------------------------------------------------------


def test_default_exchange_timeout_is_finite_and_documented() -> None:
    settings = Settings(_env_file=None)
    assert settings.exchange_timeout_ms == DEFAULT_EXCHANGE_TIMEOUT_MS == 10_000
    # 10s matches CCXT's own default per-operation timeout: healthy Binance
    # public endpoints answer in well under two seconds, so this leaves an
    # order of magnitude of headroom (no false failures under normal latency)
    # while making a hung connect/read fail fast.


def test_exchange_timeout_can_be_overridden_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE_TIMEOUT_MS", "25000")
    settings = Settings(_env_file=None)
    assert settings.exchange_timeout_ms == 25_000


@pytest.mark.parametrize("value", ["0", "-1", "999", "60001", "not-a-number"])
def test_exchange_timeout_rejects_invalid_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE_TIMEOUT_MS", value)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.parametrize("value", [MIN_EXCHANGE_TIMEOUT_MS, MAX_EXCHANGE_TIMEOUT_MS])
def test_exchange_timeout_accepts_bounded_values(monkeypatch, value: int) -> None:
    monkeypatch.setenv("TRADING_ASSISTANT_EXCHANGE_TIMEOUT_MS", str(value))
    settings = Settings(_env_file=None)
    assert settings.exchange_timeout_ms == value


def test_resolve_exchange_timeout_ms_validates_and_defaults() -> None:
    assert resolve_exchange_timeout_ms(None) == DEFAULT_EXCHANGE_TIMEOUT_MS
    assert resolve_exchange_timeout_ms(15_000) == 15_000
    for invalid in (0, -5, MIN_EXCHANGE_TIMEOUT_MS - 1, MAX_EXCHANGE_TIMEOUT_MS + 1):
        with pytest.raises(ValueError, match="exchange timeout"):
            resolve_exchange_timeout_ms(invalid)


# ----------------------------------------------------------------------
# CCXT wiring: the timeout reaches the exchange object and every request
# ----------------------------------------------------------------------


def test_ccxt_exchange_is_built_with_the_configured_timeout() -> None:
    source = CCXTMarketDataSource("binance")
    try:
        assert source.timeout_ms == DEFAULT_EXCHANGE_TIMEOUT_MS
        # CCXT timeout semantics: milliseconds, applied per socket operation.
        assert source._exchange.timeout == 10_000
        assert source._exchange.enableRateLimit is True
        # Public market data only: no credentials are configured.
        assert not source._exchange.apiKey
        assert not source._exchange.secret
    finally:
        source.close()


def test_ccxt_source_accepts_an_explicit_timeout_and_rejects_bad_ones() -> None:
    source = CCXTMarketDataSource("binance", timeout_ms=15_000)
    try:
        assert source._exchange.timeout == 15_000
    finally:
        source.close()
    for invalid in (0, MIN_EXCHANGE_TIMEOUT_MS - 1, MAX_EXCHANGE_TIMEOUT_MS + 1):
        with pytest.raises(ValueError, match="exchange timeout"):
            CCXTMarketDataSource("binance", timeout_ms=invalid)


def test_market_data_service_factory_wires_the_configured_timeout() -> None:
    settings = Settings(_env_file=None, exchange="binance", exchange_timeout_ms=25_000)
    engine = create_database_engine("sqlite:///:memory:")
    try:
        service = create_market_data_service(engine, settings=settings)
        assert service.source.timeout_ms == 25_000
        assert service.source._exchange.timeout == 25_000
    finally:
        engine.dispose()


class _FakeHttpResponse:
    """Minimal ``requests``-shaped response for the transport-level stub."""

    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self.reason = "OK"
        self.headers: dict = {}
        self.text = json.dumps(payload)
        self.encoding = None

    def raise_for_status(self) -> None:
        return None


def _binance_payloads() -> dict[str, dict | list]:
    """Deterministic public Binance Spot metadata and kline responses."""

    spot_exchange_info = {
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
    start_ms = 1_704_067_200_000  # 2024-01-01T00:00:00Z
    return {
        "/api/v3/exchangeInfo": spot_exchange_info,
        "/fapi/v1/exchangeInfo": {"symbols": []},
        "/dapi/v1/exchangeInfo": {"symbols": []},
        "/api/v3/klines": [
            [start_ms, "10.1", "11.2", "9.8", "10.7", "3.25",
             start_ms + 3_599_999, "34.775", 12, "1.5", "16.05", "0"]
        ],
    }


def test_configured_timeout_reaches_binance_metadata_and_klines(monkeypatch) -> None:
    """The same configured CCXT timeout applies to metadata and OHLCV requests."""

    source = CCXTMarketDataSource("binance", timeout_ms=15_000)
    exchange = source._exchange
    recorded: list[tuple[str, object]] = []
    payloads = _binance_payloads()

    def fake_request(method, url, **kwargs):
        recorded.append((url, kwargs.get("timeout")))
        for route, payload in payloads.items():
            if route in url:
                return _FakeHttpResponse(payload)
        raise AssertionError(f"unexpected request url: {url}")

    monkeypatch.setattr(exchange.session, "request", fake_request)
    try:
        rows = source.fetch_ohlcv(
            "BTC/USDT", timeframe="1h", since_ms=1_704_067_200_000, limit=10
        )
        assert len(rows) == 1
        assert rows[0][0] == 1_704_067_200_000
        urls = [url for url, _timeout in recorded]
        assert any("/api/v3/exchangeInfo" in url for url in urls)
        assert any("/fapi/v1/exchangeInfo" in url for url in urls)
        assert any("/dapi/v1/exchangeInfo" in url for url in urls)
        assert any("/api/v3/klines" in url for url in urls)
        assert len(recorded) == 4
        # 15000 ms -> 15.0 seconds per CCXT socket operation.
        assert all(timeout == 15.0 for _url, timeout in recorded)
    finally:
        source.close()


# ----------------------------------------------------------------------
# Watchdog: one network call can never block the runner indefinitely
# ----------------------------------------------------------------------


class _StubClient:
    """Offline CCXT-client double injected without constructing an exchange."""

    def __init__(self, rows=(), error: Exception | None = None) -> None:
        self.id = "binance"
        self.timeframes = {"1h": "60"}
        self.markets = {"BTC/USDT": {"id": "XBTUSDT"}}
        self.number = float
        self.last_http_response = None
        self.rows = list(rows)
        self.error = error
        self.closed = False

    def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
        if self.error is not None:
            raise self.error
        return self.rows

    def close(self) -> None:
        self.closed = True


def _stub_source(client: _StubClient, *, timeout_ms: int = 1_000) -> CCXTMarketDataSource:
    source = CCXTMarketDataSource.__new__(CCXTMarketDataSource)
    source._exchange = client
    source.exchange_id = "binance"
    source.last_http_response = None
    source._timeout_ms = timeout_ms
    source._exchange_class = ccxt.binance
    return source


def test_watchdog_passes_results_and_exceptions_through_unchanged() -> None:
    rows = [[0, "10.1", "11.2", "9.8", "10.7", "3.25"]]
    source = _stub_source(_StubClient(rows=rows))
    try:
        assert source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=0, limit=10) == rows
    finally:
        source.close()

    source = _stub_source(_StubClient(error=ValueError("programming error")))
    try:
        with pytest.raises(ValueError, match="programming error"):
            source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=0, limit=10)
        # A programming error is not a network failure: never classified as
        # transient, never retried as if it were an outage.
        assert not is_transient_network_error(ValueError("programming error"))
    finally:
        source.close()


def test_watchdog_deadline_is_bounded_and_derived_from_configuration() -> None:
    source = CCXTMarketDataSource("binance")
    try:
        assert source._network_deadline_seconds == 30.0  # 3 x 10s default
    finally:
        source.close()
    source = CCXTMarketDataSource("binance", timeout_ms=1_000)
    try:
        assert source._network_deadline_seconds == float(
            NETWORK_CALL_DEADLINE_MULTIPLIER
        )
    finally:
        source.close()


def test_watchdog_abandons_and_rebuilds_the_exchange_after_a_timeout() -> None:
    """A hung call is abandoned; the next call gets a fresh exchange client."""

    release = threading.Event()

    class _HungClient(_StubClient):
        def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
            release.wait(timeout=30)
            return []

    client = _HungClient()
    source = _stub_source(client, timeout_ms=1_000)  # 3s watchdog deadline
    started = time.monotonic()
    try:
        with pytest.raises(ExchangeNetworkTimeout, match="did not complete"):
            source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=0, limit=10)
        elapsed = time.monotonic() - started
        # Bounded: cut at the watchdog deadline, not left hanging.
        assert elapsed < 10.0
        assert source._poisoned is True
        # The abandoned client is closed best-effort and kept for cleanup.
        assert client in source._abandoned
        # The next call rebuilds a fresh exchange with the same finite timeout.
        source._ensure_exchange()
        assert source._exchange is not client
        assert source._exchange.id == "binance"
        assert source._exchange.timeout == 1_000
        assert source._poisoned is False
    finally:
        release.set()
        source.close()


def test_watchdog_bounds_a_hung_dns_resolution(monkeypatch) -> None:
    """Root-cause regression: CCXT's timeout cannot bound DNS resolution.

    With the exchange constructed exactly as the project constructs it (plus a
    small explicit timeout), a request whose DNS resolution hangs - the classic
    connectivity-loss failure - stays blocked far past the configured CCXT
    timeout. The watchdog cuts the call at its finite deadline and raises a
    transient, retryable error instead, so the forward runner can react.
    """

    source = CCXTMarketDataSource("binance", timeout_ms=1_000)  # 3s watchdog
    hang = threading.Event()

    def hung_getaddrinfo(*args, **kwargs):
        hang.wait(timeout=30)
        # Released only to end the test: no real network is contacted.
        raise socket.gaierror(-2, "simulated outage: resolver stopped responding")

    monkeypatch.setattr(socket, "getaddrinfo", hung_getaddrinfo)
    started = time.monotonic()
    try:
        with pytest.raises(ExchangeNetworkTimeout) as captured:
            source.fetch_ohlcv("BTC/USDT", timeframe="1h", since_ms=0, limit=10)
        elapsed = time.monotonic() - started
    finally:
        hang.set()
        source.close()
    # The call was cut at the watchdog deadline (3 x the 1s configured timeout)
    # instead of blocking the pass indefinitely.
    assert 2.5 <= elapsed < 10.0
    assert "network deadline" in str(captured.value)
    assert is_transient_network_error(captured.value) is True
    assert isinstance(captured.value, ExchangeDataError)


def test_ccxt_timeout_alone_does_not_bound_dns_resolution(monkeypatch) -> None:
    """Proof of the pre-fix blocking path (no watchdog involved).

    A bare CCXT exchange - constructed like the project's
    ``exchange_class({"enableRateLimit": True})`` - with an explicit small
    timeout still blocks in DNS resolution far past that timeout, because
    ``socket.create_connection`` resolves the host name outside the per-address
    connect timeout. This is why the runner could sit inside one network
    operation for an hour while the process stayed alive.
    """

    exchange = ccxt.binance({"enableRateLimit": True, "timeout": 1_000})
    hang = threading.Event()

    def hung_getaddrinfo(*args, **kwargs):
        hang.wait(timeout=30)
        raise socket.gaierror(-2, "simulated outage: resolver stopped responding")

    monkeypatch.setattr(socket, "getaddrinfo", hung_getaddrinfo)
    outcome: dict = {}

    def call() -> None:
        try:
            outcome["result"] = exchange.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=5)
        except Exception as exc:  # noqa: BLE001 - any outcome ends the test
            outcome["error"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    try:
        # 4x the configured 1s CCXT timeout: the call is still blocked.
        worker.join(timeout=4.0)
        assert worker.is_alive(), "the request must still be blocked in DNS"
    finally:
        hang.set()
        worker.join(timeout=10)
        exchange.close()


# ----------------------------------------------------------------------
# Classification: transient external vs local/structural failures
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ccxt.NetworkError("binance GET https://api.binance.com/0/public/OHLC: offline"),
        ccxt.RequestTimeout("binance GET https://api.binance.com: request timed out"),
        ccxt.ExchangeNotAvailable("binance is temporarily unavailable"),
        ccxt.DDoSProtection("binance: rate limit exceeded"),
        ConnectionError("connection reset by peer"),
        ConnectionResetError("connection reset"),
        ConnectionRefusedError("connection refused"),
        TimeoutError("timed out"),
        socket.timeout("timed out"),
        socket.gaierror(-2, "temporary failure in name resolution"),
        ExchangeNetworkTimeout("binance fetch_ohlcv exceeded its network deadline"),
    ],
)
def test_transient_network_errors_are_classified_transient(error: Exception) -> None:
    assert is_transient_network_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid configuration"),
        KeyError("programming error"),
        TypeError("programming error"),
        ExchangeDataError("exchange returned an unusable response shape"),
        PaginationError("pagination stalled"),
        RawDataWriteError("raw response could not be preserved"),
    ],
)
def test_structural_errors_are_not_classified_transient(error: Exception) -> None:
    assert is_transient_network_error(error) is False


def test_wrapped_network_error_is_classified_transient() -> None:
    """The market-data layer wraps exchange errors; the chain is walked."""

    underlying = ccxt.RequestTimeout("binance: request timed out")
    try:
        raise ExchangeDataError(
            "OHLCV fetch failed for binance BTC/USDT 1h (underlying RequestTimeout)"
        ) from underlying
    except ExchangeDataError as exc:
        assert is_transient_network_error(exc) is True


def test_wrapped_watchdog_timeout_is_classified_transient() -> None:
    """The watchdog timeout stays transient when the layer wraps it.

    The market-data service raises ``ExchangeDataError`` *from* the
    watchdog's ``ExchangeNetworkTimeout``; the forward pass must still see a
    transient network failure, not a structural one.
    """

    underlying = ExchangeNetworkTimeout(
        "binance fetch_ohlcv did not complete within its 3000ms network deadline"
    )
    try:
        raise ExchangeDataError(
            "OHLCV fetch failed for binance BTC/USDT 1h "
            "(underlying ExchangeNetworkTimeout: ...)"
        ) from underlying
    except ExchangeDataError as exc:
        assert is_transient_network_error(exc) is True


def test_sqlite_lock_error_is_not_transient() -> None:
    """A SQLite lock failure is a local/structural failure, never an outage."""

    from trading_assistant.database import is_sqlite_lock_error

    error = OperationalError(
        "INSERT INTO ohlcv_candles ...",
        {},
        sqlite3.OperationalError("database is locked"),
    )
    assert is_sqlite_lock_error(error) is True
    assert is_transient_network_error(error) is False
