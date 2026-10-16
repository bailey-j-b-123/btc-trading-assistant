"""Offline synthetic learning controls; synthetic outcomes are NOT evidence of edge."""

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
import json
import pytest
from test_strategy_lab import candles, manifest
from trading_assistant.strategy_lab import discovery as d
from trading_assistant.strategy_lab.models import (
    canonical,
    Execution,
    Strategy,
    research_code_fingerprint,
)
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.novel import independent_audit
from trading_assistant.strategy_lab.store import Store


@pytest.fixture(scope="module")
def training():
    cs = candles(int((d.TRAIN_END - d.TRAIN_START) / d.HOUR))
    return tuple(
        replace(c, timestamp=d.TRAIN_START + i * d.HOUR) for i, c in enumerate(cs)
    )


@pytest.fixture(scope="module")
def fit(training):
    return d.discover(training)


def test_same_training_exact_deterministic_bounded_unique_rules(training, fit):
    assert canonical(fit) == canonical(d.discover(training))
    assert 1 <= len(fit["hypotheses"]) <= 8 < 24 and fit["split_trials"] <= 168
    ids = [h["id"] for h in fit["hypotheses"] if "id" in h]
    assert len(ids) == len(set(ids))
    assert fit["last_label_available"] <= d.TRAIN_END
    assert fit["last_decision"] + timedelta(hours=24) <= d.TRAIN_END
    assert fit["valid_training_rows"] == len(training) - 30 - 24
    for h in fit["hypotheses"]:
        if "id" in h:
            r = d.load_rule(json.loads(canonical(h["rule"])))
            assert r.fingerprint == h["id"] and r.training_samples >= 120
            assert r.text() == h["text"]
            assert all(c.feature in d.FEATURES for c in r.conditions)


def test_fit_refuses_evaluation_prices_before_any_feature_learning(
    training, monkeypatch
):
    def forbidden(*a, **kw):
        raise AssertionError("must reject scope before learning")

    monkeypatch.setattr(d, "features", forbidden)
    extra = replace(
        training[-1], timestamp=d.TRAIN_END, close=D("1000000"), high=D("1000000")
    )
    with pytest.raises(ValueError, match="discovery prefix"):
        d.discover(training + (extra,))
    with pytest.raises(ValueError, match="discovery prefix"):
        d.discover(training[:-1])
    with pytest.raises(ValueError, match="discovery prefix"):
        d.discover(training[1:])
    with pytest.raises(ValueError, match="discovery prefix"):
        d.discover(training[:50] + training[51:])


def test_pure_fit_has_no_data_store_network_or_file_access(training, monkeypatch, fit):
    import builtins
    import socket

    def forbidden(*a, **kw):
        raise AssertionError("learning cannot read external data")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert canonical(d.discover(training)) == canonical(fit)


def rule(**kw):
    args = dict(
        conditions=(d.Condition("signed_body", "le", D(1)),),
        training_fingerprint="0" * 64,
        fit_source_sha256=research_code_fingerprint(),
        training_samples=120,
        training_mean_bps=D(1),
    )
    args.update(kw)
    return d.Rule(**args)


def evaluation(n=250):
    return tuple(
        replace(c, timestamp=d.TRAIN_END + i * d.HOUR) for i, c in enumerate(candles(n))
    )


def test_raw_feature_equations_use_prior_volume_ranges_and_current_closed_candle_only():
    cs = evaluation(31)
    at = cs[-1].timestamp + d.HOUR
    f = d.features(cs, known_at=at)
    c = cs[-1]
    previous = cs[-25:-1]
    assert (
        f["signed_body"] == 0
        and f["upper_wick"] == f["lower_wick"] == D(".5")
        and f["close_location"] == D(".5")
    )
    assert f["relative_volume"] == 1 and f["relative_range"] == 1
    assert f["opening_gap_in_prior_range"] == (c.open - cs[-2].close) / 2
    assert f["prior_range_position"] == (c.close - min(c.low for c in previous)) / (
        max(c.high for c in previous) - min(c.low for c in previous)
    )
    future = replace(
        cs[-1],
        timestamp=at,
        open=D(1),
        low=D(1),
        high=D("1000000"),
        close=D("1000000"),
        volume=D("1000000"),
    )
    assert f == d.features(cs + (future,), known_at=at)
    assert d.decide(cs, rule(), known_at=at) == d.decide(
        cs + (future,), rule(), known_at=at
    )
    assert d.features(cs, known_at=at - timedelta(microseconds=1)) is None


def test_future_prices_missing_volume_and_gap_do_not_create_signal():
    cs = evaluation(31)
    at = cs[-1].timestamp + d.HOUR
    assert d.features(cs[:-2] + cs[-1:], known_at=at) is None
    assert d.features(tuple(replace(c, volume=D(0)) for c in cs), known_at=at) is None
    assert (
        d.features(tuple(replace(c, timeframe="4h") for c in cs), known_at=at) is None
    )
    assert (
        d.decide(
            cs, rule(conditions=(d.Condition("signed_body", "gt", D(1)),)), known_at=at
        )
        is None
    )


def test_cannot_execute_backdated_or_sealed_holdout_rule(training):
    with pytest.raises(ValueError, match="retrospectively"):
        d.decide(training[-31:], rule(), known_at=d.TRAIN_START + timedelta(days=5))
    with pytest.raises(ValueError, match="sealed2025"):
        d.decide((), rule(), known_at=d.TRAIN_END.replace(year=2025))
    with pytest.raises(ValueError, match="source mismatch"):
        d.decide(
            evaluation(31),
            rule(fit_source_sha256="0" * 64),
            known_at=d.TRAIN_END + timedelta(hours=31),
        )


def test_definition_change_has_distinct_reproducible_fingerprint():
    a = rule()
    assert d.load_rule(json.loads(canonical(asdict(a)))).fingerprint == a.fingerprint
    assert (
        a.fingerprint
        != rule(conditions=(d.Condition("signed_body", "gt", D(0)),)).fingerprint
    )
    assert a.fingerprint != rule(training_fingerprint="1" * 64).fingerprint


def test_existing_cash_engine_fills_fees_risk_open_marks_and_recovery_reconcile(
    tmp_path,
):
    cs = evaluation()
    r = rule()
    e = Execution()
    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    kwargs = dict(stage="walk_forward", decision_adapter="discovery-v1")
    try:
        full = run_experiment(a, cs, manifest(cs), r, e, **kwargs)
        assert full["complete"] and full["result"]["filled_trades"] > 0
        events = a.records(full["id"])
        state = json.loads(
            a.db.execute(
                "select state from experiments where id=?", (full["id"],)
            ).fetchone()[0]
        )
        audit = independent_audit(state, events, cs, e)
        # Synthetic prices have17significant digits. The shared engine intentionally
        # rounds monetary operations at28digits, whereas this audit uses60.
        # Preserve its existing sub-attounit contract, and prove a real monetary
        # error is rejected rather than demanding impossible bit-exact ratios.
        assert D(audit["cash_error"]) < D("1e-18")
        assert D(audit["maximum_mark_error"]) < D("1e-18")
        from copy import deepcopy

        bad = deepcopy(events)
        changed = next(x for x in bad if x["kind"] == "trade")
        changed["net_pnl"] = str(D(changed["net_pnl"]) + D("0.00000001"))
        with pytest.raises(AssertionError):
            independent_audit(state, bad, cs, e)

        def fail():
            raise RuntimeError("injected transaction interruption")

        with pytest.raises(RuntimeError, match="injected"):
            run_experiment(
                b, cs, manifest(cs), r, e, max_boundaries=80, fault=fail, **kwargs
            )
        assert b.db.execute("select count(*) from events").fetchone()[0] == 0
        run_experiment(b, cs, manifest(cs), r, e, max_boundaries=80, **kwargs)
        b.close()
        b = Store(tmp_path / "b")
        recovered = run_experiment(b, cs, manifest(cs), r, e, **kwargs)
        assert recovered["id"] == full["id"]
        assert {
            k: v
            for k, v in recovered["result"].items()
            if k != "runtime_this_attempt_seconds"
        } == {
            k: v
            for k, v in full["result"].items()
            if k != "runtime_this_attempt_seconds"
        }
        assert b.records(full["id"]) == events
        saved = dict(
            b.db.execute(
                "select * from experiments where id=?", (full["id"],)
            ).fetchone()
        )
        assert run_experiment(b, cs, manifest(cs), r, e, **kwargs)["deduplicated"]
        assert (
            dict(
                b.db.execute(
                    "select * from experiments where id=?", (full["id"],)
                ).fetchone()
            )
            == saved
        )
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("adapter", [False, "other", "discovery-v1"])
def test_runner_refuses_unknown_adapter_or_wrong_rule_before_records(tmp_path, adapter):
    s = Store(tmp_path / "lab")
    cs = evaluation(50)
    try:
        with pytest.raises(ValueError):
            run_experiment(
                s, cs, manifest(cs), Strategy(), Execution(), decision_adapter=adapter
            )
        assert s.db.execute("select count(*) from experiments").fetchone()[0] == 0
    finally:
        s.close()


def test_holdout_admission_refuses_before_price_fingerprint_or_experiment(
    tmp_path, monkeypatch
):
    import trading_assistant.strategy_lab.runner as runner

    store = Store(tmp_path / "lab")
    m = manifest(evaluation(50))
    m.update(
        start="2025-01-01T00:00:00+00:00", end_exclusive="2025-02-01T00:00:00+00:00"
    )

    def forbidden(*a, **kw):
        raise AssertionError("price access before custody check")

    monkeypatch.setattr(runner, "candle_fingerprint", forbidden)
    try:
        with pytest.raises(ValueError, match="sealed2025|sealed 2025"):
            run_experiment(
                store, (), m, rule(), Execution(), decision_adapter="discovery-v1"
            )
        assert store.db.execute("select count(*) from experiments").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "feature,operator,threshold",
    [
        ("rsi", "le", D(1)),
        ("signed_body", "eval", D(1)),
        ("signed_body", "le", D("NaN")),
    ],
)
def test_rule_language_cannot_execute_arbitrary_features_or_code(
    feature, operator, threshold
):
    with pytest.raises(ValueError):
        d.Condition(feature, operator, threshold)
