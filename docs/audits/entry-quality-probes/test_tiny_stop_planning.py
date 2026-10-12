"""Executable synthetic test demonstrating the Trade 2 tiny-stop planning issue.

Proves with explicit assertions that BRAIN's Step 6 trade planner accepts an
economically unrealistic stop distance ($8.32 / ~0.996 bps on an $83k BTC frame)
and outputs a PLANNABLE trade state with an inflated R-multiple (> 5.2R), because:
1. planner._resolve_stop only checks `risk > 0` (no minimum dollar, bps, or ATR floor).
2. StopBufferMode defaults to NONE, placing the stop exactly at reference.band_low.
3. planner._apply_minimum_r only checks `r_multiple >= 1.0` (blind to absolute scale).

Can be executed directly:
    python docs/audits/entry-quality-probes/test_tiny_stop_planning.py
or with pytest:
    pytest docs/audits/entry-quality-probes/test_tiny_stop_planning.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from decimal import Decimal as D

# Ensure repo root and tests directory are importable
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

from test_setup_qualification import breakout, held
from test_trade_planning import bar, candidate, frame, result, rule_for
from trading_assistant.trade_planning.models import PlanState, RuleOutcome
from trading_assistant.trade_planning.planner import plan_trade


def test_tiny_stop_distance_accepted_by_planner():
    """Trade 2 synthetic reproduction: $8.32 risk passes Step 6 planning."""
    # 1. Structural setup: reference swing boundary at $83,521.15
    seed = breakout()
    ref = replace(
        seed.reference,
        band_low=D("83521.15"),
        band_high=D("83521.15"),
    )
    seed = replace(seed, reference=ref)

    # 2. Structural target at $83,572.96 (placed via displaced swing high at index 4)
    target_level = "83572.96"

    # 3. Decision-time candle close at $83,529.47 ($8.32 above the stop)
    decision_close = "83529.47"
    candles = tuple(
        bar(j, decision_close, high=(target_level if j == 4 else None))
        for j in range(8)
    )

    frames = [
        frame(6, (seed,), close=decision_close, candles=candles[:6]),
        frame(7, (seed, held(seed)), close=decision_close, candles=candles[:7]),
    ]
    snapshot = result(frames)
    setup = candidate(snapshot, seed)

    # 4. Execute Step 6 planning with default PlanningParameters
    plan = plan_trade(snapshot=snapshot, frame=frames[-1], setup_id=setup.id)

    # 5. Assertions verifying the exact planning defect
    # The plan is marked PLANNABLE despite having an $8.32 risk distance
    assert plan.state is PlanState.PLANNABLE, f"Expected PLANNABLE, got {plan.state}"

    # Entry is anchored to the latest closed candle close
    assert plan.entry.value == D("83529.47")

    # Stop is placed at reference.band_low with zero buffer
    assert plan.stop.value == D("83521.15")

    # Risk distance is exactly $8.32 (0.00996% / 0.996 basis points of entry)
    assert plan.risk_per_unit == D("8.32")
    risk_bps = (plan.risk_per_unit / plan.entry.value) * D(10000)
    assert risk_bps < D("1.0"), f"Expected < 1 bp risk, got {risk_bps}"

    # Target is confirmed and reward is $43.49
    assert len(plan.targets) >= 1
    nearest_target = plan.targets[0]
    assert nearest_target.level.value == D("83572.96")
    assert nearest_target.reward_per_unit == D("43.49")

    # R-multiple is artificially inflated to 5.227R because risk is minuscule
    expected_r = D("5.22716346")
    assert nearest_target.r_multiple == expected_r

    # Rule outcomes demonstrate why it passed:
    # 'positive_risk' passes simply because risk > 0
    positive_risk_rule = rule_for(plan, "positive_risk")
    assert positive_risk_rule.outcome is RuleOutcome.PASS
    assert "risk_per_unit = |entry - stop| = 8.32" in positive_risk_rule.reason

    # 'minimum_r_multiple' passes because 5.23R >= 1.0R floor
    min_r_rule = rule_for(plan, "minimum_r_multiple")
    assert min_r_rule.outcome is RuleOutcome.PASS
    assert plan.preferred_r_multiple_met is True

    print("PASS: test_tiny_stop_distance_accepted_by_planner")
    print(f"  Entry: ${plan.entry.value}")
    print(f"  Stop:  ${plan.stop.value} (Risk: ${plan.risk_per_unit} / {risk_bps:.3f} bps)")
    print(f"  Target: ${nearest_target.level.value} (Reward: ${nearest_target.reward_per_unit})")
    print(f"  R-Multiple: {nearest_target.r_multiple}R")
    print(f"  State: {plan.state.value}")


if __name__ == "__main__":
    test_tiny_stop_distance_accepted_by_planner()
