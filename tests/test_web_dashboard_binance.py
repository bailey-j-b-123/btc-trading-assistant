"""Binance-only dashboard integration and exchange-isolated history regressions.

Synthetic labelled candles are stored under explicit exchange identities and
served through the real API/app services. No network or live exchange is used.
"""

from __future__ import annotations

from dataclasses import replace

from web_fixtures import (
    SYMBOL,
    insert_candles,
    make_client,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

from trading_assistant.config import Settings
from trading_assistant.market_data.repository import CandleRepository


def binance_settings(database_url: str) -> Settings:
    return Settings(_env_file=None, symbol=SYMBOL, database_url=database_url)


def binance_qualifying_candles():
    return tuple(replace(candle, exchange="binance") for candle in qualifying_candles())


def test_binance_dashboard_and_brain_read_the_binance_series_end_to_end(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, binance_qualifying_candles())
    client = make_client(engine, binance_settings(url), clock=qualified_clock())
    try:
        meta = client.get("/api/meta").json()
        assert meta["exchange"] == "binance"

        payload = client.get("/api/dashboard").json()
        assert payload["meta"]["exchange"] == "binance"
        assert payload["market"]["candles"], "stored Binance candles feed the dashboard"
        assert payload["freshness"]["status"] == "CURRENT"
        assert payload["qualification"]["state"] == "QUALIFIED"
        assert payload["planning"]["state"] == "PLANNABLE"
        # This is the unchanged synthetic Step 6 plan contract, not a new strategy.
        assert payload["plan"]["entry"]["value"] == "124"
        assert payload["plan"]["stop"]["value"] == "117"
        assert [target["level"]["value"] for target in payload["plan"]["targets"]] == ["138"]

        candles = client.get("/api/market/candles", params={"timeframe": "1h"}).json()
        assert candles["exchange"] == "binance"
        assert candles["returned_count"] == len(payload["market"]["candles"])
        assert candles["complete"] is True

        structure = client.get("/api/market/structure", params={"timeframe": "1h"}).json()
        assert structure["exchange"] == "binance"
        assert structure["candle_count"] > 0

        forward = client.get("/api/forward").json()
        assert forward["exchange"] == "binance"
        assert forward["status"]["market_data"]["latest_stored_candle_open"]
    finally:
        engine.dispose()


def test_legacy_kraken_history_remains_unchanged_and_never_leaks_into_binance_views(tmp_path):
    engine, url = migrated_engine(tmp_path)
    binance_rows = binance_qualifying_candles()
    from decimal import Decimal

    kraken_rows = tuple(
        replace(
            candle,
            exchange="kraken",
            open=Decimal("1"),
            high=Decimal("2"),
            low=Decimal("0.5"),
            close=Decimal("1.5"),
            volume=Decimal("10"),
        )
        for candle in qualifying_candles()
    )
    repository = CandleRepository(engine)
    repository.insert_unchanged_or_new(binance_rows)
    repository.insert_unchanged_or_new(kraken_rows)
    original_kraken = repository.get_candles(
        exchange="kraken", symbol=SYMBOL, timeframe="1h"
    )
    client = make_client(engine, binance_settings(url), clock=qualified_clock())

    try:
        response = client.get("/api/market/candles", params={"timeframe": "1h"}).json()
        assert response["exchange"] == "binance"
        assert response["candles"]
        assert response["returned_count"] == len(binance_rows)
        assert all(row[0] == binance_rows[index].timestamp.timestamp() * 1000
                   for index, row in enumerate(response["candles"]))

        dashboard = client.get("/api/dashboard").json()
        assert dashboard["meta"]["exchange"] == "binance"
        assert dashboard["market"]["latest_closed_candle"]["close"] == "124"
        assert all(row[4] != "1.5" for row in dashboard["market"]["candles"])
        assert dashboard["qualification"]["state"] == "QUALIFIED"

        # New reads did not rewrite, delete or re-key the preserved old rows.
        after_kraken = repository.get_candles(
            exchange="kraken", symbol=SYMBOL, timeframe="1h"
        )
        assert after_kraken.candles == original_kraken.candles
        assert after_kraken.gaps == original_kraken.gaps
        assert len(after_kraken.candles) == len(kraken_rows)
        assert {candle.exchange for candle in after_kraken.candles} == {"kraken"}
    finally:
        engine.dispose()
