"""Training-only controls; synthetic labels never establish profitability."""

from trading_assistant.market_data.types import Candle
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from trading_assistant.strategy_lab import discovery as legacy
from trading_assistant.strategy_lab import observed_long2 as long2
from trading_assistant.strategy_lab.models import canonical




def training(n=450):
    return tuple(Candle("binance", "BTC/USDT", "1h", legacy.TRAIN_START + i * legacy.HOUR,
                     D(100), D(101) if i % 2 else D(103), D(99), D(100), D(10)) for i in range(n))


def test_scope_refuses_any_evaluation_bar(monkeypatch):
    cs = training()
    monkeypatch.setattr(legacy, "TRAIN_END", legacy.TRAIN_START + len(cs) * legacy.HOUR)
    with pytest.raises(ValueError, match="discovery prefix"):
        long2.discover_long2(cs + (replace(cs[-1], timestamp=legacy.TRAIN_END),))
    with pytest.raises(ValueError, match="discovery prefix"):
        long2.discover_long2(cs[:-1])
    with pytest.raises(ValueError, match="discovery prefix"):
        long2.discover_long2(cs[:180] + cs[181:])


def test_deterministic_learned_rules_and_fixed_budget(monkeypatch):
    cs = training()
    monkeypatch.setattr(legacy, "TRAIN_END", legacy.TRAIN_START + len(cs) * legacy.HOUR)
    left = long2.discover_long2(cs)
    right = long2.discover_long2(cs)
    assert canonical(left) == canonical(right)
    assert left["minimum_upside_percent"] == "2"
    assert left["horizon_bars"] == 120
    assert left["evaluation_or_trading_performed"] is False
    assert left["split_trials"] <= 168
    assert len(left["hypotheses"]) <= 8
    accepted = [r for r in left["hypotheses"] if r["accepted"]]
    assert len({r["id"] for r in accepted}) == len(accepted)


def test_adverse_touch_same_candle_is_not_favorable(monkeypatch):
    cs = training()
    monkeypatch.setattr(legacy, "TRAIN_END", legacy.TRAIN_START + len(cs) * legacy.HOUR)
    # Every hourly candle sees high >= 102, but a same-hour low<=98 triggers
    # the conservative -2% screen. No future intrabar ordering is invented.
    changed = tuple(replace(b, low=D(97)) for b in cs)
    result = long2.discover_long2(changed)
    assert result["unconditional_success_rate"] == "0"
    assert all(not h["accepted"] for h in result["hypotheses"])


def test_fingerprint_differs_on_rule_change():
    c = legacy.Condition("upper_wick", "le", D("0.5"))
    q = dict(conditions=(c,), training_fingerprint="0" * 64,
             source_fingerprint="1" * 64, training_samples=125,
             observed_success_rate=D(".5"), unconditional_success_rate=D(".4"))
    r = long2.OpportunityRule(**q)
    assert r.fingerprint == long2.OpportunityRule(**q).fingerprint
    assert r.fingerprint != long2.OpportunityRule(**{**q, "observed_success_rate": D(".6")}).fingerprint
    with pytest.raises(ValueError):
        long2.OpportunityRule(**{**q, "training_samples": 100})


def test_long2_adapter_uses_closed_structure_and_two_percent_room():
    from trading_assistant.strategy_lab.models import research_code_fingerprint
    from trading_assistant.strategy_lab import observed_long2 as l2
    cs = tuple(replace(b, timestamp=legacy.TRAIN_END + i * legacy.HOUR) for i, b in enumerate(training(121)))
    c = legacy.Condition("relative_volume", "gt", D("0.5"))
    rule = l2.OpportunityRule((c,), "0" * 64, research_code_fingerprint(), 120, D("0.6"), D("0.4"))
    t = cs[-1].timestamp + legacy.HOUR
    answer = l2.decide_long2(cs, rule, known_at=t)
    assert answer is not None
    assert D(answer["target"]) / D(answer["reference"]) > D("1.02")
    assert D(answer["target"]) / (D(answer["reference"]) * D("1.005") * D("1.0011")) >= D("1.02")
    assert D(answer["stop"]) < D(answer["reference"])
    # If the structural resistance is too close, no 2% opportunity exists.
    near = tuple(replace(b, high=D("101")) for b in cs)
    assert l2.decide_long2(near, rule, known_at=t) is None
    # A later 1-million high cannot leak into the rule's decision.
    later = replace(cs[-1], timestamp=t, high=D("1000000"))
    assert l2.decide_long2(cs + (later,), rule, known_at=t) == answer


def test_long2_rejects_wrong_source_training_and_protected_2025():
    from datetime import datetime, UTC
    from trading_assistant.strategy_lab.models import research_code_fingerprint
    from trading_assistant.strategy_lab import observed_long2 as l2
    cs = tuple(replace(b, timestamp=legacy.TRAIN_END + i * legacy.HOUR) for i, b in enumerate(training(121)))
    c = legacy.Condition("relative_volume", "gt", D("0.5"))
    r = l2.OpportunityRule((c,), "0" * 64, research_code_fingerprint(), 120, D("0.6"), D("0.4"))
    with pytest.raises(ValueError, match="protected 2025"):
        l2.decide_long2(cs, r, known_at=datetime(2025, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="source mismatch"):
        l2.decide_long2(cs, replace(r, source_fingerprint="1" * 64), known_at=cs[-1].timestamp + legacy.HOUR)
    with pytest.raises(ValueError, match="training period"):
        l2.decide_long2(cs, r, known_at=legacy.TRAIN_START)


def test_runner_long2_adapter_reuses_existing_auditable_cash_account(tmp_path):
    from trading_assistant.strategy_lab.models import Execution, research_code_fingerprint
    from trading_assistant.strategy_lab.runner import run_experiment
    from trading_assistant.strategy_lab.store import Store
    from trading_assistant.strategy_lab.novel import independent_audit
    from test_strategy_lab import candles, manifest
    from trading_assistant.strategy_lab import observed_long2 as l2
    import json

    cs = tuple(replace(b, high=max(b.high, b.close * D("1.04"))) for b in candles(260))
    r = l2.OpportunityRule(
        (legacy.Condition("relative_volume", "gt", D("0.5")),),
        "0" * 64, research_code_fingerprint(), 120, D("0.6"), D("0.4"),
    )
    st = Store(tmp_path / "long2")
    try:
        e = Execution()
        a = run_experiment(st, cs, manifest(cs), r, e,
                           stage="walk_forward", decision_adapter="observed-long2-v1")
        assert a["complete"] and not a["deduplicated"]
        state = json.loads(st.db.execute("select state from experiments where id=?", (a["id"],)).fetchone()[0])
        assert D(independent_audit(state, st.records(a["id"]), cs, e)["cash_error"]) < D("1e-18")
        assert run_experiment(st, cs, manifest(cs), r, e,
                              stage="walk_forward", decision_adapter="observed-long2-v1")["deduplicated"]
    finally:
        st.close()
