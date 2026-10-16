"""Synthetic execution/causality controls, never historical profitability evidence."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
import json
import pytest

from trading_assistant.strategy_lab import alpha, novel
from trading_assistant.strategy_lab.entry_policy import PullbackEntry, entry_definition
from trading_assistant.strategy_lab.models import Execution, Strategy, candidates
from trading_assistant.strategy_lab.simulation import initial_state, step
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from test_strategy_lab import candles, manifest

ALL22 = (
    *candidates(16),
    *(novel.Hypothesis(n) for n in novel.NAMES),
    *(alpha.Hypothesis(n) for n in alpha.NAMES),
)


def bar(i, o="100", low="99", high="101", close="100"):
    c = candles(i + 1)[i]
    return replace(c, open=D(o), low=D(low), high=D(high), close=D(close))


def offer(cs, rule, *, known_at):
    return {
        "known_at": known_at.isoformat(),
        "candle_open": cs[-1].timestamp.isoformat(),
        "reference": "100",
        "stop": "90",
        "target": "120",
        "source_plan_id": "test-plan",
        "evidence": {"regime": "synthetic-only"},
    }


def queue(rule=Strategy(), policy=PullbackEntry()):
    e = Execution()
    state, events = step(
        initial_state(e),
        (bar(0),),
        0,
        rule,
        e,
        decision_function=offer,
        entry_policy=policy,
    )
    assert not state["filled"]
    return state, events


@pytest.mark.parametrize(
    "rule", ALL22, ids=lambda r: getattr(r, "name", getattr(r, "family", "x"))
)
def test_shared_switch_preserves_all22_rule_definitions_and_default_bytes(rule):
    c, e = (bar(0), bar(1)), Execution()
    original = asdict(rule)
    implicit, a = step(initial_state(e), c, 0, rule, e, decision_function=offer)
    explicit, b = step(
        initial_state(e), c, 0, rule, e, decision_function=offer, entry_policy=None
    )
    assert implicit == explicit and a == b
    conditional, _ = queue(rule)
    for key in (
        "quantity",
        "stop",
        "target",
        "risk_budget",
        "entry_ceiling",
        "equity_at_decision",
    ):
        assert conditional["pending"][key] == implicit["pending"][key]
    assert asdict(rule) == original


def test_no_signal_no_invented_opportunity_or_fill():
    s, ev = step(
        initial_state(Execution()),
        (bar(0),),
        0,
        Strategy(),
        Execution(),
        entry_policy=PullbackEntry(),
        decision_function=lambda *a, **kw: None,
    )
    assert s["opportunities"] == s["filled"] == 0
    assert s["pending"] is None and all(r["kind"] == "equity_mark" for r in ev)


def test_touch_is_not_fill_then_actual_open_fills_with_same_size_levels_and_costs():
    s, _ = queue()
    q = s["pending"]["quantity"]
    s, ev = step(
        s,
        (bar(1, low="96"),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    assert s["filled"] == 0 and s["pending"]["waited_bars"] == 1
    assert any(r["kind"] == "entry_wait" for r in ev)
    s, ev = step(
        s,
        (bar(2, o="97", low="96", high="98", close="97"),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    p = s["position"]
    assert p["quantity"] == q and p["stop"] == "90" and p["target"] == "120"
    assert D(p["fill"]) == D("97") * (1 + Execution().impact) <= D("97.50")
    assert D(p["entry_fee"]) == D(q) * D(p["fill"]) * Execution().fee
    assert p["waited_bars"] == 2 and s["seen_source_plans"] == ["test-plan"]
    # Re-offering a filled source plan cannot duplicate the entry.
    s, _ = step(
        s,
        (bar(3, o="97", low="96", high="98", close="97"),),
        0,
        Strategy(),
        Execution(),
        decision_function=offer,
        entry_policy=PullbackEntry(),
    )
    assert s["filled"] == 1 and s["opportunities"] == 1


@pytest.mark.parametrize(
    "low,high,reason",
    [
        ("89", "101", "pullback_structural_invalidation"),
        ("99", "121", "pullback_target_before_entry"),
        ("89", "121", "pullback_structural_invalidation"),
    ],
)
def test_unfilled_invalidation_and_target_cancel_without_fabricated_outcome(
    low, high, reason
):
    s, _ = queue()
    s, ev = step(
        s,
        (bar(1, low=low, high=high),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    assert s["pending"] is None and s["position"] is None and s["cash"] == "10000"
    assert s["rejections"] == {reason: 1} and s["filled"] == 0
    assert not any(r["kind"] == "trade" for r in ev)


def test_timeout_exactly_six_eligible_opens_and_pending_reserves_slot():
    s, _ = queue()

    def another(cs, rule, **kw):
        return {**offer(cs, rule, **kw), "source_plan_id": "another"}

    for i in range(1, 7):
        s, ev = step(
            s,
            (bar(i),),
            0,
            Strategy(),
            Execution(),
            decision_function=another if i == 1 else lambda *a, **kw: None,
            entry_policy=PullbackEntry(),
        )
        if i < 6:
            assert s["pending"]["waited_bars"] == i
    assert s["pending"] is None and s["rejections"] == {
        "occupied_slot": 1,
        "pullback_timeout": 1,
    }
    assert s["filled"] == 0 and s["cash"] == "10000"


def test_last_permitted_open_can_fill_and_end_of_data_keeps_open_mark():
    s, _ = queue()
    for i in range(1, 6):
        s, _ = step(
            s,
            (bar(i),),
            0,
            Strategy(),
            Execution(),
            signal_enabled=False,
            entry_policy=PullbackEntry(),
        )
    s, ev = step(
        s,
        (bar(6, o="97", low="96", high="98", close="97"),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    assert s["position"] and s["position"]["waited_bars"] == 6
    assert not any(r["kind"] == "trade" for r in ev)
    q, fill = D(s["position"]["quantity"]), D(s["position"]["fill"])
    assert D(s["equity"]) == D(s["cash"]) + q * D(97) * (1 - Execution().impact) * (
        1 - Execution().fee
    )
    assert D(s["cash"]) == D(10000) - q * fill * (1 + Execution().fee)


def test_gap_cancels_pending_and_recorded_at_prevents_backdated_fill():
    s, _ = queue()
    s, ev = step(
        s,
        (bar(2, o="97", low="96"),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    assert s["filled"] == 0 and s["rejections"] == {"data_gap_before_fill": 1}
    now = bar(0).timestamp + timedelta(hours=3)
    s, _ = step(
        initial_state(Execution()),
        (bar(0),),
        0,
        Strategy(),
        Execution(),
        recorded_at=now,
        decision_function=offer,
        entry_policy=PullbackEntry(),
    )
    for i in (1, 2):
        s, _ = step(
            s,
            (bar(i, o="97", low="96"),),
            0,
            Strategy(),
            Execution(),
            signal_enabled=False,
            entry_policy=PullbackEntry(),
        )
        assert s["pending"]["waited_bars"] == 0 and s["filled"] == 0


def test_no_future_bar_extreme_affects_current_pending_or_decision():
    c = (bar(0), bar(1), bar(2, o="97", low="96"))
    poisoned = c[:2] + (replace(c[2], high=D("1000000"), low=D("0.01")),)
    for index in (0, 1):
        a, ae = step(
            initial_state(Execution()),
            c,
            index,
            Strategy(),
            Execution(),
            decision_function=offer,
            entry_policy=PullbackEntry(),
        )
        b, be = step(
            initial_state(Execution()),
            poisoned,
            index,
            Strategy(),
            Execution(),
            decision_function=offer,
            entry_policy=PullbackEntry(),
        )
        assert a == b and ae == be


@pytest.mark.parametrize("outcome", ["stop", "target", "both", "gap_stop"])
def test_original_exit_ordering_and_risk_budget_still_apply(outcome):
    s, _ = queue()
    c = bar(
        1,
        o="97",
        low="89" if outcome in ("stop", "both") else "96",
        high="121" if outcome in ("target", "both") else "98",
        close="97",
    )
    if outcome == "gap_stop":
        c = bar(1, o="89", low="88", high="98", close="97")
    s, ev = step(
        s,
        (c,),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    trades = [r for r in ev if r["kind"] == "trade"]
    if outcome == "gap_stop":
        assert s["filled"] == 0 and not trades
    else:
        t = trades[0]
        assert t["exit_reason"] == ("target" if outcome == "target" else "stop")
        assert t["ambiguous"] == (outcome == "both")
        assert D(t["net_pnl"]) >= -D(t["risk_budget"])
        assert D(t["net_pnl"]) == D(t["quantity"]) * (D(t["exit"]) - D(t["entry"])) - D(
            t["entry_fee"]
        ) - D(t["exit_fee"])


def test_identity_immutable_baseline_and_real_strategy_restart_rollback(tmp_path):
    c = candles(250)
    rule, e, policy = Strategy(max_hold=2), Execution(), PullbackEntry()
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    try:
        default = run_experiment(a, c, manifest(c), rule, e)
        row = dict(
            a.db.execute(
                "select * from experiments where id=?", (default["id"],)
            ).fetchone()
        )
        assert (
            run_experiment(a, c, manifest(c), rule, e, entry_policy=None)["id"]
            == default["id"]
        )
        full = run_experiment(a, c, manifest(c), rule, e, entry_policy=policy)
        assert full["id"] != default["id"]
        assert (
            dict(
                a.db.execute(
                    "select * from experiments where id=?", (default["id"],)
                ).fetchone()
            )
            == row
        )

        def fail():
            raise RuntimeError("transaction interruption")

        with pytest.raises(RuntimeError, match="interruption"):
            run_experiment(
                b,
                c,
                manifest(c),
                rule,
                e,
                entry_policy=policy,
                max_boundaries=80,
                fault=fail,
            )
        assert b.db.execute("select count(*) from events").fetchone()[0] == 0
        run_experiment(
            b, c, manifest(c), rule, e, entry_policy=policy, max_boundaries=80
        )
        b.close()
        b = Store(tmp_path / "b")
        resumed = run_experiment(b, c, manifest(c), rule, e, entry_policy=policy)

        def stable(x):
            return {
                k: v
                for k, v in x["result"].items()
                if k != "runtime_this_attempt_seconds"
            }

        assert stable(full) == stable(resumed)
        assert a.records(full["id"]) == b.records(resumed["id"])
        assert run_experiment(b, c, manifest(c), rule, e, entry_policy=policy)[
            "deduplicated"
        ]
        spec = json.loads(
            a.db.execute(
                "select definition from experiments where id=?", (full["id"],)
            ).fetchone()[0]
        )
        assert spec["entry_policy"] == entry_definition(policy)
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_wait_bars": 0},
        {"max_wait_bars": True},
        {"retracement_fraction": D("NaN")},
        {"retracement_fraction": D("1")},
        {"version": "future"},
    ],
)
def test_bad_config_refuses(kwargs):
    with pytest.raises(ValueError):
        PullbackEntry(**kwargs)


def test_policy_change_cannot_reinterpret_pending_checkpoint():
    s, _ = queue()
    before = deepcopy(s)
    with pytest.raises(ValueError, match="checkpoint"):
        step(s, (bar(1),), 0, Strategy(), Execution(), signal_enabled=False)
    assert s == before


def test_adverse_costs_must_also_fit_cap_not_just_raw_open():
    s, _ = queue()
    s, _ = step(
        s,
        (bar(1, o="97.49", low="96", high="98", close="97"),),
        0,
        Strategy(),
        Execution(),
        signal_enabled=False,
        entry_policy=PullbackEntry(),
    )
    assert s["filled"] == 0 and s["pending"]
    assert D("97.49") < D(s["pending"]["entry_limit"])
    assert D("97.49") * (1 + Execution().impact) > D(s["pending"]["entry_limit"])


@pytest.mark.parametrize("kind", ["novel", "alpha"])
def test_real_hypothesis_runner_fills_delayed_intent_and_audits(
    tmp_path, monkeypatch, kind
):
    from test_strategy_research_novel import fixture as novel_fixture
    from test_project_alpha import fixture as alpha_fixture
    from trading_assistant.strategy_lab.data import candle_fingerprint

    mod = novel if kind == "novel" else alpha
    rule = (
        novel.Hypothesis("breakout_retest")
        if kind == "novel"
        else alpha.Hypothesis(alpha.NAMES[1])
    )
    cs = novel_fixture(rule.name) if kind == "novel" else alpha_fixture(rule.name)
    known = cs[-1].timestamp + timedelta(hours=1)
    signal = mod.decide(cs, rule, known_at=known)
    assert signal
    policy = PullbackEntry()
    price = (policy.limit(signal) + D(signal["stop"])) / 2
    # First open stays above cap; second actually retraces; genuine decision unchanged.
    for open_price in (D(signal["reference"]), price):
        cs += (
            replace(
                cs[-1],
                timestamp=cs[-1].timestamp + timedelta(hours=1),
                open=open_price,
                close=open_price,
                low=open_price - D(".0001"),
                high=open_price + D(".0001"),
            ),
        )
    period = (
        "development",
        cs[0].timestamp.isoformat(),
        (cs[-1].timestamp + timedelta(hours=1)).isoformat(),
    )
    monkeypatch.setattr(novel, "PERIODS", (period,))
    m = dict(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        candles=len(cs),
        start=period[1],
        end_exclusive=period[2],
        candle_sha256=candle_fingerprint(cs),
        missing_candles=0,
        source_receipts=[],
        synthetic_test_only=True,
    )
    store = Store(tmp_path / "lab")
    try:
        r = mod.run(store, cs, m, rule, "base", "development", entry_policy=policy)
        assert r["result"]["filled_trades"] >= 1
        assert D(r["result"]["audit"]["cash_error"]) == 0
        assert D(r["result"]["audit"]["maximum_mark_error"]) == 0
        row = json.loads(
            store.db.execute(
                "select definition from experiments where id=?", (r["id"],)
            ).fetchone()[0]
        )
        assert row["entry_policy"] == entry_definition(policy)
    finally:
        store.close()
