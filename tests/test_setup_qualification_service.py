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


def _labelled_store_with_tail(tmp_path, *, tail_price=62160, tail_bars=25):
    """The equivalence-test store plus flat closes so edges stay in range.

    Same flat warm-up plus labelled candles as
    ``test_bounded_replay_reproduces_every_live_setup_exactly``; the flat
    tail only extends the stored history so a test can evaluate past the
    labelled end (a seed at the window edge needs room for ``as_of``).
    """
    from forward_fixtures import labelled_series

    engine, _ = create_service(tmp_path)
    pad = 20
    flat = tuple(bar(i, 100) for i in range(pad))
    shifted = tuple(
        replace(c, timestamp=EPOCH + INTERVAL * (pad + i))
        for i, c in enumerate(labelled_series())
    )
    first_tail = pad + len(shifted)
    tail = tuple(
        replace(
            bar(first_tail + i, tail_price),
            timestamp=EPOCH + INTERVAL * (first_tail + i),
        )
        for i in range(tail_bars)
    )
    insert(engine, flat + shifted + tail)
    return engine


@pytest.mark.parametrize(
    "parameters",
    [
        QualificationParameters(),
        QualificationParameters(
            continuation_max_bars=5, reversal_max_bars=7, range_max_bars=11
        ),
    ],
    ids=["default", "mixed-max-bars"],
)
def test_bounded_window_always_covers_every_live_setup(tmp_path, parameters):
    """No live setup is ever seeded before the bounded window.

    The expiry rule kills strictly past max_bars ("Exactly max_bars remains
    eligible"), while the window reaches one frame further back — so every
    WATCH/QUALIFIED setup is seeded strictly inside the window. The sweep
    proves it at every boundary of a 60-close replay, under both default
    and mixed per-family expiries.
    """
    from forward_fixtures import labelled_series

    engine = _labelled_store_with_tail(tmp_path)
    try:
        service = QualificationService(engine)
        as_of = EPOCH + (20 + len(labelled_series()) + 20) * INTERVAL
        full = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters
        )
        assert len(full) > 40
        checked = 0
        for snapshot in full:
            start = bounded_replay_start(
                as_of=snapshot.as_of, timeframe=TIMEFRAME, parameters=parameters
            )
            for setup in _live_setups(snapshot):
                checked += 1
                assert setup.created_at >= start, (
                    f"live setup {setup.id} seeded {setup.created_at} before "
                    f"window start {start} at {snapshot.as_of}"
                )
        assert checked > 0, "the sweep saw no live setup and proves nothing"
    finally:
        engine.dispose()


def test_bounded_replay_evaluates_seed_at_window_edge(tmp_path):
    """A setup seeded exactly at the window start is evaluated, not dropped.

    ``as_of`` is chosen so the bounded window starts exactly on an aged
    setup's seed close. The edge seed is expired there (the window's extra
    frame is safety margin, never live room), and the bounded replay still
    carries it with the identical terminal record as the full replay.
    """
    engine = _labelled_store_with_tail(tmp_path)
    try:
        service = QualificationService(engine)
        parameters = QualificationParameters()
        max_bars = max(
            parameters.continuation_max_bars,
            parameters.reversal_max_bars,
            parameters.range_max_bars,
        )
        probe = service.enumerate_snapshots(
            **TARGET, as_of=EPOCH + 41 * INTERVAL, parameters=parameters
        )
        aged = sorted(_live_setups(probe[-1]), key=lambda s: s.created_at)
        assert aged, "the fixture must keep a live setup to age to the edge"
        edge_seed = aged[0].created_at
        as_of = edge_seed + (max_bars + 1) * INTERVAL
        start = bounded_replay_start(
            as_of=as_of, timeframe=TIMEFRAME, parameters=parameters
        )
        assert start == edge_seed
        full = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters
        )
        bounded = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters, start_at=start
        )
        assert bounded[0].as_of == start
        full_by_id = {setup.id: setup for setup in full[-1].setups}
        bounded_by_id = {setup.id: setup for setup in bounded[-1].setups}
        edge = [
            setup for setup in full[-1].setups if setup.created_at == edge_seed
        ]
        assert edge, "the full replay must still carry the edge seed"
        for setup in edge:
            assert setup.state is SetupState.NO_SETUP
            assert setup.terminal_reason is not None
            assert setup.id in bounded_by_id, (
                f"seed at the window edge {setup.id} was dropped"
            )
            assert bounded_by_id[setup.id] == full_by_id[setup.id]
        assert _live_setups(bounded[-1]) == _live_setups(full[-1])
    finally:
        engine.dispose()


def test_bounded_replay_mixed_max_bars_uses_maximum_and_matches_full(tmp_path):
    """Mixed per-family expiries widen the window to the maximum only.

    With continuation/reversal/range expiries of 5/7/11 the window holds
    the maximum plus the one-frame margin (13 frames), and every
    fully-warmed snapshot inside it matches the full replay exactly.
    """
    engine = _labelled_store_with_tail(tmp_path, tail_bars=0)
    try:
        service = QualificationService(engine)
        parameters = QualificationParameters(
            continuation_max_bars=5, reversal_max_bars=7, range_max_bars=11
        )
        as_of = EPOCH + 41 * INTERVAL
        full = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters
        )
        start = bounded_replay_start(
            as_of=as_of, timeframe=TIMEFRAME, parameters=parameters
        )
        assert start == as_of - 12 * INTERVAL
        bounded = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters, start_at=start
        )
        assert len(bounded) == 13
        assert bounded[0].as_of == start
        assert bounded[-1].as_of == as_of
        complete = [s for s in bounded if s.as_of >= start + 11 * INTERVAL]
        assert len(complete) == 2
        full_by_as_of = {snapshot.as_of: snapshot for snapshot in full}
        for snapshot in complete:
            mate = full_by_as_of[snapshot.as_of]
            assert snapshot.state == mate.state
            assert _live_setups(snapshot) == _live_setups(mate)
        assert _live_setups(full[-1]), "the fixture must keep live setups"
    finally:
        engine.dispose()


def test_dashboard_bounded_evaluation_matches_full_replay(tmp_path):
    """The dashboard's exact evaluation path matches the full replay.

    This repeats ``DashboardService._evaluate`` step for step — default
    parameters, ``bounded_replay_start`` rooting, ``build_frames`` plus
    ``enumerate_qualifications``, final snapshot — and proves the frame
    count never grows with stored history and the live setups equal the
    full replay's.
    """
    from trading_assistant.setup_qualification.engine import (
        enumerate_qualifications,
    )

    engine = _labelled_store_with_tail(tmp_path, tail_bars=0)
    try:
        service = QualificationService(engine)
        parameters = QualificationParameters()
        as_of = EPOCH + 41 * INTERVAL
        frames = service.build_frames(
            **TARGET,
            as_of=as_of,
            parameters=parameters,
            start_at=bounded_replay_start(
                as_of=as_of, timeframe=TIMEFRAME, parameters=parameters
            ),
        )
        assert len(frames) == 12
        snapshots = enumerate_qualifications(
            frames, as_of=as_of, parameters=parameters
        )
        assert snapshots[-1].as_of == as_of
        full = service.enumerate_snapshots(
            **TARGET, as_of=as_of, parameters=parameters
        )
        assert snapshots[-1].state == full[-1].state
        assert _live_setups(snapshots[-1]) == _live_setups(full[-1])
    finally:
        engine.dispose()


def _reason_fields(reason):
    """Mirror plain.js parseAssignments: ;-separated key=value segments."""
    fields = {}
    for part in reason.split(";"):
        key, sep, value = part.strip().partition("=")
        key = key.strip()
        if (
            sep
            and key
            and all(char.isalpha() or char == "_" for char in key)
            and value.strip()
        ):
            fields[key] = value.strip()
    return fields


def _assert_decimal_or_none(value):
    assert value == "None" or Decimal(value) is not None


def _assert_reason_shape(rule_id, reason):
    """Pin the exact reason shapes the frontend translator parses.

    web/static/js/plain.js dispatches on rule_id and parses ;-separated
    assignments (plus a few anchored patterns); any backend wording change
    must update the translator in the same commit. Unknown rule ids fail
    closed so a new rule cannot silently leak raw text to the UI.
    """
    fields = _reason_fields(reason)
    if rule_id == "seed_event":
        assert reason.startswith("confirmed ")
        assert "; reference=" in reason
    elif rule_id == "later_evaluation":
        assert reason == "evaluation must be strictly after seed confirmation"
    elif rule_id == "held_retest":
        assert reason == "requires Step 4 held retest of the seed breakout"
    elif rule_id == "no_failed_breakout":
        assert reason == "catalog must contain no confirmed failure of seed breakout"
    elif rule_id == "no_failed_retest":
        assert (
            reason
            == "catalog must contain no confirmed failed retest of seed breakout"
        )
    elif rule_id == "reversal_breakout":
        assert (
            reason
            == "requires later directional Step 4 breakout at a different reference"
        )
    elif rule_id == "active_range":
        assert {"active_bounds", "frozen_bounds"} <= set(fields)
    elif rule_id == "range_followthrough":
        assert {"current_close", "seed_close"} <= set(fields)
    elif rule_id == "structure":
        assert reason.startswith("trend=")
        assert {"trend", "reason"} <= set(fields)
    elif rule_id == "volume":
        assert set(fields) == {"relative_volume", "minimum", "source_reason"}
        _assert_decimal_or_none(fields["relative_volume"])
        assert Decimal(fields["minimum"]) is not None
    elif rule_id == "volatility":
        assert "ATR_percent" in fields
        assert "source_reason" in fields
        assert "<=" in reason
        limit = reason.split("<=")[1].split(";")[0].strip()
        assert Decimal(limit) is not None
        _assert_decimal_or_none(fields["ATR_percent"])
    elif rule_id == "location":
        assert "close" in fields
        assert "[" in reason and "]" in reason
        _assert_decimal_or_none(fields["close"])
    elif rule_id == "lifecycle":
        if reason == "candidate is contiguous, unexpired and not invalidated":
            return
        head = reason.split(";")[0]
        assert head.strip()
        assert "=" not in head
        assert int(fields["age_bars"]) >= 0
        assert int(fields["max_bars"]) > 0
    elif rule_id == "classical_pattern":
        assert reason == "no current confirmed classical pattern since seed"
    elif rule_id.startswith("classical_pattern:"):
        head, middle, tail = (part.strip() for part in reason.split(";"))
        assert head and "=" not in head
        assert middle == "optional only"
        assert tail == "cannot qualify or veto a setup"
    elif rule_id == "higher_timeframe:not_requested":
        assert reason == "no higher timeframes requested; no alignment inferred"
    elif rule_id.startswith("higher_timeframe:"):
        assert reason.startswith("requires aligned trend when mandatory; ")
        assert {"trend", "unavailable_reason"} <= set(fields)
    else:
        raise AssertionError(
            f"no translator contract for rule {rule_id!r}: "
            f"extend plain.js and this pin together ({reason!r})"
        )


def test_rule_reasons_keep_frontend_translator_contract(tmp_path):
    """Every emitted rule reason keeps the shape plain.js parses.

    Sweeps a full labelled replay and checks each rule's reason against
    the translator's grammar (rule_id dispatch, ;-separated assignments,
    anchored sentences), so backend wording can never drift silently
    under the plain-English UI.
    """
    engine = _labelled_store_with_tail(tmp_path, tail_bars=0)
    try:
        service = QualificationService(engine)
        full = service.enumerate_snapshots(
            **TARGET,
            as_of=EPOCH + 41 * INTERVAL,
            parameters=QualificationParameters(),
        )
        seen: dict[str, int] = {}
        for snapshot in full:
            for setup in snapshot.setups:
                for outcome in setup.rules:
                    seen[outcome.rule_id] = seen.get(outcome.rule_id, 0) + 1
                    _assert_reason_shape(outcome.rule_id, outcome.reason)
        for rule_id in ("structure", "volume", "volatility", "location", "lifecycle"):
            assert seen.get(rule_id, 0) > 0, f"no {rule_id} rule observed"
    finally:
        engine.dispose()
