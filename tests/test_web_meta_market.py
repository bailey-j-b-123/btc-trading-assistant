"""Step 10 meta/market endpoints: identity, stored candles, structure overlays.

All tests are offline: migrated temporary SQLite databases, synthetic labelled
candles, fixed clocks. No network, no exchange.
"""

import pytest
from web_fixtures import (
    SYMBOL,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)


@pytest.fixture
def client(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    yield test_client
    engine.dispose()


def test_meta_reports_identity_and_execution_disabled(client):
    response = client.get("/api/meta")
    assert response.status_code == 200
    payload = response.json()
    assert payload["application"]["execution_disabled"] is True
    assert (
        payload["application"]["authentication_required_before_public_deployment"]
        is True
    )
    assert payload["exchange"] == "binance"
    assert payload["default_symbol"] == SYMBOL
    assert payload["default_timeframe"] == "1h"
    assert "5m" in payload["supported_timeframes"]
    assert payload["server_time_utc"].startswith("2024-01-01T21:00")
    assert "setup_qualification" in payload["rules_versions"]


def test_meta_contains_no_credentials_or_secrets(client):
    payload = client.get("/api/meta").json()
    forbidden = ("api_key", "secret", "password", "token", "credential", "private_key")
    text = repr(payload).lower()
    assert not any(word in text for word in forbidden)


def test_candles_endpoint_returns_stored_rows_exactly(client):
    response = client.get("/api/market/candles", params={"limit": 500})
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == SYMBOL
    assert payload["timeframe"] == "1h"
    assert payload["returned_count"] == 21
    first = payload["candles"][0]
    assert first[1] == "100" and first[4] == "100"  # open/close exact decimal strings
    last = payload["candles"][-1]
    assert last[4] == "124"
    assert payload["complete"] is True


def test_candles_limit_is_clamped_not_reinterpreted(client):
    response = client.get("/api/market/candles", params={"limit": 5})
    payload = response.json()
    assert payload["returned_count"] == 5
    assert payload["candles"][-1][4] == "124"  # most recent window


def test_candles_unsupported_timeframe_rejected(client):
    response = client.get("/api/market/candles", params={"timeframe": "2h"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_structure_endpoint_returns_step3_overlays(client):
    response = client.get("/api/market/structure")
    assert response.status_code == 200
    payload = response.json()
    assert payload["timeframe"] == "1h"
    assert payload["candle_count"] == 21
    assert isinstance(payload["zones"], list)
    assert isinstance(payload["swings"], list)
    assert payload["trend"]["direction"] in {"bullish", "bearish", "neutral"}


def test_arbitrary_symbol_with_no_data_is_empty_not_fake(tmp_path):
    engine, url = migrated_engine(tmp_path)
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    try:
        response = test_client.get("/api/market/candles", params={"symbol": "ETH/USD"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbol"] == "ETH/USD"
        assert payload["candles"] == []
        assert payload["returned_count"] == 0
    finally:
        engine.dispose()


def test_arbitrary_symbol_works_end_to_end(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles(symbol="SOL/USDC"))
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    try:
        response = test_client.get("/api/dashboard", params={"symbol": "SOL/USDC"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["meta"]["symbol"] == "SOL/USDC"
        assert payload["qualification"]["state"] == "QUALIFIED"
        assert payload["market"]["latest_closed_candle"]["close"] == "124"
    finally:
        engine.dispose()


def test_no_network_dependency_in_web_package():
    """The web layer never imports exchange/ccxt market-data download paths."""

    import trading_assistant.web.app as web_app
    import trading_assistant.web.state as state_module
    from trading_assistant.web import dashboard_service

    for module in (web_app, dashboard_service, state_module):
        source = module.__name__
        imported = set(getattr(module, "__dict__", {}).keys())
        assert "CCXTMarketDataSource" not in imported, source
        assert "MarketDataService" not in imported, source


def test_blank_symbol_rejected(client):
    response = client.get("/api/dashboard", params={"symbol": "   "})
    assert response.status_code in (400, 422)
