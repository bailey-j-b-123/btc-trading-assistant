"""Offline deterministic Step 6 tests: real Step 5 replay plus adversarial inputs.

Every actionable scenario runs through the existing Step 3-5 APIs on synthetic
candles (no network, no exchange). Hand-built variants use ``dataclasses.replace``
to construct new objects — upstream facts are never mutated — so the planner's
defensive contradiction handling is exercised against genuinely inconsistent
inputs.
"""

import json
import re
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EXCHANGE, SYMBOL, Candle
from test_pattern_liquidity import bar, prefix, swing_events
from test_setup_qualification import (
    at,
    breakout,
    candidate,
    frame,
    frozen_range,
    held,
    result,
    sweep,
)

from trading_assistant.market_data.types import CandleGap
from trading_assistant.market_structure.trend import TrendDirection, TrendReason
from trading_assistant.pattern_liquidity import analyze_patterns
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    EqualLevelCluster,
    FailedBreakout,
    Retest,
    Sweep,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.setup_qualification import (
    QualificationFrame,
    QualificationParameters,
    RuleOutcome,
    SetupFamily,
    SetupState,
    enumerate_qualifications,
)
from trading_assistant.trade_planning import (
    BASE_RULES,
    EntryMode,
    PlanningParameters,
    PlanState,
    StopBufferMode,
    plan_trade,
)

PLAN_KEYS = {
    "as_of",
    "config_fingerprint",
    "direction",
    "entry",
    "excluded_targets",
    "exchange",
    "family",
    "id",
    "invalidation",
    "missing_inputs",
    "planning_rules_version",
    "reasons",
    "risk_per_unit",
    "rules",
    "setup_as_of",
    "setup_config_fingerprint",
    "setup_created_at",
    "setup_id",
    "source_timeframes",
    "state",
    "state_detail",
    "stop",
    "symbol",
    "targets",
    "timeframe",
}

RICH = prefix() + (bar(5, 112), bar(6, 113), bar(7, 111), bar(8, "110.5"))


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def relabel(value, *, symbol=SYMBOL):
    """Deep copy of any evidence structure retargeted to another symbol."""
    if isinstance(value, Candle):
        return replace(value, symbol=symbol)
    if is_dataclass(value) and not isinstance(value, type):
        return replace(
            value,
            **{
                field.name: relabel(getattr(value, field.name), symbol=symbol)
                for field in fields(value)
            },
        )
    if isinstance(value, tuple):
        return tuple(relabel(item, symbol=symbol) for item in value)
    return value


def psnap(candles, at_, symbol=SYMBOL):
    return analyze_patterns(
        tuple(relabel(c, symbol=symbol) for c in candles),
        exchange=EXCHANGE,
        symbol=symbol,
        timeframe="1h",
        as_of=at_,
    )


def rich_frame(
    i,
    candles,
    events=(),
    *,
    trend="bullish",
    symbol=SYMBOL,
    equal_levels=(),
    full=False,
):
    """A frame with real Step 3 swings/zones, qualification-facing context set.

    Mirrors the Step 5 test ``frame`` helper, except the candle series is chosen
    so confirmed swings actually exist (needed to exercise structural target
    selection). ``full`` passes the *entire* series (including future bars) and
    relies on as-of filtering, proving prefix invariance through the planner.
    """
    source = psnap(candles if full else candles[:i], at(i), symbol=symbol)
    context = source.structure
    trend_reason = (
        TrendReason.CONFLICTING_STRUCTURE
        if trend == "neutral"
        else TrendReason.HIGHER_HIGHS_AND_HIGHER_LOWS
        if trend == "bullish"
        else TrendReason.LOWER_HIGHS_AND_LOWER_LOWS
    )
    context = replace(
        context,
        trend=replace(
            context.trend, direction=TrendDirection(trend), reason=trend_reason
        ),
        volume=replace(context.volume, sufficient=True, relative_volume=D("1.2")),
        volatility=replace(
            context.volatility, available=True, atr=D(2), atr_percent_of_price=D(2)
        ),
    )
    source = replace(
        source,
        structure=context,
        breakouts=tuple(e for e in events if isinstance(e, Breakout)),
        failed_breakouts=tuple(e for e in events if isinstance(e, FailedBreakout)),
        sweeps=tuple(e for e in events if isinstance(e, Sweep)),
        retests=tuple(e for e in events if isinstance(e, Retest)),
        chart_patterns=(),
        equal_levels=tuple(equal_levels),
    )
    return QualificationFrame(source, ())


def qualified_continuation(upto=7, symbol=SYMBOL, full=False):
    """Rich-candle continuation setup, planned at ``at(upto)``."""
    seed_source = psnap(RICH[:6], at(6), symbol=symbol)
    seed = swing_events(seed_source.breakouts)[0]
    events_by_frame = {
        6: (seed,),
        7: (seed, relabel(held(seed, i=7), symbol=symbol)),
    }
    for later in (8, 9):
        events_by_frame[later] = events_by_frame[7]
    candles = RICH
    frames = [
        rich_frame(i, candles, events_by_frame[i], symbol=symbol, full=full)
        for i in range(6, upto + 1)
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(upto))[-1]
    setup = candidate(snapshot, seed)
    return snapshot, frames[-1], setup, seed


def qualified(mirror=False):
    seed = breakout(mirror)
    direction = "bullish" if not mirror else "bearish"
    close = 112 if not mirror else 88
    frames = [
        frame(6, (seed,), trend=direction, close=close),
        frame(7, (seed, held(seed)), trend=direction, close=close),
    ]
    snapshot = result(frames)
    return snapshot, frames[-1], candidate(snapshot, seed)


def qualified_reversal_long():
    seed = sweep(True)
    confirm = replace(
        breakout(),
        id="later-bullish-break",
        known_at=at(7),
        candle=bar(6, 112),
        confirmation_candles=(bar(6, 112),),
    )
    frames = [frame(6, (seed,), close=91), frame(7, (seed, confirm), close=92)]
    snapshot = result(frames)
    return snapshot, frames[-1], candidate(snapshot, seed), seed, confirm


def qualified_reversal_short():
    seed = sweep()
    confirm = replace(
        breakout(True),
        id="later-reversal-break",
        known_at=at(7),
        candle=bar(6, 88),
        confirmation_candles=(bar(6, 88),),
    )
    frames = [
        frame(6, (seed,), close=109, trend="neutral"),
        frame(7, (seed, confirm), close=108, trend="neutral"),
    ]
    snapshot = result(frames)
    return snapshot, frames[-1], candidate(snapshot, seed), seed, confirm


def qualified_range(mirror=False):
    seed = sweep(mirror, range_seed=True)
    active = frozen_range()
    close = 92 if mirror else 108
    frames = [
        frame(
            6, (seed,), close=seed.candle.close, active_range=active, trend="neutral"
        ),
        frame(7, (seed,), close=close, active_range=active, trend="neutral"),
    ]
    snapshot = result(frames)
    return snapshot, frames[-1], candidate(snapshot, seed), seed


def with_atr(fr, value="2"):
    volatility = replace(
        fr.patterns.structure.volatility,
        available=True,
        atr=D(value),
        atr_percent_of_price=D(value),
    )
    return replace(
        fr,
        patterns=replace(
            fr.patterns, structure=replace(fr.patterns.structure, volatility=volatility)
        ),
    )


def rule_for(plan, name):
    return next(r for r in plan.rules if r.rule_id == name)


# ---------------------------------------------------------------------------
# Only QUALIFIED candidates can plan
# ---------------------------------------------------------------------------


def test_no_setup_and_unknown_id_cannot_plan():
    quiet_frame = frame(6, ())
    quiet = result([quiet_frame])
    assert quiet.state.value == "NO_SETUP" and not quiet.setups
    plan = plan_trade(snapshot=quiet, frame=quiet_frame, setup_id="nope")
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("setup_not_in_snapshot",)
    assert plan.state_detail == "setup_not_in_snapshot"
    assert plan.setup_id == "nope"
    assert plan.family is None and plan.direction is None
    assert plan.entry.value is None
    assert plan.stop.value is None
    assert plan.risk_per_unit is None
    assert plan.targets == ()
    assert plan.actionable is False
    pending = {r.rule_id for r in plan.rules if r.outcome is RuleOutcome.PENDING}
    assert pending >= {"seed_evidence_available", "target_levels_valid"}
    assert rule_for(plan, "setup_usable").outcome is RuleOutcome.FAIL
    assert "not present in the snapshot" in rule_for(plan, "setup_usable").reason


def test_watch_cannot_produce_actionable_plan():
    seed = breakout()
    watch_frames = [frame(6, (seed,), close=112)]
    watch = result(watch_frames)
    setup = candidate(watch, seed)
    assert setup.state is SetupState.WATCH
    plan = plan_trade(snapshot=watch, frame=watch_frames[-1], setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("setup_not_qualified",)
    assert "QUALIFIED never implies PLANNABLE" in rule_for(plan, "setup_usable").reason


def test_gate_failed_watch_and_terminal_and_expired_cannot_plan():
    seed = breakout()
    catalog = (seed, held(seed))
    demoted_frames = [
        frame(6, (seed,)),
        frame(7, catalog),
        frame(8, catalog, volume=None),
    ]
    demoted = enumerate_qualifications(demoted_frames, as_of=at(8))[-1]
    plan = plan_trade(
        snapshot=demoted, frame=demoted_frames[-1], setup_id=demoted.setups[0].id
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("setup_not_qualified",)

    invalidated = enumerate_qualifications(
        [frame(6, (seed,)), frame(7, catalog), frame(8, catalog, close=109)],
        as_of=at(8),
    )[-1]
    plan = plan_trade(
        snapshot=invalidated,
        frame=frame(8, catalog, close=109),
        setup_id=invalidated.setups[0].id,
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("terminal_source_setup",)
    assert "opposite_close_through_reference" in rule_for(plan, "setup_usable").reason

    parameters = QualificationParameters(continuation_max_bars=1)
    expiring = enumerate_qualifications(
        [frame(6, (seed,)), frame(7, catalog), frame(8, catalog)],
        as_of=at(8),
        parameters=parameters,
    )
    assert expiring[1].setups[0].state is SetupState.QUALIFIED
    assert expiring[2].setups[0].terminal_reason == "maximum_bars_elapsed"
    plan = plan_trade(
        snapshot=expiring[2],
        frame=frame(8, catalog),
        setup_id=expiring[2].setups[0].id,
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("terminal_source_setup",)
    # the earlier historical snapshot still plans its own as_of (immutable record)
    historic = plan_trade(
        snapshot=expiring[1],
        frame=frame(7, catalog),
        setup_id=expiring[1].setups[0].id,
    )
    assert historic.state is PlanState.PLANNABLE
    assert historic.as_of == at(7)


# ---------------------------------------------------------------------------
# All three families, long and short
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mirror", [False, True])
def test_continuation_long_and_short_plannable(mirror):
    snapshot, planning_frame, setup = qualified(mirror)
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.actionable
    entry = D(112) if not mirror else D(88)
    invalidation = D(110) if not mirror else D(90)
    assert plan.entry.value == entry
    assert plan.invalidation.value == invalidation
    assert plan.stop.value == invalidation  # default no-buffer mode
    assert plan.risk_per_unit == 2
    (target,) = plan.targets
    assert target.level.value == (D(116) if not mirror else D(84))
    assert target.reward_per_unit == 4
    assert target.r_multiple == D("2.00000000")
    assert plan.direction == ("bullish" if not mirror else "bearish")
    assert plan.family is SetupFamily.BREAKOUT_RETEST
    assert {r.outcome for r in plan.rules} == {RuleOutcome.PASS}
    assert [r.rule_id for r in plan.rules] == list(BASE_RULES)
    if mirror:
        assert plan.stop.value > plan.entry.value  # short stop above entry
    else:
        assert plan.stop.value < plan.entry.value  # long stop below entry


def test_reversal_short_and_long_plannable():
    snapshot, planning_frame, setup, _seed, _confirm = qualified_reversal_short()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.family is SetupFamily.LIQUIDITY_REVERSAL
    assert plan.direction == "bearish"
    assert plan.entry.value == D(108)
    assert plan.invalidation.value == D(110)
    assert plan.stop.value == D(110) > plan.entry.value
    assert plan.risk_per_unit == 2
    (target,) = plan.targets
    assert target.level.value == D(104) < plan.entry.value  # short targets below
    assert target.reward_per_unit == 4
    assert target.r_multiple == D("2.00000000")

    snapshot, planning_frame, setup, _seed, _confirm = qualified_reversal_long()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.direction == "bullish"
    assert plan.entry.value == D(92)
    assert plan.invalidation.value == D(90)
    assert plan.stop.value == D(90) < plan.entry.value
    assert plan.risk_per_unit == 2
    assert plan.targets[0].level.value == D(96) > plan.entry.value


@pytest.mark.parametrize("mirror", [False, True])
def test_range_reversal_uses_opposite_boundary_as_structural_target(mirror):
    snapshot, planning_frame, setup, _seed = qualified_range(mirror)
    assert setup.family is SetupFamily.RANGE_REVERSAL
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.invalidation.value == (D(90) if mirror else D(110))
    assert plan.stop.value == plan.invalidation.value
    assert plan.risk_per_unit == 2
    (target,) = plan.targets
    assert target.is_structural  # frozen opposite range boundary, not a fallback
    assert target.level.value == (D(110) if mirror else D(90))
    expected_type = (
        "step4_reference_range_high" if mirror else "step4_reference_range_low"
    )
    assert target.level.source_type == expected_type
    assert target.reward_per_unit == 18
    assert target.r_multiple == D("9.00000000")


# ---------------------------------------------------------------------------
# Entry rules
# ---------------------------------------------------------------------------


def test_entry_mode_frozen_confirmation_per_family():
    params = PlanningParameters(entry_mode=EntryMode.FROZEN_CONFIRMATION)

    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot, frame=planning_frame, setup_id=setup.id, parameters=params
    )
    assert plan.state is PlanState.PLANNABLE
    assert plan.entry.value == D(111)  # held-retest candle close
    assert plan.entry.source_type == "step4_retest_close"
    assert plan.entry.source_id.endswith(":held:7")
    assert plan.risk_per_unit == 1

    snapshot, planning_frame, setup, _seed, confirm = qualified_reversal_short()
    plan = plan_trade(
        snapshot=snapshot, frame=planning_frame, setup_id=setup.id, parameters=params
    )
    assert plan.entry.value == D(88)  # confirmation breakout close
    assert plan.entry.source_type == "step4_breakout_close"
    assert plan.entry.source_id == confirm.id

    snapshot, planning_frame, setup, seed = qualified_range()
    plan = plan_trade(
        snapshot=snapshot, frame=planning_frame, setup_id=setup.id, parameters=params
    )
    assert plan.entry.value == seed.reclaim_close == D(109)
    assert plan.entry.source_type == "step4_sweep_reclaim_close"
    assert plan.invalidation.value == D(110)
    assert plan.risk_per_unit == 1


def test_frozen_entry_off_band_is_rejected_not_corrected():
    snapshot, _planning_frame, _setup, _seed, confirm = qualified_reversal_long()
    # Bullish setup: a confirmation whose own close print lies back beyond the
    # swept band must be rejected by the planner's defensive side check.
    assert snapshot.setups[0].direction == "bullish"
    broken = replace(confirm, breakout_close=D(85))
    frames = [
        frame(6, (sweep(True),), close=91),
        frame(7, (sweep(True), broken), close=92),
    ]
    snap7 = result(
        [
            frame(6, (sweep(True),), close=91),
            frame(7, (sweep(True), replace(broken, breakout_close=D(112))), close=92),
        ]
    )
    good = candidate(snap7, sweep(True))
    assert good.state is SetupState.QUALIFIED
    # planner consumes the *crafted* confirmation frame directly (the snapshot
    # remains the genuine QUALIFIED one): the frozen level 85 must be rejected.
    crafted = replace(
        frames[1], patterns=replace(frames[1].patterns, breakouts=(broken,))
    )
    plan = plan_trade(
        snapshot=snap7,
        frame=crafted,
        setup_id=good.id,
        parameters=PlanningParameters(entry_mode=EntryMode.FROZEN_CONFIRMATION),
    )
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("entry_not_on_trade_side",)
    assert plan.entry.value == D(85)  # offending number stays visible, unfixed


# ---------------------------------------------------------------------------
# Stop versus logical invalidation
# ---------------------------------------------------------------------------


def test_logical_invalidation_is_structural_and_stop_is_buffered():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.PERCENTAGE, stop_buffer_percentage="0.2"
        ),
    )
    assert plan.invalidation.value == D(110)
    assert plan.invalidation.source_id == setup.reference_id
    assert plan.invalidation.source_type == "step4_reference_band_low"
    assert plan.invalidation.transformation is None  # verbatim frozen level
    assert plan.stop.value == D("109.78000000")  # 110 - tolerance_band(110, 0.2%)
    assert plan.stop.source_value == D(110)
    assert plan.stop.source_type == "planner_stop_buffer_percentage"
    assert (
        "tolerance_band(110, stop_buffer_percentage 0.2%)" in plan.stop.transformation
    )
    assert plan.risk_per_unit == D("2.22000000")


def test_atr_buffer_uses_existing_atr_level():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=with_atr(planning_frame),
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.ATR, stop_buffer_atr_multiple="1.5"
        ),
    )
    assert plan.state is PlanState.PLANNABLE
    assert plan.stop.value == D("107.00000000")  # 110 - quantized(2 x 1.5)
    assert plan.risk_per_unit == D("5.00000000")
    (target,) = plan.targets
    assert target.level.value == D("122.00000000")
    assert target.r_multiple == D("2.00000000")


def test_missing_atr_is_gap_not_substitute_buffer():
    snapshot, planning_frame, setup = qualified()
    # the flat fixture frame genuinely lacks an ATR level (7 candles < period),
    # while the percentage field the qualification gate read is present
    assert planning_frame.patterns.structure.volatility.atr is None
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(stop_buffer_mode=StopBufferMode.ATR),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("missing_atr_for_stop_buffer",)
    assert plan.missing_inputs == ("volatility.atr",)
    assert plan.stop.value is None
    assert plan.targets == ()
    pending = {r.rule_id for r in plan.rules if r.outcome is RuleOutcome.PENDING}
    assert {"stop_beyond_entry", "positive_risk", "target_levels_valid"} <= pending
    assert (
        "blocking outcome: missing_atr_for_stop_buffer"
        in rule_for(plan, "target_levels_valid").reason
    )


def test_stop_equality_and_zero_risk_rejected_not_clamped():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)), close=110)]
    snapshot = result(frames)
    setup = candidate(snapshot, seed)
    assert setup.state is SetupState.QUALIFIED  # location uses >=, still qualified
    plan = plan_trade(snapshot=snapshot, frame=frames[-1], setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == (
        "stop_not_beyond_entry",
        "non_positive_risk",
        "no_valid_target_available",
    )
    assert plan.entry.value == D(110)
    assert plan.invalidation.value == D(110)
    assert plan.risk_per_unit is None


def test_stop_pushed_to_non_positive_rejected():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=with_atr(planning_frame, "100"),
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.ATR, stop_buffer_atr_multiple="10"
        ),
    )
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("stop_non_positive", "no_valid_target_available")
    assert plan.stop.value is None
    assert plan.risk_per_unit is None
    assert plan.targets == ()
    assert "not a finite positive price" in rule_for(plan, "stop_beyond_entry").reason


def test_large_valid_buffer_widens_risk_without_rejection():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=with_atr(planning_frame, "10"),
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.ATR, stop_buffer_atr_multiple="5"
        ),
    )
    assert plan.state is PlanState.PLANNABLE
    assert plan.stop.value == D("60.00000000")
    assert plan.risk_per_unit == D("52.00000000")


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------


def test_structural_target_only_from_levels_known_at_as_of():
    snapshot, planning_frame, setup, _seed = qualified_continuation(upto=9)
    assert setup.state is SetupState.QUALIFIED
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.entry.value == D("110.5")
    assert plan.risk_per_unit == D("0.5")
    (target,) = plan.targets
    assert target.is_structural
    assert target.level.value == D(114)  # confirmed swing high, existing Step 3 fact
    assert target.level.source_type in (
        "step4_reference_swing_high",
        "step4_reference_zone",
    )
    assert target.reward_per_unit == D("3.5")
    assert target.r_multiple == D("7.00000000")
    assert any("not strictly above entry" in note for note in plan.excluded_targets)
    assert rule_for(plan, "target_levels_valid").reason.startswith("1 target(s)")
    assert "structural levels" in rule_for(plan, "target_levels_valid").reason


def test_frozen_entry_with_structural_target_exact_numbers():
    snapshot, planning_frame, setup, _seed = qualified_continuation(upto=9)
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(entry_mode=EntryMode.FROZEN_CONFIRMATION),
    )
    assert plan.entry.value == D(111)  # held-retest close
    assert plan.risk_per_unit == 1
    (target,) = plan.targets
    assert target.is_structural and target.level.value == D(114)
    assert target.reward_per_unit == 3
    assert target.r_multiple == D("3.00000000")


def test_r_derived_fallback_labelled_and_only_when_needed():
    snapshot, planning_frame, setup = qualified()  # flat structure: nothing above
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    (target,) = plan.targets
    assert target.is_structural is False
    assert target.level.source_type == "r_multiple_fallback"
    assert target.level.source_value == plan.risk_per_unit
    assert "entry 112 + (2 R x risk 2)" in target.level.transformation
    assert "no valid structural level" in rule_for(plan, "target_levels_valid").reason

    # With structural evidence available, the fallback is not used at all.
    snapshot, planning_frame, setup, _seed = qualified_continuation(upto=9)
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert all(t.is_structural for t in plan.targets)

    # Disabling fallbacks leaves the flat-candidate plan with an explicit gap.
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(r_multiple_fallbacks=()),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("no_valid_target_available",)


def test_multiple_fallbacks_sorted_and_canonicalized():
    snapshot, planning_frame, setup = qualified()
    a = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(r_multiple_fallbacks=(D(3), D("1.5"))),
    )
    b = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(r_multiple_fallbacks=(1.5, "3.0")),
    )
    assert a == b  # canonical spelling/order -> byte-identical plans and identities
    assert [t.level.value for t in a.targets] == [D("115.0"), D(118)]
    assert [str(t.r_multiple) for t in a.targets] == ["1.50000000", "3.00000000"]
    assert [t.reward_per_unit for t in a.targets] == [D("3.0"), D("6.0")]
    assert a.targets[0].level.value < a.targets[1].level.value  # nearest R first


def test_duplicate_structural_levels_collapse_once():
    snapshot, planning_frame, setup, _seed = qualified_continuation(upto=9)
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert len(plan.targets) == 1  # swing-high reference and its zone share 114
    assert any(
        "duplicate of already-selected level 114" in note
        for note in plan.excluded_targets
    )


def test_max_structural_targets_cap_and_ordering():
    cluster = EqualLevelCluster(
        "eq-113",
        "equal_high",
        (),
        D(113),
        D("113.5"),
        D("113.25"),
        at(5),
        at(6),
        at(4),
        at(5),
        2,
        PatternLiquidityParameters(),
    )
    seed_source = psnap(RICH[:6], at(6))
    seed = swing_events(seed_source.breakouts)[0]
    retest = held(seed, i=7)
    frames = [
        rich_frame(6, RICH, (seed,)),
        rich_frame(7, RICH, (seed, retest)),
        rich_frame(8, RICH, (seed, retest)),
        rich_frame(9, RICH, (seed, retest), equal_levels=(cluster,)),
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(9))[-1]
    setup = candidate(snapshot, seed)
    assert setup.state is SetupState.QUALIFIED
    plan = plan_trade(
        snapshot=snapshot,
        frame=frames[-1],
        setup_id=setup.id,
        parameters=PlanningParameters(max_structural_targets=1),
    )
    assert [t.level.value for t in plan.targets] == [D(113)]  # nearest first
    assert any(
        "beyond max_structural_targets 1" in note for note in plan.excluded_targets
    )
    full = plan_trade(snapshot=snapshot, frame=frames[-1], setup_id=setup.id)
    assert [t.level.value for t in full.targets] == [D(113), D(114)]


def test_target_on_entry_level_rejected_not_bumped():
    cluster = EqualLevelCluster(
        "eq-112",
        "equal_high",
        (),
        D(112),
        D(112),
        D(112),
        at(5),
        at(6),
        at(4),
        at(5),
        2,
        PatternLiquidityParameters(),
    )
    seed = breakout()
    frames = [
        frame(6, (seed,)),
        replace(
            frame(7, (seed, held(seed))),
            patterns=replace(
                frame(7, (seed, held(seed))).patterns, equal_levels=(cluster,)
            ),
        ),
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(7))[-1]
    setup = candidate(snapshot, seed)
    plan = plan_trade(snapshot=snapshot, frame=frames[-1], setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert not plan.targets[0].is_structural  # fallback, not a nudged 112 -> 112.x
    assert any(
        "eq-112: candidate level not strictly above entry" in note
        for note in plan.excluded_targets
    )


def test_equal_level_targets_can_be_disabled():
    cluster = EqualLevelCluster(
        "eq-115",
        "equal_high",
        (),
        D(115),
        D("115.2"),
        D("115.1"),
        at(5),
        at(6),
        at(4),
        at(5),
        2,
        PatternLiquidityParameters(),
    )
    seed = breakout()
    base = frame(7, (seed, held(seed)))
    frames = [
        frame(6, (seed,)),
        replace(base, patterns=replace(base.patterns, equal_levels=(cluster,))),
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(7))[-1]
    setup = candidate(snapshot, seed)
    enabled = plan_trade(snapshot=snapshot, frame=frames[-1], setup_id=setup.id)
    assert enabled.targets[0].level.value == D(115)
    assert enabled.targets[0].level.source_type == "step4_equal_level_cluster"
    assert enabled.targets[0].level.confirmed_at == at(6)
    disabled = plan_trade(
        snapshot=snapshot,
        frame=frames[-1],
        setup_id=setup.id,
        parameters=PlanningParameters(include_equal_levels_as_targets=False),
    )
    assert disabled.targets[0].level.source_type == "r_multiple_fallback"


def test_exact_r_quantization_half_even():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.PERCENTAGE,
            stop_buffer_percentage="0.3",
            r_multiple_fallbacks=(),
        ),
    )
    assert plan.stop.value == D("109.67000000")
    assert plan.state is PlanState.NO_PLAN  # no structural level and no fallback
    assert plan.reasons == ("no_valid_target_available",)

    snapshot, planning_frame, setup, _seed = qualified_continuation(upto=9)
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            stop_buffer_mode=StopBufferMode.PERCENTAGE, stop_buffer_percentage="0.3"
        ),
    )
    assert plan.entry.value == D("110.5")
    assert plan.stop.value == D("109.67000000")
    assert plan.risk_per_unit == D("0.83000000")
    (target,) = plan.targets
    assert target.reward_per_unit == D("3.5")
    assert target.r_multiple == D("4.21686747")  # 3.5 / 0.83 at 8dp half-even


# ---------------------------------------------------------------------------
# Minimum-R policy
# ---------------------------------------------------------------------------


def test_low_r_plannable_without_minimum_r_rule():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(r_multiple_fallbacks=(D("0.25"),)),
    )
    assert plan.state is PlanState.PLANNABLE  # low R alone never rejects
    assert plan.targets[0].r_multiple == D("0.25000000")
    assert "minimum_r_multiple" not in {r.rule_id for r in plan.rules}


def test_minimum_r_excludes_near_target_and_reports_threshold():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            r_multiple_fallbacks=(D(1), D(2)), min_r_multiple="1.5"
        ),
    )
    assert plan.state is PlanState.PLANNABLE
    assert [t.level.value for t in plan.targets] == [D(116)]
    assert any(
        "R 1.00000000 < minimum_r_multiple 1.5" in note
        for note in plan.excluded_targets
    )


def test_minimum_r_rejects_plan_when_nothing_survives():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(min_r_multiple="3"),
    )
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("minimum_r_multiple_not_met",)
    assert plan.targets == ()
    rule = rule_for(plan, "minimum_r_multiple")
    assert rule.outcome is RuleOutcome.FAIL
    assert "minimum_r_multiple 3" in rule.reason
    assert [r.rule_id for r in plan.rules] == list(BASE_RULES) + ["minimum_r_multiple"]


# ---------------------------------------------------------------------------
# Missing/UNKNOWN evidence
# ---------------------------------------------------------------------------


def test_missing_close_is_reported_gap():
    snapshot, planning_frame, setup = qualified()
    no_close = replace(
        planning_frame,
        patterns=replace(
            planning_frame.patterns,
            structure=replace(
                planning_frame.patterns.structure,
                volatility=replace(
                    planning_frame.patterns.structure.volatility, latest_close=None
                ),
            ),
        ),
    )
    plan = plan_trade(snapshot=snapshot, frame=no_close, setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("missing_plan_close",)
    assert plan.missing_inputs == ("structure.volatility.latest_close",)
    assert plan.entry.value is None
    assert plan.entry.source_type == "unknown"


def test_missing_confirmation_event_is_gap():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    snapshot = result(frames)
    setup = candidate(snapshot, seed)
    stripped = frame(7, (seed,))  # genuine QUALIFIED snapshot, frame lacking it
    plan = plan_trade(snapshot=snapshot, frame=stripped, setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("confirmation_event_missing",)
    assert plan.missing_inputs == ("step4_retest(held)",)
    assert (
        "held Step 4 retest" in rule_for(plan, "family_confirmation_available").reason
    )


def test_missing_seed_event_is_gap():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    snapshot = result(frames)
    orphaned = replace(
        snapshot, setups=(replace(candidate(snapshot, seed), seed_event_id="absent"),)
    )
    plan = plan_trade(
        snapshot=orphaned, frame=frames[-1], setup_id=orphaned.setups[0].id
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("seed_event_missing",)
    assert plan.missing_inputs == ("step4_seed_event",)


def test_missing_reference_band_is_gap():
    snapshot, planning_frame, setup = qualified()
    seed = breakout()
    naked = replace(
        seed, reference=replace(seed.reference, band_low=None, band_high=None)
    )
    tail = replace(
        planning_frame, patterns=replace(planning_frame.patterns, breakouts=(naked,))
    )
    plan = plan_trade(snapshot=snapshot, frame=tail, setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("missing_reference_band",)
    assert "step4_reference_band" in plan.missing_inputs


def test_source_history_gaps_never_bridge_into_a_plan():
    seed = breakout()
    gapped = replace(
        frame(7, (seed, held(seed))),
        patterns=replace(
            frame(7, (seed, held(seed))).patterns,
            completeness=replace(
                frame(7, (seed, held(seed))).patterns.completeness,
                gaps=(CandleGap(at(6), at(6), 1),),
                complete=False,
            ),
        ),
    )
    history = enumerate_qualifications([frame(6, (seed,)), gapped], as_of=at(7))
    dead = history[-1].setups[0]
    assert dead.state is SetupState.NO_SETUP
    assert dead.terminal_reason == "source_candle_gap"
    plan = plan_trade(snapshot=history[-1], frame=gapped, setup_id=dead.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("terminal_source_setup",)


# ---------------------------------------------------------------------------
# Contradictory inputs are INVALID (never NO_PLAN-downgraded)
# ---------------------------------------------------------------------------


def test_reference_family_direction_identity_mismatches_are_invalid():
    snapshot, planning_frame, setup = qualified()

    wrong_ref = replace(snapshot, setups=(replace(setup, reference_id="bogus"),))
    plan = plan_trade(snapshot=wrong_ref, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("reference_id_mismatch",)

    wrong_family = replace(
        snapshot, setups=(replace(setup, family=SetupFamily.RANGE_REVERSAL),)
    )
    plan = plan_trade(snapshot=wrong_family, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("family_mismatch",)

    wrong_direction = replace(snapshot, setups=(replace(setup, direction="bearish"),))
    plan = plan_trade(snapshot=wrong_direction, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("direction_mismatch",)

    wrong_created = replace(snapshot, setups=(replace(setup, created_at=at(99)),))
    plan = plan_trade(snapshot=wrong_created, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("setup_identity_mismatch",)


def test_frame_snapshot_mismatch_is_invalid_not_guessed():
    snapshot, _planning_frame, _setup = qualified()
    later = frame(8, (breakout(), held(breakout())))
    plan = plan_trade(snapshot=snapshot, frame=later, setup_id="whatever")
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("frame_snapshot_asof_mismatch",)
    assert rule_for(plan, "source_inputs_consistent").outcome is RuleOutcome.FAIL
    assert all(
        r.outcome is RuleOutcome.PENDING
        for r in plan.rules
        if r.rule_id != "source_inputs_consistent"
    )


def test_instrument_mismatch_is_invalid():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(
        snapshot=replace(snapshot, symbol="ETH/USD"),
        frame=planning_frame,
        setup_id=setup.id,
    )
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("instrument_mismatch",)


def test_future_evidence_cannot_enter_the_plan():
    seed = breakout()
    frames = [frame(6, (seed,)), frame(7, (seed, held(seed)))]
    snapshot = result(frames)
    setup = candidate(snapshot, seed)
    early_snapshot = replace(
        snapshot, as_of=at(5), setups=(replace(setup, as_of=at(5)),)
    )
    early_frame = frame(5, (replace(seed, known_at=at(6)),))
    plan = plan_trade(snapshot=early_snapshot, frame=early_frame, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("future_evidence_used",)
    reason = rule_for(plan, "availability_at_as_of").reason
    assert "seed known_at" in reason and "seed candle closes" in reason
    assert plan.entry.value is None and plan.targets == ()


def test_stale_qualified_objects_are_never_actionable():
    seed = breakout()
    catalog = (seed, held(seed))
    frames = [
        frame(6, (seed,)),
        frame(7, catalog),
        frame(8, catalog, volume=None),  # demoted at 8
    ]
    history = enumerate_qualifications(frames, as_of=at(8))
    stale, current = history[1], history[2]
    assert candidate(stale, seed).state is SetupState.QUALIFIED
    assert candidate(current, seed).state is SetupState.WATCH
    live_plan = plan_trade(
        snapshot=current, frame=frames[-1], setup_id=candidate(stale, seed).id
    )
    assert live_plan.state is PlanState.NO_PLAN
    assert live_plan.reasons == ("setup_not_qualified",)
    # the historical snapshot keeps reproducing its own identical plan
    historic = plan_trade(
        snapshot=stale,
        frame=frames[1],
        setup_id=candidate(stale, seed).id,
    )
    assert historic.state is PlanState.PLANNABLE
    assert historic == plan_trade(
        snapshot=stale, frame=frames[1], setup_id=candidate(stale, seed).id
    )


def test_setup_as_of_not_matching_snapshot_is_stale():
    snapshot, planning_frame, setup = qualified()
    shifted = replace(snapshot, setups=(replace(setup, as_of=at(6)),))
    plan = plan_trade(snapshot=shifted, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("stale_qualification",)


# ---------------------------------------------------------------------------
# Range-family specific contradictions
# ---------------------------------------------------------------------------


def test_range_state_conflicts_are_invalid():
    snapshot, planning_frame, setup, seed = qualified_range(mirror=False)

    no_active = frame(7, (seed,), close=108, trend="neutral")
    plan = plan_trade(snapshot=snapshot, frame=no_active, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("active_range_mismatch",)

    no_bounds = replace(
        planning_frame,
        patterns=replace(
            planning_frame.patterns,
            sweeps=(replace(seed, reference=replace(seed.reference, range=None)),),
        ),
    )
    plan = plan_trade(snapshot=snapshot, frame=no_bounds, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("frozen_range_evidence_missing",)

    stalled = frame(
        7,
        (seed,),
        close=seed.candle.close,
        active_range=frozen_range(),
        trend="neutral",
    )
    plan = plan_trade(snapshot=snapshot, frame=stalled, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("range_followthrough_failed",)


def test_range_entry_outside_frozen_bounds_rejected():
    snapshot, _planning_frame, setup, seed = qualified_range(mirror=False)
    wild = replace(seed, reclaim_close=D(85))  # below frozen range low 90
    frames = [
        frame(6, (wild,), close=85, active_range=frozen_range(), trend="neutral"),
        frame(7, (wild,), close=108, active_range=frozen_range(), trend="neutral"),
    ]
    plan = plan_trade(
        snapshot=snapshot,
        frame=frames[1],
        setup_id=setup.id,
        parameters=PlanningParameters(entry_mode=EntryMode.FROZEN_CONFIRMATION),
    )
    # band side passes (85 <= band low 110); the frozen range bound rejects.
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("range_entry_outside_frozen_bounds",)
    assert plan.entry.value == D(85)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------


def test_source_traceability_of_every_level():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.timeframe == "1h"
    assert plan.source_timeframes == ("1h",)
    assert plan.setup_as_of == at(7)
    assert plan.setup_created_at == at(6)
    assert plan.as_of == at(7)

    assert plan.entry.source_type == "step3_volatility_latest_close"
    assert (
        plan.entry.observed_at == planning_frame.patterns.structure.window_end_timestamp
    )
    assert plan.entry.confirmed_at == plan.as_of
    assert plan.entry.source_value == plan.entry.value

    assert plan.invalidation.source_id == setup.reference_id
    assert plan.invalidation.source_value == plan.invalidation.value
    assert plan.invalidation.confirmed_at is not None
    assert plan.invalidation.confirmed_at <= plan.as_of

    assert plan.stop.source_id == f"{setup.reference_id}:buffer"
    assert plan.stop.source_value == plan.invalidation.value
    assert "no buffer applied" in plan.stop.transformation

    (target,) = plan.targets
    assert len(target.level.source_id) == 64
    assert target.level.source_type == "r_multiple_fallback"
    assert target.level.observed_at is None  # derived level invents no timestamp

    # Every source reference resolves inside the consumed frame's evidence.
    catalog_ids = {e.id for e in planning_frame.patterns.events()}
    assert setup.seed_event_id in catalog_ids
    seed_event = next(
        e for e in planning_frame.patterns.breakouts if e.id == setup.seed_event_id
    )
    assert setup.reference_id == seed_event.reference.id
    assert len(plan.entry.source_id) == 64
    assert plan.invalidation.observed_at in {
        swing.timestamp for swing in seed_event.reference.swings
    }
    assert (
        plan.to_json_dict()["entry"]["source_type"] == "step3_volatility_latest_close"
    )


# ---------------------------------------------------------------------------
# Identity, immutability, configuration
# ---------------------------------------------------------------------------


def test_plan_identity_is_stable_and_config_bound():
    snapshot, planning_frame, setup = qualified()
    first = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    second = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert first == second and first.id == second.id
    assert re.fullmatch(r"[0-9a-f]{64}", first.id)
    alt = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(stop_buffer_percentage="0.3"),
    )
    assert alt.id != first.id  # any config change moves the identity namespace
    assert alt.entry.value == first.entry.value
    assert first.config_fingerprint != alt.config_fingerprint
    assert first.setup_config_fingerprint == snapshot.config_fingerprint
    assert first.planning_rules_version == "trade-planning-v1"


def test_full_rebuild_reproduces_identical_plan():
    a_snapshot, a_frame, a_setup = qualified()
    b_snapshot, b_frame, b_setup = qualified()  # fresh objects, same values
    pa = plan_trade(snapshot=a_snapshot, frame=a_frame, setup_id=a_setup.id)
    pb = plan_trade(snapshot=b_snapshot, frame=b_frame, setup_id=b_setup.id)
    assert pa == pb and pa.id == pb.id
    assert json.dumps(pa.to_json_dict(), sort_keys=True) == json.dumps(
        pb.to_json_dict(), sort_keys=True
    )


def test_configuration_canonical_spellings_reproduce_same_plan():
    snapshot, planning_frame, setup = qualified()
    a = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(stop_buffer_percentage="0.20"),
    )
    b = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(stop_buffer_percentage=D("0.2")),
    )
    assert a == b
    canonical = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            r_multiple_fallbacks=(D("2.00"),), min_r_multiple="2.0"
        ),
    )
    default = plan_trade(
        snapshot=snapshot,
        frame=planning_frame,
        setup_id=setup.id,
        parameters=PlanningParameters(min_r_multiple=D(2)),
    )
    assert canonical == default


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entry_mode": "teleport"},
        {"entry_mode": 3},
        {"stop_buffer_mode": "vibes"},
        {"stop_buffer_atr_multiple": 0},
        {"stop_buffer_atr_multiple": "NaN"},
        {"stop_buffer_atr_multiple": "Infinity"},
        {"stop_buffer_percentage": 0},
        {"stop_buffer_percentage": 100},
        {"max_structural_targets": 0},
        {"max_structural_targets": 11},
        {"max_structural_targets": True},
        {"include_equal_levels_as_targets": "yes"},
        {"r_multiple_fallbacks": [2]},
        {"r_multiple_fallbacks": (D(0),)},
        {"r_multiple_fallbacks": (D(2), D("2.0"))},
        {"min_r_multiple": -1},
        {"min_r_multiple": "not-a-number"},
    ],
)
def test_config_validation(kwargs):
    with pytest.raises((ValueError, TypeError)):
        PlanningParameters(**kwargs)


def test_plans_are_immutable_and_never_mutate_sources():
    snapshot, planning_frame, setup = qualified()
    snapshot_json = snapshot.to_json_dict()
    frame_json = planning_frame.patterns.to_json_dict()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    with pytest.raises(FrozenInstanceError):
        plan.state = PlanState.NO_PLAN
    with pytest.raises(FrozenInstanceError):
        plan.targets[0].level.value = D(0)
    projected = plan.to_json_dict()
    projected["entry"]["value"] = "tampered"
    projected["rules"].clear()
    assert plan.to_json_dict()["entry"]["value"] == "112"
    assert snapshot.to_json_dict() == snapshot_json
    assert planning_frame.patterns.to_json_dict() == frame_json
    assert len(snapshot.setups) == 1
    assert snapshot.setups[0].state is SetupState.QUALIFIED  # untouched upstream


def test_payload_has_no_execution_or_sizing_fields():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    payload = plan.to_json_dict()
    assert set(payload) == PLAN_KEYS

    def walk_keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from walk_keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk_keys(item)

    forbidden = {
        "balance",
        "leverage",
        "quantity",
        "notional",
        "margin",
        "position",
        "pnl",
        "order",
        "api_key",
        "secret",
        "trailing",
        "account",
    }
    assert forbidden.isdisjoint({key.lower() for key in walk_keys(payload)})


def test_no_exchange_client_or_secret_tokens_in_planner_sources():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for path in sorted((root / "src/trading_assistant/trade_planning").glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in ("ccxt", "api_key", "secret", "passphrase", "load_markets"):
            assert token not in text, (path.name, token)


# ---------------------------------------------------------------------------
# Anti-lookahead
# ---------------------------------------------------------------------------


def test_inserting_future_candles_cannot_alter_a_historical_plan():
    snapshot_trunc, frame_trunc, setup_trunc, _seed = qualified_continuation(upto=7)
    snapshot_full, frame_full, setup_full, _seed = qualified_continuation(
        upto=7, full=True
    )  # same as_of, frames built from the entire 9-candle series
    assert snapshot_full == snapshot_trunc
    plan_trunc = plan_trade(
        snapshot=snapshot_trunc,
        frame=frame_trunc,
        setup_id=setup_trunc.id,
    )
    plan_full = plan_trade(
        snapshot=snapshot_full, frame=frame_full, setup_id=setup_full.id
    )
    assert plan_full == plan_trunc
    # The 114 swing high only confirms later; it cannot become a 7-o'clock target.
    assert plan_trunc.state is PlanState.PLANNABLE
    assert all(not t.is_structural for t in plan_trunc.targets)
    later, later_frame, later_setup, _seed = qualified_continuation(upto=9)
    plan_later = plan_trade(snapshot=later, frame=later_frame, setup_id=later_setup.id)
    assert any(t.is_structural and t.level.value == D(114) for t in plan_later.targets)
    assert plan_trunc.as_of == at(7) and plan_later.as_of == at(9)
    assert plan_trunc.id != plan_later.id


def test_only_information_at_or_before_as_of_may_be_used():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    for level in (plan.entry, plan.invalidation, plan.stop, plan.targets[0].level):
        stamp = level.confirmed_at
        assert stamp is None or stamp <= plan.as_of
    assert all(
        (t.level.confirmed_at is None or t.level.confirmed_at <= plan.as_of)
        for t in plan.targets
    )


def test_chronological_replay_plans_at_every_close():
    seed = breakout()
    catalog = (seed, held(seed))
    frames = [
        frame(6, (seed,)),
        frame(7, catalog),
        frame(8, catalog),
        frame(9, catalog, close=109),
    ]
    history = enumerate_qualifications(frames, as_of=at(9))
    assert [s.as_of for s in history] == [at(i) for i in (6, 7, 8, 9)]
    states = []
    for index, snapshot in enumerate(history):
        plan = plan_trade(
            snapshot=snapshot,
            frame=frames[index],
            setup_id=snapshot.setups[0].id,
        )
        states.append(plan.state)
    assert states == [
        PlanState.NO_PLAN,  # WATCH at the seed close
        PlanState.PLANNABLE,  # QUALIFIED at 7
        PlanState.PLANNABLE,  # still QUALIFIED at 8
        PlanState.NO_PLAN,  # terminally invalidated at 9
    ]
    plan7 = plan_trade(
        snapshot=history[1], frame=frames[1], setup_id=history[1].setups[0].id
    )
    plan8 = plan_trade(
        snapshot=history[2], frame=frames[2], setup_id=history[2].setups[0].id
    )
    assert plan7.entry.value == plan8.entry.value == D(112)
    assert plan7.id != plan8.id  # plan identity binds its own as_of


# ---------------------------------------------------------------------------
# Generic instrument support
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USD", "SOL/USDC"])
def test_planner_is_symbol_generic(symbol):
    snapshot, planning_frame, setup, _seed = qualified_continuation(
        upto=7, symbol=symbol
    )
    assert setup.state is SetupState.QUALIFIED
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.symbol == symbol
    assert plan.entry.value == D(113)  # purely candle-driven numbers
    assert plan.invalidation.value == D(110)
    assert plan.risk_per_unit == 3
    assert plan.targets[0].level.value == D(119)


def test_ids_differ_only_through_instrument_bound_evidence():
    btc = qualified_continuation(upto=7, symbol="BTC/USDT")
    eth = qualified_continuation(upto=7, symbol="ETH/USD")
    pb = plan_trade(snapshot=btc[0], frame=btc[1], setup_id=btc[2].id)
    pe = plan_trade(snapshot=eth[0], frame=eth[1], setup_id=eth[2].id)
    assert pb.id != pe.id and pb.setup_id != pe.setup_id

    def numeric(plan):
        return (
            plan.entry.value,
            plan.invalidation.value,
            plan.stop.value,
            plan.risk_per_unit,
            [t.level.value for t in plan.targets],
        )

    assert numeric(pb) == numeric(pe)


# ---------------------------------------------------------------------------
# API guards and projection
# ---------------------------------------------------------------------------


def test_plan_trade_argument_guards():
    snapshot, planning_frame, setup = qualified()
    with pytest.raises(TypeError):
        plan_trade(snapshot="nope", frame=planning_frame, setup_id=setup.id)
    with pytest.raises(TypeError):
        plan_trade(snapshot=snapshot, frame=(), setup_id=setup.id)
    with pytest.raises(TypeError):
        plan_trade(
            snapshot=snapshot,
            frame=planning_frame,
            setup_id=setup.id,
            parameters={},
        )
    with pytest.raises(ValueError):
        plan_trade(snapshot=snapshot, frame=planning_frame, setup_id="   ")


def test_json_projection_is_serializable_and_detached():
    snapshot, planning_frame, setup = qualified()
    plan = plan_trade(snapshot=snapshot, frame=planning_frame, setup_id=setup.id)
    payload = json.loads(json.dumps(plan.to_json_dict(), sort_keys=True))
    assert payload["state"] == "PLANNABLE"
    assert payload["entry"]["value"] == "112"
    assert payload["as_of"] == "2024-01-01T07:00:00Z"
    assert payload["targets"][0]["r_multiple"] == "2.00000000"
    assert payload["reasons"] == []
    assert payload["state_detail"] is None
    assert payload["planning_rules_version"] == "trade-planning-v1"
    for rule in payload["rules"]:
        assert rule["outcome"] in {"passed", "failed", "pending"}
        assert re.fullmatch(r"[a-z0-9_]+", rule["rule_id"])
    for reason in payload["reasons"]:
        assert re.fullmatch(r"[a-z0-9_]+", reason)
