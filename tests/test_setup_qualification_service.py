"""Read-only SQLite integration: historical replay, future inserts, no new schema."""

from dataclasses import replace

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, TIMEFRAME
from sqlalchemy import inspect
from test_market_structure_service import create_service, insert, stored_rows
from test_pattern_liquidity import bar, prefix

from trading_assistant.pattern_liquidity import PatternLiquidityService
from trading_assistant.setup_qualification import (
    QualificationParameters,
    QualificationService,
    SetupState,
)

TARGET = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}


def test_database_replay_future_insertion_step2_4_safety_and_schema(tmp_path):
    engine, structure = create_service(tmp_path)
    service = QualificationService(engine)
    patterns = PatternLiquidityService(engine)
    candles = prefix() + (
        bar(5, 112),
        bar(6, 111, high=112, low=109),
        bar(7, 108),
        bar(8, 113),
    )
    try:
        insert(engine, candles[:7])
        at = EPOCH + 7 * INTERVAL
        rows_before = stored_rows(engine)
        tables = inspect(engine).get_table_names()
        structure_before = structure.snapshot(as_of=at)
        patterns_before = patterns.snapshot(**TARGET, as_of=at)
        history = service.enumerate_snapshots(**TARGET, as_of=at)
        before = service.snapshot(**TARGET, as_of=at)
        assert history[-1] == before
        assert [s.as_of for s in history] == [EPOCH + i * INTERVAL for i in range(1, 8)]
        assert history[-2].state == SetupState.WATCH
        assert before.setups
        assert service.enumerate_snapshots(**TARGET, as_of=at, known_since=at) == (
            before,
        )
        assert stored_rows(engine) == rows_before
        insert(engine, candles[7:])
        rows_after = stored_rows(engine)
        assert service.snapshot(**TARGET, as_of=at) == before
        assert service.enumerate_snapshots(**TARGET, as_of=at) == history
        assert structure.snapshot(as_of=at) == structure_before
        assert patterns.snapshot(**TARGET, as_of=at) == patterns_before
        assert stored_rows(engine) == rows_after
        assert rows_before == rows_after[: len(rows_before)]
        assert inspect(engine).get_table_names() == tables
    finally:
        engine.dispose()


def test_empty_history_gap_boundaries_and_symbol_isolation(tmp_path):
    engine, _ = create_service(tmp_path)
    service = QualificationService(engine)
    try:
        empty = service.snapshot(**TARGET, as_of=EPOCH)
        assert empty.state == SetupState.NO_SETUP
        assert empty.status == "incomplete"
        candles = prefix() + (bar(5, 112), bar(7, 111, high=112, low=109))
        insert(engine, candles)
        insert(engine, tuple(replace(c, symbol="ETH/USD") for c in candles))
        history = service.enumerate_snapshots(**TARGET, as_of=EPOCH + 8 * INTERVAL)
        assert history[5].state == SetupState.WATCH
        assert history[6].state == SetupState.NO_SETUP
        assert all(
            s.terminal_reason == "missing_current_candle" for s in history[6].setups
        )
        assert history[7].state == SetupState.NO_SETUP
        other = service.snapshot(
            **(TARGET | {"symbol": "ETH/USD"}), as_of=EPOCH + 8 * INTERVAL
        )
        assert other.symbol == "ETH/USD"
        assert {s.id for s in other.setups}.isdisjoint(s.id for s in history[-1].setups)
    finally:
        engine.dispose()


def test_requested_higher_timeframe_missing_stays_unknown_and_boundary_validation(
    tmp_path,
):
    engine, _ = create_service(tmp_path)
    service = QualificationService(engine)
    try:
        insert(engine, prefix() + (bar(5, 112),))
        p = QualificationParameters(
            higher_timeframes=("4h",), require_higher_timeframe_alignment=True
        )
        snapshot = service.snapshot(**TARGET, as_of=EPOCH + 6 * INTERVAL, parameters=p)
        assert snapshot.state == SetupState.WATCH
        assert all("higher_timeframe:4h" in s.pending_rules for s in snapshot.setups)
        with pytest.raises(ValueError, match="candle-close boundary"):
            service.snapshot(**TARGET, as_of=EPOCH + INTERVAL / 2)
    finally:
        engine.dispose()
