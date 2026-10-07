"""Component #1 required-window gate: regression tests.

These tests pin the closed-candle-only data-integrity contract:

* the required trailing depth is derived from the live parameter objects
  (``replay_span + max(structural_span, pattern_span)`` = 131 by default);
* every required window ends at the latest fully closed candle — a forming
  candle is never required and never assessed;
* only a hole *inside* the required window blocks; an older hole neither
  blocks the decision nor hides from whole-series diagnostics;
* bad/missing/stale/incomplete data fails closed with exact missing
  boundaries recorded, never silently valid: no plan is produced and no
  decision is accepted over an incomplete required window;
* best-effort backfill can only restore completeness through the unchanged
  Step 2 path, never invent it.

Everything runs offline against temporary SQLite databases, synthetic
labelled candles, and fake public OHLCV sources.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from forward_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    FakeExchange,
    bar,
    extend,
    labelled_series,
    make_harness,
)
from multi_timeframe_fixtures import (
    DECISION_TIME,
    four_hour_candles,
    hierarchy_candles,
    insert_hierarchy,
    make_service as make_mtf_service,
    one_hour_candles,
)
from test_market_data import FIVE_MINUTES_MS, FakeSource, candle_row, create_service
from web_fixtures import (
    QUALIFYING_ROWS,
    insert_candles,
    make_client,
    make_settings,
    migrated_engine,
    qualified_clock,
    qualifying_candles,
)
from web_fixtures import (
    bar as web_bar,
)

from trading_assistant.forward_testing import DataHealth, HeartbeatStatus
from trading_assistant.market_data.integrity import (
    assess_required_window,
    required_trailing_depth,
    required_window,
)
from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import datetime_to_milliseconds
from trading_assistant.market_data.validation import validate_ohlcv_rows
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.multi_timeframe.context import build_context_snapshot
from trading_assistant.multi_timeframe.setup import build_setup_snapshot
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.setup_qualification.parameters import QualificationParameters


def _default_depth() -> int:
    return required_trailing_depth(
        structure=MarketStructureParameters(),
        pattern=PatternLiquidityParameters(),
        qualification=QualificationParameters(),
    )


# ----------------------------------------------------------------------
# Required depth: derived from live parameters, never a magic constant
# ----------------------------------------------------------------------


def test_required_trailing_depth_defaults_to_replay_plus_deepest_span() -> None:
    assert _default_depth() == 11 + 120 == 131


def test_required_trailing_depth_follows_the_live_parameters() -> None:
    qualification = replace(
        QualificationParameters(),
        continuation_max_bars=20,
        reversal_max_bars=20,
        range_max_bars=20,
    )
    assert (
        required_trailing_depth(
            structure=MarketStructureParameters(),
            pattern=PatternLiquidityParameters(),
            qualification=qualification,
        )
        == 21 + 120
    )


def test_required_trailing_depth_rejects_a_non_positive_lookback() -> None:
    def _structure(period):
        return SimpleNamespace(
            ranges=SimpleNamespace(lookback_candles=120),
            volume=SimpleNamespace(period=period),
            volatility=SimpleNamespace(period=14),
        )

    with pytest.raises(ValueError, match="volume.period"):
        required_trailing_depth(
            structure=_structure(0),
            pattern=PatternLiquidityParameters(),
            qualification=QualificationParameters(),
        )
    with pytest.raises(TypeError, match="volume.period"):
        required_trailing_depth(
            structure=_structure(True),
            pattern=PatternLiquidityParameters(),
            qualification=QualificationParameters(),
        )


# ----------------------------------------------------------------------
# Required window: always ends at the latest fully closed candle
# ----------------------------------------------------------------------


def test_required_window_ends_at_the_latest_closed_open() -> None:
    # A decision instant in the middle of a forming candle still requires only
    # closed candles: the window ends at the last close, never the forming one.
    start, end = required_window(
        QUALIFYING_BOUNDARY + timedelta(minutes=30), TIMEFRAME, depth=5
    )
    assert end == QUALIFYING_BOUNDARY - INTERVAL
    assert start == end - 4 * INTERVAL


def test_required_window_rejects_a_non_positive_depth() -> None:
    with pytest.raises(ValueError, match="depth"):
        required_window(QUALIFYING_BOUNDARY, TIMEFRAME, depth=0)


# ----------------------------------------------------------------------
# Assessment: exact missing opens, truncation reported, never fabricated
# ----------------------------------------------------------------------


def test_contiguous_short_history_is_complete_with_truncation_reported() -> None:
    assessment = assess_required_window(
        labelled_series(),
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        decision_time=QUALIFYING_BOUNDARY,
        depth=_default_depth(),
    )
    assert assessment.complete
    assert assessment.missing_opens == ()
    assert assessment.missing_count == 0
    assert assessment.window_end == QUALIFYING_BOUNDARY - INTERVAL
    # The required window starts before the first stored candle: reported as
    # truncation, never filled in.
    assert assessment.truncated_before == labelled_series()[0].timestamp
    assert assessment.first_stored_open == labelled_series()[0].timestamp


def test_hole_inside_the_required_window_reports_exact_opens() -> None:
    series = labelled_series()
    hole = series[8].timestamp
    gapped = tuple(c for c in series if c.timestamp != hole)
    assessment = assess_required_window(
        gapped,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        decision_time=QUALIFYING_BOUNDARY,
        depth=_default_depth(),
    )
    assert not assessment.complete
    assert assessment.missing_opens == (hole,)
    assert hole.isoformat() in assessment.missing_summary()
    payload = assessment.to_json_dict()
    assert payload["complete"] is False
    assert payload["missing_opens"] == ["2024-01-01T08:00:00Z"]
    assert payload["required_depth"] == 131


def test_hole_older_than_the_required_window_does_not_block() -> None:
    prefix = tuple(bar(i, 100 + (i % 9)) for i in range(-120, 0) if i != -115)
    series = prefix + labelled_series()
    assessment = assess_required_window(
        series,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        decision_time=QUALIFYING_BOUNDARY,
        depth=_default_depth(),
    )
    assert assessment.complete
    assert assessment.missing_opens == ()
    # The full 131-close window is stored: nothing is truncated.
    assert assessment.truncated_before is None


def test_cross_instrument_input_is_refused_not_filtered() -> None:
    series = labelled_series()
    mixed = series[:8] + (replace(series[8], symbol="ETH/USDT"),) + series[9:]
    with pytest.raises(ValueError, match="must match"):
        assess_required_window(
            mixed,
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            decision_time=QUALIFYING_BOUNDARY,
            depth=_default_depth(),
        )
    with pytest.raises(TypeError, match="Candle"):
        assess_required_window(
            ["not-a-candle"],
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            decision_time=QUALIFYING_BOUNDARY,
            depth=_default_depth(),
        )


def test_empty_history_is_incomplete_with_no_effective_span() -> None:
    assessment = assess_required_window(
        (),
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        decision_time=QUALIFYING_BOUNDARY,
        depth=_default_depth(),
    )
    assert not assessment.complete
    assert assessment.first_stored_open is None
    assert assessment.effective_start is None


# ----------------------------------------------------------------------
# Step 2 validation: zero prices rejected, zero volume accepted,
# out-of-range rows reconciled
# ----------------------------------------------------------------------


def test_zero_and_negative_ohlc_are_rejected_as_non_positive() -> None:
    report = validate_ohlcv_rows(
        [
            [0, 0, 0, 0, 0, "3.25"],  # all-zero OHLC is still well-formed
            [FIVE_MINUTES_MS, "-1", "11.2", "9.8", "10.7", "3.25"],
        ],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )
    assert report.rejected_count == 2
    assert {issue.code for issue in report.issues} >= {"non_positive_price"}
    assert report.candles == ()


def test_zero_volume_is_accepted_while_negative_volume_is_rejected() -> None:
    report = validate_ohlcv_rows(
        [candle_row(0, volume="0")],
        exchange="mock-exchange",
        symbol="ETH/USDT",
        timeframe="5m",
    )
    assert report.rejected_count == 0
    assert len(report.candles) == 1
    assert report.candles[0].volume == 0


def test_out_of_range_rows_are_excluded_and_threaded_to_the_result(tmp_path) -> None:
    base = datetime_to_milliseconds(EPOCH)
    rows = [
        candle_row(base),
        candle_row(base + FIVE_MINUTES_MS),
        candle_row(base + 2 * FIVE_MINUTES_MS),
    ]
    engine, service = create_service(tmp_path, FakeSource(rows))
    try:
        result = service.download_history(
            start_time=EPOCH,
            end_time=EPOCH + timedelta(minutes=5),
            as_of=EPOCH + timedelta(minutes=15),
        )
        # Kraken-style rolling responses serve newer rows than requested: they
        # are excluded by range, never stored, and the exclusion is counted so
        # received/accepted stays exactly reconcilable.
        assert result.excluded_range_count == 1
        assert result.accepted_count == 2
        assert result.received_count == (
            result.accepted_count
            + result.rejected_count
            + result.excluded_open_count
            + result.excluded_range_count
        )
        stored = service.repository.get_candles(
            exchange="mock-exchange", symbol="ETH/USDT", timeframe="5m"
        )
        assert len(stored.candles) == 2
    finally:
        service.close()
        engine.dispose()


# ----------------------------------------------------------------------
# Layer overrides fail closed on invalid gate input
# ----------------------------------------------------------------------


def test_layer_overrides_reject_invalid_gate_input() -> None:
    with pytest.raises(ValueError, match="missing_candle_count"):
        build_context_snapshot(None, boundary=None, missing_candle_count=-1)
    with pytest.raises(ValueError, match="missing_candle_count"):
        build_context_snapshot(None, boundary=None, missing_candle_count=True)
    with pytest.raises(ValueError, match="window_status"):
        build_setup_snapshot(
            None,
            selected=None,
            ended=None,
            frame=None,
            boundary=None,
            window_status="bogus",
        )


# ----------------------------------------------------------------------
# Forward runner: per-boundary required-window gate + backfill
# ----------------------------------------------------------------------


def test_catch_up_boundary_with_a_hole_is_incomplete_and_never_planned() -> None:
    series = labelled_series()
    gapped = tuple(c for c in series if c.timestamp != series[8].timestamp)
    harness = make_harness(
        series=extend(gapped, ((21, 124), (22, 125))),
        ledger_start=QUALIFYING_BOUNDARY,
    )
    harness.advance_to(QUALIFYING_BOUNDARY + 2 * INTERVAL)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    cycles = {cycle.as_of: cycle for cycle in harness.cycles()}
    assert set(cycles) == {
        QUALIFYING_BOUNDARY,
        QUALIFYING_BOUNDARY + INTERVAL,
        QUALIFYING_BOUNDARY + 2 * INTERVAL,
    }
    for boundary in (QUALIFYING_BOUNDARY, QUALIFYING_BOUNDARY + INTERVAL):
        cycle = cycles[boundary]
        assert cycle.data_health is DataHealth.INCOMPLETE
        assert cycle.missing_candle_count == 1
        assert cycle.data_health_detail.startswith(
            "catch-up pass over an earlier closed candle;"
        )
        assert "2024-01-01T08:00:00" in cycle.data_health_detail
    newest = cycles[QUALIFYING_BOUNDARY + 2 * INTERVAL]
    assert newest.data_health is DataHealth.INCOMPLETE
    assert "catch-up" not in newest.data_health_detail
    assert harness.plans() == ()
    assert result.data_health is DataHealth.INCOMPLETE


def test_ancient_hole_neither_blocks_planning_nor_hides_from_diagnostics() -> None:
    prefix = tuple(bar(i, 100 + (i % 9)) for i in range(-120, 0) if i != -115)
    harness = make_harness(
        series=prefix + labelled_series(), ledger_start=QUALIFYING_BOUNDARY
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.status is HeartbeatStatus.PROCESSED
    (cycle,) = harness.cycles()
    assert cycle.data_health is DataHealth.CURRENT
    assert cycle.missing_candle_count == 0
    assert harness.plans() != ()
    # The ancient hole stays visible in whole-series diagnostics.
    stored = harness.service.candles.get_candles(
        exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME
    )
    assert stored.missing_candle_count == 1
    status = harness.service.status()
    assert status["market_data"]["required_window"]["complete"] is True


def test_forward_backfill_restores_a_holed_window_through_step2() -> None:
    full = labelled_series()
    gapped = tuple(c for c in full if c.timestamp != full[8].timestamp)
    source = FakeExchange()
    source.set_candles(full)
    harness = make_harness(
        series=gapped, ledger_start=QUALIFYING_BOUNDARY, source=source
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=True)
    assert result.status is HeartbeatStatus.PROCESSED
    assert result.market_data_error is None
    (cycle,) = harness.cycles()
    assert cycle.data_health is DataHealth.CURRENT
    assert cycle.missing_candle_count == 0
    assert harness.plans() != ()
    backfill = json.loads(cycle.market_data_json)["backfill"]
    assert backfill["attempted"] is True
    assert backfill["refilled"] is True
    assert backfill["inserted_count"] == 1
    assert backfill["missing_opens"] == 1


def test_forward_refresh_reports_backfill_and_range_diagnostics() -> None:
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=True)
    assert result.status is HeartbeatStatus.PROCESSED
    payload = json.loads(harness.cycles()[0].market_data_json)
    assert payload["refreshed"] is True
    assert payload["excluded_range_count"] == 0
    assert payload["backfill"]["attempted"] is False


# ----------------------------------------------------------------------
# Multi-timeframe hierarchy: scoped layers + backfill + diagnostics
# ----------------------------------------------------------------------


def test_mtf_ancient_hole_scopes_the_setup_layer_but_stays_visible(tmp_path) -> None:
    base = hierarchy_candles(aligned=True)
    prefix = tuple(web_bar(i, 100 + (i % 9)) for i in range(-120, 0) if i != -115)
    candles = dict(base)
    candles["1h"] = prefix + base["1h"]
    engine, _url = migrated_engine(tmp_path, "mtf-ancient.sqlite3")
    try:
        insert_hierarchy(engine, candles)
        service = make_mtf_service(engine)
        snapshot = service.evaluate(decision_time=DECISION_TIME)
        # The whole 1h series is gapped, but the required setup window is
        # contiguous: the layer reports evaluated, not incomplete.
        assert snapshot.setup.available is True
        assert snapshot.setup.snapshot_status == "evaluated"
        assert snapshot.setup.reason is None
        stored = CandleRepository(engine).get_candles(
            exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h"
        )
        assert stored.missing_candle_count == 1
    finally:
        engine.dispose()


def test_mtf_hole_inside_the_setup_window_marks_only_the_setup_layer(tmp_path) -> None:
    base = hierarchy_candles(aligned=True)
    one_hour = base["1h"]
    candles = dict(base)
    candles["1h"] = tuple(c for c in one_hour if c.timestamp != one_hour[-3].timestamp)
    engine, _url = migrated_engine(tmp_path, "mtf-setup.sqlite3")
    try:
        insert_hierarchy(engine, candles)
        service = make_mtf_service(engine)
        snapshot = service.evaluate(decision_time=DECISION_TIME)
        assert snapshot.setup.snapshot_status == "incomplete"
        assert snapshot.setup.reason == "the required setup-window candles are incomplete"
        assert snapshot.context.missing_candle_count == 0
        assert snapshot.context.reason is None
    finally:
        engine.dispose()


def test_mtf_hole_inside_the_context_window_marks_only_the_context_layer(
    tmp_path,
) -> None:
    base = hierarchy_candles(aligned=True)
    four_hour = base["4h"]
    candles = dict(base)
    candles["4h"] = tuple(c for c in four_hour if c.timestamp != four_hour[-2].timestamp)
    engine, _url = migrated_engine(tmp_path, "mtf-context.sqlite3")
    try:
        insert_hierarchy(engine, candles)
        service = make_mtf_service(engine)
        snapshot = service.evaluate(decision_time=DECISION_TIME)
        assert snapshot.context.missing_candle_count == 1
        assert snapshot.context.reason == "the context candle window is incomplete"
        assert snapshot.setup.snapshot_status == "evaluated"
    finally:
        engine.dispose()


def test_mtf_status_reports_a_required_window_per_timeframe(tmp_path) -> None:
    engine, _url = migrated_engine(tmp_path, "mtf-status.sqlite3")
    try:
        insert_hierarchy(engine, hierarchy_candles(aligned=True))
        service = make_mtf_service(engine)
        status = service.status(now=DECISION_TIME)
        assert set(status["market_data"]) == {"4h", "1h", "15m", "5m"}
        for timeframe, health in status["market_data"].items():
            window = health["required_window"]
            assert window["required_depth"] == 131, timeframe
            assert window["complete"] is True, timeframe
            assert window["missing_count"] == 0, timeframe
    finally:
        engine.dispose()


def test_mtf_backfill_restores_a_holed_timeframe_through_step2(tmp_path) -> None:
    base = hierarchy_candles(aligned=True)
    one_hour = base["1h"]
    candles = dict(base)
    candles["1h"] = tuple(c for c in one_hour if c.timestamp != one_hour[-3].timestamp)
    engine, url = migrated_engine(tmp_path, "mtf-backfill.sqlite3")
    source = FakeExchange()
    source.exchange_id = EXCHANGE
    source.set_candles(
        list(one_hour_candles())
        + list(four_hour_candles(aligned=True))
        + list(base["5m"])
        + list(base["15m"])
    )
    settings = make_settings(url).model_copy(update={"raw_data_dir": str(tmp_path / "raw")})
    market_data = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(tmp_path / "raw"),
        clock=lambda: DECISION_TIME,
    )
    try:
        insert_hierarchy(engine, candles)
        service = make_mtf_service(engine, market_data_service=market_data)
        result = service.run_once(now=DECISION_TIME, refresh_market_data=True)
        assert result.market_data_error is None
        payload = json.loads(result.market_data_json)
        backfill = payload["timeframes"]["1h"]["backfill"]
        assert backfill["attempted"] is True
        assert backfill["filled_count"] == 1
        assert backfill["missing_after"] == 0
        assert backfill["complete"] is True
        assert payload["timeframes"]["5m"]["backfill"]["attempted"] is False
        snapshot = service.evaluate(decision_time=DECISION_TIME)
        assert snapshot.setup.snapshot_status == "evaluated"
    finally:
        market_data.close()
        engine.dispose()


# ----------------------------------------------------------------------
# Dashboard: describe evidence, but never plan or decide over a hole
# ----------------------------------------------------------------------


def _gapped_client(tmp_path, name: str):
    engine, url = migrated_engine(tmp_path, name)
    series = qualifying_candles()
    gapped = tuple(c for c in series if c.timestamp != series[8].timestamp)
    insert_candles(engine, gapped)
    settings = make_settings(url)
    return engine, make_client(engine, settings, clock=qualified_clock())


def test_gapped_dashboard_withholds_the_plan_and_reports_the_window(tmp_path) -> None:
    engine, client = _gapped_client(tmp_path, "web-gapped.sqlite3")
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["plan"] is None
        assert payload["planning"]["state"] is None
        (reason,) = payload["planning"]["reasons"]
        assert reason.startswith("planning withheld: the required 1h window")
        assert "2024-01-01T08:00:00" in reason
        assert payload["journal"]["can_decide"] is False
        window = payload["market"]["required_window"]
        assert window["complete"] is False
        assert window["missing_opens"] == ["2024-01-01T08:00:00Z"]
        assert window["required_depth"] == 131
    finally:
        engine.dispose()


def _qualified_gapped_client(tmp_path):
    # The qualifying geometry repeated: a setup seeded after the early gap
    # still reaches QUALIFIED at the later close, while the gap stays inside
    # the required window — the exact case the decide gate must refuse.
    rows = list(QUALIFYING_ROWS) + list(QUALIFYING_ROWS)
    series = tuple(web_bar(i, price) for i, price in enumerate(rows))
    gapped = tuple(c for c in series if c.timestamp != series[8].timestamp)
    engine, url = migrated_engine(tmp_path, "web-qualified-gapped.sqlite3")
    insert_candles(engine, gapped)
    settings = make_settings(url)
    clock = EPOCH + 42 * timedelta(hours=1)
    return engine, make_client(engine, settings, clock=clock)


def test_qualified_dashboard_over_a_hole_shows_no_plan_and_no_decision(
    tmp_path,
) -> None:
    engine, client = _qualified_gapped_client(tmp_path)
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["qualification"]["selected_setup_id"] is not None
        assert payload["plan"] is None
        assert payload["planning"]["state"] is None
        assert payload["journal"]["can_decide"] is False
        assert payload["journal"]["disabled_reason"].startswith("planning withheld:")
    finally:
        engine.dispose()


def test_decide_over_a_hole_is_refused_with_the_gate_reason(tmp_path) -> None:
    engine, client = _qualified_gapped_client(tmp_path)
    try:
        dashboard = client.get("/api/dashboard").json()
        body = {
            "decision": "ACCEPTED",
            "as_of": dashboard["meta"]["as_of"],
            "setup_id": dashboard["qualification"]["selected_setup_id"],
        }
        response = client.post("/api/dashboard/decisions", json=body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "proposal_not_decidable"
        assert "planning withheld" in response.json()["error"]["message"]
    finally:
        engine.dispose()
