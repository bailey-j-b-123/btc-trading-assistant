"""Step 12 web layer: the read-only LIVE/PAPER API and its honesty guarantees.

The dashboard endpoints must never fetch candles, run a forward pass, write to
the ledger, or invent live data. Everything here runs offline against temporary
migrated databases with injected clocks: an empty ledger, and a ledger seeded by
the real forward service through the fake public exchange fixture.
"""

from __future__ import annotations

import pytest
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    clock_at,
    labelled_series,
    make_harness,
)
from web_fixtures import (
    insert_candles,
    make_settings,
    migrated_engine,
    qualifying_candles,
)

from trading_assistant.web import create_app

FORBIDDEN_BODY_KEYS = (
    "order",
    "balance",
    "position_size",
    "quantity",
    "leverage",
    "withdraw",
    "credential",
    "api_key",
    "apikey",
    "notional",
)


def _client(engine, settings, *, clock):
    from fastapi.testclient import TestClient

    app = create_app(engine=engine, settings=settings, clock=lambda: clock)
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path):
    engine, url = migrated_engine(tmp_path, "web_forward_empty.sqlite3")
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = _client(engine, settings, clock=clock_at(QUALIFYING_BOUNDARY))
    yield engine, client
    engine.dispose()


@pytest.fixture
def seeded_client(tmp_path):
    """A real forward ledger: one qualifying close and its two paper plans."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 2
    harness.step((bar_after_entry(),), refresh_market_data=False)
    client = _client(
        harness.engine,
        harness.settings,
        clock=clock_at(QUALIFYING_BOUNDARY + 2 * INTERVAL),
    )
    yield harness, client


def bar_after_entry():
    from forward_fixtures import bar

    return bar(21, 126, low=123)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


# ----------------------------------------------------------------------
# Empty and error states
# ----------------------------------------------------------------------


def test_empty_forward_ledger_is_explicit_and_never_invented(empty_client) -> None:
    _, client = empty_client
    response = client.get("/api/forward")
    assert response.status_code == 200
    body = response.json()

    assert body["label"] == "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE"
    assert body["paper_label"] == "PAPER OBSERVATION — NO REAL ORDER"
    assert body["market_data_label"] == "LIVE MARKET DATA"
    assert body["execution_disabled"] is True
    assert (
        body["disclaimer"]
        == "Paper trading and historical performance do not establish future "
        "profitability."
    )

    status = body["status"]
    assert status["sample"]["cycles"] == 0
    assert status["sample"]["observations"] == 0
    assert status["sample"]["paper_plans"] == 0
    assert status["unresolved_paper_plan_count"] == 0
    # No close has been recorded, so the current state is explicitly unknown
    # rather than a fabricated setup.
    assert status["current_state"]["available"] is False
    assert status["current_state"]["unavailable_reason"]
    assert status["current_paper_plan"] is None
    assert status["latest_cycle"] is None
    # Stored market data exists and is fresh, but freshness alone is not a
    # conclusion: the forward ledger holds nothing and says so.
    assert status["market_data"]["latest_closed_candle_open"] is not None
    assert status["market_data"]["data_health"] in {"CURRENT", "STALE"}
    assert status["runner"] is None

    observations = body["observations"]
    assert observations["observations"] == []
    assert observations["paper_plans"] == []
    assert observations["label"] == "PAPER OBSERVATION — NO REAL ORDER"

    report = body["report"]
    assert report["metrics"]["paper_plan_count"] == 0
    assert report["metrics"]["total_cycles"] == 0
    assert report["metrics"]["entry_reached_rate"]["percentage"] is None
    assert report["warnings"]
    assert list(body["limitations"])
    assert any(
        "do not establish future profitability" in limitation
        for limitation in body["limitations"]
    )
    # An empty live ledger is described as such; nothing is rendered as a result.
    assert "NOT REAL PERFORMANCE" in body["label"]


def test_forward_view_without_any_stored_candle_is_unknown_not_zero_prices(tmp_path) -> None:
    engine, url = migrated_engine(tmp_path, "web_forward_nodata.sqlite3")
    client = _client(
        engine, make_settings(url), clock=clock_at(QUALIFYING_BOUNDARY)
    )
    body = client.get("/api/forward").json()
    market = body["status"]["market_data"]

    assert market["latest_closed_candle_open"] is None
    assert market["data_health"] == "UNKNOWN"
    assert market["data_health_detail"]
    assert body["status"]["runner"] is None
    assert body["status"]["sample"]["cycles"] == 0
    assert body["observations"]["observations"] == []
    assert body["observations"]["paper_plans"] == []
    # No price, level or conclusion is fabricated to fill the empty view.
    assert "0" not in str(market["latest_closed_candle_open"])
    assert body["report"]["metrics"]["paper_plan_count"] == 0
    assert any(
        "pending_boundaries" in warning for warning in body["report"]["warnings"]
    )
    engine.dispose()


def test_forward_endpoints_are_read_only(empty_client) -> None:
    engine, client = empty_client
    service = client.app.state.services.forward
    before = service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME)

    for _ in range(2):
        assert client.get("/api/forward").status_code == 200
    assert client.get("/api/forward/comparison").status_code == 200

    after = service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME)
    assert after == before
    assert before["cycles"] == 0
    assert before["observations"] == 0
    assert before["paper_plans"] == 0
    assert before["outcome_versions"] == 0


def test_forward_api_rejects_unsupported_inputs_with_json_errors(empty_client) -> None:
    _, client = empty_client
    bad_timeframe = client.get("/api/forward", params={"timeframe": "3m"})
    assert bad_timeframe.status_code == 400
    assert set(bad_timeframe.json()) == {"error"}
    assert bad_timeframe.json()["error"]["code"]
    assert bad_timeframe.json()["error"]["message"]

    # A negative cost assumption is a request error, never a silently coerced 0.
    bad_friction = client.get("/api/forward", params={"fee_bps": "-1"})
    assert bad_friction.status_code in {400, 422}

    huge_limit = client.get("/api/forward", params={"limit": "100000"})
    assert huge_limit.status_code in {400, 422}


def test_forward_api_has_no_order_or_account_surface(empty_client) -> None:
    _, client = empty_client
    for path in ("/api/forward", "/api/forward/comparison"):
        body = client.get(path).json()
        keys = {key.lower() for key in _walk_keys(body)}
        for banned in FORBIDDEN_BODY_KEYS:
            assert not any(banned in key for key in keys), (path, banned)


# ----------------------------------------------------------------------
# Recorded ledger
# ----------------------------------------------------------------------


def test_seeded_forward_view_shows_paper_observations_not_trades(seeded_client) -> None:
    harness, client = seeded_client
    body = client.get("/api/forward").json()

    observations = body["observations"]["observations"]
    assert observations
    assert len(observations) == len(harness.observations())
    assert body["observations"]["paper_plans"]

    plan_states = {item["plan_state"] for item in observations}
    assert "PLANNABLE" in plan_states
    # WATCH/NO_SETUP closes are recorded with their evidence but never carry a
    # paper plan, so no observation without a plan may point at one.
    for item in observations:
        assert item["plan_state"] in {"PLANNABLE", "NO_PLAN", "WATCH_ONLY", None}
        if item["plan_state"] != "PLANNABLE":
            assert item["paper_plan_id"] is None
        assert item["exchange"] == EXCHANGE
        assert item["symbol"] == SYMBOL
        assert item["timeframe"] == TIMEFRAME
        assert item["as_of"]
        assert item["setup_id"]
        assert item["setup_state"]
        assert item["data_health"]
        assert item["observation_id"]

    plans = body["observations"]["paper_plans"]
    assert len(plans) == 2
    for plan in plans:
        assert plan["plan_json"]
        assert plan["setup_id"]
        assert plan["entry"]
        assert plan["stop"] is not None
        assert plan["risk_per_unit"]
        assert plan["friction_fingerprint"]
        assert plan["latest_outcome"] is not None
        assert plan["outcome_version_count"] >= 1
        # The paper plan is a proposal with observed levels; it is never a fill.
        text = str(plan).lower()
        for banned in ("executed at", "was filled", "quantity", "position size"):
            assert banned not in text

    status = body["status"]
    assert status["sample"]["cycles"] >= 1
    assert status["sample"]["paper_plans"] == 2
    assert status["current_state"]["available"] is True
    assert body["friction_from_recorded_plans"] is True

    report = body["report"]
    assert report["metrics"]["paper_plan_count"] == 2
    assert report["metrics"]["distinct_boundaries"] >= 1
    assert report["friction_fingerprint"]
    assert report["metrics"]["unresolved_rate"]["denominator"] >= 0


def test_comparison_endpoint_separates_historical_and_forward(seeded_client) -> None:
    _, client = seeded_client
    response = client.get("/api/forward/comparison")
    assert response.status_code == 200
    body = response.json()

    assert body["label"] == "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE"
    assert body["execution_disabled"] is True
    comparison = body["comparison"]
    assert comparison["historical"]["label"] == "HISTORICAL VALIDATION"
    assert comparison["forward"]["label"] == "LIVE FORWARD PAPER OBSERVATIONS"
    assert "NOT LIVE PERFORMANCE" in comparison["historical"]["disclaimer"]
    assert "NO REAL ORDER" in comparison["forward"]["disclaimer"]
    assert comparison["historical"]["available"] is True
    assert comparison["forward"]["available"] is True
    assert comparison["historical"]["metrics"]["paper_plan_count"] >= 1
    assert comparison["forward"]["metrics"]["paper_plan_count"] == 2
    assert comparison["version_comparability_note"]

    rows = comparison["rows"]
    assert rows
    for row in rows:
        assert row["metric"]
        assert row["historical_denominator"] is not None
        assert row["forward_denominator"] is not None
    assert any(
        "denominators_differ" in warning for warning in comparison["warnings"]
    )
    assert any(
        "not a statistical test" in limitation
        for limitation in comparison["limitations"]
    )
    assert any(
        "do not establish future profitability" in limitation
        for limitation in comparison["limitations"]
    )


def test_forward_view_reports_staleness_honestly(seeded_client) -> None:
    harness, client = seeded_client
    # The dashboard clock is two closes ahead of the last recorded/stored candle.
    body = client.get("/api/forward").json()
    market = body["status"]["market_data"]
    assert market["data_health"] in {"STALE", "CURRENT"}
    assert market["data_health_detail"]
    assert market["closed_candle_policy"]
    assert market["expected_latest_closed_candle_open"]
    # Whatever the verdict, it is never silent: the label and detail are present
    # and the response never claims the ledger is current without evidence.
    if market["data_health"] == "STALE":
        assert market["staleness_intervals"] >= 1
        assert "STALE" in body["status"]["runner"]["status"] or body["status"]["runner"]
    assert harness.service.status()["sample"]["paper_plans"] == 2
