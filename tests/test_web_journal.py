"""Step 10 journal API: decisions through Step 7, duplicate protection,
immutable historical snapshots, filtering, and observation endpoints.
"""

import pytest
from web_fixtures import (
    EPOCH,
    INTERVAL,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
    watch_candles,
)

from trading_assistant.journaling import DecisionState, JournalService


@pytest.fixture
def client(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    yield test_client
    engine.dispose()


def _dashboard(client):
    return client.get("/api/dashboard").json()


def _decide(client, decision, **extra):
    dashboard = _dashboard(client)
    body = {
        "decision": decision,
        "as_of": dashboard["meta"]["as_of"],
        "setup_id": dashboard["qualification"]["selected_setup_id"],
        **extra,
    }
    return client.post("/api/dashboard/decisions", json=body)


# ---------------------------------------------------------------------------
# Recording decisions through Step 7
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED", "SKIPPED"])
def test_decisions_record_through_step7(client, decision):
    response = _decide(client, decision, reason="deliberate test choice")
    assert response.status_code == 200
    payload = response.json()
    assert payload["duplicate"] is False
    assert payload["decision"]["decision"] == decision
    assert payload["decision"]["reason"] == "deliberate test choice"

    # The exact Step 7 journal service sees the same row: the web layer only
    # writes through the journal, never around it.
    service = JournalService(client.app.state.services.engine)
    latest = service.latest_decision(journal_id=payload["journal_id"])
    assert latest is not None
    assert latest.decision is DecisionState(decision)
    history = service.decision_history(journal_id=payload["journal_id"])
    assert len(history) == 1


def test_decision_requires_explicit_choice_no_default(client):
    dashboard = _dashboard(client)
    body = {
        "as_of": dashboard["meta"]["as_of"],
        "setup_id": dashboard["qualification"]["selected_setup_id"],
    }
    response = client.post("/api/dashboard/decisions", json=body)
    assert response.status_code == 422  # missing decision -> never assumed ACCEPTED


def test_invalid_decision_value_rejected(client):
    dashboard = _dashboard(client)
    body = {
        "decision": "BUY",
        "as_of": dashboard["meta"]["as_of"],
        "setup_id": dashboard["qualification"]["selected_setup_id"],
    }
    response = client.post("/api/dashboard/decisions", json=body)
    assert response.status_code == 422


def test_duplicate_submission_is_suppressed(client):
    first = _decide(client, "ACCEPTED", reason="same note")
    second = _decide(client, "ACCEPTED", reason="same note")
    assert first.status_code == second.status_code == 200
    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True
    assert (
        second.json()["decision"]["decision_id"]
        == first.json()["decision"]["decision_id"]
    )

    service = JournalService(client.app.state.services.engine)
    history = service.decision_history(journal_id=first.json()["journal_id"])
    assert len(history) == 1  # nothing was appended


def test_correction_appends_and_supersedes(client):
    first = _decide(client, "ACCEPTED", reason="initial view")
    correction = _decide(client, "REJECTED", reason="changed my mind")
    assert correction.json()["duplicate"] is False
    decision = correction.json()["decision"]
    assert decision["decision"] == "REJECTED"
    assert decision["supersedes_decision_id"] == first.json()["decision"]["decision_id"]

    service = JournalService(client.app.state.services.engine)
    history = service.decision_history(journal_id=first.json()["journal_id"])
    assert [row.decision for row in history] == [
        DecisionState.ACCEPTED,
        DecisionState.REJECTED,
    ]


def test_decision_applies_to_the_displayed_as_of_snapshot(client):
    """Decisions are pinned to the submitted as_of — deterministic replay."""

    dashboard = _dashboard(client)
    response = _decide(client, "SKIPPED")
    assert response.status_code == 200
    assert response.json()["as_of"] == dashboard["meta"]["as_of"]


def test_cannot_decide_without_qualified_setup(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, watch_candles())
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=EPOCH + 7 * INTERVAL)
    try:
        dashboard = test_client.get("/api/dashboard").json()
        assert dashboard["journal"]["can_decide"] is False
        body = {
            "decision": "ACCEPTED",
            "as_of": dashboard["meta"]["as_of"],
            "setup_id": dashboard["qualification"]["setups"][0]["id"],
        }
        response = test_client.post("/api/dashboard/decisions", json=body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "proposal_not_decidable"
    finally:
        engine.dispose()


def test_unknown_setup_id_rejected(client):
    dashboard = _dashboard(client)
    body = {
        "decision": "ACCEPTED",
        "as_of": dashboard["meta"]["as_of"],
        "setup_id": "nonexistent-setup-id",
    }
    response = client.post("/api/dashboard/decisions", json=body)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "setup_not_found"


# ---------------------------------------------------------------------------
# Listing, filtering, and immutable detail
# ---------------------------------------------------------------------------


def _seed_records(client):
    accepted = _decide(client, "ACCEPTED", reason="taken")
    dashboard = _dashboard(client)
    return accepted.json(), dashboard


def test_journal_listing_and_detail_immutable(client):
    decided, dashboard = _seed_records(client)
    journal_id = decided["journal_id"]

    listing = client.get("/api/journal/records").json()
    assert listing["returned_count"] == 1
    item = listing["items"][0]
    assert item["journal_id"] == journal_id
    assert item["setup_state"] == "QUALIFIED"
    assert item["plan_state"] == "PLANNABLE"
    assert item["latest_decision"]["decision"] == "ACCEPTED"
    assert item["plan_levels"]["entry"] == "124"

    detail = client.get(f"/api/journal/records/{journal_id}").json()
    assert detail["record"]["journal_id"] == journal_id
    assert detail["plan"]["entry"]["value"] == "124"
    # The stored snapshot bytes are the exact Step 5 projection.
    assert detail["setup_snapshot"]["state"] == "QUALIFIED"
    assert detail["setup_snapshot"]["as_of"] == dashboard["meta"]["as_of"]


def test_historical_snapshot_stays_historical(tmp_path):
    """Later candles must not rewrite a stored historical record."""

    from web_fixtures import bar

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        decided, dashboard = _seed_records(client)
        journal_id = decided["journal_id"]
        before = client.get(f"/api/journal/records/{journal_id}").json()

        # Market moves on: append later candles and advance the clock.
        extra = tuple(
            bar(21 + index, price) for index, price in enumerate((126, 127, 125))
        )
        insert_candles(engine, extra)
        later = make_client(engine, settings, clock=qualified_clock() + 3 * INTERVAL)

        after = later.get(f"/api/journal/records/{journal_id}").json()
        assert after["record"] == before["record"]
        assert after["setup_snapshot"] == before["setup_snapshot"]
        assert after["plan"] == before["plan"]
        # Current market moved, but the record's as_of did not.
        current = later.get("/api/dashboard").json()
        assert current["meta"]["as_of"] != dashboard["meta"]["as_of"]
        assert after["record"]["setup_as_of"] == dashboard["meta"]["as_of"]
    finally:
        engine.dispose()


def test_journal_filters(client):
    _seed_records(client)
    assert (
        client.get("/api/journal/records", params={"symbol": "BTC/USDT"}).json()[
            "returned_count"
        ]
        == 1
    )
    assert (
        client.get("/api/journal/records", params={"symbol": "ETH/USD"}).json()[
            "returned_count"
        ]
        == 0
    )
    assert (
        client.get("/api/journal/records", params={"setup_state": "QUALIFIED"}).json()[
            "returned_count"
        ]
        == 1
    )
    assert (
        client.get("/api/journal/records", params={"setup_state": "WATCH"}).json()[
            "returned_count"
        ]
        == 0
    )
    assert (
        client.get("/api/journal/records", params={"plan_state": "PLANNABLE"}).json()[
            "returned_count"
        ]
        == 1
    )
    assert (
        client.get(
            "/api/journal/records", params={"decision_state": "ACCEPTED"}
        ).json()["returned_count"]
        == 1
    )
    assert (
        client.get(
            "/api/journal/records", params={"decision_state": "REJECTED"}
        ).json()["returned_count"]
        == 0
    )
    assert (
        client.get("/api/journal/records", params={"direction": "bullish"}).json()[
            "returned_count"
        ]
        == 1
    )
    assert (
        client.get(
            "/api/journal/records",
            params={"setup_family": "breakout_retest_continuation"},
        ).json()["returned_count"]
        == 1
    )
    # Date range filtering over setup_as_of.
    assert (
        client.get(
            "/api/journal/records", params={"range_from": "2024-01-01T00:00:00Z"}
        ).json()["returned_count"]
        == 1
    )
    assert (
        client.get(
            "/api/journal/records", params={"range_from": "2024-06-01T00:00:00Z"}
        ).json()["returned_count"]
        == 0
    )


def test_journal_detail_not_found(client):
    response = client.get("/api/journal/records/does-not-exist")
    assert response.status_code == 404


def test_record_decision_endpoint_with_duplicate_protection(client):
    decided, _ = _seed_records(client)
    journal_id = decided["journal_id"]
    again = client.post(
        f"/api/journal/records/{journal_id}/decisions",
        json={"decision": "ACCEPTED", "reason": "taken"},
    )
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    changed = client.post(
        f"/api/journal/records/{journal_id}/decisions",
        json={"decision": "SKIPPED", "reason": "window lapsed"},
    )
    assert changed.json()["duplicate"] is False
    assert changed.json()["decision"]["decision"] == "SKIPPED"


def test_observation_endpoint_on_plannable_record(client):
    decided, _ = _seed_records(client)
    journal_id = decided["journal_id"]
    response = client.post(f"/api/journal/records/{journal_id}/observations", json={})
    assert response.status_code == 200
    observation = response.json()
    # No candles exist after the plan instant, so the honest answer is
    # UNKNOWN-family status, never an invented win/loss.
    assert observation["status"] in {
        "INCOMPLETE_DATA",
        "OPEN_AT_CUTOFF",
        "ENTRY_NOT_REACHED",
        "INVALIDATED_BEFORE_ENTRY",
        "STOPPED",
        "AMBIGUOUS",
    }
    assert observation["journal_id"] == journal_id


def test_observation_requires_plannable_plan(client, tmp_path):
    engine, url = migrated_engine(tmp_path)
    settings = make_settings(url)
    empty_client = make_client(engine, settings, clock=qualified_clock())
    try:
        # Journal a snapshot-kind record through Step 7 (no plan attached).
        from web_fixtures import SYMBOL

        from trading_assistant.setup_qualification import QualificationService

        service = QualificationService(engine)
        snapshot = service.snapshot(
            exchange="binance", symbol=SYMBOL, timeframe="1h", as_of=EPOCH + INTERVAL
        )
        record = JournalService(engine).journal_snapshot(snapshot=snapshot)
        response = empty_client.post(
            f"/api/journal/records/{record.journal_id}/observations", json={}
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_observable"
    finally:
        engine.dispose()


def test_untrusted_note_is_stored_verbatim_and_returned_escaped_free(client):
    hostile = '<script>alert("x")</script> & "quotes" — Bailey'
    response = _decide(client, "REJECTED", reason=hostile)
    assert response.status_code == 200
    journal_id = response.json()["journal_id"]
    detail = client.get(f"/api/journal/records/{journal_id}").json()
    assert detail["decisions"][0]["reason"] == hostile  # exact bytes, no mangling
    # API responses are JSON: the text is data, never markup the browser parses.
    assert (
        "text/html"
        not in client.get(f"/api/journal/records/{journal_id}").headers["content-type"]
    )
