"""Exchange-aware public quote: Binance path, Kraken parity, fail-closed rules.

The quote path is a display island: no DB, no qualification input, no fabricated
ticks. These tests pin the Binance public spot ticker contract, the unchanged
Kraken behaviour, and the exchange identity carried by the payload. Everything
is offline: ``urlopen``/fetchers are monkeypatched, nothing contacts Binance.
"""

from __future__ import annotations

import pytest
from dataclasses import replace
from web_fixtures import (
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

from trading_assistant.web import live_price as module


@pytest.fixture
def fresh_quote_state(monkeypatch):
    """Reset the module-level quote cache between tests (auto-restored)."""

    monkeypatch.setattr(module, "_cached", None)
    monkeypatch.setattr(module, "_last_attempt", 0.0)
    monkeypatch.setattr(module, "_last_failed", False)


def test_binance_public_quote_only_validates_positive_last_trade(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    response = Response()
    monkeypatch.setattr(module, "urlopen", lambda url, timeout: response)
    assert module.BINANCE_TICKER_URL == "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
    monkeypatch.setattr(
        module.json,
        "load",
        lambda _: {"symbol": "BTCUSDT", "price": "82512.40"},
    )
    assert module._fetch_binance_price() == "82512.40"
    bad_payloads = [
        {"symbol": "ETHUSDT", "price": "82512.40"},  # wrong symbol
        {"price": "82512.40"},  # missing symbol
        {"symbol": "BTCUSDT"},  # missing price
        {"symbol": "BTCUSDT", "price": None},
        {"symbol": "BTCUSDT", "price": 82512.40},  # not a string
        {"symbol": "BTCUSDT", "price": ""},
        {"symbol": "BTCUSDT", "price": "   "},
        ["not", "a", "dict"],
    ]
    for bad in bad_payloads:
        monkeypatch.setattr(module.json, "load", lambda _, bad=bad: bad)
        with pytest.raises(ValueError):
            module._fetch_binance_price()
    for bad_price in ["NaN", "-1", "0", "Infinity", "-Infinity"]:
        monkeypatch.setattr(
            module.json,
            "load",
            lambda _, bad_price=bad_price: {"symbol": "BTCUSDT", "price": bad_price},
        )
        with pytest.raises(ValueError):
            module._fetch_binance_price()


def test_live_price_uses_the_binance_public_ticker_when_configured(monkeypatch, fresh_quote_state):
    monkeypatch.setattr(module, "_fetch_binance_price", lambda: "82512.40")
    payload = module.live_price(exchange="binance")
    assert payload["status"] == "CURRENT"
    assert payload["price"] == "82512.40"
    assert payload["exchange"] == "binance"
    assert payload["source"] == "Binance public ticker"
    assert payload["display_only"] is True

    # A failed refresh keeps the last good quote explicitly stale.
    monkeypatch.setattr(
        module, "_fetch_binance_price", lambda: (_ for _ in ()).throw(TimeoutError())
    )
    monkeypatch.setattr(module, "_last_attempt", 0.0)
    stale = module.live_price(exchange="binance")
    assert stale["status"] == "STALE"
    assert stale["price"] == "82512.40"  # no fabricated tick
    assert stale["exchange"] == "binance"

    # No quote at all is unavailable, never a guess.
    monkeypatch.setattr(module, "_cached", None)
    monkeypatch.setattr(module, "_last_attempt", 0.0)
    unavailable = module.live_price(exchange="binance")
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["price"] is None
    assert unavailable["exchange"] == "binance"


def test_venue_switch_failure_never_returns_the_previous_exchange_quote(
    monkeypatch, fresh_quote_state
):
    monkeypatch.setattr(module, "_fetch_binance_price", lambda: "82512.40")
    binance = module.live_price(exchange="binance")
    assert binance["status"] == "CURRENT"

    monkeypatch.setattr(
        module, "_fetch_price", lambda: (_ for _ in ()).throw(TimeoutError())
    )
    kraken = module.live_price(exchange="kraken")
    assert kraken["status"] == "UNAVAILABLE"
    assert kraken["price"] is None
    assert kraken["exchange"] == "kraken"
    assert kraken["source"] == "kraken public ticker"


def test_live_price_keeps_the_kraken_path_and_identity(monkeypatch, fresh_quote_state):
    monkeypatch.setattr(module, "_fetch_price", lambda: "82512.40")
    payload = module.live_price(exchange="kraken")
    assert payload["status"] == "CURRENT"
    assert payload["price"] == "82512.40"
    assert payload["exchange"] == "kraken"
    assert payload["source"] == "Kraken public ticker"


def test_live_price_fails_closed_for_an_unsupported_exchange(monkeypatch, fresh_quote_state):
    monkeypatch.setattr(module, "_fetch_price", lambda: "82512.40")
    monkeypatch.setattr(module, "_fetch_binance_price", lambda: "82512.40")
    payload = module.live_price(exchange="mock-exchange")
    assert payload["status"] == "UNAVAILABLE"
    assert payload["price"] is None
    assert payload["exchange"] == "mock-exchange"
    assert "mock-exchange" in payload["source"]
    # The unsupported venue never silently quotes another exchange.
    assert payload["source"] != "Kraken public ticker"
    assert payload["source"] != "Binance public ticker"


def test_live_price_endpoint_follows_the_configured_environment(
    tmp_path, monkeypatch, fresh_quote_state
):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url).model_copy(update={"exchange": "binance"})
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        monkeypatch.setattr(module, "_fetch_binance_price", lambda: "82512.40")
        payload = client.get("/api/market/live-price").json()
        assert payload["status"] == "CURRENT"
        assert payload["price"] == "82512.40"
        assert payload["exchange"] == "binance"
        assert payload["source"] == "Binance public ticker"
        assert payload["display_only"] is True

        # A second app configured for Kraken switches venue explicitly; the
        # cache fetches Kraken and cannot relabel the Binance price as Kraken.
        kraken_settings = make_settings(url).model_copy(update={"exchange": "kraken"})
        kraken_client = make_client(engine, kraken_settings, clock=qualified_clock())
        monkeypatch.setattr(module, "_fetch_price", lambda: "70000.00")
        switched = kraken_client.get("/api/market/live-price").json()
        assert switched["status"] == "CURRENT"
        assert switched["exchange"] == "kraken"
        assert switched["price"] == "70000.00"
        assert switched["source"] == "Kraken public ticker"
    finally:
        engine.dispose()


def test_binance_quote_failure_never_changes_closed_engine(tmp_path, monkeypatch, fresh_quote_state):
    """The display island stays isolated: quote failures cannot touch the engine."""

    engine, url = migrated_engine(tmp_path)
    binance_candles = tuple(replace(candle, exchange="binance") for candle in qualifying_candles())
    insert_candles(engine, binance_candles)
    settings = make_settings(url).model_copy(update={"exchange": "binance"})
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        before = client.get("/api/dashboard").json()
        assert before["market"]["candles"]
        monkeypatch.setattr(
            module, "_fetch_binance_price", lambda: (_ for _ in ()).throw(TimeoutError())
        )
        payload = client.get("/api/market/live-price").json()
        assert payload["status"] == "UNAVAILABLE"
        assert payload["price"] is None
        assert payload["exchange"] == "binance"
        after = client.get("/api/dashboard").json()
        for key in (
            "market",
            "qualification",
            "planning",
            "plan",
            "multi_timeframe",
            "overlays",
            "scenario",
            "looking_for",
        ):
            assert before[key] == after[key], key
    finally:
        engine.dispose()
