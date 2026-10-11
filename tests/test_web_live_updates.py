"""Cheap boundary check behind the automatic dashboard update (GET /api/market/latest-closed).

All candles here are SYNTHETIC test fixtures (deterministic prices, labelled as such). They check
the change-detection contract only: the boundary the browser compares against, that the check runs
no analysis and writes nothing, and that the live Binance quote never reaches the stored-candle
payload. They say nothing about real exchange data.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from test_web_chart_pagination import EXCHANGE, HOUR, START, SYMBOL, synthetic_hours
from web_fixtures import insert_candles, make_settings, migrated_engine

from trading_assistant.web import create_app
from trading_assistant.web import live_price as live_price_module
from trading_assistant.web import dashboard_service as dashboard_service_module


class MutableClock:
    """Fixed-by-test clock. Moving it changes the current boundary without touching any stored row."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def build(tmp_path, count: int):
    """SYNTHETIC: `count` hourly candles from START, clock one interval after the newest candle."""

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, synthetic_hours(count))
    clock = MutableClock(START + count * HOUR)
    app = create_app(engine=engine, settings=make_settings(url), clock=clock)
    return engine, TestClient(app), clock


def latest(client, **params):
    response = client.get(
        "/api/market/latest-closed",
        params={"symbol": SYMBOL, "timeframe": "1h", **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def dashboard(client):
    response = client.get("/api/dashboard", params={"symbol": SYMBOL, "timeframe": "1h"})
    assert response.status_code == 200, response.text
    return response.json()


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def without_wall_clock(value):
    """Drop only `generated_at` (the explanation's wall-clock stamp); every other field is compared."""

    if isinstance(value, dict):
        return {k: without_wall_clock(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [without_wall_clock(v) for v in value]
    return value


def row_counts(engine) -> dict[str, int]:
    counts = {}
    with engine.connect() as connection:
        for table in sorted(inspect(engine).get_table_names()):
            counts[table] = connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one()
    return counts


def test_default_as_of_is_the_dashboard_decision_boundary(tmp_path):
    # SYNTHETIC: 30 closed hours, clock at the boundary after the newest candle (START+30h).
    _, client, _ = build(tmp_path, 30)
    payload = latest(client)
    assert payload["exchange"] == EXCHANGE
    assert payload["timeframe"] == "1h"
    assert parse(payload["latest_stored_open"]) == START + 29 * HOUR
    assert payload["default_as_of"] == dashboard(client)["meta"]["as_of"]


def test_default_as_of_advances_only_when_a_closed_candle_is_stored(tmp_path):
    # SYNTHETIC: stored through 29:00; clock is at 31:00, so the newest closed candle is 30:00.
    engine, client, clock = build(tmp_path, 30)
    clock.now = START + 31 * HOUR
    before = latest(client)["default_as_of"]
    assert before == dashboard(client)["meta"]["as_of"]

    # Same stored data, same boundary: unchanged.
    assert latest(client)["default_as_of"] == before

    # A new closed candle (hour 30) is stored: the boundary moves to hour 31.
    insert_candles(engine, synthetic_hours(31)[30:])
    after = latest(client)["default_as_of"]
    assert after != before
    assert after == dashboard(client)["meta"]["as_of"]


def test_check_runs_no_analysis_and_writes_nothing(tmp_path, monkeypatch):
    engine, client, _ = build(tmp_path, 30)
    counts_before = row_counts(engine)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the cheap boundary check must not run analysis")

    # Expensive paths are replaced by failures: the check must not reach them.
    monkeypatch.setattr(dashboard_service_module.DashboardService, "dashboard", forbidden)
    monkeypatch.setattr(dashboard_service_module, "enumerate_qualifications", forbidden)
    monkeypatch.setattr(dashboard_service_module, "plan_trade", forbidden)
    monkeypatch.setattr(dashboard_service_module, "present_zones", forbidden)

    for _ in range(3):
        assert latest(client)["freshness"]["status"] == "CURRENT"

    assert row_counts(engine) == counts_before


def test_unknown_timeframe_is_rejected(tmp_path):
    _, client, _ = build(tmp_path, 30)
    response = client.get(
        "/api/market/latest-closed", params={"symbol": SYMBOL, "timeframe": "7m"}
    )
    assert 400 <= response.status_code < 500, response.text


def test_empty_store_reports_no_candles_rather_than_inventing_one(tmp_path):
    # SYNTHETIC: migrated database with no candles at all.
    engine, url = migrated_engine(tmp_path)
    clock = MutableClock(START + 5 * HOUR)
    client = TestClient(create_app(engine=engine, settings=make_settings(url), clock=clock))
    payload = latest(client)
    assert payload["latest_stored_open"] is None
    assert payload["freshness"]["status"] != "CURRENT"


def test_live_quote_never_changes_the_stored_candle_payload(tmp_path, monkeypatch):
    _, client, _ = build(tmp_path, 30)
    before_latest = latest(client)
    before_dashboard = dashboard(client)

    # A live quote, even an extreme one, must not reach the stored-candle payloads.
    # The module keeps a 15 s cache; reset it through monkeypatch so this test sees its own fetch.
    monkeypatch.setattr(live_price_module, "_cached", None)
    monkeypatch.setattr(live_price_module, "_last_attempt", 0.0)
    monkeypatch.setattr(live_price_module, "_last_failed", False)
    monkeypatch.setattr(live_price_module, "_fetch_binance_price", lambda: "99999.99")
    quote = client.get("/api/market/live-price")
    assert quote.status_code == 200, quote.text
    assert quote.json()["price"] == "99999.99"

    assert latest(client) == before_latest
    assert without_wall_clock(dashboard(client)) == without_wall_clock(before_dashboard)
    assert "99999.99" not in str(before_latest)
