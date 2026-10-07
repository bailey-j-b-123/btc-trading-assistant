"""Offline rule tests use real source types; mocked contexts isolate each gate."""

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EPOCH, INTERVAL
from test_pattern_liquidity import bar, mirrored, prefix, snap, swing_events

from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.market_data.types import CandleGap
from trading_assistant.market_structure.higher_timeframe import HigherTimeframeContext
from trading_assistant.market_structure.ranges import RangeDetection, RangeParameters
from trading_assistant.market_structure.trend import TrendDirection, TrendReason
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    ChartPattern,
    FailedBreakout,
    PatternGeometry,
    Retest,
    Sweep,
)
from trading_assistant.setup_qualification import (
    EvidenceStatus,
    QualificationFrame,
    QualificationParameters,
    RuleOutcome,
    SetupFamily,
    SetupState,
    enumerate_qualifications,
    qualify,
)


def at(i):
    return EPOCH + i * INTERVAL


def breakout(mirror=False):
    candles = prefix() + (bar(5, 112),)
    return swing_events(snap(mirrored(candles) if mirror else candles).breakouts)[0]


def held(seed, i=7, state="held"):
    candle = bar(i - 1, 111 if seed.direction == "bullish" else 89)
    return Retest(
        f"{seed.id}:{state}:{i}",
        seed,
        state,
        candle,
        at(i),
        D(0),
        i - 6,
        (candle,),
        seed.parameters,
    )


def sweep(mirror=False, range_seed=False):
    candles = prefix() + (bar(5, 109, high=112),)
    event = swing_events(snap(mirrored(candles) if mirror else candles).sweeps)[0]
    if range_seed:
        bounds = frozen_range()
        ref = replace(
            event.reference,
            id="range-reference",
            type="range_low" if mirror else "range_high",
            range=bounds,
        )
        event = replace(event, id="range-sweep", reference=ref)
    return event


def failure():
    source = snap(prefix() + (bar(5, 112), bar(6, 109)))
    return next(e for e in source.failed_breakouts if e.breakout.id == breakout().id)


def frozen_range():
    return RangeDetection(
        D(110),
        D(90),
        D(20),
        D(20),
        at(0),
        at(4),
        at(4),
        (at(2),),
        (at(3),),
        1,
        1,
        2,
        1,
        D(100),
        True,
        0,
        False,
        True,
        RangeParameters(),
        at(5),
    )


def frame(
    i,
    events=(),
    *,
    trend="bullish",
    close=112,
    volume="1.2",
    atr="2",
    active_range=None,
    higher=(),
    candles=None,
):
    # ``candles`` is an optional explicit series (used by fixtures that need a
    # displaced candle to produce a genuine structural reference); the default
    # flat series is unchanged, so existing callers keep identical behaviour.
    series = candles if candles is not None else tuple(bar(j, close) for j in range(i))
    source = snap(series, at=at(i))
    context = source.structure
    trend_value = (
        TrendDirection.NEUTRAL if trend == "unknown" else TrendDirection(trend)
    )
    reason = (
        TrendReason.INSUFFICIENT_SWINGS
        if trend == "unknown"
        else TrendReason.CONFLICTING_STRUCTURE
        if trend == "neutral"
        else TrendReason.HIGHER_HIGHS_AND_HIGHER_LOWS
        if trend == "bullish"
        else TrendReason.LOWER_HIGHS_AND_LOWER_LOWS
    )
    context = replace(
        context,
        trend=replace(context.trend, direction=trend_value, reason=reason),
        volume=replace(
            context.volume,
            sufficient=volume is not None,
            relative_volume=None if volume is None else D(volume),
        ),
        volatility=replace(
            context.volatility,
            available=atr is not None,
            atr_percent_of_price=None if atr is None else D(atr),
        ),
        range=replace(context.range, range=active_range),
    )
    source = replace(
        source,
        structure=context,
        breakouts=tuple(e for e in events if isinstance(e, Breakout)),
        failed_breakouts=tuple(e for e in events if isinstance(e, FailedBreakout)),
        sweeps=tuple(e for e in events if isinstance(e, Sweep)),
        retests=tuple(e for e in events if isinstance(e, Retest)),
        chart_patterns=tuple(e for e in events if isinstance(e, ChartPattern)),
        equal_levels=(),
    )
    return QualificationFrame(source, higher)


def result(frames, parameters=None):
    return qualify(frames, as_of=frames[-1].patterns.as_of, parameters=parameters)


def candidate(snapshot, seed):
    return next(s for s in snapshot.setups if s.seed_event_id == seed.id)


def rule_for(setup, name):
    return next(r for r in setup.rules if r.rule_id == name)


def confirmed_pattern(i=6, direction="bullish"):
    return ChartPattern(
        "pattern-event",
        "pattern-geometry",
        "double_bottom" if direction == "bullish" else "double_top",
        "confirmed",
        (),
        D(100),
        D(90),
        PatternGeometry(D(0), D(2), None, D(0), 5),
        at(2),
        at(5),
        at(i),
        at(i - 1),
        (),
        breakout().parameters,
    )


def test_quiet_market_no_setup_and_pattern_alone_cannot_seed():
    for events in ((), (confirmed_pattern(),)):
        snapshot = result([frame(6, events)])
        assert snapshot.state == SetupState.NO_SETUP
        assert not snapshot.setups
        assert snapshot.reasons == ("no_seed_confirmed_in_replayed_frames",)


@pytest.mark.parametrize("mirror", [False, True])
def test_watch_to_qualified_multiple_factors_and_audit(mirror):
    seed = breakout(mirror)
    direction = seed.direction
    close = 112 if not mirror else 88
    frames = [
        frame(6, (seed,), trend=direction, close=close),
        frame(7, (seed, held(seed)), trend=direction, close=close),
    ]
    history = enumerate_qualifications(frames, as_of=at(7))
    first, last = (candidate(s, seed) for s in history)
    assert first.state == SetupState.WATCH
    assert "held_retest" in first.pending_rules
    assert last.state == SetupState.QUALIFIED
    assert first.id == last.id
    assert {
        "seed_event",
        "held_retest",
        "structure",
        "volume",
        "volatility",
        "location",
        "lifecycle",
    } <= set(last.passed_rules)
    assert last.direction == direction
    assert last.source_timeframes == ("1h",)
    assert last.evidence[0].source_reference == seed.id
    assert last.evidence[0].observed_at == seed.candle.timestamp
    assert last.evidence[0].confirmed_at == seed.known_at
    assert all(
        e.confirmed_at is None or e.confirmed_at <= last.as_of for e in last.evidence
    )


@pytest.mark.parametrize(
    "change,rule_name,outcome",
    [
        ({"volume": None}, "volume", RuleOutcome.PENDING),
        ({"atr": None}, "volatility", RuleOutcome.PENDING),
        ({"trend": "unknown"}, "structure", RuleOutcome.PENDING),
        ({"volume": "0.9"}, "volume", RuleOutcome.FAIL),
        ({"atr": "0"}, "volatility", RuleOutcome.FAIL),
        ({"atr": "10.1"}, "volatility", RuleOutcome.FAIL),
        ({"trend": "bearish"}, "structure", RuleOutcome.FAIL),
        ({"trend": "neutral"}, "structure", RuleOutcome.FAIL),
    ],
)
def test_each_required_context_gate_blocks_qualification(change, rule_name, outcome):
    seed = breakout()
    setup = candidate(
        result([frame(6, (seed,)), frame(7, (seed, held(seed)), **change)]), seed
    )
    assert setup.state == SetupState.WATCH
    checked = rule_for(setup, rule_name)
    assert checked.required and checked.outcome == outcome
    assert checked.evidence[0].status == (
        EvidenceStatus.UNKNOWN
        if outcome == RuleOutcome.PENDING
        else EvidenceStatus.OPPOSING
    )


def test_watch_can_remain_watch_and_qualified_can_downgrade_and_requalify():
    seed = breakout()
    catalog = (seed, held(seed))
    frames = [
        frame(6, (seed,)),
        frame(7, catalog),
        frame(8, catalog, volume=None),
        frame(9, catalog, volume=None),
        frame(10, catalog),
    ]
    assert [
        candidate(s, seed).state for s in enumerate_qualifications(frames, as_of=at(10))
    ] == [
        SetupState.WATCH,
        SetupState.QUALIFIED,
        SetupState.WATCH,
        SetupState.WATCH,
        SetupState.QUALIFIED,
    ]


@pytest.mark.parametrize("kind", ["close", "failure", "retest"])
def test_invalidation_is_terminal_even_when_price_recovers(kind):
    seed = breakout()
    event = (
        failure()
        if kind == "failure"
        else held(seed, state="failed")
        if kind == "retest"
        else None
    )
    catalog = (seed, event) if event else (seed,)
    history = enumerate_qualifications(
        [frame(6, (seed,)), frame(7, catalog, close=109), frame(8, catalog)],
        as_of=at(8),
    )
    dead = candidate(history[1], seed)
    assert dead.state == SetupState.NO_SETUP
    assert (
        dead.terminal_reason
        == {
            "close": "opposite_close_through_reference",
            "failure": "failed_breakout",
            "retest": "failed_retest",
        }[kind]
    )
    assert candidate(history[2], seed).terminal_reason == dead.terminal_reason
    assert candidate(history[2], seed).ended_at == at(7)
    assert candidate(history[2], seed).rules == dead.rules


@pytest.mark.parametrize("kind", ["continuation", "reversal", "range"])
def test_expiry_for_every_family_exact_boundary(kind):
    seed = breakout() if kind == "continuation" else sweep(range_seed=kind == "range")
    close = 112 if kind == "continuation" else 108
    p = QualificationParameters(
        continuation_max_bars=1, reversal_max_bars=1, range_max_bars=1
    )
    frames = [frame(i, (seed,), close=close) for i in (6, 7, 8, 9)]
    history = enumerate_qualifications(frames, as_of=at(9), parameters=p)
    assert candidate(history[1], seed).state == SetupState.WATCH
    for snapshot in history[2:]:
        assert candidate(snapshot, seed).state == SetupState.NO_SETUP
        assert candidate(snapshot, seed).terminal_reason == "maximum_bars_elapsed"
        assert candidate(snapshot, seed).ended_at == at(8)


@pytest.mark.parametrize("use_failure", [False, True])
def test_liquidity_reversal_needs_later_different_reference_breakout(use_failure):
    seed = failure() if use_failure else sweep()
    i = 7 if use_failure else 6
    confirm = replace(
        breakout(True),
        id="later-reversal-break",
        known_at=at(i + 1),
        candle=bar(i, 88),
        confirmation_candles=(bar(i, 88),),
    )
    frames = [
        frame(i, (seed,), close=109, trend="neutral"),
        frame(i + 1, (seed, confirm), close=108, trend="neutral"),
    ]
    first = candidate(result(frames[:1]), seed)
    last = candidate(result(frames), seed)
    assert first.state == SetupState.WATCH
    assert first.family == SetupFamily.LIQUIDITY_REVERSAL
    assert "reversal_breakout" in first.pending_rules
    assert last.state == SetupState.QUALIFIED
    assert rule_for(last, "structure").evidence[0].status == EvidenceStatus.NEUTRAL


@pytest.mark.parametrize("mirror", [False, True])
def test_range_rejection_requires_active_location_and_followthrough(mirror):
    seed = sweep(mirror, range_seed=True)
    active = frozen_range()
    close = 92 if mirror else 108
    frames = [
        frame(
            6, (seed,), close=seed.candle.close, active_range=active, trend="neutral"
        ),
        frame(7, (seed,), close=close, active_range=active, trend="neutral"),
    ]
    setup = candidate(result(frames), seed)
    assert setup.family == SetupFamily.RANGE_REVERSAL
    assert setup.state == SetupState.QUALIFIED
    assert "range_followthrough" in setup.passed_rules
    no_range = candidate(
        result([frames[0], frame(7, (seed,), close=close, trend="neutral")]), seed
    )
    assert no_range.state == SetupState.WATCH
    assert "active_range" in no_range.pending_rules
    no_progress = candidate(
        result(
            [
                frames[0],
                frame(
                    7,
                    (seed,),
                    close=seed.candle.close,
                    active_range=active,
                    trend="neutral",
                ),
            ]
        ),
        seed,
    )
    assert no_progress.state == SetupState.WATCH
    dead = candidate(
        result([*frames, frame(8, (seed,), close=111 if mirror else 89)]), seed
    )
    assert dead.state == SetupState.NO_SETUP
    assert dead.terminal_reason == "close_outside_frozen_range"


def higher_context(i, *, trend="bullish", complete=True):
    base = frame(i, trend=trend)
    expected = latest_closed_candle_open_time(at(i), "4h")
    analysis = replace(
        base.patterns.structure,
        window_end_timestamp=expected,
        volume=replace(
            base.patterns.structure.volume, latest_candle_timestamp=expected
        ),
        volatility=replace(
            base.patterns.structure.volatility, latest_candle_timestamp=expected
        ),
    )
    completeness = replace(
        base.patterns.completeness,
        window_end=expected,
        expected_latest_closed_open_time=expected,
        complete=complete,
    )
    return HigherTimeframeContext("4h", True, None, analysis, completeness)


@pytest.mark.parametrize(
    "required,trend,complete,expected",
    [
        (False, None, True, SetupState.QUALIFIED),
        (True, None, True, SetupState.WATCH),
        (True, "bullish", True, SetupState.QUALIFIED),
        (False, "bearish", True, SetupState.WATCH),
        (True, "bearish", True, SetupState.WATCH),
        (False, "neutral", True, SetupState.QUALIFIED),
        (True, "neutral", True, SetupState.WATCH),
        (True, "bullish", False, SetupState.WATCH),
        (True, "unknown", True, SetupState.WATCH),
    ],
)
def test_higher_timeframe_alignment_unknown_and_opposing_veto(
    required, trend, complete, expected
):
    seed = breakout()
    p = QualificationParameters(
        higher_timeframes=("4h",), require_higher_timeframe_alignment=required
    )
    higher = (
        () if trend is None else (higher_context(7, trend=trend, complete=complete),)
    )
    setup = candidate(
        result([frame(6, (seed,)), frame(7, (seed, held(seed)), higher=higher)], p),
        seed,
    )
    assert setup.state == expected
    assert setup.source_timeframes == ("1h", "4h")
    htf = rule_for(setup, "higher_timeframe:4h")
    if trend == "bearish":
        assert htf.veto
    if trend is None or not complete or trend == "unknown":
        assert htf.outcome == RuleOutcome.PENDING


def test_pattern_support_is_optional_and_cannot_replace_retest():
    seed = breakout()
    shape = confirmed_pattern()
    frames = [frame(6, (seed, shape)), frame(7, (seed, shape))]
    setup = candidate(result(frames), seed)
    assert setup.state == SetupState.WATCH
    assert (
        rule_for(setup, "classical_pattern:pattern-event").outcome == RuleOutcome.PASS
    )
    opposing = replace(shape, type="double_top")
    frames = [frame(6, (seed, opposing)), frame(7, (seed, opposing, held(seed)))]
    setup = candidate(result(frames), seed)
    assert setup.state == SetupState.QUALIFIED
    assert "classical_pattern:pattern-event" in setup.failed_rules


def test_gap_missing_frames_and_missing_current_candle_never_bridge():
    seed = breakout()
    skipped = candidate(
        result([frame(6, (seed,)), frame(8, (seed, held(seed, 8)))]), seed
    )
    assert skipped.terminal_reason == "missing_replay_frames"
    missing = frame(7, (seed,))
    missing = replace(
        missing,
        patterns=replace(
            missing.patterns,
            structure=replace(
                missing.patterns.structure,
                window_end_timestamp=at(5),
                volume=replace(
                    missing.patterns.structure.volume, latest_candle_timestamp=at(5)
                ),
                volatility=replace(
                    missing.patterns.structure.volatility, latest_candle_timestamp=at(5)
                ),
            ),
        ),
    )
    assert (
        candidate(result([frame(6, (seed,)), missing]), seed).terminal_reason
        == "missing_current_candle"
    )
    gapped = frame(7, (seed, held(seed)))
    gapped = replace(
        gapped,
        patterns=replace(
            gapped.patterns,
            completeness=replace(
                gapped.patterns.completeness,
                gaps=(CandleGap(at(6), at(6), 1),),
                complete=False,
            ),
        ),
    )
    assert (
        candidate(result([frame(6, (seed,)), gapped]), seed).terminal_reason
        == "source_candle_gap"
    )


def test_old_gap_does_not_permanently_block_new_segment():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    frames = [
        replace(
            f,
            patterns=replace(
                f.patterns,
                completeness=replace(
                    f.patterns.completeness,
                    gaps=(CandleGap(at(1), at(1), 1),),
                    complete=False,
                ),
            ),
        )
        for f in frames
    ]
    assert candidate(result(frames), seed).state == SetupState.QUALIFIED
    assert result(frames).status == "incomplete"


def test_stable_ids_fingerprints_snapshots_source_safety_and_json_detachment():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    before = tuple(f.patterns.to_json_dict() for f in frames)
    first = result(frames)
    second = result(tuple(frames))
    assert first == second
    assert json.dumps(first.to_json_dict(), sort_keys=True) == json.dumps(
        second.to_json_dict(), sort_keys=True
    )
    assert candidate(first, seed).id == candidate(result(frames[:1]), seed).id
    with pytest.raises(FrozenInstanceError):
        first.state = SetupState.NO_SETUP
    with pytest.raises(FrozenInstanceError):
        first.setups[0].rules[0].evidence[0].reason = "edited"
    projected = first.to_json_dict()
    projected["setups"][0]["rules"].clear()
    assert first == second
    assert tuple(f.patterns.to_json_dict() for f in frames) == before


def test_config_intentionally_changes_results_and_namespace():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    original = result(frames)
    strict = result(frames, QualificationParameters(min_relative_volume="2"))
    assert original.state == SetupState.QUALIFIED
    assert strict.state == SetupState.WATCH
    assert original.config_fingerprint != strict.config_fingerprint
    assert candidate(original, seed).id != candidate(strict, seed).id
    assert (
        result(frames, QualificationParameters(min_relative_volume="1.000")) == original
    )


def test_chronological_filter_after_warmup_future_frames_ignored():
    seed = breakout()
    frames = [
        frame(6, (seed,)),
        frame(7, (seed, held(seed))),
        frame(8, (seed, held(seed)), close=109),
    ]
    historical = enumerate_qualifications(frames[:2], as_of=at(7))
    assert enumerate_qualifications(frames, as_of=at(7)) == historical
    assert (
        enumerate_qualifications(frames, as_of=at(7), known_since=at(7))
        == historical[1:]
    )
    assert qualify(frames, as_of=at(7)) == historical[-1]
    assert not result([frames[1]]).setups  # no retrospective seed from a latest catalog
    with pytest.raises(ValueError, match="chronological"):
        enumerate_qualifications(frames[::-1], as_of=at(8))
    with pytest.raises(ValueError, match="chronological"):
        enumerate_qualifications([frames[0], frames[0]], as_of=at(6))
    with pytest.raises(ValueError, match="exactly at as_of"):
        qualify(frames[:1], as_of=at(7))
    with pytest.raises(ValueError, match="known_since"):
        enumerate_qualifications(frames, as_of=at(7), known_since=at(8))


def test_actual_step4_candle_prefix_invariance_at_every_close():
    candles = prefix() + (
        bar(5, 112),
        bar(6, 111, high=112, low=109),
        bar(7, 108),
        bar(8, 113),
    )
    online = [
        QualificationFrame(snap(candles[:i], at=at(i)))
        for i in range(1, len(candles) + 1)
    ]
    extended = [
        QualificationFrame(snap(candles, at=at(i))) for i in range(1, len(candles) + 1)
    ]
    for i in range(1, len(candles) + 1):
        assert qualify(online[:i], as_of=at(i)) == qualify(extended, as_of=at(i))
    assert any(
        s.setups for s in enumerate_qualifications(online, as_of=at(len(candles)))
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"continuation_max_bars": 0},
        {"range_max_bars": True},
        {"reversal_max_bars": 1.5},
        {"min_relative_volume": 0},
        {"max_atr_percent": "NaN"},
        {"max_atr_percent": "Infinity"},
        {"higher_timeframes": ["4h"]},
        {"higher_timeframes": ("4h", "4h")},
        {"higher_timeframes": ("nonsense",)},
        {"require_higher_timeframe_alignment": True},
        {"require_higher_timeframe_alignment": 1},
    ],
)
def test_config_validation(kwargs):
    with pytest.raises((ValueError, TypeError)):
        QualificationParameters(**kwargs)


def test_rejects_future_or_misaligned_context_and_historical_source_rewrites():
    seed = breakout()
    with pytest.raises(ValueError, match="after frame"):
        result([frame(6, (replace(seed, known_at=at(7)),))])
    with pytest.raises(ValueError, match="after frame"):
        future_ref = replace(seed.reference, known_at=at(7))
        result([frame(6, (replace(seed, reference=future_ref),))])
    with pytest.raises(ValueError, match="append-only"):
        result([frame(6, (seed,)), frame(7, (replace(seed, penetration=D(999)),))])
    with pytest.raises(ValueError, match="append-only"):
        result([frame(6, (seed,)), frame(7)])
    with pytest.raises(ValueError, match="longer"):
        result([frame(6, (seed,))], QualificationParameters(higher_timeframes=("1h",)))
    p = QualificationParameters(higher_timeframes=("4h",))
    with pytest.raises(ValueError, match="share as_of"):
        result([frame(6, (seed,), higher=(higher_context(7),))], p)
    bad = frame(6, (seed,))
    bad = replace(
        bad,
        patterns=replace(
            bad.patterns, structure=replace(bad.patterns.structure, as_of=at(7))
        ),
    )
    with pytest.raises(ValueError, match="frame as_of"):
        result([bad])


def test_bullish_liquidity_reversal_and_opposite_close_invalidation():
    seed = sweep(True)
    confirm = replace(
        breakout(),
        id="later-bullish-break",
        known_at=at(7),
        candle=bar(6, 112),
        confirmation_candles=(bar(6, 112),),
    )
    frames = [frame(6, (seed,), close=91), frame(7, (seed, confirm), close=92)]
    setup = candidate(result(frames), seed)
    assert setup.direction == "bullish"
    assert setup.state == SetupState.QUALIFIED
    frames.append(frame(8, (seed, confirm), close=89))
    assert (
        candidate(result(frames), seed).terminal_reason
        == "opposite_close_through_reference"
    )


@pytest.mark.parametrize(
    "confirmation_kind", ["same_reference", "same_time", "wrong_direction"]
)
def test_reversal_confirmation_must_be_later_directional_and_distinct(
    confirmation_kind,
):
    seed = sweep()
    confirm = replace(breakout(True), id="reversal-check", known_at=at(7))
    if confirmation_kind == "same_reference":
        confirm = replace(confirm, reference=seed.reference)
    elif confirmation_kind == "same_time":
        confirm = replace(confirm, known_at=at(6))
    else:
        confirm = replace(confirm, direction="bullish")
    first_events = (seed, confirm) if confirm.known_at == at(6) else (seed,)
    frames = [
        frame(6, first_events, close=109),
        frame(7, (seed, confirm), close=108, trend="neutral"),
    ]
    setup = candidate(result(frames), seed)
    assert setup.state == SetupState.WATCH
    assert "reversal_breakout" in setup.pending_rules


def test_observed_retest_not_held_and_qualified_candidate_expires():
    seed = breakout()
    observed = held(seed, state="observed")
    frames = [frame(6, (seed,)), frame(7, (seed, observed))]
    assert candidate(result(frames), seed).state == SetupState.WATCH
    frames = [
        frame(6, (seed,)),
        frame(7, (seed, held(seed))),
        frame(8, (seed, held(seed))),
    ]
    p = QualificationParameters(continuation_max_bars=1)
    history = enumerate_qualifications(frames, as_of=at(8), parameters=p)
    assert candidate(history[1], seed).state == SetupState.QUALIFIED
    assert candidate(history[2], seed).terminal_reason == "maximum_bars_elapsed"


def test_threshold_equality_and_reference_band_interior():
    seed = breakout()
    frames = [
        frame(6, (seed,)),
        frame(7, (seed, held(seed)), volume="1", atr="10", close=110),
    ]
    assert candidate(result(frames), seed).state == SetupState.QUALIFIED
    seed = replace(
        seed, reference=replace(seed.reference, band_low=D(109), band_high=D(111))
    )
    setup = candidate(
        result([frame(6, (seed,)), frame(7, (seed, held(seed)), close=110)]), seed
    )
    assert setup.state == SetupState.WATCH
    assert "location" in setup.failed_rules
    assert setup.terminal_reason is None


def test_context_timestamp_guards_and_source_parameter_changes():
    seed = breakout()
    f = frame(6, (seed,))
    future_metrics = replace(
        f.patterns.structure,
        volume=replace(f.patterns.structure.volume, latest_candle_timestamp=at(7)),
    )
    with pytest.raises(ValueError, match="after frame"):
        result([replace(f, patterns=replace(f.patterns, structure=future_metrics))])
    current_open = replace(
        f.patterns.structure,
        volume=replace(f.patterns.structure.volume, latest_candle_timestamp=at(6)),
    )
    with pytest.raises(ValueError, match="source window"):
        result([replace(f, patterns=replace(f.patterns, structure=current_open))])
    later = frame(7, (seed,))
    changed = replace(later.patterns.parameters, retest_window_candles=5)
    with pytest.raises(ValueError, match="parameters cannot change"):
        result(
            [f, replace(later, patterns=replace(later.patterns, parameters=changed))]
        )
    with pytest.raises(ValueError, match="duplicate source event IDs"):
        result([frame(6, (seed, seed))])


def test_absent_ratio_and_close_are_unknown_not_zero_or_neutral():
    seed = breakout()
    f = frame(7, (seed, held(seed)))
    context = replace(
        f.patterns.structure,
        volume=replace(
            f.patterns.structure.volume, sufficient=True, relative_volume=None
        ),
        volatility=replace(f.patterns.structure.volatility, latest_close=None),
    )
    f = replace(f, patterns=replace(f.patterns, structure=context))
    setup = candidate(result([frame(6, (seed,)), f]), seed)
    assert setup.state == SetupState.WATCH
    assert {"volume", "location"} <= set(setup.pending_rules)
    assert setup.terminal_reason is None
