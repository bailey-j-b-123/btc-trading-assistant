"""Independent money arithmetic and explicit-cohort controls, offline only."""

from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_assistant.costs import FrictionAssumptions
from trading_assistant.trade_planning.economics import assess, declared_scenario
from trading_assistant.trade_planning.parameters import PlanningParameters
from trading_assistant.trade_planning.planner import plan_trade
from test_trade_planning import qualified


@pytest.mark.parametrize(
    "direction,stop,target", [("bullish", "90", "120"), ("bearish", "110", "80")]
)
def test_both_sides_exact_fills_fees_and_monotonic_costs(direction, stop, target):
    normal = FrictionAssumptions(
        fee_bps=D(10), entry_slippage_bps=D(4), exit_slippage_bps=D(4)
    )
    stress = FrictionAssumptions(
        fee_bps=D(20), entry_slippage_bps=D(11), exit_slippage_bps=D(11)
    )
    a = assess(
        entry=D(100), stop=D(stop), target=D(target), direction=direction, costs=normal
    )
    sign = D(1) if direction == "bullish" else D(-1)
    e = D(100) * (1 + sign * D(".0004"))
    t = D(target) * (1 - sign * D(".0004"))
    s = D(stop) * (1 - sign * D(".0004"))
    assert a.reward == sign * (t - e) - (e + t) * D(".001")
    assert a.loss == sign * (e - s) + (e + s) * D(".001") > 10
    b = assess(
        entry=D(100), stop=D(stop), target=D(target), direction=direction, costs=stress
    )
    assert 0 < b.net_r < a.net_r < 2
    assert a.passed and b.passed


def test_gate_must_not_round_a_subfloor_reward_up_to_one():
    tiny = FrictionAssumptions(fee_bps=D("0.000000001"))
    a = assess(entry=D(100), stop=D(90), target=D(110), direction="bullish", costs=tiny)
    assert a.net_r == 1 and a.reward < a.loss and not a.passed


def test_optin_raw_floor_also_compares_exact_amounts_before_display_rounding():
    from trading_assistant.trade_planning.planner import _PlanContext, _apply_minimum_r
    from trading_assistant.trade_planning.models import PlannedTarget, UNKNOWN_LEVEL

    context = _PlanContext(
        PlanningParameters(
            min_r_multiple=D(2), economic_costs=FrictionAssumptions(fee_bps=D("0.1"))
        )
    )
    context.risk_per_unit = D(10)
    context.targets = (PlannedTarget(UNKNOWN_LEVEL, D("19.999999999"), D(2), True),)
    _apply_minimum_r(context)
    assert context.targets == () and context.reasons == ["minimum_r_multiple_not_met"]


@pytest.mark.parametrize("bearish", [False, True])
def test_real_planner_explicit_gate_version_and_unchanged_levels(bearish):
    q, frame, setup = qualified(bearish)
    legacy = plan_trade(snapshot=q, frame=frame, setup_id=setup.id)
    assert legacy.actionable
    parameters = PlanningParameters(
        economic_costs=FrictionAssumptions(fee_bps=D("0.1"))
    )
    mild = plan_trade(snapshot=q, frame=frame, setup_id=setup.id, parameters=parameters)
    assert mild.actionable and mild.id != legacy.id
    assert mild.entry == legacy.entry and mild.stop == legacy.stop
    assert (
        mild.planning_rules_version == "trade-planning-v2+economic-planning-research-v1"
    )
    assert "economic_viability" in mild.passed_rules
    high = plan_trade(
        snapshot=q,
        frame=frame,
        setup_id=setup.id,
        parameters=replace(
            parameters, economic_costs=FrictionAssumptions(fee_bps=D(9000))
        ),
    )
    assert not high.actionable and high.reasons == ("economic_nonviability",)
    assert high.entry == legacy.entry and high.stop == legacy.stop
    assert high.targets == () and high.excluded_targets
    zero = plan_trade(
        snapshot=q,
        frame=frame,
        setup_id=setup.id,
        parameters=replace(parameters, economic_costs=FrictionAssumptions()),
    )
    assert zero.reasons == ("economic_viability_unverifiable",)
    assert plan_trade(snapshot=q, frame=frame, setup_id=setup.id) == legacy


def test_legacy_default_fingerprint_is_preserved_and_cost_fingerprint_is_distinct():
    assert (
        PlanningParameters().fingerprint()
        == "ef32f0064740b758d06dca4bd2df9130d90a39689d10aa5057f922916806743f"
    )
    with pytest.raises(ValueError):
        PlanningParameters(min_cost_adjusted_r_multiple=D(".99"))
    with pytest.raises(TypeError):
        PlanningParameters(economic_costs={})


def test_forward_and_historical_bind_the_same_reported_cost_scenario(
    monkeypatch, tmp_path
):
    from trading_assistant.forward_testing.service import ForwardTestService
    from trading_assistant.forward_testing.parameters import ForwardParameters
    from trading_assistant.historical_validation.parameters import (
        FrictionAssumptions as Recorded,
        ValidationConfig,
    )
    from trading_assistant.historical_validation.service import (
        HistoricalValidationService,
    )
    from web_fixtures import (
        migrated_engine,
        make_settings,
        insert_candles,
        qualifying_candles,
    )
    from market_structure_fixtures import EXCHANGE, SYMBOL

    scenario = Recorded(fee_bps=D(10), entry_slippage_bps=D(4), exit_slippage_bps=D(4))
    requested = PlanningParameters(economic_costs=FrictionAssumptions(fee_bps=D("0.1")))
    engine, url = migrated_engine(tmp_path)
    insert_candles(engine, qualifying_candles())
    try:
        forward = ForwardTestService(
            engine,
            settings=make_settings(url),
            parameters=ForwardParameters(friction=scenario),
            planning_parameters=requested,
        )
        assert forward.planning_parameters.economic_costs == declared_scenario(scenario)
        historical = HistoricalValidationService(engine)
        captured = []
        original = historical._records

        def capture(*args, **kwargs):
            captured.append(kwargs["planning_parameters"])
            return original(*args, **kwargs)

        monkeypatch.setattr(historical, "_records", capture)
        historical.validate(
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="1h",
            config=ValidationConfig(friction=scenario),
            planning_parameters=requested,
        )
        assert (
            captured
            and captured[0].economic_costs == forward.planning_parameters.economic_costs
        )
        q, frame, setup = qualified(False)
        a = forward._plan(snapshot=q, frame=frame, setup=setup)
        b = plan_trade(
            snapshot=q, frame=frame, setup_id=setup.id, parameters=captured[0]
        )
        assert a.to_json_dict() == b.to_json_dict()
        assert requested.economic_costs.fee_bps == D("0.1")
    finally:
        engine.dispose()


@pytest.mark.parametrize("fee", ["0", "9000"])
def test_economic_refusals_have_explicit_forward_observation_explanations(fee):
    from trading_assistant.forward_testing.service import ForwardTestService

    q, frame, setup = qualified(False)
    plan = plan_trade(
        snapshot=q,
        frame=frame,
        setup_id=setup.id,
        parameters=PlanningParameters(
            economic_costs=FrictionAssumptions(fee_bps=D(fee))
        ),
    )
    reason = ForwardTestService._economic_refusal_reason(plan)
    assert reason.startswith("NO TRADE — ")
    assert (
        ("zero declared costs" in reason) if fee == "0" else ("net stop loss" in reason)
    )
