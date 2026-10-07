"""Step 13 UI support: one honest stored series per chart timeframe.

The dashboard chart switcher (5m / 15m / 1h / 4h) is view-only: it only reads
the existing ``/api/market/candles`` and ``/api/market/structure`` endpoints.
These tests prove each timeframe serves its own stored closed candles honestly
(empty stays empty — never interpolated or borrowed from another timeframe),
that structure overlays echo the requested timeframe, and that these market
reads cannot change any engine state (no POST exists on ``/api/market``, and
the dashboard payload is identical before and after chart reads).

All fixtures are synthetic and labelled; no scenario uses real market data.
"""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from web_fixtures import (
    EPOCH,
    EXCHANGE,
    SYMBOL,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

from trading_assistant.market_data.types import Candle

#: Synthetic 5m series: 30 closed candles, the last closing exactly at the
#: dashboard decision boundary (EPOCH + 21h), labelled TEST FIXTURE, not real
#: market history. The final open is one 5m interval before the clock instant,
#: so every fixture candle is genuinely closed (closed-candle-only).
FIVE_MINUTE_COUNT = 30


def five_minute_candles() -> tuple[Candle, ...]:
    end = EPOCH + timedelta(hours=21) - timedelta(minutes=5)
    candles = []
    for index in range(FIVE_MINUTE_COUNT):
        price = D("62000") + D(str(index * 10))
        candles.append(
            Candle(
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="5m",
                timestamp=end - timedelta(minutes=5 * (FIVE_MINUTE_COUNT - 1 - index)),
                open=price,
                high=price + D("8"),
                low=price - D("8"),
                close=price + D("5"),
                volume=D("3.25"),
            )
        )
    return tuple(candles)


@pytest.fixture
def client(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    insert_candles(engine, five_minute_candles())
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    yield test_client
    engine.dispose()


def test_each_chart_timeframe_serves_only_its_own_stored_rows(client):
    five = client.get("/api/market/candles", params={"timeframe": "5m"}).json()
    assert five["timeframe"] == "5m"
    assert five["returned_count"] == FIVE_MINUTE_COUNT
    assert len(five["candles"]) == FIVE_MINUTE_COUNT
    assert five["candles"][-1][4] == "62295"  # exact stored close, unrounded
    assert five["candles"][0][4] == "62005"

    one_hour = client.get("/api/market/candles", params={"timeframe": "1h"}).json()
    assert one_hour["timeframe"] == "1h"
    assert one_hour["returned_count"] == 21
    assert one_hour["candles"][-1][4] == "124"
    # The two series never share rows: different stored tables, different data.
    assert five["candles"] != one_hour["candles"]


def test_unstored_chart_timeframes_are_empty_not_fabricated(client):
    for timeframe in ("15m", "4h"):
        payload = client.get("/api/market/candles", params={"timeframe": timeframe}).json()
        assert payload["timeframe"] == timeframe
        assert payload["candles"] == []
        assert payload["returned_count"] == 0


def test_candles_can_be_pinned_to_the_dashboard_decision_instant(client):
    as_of = qualified_clock().isoformat().replace("+00:00", "Z")
    payload = client.get(
        "/api/market/candles", params={"timeframe": "5m", "end_time": as_of}
    ).json()
    assert payload["timeframe"] == "5m"
    assert payload["returned_count"] == FIVE_MINUTE_COUNT
    # Pinned reads stay at closed candles: nothing past the decision instant.
    assert payload["candles"][-1][4] == "62295"


def test_structure_echoes_the_requested_chart_timeframe(client):
    as_of = qualified_clock().isoformat().replace("+00:00", "Z")
    payload = client.get(
        "/api/market/structure", params={"timeframe": "5m", "as_of": as_of}
    ).json()
    assert payload["timeframe"] == "5m"
    assert payload["candle_count"] == FIVE_MINUTE_COUNT
    assert isinstance(payload["zones"], list)
    assert isinstance(payload["swings"], list)


def test_structure_without_stored_candles_is_honestly_empty(client):
    payload = client.get("/api/market/structure", params={"timeframe": "4h"}).json()
    assert payload["timeframe"] == "4h"
    assert payload["candle_count"] == 0
    assert payload["zones"] == []
    assert payload["swings"] == []


def test_market_endpoints_expose_no_state_changing_method(client):
    candidates = []
    for route in client.app.routes:
        nested = getattr(route, "original_router", None)
        if nested is not None and hasattr(nested, "routes"):
            candidates.extend(nested.routes)
        else:
            candidates.append(route)
    market_routes = [
        route
        for route in candidates
        if str(getattr(route, "path", "")).startswith("/api/market")
    ]
    assert market_routes, "the chart data endpoints must exist"
    assert {str(route.path) for route in market_routes} == {
        "/api/market/candles",
        "/api/market/structure",
        "/api/market/live-price",
    }
    for route in market_routes:
        assert set(route.methods or set()) <= {"GET", "HEAD", "OPTIONS"}, route.path


def test_chart_reads_cannot_change_engine_state(client):
    def engine_state(payload: dict) -> dict:
        multi = payload["multi_timeframe"]
        return {
            "as_of": payload["meta"]["as_of"],
            "qualification_state": payload["qualification"]["state"],
            "selected_setup": payload["qualification"]["selected_setup_id"],
            "rules_version": payload["qualification"]["rules_version"],
            "planning": payload["planning"],
            "plan": payload["plan"],
            "market_candles": payload["market"]["candles"],
            "hierarchy": (
                {k: multi.get(k) for k in ("decision", "overall", "status", "alignment", "counter_trend")}
                if multi.get("available") is True
                else {"available": False}
            ),
        }

    before = engine_state(client.get("/api/dashboard").json())
    as_of = qualified_clock().isoformat().replace("+00:00", "Z")
    for timeframe in ("5m", "15m", "1h", "4h"):
        assert client.get("/api/market/candles", params={"timeframe": timeframe}).status_code == 200
        assert (
            client.get(
                "/api/market/structure", params={"timeframe": timeframe, "as_of": as_of}
            ).status_code
            == 200
        )
    after = engine_state(client.get("/api/dashboard").json())
    assert after == before
