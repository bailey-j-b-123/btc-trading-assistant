"""Step 13 service-level multi-timeframe tests (real engines, temp databases).

These tests exercise the full stack over the deterministic fixtures: multi-
timeframe public acquisition through ONE managed client, the end-to-end
hierarchy evaluation against the real Step 3/4/5 engines, the immutable
append-only ledger (idempotency, restart safety, version isolation, old-record
readability), no-lookahead replay, the dashboard ladder payload, and the
hierarchy-aware grounded explanation.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from trading_assistant.market_data.raw_storage import RawResponseStore
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.multi_timeframe.hierarchy import (
    TimeframeHierarchy,
    TimeframeRole,
    TimeframeStep,
    default_hierarchy,
)
from trading_assistant.multi_timeframe.replay import replay_hierarchy
from trading_assistant.multi_timeframe.service import (
    MultiTimeframeService,
    RunnerStatus,
)

from forward_fixtures import FakeExchange
from multi_timeframe_fixtures import (
    DECISION_TIME,
    EPOCH,
    EXCHANGE,
    SCENARIO_DECISION_TIME,
    SYMBOL,
    four_hour_candles,
    hierarchy_candles,
    insert_hierarchy,
    one_hour_candles,
    scenario_candles,
)
from web_fixtures import make_settings, migrated_engine

HOUR = timedelta(hours=1)
BAND_LOW = Decimal("117")
BAND_HIGH = Decimal("117")


def make_service(engine, *, clock=None, hierarchy=None, market_data_service=None, settings=None):
    resolved_settings = settings
    if resolved_settings is None:
        resolved_settings = make_settings(str(engine.url))
    return MultiTimeframeService(
        engine,
        settings=resolved_settings,
        clock=clock if clock is not None else (lambda: DECISION_TIME),
        hierarchy=hierarchy if hierarchy is not None else default_hierarchy(),
        market_data_service=market_data_service,
    )


@pytest.fixture
def aligned_engine(tmp_path):
    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    insert_hierarchy(engine, hierarchy_candles(aligned=True))
    return engine, url


@pytest.fixture
def counter_engine(tmp_path):
    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    insert_hierarchy(engine, hierarchy_candles(aligned=False))
    return engine, url


# ---------------------------------------------------------------------------
# Multi-timeframe acquisition (one managed client, closed candles only)
# ---------------------------------------------------------------------------


def _acquisition_settings(url: str, tmp_path: Path):
    settings = make_settings(url)
    return settings.model_copy(update={"raw_data_dir": str(tmp_path / "raw")})


def test_multi_timeframe_acquisition_uses_one_client_for_all_timeframes(tmp_path):
    """4H/1H/15M/5M are fetched through ONE service; storage is idempotent."""

    engine, url = migrated_engine(tmp_path, "acquire.sqlite3")
    source = FakeExchange()
    source.exchange_id = "binance"
    source.set_candles(
        list(one_hour_candles())
        + list(four_hour_candles(aligned=True))
        + [
            c
            for timeframe, factor in (("5m", 12), ("15m", 4))
            for c in hierarchy_candles()[timeframe]
        ]
    )
    settings = _acquisition_settings(url, tmp_path)
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: DECISION_TIME,
    )
    try:
        first = service.download_history_all(
            start_time=EPOCH,
            timeframes=("4h", "1h", "15m", "5m"),
            as_of=DECISION_TIME,
        )
        assert set(first) == {"4h", "1h", "15m", "5m"}
        for timeframe, update in first.items():
            assert update.inserted_count > 0, timeframe
        # ONE client served every timeframe.
        assert service.source is source
        assert source.calls >= 4

        second = service.download_history_all(
            start_time=EPOCH,
            timeframes=("4h", "1h", "15m", "5m"),
            as_of=DECISION_TIME,
        )
        for timeframe, update in second.items():
            assert update.inserted_count == 0, timeframe
            assert update.already_present_count > 0, timeframe
        # An incremental update finds nothing new either (the source has no
        # candles beyond the stored window).
        third = service.update_history_all(
            timeframes=("4h", "1h", "15m", "5m"), as_of=DECISION_TIME
        )
        for timeframe, update in third.items():
            assert update.inserted_count == 0, timeframe
    finally:
        service.close()

    from trading_assistant.market_data.repository import CandleRepository

    repository = CandleRepository(engine)
    for timeframe in ("4h", "1h", "15m", "5m"):
        result = repository.get_candles(
            exchange="binance", symbol=SYMBOL, timeframe=timeframe
        )
        assert result.candles, timeframe
        assert result.missing_candle_count == 0, timeframe


def test_download_history_all_backfills_all_timeframes_through_one_service(tmp_path):
    engine, url = migrated_engine(tmp_path, "backfill.sqlite3")
    source = FakeExchange()
    source.exchange_id = "binance"
    source.set_candles(list(one_hour_candles()) + list(four_hour_candles(aligned=True)))
    settings = _acquisition_settings(url, tmp_path)
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
        clock=lambda: DECISION_TIME,
    )
    try:
        results = service.download_history_all(
            start_time=EPOCH,
            timeframes=("1h", "4h"),
            as_of=DECISION_TIME,
        )
        assert set(results) == {"1h", "4h"}
        assert all(update.inserted_count > 0 for update in results.values())
    finally:
        service.close()


def test_update_history_all_rejects_duplicate_and_unsupported_timeframes(tmp_path):
    engine, url = migrated_engine(tmp_path, "reject.sqlite3")
    source = FakeExchange()
    source.exchange_id = "binance"
    settings = _acquisition_settings(url, tmp_path)
    service = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
    )
    try:
        with pytest.raises(ValueError, match="duplicate timeframe"):
            service.update_history_all(timeframes=("1h", "1h"), as_of=DECISION_TIME)
        with pytest.raises(ValueError, match="supported_timeframes"):
            service.update_history_all(timeframes=("2h", "1h"), as_of=DECISION_TIME)
        with pytest.raises(ValueError, match="at least one"):
            service.update_history_all(timeframes=(), as_of=DECISION_TIME)
    finally:
        service.close()


def test_closed_candles_through_returns_only_closed_candles(tmp_path):
    engine, url = migrated_engine(tmp_path, "closed.sqlite3")
    insert_hierarchy(engine, hierarchy_candles(aligned=True))
    settings = _acquisition_settings(url, tmp_path)
    service = MarketDataService(
        engine,
        FakeExchange(),
        settings=settings,
        raw_store=RawResponseStore(settings.raw_data_dir),
    )
    try:
        candles = service.closed_candles_through(timeframe="5m", as_of=DECISION_TIME)
        assert candles
        assert all(candle.timestamp + timedelta(minutes=5) <= DECISION_TIME for candle in candles)
        assert candles[-1].timestamp == DECISION_TIME - timedelta(minutes=5)
    finally:
        service.close()


# ---------------------------------------------------------------------------
# End-to-end evaluation against the real engines
# ---------------------------------------------------------------------------


def test_service_evaluates_the_complete_aligned_hierarchy(aligned_engine):
    engine, url = aligned_engine
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)
    assert snapshot.status == "evaluated"
    assert snapshot.context.regime.value == "bullish_structure"
    assert snapshot.context.available is True
    assert snapshot.setup.setup_state == "QUALIFIED"
    assert snapshot.setup.family == "breakout_retest_continuation"
    assert snapshot.setup.direction == "bullish"
    assert (snapshot.setup.reference_band_low, snapshot.setup.reference_band_high) == (
        BAND_LOW,
        BAND_HIGH,
    )
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.execution.state.value == "waiting"
    assert snapshot.alignment.value == "aligned"
    assert snapshot.counter_trend is False
    assert snapshot.decision.value == "awaiting_execution"
    # The decision boundary exposes the latest closed candle per timeframe.
    assert snapshot.boundary.boundary_for("4h").candle_open_time == EPOCH + 16 * HOUR
    assert snapshot.boundary.boundary_for("1h").candle_open_time == EPOCH + 20 * HOUR
    assert snapshot.boundary.boundary_for("15m").candle_open_time == EPOCH + 20 * HOUR + 45 * timedelta(minutes=1)
    assert snapshot.boundary.boundary_for("5m").candle_open_time == EPOCH + 20 * HOUR + 55 * timedelta(minutes=1)
    assert snapshot.boundary.all_candles_known


def test_service_counter_trend_fixture_flags_counter_trend(counter_engine):
    engine, _url = counter_engine
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)
    assert snapshot.context.regime.value == "bearish_structure"
    assert snapshot.alignment.value == "counter_trend"
    assert snapshot.counter_trend is True
    # The 4H context is never overridden by the bullish 1H setup.
    assert snapshot.setup.direction == "bullish"
    # An ordinary counter-trend setup stays below PLANNABLE.
    assert snapshot.decision.value == "awaiting_confirmation"
    assert "counter_trend_blocked_below_plannable" in snapshot.reasons
    assert any(
        "counter-trend" in item and "PLANNABLE" in item for item in snapshot.waiting_for
    )


def test_service_counter_trend_stays_blocked_even_when_lower_layers_complete(tmp_path):
    """Bearish 4H + bullish 1H + confirming 15m + triggered 5m: still not PLANNABLE.

    The lower layers complete exactly as they would for an aligned setup; the
    4H directional structure is what keeps the hierarchy below PLANNABLE, and
    the block stays visible in the snapshot, the ladder payload and the
    deterministic explanation.
    """

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=False, scenario=scenario))
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)

    # The lower layers genuinely completed.
    assert snapshot.setup.setup_state == "QUALIFIED"
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.execution.state.value == "triggered"
    # ... and the hierarchy still refuses to complete.
    assert snapshot.alignment.value == "counter_trend"
    assert snapshot.counter_trend is True
    assert snapshot.decision.value == "awaiting_confirmation"
    assert snapshot.decision.value != "plannable"
    assert "counter_trend_blocked_below_plannable" in snapshot.reasons
    assert any(
        "failed/transitioned" in item for item in snapshot.waiting_for
    )

    # The block is visible in the ladder payload and the explanation.
    from trading_assistant.multi_timeframe.explanation import explain_hierarchy
    from trading_assistant.multi_timeframe.ladder import ladder_payload

    payload = ladder_payload(snapshot)
    assert payload["counter_trend"] is True
    assert payload["overall"] == "COUNTER-TREND SETUP — BLOCKED BELOW PLANNABLE"
    explanation = explain_hierarchy(snapshot)
    assert any("blocked below PLANNABLE" in s for s in explanation["sentences"])


def test_service_scenario_plannable_when_execution_triggers(tmp_path):
    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.setup.setup_state == "QUALIFIED"
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.confirmation.candle_count == 28
    assert snapshot.execution.state.value == "triggered"
    assert snapshot.execution.candle_count == 84
    assert snapshot.execution.armed_at is not None
    assert snapshot.execution.trigger_at is not None
    assert snapshot.alignment.value == "aligned"
    assert snapshot.decision.value == "plannable"
    assert snapshot.reasons == ("hierarchy_complete",)


def _plannable_scenario():
    return scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )


def _insert_with_series_gaps(
    engine, *, aligned=True, scenario=None, drop_15m_open=None, drop_5m_open=None,
    truncate_15m_after=None, truncate_5m_after=None,
):
    """Insert the fixture with a controlled 15m/5m data-quality gap."""

    series = dict(hierarchy_candles(aligned=aligned, scenario=scenario))
    if drop_15m_open is not None:
        series["15m"] = tuple(
            c for c in series["15m"] if c.timestamp != drop_15m_open
        )
    if truncate_15m_after is not None:
        series["15m"] = tuple(
            c for c in series["15m"] if c.timestamp <= truncate_15m_after
        )
    if drop_5m_open is not None:
        series["5m"] = tuple(
            c for c in series["5m"] if c.timestamp != drop_5m_open
        )
    if truncate_5m_after is not None:
        series["5m"] = tuple(
            c for c in series["5m"] if c.timestamp <= truncate_5m_after
        )
    insert_hierarchy(engine, series)
    return series


def test_service_missing_15m_candle_blocks_plannable(tmp_path):
    """CONFIRMING 15m + TRIGGERED 5m + a missing 15m candle: not PLANNABLE."""

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    _insert_with_series_gaps(
        engine,
        scenario=_plannable_scenario(),
        drop_15m_open=EPOCH + 18 * HOUR,
    )
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    # The lower layers genuinely completed on the available candles...
    assert snapshot.setup.setup_state == "QUALIFIED"
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.confirmation.missing_candle_count == 1
    assert snapshot.execution.state.value == "triggered"
    # ...but the incomplete required data blocks completion.
    assert snapshot.decision.value == "awaiting_confirmation"
    assert snapshot.decision.value != "plannable"
    assert snapshot.status == "incomplete"
    assert "confirmation_window_incomplete" in snapshot.reasons
    assert any(
        "missing closed 15m candle(s)" in item for item in snapshot.waiting_for
    )


def test_service_missing_5m_candle_blocks_plannable(tmp_path):
    """CONFIRMING 15m + TRIGGERED 5m + a missing 5m candle: not PLANNABLE."""

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    _insert_with_series_gaps(
        engine,
        scenario=_plannable_scenario(),
        drop_5m_open=EPOCH + 18 * HOUR + timedelta(minutes=5),
    )
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.confirmation.missing_candle_count == 0
    assert snapshot.execution.state.value == "triggered"
    assert snapshot.execution.missing_candle_count == 1
    assert snapshot.decision.value == "awaiting_execution"
    assert snapshot.decision.value != "plannable"
    assert snapshot.status == "incomplete"
    assert "execution_window_incomplete" in snapshot.reasons
    assert any(
        "missing closed 5m candle(s)" in item for item in snapshot.waiting_for
    )


def test_service_stale_15m_data_blocks_plannable(tmp_path):
    """Stale 15m data (expected latest closed candle not stored): not PLANNABLE."""

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    _insert_with_series_gaps(
        engine,
        scenario=_plannable_scenario(),
        truncate_15m_after=EPOCH + 21 * HOUR + timedelta(minutes=15),
    )
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.confirmation.stale is True
    assert snapshot.execution.state.value == "triggered"
    assert snapshot.decision.value == "awaiting_confirmation"
    assert snapshot.decision.value != "plannable"
    assert snapshot.status == "incomplete"
    assert "confirmation_data_stale" in snapshot.reasons
    assert any(
        "latest closed 15m confirmation candle" in item
        for item in snapshot.waiting_for
    )


def test_service_stale_5m_data_blocks_plannable(tmp_path):
    """Stale 5m data (expected latest closed candle not stored): not PLANNABLE."""

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    _insert_with_series_gaps(
        engine,
        scenario=_plannable_scenario(),
        truncate_5m_after=EPOCH + 21 * HOUR + timedelta(minutes=50),
    )
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.confirmation.stale is False
    assert snapshot.execution.state.value == "triggered"
    assert snapshot.execution.stale is True
    assert snapshot.decision.value == "awaiting_execution"
    assert snapshot.decision.value != "plannable"
    assert snapshot.status == "incomplete"
    assert "execution_data_stale" in snapshot.reasons
    assert any(
        "latest closed 5m execution candle" in item for item in snapshot.waiting_for
    )


def test_service_recovers_to_plannable_once_data_is_complete(tmp_path):
    """Blocked on incomplete data, then PLANNABLE once the candle exists.

    The blocked evaluation is recorded as its own immutable row; the recovered
    evaluation is a new row; the old row is never rewritten.
    """

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = _plannable_scenario()
    dropped_open = EPOCH + 18 * HOUR
    _insert_with_series_gaps(
        engine, scenario=scenario, drop_15m_open=dropped_open
    )
    service = make_service(engine)

    blocked = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert blocked.decision.value == "awaiting_confirmation"
    assert blocked.status == "incomplete"
    blocked_observation, created_blocked = service.record(
        blocked, recorded_at=SCENARIO_DECISION_TIME
    )
    assert created_blocked is True

    # The missing closed candle becomes genuinely available.
    from trading_assistant.market_data.repository import CandleRepository

    missing_candle = next(
        c for c in hierarchy_candles(scenario=scenario)["15m"] if c.timestamp == dropped_open
    )
    CandleRepository(engine).insert_unchanged_or_new((missing_candle,))

    recovered = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert recovered.decision.value == "plannable"
    assert recovered.status == "evaluated"
    assert recovered.confirmation.missing_candle_count == 0
    recovered_observation, created_recovered = service.record(
        recovered, recorded_at=SCENARIO_DECISION_TIME
    )
    assert created_recovered is True

    observations = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
    assert len(observations) == 2
    by_id = {row.observation_id: row for row in observations}
    assert by_id[blocked_observation.observation_id].status == "incomplete"
    assert "confirmation_window_incomplete" in json.loads(
        by_id[blocked_observation.observation_id].reasons_json
    )
    assert by_id[recovered_observation.observation_id].status == "evaluated"
    assert by_id[recovered_observation.observation_id].decision == "plannable"
    # The blocked record is byte-for-byte what it was.
    assert by_id[blocked_observation.observation_id].snapshot_json == (
        blocked_observation.snapshot_json
    )


def test_service_data_quality_block_visible_in_ladder_and_explanation(tmp_path):
    from trading_assistant.multi_timeframe.explanation import explain_hierarchy
    from trading_assistant.multi_timeframe.ladder import ladder_payload

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    _insert_with_series_gaps(
        engine,
        scenario=_plannable_scenario(),
        drop_15m_open=EPOCH + 18 * HOUR,
    )
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)

    payload = ladder_payload(snapshot)
    assert payload["overall"] == "WAITING FOR COMPLETE MARKET DATA"
    assert payload["status"] == "incomplete"
    assert payload["decision"] == "awaiting_confirmation"
    assert any(
        "missing closed 15m candle(s)" in item for item in payload["waiting_for"]
    )

    explanation = explain_hierarchy(snapshot)
    assert any(
        "incomplete" in s and "complete/current data" in s
        for s in explanation["sentences"]
    )
    assert explanation["waiting_for_text"].startswith("Waiting for:")


def test_service_scenario_execution_invalidated(tmp_path):
    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("121", "120", "119", "118"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "116.5", "116.8", "117.1", "117.4", "117.6",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.execution.state.value == "invalidated"
    assert snapshot.decision.value == "invalidated"


def test_service_scenario_confirmation_contradicting(tmp_path):
    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("116", "115", "114", "113"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117.2", "117.1", "117.05", "117.02", "117.01",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    assert snapshot.confirmation.state.value == "contradicting"
    assert snapshot.execution.state.value == "not_armed"
    assert snapshot.decision.value == "awaiting_confirmation"
    assert snapshot.waiting_for


def test_service_never_uses_candles_that_close_after_the_decision(tmp_path):
    """Scenario candles stored, decision at 21:00: they must be ignored."""

    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)
    assert snapshot.confirmation.candle_count == 24
    assert snapshot.execution.candle_count == 72
    assert snapshot.confirmation.state.value == "confirming"
    assert snapshot.execution.state.value == "waiting"
    assert snapshot.decision.value == "awaiting_execution"


def test_service_watch_and_no_setup_boundaries(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    watch = service.evaluate(decision_time=EPOCH + 10 * HOUR)
    assert watch.setup.setup_state == "WATCH"
    assert watch.decision.value == "watch"
    early = service.evaluate(decision_time=EPOCH + 2 * HOUR)
    assert early.setup.setup_id is None
    assert early.decision.value == "no_setup"
    assert early.confirmation.state.value == "not_applicable"
    assert early.execution.state.value == "not_armed"


def test_service_rejects_hierarchy_timeframes_outside_supported_settings(tmp_path):
    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    settings = make_settings(url, supported_timeframes=("15m", "1h", "4h", "1d"))
    with pytest.raises(ValueError, match="supported_timeframes"):
        make_service(engine, settings=settings)


# ---------------------------------------------------------------------------
# Immutable ledger: idempotency, restart safety, version isolation, readability
# ---------------------------------------------------------------------------


def test_service_records_immutable_observation_and_blocks_mutation(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)
    observation, created = service.record(snapshot, recorded_at=DECISION_TIME)
    assert created is True
    assert observation.decision == "awaiting_execution"
    assert observation.rules_version == "multi-timeframe-ledger-v1"

    # SQLite immutability triggers block UPDATE and DELETE of the record.
    with engine.begin() as connection:
        with pytest.raises(Exception, match="append-only"):
            connection.execute(
                text("UPDATE forward_hierarchy_observations SET decision = 'plannable'")
            )
        with pytest.raises(Exception, match="append-only"):
            connection.execute(text("DELETE FROM forward_hierarchy_observations"))

    stored = service.ledger.observation_at(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        decision_time=DECISION_TIME,
        hierarchy_fingerprint=service.hierarchy.fingerprint(),
    )
    assert stored is not None
    assert stored.observation_id == observation.observation_id
    assert json.loads(stored.snapshot_json)["decision"] == "awaiting_execution"


def test_service_record_is_idempotent_across_restarts(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)
    first, created_first = service.record(snapshot, recorded_at=DECISION_TIME)
    assert created_first is True
    # A "restart" (a brand-new service over the same database) at the same
    # boundary records nothing new.
    restarted = make_service(engine)
    again, created_again = restarted.record(snapshot, recorded_at=DECISION_TIME + 5 * HOUR)
    assert created_again is False
    assert again.observation_id == first.observation_id
    assert restarted.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL)["observations"] == 1


def test_service_version_isolation_between_hierarchies(aligned_engine):
    engine, _url = aligned_engine
    default_service = make_service(engine)
    snapshot_default = default_service.evaluate(decision_time=DECISION_TIME)
    default_service.record(snapshot_default, recorded_at=DECISION_TIME)

    other_hierarchy = TimeframeHierarchy(
        steps=(
            TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1d"),
            TimeframeStep(role=TimeframeRole.SETUP, timeframe="4h"),
            TimeframeStep(role=TimeframeRole.CONFIRMATION, timeframe="1h"),
            TimeframeStep(role=TimeframeRole.EXECUTION, timeframe="15m"),
        )
    )
    other_service = make_service(
        engine,
        hierarchy=other_hierarchy,
        settings=make_settings(
            str(engine.url), supported_timeframes=("5m", "15m", "1h", "4h", "1d")
        ),
    )
    snapshot_other = other_service.evaluate(decision_time=DECISION_TIME)
    observation_other, created_other = other_service.record(
        snapshot_other, recorded_at=DECISION_TIME
    )
    assert created_other is True
    assert observation_other.observation_id != snapshot_default.identity()
    assert observation_other.hierarchy_fingerprint != snapshot_default.hierarchy_fingerprint

    observations = service_all(engine)
    assert len(observations) == 2
    fingerprints = {row.hierarchy_fingerprint for row in observations}
    assert fingerprints == {
        snapshot_default.hierarchy_fingerprint,
        snapshot_other.hierarchy_fingerprint,
    }


def service_all(engine):
    return MultiTimeframeService(engine, settings=make_settings(str(engine.url))).ledger.observations(
        exchange=EXCHANGE, symbol=SYMBOL
    )


def test_service_old_records_remain_readable_after_new_boundaries(tmp_path):
    engine, _url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    scenario = scenario_candles(
        band_low=BAND_LOW,
        band_high=BAND_HIGH,
        closes_15m=("120", "119", "118", "117.5"),
        closes_5m=(
            "123", "122", "121", "120", "119", "118",
            "117.5", "117", "117.2", "117.5", "117.8", "118",
        ),
    )
    insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
    service = make_service(engine)

    first_snapshot = service.evaluate(decision_time=DECISION_TIME)
    first_observation, _created = service.record(first_snapshot, recorded_at=DECISION_TIME)
    second_snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)
    second_observation, _created = service.record(
        second_snapshot, recorded_at=SCENARIO_DECISION_TIME
    )

    observations = service.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
    assert len(observations) == 2
    by_id = {row.observation_id: row for row in observations}
    old = by_id[first_observation.observation_id]
    # The old record is byte-for-byte what it was: history is never rewritten.
    assert old.snapshot_json == first_observation.snapshot_json
    assert json.loads(old.snapshot_json)["decision"] == "awaiting_execution"
    assert json.loads(old.snapshot_json)["execution"]["state"] == "waiting"
    new = by_id[second_observation.observation_id]
    assert json.loads(new.snapshot_json)["decision"] == "plannable"
    assert json.loads(new.snapshot_json)["execution"]["state"] == "triggered"
    # The newest-first read order is stable and complete.
    newest = service.ledger.observations(
        exchange=EXCHANGE, symbol=SYMBOL, newest_first=True
    )
    assert newest[0].observation_id == second_observation.observation_id


# ---------------------------------------------------------------------------
# Historical replay: identical results without lookahead
# ---------------------------------------------------------------------------


def test_replay_hierarchy_is_identical_with_and_without_future_candles(tmp_path):
    def build(with_scenario: bool):
        engine, _url = migrated_engine(tmp_path, f"replay-{with_scenario}.sqlite3")
        scenario = (
            scenario_candles(
                band_low=BAND_LOW,
                band_high=BAND_HIGH,
                closes_15m=("120", "119", "118", "117.5"),
                closes_5m=(
                    "123", "122", "121", "120", "119", "118",
                    "117.5", "117", "117.2", "117.5", "117.8", "118",
                ),
            )
            if with_scenario
            else None
        )
        insert_hierarchy(engine, hierarchy_candles(aligned=True, scenario=scenario))
        return make_service(engine)

    with_future = build(True)
    without_future = build(False)
    start = EPOCH + 18 * HOUR
    end = EPOCH + 21 * HOUR
    replayed_with = replay_hierarchy(with_future, start=start, end=end)
    replayed_without = replay_hierarchy(without_future, start=start, end=end)
    assert len(replayed_with) == len(replayed_without) == 37  # 3h of 5M boundaries
    for left, right in zip(replayed_with, replayed_without):
        assert left.identity() == right.identity()
        assert left.decision == right.decision
    # And the recorded ledger is identical too.
    replay_hierarchy(with_future, start=start, end=end, record=True, recorded_at=DECISION_TIME)
    replay_hierarchy(without_future, start=start, end=end, record=True, recorded_at=DECISION_TIME)
    ids_with = [
        row.observation_id
        for row in with_future.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
    ]
    ids_without = [
        row.observation_id
        for row in without_future.ledger.observations(exchange=EXCHANGE, symbol=SYMBOL)
    ]
    assert ids_with == ids_without


# ---------------------------------------------------------------------------
# Runner: pending boundaries, idempotency, market-data error honesty
# ---------------------------------------------------------------------------


def test_run_once_processes_the_pending_boundary_and_is_idempotent(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    first = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert first.status is RunnerStatus.PROCESSED
    assert first.processed_boundaries == (DECISION_TIME,)
    assert first.observations_recorded == 1
    assert first.observations_created == 1
    assert first.latest_decision_time == DECISION_TIME

    # A repeated pass at the same boundary creates nothing new.
    second = service.run_once(now=DECISION_TIME, refresh_market_data=False)
    assert second.status is RunnerStatus.IDLE
    assert second.observations_recorded == 0
    assert service.ledger.counts(exchange=EXCHANGE, symbol=SYMBOL)["observations"] == 1


def test_run_once_reports_missing_market_data_source_honestly(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    result = service.run_once(now=DECISION_TIME, refresh_market_data=True)
    assert result.market_data_error_type == "HierarchyNotConfigured"
    assert result.market_data_error is not None
    # Stored closed candles are still evaluated; the failure is reported.
    assert result.status is RunnerStatus.PROCESSED
    payload = json.loads(result.market_data_json)
    assert payload["refreshed"] is False


def test_run_once_refreshes_every_timeframe_through_one_service(tmp_path):
    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    source = FakeExchange()
    source.exchange_id = EXCHANGE
    source.set_candles(
        list(one_hour_candles())
        + list(four_hour_candles(aligned=True))
        + list(hierarchy_candles()["5m"])
        + list(hierarchy_candles()["15m"])
    )
    settings = make_settings(url)
    market_data = MarketDataService(
        engine,
        source,
        settings=settings,
        raw_store=RawResponseStore(tmp_path / "raw"),
        clock=lambda: DECISION_TIME,
    )
    try:
        service = make_service(engine, market_data_service=market_data)
        result = service.run_once(now=DECISION_TIME, refresh_market_data=True)
        assert result.market_data_error is None
        payload = json.loads(result.market_data_json)
        assert payload["refreshed"] is True
        assert set(payload["timeframes"]) == {"4h", "1h", "15m", "5m"}
        assert result.status is RunnerStatus.PROCESSED
    finally:
        market_data.close()


def test_status_reports_data_health_counts_and_pending_boundaries(aligned_engine):
    engine, _url = aligned_engine
    service = make_service(engine)
    service.run_once(now=DECISION_TIME, refresh_market_data=False)
    status = service.status(now=DECISION_TIME)
    assert status["sample"]["observations"] == 1
    assert status["sample"]["pending_boundaries"] == 0
    assert set(status["market_data"]) == {"4h", "1h", "15m", "5m"}
    for timeframe, health in status["market_data"].items():
        assert health["data_health"] == "CURRENT", timeframe
    assert status["latest_observation"]["decision"] == "awaiting_execution"
    assert status["limitations"]


# ---------------------------------------------------------------------------
# Dashboard ladder payload (via the real web app)
# ---------------------------------------------------------------------------


def test_dashboard_ladder_payload_via_api(aligned_engine):
    from fastapi.testclient import TestClient

    from trading_assistant.web import create_app

    engine, url = aligned_engine
    settings = make_settings(url)
    service = make_service(engine)
    service.run_once(now=DECISION_TIME, refresh_market_data=False)
    app = create_app(
        engine=engine, settings=settings, clock=lambda: DECISION_TIME
    )
    client = TestClient(app)
    try:
        response = client.get("/api/dashboard")
        assert response.status_code == 200
        payload = response.json()
        assert "multi_timeframe" in payload
        ladder = payload["multi_timeframe"]
        assert ladder["available"] is True
        assert ladder["decision"] == "awaiting_execution"
        assert ladder["overall"] == "WAITING FOR ENTRY TIMING"
        assert ladder["alignment"] == "aligned"
        assert ladder["counter_trend"] is False
        assert [row["label"] for row in ladder["ladder"]] == [
            "4H CONTEXT",
            "1H SETUP",
            "15M CONFIRMATION",
            "5M EXECUTION",
        ]
        for row in ladder["ladder"]:
            # Plain English only: no internal enum names on the ladder.
            assert row["state"] not in (
                "awaiting_execution",
                "ALIGNED",
                "CONFIRMING",
                "WAITING",
                "bullish_structure",
            )
            assert row["boundary_open"]
            assert row["boundary_close"]
        assert ladder["latest_recorded"]["decision"] == "awaiting_execution"
        assert ladder["snapshot"]["decision"] == "awaiting_execution"
        assert ladder["limitations"]
        # The explanation is hierarchy-aware.
        explanation = payload["explanation"]
        assert explanation["sections"]
        text = "\n".join(section["text"] for section in explanation["sections"])
        assert "Multi-timeframe hierarchy" in text
        assert "awaiting_execution" in text  # raw decision appears in the explanation
    finally:
        client.close()


def test_dashboard_ladder_unavailable_when_timeframes_are_unsupported(tmp_path):
    from fastapi.testclient import TestClient

    from trading_assistant.web import create_app

    engine, url = migrated_engine(tmp_path, "hierarchy.sqlite3")
    insert_hierarchy(engine, hierarchy_candles(aligned=True))
    settings = make_settings(url, supported_timeframes=("15m", "1h", "4h", "1d"))
    app = create_app(engine=engine, settings=settings, clock=lambda: DECISION_TIME)
    client = TestClient(app)
    try:
        response = client.get("/api/dashboard")
        assert response.status_code == 200
        ladder = response.json()["multi_timeframe"]
        assert ladder["available"] is False
        assert ladder["error"]["type"] == "HierarchyNotConfigured"
        assert ladder["limitations"]
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Grounded, hierarchy-aware explanation
# ---------------------------------------------------------------------------


def test_explanation_context_and_renderer_include_the_hierarchy(aligned_engine):
    from trading_assistant.ai_explanation import ExplanationService
    from trading_assistant.setup_qualification.service import QualificationService
    from trading_assistant.setup_qualification.engine import enumerate_qualifications
    from trading_assistant.setup_qualification.parameters import QualificationParameters
    from trading_assistant.setup_qualification.service import bounded_replay_start

    engine, url = aligned_engine
    service = make_service(engine)
    snapshot = service.evaluate(decision_time=DECISION_TIME)

    qualification = QualificationService(engine)
    frames = qualification.build_frames(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        as_of=DECISION_TIME,
        parameters=QualificationParameters(),
        start_at=bounded_replay_start(
            as_of=DECISION_TIME,
            timeframe="1h",
            parameters=QualificationParameters(),
        ),
    )
    qualification_snapshot = enumerate_qualifications(frames, as_of=DECISION_TIME)[-1]

    explanations = ExplanationService()
    context = explanations.build_context(
        snapshot=qualification_snapshot,
        frame=frames[-1],
        hierarchy=snapshot,
    )
    assert context.payload["multi_timeframe"] is not None
    assert context.payload["multi_timeframe"]["decision"] == "awaiting_execution"

    result = explanations.explain(context)
    narrative = "\n".join(section.text for section in result.sections)
    assert "Multi-timeframe hierarchy" in narrative
    assert "4h context" in narrative
    assert "15m confirmation" in narrative
    assert "5m execution" in narrative
    assert "Waiting for:" in narrative
    assert "Invalidated if:" in narrative

    # Without a hierarchy snapshot the explanation is unchanged in shape.
    plain = explanations.build_context(
        snapshot=qualification_snapshot, frame=frames[-1]
    )
    assert plain.payload["multi_timeframe"] is None
    plain_result = explanations.explain(plain)
    plain_narrative = "\n".join(section.text for section in plain_result.sections)
    assert "Multi-timeframe hierarchy" not in plain_narrative


def test_explanation_context_rejects_a_future_dated_hierarchy(aligned_engine):
    from trading_assistant.ai_explanation import ExplanationService
    from trading_assistant.ai_explanation.errors import ContextBuildError
    from trading_assistant.setup_qualification.service import QualificationService
    from trading_assistant.setup_qualification.engine import enumerate_qualifications
    from trading_assistant.setup_qualification.parameters import QualificationParameters
    from trading_assistant.setup_qualification.service import bounded_replay_start

    engine, _url = aligned_engine
    service = make_service(engine)
    future_snapshot = service.evaluate(decision_time=SCENARIO_DECISION_TIME)

    qualification = QualificationService(engine)
    frames = qualification.build_frames(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        as_of=DECISION_TIME,
        parameters=QualificationParameters(),
        start_at=bounded_replay_start(
            as_of=DECISION_TIME,
            timeframe="1h",
            parameters=QualificationParameters(),
        ),
    )
    qualification_snapshot = enumerate_qualifications(frames, as_of=DECISION_TIME)[-1]

    explanations = ExplanationService()
    with pytest.raises(ContextBuildError, match="future data"):
        explanations.build_context(
            snapshot=qualification_snapshot,
            frame=frames[-1],
            hierarchy=future_snapshot,
        )
