"""Research exit mechanics only; synthetic fixtures are not performance evidence."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
import json
import pytest
from trading_assistant.strategy_lab import alpha, novel
from trading_assistant.strategy_lab.models import Execution, Strategy, candidates
from trading_assistant.strategy_lab.simulation import (
    initial_state,
    step,
    metrics,
    validate_exit_policy,
)
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.data import candle_fingerprint
from test_strategy_lab import candles, manifest
from test_project_alpha import fixture as alpha_fixture
from test_strategy_research_novel import fixture as novel_fixture

ALL22 = (
    *candidates(16),
    *(novel.Hypothesis(n) for n in novel.NAMES),
    *(alpha.Hypothesis(n) for n in alpha.NAMES),
)


def occupied(rule):
    e = Execution()
    c = candles(1)[0]
    c = replace(c, open=D(110), high=D(111), low=D(109), close=D(110))
    s = initial_state(e)
    s["cash"] = str(e.initial_capital - D(110) * (1 + e.fee))
    s["position"] = {
        "quantity": "1",
        "fill": "110",
        "entry_fee": str(D(110) * e.fee),
        "entry_at": c.timestamp.isoformat(),
        "held": rule.max_hold,
        "stop": "100",
        "target": "120",
        "signal_id": "synthetic-entry",
        "decision": c.timestamp.isoformat(),
        "risk_budget": "50",
        "equity_at_decision": "10000",
        "evidence": {"regime": "synthetic-test-only"},
    }
    return s, c, e


@pytest.mark.parametrize(
    "rule", ALL22, ids=lambda x: getattr(x, "name", getattr(x, "family", "unknown"))
)
def test_one_shared_exit_switch_supports_all22_without_changing_their_rules(rule):
    s, c, e = occupied(rule)
    before = asdict(rule)
    default, events = step(s, (c,), 0, rule, e, signal_enabled=False)
    explicit, explicit_events = step(
        s, (c,), 0, rule, e, signal_enabled=False, exit_policy="strategy_default"
    )
    held, other = step(
        s, (c,), 0, rule, e, signal_enabled=False, exit_policy="stop_target_only"
    )
    assert default == explicit and events == explicit_events
    assert default["position"] is None and [
        r["exit_reason"] for r in events if r["kind"] == "trade"
    ] == ["time_exit"]
    assert held["position"] and not any(r["kind"] == "trade" for r in other)
    assert held["cash"] == s["cash"]
    for field in ("quantity", "fill", "entry_fee", "stop", "target", "risk_budget"):
        assert held["position"][field] == s["position"][field]
    assert asdict(rule) == before


@pytest.mark.parametrize("policy", ["strategy_default", "stop_target_only"])
@pytest.mark.parametrize(
    "outcome", ["stop", "target", "both", "gap_stop", "gap_target"]
)
def test_stop_target_and_adverse_gap_rules_are_identical(policy, outcome):
    rule = Strategy()
    s, c, e = occupied(rule)
    if outcome in ("stop", "both"):
        c = replace(c, low=D(99))
    if outcome in ("target", "both"):
        c = replace(c, high=D(121))
    if outcome == "gap_stop":
        c = replace(c, open=D(99), low=D(98))
    if outcome == "gap_target":
        c = replace(c, open=D(121), high=D(122))
    result, records = step(
        s, (c,), 0, rule, e, signal_enabled=False, exit_policy=policy
    )
    t = next(r for r in records if r["kind"] == "trade")
    assert t["exit_reason"] == {
        "both": "stop",
        "gap_target": "gap_target_no_favourable_improvement",
    }.get(outcome, outcome)
    assert t["ambiguous"] == (outcome == "both")
    raw = (
        D(99)
        if outcome == "gap_stop"
        else D(120)
        if outcome in ("target", "gap_target")
        else D(100)
    )
    assert D(t["exit"]) == raw * (1 - e.impact)
    assert D(t["exit_fee"]) == D(t["quantity"]) * D(t["exit"]) * e.fee
    assert D(result["cash"]) == D(s["cash"]) + D(t["exit"]) - D(t["exit_fee"])


def test_open_end_is_real_position_and_conservative_mark_not_fabricated_close():
    rule = Strategy(max_hold=2)
    s, c, e = occupied(rule)
    for i in range(10):
        b = replace(
            c, timestamp=c.timestamp + timedelta(hours=i), close=D(112), high=D(113)
        )
        s, records = step(
            s, (b,), 0, rule, e, signal_enabled=False, exit_policy="stop_target_only"
        )
    assert (
        s["position"]["held"] == 12 and s["filled"] == 0
    )  # seeded test position, not an invented historical fill
    assert s["position"]["target"] == "120" and s["position"]["stop"] == "100"
    assert not any(r["kind"] == "trade" for r in records)
    expected = D(s["cash"]) + D(112) * (1 - e.impact) * (1 - e.fee)
    assert D(s["equity"]) == expected
    r = metrics(s, records, e)
    assert r["open_positions"] == 1 and r["closed_trades"] == 0
    assert D(r["liquidation_marked_equity"]) == expected


def test_gap_remains_unresolved_without_invented_exit():
    rule = Strategy()
    s, c, e = occupied(rule)
    s, _ = step(
        s, (c,), 0, rule, e, signal_enabled=False, exit_policy="stop_target_only"
    )
    gap = replace(c, timestamp=c.timestamp + timedelta(hours=2), high=D(999))
    s, records = step(
        s, (gap,), 0, rule, e, signal_enabled=False, exit_policy="stop_target_only"
    )
    assert s["has_unresolved_gap"] and any(r["kind"] == "unresolved" for r in records)
    assert not any(r["kind"] == "trade" for r in records)
    assert (
        next(r for r in records if r["kind"] == "equity_mark")["liquidation_equity"]
        is None
    )


@pytest.mark.parametrize(
    "bad", [None, False, "stop_only", "time_exit", "STOP_TARGET_ONLY", [], 42]
)
def test_bad_policy_refuses_before_state_or_experiment_changes(tmp_path, bad):
    with pytest.raises(ValueError, match="exit_policy"):
        validate_exit_policy(bad)
    c = candles(100)
    store = Store(tmp_path / "lab")
    try:
        with pytest.raises(ValueError, match="exit_policy"):
            run_experiment(
                store, c, manifest(c), Strategy(), Execution(), exit_policy=bad
            )
        assert store.db.execute("select count(*) from experiments").fetchone()[0] == 0
    finally:
        store.close()


def stable(answer):
    return {
        k: v for k, v in answer["result"].items() if k != "runtime_this_attempt_seconds"
    }


def test_original_runner_policy_identity_resume_and_completed_results_immutable(
    tmp_path,
):
    c = candles(250)
    h = Strategy(max_hold=2)
    e = Execution()
    s, t = Store(tmp_path / "source"), Store(tmp_path / "resume")
    try:
        before = run_experiment(s, c, manifest(c), h, e)
        default = run_experiment(
            s, c, manifest(c), h, e, exit_policy="strategy_default"
        )
        assert default["id"] == before["id"] and default["deduplicated"]
        saved = dict(
            s.db.execute(
                "select * from experiments where id=?", (before["id"],)
            ).fetchone()
        )
        new = run_experiment(s, c, manifest(c), h, e, exit_policy="stop_target_only")
        assert new["id"] != before["id"]
        assert not any(
            r["exit_reason"] == "time_exit" for r in s.records(new["id"], "trade")
        )
        definitions = [
            json.loads(r[0]) for r in s.db.execute("select definition from experiments")
        ]
        assert {r["exit_policy"] for r in definitions} == {
            "strategy_default",
            "stop_target_only",
        }
        assert (
            definitions[0]["execution"]
            == definitions[1]["execution"]
            == json.loads(
                __import__(
                    "trading_assistant.strategy_lab.models", fromlist=["canonical"]
                ).canonical(e)
            )
        )
        assert (
            dict(
                s.db.execute(
                    "select * from experiments where id=?", (before["id"],)
                ).fetchone()
            )
            == saved
        )
        run_experiment(
            t, c, manifest(c), h, e, exit_policy="stop_target_only", max_boundaries=80
        )
        t.close()
        t = Store(tmp_path / "resume")
        resumed = run_experiment(
            t, c, manifest(c), h, e, exit_policy="stop_target_only"
        )
        assert stable(new) == stable(resumed) and s.records(new["id"]) == t.records(
            resumed["id"]
        )
        assert t.db.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        s.close()
        t.close()


@pytest.mark.parametrize("kind", ["novel", "alpha"])
def test_hypothesis_runners_forward_policy_record_identity_and_audit_open_position(
    tmp_path, monkeypatch, kind
):
    mod = novel if kind == "novel" else alpha
    c = (
        novel_fixture("trend_pullback")
        if kind == "novel"
        else alpha_fixture(alpha.NAMES[1])
    )
    rule = (
        novel.Hypothesis("trend_pullback")
        if kind == "novel"
        else alpha.Hypothesis(alpha.NAMES[1])
    )
    ref = c[-1].close
    # Extend more than the original holding limit, below actual target and above stop.
    for i in range(rule.max_hold + 2):
        c = c + (
            replace(
                c[-1],
                timestamp=c[-1].timestamp + timedelta(hours=1),
                open=ref,
                close=ref,
                high=ref + D(".01"),
                low=ref - D(".01"),
            ),
        )
    period = (
        "development",
        c[0].timestamp.isoformat(),
        (c[-1].timestamp + timedelta(hours=1)).isoformat(),
    )
    monkeypatch.setattr(novel, "PERIODS", (period,))
    m = {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "candles": len(c),
        "start": period[1],
        "end_exclusive": period[2],
        "candle_sha256": candle_fingerprint(c),
        "missing_candles": 0,
        "source_receipts": [],
        "synthetic_test_only": True,
    }
    store = Store(tmp_path / "lab")
    try:
        old = mod.run(store, c, m, rule, "base", "development")
        new = mod.run(
            store, c, m, rule, "base", "development", exit_policy="stop_target_only"
        )
        assert old["id"] != new["id"]
        assert (
            old["result"]["closed_trades"] >= 1 and new["result"]["open_positions"] == 1
        )
        assert (
            new["result"]["closed_trades"] == 0
            and D(new["result"]["audit"]["cash_error"]) == 0
        )
        assert D(new["result"]["audit"]["maximum_mark_error"]) == 0
        state = json.loads(
            store.db.execute(
                "select state from experiments where id=?", (new["id"],)
            ).fetchone()[0]
        )
        bad = deepcopy(store.records(old["id"]))
        assert any(
            r["kind"] == "trade" and r["exit_reason"] == "time_exit" for r in bad
        )
        with pytest.raises(AssertionError):
            novel.independent_audit(
                state, bad, c, novel.costs("base"), exit_policy="stop_target_only"
            )
    finally:
        store.close()
