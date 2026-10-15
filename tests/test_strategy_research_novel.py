"""Synthetic causal/accounting controls; NEVER trading-performance evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from pathlib import Path
import pytest

from trading_assistant.market_data.types import Candle
from trading_assistant.strategy_lab import novel as n
from trading_assistant.strategy_lab.data import candle_fingerprint
from trading_assistant.strategy_lab.models import Execution, Strategy
from trading_assistant.strategy_lab.simulation import initial_state, step
from trading_assistant.strategy_lab.store import Store


def fixture(name):
    start = datetime(2023, 4, 1, tzinfo=UTC)
    values = [D(100)]*121
    if name == "trend_pullback":
        values = [D(100)+D(i)/10 for i in range(121)]
        values[-2:] = [D(110), D(114)]
    elif name == "compression_expansion":
        values[:100] = [D(95) if i % 2 else D(105) for i in range(100)]
        values[-1] = D(101)
    bars = [Candle("binance", "BTC/USDT", "1h", start+timedelta(hours=i), p, p+D("0.1"), p-D("0.1"), p, D(10)) for i,p in enumerate(values)]
    def at(i, o, h, low, c):
        bars[i] = replace(bars[i], open=D(o), high=D(h), low=D(low), close=D(c))
    if name == "trend_pullback":
        at(120, "111", "114.1", "110.9", "114")
    elif name == "breakout_retest":
        at(118,"100","101.1","99.95","101")
        at(119,"100.8","100.9","100.05","100.6")
        at(120,"100.3","100.8","100.05","100.7")
    elif name == "compression_expansion":
        at(120,"100","110","99.9","101")
    else:
        at(115,"100","100.1","95","100")
        at(120,"95.1","95.3","94.9","95.2")
    return tuple(bars)


@pytest.mark.parametrize("name", n.NAMES)
def test_actual_rules_detect_positive_fixture_and_never_read_future(name):
    c = fixture(name)
    known = c[-1].timestamp+timedelta(hours=1)
    rule = n.Hypothesis(name)
    signal = n.decide(c, rule, known_at=known)
    assert signal is not None
    assert D(signal["stop"]) < D(signal["reference"]) < D(signal["target"])
    assert signal["reference"] == str(c[-1].close)
    assert signal["evidence"]["closed_count"] == 121
    future = replace(c[-1], timestamp=known, open=D(1), low=D(1), high=D(99999), close=D(99999))
    assert n.decide(c+(future,), rule, known_at=known) == signal
    assert n.decide(c, rule, known_at=known-timedelta(microseconds=1)) is None


@pytest.mark.parametrize("name", n.NAMES)
def test_missing_duplicate_insufficient_and_wrong_timeframe_refuse(name):
    c = fixture(name)
    rule = n.Hypothesis(name)
    known = c[-1].timestamp+timedelta(hours=1)
    assert n.decide(c[1:], rule, known_at=known) is None
    assert n.decide(c[:60]+c[61:], rule, known_at=known) is None
    duplicate = c[:60]+(c[59],)+c[61:]
    assert n.decide(duplicate, rule, known_at=known) is None
    assert n.decide(tuple(replace(x,timeframe="4h") for x in c), rule, known_at=known) is None
    assert n.decide(c, rule, known_at=known+timedelta(hours=1)) is None


def test_retest_identity_and_invalidation_use_actual_frozen_breakout():
    c = fixture("breakout_retest")
    rule = n.Hypothesis("breakout_retest")
    at = c[-1].timestamp+timedelta(hours=1)
    a = n.decide(c,rule,known_at=at)
    assert a["evidence"]["frozen_level"] == "100.1"
    assert a["evidence"]["breakout_open"] == c[118].timestamp.isoformat()
    different = list(c)
    different[100] = replace(c[100], high=D("100.2"))
    b = n.decide(tuple(different),rule,known_at=at)
    assert b and b["source_plan_id"] != a["source_plan_id"]
    invalid = list(c)
    invalid[119] = replace(c[119],low=D(90))
    assert n.decide(tuple(invalid),rule,known_at=at) is None
    state, _ = step(initial_state(Execution()),c,120,rule,Execution(),decision_function=n.decide)
    state["position"] = state["pending"] = None
    _, produced = step(state,c,120,rule,Execution(),decision_function=n.decide)
    assert not any(e["kind"] == "signal" for e in produced)


def test_flat_series_is_not_a_signal_and_original_definitions_are_unchanged():
    c = tuple(replace(x,open=D(100),high=D("100.1"),low=D("99.9"),close=D(100)) for x in fixture("trend_pullback"))
    for name in n.NAMES:
        assert n.decide(c,n.Hypothesis(name),known_at=c[-1].timestamp+timedelta(hours=1)) is None
    with pytest.raises(ValueError):
        n.Hypothesis("short")
    with pytest.raises(ValueError):
        n.Hypothesis("trend_pullback",timeframe="4h")
    assert Strategy().version == "lab-strategy-v1"
    path = Path(__file__).parents[1]/"configs/strategy-research-v1.json"
    assert sha256(path.read_bytes()).hexdigest() == n.SPEC_SHA
    assert len(json.loads(path.read_text())["candidates"]) == 4


def test_resume_rollback_dedup_and_independent_accounting(tmp_path,monkeypatch):
    c = list(fixture("trend_pullback"))
    for i in range(121,144):
        c.append(replace(c[-1], timestamp=c[0].timestamp+timedelta(hours=i), open=D(114),high=D(130),low=D(113),close=D(115)))
    c = tuple(c)
    monkeypatch.setattr(n,"PERIODS",(("development","2023-04-01","2023-04-07"),))
    m = {"timeframe":"1h","exchange":"binance","symbol":"BTC/USDT","candles":len(c),
         "start":c[0].timestamp.isoformat(),"end_exclusive":(c[-1].timestamp+timedelta(hours=1)).isoformat(),
         "candle_sha256":candle_fingerprint(c),"missing_candles":0,"source_receipts":[],"test_only":"synthetic"}
    a,b = Store(tmp_path/"a"), Store(tmp_path/"b")
    rule = n.Hypothesis("trend_pullback")
    try:
        full = n.run(a,c,m,rule,"base","development")
        assert full["complete"] and full["result"]["closed_trades"] > 0
        assert D(full["result"]["audit"]["cash_error"]) == 0
        def fail():
            raise RuntimeError("injected batch failure")
        with pytest.raises(RuntimeError,match="injected"):
            n.run(b,c,m,rule,"base","development",fault=fail)
        assert b.db.execute('select count(*) from events').fetchone()[0] == 0
        n.run(b,c,m,rule,"base","development",max_boundaries=125)
        b.close()
        b=Store(tmp_path/"b")
        recovered=n.run(b,c,m,rule,"base","development")
        assert recovered["result"] == full["result"]
        assert b.records(recovered["id"]) == a.records(full["id"])
        assert n.run(b,c,m,rule,"base","development")["deduplicated"]
        assert b.status()["integrity"] == "ok"
        state=json.loads(a.db.execute('select state from experiments').fetchone()[0])
        records=a.records(full["id"])
        corrupt=[dict(r,net_pnl="999") if r["kind"]=="trade" else r for r in records]
        with pytest.raises(AssertionError):
            n.independent_audit(state,corrupt,c,Execution())
    finally:
        a.close()
        b.close()


def test_scope_content_and_cost_changes_refuse_before_experiment(tmp_path):
    c=fixture("trend_pullback")
    for period in ("holdout","forward_paper","2026"):
        with pytest.raises(ValueError,match="unapproved"):
            n.validate_source(c,{},period)
    with pytest.raises(ValueError,match="only frozen"):
        n.costs("free")
    assert n.costs("stress").impact > n.costs("base").impact
    with pytest.raises(ValueError,match="mismatch"):
        n.validate_source(c,{"timeframe":"5m"},"development")


def test_actual_gap_while_position_open_preserves_unresolved_cash(tmp_path, monkeypatch):
    c = list(fixture("trend_pullback"))
    for i in range(121,144):
        if i != 125:
            c.append(replace(c[-1],timestamp=c[0].timestamp+timedelta(hours=i),
                             open=D(114),high=D("114.5"),low=D(113),close=D(114)))
    c = tuple(c)
    monkeypatch.setattr(n,"PERIODS",(("development","2023-04-01","2023-04-07"),))
    m = {"timeframe":"1h","exchange":"binance","symbol":"BTC/USDT","candles":len(c),
         "start":c[0].timestamp.isoformat(),"end_exclusive":(c[-1].timestamp+timedelta(hours=1)).isoformat(),
         "candle_sha256":candle_fingerprint(c),"missing_candles":1,"source_receipts":[],"test_only":"synthetic"}
    store = Store(tmp_path/"gap")
    try:
        answer=n.run(store,c,m,n.Hypothesis("trend_pullback"),"base","development")
        r=answer["result"]
        assert r["filled_trades"] == 1 and r["closed_trades"] == 0
        assert r["unresolved_gap_positions"] == 1 and r["net_return"] is None
        assert r["liquidation_marked_equity"] is None
        assert D(r["audit"]["cash_error"]) == 0
        assert len(store.records(answer["id"],"unresolved")) == 1
        bad=dict(m,candle_sha256="0"*64)
        with pytest.raises(ValueError,match="mismatch"):
            n.run(store,c,bad,n.Hypothesis("trend_pullback"),"base","development")
        assert store.db.execute('select count(*) from experiments').fetchone()[0] == 1
    finally:
        store.close()


def test_gate_requires_full_matrix_and_rejects_economic_failure():
    rows=[]
    for period,_,_ in n.PERIODS:
        for cost in ("base","stress"):
            rows.append({"result":{"period":period,"cost":cost,"closed_trades":110,
                "net_return":"0.1","expectancy_r":"0.2","maximum_close_mark_drawdown":"0.05",
                "ambiguous_adverse_trades":0,"open_positions":0,"pending_entries":0,
                "unresolved_gap_positions":0,"bootstrap_lower_mean_r":0.01}})
    assert n.verdict(rows)["decision"] == "ELIGIBLE FOR FURTHER VALIDATION"
    assert n.verdict(rows[:-1])["decision"] == "INSUFFICIENT EVIDENCE"
    rows[-1]["result"]["net_return"]="-0.01"
    assert n.verdict(rows)["decision"] == "REJECTED"
    assert n.bootstrap_lower(["-1","2","0.5"]*10) == n.bootstrap_lower(["-1","2","0.5"]*10)
