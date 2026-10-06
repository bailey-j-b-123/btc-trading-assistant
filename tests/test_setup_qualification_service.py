"""Read-only SQLite integration: historical replay, future inserts, no new schema."""

from dataclasses import replace
from decimal import Decimal

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
    bounded_replay_start,
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


def _live_setups(snapshot):
    return [
        setup
        for setup in snapshot.setups
        if setup.state in (SetupState.WATCH, SetupState.QUALIFIED)
    ]


def test_same_version_decides_differently_under_different_volume_state(tmp_path):
    """Market state drives the decision: identical prices, different volume.

    The thin-volume series shares every price print (hence every seed) with
    the qualifying series; only the latest close prints negligible volume.
    The same default strategy version then refuses QUALIFIED on the volume
    rule instead of qualifying — the decision follows market state, not a
    retuned threshold.
    """
    from forward_fixtures import labelled_series

    tmp_b = tmp_path / "thin"
    tmp_b.mkdir()
    engine_a, _ = create_service(tmp_path)
    engine_b, _ = create_service(tmp_b)
    try:
        base = labelled_series()
        thin = tuple(
            replace(c, volume=Decimal("0.001")) if i == 20 else c
            for i, c in enumerate(base)
        )
        insert(engine_a, base)
        insert(engine_b, thin)
        parameters = QualificationParameters()
        as_of = EPOCH + 21 * INTERVAL
        snap_a = QualificationService(engine_a).snapshot(
            **TARGET, as_of=as_of, parameters=parameters
        )
        snap_b = QualificationService(engine_b).snapshot(
            **TARGET, as_of=as_of, parameters=parameters
        )
        assert snap_a.state == SetupState.QUALIFIED
        assert snap_b.state == SetupState.WATCH
        # The seeds are identical: same families at the same closes.
        seeds_a = sorted(
            (s.family, s.created_at) for s in _live_setups(snap_a)
        )
        seeds_b = sorted(
            (s.family, s.created_at) for s in _live_setups(snap_b)
        )
        assert seeds_a == seeds_b
        # Only the volume rule blocks the thin series.
        assert any(s.state is SetupState.QUALIFIED for s in snap_a.setups)
        assert not any(s.state is SetupState.QUALIFIED for s in snap_b.setups)
        assert all(
            s.failed_rules == ("volume",) for s in _live_setups(snap_b)
        )
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_bounded_replay_reproduces_every_live_setup_exactly(tmp_path):
    """Bounded replay matches full replay on live candidates; old terminals drop.

    With 41 stored candles the 12-frame bounded replay reproduces every live
    (WATCH or QUALIFIED) setup with dataclass equality at every live-complete
    boundary (``B >= start + max_bars``), while terminals seeded before the
    window stay out of the envelope — their ledger records, where they
    mattered, already exist. Earlier frames are warm-up scaffolding and are
    asserted only for shape, never for content.
    """
    from forward_fixtures import labelled_series

    engine, _ = create_service(tmp_path)
    service = QualificationService(engine)
    try:
        pad = 20
        flat = tuple(bar(i, 100) for i in range(pad))
        shifted = tuple(
            replace(c, timestamp=EPOCH + INTERVAL * (pad + i))
            for i, c in enumerate(labelled_series())
        )
        insert(engine, flat + shifted)
        parameters = QualificationParameters()
        as_of = EPOCH + (pad + 21) * INTERVAL
        full = service.enumerate_snapshots(**TARGET, as_of=as_of, parameters=parameters)
        assert len(full) == pad + 21
        assert full[-1].state == SetupState.QUALIFIED
        assert any(s.state is SetupState.WATCH for s in full[-1].setups), (
            "the fixture must keep live setups at the end or the test is vacuous"
        )
        start = bounded_replay_start(
            as_of=as_of, timeframe=TIMEFRAME, parameters=parameters
        )
        bounded = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters, start_at=start
        )
        assert len(bounded) == 12  # (max_bars + 1) closes back, inclusive
        assert bounded[0].as_of == start
        assert bounded[-1].as_of == as_of == full[-1].as_of
        assert [s.as_of for s in bounded] == [
            start + i * INTERVAL for i in range(12)
        ]
        max_bars = max(
            parameters.continuation_max_bars,
            parameters.reversal_max_bars,
            parameters.range_max_bars,
        )
        complete = [s for s in bounded if s.as_of >= start + max_bars * INTERVAL]
        assert len(complete) == 2  # the final snapshot and the one before it
        full_by_as_of = {snapshot.as_of: snapshot for snapshot in full}
        for snapshot in complete:
            mate = full_by_as_of[snapshot.as_of]
            assert snapshot.state == mate.state
            assert _live_setups(snapshot) == _live_setups(mate)
        assert len(bounded[-1].setups) < len(full[-1].setups)
        with pytest.raises(ValueError, match="candle-close boundary"):
            service.enumerate_snapshots(
                **TARGET, as_of=as_of, start_at=as_of - INTERVAL / 2
            )
        with pytest.raises(ValueError, match="must not be after as_of"):
            service.enumerate_snapshots(
                **TARGET, as_of=as_of, start_at=as_of + INTERVAL
            )
    finally:
        engine.dispose()
