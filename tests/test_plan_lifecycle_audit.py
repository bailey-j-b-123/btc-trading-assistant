"""Synthetic lifecycle controls, never historical performance evidence."""

from dataclasses import replace
from decimal import Decimal as D
from datetime import timedelta
import json
import pytest
from test_strategy_lab import candles
from trading_assistant.strategy_lab.models import Execution, Strategy
from trading_assistant.strategy_lab.simulation import initial_state, step


def signal(candles, strategy, *, known_at):
    return {
        "known_at": known_at.isoformat(),
        "candle_open": candles[-1].timestamp.isoformat(),
        "source_plan_id": "one-genuine-plan-fixture",
        "reference": "100",
        "stop": "90",
        "target": "120",
        "evidence": {"regime": "SYNTHETIC"},
    }


def bars():
    return tuple(
        replace(c, open=D(100), high=D(101), low=D(99), close=D(100))
        for c in candles(7)
    )


def test_occupied_slot_does_not_consume_a_plan_that_never_filled():
    c = bars()
    e = Execution()
    s = Strategy()
    state = initial_state(e)
    # A different, legitimately pending plan occupies the slot through bar0.
    state["position"] = {
        "quantity": "1",
        "fill": "100",
        "entry_fee": "0.1",
        "entry_at": c[0].timestamp.isoformat(),
        "held": 0,
        "stop": "90",
        "target": "120",
        "signal_id": "other",
        "decision": c[0].timestamp.isoformat(),
        "evidence": {},
        "risk_budget": "50",
        "equity_at_decision": "10000",
    }
    state["cash"] = "9899.9"
    a, events = step(state, c, 0, s, e, decision_function=signal)
    assert a["rejections"]["occupied_slot"] == 1
    # Existing position exits normally on the next bar; the source is freshly
    # re-evaluated at that later close, never replayed at its old timestamp.
    updated = list(c)
    updated[1] = replace(c[1], high=D(121))
    b, _ = step(a, tuple(updated), 1, s, e, decision_function=signal)
    d, events = step(b, tuple(updated), 2, s, e, decision_function=signal)
    assert d["filled"] == 1, (
        "unfilled blocked plan must be allowed a later valid attempt"
    )
    assert d["source_plan_states"]["one-genuine-plan-fixture"] == "filled"


@pytest.mark.parametrize("reason", ["entry_gap", "notional", "capital", "data_gap"])
def test_rejected_unfilled_attempt_can_retry_only_after_new_evaluation(reason):
    c = list(bars())
    e = Execution()
    s = Strategy()
    a, _ = step(initial_state(e), c, 0, s, e, decision_function=signal)
    if reason == "entry_gap":
        c[1] = replace(c[1], open=D(110), high=D(111), low=D(109), close=D(110))
    if reason == "notional":
        a["pending"]["quantity"] = "0.01"
    if reason == "capital":
        a["cash"] = "1"
    if reason == "data_gap":
        c[1] = replace(c[1], timestamp=c[1].timestamp.replace(hour=3))
    b, events = step(a, c, 1, s, e, decision_function=(lambda *a, **k: None))
    assert b["filled"] == 0 and any(x["kind"] == "rejection" for x in events)
    assert "one-genuine-plan-fixture" not in b.get("seen_source_plans", [])
    assert b["source_plan_states"]["one-genuine-plan-fixture"].startswith("rejected:")
    if reason == "capital":
        b["cash"] = "10000"  # Accounting fixture, not a deposit claim.
    b["last_timestamp"] = c[1].timestamp.isoformat()
    for i in range(2, len(c)):
        c[i] = replace(c[i], timestamp=c[i - 1].timestamp + timedelta(hours=1))
    d, _ = step(b, c, 2, s, e, decision_function=signal)
    f, _ = step(d, c, 3, s, e, decision_function=signal)
    assert f["filled"] == 1


def test_filled_plan_is_consumed_even_after_its_position_closes():
    c = list(bars())
    e = Execution()
    s = Strategy()
    a, _ = step(initial_state(e), c, 0, s, e, decision_function=signal)
    b, _ = step(a, c, 1, s, e, decision_function=signal)
    assert b["filled"] == 1
    c[2] = replace(c[2], high=D(121))
    d, _ = step(b, c, 2, s, e, decision_function=signal)
    f, _ = step(d, c, 3, s, e, decision_function=signal)
    assert f["filled"] == 1 and f["position"] is None and f["pending"] is None
    assert f["source_plan_states"]["one-genuine-plan-fixture"] == "closed"


def test_restart_retains_pending_and_spent_identities(tmp_path):
    from trading_assistant.strategy_lab.store import Store

    c = list(bars())
    e = Execution()
    s = Strategy()
    store = Store(tmp_path / "lab")
    key, _ = store.experiment({"test": "synthetic lifecycle"}, initial_state(e))
    a, events = step(initial_state(e), c, 0, s, e, decision_function=signal)
    store.checkpoint(key, a, events)
    store.close()
    store = Store(tmp_path / "lab")
    a = json.loads(store.db.execute("select state from experiments").fetchone()[0])
    b, events = step(a, c, 1, s, e, decision_function=signal)
    store.checkpoint(key, b, events)
    store.close()
    store = Store(tmp_path / "lab")
    b = json.loads(store.db.execute("select state from experiments").fetchone()[0])
    c[2] = replace(c[2], high=D(121))
    d, _ = step(b, c, 2, s, e, decision_function=signal)
    f, _ = step(d, c, 3, s, e, decision_function=signal)
    assert (
        f["filled"] == 1
        and f["source_plan_states"]["one-genuine-plan-fixture"] == "closed"
    )

    def fresh(c, s, **kwargs):
        return {
            **signal(c, s, **kwargs),
            "source_plan_id": "different-actual-plan-fixture",
        }

    g, _ = step(f, c, 4, s, e, decision_function=fresh)
    h, _ = step(g, c, 5, s, e, decision_function=fresh)
    assert h["filled"] == 2
    store.close()
