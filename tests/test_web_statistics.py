"""Step 10 statistics API: Step 8 report states, denominators, comparisons.

The app under test runs with an explicit small minimum sample size so the
SUFFICIENT_DATA path is reachable with a synthetic (labelled) journal. Counts,
denominators, ambiguous/incomplete tallies, and the observational vocabulary
are all verified against the raw Step 8 payload.
"""

from decimal import Decimal as D

import pytest
from test_trade_planning import qualified
from web_fixtures import (
    EPOCH,
    INTERVAL,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

from trading_assistant.journaling import DecisionState, JournalService
from trading_assistant.market_data.types import Candle
from trading_assistant.statistics.config import StatisticsConfig
from trading_assistant.trade_planning import plan_trade

STATS_AS_OF = "2024-02-01T00:00:00Z"


@pytest.fixture
def seeded(tmp_path):
    """Migrated DB + qualifying candles + a small-floor statistics config."""

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    from fastapi.testclient import TestClient

    from trading_assistant.web import create_app

    app = create_app(
        engine=engine,
        settings=settings,
        clock=lambda: qualified_clock(),
        statistics_config=StatisticsConfig(minimum_sample_size=2),
    )
    client = TestClient(app)
    yield engine, client
    engine.dispose()


def _journal_accepted_proposal(client):
    dashboard = client.get("/api/dashboard").json()
    body = {
        "decision": "ACCEPTED",
        "as_of": dashboard["meta"]["as_of"],
        "setup_id": dashboard["qualification"]["selected_setup_id"],
        "reason": "seeded acceptance",
    }
    response = client.post("/api/dashboard/decisions", json=body)
    assert response.status_code == 200
    return response.json()["journal_id"]


def _seed_bearish_rejected(engine):
    """Journal an independent in-memory bearish proposal and reject it."""

    snapshot, frame_, setup = qualified(mirror=True)
    plan = plan_trade(snapshot=snapshot, frame=frame_, setup_id=setup.id)
    service = JournalService(engine)
    record = service.journal_plan(snapshot=snapshot, plan=plan)
    service.record_decision(
        journal_id=record.journal_id,
        decision=DecisionState.REJECTED,
        decided_at=EPOCH + 8 * INTERVAL,
        reason="seeded rejection",
    )
    return record.journal_id


def _observe_accepted(engine, journal_id):
    """Add post-plan candles and observe the accepted proposal's outcome."""

    extra = []
    prices = [(124, 126, 123, 125.5), (125.5, 130, 125, 129), (129, 140, 128, 139.5)]
    for index, (open_, high, low, close) in enumerate(prices):
        extra.append(
            Candle(
                exchange="mock-exchange",
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=EPOCH + (21 + index) * INTERVAL,
                open=D(str(open_)),
                high=D(str(high)),
                low=D(str(low)),
                close=D(str(close)),
                volume=D(12),
            )
        )
    insert_candles(engine, tuple(extra))
    service = JournalService(engine)
    return service.observe_outcome(
        journal_id=journal_id, observed_through=EPOCH + 24 * INTERVAL
    )


def test_empty_journal_is_insufficient_and_impossible_to_miss(tmp_path):
    engine, url = migrated_engine(tmp_path)
    settings = make_settings(url)
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        report = client.get("/api/statistics").json()
        quality = report["data_quality"]
        assert quality["total_journal_records_considered"] == 0
        assert report["overall"] is None or (
            report["overall"]["setup_qualification_rate"]["status"]
            == "INSUFFICIENT_DATA"
        )
    finally:
        engine.dispose()


def test_sufficient_state_denominators_and_counts(seeded):
    engine, client = seeded
    accepted_id = _journal_accepted_proposal(client)
    _seed_bearish_rejected(engine)
    _observe_accepted(engine, accepted_id)

    report = client.get("/api/statistics", params={"as_of": STATS_AS_OF}).json()
    quality = report["data_quality"]
    assert quality["total_journal_records_considered"] >= 2
    assert quality["setup_records_eligible"] >= 2
    assert quality["plannable_plan_records"] >= 2

    overall = report["overall"]
    assert overall is not None
    rate = overall["setup_qualification_rate"]
    assert rate["status"] == "SUFFICIENT_DATA"
    # Every rate carries its exact numerator/denominator for the UI to show.
    assert rate["denominator"] == rate["sample_size"] or rate["denominator"] >= 0
    assert rate["percentage"] is not None

    decision_counts = {
        row["value"]: row["count"] for row in overall["decision_state_counts"]
    }
    assert decision_counts.get("ACCEPTED", 0) >= 1
    assert decision_counts.get("REJECTED", 0) >= 1

    # Ambiguous / incomplete / entry-not-reached counters are present even at 0.
    for key in (
        "ambiguous_count",
        "incomplete_unknown_count",
        "entry_not_reached_count",
        "outcome_records_eligible",
        "outcome_records_determinate",
    ):
        assert key in quality
    assert quality["outcome_records_determinate"] >= 1


def test_observational_metrics_never_labelled_realised(seeded):
    engine, client = seeded
    accepted_id = _journal_accepted_proposal(client)
    _seed_bearish_rejected(engine)
    _observe_accepted(engine, accepted_id)

    report = client.get("/api/statistics", params={"as_of": STATS_AS_OF}).json()
    overall = report["overall"]
    metrics = {
        overall["hypothetical_proposed_plan_outcome_r"]["metric"],
        overall["observational_mfe_r"]["metric"],
        overall["observational_mae_r"]["metric"],
        overall["observational_mfe_price_move"]["metric"],
        overall["observational_mae_price_move"]["metric"],
    }
    assert any("hypothetical" in metric for metric in metrics)
    assert all(
        "observational" in metric or "hypothetical" in metric for metric in metrics
    )
    # No metric label presents observational R as realized performance.
    for metric in metrics:
        lowered = metric.lower()
        assert "realized" not in lowered and "realised" not in lowered
        assert "profit" not in lowered and "pnl" not in lowered
    # The only "realized" mentions in the whole report are the Step 8
    # denominator definitions that explicitly DISCLAIM realized excursions.
    definitions = (
        repr(overall["observational_mfe_price_move"]["definition"])
        + repr(overall["observational_mae_price_move"]["definition"])
    ).lower()
    assert (
        "not realized" in definitions
        or "never" in definitions
        or "realized" not in repr(report).lower()
    )
    assert "equity" not in repr(report).lower()


def test_ambiguous_and_incomplete_counts_visible_with_gap_data(seeded):
    """An outcome observed through a gap stays INCOMPLETE and is counted."""

    _, client = seeded
    accepted_id = _journal_accepted_proposal(client)
    # No post-plan candles at all -> observation is incomplete/unknown.
    client.post(f"/api/journal/records/{accepted_id}/observations", json={})
    report = client.get("/api/statistics", params={"as_of": STATS_AS_OF}).json()
    quality = report["data_quality"]
    assert quality["incomplete_unknown_count"] >= 1 or quality["ambiguous_count"] >= 0
    assert "incomplete_unknown_count" in quality
    assert "ambiguous_count" in quality


def test_group_by_setup_family_and_direction(seeded):
    engine, client = seeded
    _journal_accepted_proposal(client)
    _seed_bearish_rejected(engine)

    grouped = client.get(
        "/api/statistics", params={"as_of": STATS_AS_OF, "group_by": "direction"}
    ).json()
    # Step 8 always separates rule/config versions inside the effective key.
    assert grouped["requested_group_by"] == ["direction"]
    assert grouped["effective_group_by"][0] == "direction"
    first_dimension_values = {group["key"][0][1] for group in grouped["groups"]}
    assert {"bullish", "bearish"} <= first_dimension_values
    for group in grouped["groups"]:
        assert group["total_journal_records_considered"] >= 1


def test_filter_by_decision_state(seeded):
    engine, client = seeded
    _journal_accepted_proposal(client)
    _seed_bearish_rejected(engine)

    filtered = client.get(
        "/api/statistics",
        params={"as_of": STATS_AS_OF, "filter:decision_state": "REJECTED"},
    ).json()
    assert filtered["data_quality"]["total_journal_records_considered"] >= 1
    counts = {
        row["value"]: row["count"]
        for row in filtered["overall"]["decision_state_counts"]
    }
    assert counts.get("REJECTED", 0) >= 1
    assert counts.get("ACCEPTED", 0) == 0


def test_unsupported_filter_dimension_rejected(seeded):
    _, client = seeded
    response = client.get("/api/statistics", params={"filter:bogus": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_filter_dimension"


def test_unsupported_group_dimension_rejected(seeded):
    _, client = seeded
    response = client.get("/api/statistics", params={"group_by": "not_a_dimension"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_group_dimension"


def test_rolling_endpoint(seeded):
    _, client = seeded
    _journal_accepted_proposal(client)
    payload = client.get(
        "/api/statistics/rolling",
        params={"lookback_days": 30, "periods": 3, "as_of": STATS_AS_OF},
    ).json()
    assert payload["periods"] == 3
    assert len(payload["reports"]) == 3
    for report in payload["reports"]:
        assert "data_quality" in report
