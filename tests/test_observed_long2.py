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
