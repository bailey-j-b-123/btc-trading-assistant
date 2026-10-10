"""HTTP contract for chart evidence and its agreement with the dashboard payload."""

from datetime import timedelta

import pytest
from test_chart_evidence import DOUBLE_TOP_CLOSES, series
from web_fixtures import EPOCH, INTERVAL, SYMBOL, insert_candles, make_client, make_settings, migrated_engine


@pytest.fixture
def client(tmp_path):
    engine, url = migrated_engine(tmp_path, name="annotations.sqlite3")
    insert_candles(engine, series(DOUBLE_TOP_CLOSES))
    settings = make_settings(url)
    clock = EPOCH + INTERVAL * len(DOUBLE_TOP_CLOSES)
    test_client = make_client(engine, settings, clock=clock)
    yield test_client
    engine.dispose()


def test_annotations_endpoint_returns_typed_evidence(client):
    response = client.get("/api/market/annotations", params={"symbol": SYMBOL, "timeframe": "1h"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    for key in ("swings", "patterns", "breakouts", "failed_breakouts", "sweeps", "retests", "zones", "candle_shapes"):
        assert isinstance(payload[key], list), key
    assert payload["evidence_window"]["candle_count"] == len(DOUBLE_TOP_CLOSES)
    assert payload["counts"]["patterns"] == len(payload["patterns"])
    assert all(item["descriptive_only"] is True for item in payload["candle_shapes"])


def test_annotations_never_return_items_known_after_as_of(client):
    as_of = (EPOCH + INTERVAL * 22).isoformat().replace("+00:00", "Z")
    response = client.get("/api/market/annotations", params={"symbol": SYMBOL, "timeframe": "1h", "as_of": as_of})
    assert response.status_code == 200
    payload = response.json()
    limit = EPOCH + INTERVAL * 22
    from datetime import datetime

    for group in ("swings", "patterns", "candle_shapes"):
        for item in payload[group]:
            assert datetime.fromisoformat(item["known_at"].replace("Z", "+00:00")) <= limit


def test_unsupported_timeframe_is_a_structured_error(client):
    response = client.get("/api/market/annotations", params={"symbol": SYMBOL, "timeframe": "7m"})
    assert response.status_code in (400, 422)
    body = response.json()
    assert body  # structured JSON, never an HTML error page


def test_future_as_of_is_rejected(client):
    future = (EPOCH + timedelta(days=400)).isoformat().replace("+00:00", "Z")
    response = client.get("/api/market/annotations", params={"symbol": SYMBOL, "timeframe": "1h", "as_of": future})
    assert response.status_code in (400, 422)


def test_dashboard_payload_chart_evidence_matches_the_annotations_endpoint(client):
    dashboard = client.get("/api/dashboard", params={"symbol": SYMBOL, "timeframe": "1h"})
    assert dashboard.status_code == 200
    evidence = dashboard.json()["chart_evidence"]
    annotations = client.get("/api/market/annotations", params={"symbol": SYMBOL, "timeframe": "1h", "as_of": evidence["as_of"]}).json()
    assert evidence["as_of"] == annotations["as_of"]
    assert [s["id"] for s in evidence["swings"]] == [s["id"] for s in annotations["swings"]]
    assert [s["label"] for s in evidence["swings"]] == [s["label"] for s in annotations["swings"]]
    assert [p["id"] for p in evidence["patterns"]] == [p["id"] for p in annotations["patterns"]]
    assert [c["id"] for c in evidence["candle_shapes"]] == [c["id"] for c in annotations["candle_shapes"]]


def test_dashboard_still_reports_engine_fields_unchanged_by_chart_evidence(client):
    payload = client.get("/api/dashboard", params={"symbol": SYMBOL, "timeframe": "1h"}).json()
    # The engine verdict fields exist as before; chart evidence is an additional, read-only projection.
    for key in ("qualification", "planning", "plan", "journal", "multi_timeframe", "overlays", "market"):
        assert key in payload
    assert "chart_evidence" in payload
