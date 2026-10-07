"""Component #5 audit: trade-planning (Step 6) anti-lookahead regression tests.

Rule 5 bounds the seed, confirmation, reference, and setup timestamps at the
planning instant, but two consumed facts escaped the bound:

* the PLAN_CLOSE entry anchor — a latest close whose candle had not closed by
  ``as_of`` was silently used as the entry;
* the confirmation event's own candle — a held retest (or confirmation
  breakout) with ``known_at <= as_of`` but a future-dated candle passed Rule 5
  while its close feeds FROZEN_CONFIRMATION entries.

Both now report ``future_evidence_used`` (INVALID). Coherent Step 5 replays
always satisfy the bound, so production plans are unchanged.
"""

from __future__ import annotations

from dataclasses import replace

from test_setup_qualification import at
from test_trade_planning import qualified, rule_for

from trading_assistant.setup_qualification import RuleOutcome
from trading_assistant.trade_planning import PlanState, plan_trade


def test_future_plan_close_cannot_anchor_entry() -> None:
    snapshot, planning_frame, setup = qualified()
    volatility = replace(
        planning_frame.patterns.structure.volatility,
        latest_candle_timestamp=at(99),
    )
    corrupt = replace(
        planning_frame,
        patterns=replace(
            planning_frame.patterns,
            structure=replace(
                planning_frame.patterns.structure, volatility=volatility
            ),
        ),
    )
    plan = plan_trade(snapshot=snapshot, frame=corrupt, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("future_evidence_used",)
    assert (
        rule_for(plan, "entry_level_available").outcome is RuleOutcome.FAIL
    )
    assert plan.entry.value is None


def test_future_confirmation_candle_is_rejected() -> None:
    snapshot, planning_frame, setup = qualified()
    (retest,) = planning_frame.patterns.retests
    corrupt_retest = replace(
        retest, candle=replace(retest.candle, timestamp=at(99))
    )
    corrupt = replace(
        planning_frame,
        patterns=replace(planning_frame.patterns, retests=(corrupt_retest,)),
    )
    plan = plan_trade(snapshot=snapshot, frame=corrupt, setup_id=setup.id)
    assert plan.state is PlanState.INVALID
    assert plan.reasons == ("future_evidence_used",)
    reason = rule_for(plan, "availability_at_as_of").reason
    assert "candle closes" in reason
    assert plan.entry.value is None and plan.targets == ()
