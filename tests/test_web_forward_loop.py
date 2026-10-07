"""Forward-first loop: one recorded close, agreed by runner, dashboard, forward.

The frontend's planned-count path depends on ``forward.status.current_state``
describing the same recorded boundary as ``dashboard.meta``. This test runs
the real single-pass runner on a scratch database and then reads both web
endpoints (plus the structure endpoint) from that same database.
"""

from forward_fixtures import (
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    labelled_series,
    make_harness,
)
from web_fixtures import make_client

from trading_assistant.forward_testing import run_single_pass


def _client_after_one_recorded_close():
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = run_single_pass(
        harness.service,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        refresh_market_data=False,
    )
    assert result.processed_boundaries, "the harness close must be recorded"
    client = make_client(harness.engine, harness.settings, clock=harness.clock["t"])
    return harness, result, client


def test_recorded_close_agrees_across_runner_dashboard_and_forward():
    harness, result, client = _client_after_one_recorded_close()
    try:
        dashboard = client.get("/api/dashboard").json()
        forward = client.get("/api/forward", params={"limit": 8}).json()

        current = forward["status"]["current_state"]
        assert current["available"] is True
        # Same recorded boundary on both endpoints.
        assert current["as_of"] == dashboard["meta"]["as_of"]
        # Same deterministic verdict on both endpoints.
        assert current["setup_state"] == dashboard["qualification"]["state"]
        assert dashboard["qualification"]["state"] == "QUALIFIED"
        # Runner result, ledger counts, and API counts agree. Two candidates
        # are plannable at this close, but at most one may be paper-traded: the
        # ledger holds one paper plan and the second candidate is a recorded
        # refusal, so the plannable count and the plan count legitimately differ.
        planned = current["plan_state_counts"].get("PLANNABLE", 0)
        assert planned == 2
        assert result.paper_plans_created == 1
        assert forward["status"]["sample"]["paper_plans"] == 1
        assert (
            forward["status"]["sample"]["pending_catch_up_boundaries"] == 0
        )
        # The heartbeat the runner wrote is the one the API reports.
        runner = forward["status"]["runner"]
        assert runner["status"] in ("STARTED", "PROCESSED", "IDLE")
        assert runner["last_error"] is None
        # The dashboard's scenario section answers from the same snapshot.
        assert dashboard["scenario"]["invalidate"], "qualified setups must explain invalidation"
    finally:
        harness.engine.dispose()


def test_market_state_trend_matches_the_structure_endpoint():
    harness, _result, client = _client_after_one_recorded_close()
    try:
        dashboard = client.get("/api/dashboard").json()
        structure = client.get("/api/market/structure").json()
        market_trend = dashboard["market_state"]["trend"]
        structure_trend = structure["trend"]
        assert market_trend["direction"] == structure_trend["direction"] == "bullish"
        assert market_trend["reason"] == structure_trend["reason"]
        assert market_trend["sufficient"] is True
    finally:
        harness.engine.dispose()
