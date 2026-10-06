"""Step 10 dashboard payload: setup states, plan exactness, freshness, evidence.

Scenarios:
* empty database            -> NO_SETUP, UNKNOWN freshness, no invented price
* watch fixture             -> WATCH renders with developing-evidence state
* qualifying replay         -> QUALIFIED + PLANNABLE levels identical to Step 6
* QUALIFIED but NO_PLAN     -> planning state visibly distinct from setup state
* stale/historical clocks   -> freshness labels, never LIVE/CURRENT
* arbitrary symbol          -> symbol-generic behaviour

All fixtures are synthetic and labelled; no scenario uses real market data.
"""

import pytest
from test_setup_qualification import breakout, frame, held, result
from web_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    insert_candles,
    later_clock,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
    watch_candles,
)

from trading_assistant.trade_planning import plan_trade
from trading_assistant.web.dashboard_service import DashboardService


@pytest.fixture
def qualified_client(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    test_client = make_client(engine, settings, clock=qualified_clock())
    yield test_client
    engine.dispose()


# ---------------------------------------------------------------------------
# NO_SETUP on empty data
# ---------------------------------------------------------------------------


def test_empty_database_renders_no_setup_without_fake_data(tmp_path):
    engine, url = migrated_engine(tmp_path)
    settings = make_settings(url)
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["qualification"]["state"] == "NO_SETUP"
        assert payload["qualification"]["selected_setup_id"] is None
        assert payload["plan"] is None
        assert payload["planning"]["state"] is None
        # No invented price: nothing stored means null, never a number.
        assert payload["market"]["latest_closed_candle"] is None
        assert payload["market"]["candles"] == []
        # Freshness is UNKNOWN without stored candles.
        assert payload["freshness"]["status"] == "UNKNOWN"
        assert payload["freshness"]["latest_stored"] is None
        # Decision controls are disabled with an explanation.
        assert payload["journal"]["can_decide"] is False
        assert payload["journal"]["disabled_reason"]
        # Explanation renders for NO_SETUP too (or reports a structured error).
        explanation = payload["explanation"]
        assert "sections" in explanation or explanation.get("available") is False
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# WATCH
# ---------------------------------------------------------------------------


def test_watch_snapshot_renders(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, watch_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=EPOCH + 7 * INTERVAL)
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["qualification"]["state"] == "WATCH"
        assert payload["qualification"]["setups"], (
            "WATCH must expose tracked candidates"
        )
        assert payload["plan"] is None
        assert payload["planning"]["state"] is None
        assert payload["journal"]["can_decide"] is False
        assert (
            "developing" in payload["journal"]["disabled_reason"].lower()
            or "no qualified" in payload["journal"]["disabled_reason"].lower()
        )
        assert payload["freshness"]["status"] == "CURRENT"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# QUALIFIED + PLANNABLE: levels must match Step 6 exactly
# ---------------------------------------------------------------------------


def test_qualified_renders_with_exact_plan_levels(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    assert payload["qualification"]["state"] == "QUALIFIED"
    assert payload["qualification"]["status"] == "evaluated"
    selected = payload["qualification"]["selected_setup_id"]
    assert selected is not None

    plan = payload["plan"]
    assert plan is not None
    assert payload["planning"]["state"] == "PLANNABLE"

    # Independent recomputation through Step 6 with the same deterministic
    # services: the API must not reshape or recompute any level.
    state = qualified_client.app.state.services
    service = DashboardService(state)
    snapshot, frame_, _frames = service._evaluate(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", as_of=qualified_clock()
    )
    expected = plan_trade(snapshot=snapshot, frame=frame_, setup_id=selected)
    assert plan == expected.to_json_dict(), (
        "dashboard plan must equal Step 6 output exactly"
    )
    assert plan["entry"]["value"] == "124"
    assert plan["stop"]["value"] == "117"
    assert plan["invalidation"]["value"] == "117"
    assert plan["risk_per_unit"] == "7"
    assert [t["level"]["value"] for t in plan["targets"]] == ["138"]


def test_qualified_payload_keeps_setup_and_planning_state_separate(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    assert payload["qualification"]["state"] == "QUALIFIED"
    assert "state" in payload["planning"]
    # Two distinct vocabularies: the UI can never conflate them.
    assert payload["planning"]["state"] in {"PLANNABLE", "NO_PLAN", "INVALID"}


def test_dashboard_market_and_overlays_present(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    market = payload["market"]
    assert market["latest_closed_candle"]["close"] == "124"
    assert market["complete"] is True
    overlays = payload["overlays"]
    assert isinstance(overlays["zones"], list)
    assert isinstance(overlays["equal_levels"], list)
    assert overlays["setup_reference"] is not None


def test_evidence_sections_have_content(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    snapshot = payload["qualification"]["snapshot"]
    selected = payload["qualification"]["selected_setup_id"]
    setup = next(s for s in snapshot["setups"] if s["id"] == selected)
    rules = setup["rules"]
    assert rules, "selected setup must carry its deterministic rules"
    assert all(r["outcome"] in {"passed", "failed", "pending"} for r in rules)
    # Evidence for/against must be surfaced verbatim from Step 5.
    evidence = [e for r in rules for e in r["evidence"]]
    assert any(e["status"] == "supportive" for e in evidence)


def test_step9_explanation_displayed(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    explanation = payload["explanation"]
    assert explanation["provenance"] == "deterministic-local"
    assert explanation["sections"], "explanation must contain sections"
    titles = [section["title"].lower() for section in explanation["sections"]]
    assert any("engine sees" in title for title in titles)
    assert explanation["limitations"], "limitations must be listed"
    assert explanation["setup_state"] == "QUALIFIED"
    assert explanation["plan_state"] == "PLANNABLE"


# ---------------------------------------------------------------------------
# QUALIFIED + NO_PLAN: visibly distinct from PLANNABLE
# ---------------------------------------------------------------------------


def test_qualified_but_no_plan_is_distinct(qualified_client, monkeypatch):
    """A genuine QUALIFIED snapshot whose frame lacks confirmation -> NO_PLAN.

    Reuses the exact Step 6 gap fixture (missing held retest in the frame) so
    the refusal is the real planner output, not a UI invention.
    """

    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    snapshot = result(frames)
    stripped = frame(7, (seed,))  # QUALIFIED snapshot, frame lacking the retest

    def fake_evaluate(self, **_kwargs):
        return snapshot, stripped, (stripped,)

    monkeypatch.setattr(DashboardService, "_evaluate", fake_evaluate)
    payload = qualified_client.get("/api/dashboard").json()

    assert payload["qualification"]["state"] == "QUALIFIED"
    assert payload["planning"]["state"] == "NO_PLAN"
    assert "confirmation_event_missing" in payload["planning"]["reasons"]
    assert payload["plan"]["state"] == "NO_PLAN"
    assert payload["plan"]["entry"]["value"] is None  # UNKNOWN stays unknown
    assert payload["journal"]["can_decide"] is False
    assert "NO_PLAN" in payload["journal"]["disabled_reason"]


# ---------------------------------------------------------------------------
# Freshness: CURRENT / STALE / HISTORICAL — never LIVE
# ---------------------------------------------------------------------------


def test_freshness_current_at_boundary(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["freshness"]["status"] == "CURRENT"
        assert payload["freshness"]["staleness_intervals"] == 0
    finally:
        engine.dispose()


def test_stale_data_is_visibly_labelled_when_clock_advances(tmp_path):
    """Default view snaps to the newest stored boundary: HISTORICAL, never LIVE."""

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=later_clock(hours=5))
    try:
        payload = client.get("/api/dashboard").json()
        freshness = payload["freshness"]
        assert freshness["status"] == "HISTORICAL"
        assert freshness["staleness_intervals"] == 5
        assert freshness["status"] not in {"CURRENT", "UNKNOWN"}
        # The dashboard honours the snap: as_of is the last stored boundary.
        assert payload["meta"]["as_of"].startswith("2024-01-01T21:00")
    finally:
        engine.dispose()


def test_stale_label_for_explicit_current_boundary_request(tmp_path):
    """Requesting the current boundary while data lags is STALE."""

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=later_clock(hours=5))
    try:
        explicit = later_clock(hours=5).isoformat().replace("+00:00", "Z")
        payload = client.get("/api/dashboard", params={"as_of": explicit}).json()
        freshness = payload["freshness"]
        assert freshness["status"] == "STALE"
        assert freshness["staleness_intervals"] == 5
    finally:
        engine.dispose()


def test_historical_as_of_never_labelled_current(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    settings = make_settings(url)
    client = make_client(engine, settings, clock=later_clock(hours=8))
    try:
        historical_as_of = (EPOCH + 21 * INTERVAL).isoformat().replace("+00:00", "Z")
        payload = client.get(
            "/api/dashboard", params={"as_of": historical_as_of}
        ).json()
        assert payload["freshness"]["status"] == "HISTORICAL"
        assert payload["qualification"]["state"] == "QUALIFIED"
    finally:
        engine.dispose()


def test_as_of_must_be_candle_close_boundary(qualified_client):
    bad = (EPOCH + 21 * INTERVAL + INTERVAL / 2).isoformat().replace("+00:00", "Z")
    response = qualified_client.get("/api/dashboard", params={"as_of": bad})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "as_of_not_aligned"


def test_future_as_of_rejected(qualified_client):
    future = (EPOCH + 40 * INTERVAL).isoformat().replace("+00:00", "Z")
    response = qualified_client.get("/api/dashboard", params={"as_of": future})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "as_of_in_future"


def test_no_price_when_series_missing_for_symbol(qualified_client):
    payload = qualified_client.get(
        "/api/dashboard", params={"symbol": "ETH/USD"}
    ).json()
    assert payload["market"]["latest_closed_candle"] is None
    assert payload["freshness"]["status"] == "UNKNOWN"
    assert payload["qualification"]["state"] == "NO_SETUP"


# ---------------------------------------------------------------------------
# Setup-reference overlay: explicit per-kind reference resolution
# ---------------------------------------------------------------------------


def test_setup_reference_overlay_resolves_every_seed_kind():
    """The chart overlay resolves references without guessing attributes.

    A FailedBreakout carries its reference on the wrapped breakout; resolving
    it must not depend on the source breakout also being present in the frame
    catalog. Retests resolve through their source breakout; reference-less
    events are skipped explicitly.
    """

    from dataclasses import replace

    from test_pattern_liquidity import bar, prefix, snap
    from test_setup_qualification import at, failure

    from trading_assistant.pattern_liquidity.events import (
        Breakout,
        FailedBreakout,
        Retest,
    )
    from trading_assistant.setup_qualification import QualificationFrame

    seed = failure()
    assert isinstance(seed, FailedBreakout)
    # A frame whose catalog holds only the failure (no source breakout).
    source = snap(prefix() + (bar(5, 112), bar(6, 109)), at=at(7))
    assert any(
        isinstance(e, FailedBreakout) and e.id == seed.id
        for e in source.events()
    )
    lone = replace(
        source,
        breakouts=(),
        failed_breakouts=tuple(
            e for e in source.failed_breakouts if e.id == seed.id
        ),
        sweeps=(),
        retests=(),
        equal_levels=(),
        chart_patterns=(),
    )
    assert not any(isinstance(e, Breakout) for e in lone.events())
    resolved = DashboardService._reference_level(
        frame=QualificationFrame(lone, ()),
        reference_id=seed.breakout.reference.id,
    )
    assert resolved is not None
    assert resolved["type"] == seed.breakout.reference.type
    assert resolved["band_low"] == format(seed.breakout.reference.band_low, "f")
    assert resolved["band_high"] == format(
        seed.breakout.reference.band_high, "f"
    )

    # A retest-only catalog resolves through the source breakout as well.
    retest = next(
        e
        for e in source.events()
        if isinstance(e, Retest) and e.breakout.id == seed.breakout.id
    ) if any(
        isinstance(e, Retest) and e.breakout.id == seed.breakout.id
        for e in source.events()
    ) else None
    if retest is not None:
        lone_retest = replace(
            source,
            breakouts=(),
            failed_breakouts=(),
            sweeps=(),
            retests=(retest,),
            equal_levels=(),
            chart_patterns=(),
        )
        resolved_retest = DashboardService._reference_level(
            frame=QualificationFrame(lone_retest, ()),
            reference_id=seed.breakout.reference.id,
        )
        assert resolved_retest is not None
        assert resolved_retest["band_low"] == resolved["band_low"]

    # Unknown references stay unknown; nothing is invented.
    assert (
        DashboardService._reference_level(
            frame=QualificationFrame(source, ()),
            reference_id="no-such-reference",
        )
        is None
    )
    assert (
        DashboardService._reference_level(
            frame=QualificationFrame(source, ()), reference_id=None
        )
        is None
    )


# ---------------------------------------------------------------------------
# Market state + scenario: deterministic projections, never invented facts
# ---------------------------------------------------------------------------


def test_market_state_reports_current_facts(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    market_state = payload["market_state"]
    assert market_state["available"] is True
    assert market_state["trend"]["direction"] == "bullish"
    assert market_state["trend"]["sufficient"] is True
    assert market_state["trend"]["transition"] == "unchanged"
    assert market_state["trend"]["momentum"] == "steady"
    assert market_state["volatility"]["available"] is True
    assert market_state["volatility"]["atr_percent_of_price"] == "2.75345715"
    assert market_state["volatility"]["direction"]["label"] == "contracting"
    assert market_state["volume"]["sufficient"] is True
    assert market_state["volume"]["relative_volume"] == "1.00000000"
    assert market_state["range"]["active"] is False
    assert market_state["levels"]["zone_count"] == 5
    assert market_state["levels"]["nearest_support"]["band_high"] == "122"
    assert market_state["events"]["breakouts"]["count"] == 6
    assert market_state["events"]["retests"]["held_count"] >= 1
    assert len(market_state["breakout_state"]["attempts"]) == 2
    assert market_state["higher_timeframes"]["requested"] == []
    assert market_state["last_close"] == "124"


def test_scenario_answers_come_from_backend_facts(qualified_client):
    payload = qualified_client.get("/api/dashboard").json()
    scenario = payload["scenario"]
    assert scenario["available"] is True
    assert "BULLISH" in scenario["doing_now"]
    assert "QUALIFIED" in scenario["doing_now"]
    seeing = scenario["bot_seeing"]
    assert seeing["state"] == "QUALIFIED"
    assert seeing["live_count"] == 4
    assert all(s["direction"] == "bullish" for s in seeing["live_setups"])
    assert all(s["bars_remaining"] >= 0 for s in seeing["live_setups"])
    assert all(s["vetoed"] is False for s in seeing["live_setups"])
    # Bullish side develops; bearish side has nothing developing.
    assert len(scenario["strengthen_bullish"]["developing_setups"]) == 4
    assert scenario["strengthen_bearish"]["none_developing"] is True
    assert "breakout_retest_continuation" in scenario["strengthen_bearish"][
        "to_start_a_setup"
    ]
    # Waiting-for merges the WATCH setups' pending required rules.
    waiting = {item["rule"] for item in scenario["waiting_for"]["pending"]}
    assert waiting == {"held_retest", "later_evaluation"}
    assert scenario["waiting_for"]["note"] is None
    # Invalidation carries the exact Step 6 levels for the selected setup.
    selected = payload["qualification"]["selected_setup_id"]
    cases = {c["setup_id"]: c for c in scenario["invalidate"]["cases"]}
    assert cases[selected]["plan_invalidation"]["value"] == "117"
    assert cases[selected]["plan_stop"]["value"] == "117"
    assert cases[selected]["plan_entry"]["value"] == "124"


def test_market_state_and_scenario_stay_honest_without_data(tmp_path):
    engine, url = migrated_engine(tmp_path)
    settings = make_settings(url)
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        payload = client.get("/api/dashboard").json()
        market_state = payload["market_state"]
        assert market_state["available"] is True
        assert market_state["trend"]["sufficient"] is False
        assert market_state["volatility"]["available"] is False
        assert market_state["volume"]["sufficient"] is False
        assert market_state["range"]["active"] is False
        assert market_state["levels"]["zone_count"] == 0
        assert market_state["events"]["breakouts"]["count"] == 0
        assert market_state["last_close"] is None
        scenario = payload["scenario"]
        assert scenario["available"] is True
        assert "UNKNOWN" in scenario["doing_now"]
        assert "NO_SETUP" in scenario["doing_now"]
        assert scenario["bot_seeing"]["live_count"] == 0
        assert scenario["waiting_for"]["pending"] == []
        assert "fresh seed event" in scenario["waiting_for"]["note"]
        assert scenario["invalidate"]["cases"] == []
    finally:
        engine.dispose()


def test_market_state_helpers_are_total():
    from trading_assistant.web.dashboard_service import (
        metric_direction,
        range_transition,
        trend_transition,
    )

    assert trend_transition(None, None) == "unknown"
    assert metric_direction(None, None, up="u", down="d")["label"] == "unknown"
    assert range_transition(None, None) == "absent"
