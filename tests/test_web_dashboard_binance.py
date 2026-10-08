"""Binance Spot integration with the dashboard, market-structure and BRAIN read paths.

The same synthetic qualifying fixture is stored under the ``binance`` exchange
identity and served through the real web stack with
``exchange="binance"``: the dashboard, chart-data, structure and forward
endpoints must all read the Binance series and produce the exact same
deterministic verdict the Kraken identity produces — no trading logic is
redesigned or special-cased per venue. Kraken-keyed rows coexisting in the
same database must never leak into Binance responses (or vice versa).
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


def venue_settings(database_url: str, exchange: str) -> Settings:
    """Settings for a specific exchange identity (make_settings pins mock-exchange)."""

    return Settings(symbol=SYMBOL, exchange=exchange, database_url=database_url)


def binance_qualifying_candles():
    """The shared qualifying fixture re-keyed to the Binance exchange identity."""

    return tuple(replace(candle, exchange="binance") for candle in qualifying_candles())


def test_binance_dashboard_reads_the_binance_series_end_to_end(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, binance_qualifying_candles())
    client = make_client(
        engine, venue_settings(url, "binance"), clock=qualified_clock()
    )
    try:
        meta = client.get("/api/meta").json()
        assert meta["exchange"] == "binance"

        payload = client.get("/api/dashboard").json()
        assert payload["meta"]["exchange"] == "binance"
        assert payload["market"]["candles"], "stored Binance candles feed the chart"
        assert payload["freshness"]["status"] == "CURRENT"
        # The deterministic engine verdict is identical to the Kraken fixture:
        # same closed candles, same qualification, same plan levels.
        assert payload["qualification"]["state"] == "QUALIFIED"
        assert payload["planning"]["state"] == "PLANNABLE"
        plan = payload["plan"]
        assert plan["entry"]["value"] == "124"
        assert plan["stop"]["value"] == "117"
        assert [target["level"]["value"] for target in plan["targets"]] == ["138"]
        assert "multi_timeframe" in payload

        candles = client.get("/api/market/candles", params={"timeframe": "1h"}).json()
        assert candles["exchange"] == "binance"
        assert candles["returned_count"] == len(payload["market"]["candles"])
        assert candles["complete"] is True

        structure = client.get("/api/market/structure", params={"timeframe": "1h"}).json()
        assert structure["exchange"] == "binance"
        assert structure["candle_count"] > 0

        forward = client.get("/api/forward").json()
        assert forward["exchange"] == "binance"
        assert forward["status"]["market_data"]["data_health"] == "CURRENT"
        assert forward["status"]["market_data"]["latest_stored_candle_open"]
    finally:
        engine.dispose()


def test_binance_and_kraken_series_stay_separate_in_web_responses(tmp_path):
    engine, url = migrated_engine(tmp_path)
    # The identical synthetic series exists under Binance, Kraken and a third
    # (mock) identity; every web view must still read only its own key.
    insert_candles(engine, binance_qualifying_candles())
    insert_candles(engine, qualifying_candles())  # mock-exchange identity
    kraken_candles = tuple(
        replace(candle, exchange="kraken") for candle in qualifying_candles()
    )
    insert_candles(engine, kraken_candles)
    binance_client = make_client(
        engine, venue_settings(url, "binance"), clock=qualified_clock()
    )
    kraken_client = make_client(
        engine, venue_settings(url, "kraken"), clock=qualified_clock()
    )
    try:
        binance_candles = binance_client.get(
            "/api/market/candles", params={"timeframe": "1h"}
        ).json()
        kraken_candles_payload = kraken_client.get(
            "/api/market/candles", params={"timeframe": "1h"}
        ).json()
        assert binance_candles["exchange"] == "binance"
        assert kraken_candles_payload["exchange"] == "kraken"
        assert binance_candles["returned_count"] == kraken_candles_payload["returned_count"]
        # Same synthetic values, but the identities never mix: each response
        # reports its own exchange and each engine reads only its own series.
        assert binance_candles["candles"] == kraken_candles_payload["candles"]

        binance_dashboard = binance_client.get("/api/dashboard").json()
        kraken_dashboard = kraken_client.get("/api/dashboard").json()
        assert binance_dashboard["meta"]["exchange"] == "binance"
        assert kraken_dashboard["meta"]["exchange"] == "kraken"
        assert binance_dashboard["qualification"]["state"] == "QUALIFIED"
        assert kraken_dashboard["qualification"]["state"] == "QUALIFIED"

        # An exchange with no stored series sees none — no cross-venue fallback.
        empty_client = make_client(
            engine, venue_settings(url, "coinbase"), clock=qualified_clock()
        )
        empty = empty_client.get("/api/market/candles", params={"timeframe": "1h"}).json()
        assert empty["exchange"] == "coinbase"
        assert empty["candles"] == []
        assert empty_client.get("/api/dashboard").json()["market"]["candles"] == []
    finally:
        engine.dispose()
