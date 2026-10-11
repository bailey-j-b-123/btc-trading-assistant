"""Paper-trade tracker: the persistent Active/Recent ledger view.

``ForwardTestService.paper_tracker_payload`` (exposed at ``/api/forward`` →
``paper_tracker``) must show recorded paper plans exactly as the ledger stores
them — levels, entry reached, latest outcome version, outcome timestamp, and
the engine's own settled/unresolved verdicts — and must keep showing a
recorded plan after later closes conclude "No trade". Nothing here may invent
state, overwrite an outcome, or consult a live price.

Everything runs offline against a temporary migrated database, the synthetic
labelled series (entry 124, stop 117, target 138 — the same geometry as the
first genuine forward paper trade), and injected clocks. The rows are recorded
by the real forward service, never fabricated by the test.
"""

from __future__ import annotations

import pytest
from forward_fixtures import (
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    bar,
    clock_at,
    labelled_series,
    make_harness,
)
from web_fixtures import insert_candles, make_settings, migrated_engine, qualifying_candles

from trading_assistant.forward_testing.models import paper_outcome_is_settled
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.web import create_app

LONG_ENTRY = "124"
LONG_STOP = "117"
LONG_TARGET = "138"


def _client(engine, settings, *, clock):
    from fastapi.testclient import TestClient

    app = create_app(engine=engine, settings=settings, clock=lambda: clock)
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path):
    engine, url = migrated_engine(tmp_path, "tracker_empty.sqlite3")
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = _client(engine, settings, clock=clock_at(QUALIFYING_BOUNDARY))
    yield engine, client
    engine.dispose()


def plan_harness():
    """The labelled series processed at its qualifying close: one paper plan."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    return harness


def stopped_harness():
    """Entry reached, stopped, then further closes that record no new plan.

    The labelled series qualifies two setups at its qualifying close. The
    first becomes the paper plan; the second is refused while the slot is
    occupied and takes the slot only after the stop releases it — exactly the
    one-active-paper-trade policy. Both plans stay recorded; later closes
    record observations only.
    """

    harness = plan_harness()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 120, high=126, low=116),), refresh_market_data=False)
    # Two further closes after the stop: whatever they conclude, they record
    # observations only, no second decision.
    harness.step((bar(23, 121, low=118, high=122),), refresh_market_data=False)
    harness.step((bar(24, 122, low=119, high=123),), refresh_market_data=False)
    return harness


def stopped_plan(harness):
    """The recorded plan whose outcome became STOPPED."""

    for plan in harness.plans():
        outcome = next(
            item
            for item in harness.latest_outcomes()
            if item.paper_plan_id == plan.paper_plan_id
        )
        if outcome.observation.status is OutcomeStatus.STOPPED:
            return plan, outcome
    raise AssertionError("no recorded plan with a STOPPED latest outcome")


def tracker_of(payload: dict) -> dict:
    assert "paper_tracker" in payload, "the forward payload must carry the tracker"
    return payload["paper_tracker"]


# ----------------------------------------------------------------------
# Empty ledger
# ----------------------------------------------------------------------


def test_empty_ledger_tracks_nothing_and_invents_no_plan(empty_client) -> None:
    _, client = empty_client
    tracker = tracker_of(client.get("/api/forward").json())

    assert tracker["label"] == "PAPER OBSERVATION — NO REAL ORDER"
    assert tracker["tracked_plans"] == []
    assert tracker["active_paper_plan_id"] is None
    assert tracker["unresolved_paper_plan_count"] == 0
    assert tracker["recorded_paper_plan_count"] == 0
    # With no close ever recorded, the assessment slot says so explicitly
    # instead of standing in a placeholder verdict.
    assert tracker["latest_assessment"]["available"] is False
    assert tracker["latest_assessment"]["unavailable_reason"]
    assert tracker["latest_assessment"]["setup_state"] is None
    assert tracker["limitations"]


def test_tracker_limit_is_validated() -> None:
    harness = plan_harness()
    with pytest.raises(ValueError):
        harness.service.paper_tracker_payload(limit=0)
    with pytest.raises(ValueError):
        harness.service.paper_tracker_payload(limit=101)


# ----------------------------------------------------------------------
# A recorded, unresolved plan
# ----------------------------------------------------------------------


def test_recorded_plan_is_tracked_with_levels_entry_reach_and_outcome() -> None:
    harness = plan_harness()
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    tracker = harness.service.paper_tracker_payload()

    assert tracker["recorded_paper_plan_count"] == 1
    assert tracker["unresolved_paper_plan_count"] == 1
    assert tracker["active_paper_plan_id"] == harness.plans()[0].paper_plan_id
    assert len(tracker["tracked_plans"]) == 1

    plan = tracker["tracked_plans"][0]
    assert plan["paper_plan_id"] == harness.plans()[0].paper_plan_id
    assert plan["direction"] == "bullish"
    assert plan["entry"] == LONG_ENTRY
    assert plan["stop"] == LONG_STOP
    assert plan["targets"] == [LONG_TARGET]
    # Entry was reached by the closed candle; the interim verdict stays open.
    assert plan["entry_reached"] is True
    assert plan["entry_timestamp"] == bar(21, 126).timestamp.isoformat().replace(
        "+00:00", "Z"
    )
    assert plan["settled"] is False
    assert plan["occupies_slot"] is True
    assert plan["outcome_version_count"] >= 1
    outcome = plan["outcome"]
    assert outcome is not None
    assert outcome["status"] == OutcomeStatus.OPEN_AT_CUTOFF.value
    assert outcome["entry_reached"] is True
    assert outcome["recorded_at"]
    assert outcome["observed_through"]


def test_plan_without_outcome_yet_is_unresolved_and_occupies_the_slot() -> None:
    harness = plan_harness()
    tracker = harness.service.paper_tracker_payload()
    plan = tracker["tracked_plans"][0]

    assert plan["outcome"] is None
    assert plan["entry_reached"] is None
    assert plan["settled"] is False
    assert plan["occupies_slot"] is True
    assert tracker["unresolved_paper_plan_count"] == 1
    assert tracker["latest_assessment"]["available"] is True


# ----------------------------------------------------------------------
# The recorded STOPPED trade survives later "No trade" closes
# ----------------------------------------------------------------------


def test_stopped_plan_is_settled_and_still_listed_after_no_trade_closes() -> None:
    harness = stopped_harness()
    plan_row, outcome_row = stopped_plan(harness)
    tracker = harness.service.paper_tracker_payload()

    # Both recorded plans stay on the tracker: the newest one still occupying
    # the slot, and the stopped one retained with its final verdict.
    assert tracker["recorded_paper_plan_count"] == 2
    assert len(tracker["tracked_plans"]) == 2

    tracked_by_id = {plan["paper_plan_id"]: plan for plan in tracker["tracked_plans"]}
    stopped = tracked_by_id[plan_row.paper_plan_id]
    assert stopped["entry"] == LONG_ENTRY
    assert stopped["stop"] == LONG_STOP
    assert stopped["targets"] == [LONG_TARGET]
    assert stopped["entry_reached"] is True
    assert stopped["settled"] is True
    assert stopped["occupies_slot"] is False

    outcome = stopped["outcome"]
    assert outcome["status"] == OutcomeStatus.STOPPED.value
    assert outcome["stop_reached"] is True
    assert outcome["recorded_at"]

    # The newest assessment recorded no new paper plan: the engine said
    # "no trade" at that close, yet the recorded plans stay listed next to it.
    assert tracker["latest_assessment"]["available"] is True
    assert tracker["latest_assessment"]["as_of"] > plan_row.plan_as_of.isoformat().replace(
        "+00:00", "Z"
    )
    latest_cycle = harness.cycles()[-1]
    assert latest_cycle.paper_plan_count == 0

    # The engine's own settlement function agrees with the reported verdict.
    assert paper_outcome_is_settled(outcome_row, plan_row) is True


def test_outcome_versions_accumulate_and_only_the_latest_is_shown() -> None:
    harness = stopped_harness()
    plan_row, _ = stopped_plan(harness)
    versions = harness.outcome_versions(plan_row.paper_plan_id)
    assert len(versions) >= 2, "each processed close appends its own outcome version"

    tracker = harness.service.paper_tracker_payload()
    tracked = {
        plan["paper_plan_id"]: plan for plan in tracker["tracked_plans"]
    }[plan_row.paper_plan_id]
    assert tracked["outcome_version_count"] == len(versions)
    assert tracked["outcome"]["outcome_id"] == versions[-1].outcome_id
    assert tracked["outcome"]["sequence"] == versions[-1].sequence
    # The historical versions are unchanged and recoverable.
    assert [version.sequence for version in versions] == list(
        range(1, len(versions) + 1)
    )


# ----------------------------------------------------------------------
# HTTP surface: read-only, honest, and free of trading vocabulary
# ----------------------------------------------------------------------


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def test_tracker_endpoint_is_read_only_with_no_account_surface() -> None:
    harness = stopped_harness()
    client = _client(engine=harness.engine, settings=harness.settings, clock=harness.clock["t"])
    before = harness.counts()

    for _ in range(2):
        payload = client.get("/api/forward")
        assert payload.status_code == 200
        tracker = tracker_of(payload.json())
        assert tracker["symbol"] == SYMBOL
        assert tracker["timeframe"] == TIMEFRAME
        assert tracker["exchange"] == EXCHANGE
        assert len(tracker["tracked_plans"]) == 2

    assert harness.counts() == before
    assert before["paper_plans"] == 2
    assert before["outcome_versions"] >= 2

    keys = {key.lower() for key in _walk_keys(tracker)}
    for banned in (
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
    ):
        assert not any(banned in key for key in keys), banned
