"""Temporary migrated SQLite history safety, enumeration and future-insertion tests."""

from dataclasses import replace

import pytest
from market_structure_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    TIMEFRAME,
    zigzag_candles,
)
from sqlalchemy import inspect
from test_market_structure_service import create_service, insert, stored_rows
from test_pattern_liquidity import bar, prefix

from trading_assistant.pattern_liquidity import PatternLiquidityService

TARGET = {"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": TIMEFRAME}


def test_database_future_insertion_snapshot_identity_and_source_safety(tmp_path):
    engine, structure = create_service(tmp_path)
    service = PatternLiquidityService(engine)
    candles = zigzag_candles(
        ("100", "110", "100", "115", "100", "110", "95", "112"), leg=4
    )
    try:
        insert(engine, candles[:15])
        at = candles[14].timestamp + INTERVAL
        table_names = inspect(engine).get_table_names()
        step3_before = structure.snapshot(as_of=at)
        before_rows = stored_rows(engine)
        before = service.snapshot(**TARGET, as_of=at)
        assert (
            before.to_json_dict() == service.snapshot(**TARGET, as_of=at).to_json_dict()
        )
        assert stored_rows(engine) == before_rows
        insert(engine, candles[15:])
        after_rows = stored_rows(engine)
        assert service.snapshot(**TARGET, as_of=at) == before
        assert (
            service.snapshot(**TARGET, as_of=at).to_json_dict() == before.to_json_dict()
        )
        assert structure.snapshot(as_of=at) == step3_before
        assert stored_rows(engine) == after_rows
        assert before_rows == after_rows[: len(before_rows)]
        assert inspect(engine).get_table_names() == table_names
        assert service.enumerate_events(**TARGET, as_of=at) == before.events()
    finally:
        engine.dispose()


def test_event_enumeration_preserves_occurrences_and_filters_after_warmup(tmp_path):
    engine, _ = create_service(tmp_path)
    service = PatternLiquidityService(engine)
    try:
        candles = prefix() + (bar(5, 112), bar(6, 109))
        insert(engine, candles)
        end = EPOCH + 7 * INTERVAL
        snapshot = service.snapshot(**TARGET, as_of=end)
        assert snapshot.breakouts and snapshot.failed_breakouts and snapshot.retests
        events = service.enumerate_events(**TARGET, as_of=end)
        assert events == snapshot.events()
        assert len(events) == len({e.id for e in events})
        assert events == service.enumerate_events(**TARGET, as_of=end)
        later = service.enumerate_events(**TARGET, as_of=end, known_since=end)
        assert later == tuple(e for e in events if e.known_at == end)
        assert any(hasattr(e, "breakout") for e in later)
        assert not service.snapshot(**TARGET, as_of=end - INTERVAL).failed_breakouts
        with pytest.raises(ValueError, match="known_since"):
            service.enumerate_events(**TARGET, as_of=end, known_since=end + INTERVAL)
    finally:
        engine.dispose()


def test_database_gaps_empty_history_and_instrument_isolation(tmp_path):
    engine, _ = create_service(tmp_path)
    service = PatternLiquidityService(engine)
    try:
        assert service.snapshot(**TARGET, as_of=EPOCH).status == "insufficient"
        candles = prefix() + (bar(6, 112), bar(7, 109))
        insert(engine, candles)
        insert(engine, tuple(replace(c, symbol="ETH/USD") for c in candles))
        before_rows = stored_rows(engine)
        result = service.snapshot(**TARGET, as_of=EPOCH + 10 * INTERVAL)
        assert result.completeness.missing_candle_count == 3
        assert result.status == "incomplete"
        assert not result.breakouts and not result.sweeps and not result.retests
        assert stored_rows(engine) == before_rows
        other = service.snapshot(
            **(TARGET | {"symbol": "ETH/USD"}), as_of=EPOCH + 8 * INTERVAL
        )
        assert other.symbol == "ETH/USD"
    finally:
        engine.dispose()
