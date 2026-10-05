"""Step 3 service tests: read-only snapshots over a temporary SQLite database.

All exchange data is synthetic and inserted directly through the Step 2
repository; no test contacts an exchange or touches project data. Databases and
raw directories are created per test under pytest's ``tmp_path``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from market_structure_fixtures import (
    EPOCH,
    INTERVAL,
    TIMEFRAME,
    flat_candles,
    zigzag_candles,
    zigzag_candles_at,
)
from sqlalchemy import inspect, text

from trading_assistant.config import Settings
from trading_assistant.database import create_database_engine
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_structure import (
    INCOMPLETE_DATA,
    INSUFFICIENT_CANDLES,
    NO_STORED_CANDLES,
    MarketStructureParameters,
    RangeParameters,
    TrendDirection,
    create_market_structure_service,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RANGE_PIVOTS = ("100", "104", "100", "104", "100", "104", "100", "104", "100", "104")
FOUR_HOURS = timedelta(hours=4)


def configured_settings(tmp_path: Path, *, default_timeframe: str = TIMEFRAME) -> Settings:
    return Settings(
        _env_file=None,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        exchange="mock-exchange",
        default_timeframe=default_timeframe,
        supported_timeframes=("15m", "1h", "4h", "1d"),
        raw_data_dir=tmp_path / "raw",
        database_url=f"sqlite:///{tmp_path / 'structure.sqlite3'}",
    )


def migrate(database_url: str) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def create_service(tmp_path: Path, *, default_timeframe: str = TIMEFRAME):
    tmp_path.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite:///{tmp_path / 'structure.sqlite3'}"
    migrate(database_url)
    engine = create_database_engine(database_url)
    settings = configured_settings(tmp_path, default_timeframe=default_timeframe)
    service = create_market_structure_service(engine, settings=settings)
    return engine, service


def insert(engine, candles) -> None:
    CandleRepository(engine).insert_unchanged_or_new(candles)


def stored_rows(engine) -> list[tuple]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT exchange, symbol, timeframe, timestamp, open, high, low, close, volume "
                    "FROM ohlcv_candles ORDER BY exchange, symbol, timeframe, timestamp"
                )
            )
        )


def closed_at(index: int) -> datetime:
    """Instant at which the candle opened at ``index`` hours has fully closed."""

    return EPOCH + INTERVAL * (index + 1)


def test_snapshot_contains_every_section_and_is_reproducible(tmp_path):
    engine, service = create_service(tmp_path)
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    insert(engine, candles)
    as_of = closed_at(len(candles) - 1)
    try:
        snapshot = service.snapshot(as_of=as_of)

        assert snapshot.exchange == "mock-exchange"
        assert snapshot.symbol == "BTC/USDT"
        assert snapshot.timeframe == "1h"
        assert snapshot.as_of == as_of
        assert snapshot.latest_closed_candle == candles[-1]
        assert snapshot.complete is True
        assert snapshot.gaps == ()
        assert snapshot.missing_candle_count == 0
        assert snapshot.completeness.expected_latest_closed_open_time == candles[-1].timestamp
        assert snapshot.swings == snapshot.analysis.confirmed_swings
        assert snapshot.trend.direction is TrendDirection.NEUTRAL
        assert snapshot.detected_range is not None
        assert snapshot.active_range is not None
        assert snapshot.zones
        assert snapshot.volatility.available is True
        assert snapshot.volume.sufficient is True
        assert snapshot.parameters.swings.left_window == 2
        # Configured higher timeframes with no stored candles are explicit.
        assert [context.timeframe for context in snapshot.higher_timeframes] == ["4h", "1d"]
        assert all(not context.available for context in snapshot.higher_timeframes)
        assert {context.reason for context in snapshot.higher_timeframes} == {NO_STORED_CANDLES}

        # The same request against unchanged data always returns an equal result.
        assert service.snapshot(as_of=as_of) == snapshot
        assert service.timeframe_analysis(as_of=as_of) == snapshot.analysis

        # The snapshot is fully machine-readable and JSON serializable.
        payload = json.loads(json.dumps(snapshot.to_json_dict()))
        assert payload["trend"]["direction"] == "neutral"
        assert payload["swings"][0]["kind"] in {"high", "low"}
        assert payload["swing_detection"]["parameters"]["tie_policy"] == "strict"
        assert payload["range"]["detected"]["active"] is True
        assert payload["range"]["active"] == payload["range"]["detected"]
        assert payload["volatility"]["available"] is True
        assert payload["volume"]["sufficient"] is True
        assert payload["completeness"]["complete"] is True
        assert payload["parameters"]["ranges"]["max_width_pct"] == "10"
        assert payload["higher_timeframes"][0]["timeframe"] == "4h"
    finally:
        engine.dispose()


def test_historical_snapshot_at_t_is_unchanged_when_future_candles_are_added(tmp_path):
    candles = zigzag_candles(RANGE_PIVOTS, leg=5) + flat_candles(
        30, start_index=len(zigzag_candles(RANGE_PIVOTS, leg=5)), price="104"
    )
    as_of = closed_at(45)
    snapshot_time = create_service(tmp_path / "calculated-then")
    full_database = create_service(tmp_path / "recalculated-later")
    try:
        # "Calculated then": only the candles known at as_of exist at all.
        insert(snapshot_time[0], candles[:46])
        calculated_then = snapshot_time[1].snapshot(as_of=as_of)

        # "Recalculated later": the database has all later candles from the start.
        insert(full_database[0], candles)
        recalculated = full_database[1].snapshot(as_of=as_of)
        assert recalculated == calculated_then

        # Later candles changing the database still cannot change the history.
        insert(full_database[0], flat_candles(20, start_index=len(candles), price="104"))
        assert full_database[1].snapshot(as_of=as_of) == calculated_then

        # A later as_of does see the appended candles, so the equality above is
        # not an artefact of the snapshot ignoring new data entirely.
        later = full_database[1].snapshot(as_of=closed_at(len(candles) + 19))
        assert later != calculated_then
        assert later.completeness.candle_count > calculated_then.completeness.candle_count
    finally:
        snapshot_time[0].dispose()
        full_database[0].dispose()


def test_snapshot_excludes_the_candle_that_is_still_forming_at_as_of(tmp_path):
    engine, service = create_service(tmp_path)
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    insert(engine, candles)
    try:
        snapshot = service.snapshot(as_of=closed_at(3))

        assert snapshot.latest_closed_candle.timestamp == EPOCH + INTERVAL * 3
        assert snapshot.completeness.candle_count == 4
        assert snapshot.completeness.window_end == EPOCH + INTERVAL * 3
        assert snapshot.complete is True
        assert all(swing.confirmed_at <= snapshot.as_of for swing in snapshot.swings)

        # The very same instant expressed in another timezone behaves identically.
        plus_two = snapshot.as_of.astimezone(timezone_offset())
        assert service.snapshot(as_of=plus_two) == snapshot
    finally:
        engine.dispose()


def timezone_offset():
    return datetime(2024, 1, 1, tzinfo=UTC).astimezone().tzinfo


def test_missing_and_gapped_candles_are_reported_without_fabrication(tmp_path):
    engine, service = create_service(tmp_path)
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    with_hole = tuple(candle for candle in candles[:20] if candle.timestamp != candles[10].timestamp)
    insert(engine, with_hole)
    try:
        snapshot = service.snapshot(as_of=closed_at(24))

        assert snapshot.complete is False
        assert snapshot.missing_candle_count == 6  # 1 internal hole + 5 trailing candles
        assert len(snapshot.gaps) == 2
        assert snapshot.completeness.window_start == candles[0].timestamp
        assert snapshot.completeness.window_end == candles[19].timestamp
        assert snapshot.completeness.expected_latest_closed_open_time == candles[24].timestamp
        assert snapshot.completeness.missing_candles_after_latest_stored == 5
        assert snapshot.latest_closed_candle == candles[19]

        gap = snapshot.gaps[0]
        assert gap.start == candles[10].timestamp
        assert gap.end == candles[10].timestamp
        assert gap.missing_count == 1

        # Structure is not invented across the hole: windows that span it are skipped.
        assert snapshot.analysis.swings.gap_window_count == 4
        assert all(
            (swing.timestamp - EPOCH) // INTERVAL not in {8, 9, 10, 11, 12}
            for swing in snapshot.swings
        )
    finally:
        engine.dispose()


def test_higher_timeframes_use_their_own_stored_candles(tmp_path):
    engine, service = create_service(tmp_path)
    hourly = zigzag_candles(RANGE_PIVOTS, leg=5) + flat_candles(
        90, start_index=len(zigzag_candles(RANGE_PIVOTS, leg=5)), price="104"
    )
    four_hourly = zigzag_candles_at(
        ("100", "110", "104", "112", "106", "114"),
        leg=5,
        step=FOUR_HOURS,
        timeframe="4h",
    )
    daily = zigzag_candles_at(
        ("100", "120", "110", "130"),
        leg=5,
        step=timedelta(days=1),
        timeframe="1d",
    )
    insert(engine, hourly)
    insert(engine, four_hourly)
    insert(engine, daily)
    try:
        snapshot = service.snapshot(as_of=closed_at(len(hourly) - 1))

        assert [context.timeframe for context in snapshot.higher_timeframes] == ["4h", "1d"]
        for context in snapshot.higher_timeframes:
            assert context.synthesized is False
            assert context.available is True
            assert context.analysis is not None

        four_hour_context = snapshot.higher_timeframes[0]
        expected_four_hour = service.timeframe_analysis(as_of=snapshot.as_of, timeframe="4h")
        assert four_hour_context.analysis == expected_four_hour
        # Its candles are the stored 4h candles, not resampled 1h candles.
        assert all(
            swing.timestamp.minute == 0 and swing.timestamp.hour % 4 == 0
            for swing in four_hour_context.swings
        )
        assert four_hour_context.completeness.candle_count == len(four_hourly)

        # The timeframe list is configurable and validated.
        restricted = service.snapshot(
            as_of=snapshot.as_of,
            parameters=MarketStructureParameters(higher_timeframes=("4h",)),
        )
        assert [context.timeframe for context in restricted.higher_timeframes] == ["4h"]
        with pytest.raises(ValueError, match="must be longer than"):
            service.snapshot(
                as_of=snapshot.as_of,
                parameters=MarketStructureParameters(higher_timeframes=("15m",)),
            )
        with pytest.raises(ValueError, match="not in configured supported_timeframes"):
            service.snapshot(
                as_of=snapshot.as_of,
                parameters=MarketStructureParameters(higher_timeframes=("2h",)),
            )
    finally:
        engine.dispose()


def test_higher_timeframe_missing_or_incomplete_data_is_explicit(tmp_path):
    engine, service = create_service(tmp_path)
    hourly = zigzag_candles(RANGE_PIVOTS, leg=5)
    sparse_four_hourly = zigzag_candles_at(("100", "104"), leg=2, step=FOUR_HOURS, timeframe="4h")
    gapped_daily = tuple(
        candle
        for candle in zigzag_candles_at(
            ("100", "120", "110", "130"), leg=5, step=timedelta(days=1), timeframe="1d"
        )
        if candle.timestamp
        not in {
            EPOCH + timedelta(days=1) * 7,
            EPOCH + timedelta(days=1) * 8,
            EPOCH + timedelta(days=1) * 9,
        }
    )
    insert(engine, hourly)
    insert(engine, sparse_four_hourly)
    insert(engine, gapped_daily)
    as_of = EPOCH + timedelta(days=20)
    try:
        snapshot = service.snapshot(as_of=as_of)
        contexts = {context.timeframe: context for context in snapshot.higher_timeframes}
        assert set(contexts) == {"4h", "1d"}

        four_hour = contexts["4h"]
        assert four_hour.available is False
        assert four_hour.reason == INSUFFICIENT_CANDLES
        assert four_hour.analysis is None
        assert four_hour.completeness.candle_count == 3
        assert four_hour.trend is None
        assert four_hour.swings == ()
        assert four_hour.active_range is None

        daily = contexts["1d"]
        assert daily.available is True
        assert daily.reason == INCOMPLETE_DATA
        assert daily.completeness.complete is False
        # 3 missing days inside the range plus 4 trailing days up to as_of.
        assert daily.completeness.missing_candle_count == 7
        assert len(daily.completeness.gaps) == 2
        assert daily.analysis is not None
        assert daily.trend is not None

        # A timeframe with no stored candles at all is explicit, not synthesized.
        empty_database_url = f"sqlite:///{tmp_path / 'empty.sqlite3'}"
        migrate(empty_database_url)
        engine_without_daily = create_database_engine(empty_database_url)
        try:
            insert(engine_without_daily, hourly)
            empty_context = create_market_structure_service(
                engine_without_daily, settings=configured_settings(tmp_path)
            ).snapshot(as_of=as_of)
            assert [context.timeframe for context in empty_context.higher_timeframes] == ["4h", "1d"]
            assert all(not context.available for context in empty_context.higher_timeframes)
            assert {context.reason for context in empty_context.higher_timeframes} == {NO_STORED_CANDLES}
            assert all(
                context.completeness.candle_count == 0 for context in empty_context.higher_timeframes
            )
        finally:
            engine_without_daily.dispose()
    finally:
        engine.dispose()


def test_structure_calculation_never_modifies_source_ohlcv_records(tmp_path):
    engine, service = create_service(tmp_path)
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    insert(engine, candles)
    try:
        before = stored_rows(engine)
        tables_before = set(inspect(engine).get_table_names())

        for as_of in (closed_at(10), closed_at(30), closed_at(len(candles) - 1)):
            service.snapshot(as_of=as_of, parameters=MarketStructureParameters(
                ranges=RangeParameters(lookback_candles=30)
            ))
        service.timeframe_analysis(as_of=closed_at(20))

        assert stored_rows(engine) == before
        # No derived tables are created and no migration is required.
        assert set(inspect(engine).get_table_names()) == tables_before == {"alembic_version", "ohlcv_candles"}
    finally:
        engine.dispose()


def test_insertion_order_does_not_affect_results(tmp_path):
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    chronological = create_service(tmp_path / "chronological")
    reversed_order = create_service(tmp_path / "reversed")
    try:
        insert(chronological[0], candles)
        insert(reversed_order[0], tuple(reversed(candles)))
        as_of = closed_at(len(candles) - 1)

        assert (
            reversed_order[1].snapshot(as_of=as_of)
            == chronological[1].snapshot(as_of=as_of)
        )
    finally:
        chronological[0].dispose()
        reversed_order[0].dispose()


def test_defaults_come_from_settings_and_can_be_overridden(tmp_path):
    engine, service = create_service(tmp_path)
    hourly = zigzag_candles(RANGE_PIVOTS, leg=5)
    insert(engine, hourly)
    try:
        default_snapshot = service.snapshot(as_of=closed_at(len(hourly) - 1))
        assert (default_snapshot.exchange, default_snapshot.symbol, default_snapshot.timeframe) == (
            "mock-exchange",
            "BTC/USDT",
            "1h",
        )

        eth_candles = tuple(
            replace(candle, symbol="ETH/USDT", timeframe="15m", timestamp=EPOCH + timedelta(minutes=15) * index)
            for index, candle in enumerate(hourly)
        )
        insert(engine, eth_candles)
        eth_snapshot = service.snapshot(
            exchange="mock-exchange",
            symbol="ETH/USDT",
            timeframe="15m",
            as_of=closed_at(len(hourly) - 1),
        )
        assert eth_snapshot.symbol == "ETH/USDT"
        assert eth_snapshot.timeframe == "15m"
        assert eth_snapshot.completeness.candle_count == len(eth_candles)
        assert eth_snapshot.trend.direction is TrendDirection.NEUTRAL
        assert eth_snapshot.detected_range is not None
    finally:
        engine.dispose()


def test_invalid_requests_raise_clear_errors(tmp_path):
    engine, service = create_service(tmp_path)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            service.snapshot(as_of=datetime(2024, 1, 1))  # noqa: DTZ001 - naive on purpose
        with pytest.raises(ValueError, match="supported_timeframes"):
            service.snapshot(timeframe="2h", as_of=closed_at(5))
        with pytest.raises(ValueError, match="symbol must not be empty"):
            service.snapshot(symbol="   ", as_of=closed_at(5))
        with pytest.raises(ValueError, match="exchange must not be empty"):
            service.snapshot(exchange="", as_of=closed_at(5))
    finally:
        engine.dispose()


def test_empty_database_returns_an_explicit_insufficient_snapshot(tmp_path):
    engine, service = create_service(tmp_path)
    try:
        snapshot = service.snapshot(as_of=closed_at(5))

        assert snapshot.latest_closed_candle is None
        assert snapshot.completeness.candle_count == 0
        assert snapshot.complete is False
        assert snapshot.completeness.missing_candles_after_latest_stored is None
        assert snapshot.swings == ()
        assert snapshot.trend.direction is TrendDirection.NEUTRAL
        assert snapshot.detected_range is None
        assert snapshot.active_range is None
        assert snapshot.zones == ()
        assert snapshot.volatility.available is False
        assert snapshot.volume.sufficient is False
        assert [context.timeframe for context in snapshot.higher_timeframes] == ["4h", "1d"]
        assert all(not context.available for context in snapshot.higher_timeframes)
        assert {context.reason for context in snapshot.higher_timeframes} == {NO_STORED_CANDLES}
        assert all(context.synthesized is False for context in snapshot.higher_timeframes)
    finally:
        engine.dispose()


def test_snapshot_range_and_zone_evidence_is_reported_for_consolidation(tmp_path):
    engine, service = create_service(tmp_path)
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    insert(engine, candles)
    try:
        snapshot = service.snapshot(as_of=closed_at(len(candles) - 1))
        detected = snapshot.detected_range
        assert detected is not None

        assert detected.range_high == Decimal("104.475")
        assert detected.range_low == Decimal("99.550")
        assert detected.touch_count == 8
        assert detected.upper_touch_timestamps[0] == EPOCH + INTERVAL * 5
        assert detected.lower_touch_timestamps[-1] == EPOCH + INTERVAL * 40
        assert snapshot.active_range is detected

        roles = {zone.role.value for zone in snapshot.zones}
        assert roles == {"support", "resistance"}
        assert all(zone.touch_count >= 1 for zone in snapshot.zones)
        assert all(zone.first_observed_timestamp <= zone.last_tested_timestamp for zone in snapshot.zones)
    finally:
        engine.dispose()
