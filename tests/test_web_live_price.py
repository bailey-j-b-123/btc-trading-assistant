"""The quote path is a display island: no DB, no qualification input."""

from dataclasses import replace

from web_fixtures import (
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)

from trading_assistant.web import live_price as module


def test_public_quote_only_validates_positive_last_trade(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    response = Response()
    monkeypatch.setattr(module, "urlopen", lambda url, timeout: response)
    assert module.KRAKEN_TICKER_URL.endswith("pair=XBTUSDT")
    assert module.CACHE_SECONDS >= 15
    assert module.STALE_SECONDS > module.CACHE_SECONDS
    monkeypatch.setattr(
        module.json,
        "load",
        lambda _: {"error": [], "result": {"XBTUSDT": {"c": ["82512.40"]}}},
    )
    assert module._fetch_price() == "82512.40"
    for bad in ["NaN", "-1", "0", "Infinity"]:
        monkeypatch.setattr(
            module.json,
            "load",
            lambda _, bad=bad: {"error": [], "result": {"XBTUSDT": {"c": [bad]}}},
        )
        try:
            module._fetch_price()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid live quote accepted")


def test_quote_failure_and_staleness_never_change_closed_engine(tmp_path, monkeypatch):
    engine, url = migrated_engine(tmp_path)
    kraken_candles = tuple(replace(candle, exchange="kraken") for candle in qualifying_candles())
    insert_candles(engine, kraken_candles)
    # This quote regression is specifically the retained Kraken path; align
    # the app's injected settings instead of relying on a process env default.
    settings = make_settings(url).model_copy(update={"exchange": "kraken"})
    client = make_client(engine, settings, clock=qualified_clock())
    try:
        before = client.get("/api/dashboard").json()
        assert before["market"]["candles"]
        monkeypatch.setattr(module, "_cached", None)
        monkeypatch.setattr(module, "_last_failed", False)
        monkeypatch.setattr(module, "_last_attempt", 0.0)
        monkeypatch.setattr(module, "_fetch_price", lambda: "82512.40")
        first = client.get("/api/market/live-price").json()
        assert first["status"] == "CURRENT"
        assert first["price"] == "82512.40"
        assert first["display_only"] is True
        # A failed refresh and a frozen old quote are explicitly stale.
        monkeypatch.setattr(
            module, "_fetch_price", lambda: (_ for _ in ()).throw(TimeoutError())
        )
        monkeypatch.setattr(module, "_last_attempt", 0.0)
        immediate = client.get("/api/market/live-price").json()
        assert immediate["status"] == "STALE"
        monkeypatch.setitem(
            module._cached, "received_monotonic", module.time.monotonic() - 60
        )
        stale = client.get("/api/market/live-price").json()
        assert stale["status"] == "STALE"
        assert stale["price"] == first["price"]  # no fabricated tick
        monkeypatch.setattr(module, "_cached", None)
        monkeypatch.setattr(module, "_last_attempt", 0.0)
        assert client.get("/api/market/live-price").json()["status"] == "UNAVAILABLE"
        after = client.get("/api/dashboard").json()
        for key in (
            "market",
            "qualification",
            "planning",
            "plan",
            "multi_timeframe",
            "overlays",
            "scenario",
            "looking_for",
        ):
            assert before[key] == after[key], key
    finally:
        engine.dispose()


def test_looking_for_is_projection_of_one_actual_setup_not_a_plan(tmp_path):
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    client = make_client(engine, make_settings(url), clock=qualified_clock())
    try:
        payload = client.get("/api/dashboard").json()
        view = payload["looking_for"]
        setup = next(
            s
            for s in payload["qualification"]["snapshot"]["setups"]
            if s["id"] == view["setup_id"]
        )
        assert view["available"] is True
        assert view["timeframe"] == payload["meta"]["timeframe"]
        assert view["family"] == setup["family"]
        assert view["direction"] == setup["direction"]
        assert view["reference"] == payload["overlays"]["setup_reference"]
        assert view["pending_required"] == [
            {"rule_id": r["rule_id"], "reason": r["reason"]}
            for r in setup["rules"]
            if r["required"] and r["outcome"] == "pending"
        ]
        assert view["invalidation"] == payload["plan"]["invalidation"]["value"]
        assert "future" not in str(view).lower()
    finally:
        engine.dispose()


def test_no_setup_never_creates_scenario(tmp_path):
    engine, url = migrated_engine(tmp_path)
    client = make_client(engine, make_settings(url), clock=qualified_clock())
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["looking_for"]["available"] is False
        assert "reference" not in payload["looking_for"]
        assert payload["market"]["candles"] == []
    finally:
        engine.dispose()


def test_watch_reference_is_only_displayed_for_one_unambiguous_watch(tmp_path):
    from dataclasses import replace

    from web_fixtures import EPOCH, INTERVAL, watch_candles

    from trading_assistant.web.dashboard_service import DashboardService

    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, watch_candles())
    client = make_client(engine, make_settings(url), clock=EPOCH + 7 * INTERVAL)
    try:
        payload = client.get("/api/dashboard").json()
        state = client.app.state.services
        snapshot, frame, _ = DashboardService(state)._evaluate(
            exchange=state.settings.exchange,
            symbol=payload["meta"]["symbol"],
            timeframe=state.settings.default_timeframe,
            as_of=EPOCH + 7 * INTERVAL,
        )
        watch = [s for s in snapshot.setups if s.state.value == "WATCH"]
        if len(watch) == 1:
            assert payload["looking_for"]["setup_id"] == watch[0].id
            assert payload["looking_for"]["state"] == "WATCH"
            assert payload["looking_for"]["invalidation"] is None
        # A second simultaneous watch is NOT a new engine selection.
        if watch:
            ambiguous = replace(
                snapshot, setups=(watch[0], replace(watch[0], id="another-watch"))
            )
            projected = DashboardService(state)._looking_for(
                frame=frame,
                snapshot=ambiguous,
                selected=None,
                plan=None,
            )
            assert projected["available"] is False
            assert "reference" not in projected
    finally:
        engine.dispose()
