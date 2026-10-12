"""Step 6 economic viability (trade-planning-v3) — focused synthetic tests.

These tests pin the new mandatory ``economic_viability`` planning rule and its
wiring into Step 12 (forward testing) and the dashboard. Everything is
deterministic and synthetic; nothing reads an exchange or project data.

The scenarios below are *explicitly stated hypothetical* cost scenarios
(hypothetical per-unit fees and adverse execution costs, in basis points) —
declared scenario inputs for a deterministic engine, never validated market
facts and never a statement about what any real trade earned or would earn.

The gate metric is the TRUE net reward-to-risk, computed by the shared cost
model in ``trading_assistant.costs``:

* net potential reward after the declared fees and adverse execution costs,
  compared with
* net potential loss at the stop after the same declared costs.

A stop hit pays the same fees and adverse execution costs as any other exit,
so the net stop loss is strictly larger than the raw risk. A target passes
only if its net reward after costs covers at least
``min_cost_adjusted_r_multiple`` (default 1) times that net stop loss. The
HISTORICAL friction-adjusted R (net reward normalised by the *original*
cost-free risk, ``friction_adjusted_r_from_levels``) is retained for Step
11/12 reporting and is shown, clearly labelled, on every per-target
exclusion — but it is not the gate, because it cannot see that the stop
itself would cost more than its raw distance. One of the tests below pins
exactly that distinction: a target whose historical friction-adjusted R
exceeds 1 while its net reward does not cover the net stop loss.

The anchor case is the verified 11 Oct 2026 paper example from the PR #45
audit (Binance Spot BTC/USDT 1H): entry ``83529.47``, stop/invalidation
``83521.15`` (risk ``8.32`` = 0.996 bps of price — a tiny stop that produced
the ``ENTRY_AND_EXIT_SAME_CANDLE`` ambiguity on 1-minute noise), structural
targets ``83572.96`` and ``83577.00`` with raw R multiples ``5.22716346`` and
``5.71274038``. Both raw multiples clear the 1R floor (that was the trap),
but under the clearly stated illustrative scenario
(fee 10 bps, 5 bps adverse entry slippage, 5 bps adverse exit slippage) the
net reward after costs is far below the net stop loss after costs, so the
plan must be refused with the exact numbers, not certified.

Documented, pinned behaviours:

* ``cost_assumptions is None`` (no cost scenario in effect for this planning
  pass — e.g. the v2-equivalent default parameters) leaves the rule PENDING
  and the plan otherwise byte-identical in its decisions to v2.
* An explicit all-zero scenario is a *gap*, not a certification: the plan is
  withheld as ``economic_viability_unverifiable`` with every target excluded
  by name. Missing or zero fee assumptions are never treated as proof that a
  trade is economically viable.
* A non-zero scenario applies the shared net cost model to each retained
  target; targets whose net reward after costs does not cover the floor
  multiple of the net stop loss after costs are excluded with their exact
  net numbers (and the historical friction-adjusted R, labelled), and a plan
  with no remaining viable target is refused as ``economic_nonviability``.
* The explanation of a retained plan always names the nearest RETAINED
  target and shows that target's own net numbers — never the first evaluated
  (possibly excluded) target's.
* The forward runner always forces its declared friction into the planning
  parameters (``ForwardTestService.__init__``), so a recorded paper plan's
  ``friction_fingerprint`` is the identity of the scenario its gate actually
  used. The dashboard plans with the shared configuration; when the runner
  was started with a different planning configuration (for example CLI
  fee/slippage overrides), the dashboard says so explicitly via
  ``planning.scenario_mismatch`` instead of presenting the two as one
  decision.
* ``trade-planning-v3`` plus the planning configuration fingerprint
  (which now includes the cost scenario) keeps old and new cohorts
  distinguishable in stored records.

Limitations (deliberately not tested here): the scenarios are hypothetical,
so none of this measures real fees, real slippage, or profitability; fill
probability, size, and account effects are outside the model by design.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

from forward_fixtures import (
    DEFAULT_COST_SCENARIO,
    QUALIFYING_BOUNDARY,
    labelled_series,
    make_harness,
)
from market_structure_fixtures import candle_at
from test_pattern_liquidity import snap, swing_events
from test_setup_qualification import at, held
from test_trade_planning import qualified, rich_frame
from trading_assistant.costs import (
    FrictionAssumptions,
    friction_adjusted_r_from_levels,
    friction_net_reward_to_risk,
)
from trading_assistant.forward_testing import (
    ECONOMIC_NONVIABILITY_REASON,
    ECONOMIC_VIABILITY_UNVERIFIABLE_REASON,
    ForwardParameters,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.setup_qualification import enumerate_qualifications
from trading_assistant.trade_planning import (
    PLANNING_RULES_VERSION,
    PlanningParameters,
    PlanState,
    plan_trade,
)
from web_fixtures import insert_candles, make_settings, migrated_engine

ENTRY = D("83529.47")
STOP = D("83521.15")
TARGET_1 = D("83572.96")
TARGET_2 = D("83577.00")
RISK = D("8.32")

#: Stated illustrative scenario for the anchor case: 10 bps hypothetical
#: two-sided fee, 5 bps adverse slippage on each side. It is deliberately
#: strong enough that the 0.996-bps stop cannot survive the costs of a round
#: trip at an 83,529.47 entry.
STATED_SCENARIO = FrictionAssumptions(
    fee_bps=D("10"), entry_slippage_bps=D("5"), exit_slippage_bps=D("5")
)


def _parameters(scenario: FrictionAssumptions) -> PlanningParameters:
    return PlanningParameters(cost_assumptions=scenario)


def _hbar(index: int, close, *, high=None, low=None):
    price = D(str(close))
    return candle_at(
        index,
        open_=str(price),
        close=str(price),
        high=str(high if high is not None else price + D("1")),
        low=str(low if low is not None else price - D("1")),
        volume="10",
        timeframe="1h",
    )


def _mirror_candle(candle):
    axis = D("167100")
    return replace(
        candle,
        open=axis - candle.open,
        high=axis - candle.low,
        low=axis - candle.high,
        close=axis - candle.close,
    )


def historical_fixture(*, mirror: bool = False):
    """The 11 Oct 2026 paper example, rebuilt through the real Steps 3-6.

    Prefix bars form the broken reference (its high is exactly the historical
    invalidation ``83521.15``), bar 5 breaks out and closes at the historical
    entry ``83529.47``, bar 6 is the held retest, and the flat frame series
    carries the two historical overheads as confirmed structure (bar 8's high
    ``83572.96`` as a zone and bar 11's high ``83577.00`` as a swing high).
    ``mirror=True`` mirrors the whole geometry around 167100 so the identical
    levels appear on the short side (entry ``83570.53``, stop ``83578.85``,
    targets ``83527.04``/``83523.00``).

    Note: the pattern-liquidity breakout tolerance is rescaled to 0.001% for
    this fixture because at 83,529 the historical 0.01% penetration is inside
    the default 0.1% tolerance band. That is a fixture-scale adjustment of
    Step 4's documented, configurable input; these tests exercise the Step 6
    economic-viability gate, not the breakout tolerance itself.
    """

    as_of = 15
    pp = PatternLiquidityParameters(breakout_tolerance_pct=D("0.001"))
    prefix = tuple(
        _hbar(i, price)
        for i, price in enumerate((83400, 83440, 83520.15, 83450, 83430))
    )
    breakout_candle = _hbar(5, ENTRY)
    seed = swing_events(snap(prefix + (breakout_candle,), parameters=pp).breakouts)[0]
    retest_candle = _hbar(6, 83525, low=STOP)
    retest = replace(
        held(seed, i=7),
        candle=retest_candle,
        evidence_candles=(retest_candle,),
    )
    candles = []
    for j in range(as_of):
        high = TARGET_1 if j == 8 else TARGET_2 if j == 11 else None
        candles.append(_hbar(j, ENTRY, high=high))
    if mirror:
        prefix = tuple(_mirror_candle(c) for c in prefix)
        breakout_candle = _mirror_candle(breakout_candle)
        retest_candle = _mirror_candle(retest_candle)
        candles = tuple(_mirror_candle(c) for c in candles)
        seed = swing_events(
            snap(prefix + (breakout_candle,), parameters=pp).breakouts
        )[0]
        retest = replace(
            held(seed, i=7),
            candle=retest_candle,
            evidence_candles=(retest_candle,),
        )
    trend = "bearish" if mirror else "bullish"
    frames = [rich_frame(6, candles, (seed,), trend=trend)] + [
        rich_frame(j, candles, (seed, retest), trend=trend)
        for j in range(7, as_of + 1)
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(as_of))[-1]
    setup = snapshot.setups[0]
    return snapshot, frames[-1], setup


def two_target_fixture():
    """A small-price long with TWO structural targets (116 then 118).

    Same geometry as the ``qualified()`` fixture (entry 112, stop 110, first
    target 116 at bar 8) with a second confirmed overhead at 118 (bar 11).
    Used to pin that the viability explanation shows the nearest *retained*
    target's own numbers even when the nearest *evaluated* target is
    excluded.
    """

    as_of = 15
    prefix = tuple(_hbar(i, price) for i, price in enumerate((100, 104, 109, 104, 103)))
    seed = swing_events(snap(prefix + (_hbar(5, 112),)).breakouts)[0]
    retest_candle = _hbar(6, 111, low=110)
    retest = replace(
        held(seed, i=7),
        candle=retest_candle,
        evidence_candles=(retest_candle,),
    )
    candles = []
    for j in range(as_of):
        high = 116 if j == 8 else 118 if j == 11 else None
        candles.append(_hbar(j, 112, high=high))
    frames = [rich_frame(6, candles, (seed,))] + [
        rich_frame(j, candles, (seed, retest)) for j in range(7, as_of + 1)
    ]
    snapshot = enumerate_qualifications(frames, as_of=at(as_of))[-1]
    setup = snapshot.setups[0]
    return snapshot, frames[-1], setup


# ----------------------------------------------------------------------
# Anchor case: the historical tiny-stop trade
# ----------------------------------------------------------------------


def test_historical_levels_reproduce_through_the_real_pipeline() -> None:
    """The v2-equivalent plan still reproduces the exact historical levels."""

    snapshot, frame, setup = historical_fixture()
    plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
    assert plan.state is PlanState.PLANNABLE
    assert plan.planning_rules_version == PLANNING_RULES_VERSION == "trade-planning-v3"
    assert plan.entry.value == ENTRY
    assert plan.stop.value == STOP
    assert plan.risk_per_unit == RISK
    assert [t.level.value for t in plan.targets] == [TARGET_1, TARGET_2]
    assert [t.r_multiple for t in plan.targets] == [D("5.22716346"), D("5.71274038")]
    # The raw 1R floor passes both targets — this is exactly why the raw
    # check alone was insufficient.
    minimum_r = next(r for r in plan.rules if r.rule_id == "minimum_r_multiple")
    assert minimum_r.outcome.value == "passed"
    # With no cost scenario in effect the new rule is PENDING, not evaluated.
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "pending"


def test_historical_trade_rejected_under_stated_scenario() -> None:
    """Raw 5R whose net reward after costs is below the net stop loss is refused."""

    snapshot, frame, setup = historical_fixture()
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(STATED_SCENARIO),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("economic_nonviability",)
    assert plan.state_detail == "economic_nonviability"
    assert plan.targets == ()
    assert plan.preferred_r_multiple_met is None
    # The raw floor passed (single cause: the economic gate, not the raw floor).
    minimum_r = next(r for r in plan.rules if r.rule_id == "minimum_r_multiple")
    assert minimum_r.outcome.value == "passed"
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "failed"
    # Every target is excluded by name with its exact NET numbers (net reward
    # / net stop loss / net R) and the historical friction-adjusted R,
    # clearly labelled, for comparison.
    assert (
        "83572.96: net reward -207.163623255 / net stop loss 258.895934160 "
        "= net R -0.80018106 < min_cost_adjusted_r_multiple 1 under scenario "
        "fee_bps=10, entry_slippage_bps=5, exit_slippage_bps=5 (historical "
        "friction-adjusted R vs original risk -24.89947395)"
        in plan.excluded_targets
    )
    assert (
        "83577.00: net reward -203.129681235 / net stop loss 258.895934160 "
        "= net R -0.78459973 < min_cost_adjusted_r_multiple 1 under scenario "
        "fee_bps=10, entry_slippage_bps=5, exit_slippage_bps=5 (historical "
        "friction-adjusted R vs original risk -24.41462515)"
        in plan.excluded_targets
    )


def test_historical_short_mirror_rejected_under_stated_scenario() -> None:
    """The same net economics reject the mirrored short-side geometry too."""

    snapshot, frame, setup = historical_fixture(mirror=True)
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(STATED_SCENARIO),
    )
    assert setup.direction == "bearish"
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("economic_nonviability",)
    assert plan.entry.value == D("83570.53")
    assert plan.stop.value == D("83578.85")
    assert plan.risk_per_unit == RISK
    assert (
        "83527.04: net reward -127.038763255 / net stop loss 75.254694160 "
        "= net R -1.68811746 < min_cost_adjusted_r_multiple 1 under scenario "
        "fee_bps=10, entry_slippage_bps=5, exit_slippage_bps=5 (historical "
        "friction-adjusted R vs original risk -24.89859775)"
        in plan.excluded_targets
    )
    assert (
        "83523.00: net reward -131.076741235 / net stop loss 75.254694160 "
        "= net R -1.74177495 < min_cost_adjusted_r_multiple 1 under scenario "
        "fee_bps=10, entry_slippage_bps=5, exit_slippage_bps=5 (historical "
        "friction-adjusted R vs original risk -24.41229222)"
        in plan.excluded_targets
    )


def test_mild_scenario_certifies_the_historical_trade() -> None:
    """Under a mild stated scenario the same levels' net reward covers the net loss."""

    snapshot, frame, setup = historical_fixture()
    mild = FrictionAssumptions(fee_bps=D("0.5"), entry_slippage_bps=D("0"), exit_slippage_bps=D("0"))
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(mild),
    )
    assert plan.state is PlanState.PLANNABLE
    assert [t.level.value for t in plan.targets] == [TARGET_1, TARGET_2]
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "passed"
    # The pass reason names the nearest RETAINED target with its own net
    # numbers: net reward 35.1348785 over net stop loss 16.6725310 =
    # net R 2.10735122 (historical friction-adjusted R 4.22294213).
    assert (
        "nearest retained target 83572.96: net reward 35.1348785 / net stop "
        "loss 16.6725310 = net R 2.10735122 vs raw R 5.22716346 (historical "
        "friction-adjusted R vs original risk 4.22294213)" in economic.reason
    )
    # A 1/1/1 bp scenario no longer certifies this trade: the net stop loss
    # (16.86...) exceeds the net reward, so the gate — not the historical
    # metric — is what decides.
    one_bp = FrictionAssumptions(fee_bps=D("1"), entry_slippage_bps=D("1"), exit_slippage_bps=D("1"))
    one_bp_plan = plan_trade(
        snapshot=snapshot, frame=frame, setup_id=setup.id, parameters=_parameters(one_bp)
    )
    assert one_bp_plan.state is PlanState.NO_PLAN
    assert one_bp_plan.reasons == ("economic_nonviability",)
    # The recorded plan identity carries the scenario in its configuration.
    assert plan.config_fingerprint != plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(STATED_SCENARIO),
    ).config_fingerprint


def test_zero_scenario_withholds_as_unverifiable() -> None:
    """An explicit all-zero scenario is a gap: withhold, never certify."""

    snapshot, frame, setup = historical_fixture()
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(FrictionAssumptions()),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("economic_viability_unverifiable",)
    assert "planning.cost_assumptions" in plan.missing_inputs
    assert plan.targets == ()
    assert plan.preferred_r_multiple_met is None
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "failed"
    # Both targets are excluded by name, and the reason names the policy.
    assert (
        "83572.96: not certified; no reliable (non-zero) cost assumptions were "
        "provided" in plan.excluded_targets
    )
    assert (
        "83577.00: not certified; no reliable (non-zero) cost assumptions were "
        "provided" in plan.excluded_targets
    )
    assert "never treated as proof" in economic.reason


# ----------------------------------------------------------------------
# Ordinary-stop and raw-1R-then-costs cases (small-price fixture)
# ----------------------------------------------------------------------


def test_ordinary_stop_mild_costs_plannable() -> None:
    """A 2R ordinary-stop plan survives a mild 1 bp stated scenario."""

    snapshot, frame, setup = qualified()
    mild = FrictionAssumptions(
        fee_bps=D("1"), entry_slippage_bps=D("1"), exit_slippage_bps=D("1")
    )
    plan = plan_trade(
        snapshot=snapshot, frame=frame, setup_id=setup.id, parameters=_parameters(mild)
    )
    assert plan.state is PlanState.PLANNABLE
    (target,) = plan.targets
    assert target.level.value == D(116)
    assert target.r_multiple == D("2.00000000")  # raw R unchanged
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "passed"
    assert (
        "nearest retained target 116: net reward 3.95440004 / net stop loss "
        "2.04440002 = net R 1.93425944 vs raw R 2.00000000 (historical "
        "friction-adjusted R vs original risk 1.97720002)" in economic.reason
    )


def test_tiny_stop_raw_passes_but_costs_fail() -> None:
    """Raw 2R with a 2/112 risk: an extreme scenario drives net R to 0.11."""

    snapshot, frame, setup = qualified()
    extreme = FrictionAssumptions(
        fee_bps=D("100"), entry_slippage_bps=D("50"), exit_slippage_bps=D("50")
    )
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(extreme),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("economic_nonviability",)
    minimum_r = next(r for r in plan.rules if r.rule_id == "minimum_r_multiple")
    assert minimum_r.outcome.value == "passed"  # raw floor still clears
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "failed"
    assert (
        "116: net reward 0.58020 / net stop loss 5.33010 = net R 0.10885349 "
        "< min_cost_adjusted_r_multiple 1 under scenario fee_bps=100, "
        "entry_slippage_bps=50, exit_slippage_bps=50 (historical "
        "friction-adjusted R vs original risk 0.29010000)"
        in plan.excluded_targets
    )


def test_net_reward_covers_original_risk_but_not_net_stop_loss() -> None:
    """The reviewer's case: historical adj-R > 1 while net reward/net loss < 1.

    Entry 112, stop 110 (raw risk 2), target 116 (raw R 2) under fee 10 bps
    and 50/50 bps adverse slippage:

    * historical friction-adjusted R = net reward / ORIGINAL risk =
      ``1.31601000`` — above the 1R floor, so the old gate would have
      certified the target;
    * net reward-to-net stop loss = ``0.78991960`` — below the floor, because
      the stop hit itself pays the same fees and adverse slippage, making the
      net stop loss (3.332010) larger than the net reward (2.632020).

    The gate must use the second number: the target is excluded and the plan
    is refused.
    """

    snapshot, frame, setup = qualified()
    scenario = FrictionAssumptions(
        fee_bps=D("10"), entry_slippage_bps=D("50"), exit_slippage_bps=D("50")
    )
    # Compute both metrics directly from the shared cost model and pin the
    # direction of the gap: the historical metric passes, the net metric fails.
    historical = friction_adjusted_r_from_levels(
        entry=D(112),
        exit_level=D(116),
        risk_per_unit=D(2),
        direction="bullish",
        assumptions=scenario,
    )
    net_ratio = friction_net_reward_to_risk(
        entry=D(112),
        exit_level=D(116),
        stop_level=D(110),
        direction="bullish",
        assumptions=scenario,
    )
    assert historical == D("1.31601000") > D(1)
    assert net_ratio == D("0.78991960") < D(1)
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(scenario),
    )
    assert plan.state is PlanState.NO_PLAN
    assert plan.reasons == ("economic_nonviability",)
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "failed"
    # The exclusion carries the net numbers and the historical value,
    # labelled, so the two metrics cannot be confused in the record.
    assert (
        "116: net reward 2.632020 / net stop loss 3.332010 = net R 0.78991960 "
        "< min_cost_adjusted_r_multiple 1 under scenario fee_bps=10, "
        "entry_slippage_bps=50, exit_slippage_bps=50 (historical "
        "friction-adjusted R vs original risk 1.31601000)"
        in plan.excluded_targets
    )


def test_nearest_retained_explanation_shows_retained_target_own_values() -> None:
    """A regression: the pass reason shows the nearest RETAINED target's values.

    Two targets (116 and 118) under fee 20 bps and 30/30 bps slippage: 116
    (net R 0.91961832) is excluded and 118 (net R 1.55949109) is retained.
    The explanation must describe 118 with 118's own numbers — never the
    first evaluated target 116's.
    """

    snapshot, frame, setup = two_target_fixture()
    scenario = FrictionAssumptions(
        fee_bps=D("20"), entry_slippage_bps=D("30"), exit_slippage_bps=D("30")
    )
    plan = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(scenario),
    )
    assert plan.state is PlanState.PLANNABLE
    assert [t.level.value for t in plan.targets] == [D(118)]
    # The excluded (nearest evaluated) target carries its own values.
    assert (
        "116: net reward 2.860024 / net stop loss 3.110012 = net R 0.91961832 "
        "< min_cost_adjusted_r_multiple 1 under scenario fee_bps=20, "
        "entry_slippage_bps=30, exit_slippage_bps=30 (historical "
        "friction-adjusted R vs original risk 1.43001200)"
        in plan.excluded_targets
    )
    economic = next(r for r in plan.rules if r.rule_id == "economic_viability")
    assert economic.outcome.value == "passed"
    # The retained target's OWN net numbers, its own raw R — and crucially
    # NOT the excluded target 116's net R (0.91961832) anywhere in the
    # pass reason.
    assert (
        "nearest retained target 118: net reward 4.850036 / net stop loss "
        "3.110012 = net R 1.55949109 vs raw R 3.00000000 (historical "
        "friction-adjusted R vs original risk 2.42501800)" in economic.reason
    )
    assert "0.91961832" not in economic.reason


def test_scenario_change_moves_the_plan_identity() -> None:
    """Different declared scenarios are different planning cohorts."""

    snapshot, frame, setup = qualified()
    a = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(FrictionAssumptions(fee_bps=D("1"))),
    )
    b = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(FrictionAssumptions(fee_bps=D("2"))),
    )
    again = plan_trade(
        snapshot=snapshot,
        frame=frame,
        setup_id=setup.id,
        parameters=_parameters(FrictionAssumptions(fee_bps=D("1"))),
    )
    assert a.id == again.id  # deterministic under one scenario
    assert a.id != b.id  # a scenario change moves the identity namespace
    assert a.planning_rules_version == b.planning_rules_version == "trade-planning-v3"


# ----------------------------------------------------------------------
# Step 12 wiring: the forward runner
# ----------------------------------------------------------------------


def test_forward_withholds_paper_plans_without_cost_scenario() -> None:
    """An explicit zero runner scenario withholds every paper plan, explicitly."""

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(friction=FrictionAssumptions()),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0
    # The labelled series qualifies two setups at that close; every refusal is
    # recorded with the same explicit reason (never a silent skip).
    withheld = [o for o in harness.observations() if o.no_trade_reason is not None]
    assert withheld
    assert {o.no_trade_reason for o in withheld} == {
        ECONOMIC_VIABILITY_UNVERIFIABLE_REASON
    }


def test_forward_bare_default_parameters_withhold_explicitly() -> None:
    """Bare ``ForwardParameters()`` — the operator who never configured costs.

    This is the exact state BRAIN is in when it is restarted without the
    shared configuration's ``cost_fee_bps`` / ``cost_entry_slippage_bps`` /
    ``cost_exit_slippage_bps`` set (all default to 0): the runner withholds
    every paper plan, and every withheld observation records the full,
    explicit reason — the system knows why it is not creating plans, and the
    reason tells the operator precisely what to configure.
    """

    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(),  # friction unset -> zero scenario
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0
    withheld = [o for o in harness.observations() if o.no_trade_reason is not None]
    assert withheld
    assert {o.no_trade_reason for o in withheld} == {
        ECONOMIC_VIABILITY_UNVERIFIABLE_REASON
    }
    # The recorded reason is a full sentence, not a bare code: it names the
    # withheld state and the missing input the operator must provide.
    assert "NO TRADE" in withheld[0].no_trade_reason
    assert "no reliable (non-zero) cost assumptions were provided" in (
        withheld[0].no_trade_reason
    )
    assert "withheld" in withheld[0].no_trade_reason


def test_forward_records_economic_nonviability_reason() -> None:
    """A declared scenario that fails the target records the exact refusal reason."""

    heavy = FrictionAssumptions(
        fee_bps=D("500"), entry_slippage_bps=D("50"), exit_slippage_bps=D("50")
    )
    harness = make_harness(
        series=labelled_series(),
        ledger_start=QUALIFYING_BOUNDARY,
        parameters=ForwardParameters(friction=heavy),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 0
    withheld = [o for o in harness.observations() if o.no_trade_reason is not None]
    assert withheld
    assert {o.no_trade_reason for o in withheld} == {ECONOMIC_NONVIABILITY_REASON}
    # The withholding is a refusal, not a missed opportunity: every observation
    # is still recorded (with its setup id) and the candidate is monitored.
    assert all(o.setup_id for o in withheld)


def test_forward_records_declared_friction_on_created_plans() -> None:
    """A created paper plan records the exact scenario its gate used."""

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    result = harness.run(refresh_market_data=False)
    assert result.paper_plans_created == 1
    plan = harness.plans()[0]
    assert plan.planning_rules_version == "trade-planning-v3"
    assert dict(plan.strategy_versions)["trade_planning_rules"] == "trade-planning-v3"
    # The service forces its declared friction into the planning parameters, so
    # the recorded friction is the gate scenario, and the recorded planning
    # configuration is the gate configuration.
    assert plan.friction_fingerprint == DEFAULT_COST_SCENARIO.fingerprint()
    assert plan.plan_config_fingerprint == (
        harness.service.planning_parameters.fingerprint()
    )


def test_dashboard_and_forward_runner_share_the_plan_identity(tmp_path) -> None:
    """Same decision, same declared scenario: dashboard and runner agree on the plan id."""

    engine, url = migrated_engine(tmp_path, "economic-viability.sqlite3")
    insert_candles(engine, labelled_series())
    from trading_assistant.web.dashboard_service import DashboardService
    from trading_assistant.web.state import AppState

    state = AppState(
        engine, settings=make_settings(url), clock=lambda: QUALIFYING_BOUNDARY
    )
    payload = DashboardService(state).dashboard(
        symbol="BTC/USDT", timeframe="1h", as_of=QUALIFYING_BOUNDARY
    )
    dashboard_plan = payload["plan"]
    assert dashboard_plan["state"] == "PLANNABLE"

    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    paper = harness.plans()[0]
    # The dashboard plans with the forward service's planning parameters, so
    # the plan id the dashboard shows is the plan id the runner records.
    assert paper.plan_id == dashboard_plan["id"]
    assert paper.planning_rules_version == dashboard_plan["planning_rules_version"]
    # No scenario mismatch is flagged when both surfaces plan under the same
    # configuration.
    assert payload["planning"]["scenario_mismatch"] is None
    # Re-query after the plan is stored: the fingerprints still match.
    after = DashboardService(state).dashboard(
        symbol="BTC/USDT", timeframe="1h", as_of=QUALIFYING_BOUNDARY
    )
    assert after["planning"]["scenario_mismatch"] is None


def test_dashboard_flags_cost_scenario_mismatch_with_runner(tmp_path) -> None:
    """A runner started under a different cost scenario is flagged, not hidden.

    The dashboard plans with the shared configuration (1/1/1 bps in these
    settings); the forward runner is started on the SAME database with a
    different declared scenario (the CLI-override case). The dashboard must
    say out loud that its live plan and the stored paper plan are NOT the
    same decision.
    """

    from forward_fixtures import (
        FakeExchange,
        Harness,
        forward_settings,
        make_service,
    )
    from trading_assistant.web.dashboard_service import DashboardService
    from trading_assistant.web.state import AppState

    engine, url = migrated_engine(tmp_path, "mismatch.sqlite3")
    insert_candles(engine, labelled_series())

    state = AppState(
        engine, settings=make_settings(url), clock=lambda: QUALIFYING_BOUNDARY
    )
    # Before the runner stores anything there is nothing to compare.
    before = DashboardService(state).dashboard(
        symbol="BTC/USDT", timeframe="1h", as_of=QUALIFYING_BOUNDARY
    )
    assert before["planning"]["scenario_mismatch"] is None

    # The runner records under a DIFFERENT cost scenario (e.g. CLI overrides),
    # into the same database the dashboard reads.
    override = FrictionAssumptions(
        fee_bps=D("50"), entry_slippage_bps=D("25"), exit_slippage_bps=D("25")
    )
    fake = FakeExchange()
    fake.set_candles(labelled_series())
    clock = {"t": QUALIFYING_BOUNDARY}
    service = make_service(
        engine,
        forward_settings(url, raw_dir=tmp_path / "raw"),
        fake,
        clock=lambda: clock["t"],
        parameters=ForwardParameters(friction=override),
        ledger_start=QUALIFYING_BOUNDARY,
    )
    harness = Harness(
        engine=engine,
        settings=service.settings,
        clock=clock,
        service=service,
        source=fake,
        series=labelled_series(),
    )
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    stored = harness.plans()[0]

    # The dashboard's shared-configuration plan is a DIFFERENT plan id, and
    # the mismatch is stated explicitly with both fingerprints.
    after = DashboardService(state).dashboard(
        symbol="BTC/USDT", timeframe="1h", as_of=QUALIFYING_BOUNDARY
    )
    assert after["plan"]["id"] != stored.plan_id
    mismatch = after["planning"]["scenario_mismatch"]
    assert mismatch is not None
    assert stored.plan_config_fingerprint in mismatch
    assert state.forward.planning_parameters.fingerprint() in mismatch
    assert "NOT the same decision" in mismatch
