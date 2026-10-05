"""Offline deterministic Step 4 fixtures, including adversarial prefix replay."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from market_structure_fixtures import (
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    TIMEFRAME,
    candle_at,
    zigzag_candles,
)

from trading_assistant.market_structure.analysis import analyze_candles
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.ranges import RangeParameters
from trading_assistant.pattern_liquidity import (
    PatternLiquidityParameters,
    analyze_patterns,
)

D = Decimal


def bar(i, close, *, high=None, low=None):
    price = D(str(close))
    return candle_at(
        i,
        open_=str(price),
        close=str(price),
        high=str(high if high is not None else price + 1),
        low=str(low if low is not None else price - 1),
    )


def prefix():
    return tuple(bar(i, price) for i, price in enumerate((100, 104, 109, 104, 103)))


def mirrored(candles):
    return tuple(
        replace(
            c,
            open=200 - c.open,
            high=200 - c.low,
            low=200 - c.high,
            close=200 - c.close,
        )
        for c in candles
    )


def snap(candles, *, at=None, parameters=None, structure_parameters=None):
    return analyze_patterns(
        candles,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of=at or (candles[-1].timestamp + INTERVAL if candles else EPOCH),
        parameters=parameters,
        structure_parameters=structure_parameters,
    )


def swing_events(events):
    return tuple(e for e in events if e.reference.type.startswith("swing_"))


@pytest.mark.parametrize("mirror,direction", [(False, "bullish"), (True, "bearish")])
def test_breakouts_failures_and_future_failure_invisible(mirror, direction):
    candles = prefix() + (bar(5, 112), bar(6, 109))
    if mirror:
        candles = mirrored(candles)
    original = snap(candles[:6])
    historical = snap(candles, at=EPOCH + 6 * INTERVAL)
    assert original == historical
    assert not original.failed_breakouts
    (breakout,) = swing_events(original.breakouts)
    assert breakout.direction == direction
    assert breakout.known_at == EPOCH + 6 * INTERVAL
    assert breakout.reference.known_at <= breakout.candle.timestamp
    assert breakout.penetration == 2
    assert breakout.penetration_pct > 0
    assert breakout.penetration_atr is None
    later = snap(candles)
    (failure,) = (f for f in later.failed_breakouts if f.breakout.id == breakout.id)
    assert failure.reentry_distance == 1
    assert failure.elapsed_candles == 1
    assert failure.elapsed_seconds == 3600
    assert failure.breakout == breakout


@pytest.mark.parametrize(
    "close,expected", [("110", False), ("110.11", False), ("110.110001", True)]
)
def test_breakout_strict_tolerance_boundary(close, expected):
    result = snap(prefix() + (bar(5, close, high="113"),))
    assert bool(swing_events(result.breakouts)) is expected


@pytest.mark.parametrize("mirror,side", [(False, "above"), (True, "below")])
def test_wick_only_sweep_not_breakout(mirror, side):
    candles = prefix() + (bar(5, 109, high=112, low=103),)
    if mirror:
        candles = mirrored(candles)
    result = snap(candles)
    (sweep,) = swing_events(result.sweeps)
    assert sweep.direction == side
    assert sweep.penetration == 2
    assert sweep.reclaim_close == candles[-1].close
    assert not swing_events(result.breakouts)


@pytest.mark.parametrize(
    "close,high", [("111", "112"), ("109", "110.11"), ("109", "109.9")]
)
def test_non_sweep_rejection(close, high):
    assert not swing_events(snap(prefix() + (bar(5, close, high=high),)).sweeps)


def test_sweep_reclaim_tolerance():
    candles = prefix() + (bar(5, "109.95", high=112),)
    assert swing_events(snap(candles).sweeps)
    p = PatternLiquidityParameters(sweep_reclaim_pct="0.1")
    assert not swing_events(snap(candles, parameters=p).sweeps)


def test_multiple_close_confirmation_and_frozen_reference():
    p = PatternLiquidityParameters(breakout_confirmation_candles=2)
    candles = prefix() + (bar(5, 112), bar(6, 113))
    assert not snap(candles[:6], parameters=p).breakouts
    (breakout,) = swing_events(snap(candles, parameters=p).breakouts)
    assert breakout.candle.timestamp == EPOCH + 5 * INTERVAL
    assert breakout.known_at == EPOCH + 7 * INTERVAL
    assert len(breakout.confirmation_candles) == 2
    assert not snap(prefix() + (bar(5, 112), bar(6, 109)), parameters=p).breakouts


@pytest.mark.parametrize(
    "close,state", [("110", "observed"), ("111", "held"), ("109", "failed")]
)
@pytest.mark.parametrize("mirror", [False, True])
def test_retest_states_and_timing(close, state, mirror):
    candles = prefix() + (bar(5, 112), bar(6, close, high=112, low=109))
    if mirror:
        candles = mirrored(candles)
    assert not snap(candles[:6]).retests
    result = snap(candles)
    (breakout,) = swing_events(result.breakouts[:2])
    retests = tuple(r for r in result.retests if r.breakout.id == breakout.id)
    assert {r.state for r in retests} == (
        {"observed"} if state == "observed" else {"observed", state}
    )
    assert all(r.candle.timestamp >= breakout.known_at for r in retests)
    assert all(r.evidence_candles == candles[6:] for r in retests)


def test_retest_waits_for_later_closed_confirmation_and_expires():
    candles = prefix() + (bar(5, 112), bar(6, 110), bar(7, 112))
    at_observation = snap(candles, at=EPOCH + 7 * INTERVAL)
    assert {r.state for r in at_observation.retests} == {"observed"}
    assert "held" in {r.state for r in snap(candles).retests}
    p = PatternLiquidityParameters(retest_window_candles=1, failure_window_candles=1)
    assert {r.state for r in snap(candles, parameters=p).retests} == {"observed"}
    late_failure = candles[:7] + (bar(7, 108),)
    assert not snap(late_failure, parameters=p).failed_breakouts


@pytest.mark.parametrize("mirror,kind", [(False, "equal_high"), (True, "equal_low")])
def test_equal_clusters_confirmed_members_only(mirror, kind):
    candles = zigzag_candles(("100", "110", "100", "110", "100"), leg=4)
    if mirror:
        candles = mirrored(candles)
    result = snap(candles)
    (cluster,) = (c for c in result.equal_levels if c.type == kind)
    assert cluster.member_count == 2
    assert cluster.first_known_at == cluster.known_at
    assert all(s.confirmed_at <= cluster.known_at for s in cluster.members)
    before = snap(candles, at=cluster.known_at - timedelta(microseconds=1))
    assert cluster.id not in {c.id for c in before.equal_levels}
    assert snap(candles, at=cluster.known_at) == snap(
        tuple(c for c in candles if c.timestamp + INTERVAL <= cluster.known_at),
        at=cluster.known_at,
    )


@pytest.mark.parametrize(
    "pivots,kind",
    [
        (("100", "110", "100", "110", "95"), "double_top"),
        (("110", "100", "110", "100", "115"), "double_bottom"),
        (("100", "110", "100", "115", "100", "110", "95"), "head_and_shoulders"),
        (
            ("110", "100", "110", "95", "110", "100", "115"),
            "inverse_head_and_shoulders",
        ),
    ],
)
def test_patterns_geometry_formation_and_confirmation(pivots, kind):
    candles = zigzag_candles(pivots, leg=4)
    result = snap(candles)
    (formed,) = (
        p for p in result.chart_patterns if p.type == kind and p.state == "formed"
    )
    (confirmed,) = (
        p
        for p in result.chart_patterns
        if p.pattern_id == formed.pattern_id and p.state == "confirmed"
    )
    assert confirmed.known_at > formed.formed_at
    assert confirmed.pattern_id == formed.pattern_id
    assert formed.formation_timestamp < formed.formed_at
    assert formed.confirmation_timestamp is None
    assert confirmed.confirmation_timestamp == confirmed.known_at
    assert formed.geometry.depth_pct >= result.parameters.pattern_min_depth_pct
    assert all(s.confirmed_at <= formed.formed_at for s in formed.components)
    historical = snap(candles, at=formed.formed_at)
    assert not any(
        p.pattern_id == formed.pattern_id and p.state == "confirmed"
        for p in historical.chart_patterns
    )
    assert historical == snap(
        tuple(c for c in candles if c.timestamp + INTERVAL <= formed.formed_at)
    )
    assert confirmed.evidence_candles[-1].timestamp + INTERVAL == confirmed.known_at


def test_pattern_invalidation_is_not_future_knowledge():
    candles = zigzag_candles(("100", "110", "100", "110", "105"), leg=4)
    (formed,) = (p for p in snap(candles).chart_patterns if p.type == "double_top")
    future = candles + (bar(len(candles), 115),)
    result = snap(future)
    (invalidated,) = (
        p
        for p in result.chart_patterns
        if p.pattern_id == formed.pattern_id and p.state == "invalidated"
    )
    assert invalidated.known_at == future[-1].timestamp + INTERVAL
    assert snap(future, at=formed.formed_at) == snap(candles, at=formed.formed_at)


@pytest.mark.parametrize(
    "pivots,kind",
    [
        (("100", "110", "100", "113", "95"), "double_top"),  # Unequal peaks
        (("100", "100.01", "100", "100.01", "99.99"), "double_top"),  # Too shallow
        (("100", "110", "100", "110.5", "100", "110", "95"), "head_and_shoulders"),
        (("100", "110", "100", "115", "103", "110", "95"), "head_and_shoulders"),
        (("100", "110", "100", "115", "100", "112", "95"), "head_and_shoulders"),
    ],
)
def test_near_patterns_rejected(pivots, kind):
    result = snap(zigzag_candles(pivots, leg=4))
    assert not any(p.type == kind for p in result.chart_patterns)


def test_pattern_span_limit():
    c = zigzag_candles(("100", "110", "100", "110", "95"), leg=4)
    p = PatternLiquidityParameters(pattern_max_span_candles=7)
    assert not snap(c, parameters=p).chart_patterns


def test_every_prefix_is_identical_with_future_data_and_deterministic_ids():
    candles = zigzag_candles(
        ("100", "110", "100", "115", "100", "110", "95", "112"), leg=4
    )
    for n in range(1, len(candles) + 1):
        at = candles[n - 1].timestamp + INTERVAL
        past = snap(candles[:n], at=at)
        assert past == snap(candles, at=at)
        assert past.to_json_dict() == snap(candles, at=at).to_json_dict()
        events = past.events()
        assert len({e.id for e in events}) == len(events)
        assert all(e.known_at <= at for e in events)
        assert events == tuple(sorted(events, key=lambda e: (e.known_at, e.id)))
        json.dumps(past.to_json_dict())


def test_gaps_do_not_create_breakouts_sweeps_or_retests():
    c = prefix() + (bar(6, 112), bar(7, 109))
    result = snap(c)
    assert result.status == "incomplete"
    assert result.completeness.missing_candle_count == 1
    assert not result.breakouts and not result.sweeps and not result.retests
    # A pending two-candle confirmation must not jump a missing interval.
    c = prefix() + (bar(5, 112), bar(7, 113))
    assert not snap(
        c, parameters=PatternLiquidityParameters(breakout_confirmation_candles=2)
    ).breakouts
    # Confirmed breakout retained, but future failure/retest across a gap rejected.
    c = prefix() + (bar(5, 112), bar(7, 109))
    result = snap(c)
    assert result.breakouts and not result.failed_breakouts and not result.retests
    assert result.breakouts == snap(c[:6]).breakouts


def test_gap_blocks_patterns_clusters_and_transitions():
    c = zigzag_candles(("100", "110", "100", "110", "95"), leg=4)
    result = snap(c[:8] + c[9:])
    assert not result.chart_patterns and not result.equal_levels
    formed = snap(c[:15]).chart_patterns
    assert formed
    gapped = snap(c[:15] + c[16:])
    assert gapped.chart_patterns == formed
    trailing = snap(c[:15], at=EPOCH + 18 * INTERVAL)
    assert trailing.completeness.missing_candles_after_latest_stored == 3
    assert trailing.status == "incomplete"


def test_insufficient_data_and_invalid_inputs():
    result = snap(())
    assert result.status == "insufficient" and not result.events()
    assert "no_stored_candles" in result.reasons
    assert snap(prefix()[:2]).status == "insufficient"
    with pytest.raises(ValueError, match="timezone-aware"):
        snap(prefix(), at=EPOCH.replace(tzinfo=None))
    with pytest.raises(ValueError, match="instrument"):
        analyze_patterns(
            prefix(),
            exchange="other",
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            as_of=EPOCH + 5 * INTERVAL,
        )
    with pytest.raises(ValueError, match="invalid source"):
        snap((replace(bar(0, 100), high=D(1)),))


@pytest.mark.parametrize(
    "values",
    [
        {"breakout_confirmation_candles": 0},
        {"failure_window_candles": True},
        {"equal_min_members": 1},
        {"equal_min_members": 41},
        {"equal_tolerance_pct": 0},
        {"breakout_tolerance_pct": "NaN"},
        {"retest_hold_pct": -1},
        {"pattern_min_depth_pct": 0},
        {"head_min_prominence_pct": 0},
        {"sweep_penetration_pct": 100},
        {"pattern_max_span_candles": 2.5},
    ],
)
def test_parameter_validation(values):
    with pytest.raises((ValueError, TypeError)):
        PatternLiquidityParameters(**values)


def test_step3_source_and_context_unchanged_and_no_symbol_hardcoding():
    candles = prefix() + (bar(5, 112),)
    original = tuple(candles)
    at = candles[-1].timestamp + INTERVAL
    before = analyze_candles(candles, interval=INTERVAL, as_of=at)
    result = snap(candles)
    assert result.structure == before
    assert analyze_candles(candles, interval=INTERVAL, as_of=at) == before
    assert candles == original
    other = tuple(replace(c, symbol="ETH/EUR", exchange="offline") for c in candles)
    other_result = analyze_patterns(
        other, exchange="offline", symbol="ETH/EUR", timeframe=TIMEFRAME, as_of=at
    )
    assert len(other_result.breakouts) == len(result.breakouts)
    assert {e.id for e in other_result.events()}.isdisjoint(
        e.id for e in result.events()
    )


def test_zones_active_ranges_and_available_numeric_context():
    candles = zigzag_candles(
        ("100", "104", "100", "104", "100", "104", "100", "108"), leg=4
    )
    sp = MarketStructureParameters(ranges=RangeParameters(min_span_candles=8))
    result = snap(candles, structure_parameters=sp)
    assert {b.reference.type for b in result.breakouts} >= {
        "swing_high",
        "zone",
        "range_high",
    }
    last = result.breakouts[-1]
    assert last.penetration_atr is not None
    assert last.volatility.available and last.volume.sufficient
    assert last.volume.current_volume == 10


def test_rolling_equal_cluster_regrouping_is_never_backdated():
    candles = zigzag_candles(
        ("100", "110", "100", "110.2", "100", "110.4", "100", "113", "100"), leg=3
    )
    p = PatternLiquidityParameters(equal_lookback_swings=3)
    full = snap(candles, parameters=p)
    # Evicting the oldest anchor can group old swings that were not grouped before.
    assert any(c.known_at > c.latest_member_confirmed_at for c in full.equal_levels)
    for n in range(1, len(candles) + 1):
        at = candles[n - 1].timestamp + INTERVAL
        past = snap(candles[:n], parameters=p)
        assert past.events() == tuple(e for e in full.events() if e.known_at <= at)


def test_event_history_is_append_only_even_after_terminal_transitions():
    candles = zigzag_candles(
        ("100", "110", "100", "115", "100", "110", "95", "112"), leg=4
    )
    full = snap(candles)
    for n in range(1, len(candles) + 1):
        at = candles[n - 1].timestamp + INTERVAL
        assert snap(candles[:n]).events() == tuple(
            e for e in full.events() if e.known_at <= at
        )


def test_zero_prices_are_explicitly_unsupported_not_fabricated():
    candle = replace(bar(0, 1), low=D(0))
    with pytest.raises(ValueError, match="strictly positive OHLC"):
        snap((candle,))
    assert candle.low == 0
