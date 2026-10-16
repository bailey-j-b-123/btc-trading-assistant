"""Synthetic stop filters and existing real decision functions; no profitability claims."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
import json
import pytest
from trading_assistant.strategy_lab import novel
from trading_assistant.strategy_lab.stop_quality import StopQuality, quality_definition
from trading_assistant.strategy_lab.models import Execution, Strategy
from trading_assistant.strategy_lab.simulation import initial_state, step
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from test_strategy_lab import candles, manifest


def policy(**kwargs):
    return StopQuality(
        **dict(
            minimum_atr_fraction=D(".25"),
            minimum_roundtrip_cost_multiple=D(1),
            minimum_observed_range_fraction=D(".25"),
            minimum_observed_gap_multiple=D(1),
            volatility_lookback=14,
            **kwargs,
        )
    )


def evaluate(p=None, signal=None, cs=None):
    cs = cs or candles(30)
    s = signal or dict(reference="100", stop="90", evidence={"atr": "2"})
    return (p or policy()).assess(
        s, cs, known_at=cs[-1].timestamp + timedelta(hours=1), execution=Execution()
    )


def test_measurements_and_costs_exact_original_stop_never_moved():
    s = dict(reference="100", stop="90", evidence={"atr": "2"})
    before = deepcopy(s)
    r = evaluate(signal=s)
    assert r["reasons"] == [] and s == before and r["original_stop"] == "90"
    assert D(r["exact_flat_roundtrip_cost_per_btc"]) == D(".28")
    assert r["alternative_stop_selected"] is False
    assert set(r["required_distances"]) == {
        "stop_quality_atr_distance",
        "stop_quality_roundtrip_cost_distance",
        "stop_quality_observed_range_distance",
        "stop_quality_observed_gap_distance",
    }


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("minimum_atr_fraction", "100", "stop_quality_atr_distance"),
        (
            "minimum_roundtrip_cost_multiple",
            "100",
            "stop_quality_roundtrip_cost_distance",
        ),
        (
            "minimum_observed_range_fraction",
            "100",
            "stop_quality_observed_range_distance",
        ),
        ("minimum_observed_gap_multiple", "100", "stop_quality_observed_gap_distance"),
    ],
)
def test_explicit_threshold_each_rejects_without_widening(field, value, reason):
    args = quality_definition(policy())
    args.pop("version")
    args = {k: D(v) if k != "volatility_lookback" else v for k, v in args.items()}
    args[field] = D(value)
    cs = candles(30)
    if field == "minimum_observed_gap_multiple":
        cs = cs[:-1] + (
            replace(cs[-1], open=cs[-1].open + D(1), high=cs[-1].high + D(1)),
        )
    r = evaluate(StopQuality(**args), cs=cs)
    assert reason in r["reasons"] and r["original_stop"] == "90"


@pytest.mark.parametrize("atr", [None, "NaN", "Infinity", "broken", "0", "-1"])
def test_missing_atr_refuses_honestly(atr):
    assert evaluate(signal=dict(reference="100", stop="90", evidence={"atr": atr}))[
        "reasons"
    ] == ["stop_quality_missing_original_atr"]


def test_future_incomplete_gapped_and_cross_timeframe_data_refused():
    cs = candles(30)
    s = dict(
        reference="100",
        stop="90",
        evidence={
            "atr": "2",
            "atr_known_at": (cs[-1].timestamp + timedelta(hours=2)).isoformat(),
        },
    )
    assert evaluate(signal=s)["reasons"] == ["stop_quality_future_or_invalid_atr_time"]
    assert evaluate(cs=cs[:10])["reasons"] == [
        "stop_quality_insufficient_observed_volatility"
    ]
    assert evaluate(cs=cs[:-2] + cs[-1:])["reasons"] == [
        "stop_quality_noncausal_or_incomplete_window"
    ]
    assert evaluate(cs=cs[:-1] + (replace(cs[-1], timeframe="4h"),))["reasons"] == [
        "stop_quality_noncausal_or_incomplete_window"
    ]
    future = replace(cs[-1], timestamp=cs[-1].timestamp + timedelta(hours=1))
    r = policy().assess(
        dict(reference="100", stop="90", evidence={"atr": "2"}),
        cs + (future,),
        known_at=cs[-1].timestamp + timedelta(hours=1),
        execution=Execution(),
    )
    assert r["reasons"] == ["stop_quality_noncausal_or_incomplete_window"]


def test_exact_threshold_equality_passes_and_stress_cost_screen_is_independent():
    p = StopQuality(D(0), D(1), D(0), D(0), 14)
    s = dict(reference="100", stop="99.72", evidence={"atr": "2"})
    assert not evaluate(p, signal=s)["reasons"]
    cs = candles(30)
    r = p.assess(
        s,
        cs,
        known_at=cs[-1].timestamp + timedelta(hours=1),
        execution=novel.costs("stress"),
    )
    assert r["reasons"] == ["stop_quality_roundtrip_cost_distance"]
    assert D(r["exact_flat_roundtrip_cost_per_btc"]) == D(".62")


def test_shared_step_accepts_identical_size_and_rejects_unsuitable_without_any_fill():
    cs = candles(50)
    e = Execution()
    rule = Strategy()

    def offer(window, rule, known_at):
        return dict(
            known_at=known_at.isoformat(),
            candle_open=window[-1].timestamp.isoformat(),
            reference="100",
            stop="90",
            target="120",
            evidence={"atr": "2"},
        )

    old, ev = step(initial_state(e), cs, 49, rule, e, decision_function=offer)
    same, implicit = step(
        initial_state(e), cs, 49, rule, e, decision_function=offer, stop_quality=None
    )
    assert old == same and ev == implicit
    accepted, ev = step(
        initial_state(e),
        cs,
        49,
        rule,
        e,
        decision_function=offer,
        stop_quality=policy(),
    )
    assert accepted["pending"] == old["pending"] and accepted["cash"] == old["cash"]
    bad = StopQuality(D(100), D(1), D(".25"), D(1), 14)
    rejected, ev = step(
        initial_state(e), cs, 49, rule, e, decision_function=offer, stop_quality=bad
    )
    assert (
        rejected["pending"] is None
        and rejected["filled"] == 0
        and rejected["cash"] == "10000"
    )
    assert next(r for r in ev if r["kind"] == "rejection")["quality_reasons"] == [
        "stop_quality_atr_distance"
    ]


def test_actual_strategy_resume_identity_and_old_rows_immutable(tmp_path):
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    cs = candles(250)
    rule = Strategy()
    e = Execution()
    p = policy()
    try:
        base = run_experiment(a, cs, manifest(cs), rule, e)
        saved = dict(
            a.db.execute(
                "select * from experiments where id=?", (base["id"],)
            ).fetchone()
        )
        new = run_experiment(a, cs, manifest(cs), rule, e, stop_quality=p)
        assert new["id"] != base["id"]
        assert (
            dict(
                a.db.execute(
                    "select * from experiments where id=?", (base["id"],)
                ).fetchone()
            )
            == saved
        )

        def fail():
            raise RuntimeError("injected stop quality interruption")

        with pytest.raises(RuntimeError, match="injected"):
            run_experiment(
                b,
                cs,
                manifest(cs),
                rule,
                e,
                stop_quality=p,
                max_boundaries=80,
                fault=fail,
            )
        assert b.db.execute("select count(*) from events").fetchone()[0] == 0
        run_experiment(b, cs, manifest(cs), rule, e, stop_quality=p, max_boundaries=80)
        b.close()
        b = Store(tmp_path / "b")
        resumed = run_experiment(b, cs, manifest(cs), rule, e, stop_quality=p)
        assert {
            k: v
            for k, v in new["result"].items()
            if k != "runtime_this_attempt_seconds"
        } == {
            k: v
            for k, v in resumed["result"].items()
            if k != "runtime_this_attempt_seconds"
        }
        assert a.records(new["id"]) == b.records(resumed["id"])
        definition = json.loads(
            a.db.execute(
                "select definition from experiments where id=?", (new["id"],)
            ).fetchone()[0]
        )
        assert definition["stop_quality"] == quality_definition(p)
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize(
    "values",
    [
        (D("NaN"), D(1), D(1), D(1), 14),
        (D(-1), D(1), D(1), D(1), 14),
        (D(1), D(1), D(1), D(1), True),
        (D(1), D(1), D(1), D(1), 1),
    ],
)
def test_invalid_explicit_settings_refused(values):
    with pytest.raises(ValueError):
        StopQuality(*values)


def test_five_minute_adapter_has_actual_volatility_window_despite_one_bar_decision_warmup():
    from trading_assistant.strategy_lab.brain import BrainPolicy

    cs = tuple(
        replace(
            c,
            timeframe="5m",
            timestamp=candles(1)[0].timestamp + timedelta(minutes=5 * i),
        )
        for i, c in enumerate(candles(30))
    )

    def offer(window, rule, known_at):
        return dict(
            known_at=known_at.isoformat(),
            candle_open=window[-1].timestamp.isoformat(),
            reference="100",
            stop="90",
            target="120",
            evidence={
                "atr": "2",
                "atr_timeframe": "1h",
                "atr_known_at": cs[0].timestamp.isoformat(),
            },
        )

    s, ev = step(
        initial_state(Execution()),
        cs,
        29,
        BrainPolicy("A1"),
        Execution(),
        decision_function=offer,
        stop_quality=policy(),
    )
    assert s["pending"]
    facts = next(e["assessment"] for e in ev if e["kind"] == "stop_quality")
    assert (
        not facts["reasons"]
        and facts["timeframe"] == "5m"
        and facts["original_atr_timeframe"] == "1h"
    )
    assert facts["source_first_open"] == cs[-15].timestamp.isoformat()
