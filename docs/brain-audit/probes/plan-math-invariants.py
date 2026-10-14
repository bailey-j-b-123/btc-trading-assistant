"""BRAIN AUDIT probe: independently re-derive the plan arithmetic from the plan itself.

Uses the project's own synthetic qualification fixtures (tests/) to obtain
QUALIFIED setups, then re-computes every published number with plain Decimal
arithmetic and compares it to what the planner published. No trading rule is
changed; nothing is written.

Run:  PYTHONPATH=<worktree>/src:<worktree>/tests python plan-math-invariants.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/user/audit-wt/pr69").resolve()
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from test_trade_planning import (  # noqa: E402
    qualified,
    qualified_continuation,
    qualified_failed_breakout_reversal,
    qualified_range,
    qualified_reversal_long,
    qualified_reversal_short,
)

from trading_assistant.market_structure.numeric import divide, quantize_derived  # noqa: E402
from trading_assistant.trade_planning.levels import is_long  # noqa: E402
from trading_assistant.trade_planning.models import PlanState  # noqa: E402
from trading_assistant.trade_planning.planner import plan_trade  # noqa: E402
from trading_assistant.trade_planning.parameters import PlanningParameters  # noqa: E402

SCENARIOS = {
    "continuation long": qualified(False),
    "continuation short": qualified(True),
    "reversal long": qualified_reversal_long(),
    "reversal short": qualified_reversal_short(),
    "failed-breakout reversal": qualified_failed_breakout_reversal(),
    "range long": qualified_range(False),
    "rich continuation": qualified_continuation(upto=9),
}

failures = 0
planned = 0
refused = 0

for name, parts in SCENARIOS.items():
    snapshot, frame, setup = parts[0], parts[1], parts[2]
    plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
    if plan.state is not PlanState.PLANNABLE:
        refused += 1
        print(f"[refused] {name}: state={plan.state} reasons={plan.reasons}")
        continue
    planned += 1
    long = is_long(plan.direction)
    entry = plan.entry.value
    stop = plan.stop.value
    risk = plan.risk_per_unit
    problems: list[str] = []

    # 1. entry really is the Step 3 latest closed close the frame carries
    latest_close = frame.patterns.structure.volatility.latest_close
    if latest_close is not None and entry != latest_close:
        problems.append(f"entry {entry} != frame latest close {latest_close}")

    # 2. risk is exactly |entry - stop|, quantized
    if risk != quantize_derived(abs(entry - stop)):
        problems.append(f"risk {risk} != quantize(|{entry}-{stop}|)")

    # 3. stop on the losing side
    if long and not stop < entry:
        problems.append(f"long stop {stop} not below entry {entry}")
    if not long and not stop > entry:
        problems.append(f"short stop {stop} not above entry {entry}")

    # 4. every target: correct side, reward re-derived, R re-derived
    floor = PlanningParameters().min_r_multiple
    if not plan.targets:
        problems.append("planned with no targets")
    for target in plan.targets:
        value = target.level.value
        reward = value - entry if long else entry - value
        if reward <= 0:
            problems.append(f"target {value} on the wrong side of entry {entry}")
        if target.reward_per_unit != reward:
            problems.append(f"target {value} reward {target.reward_per_unit} != {reward}")
        expected_r = quantize_derived(divide(reward, risk)) if risk and risk > 0 else None
        if target.r_multiple != expected_r:
            problems.append(f"target {value} R {target.r_multiple} != {expected_r}")
        if target.r_multiple is None or target.r_multiple < floor:
            problems.append(f"target {value} R {target.r_multiple} below floor {floor}")

    status = "OK" if not problems else "FAIL"
    if problems:
        failures += 1
    print(
        f"[{status}] {name}: dir={plan.direction} entry={entry} stop={stop} risk={risk} "
        f"targets={[(t.level.value, t.reward_per_unit, t.r_multiple) for t in plan.targets]}"
    )
    for problem in problems:
        print(f"        !! {problem}")

# 5. no-future-evidence invariant on every planned trade: the entry anchor candle
#    must have closed at or before the planning as_of.
from datetime import timedelta  # noqa: E402

from trading_assistant.market_structure.candles import interval_for_timeframe  # noqa: E402

print("\nno-future-evidence check (entry anchor closed by the plan as_of):")
for name, parts in SCENARIOS.items():
    snapshot, frame, setup = parts[0], parts[1], parts[2]
    plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup.id)
    if plan.state is not PlanState.PLANNABLE:
        continue
    opened = frame.patterns.structure.volatility.latest_candle_timestamp
    interval = interval_for_timeframe(plan.timeframe)
    closes = opened + interval
    ok = plan.as_of is not None and closes <= plan.as_of
    print(f"  [{'OK' if ok else 'FAIL'}] {name}: anchor closes {closes} <= plan as_of {plan.as_of}")
    if not ok:
        failures += 1

print(f"\nplanned={planned} refused={refused} invariant failures={failures}")
