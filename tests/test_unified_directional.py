"""Synthetic offline financial contracts, not strategy performance evidence."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import copy
import json
import pytest
from trading_assistant.market_data.types import Candle
from trading_assistant.strategy_lab.models import Execution, Strategy, identity
from trading_assistant.strategy_lab.directional import DirectionalModel, FundingEvent
from trading_assistant.strategy_lab.simulation import initial_state, step
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.novel import independent_audit
from trading_assistant.strategy_lab.report import report
from test_strategy_lab import manifest

START = datetime(2024, 1, 1, tzinfo=UTC)
MODEL = DirectionalModel()


@dataclass(frozen=True)
class FixturePlans:
    plans: tuple
    timeframe: str = "1h"
    max_hold: int = 2
    warmup: int = 1

    @property
    def fingerprint(self):
        return identity(self)


def fixture_provider(candles, strategy, *, known_at):
    """PREDECLARED synthetic finance fixture, never used for market research."""
    i = (
        int(
            (known_at - START) / timedelta(hours=1 if strategy.timeframe == "1h" else 4)
        )
        - 1
    )
    for index, direction, stop, target in strategy.plans:
        if index == i:
            c = candles[-1]
            return dict(
                direction=direction,
                known_at=known_at.isoformat(),
                candle_open=c.timestamp.isoformat(),
                reference=str(c.close),
                stop=str(stop),
                target=str(target),
                evidence={"regime": "synthetic_financial_fixture"},
            )
    return None


def bars(prices):
    return tuple(
        Candle(
            "binance",
            "BTC/USDT",
            "1h",
            START + timedelta(hours=i),
            D(o),
            D(h),
            D(low),
            D(c),
            D(10),
        )
        for i, (o, h, low, c) in enumerate(prices)
    )


def flat(n=10):
    return bars([("100", "101", "99", "100")] * n)


def execute(
    tmp_path,
    c,
    strategy,
    *,
    model=MODEL,
    e=Execution(),
    marks=None,
    funding=(),
    name="lab",
    max_boundaries=None,
    fault=None,
):
    store = Store(tmp_path / name)
    try:
        run = run_experiment(
            store,
            c,
            {
                **manifest(c),
                "timeframe": c[0].timeframe,
                "end_exclusive": (
                    c[-1].timestamp
                    + timedelta(hours=1 if c[0].timeframe == "1h" else 4)
                ).isoformat(),
            },
            strategy,
            e,
            directional_model=model,
            mark_candles=marks,
            funding_events=funding,
            signal_provider=fixture_provider,
            max_boundaries=max_boundaries,
            fault=fault,
        )
        row = store.db.execute(
            "select * from experiments where id=?", (run["id"],)
        ).fetchone()
        state = json.loads(row["state"])
        records = store.records(run["id"])
        return run, state, records, report(store)
    finally:
        store.close()


def test_alternating_long_short_one_cash_account_and_equity_curve(tmp_path):
    c = flat()
    s = FixturePlans(
        (
            (0, "LONG", D(95), D(110)),
            (2, "SHORT", D(105), D(90)),
            (4, "LONG", D(95), D(110)),
            (6, "SHORT", D(105), D(90)),
        )
    )
    f = (
        FundingEvent(START + timedelta(hours=2), D(".0001"), D(100)),
        FundingEvent(START + timedelta(hours=4), D(".0001"), D(100)),
    )
    run, state, records, rep = execute(tmp_path, c, s, funding=f)
    trades = sorted(
        (r for r in records if r["kind"] == "trade"), key=lambda x: x["entry_at"]
    )
    assert [t["direction"] for t in trades] == ["LONG", "SHORT", "LONG", "SHORT"]
    assert state["position"] is None and state["filled"] == 4
    assert D(state["cash"]) == D(10000) + sum(D(t["net_pnl"]) for t in trades)
    payments = [r for r in records if r["kind"] == "funding"]
    assert (
        len(payments) == 2 and D(payments[0]["amount"]) * D(payments[1]["amount"]) < 0
    )
    checked = independent_audit(
        state, records, c, Execution(), directional_model=MODEL, funding_events=f
    )
    assert D(checked["maximum_currency_error"]) < D("1e-18") and checked[
        "marks_checked"
    ] == len(c)
    json.dumps(run)  # same public JSON-reporting contract as default mode
    assert run["result"]["audit"]["trades_checked"] == 4
    assert run["result"]["by_direction"]["SHORT"]["closed"] == 2
    assert rep["historical"][0]["result"]["execution_model"] == MODEL.version
    assert rep["historical"][0]["definition"]["directional_execution"]["funding_events"]


@pytest.mark.parametrize(
    "side,stop,target,exitbar,reason",
    [
        ("LONG", "95", "110", ("100", "111", "99", "109"), "target"),
        ("SHORT", "105", "90", ("100", "101", "89", "91"), "target"),
        ("LONG", "95", "110", ("100", "111", "94", "100"), "stop"),
        ("SHORT", "105", "90", ("100", "106", "89", "100"), "stop"),
        ("LONG", "95", "110", ("90", "91", "89", "90"), "gap_stop"),
        ("SHORT", "105", "90", ("110", "111", "109", "110"), "gap_stop"),
        (
            "LONG",
            "95",
            "110",
            ("115", "116", "114", "115"),
            "gap_target_no_favourable_improvement",
        ),
        (
            "SHORT",
            "105",
            "90",
            ("85", "86", "84", "85"),
            "gap_target_no_favourable_improvement",
        ),
    ],
)
def test_directional_exit_prices_costs_and_adverse_ambiguity(
    tmp_path, side, stop, target, exitbar, reason
):
    # Fill a flat first entry bar; the tested exit occurs on the next bar.
    c = bars(
        [
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
            exitbar,
            ("100", "101", "99", "100"),
        ]
    )
    s = FixturePlans(((0, side, D(stop), D(target)),), max_hold=4)
    run, state, r, _ = execute(tmp_path, c, s)
    t = next(x for x in r if x["kind"] == "trade")
    assert t["exit_reason"] == reason
    assert t["ambiguous"] == (reason == "stop")
    independent_audit(state, r, c, Execution(), directional_model=MODEL)
    if reason == "target":
        assert D(t["net_pnl"]) > 0
    if reason == "gap_stop":
        assert D(t["net_pnl"]) < 0 and D(t["raw_exit"]) == D(exitbar[0])


def test_open_short_locks_collateral_no_phantom_proceeds(tmp_path):
    c = flat(2)
    s = FixturePlans(((0, "SHORT", D(105), D(90)),), max_hold=10)
    run, state, r, _ = execute(tmp_path, c, s)
    p = state["position"]
    assert p and D(state["cash"]) < 10000
    assert D(state["cash"]) + D(p["collateral"]) == 10000
    assert D(state["equity"]) < 10000 and run["result"]["open_positions"] == 1
    independent_audit(state, r, c, Execution(), directional_model=MODEL)


def test_occupied_slot_and_no_trade_share_same_interface(tmp_path):
    s = FixturePlans(
        (
            (0, "SHORT", D(105), D(90)),
            (1, "LONG", D(95), D(110)),
            (2, "NO_TRADE", D(95), D(110)),
        ),
        max_hold=5,
    )
    run, state, r, _ = execute(tmp_path, flat(4), s)
    assert (
        state["filled"] == 1
        and state["opportunities"] == 2
        and state["rejections"] == {"occupied_slot": 1}
    )


@pytest.mark.parametrize("side,adverse", [("SHORT", "210"), ("LONG", "0.001")])
def test_mark_liquidation_precedes_possible_protective_stop(tmp_path, side, adverse):
    c = flat(3)
    m = list(c)
    m[2] = replace(
        m[2],
        high=D(adverse) if side == "SHORT" else D(101),
        low=D(adverse) if side == "LONG" else D(99),
    )
    model = replace(MODEL, mark_source="recorded_marks")
    levels = (D(95), D(110)) if side == "LONG" else (D(105), D(90))
    run, state, r, _ = execute(
        tmp_path,
        c,
        FixturePlans(((0, side, *levels),), max_hold=4),
        model=model,
        marks=tuple(m),
    )
    t = next(x for x in r if x["kind"] == "trade")
    assert t["exit_reason"] == "liquidation" and D(t["liquidation_fee"]) > 0
    assert D(t["net_pnl"]) < 0 and t["ambiguous"]
    independent_audit(
        state, r, c, Execution(), directional_model=model, mark_candles=tuple(m)
    )
    if side == "SHORT":
        assert (
            state["has_insolvency"]
            and D(state["collateral_deficit"]) > 0
            and run["result"]["net_return"] is None
        )


def test_liquidation_deficit_freezes_new_entries(tmp_path):
    c = bars(
        [
            ("100", "101", "99", "100"),
            ("300", "301", "299", "300"),
            ("300", "301", "299", "300"),
        ]
    )
    # Do not permit entry through an adverse gap (no fictional stop-price fill).
    _, state, r, _ = execute(tmp_path, c, FixturePlans(((0, "SHORT", D(105), D(90)),)))
    assert (
        state["filled"] == 0 and state["rejections"]["entry_gap_or_invalid_levels"] == 1
    )


def test_restart_rollback_and_complete_dedup(tmp_path):
    c = flat(10)
    s = FixturePlans(
        (
            (0, "LONG", D(95), D(110)),
            (2, "SHORT", D(105), D(90)),
            (5, "SHORT", D(105), D(90)),
        )
    )
    full, fs, fr, _ = execute(tmp_path, c, s, name="full")
    partial, _, _, _ = execute(tmp_path, c, s, name="resume", max_boundaries=2)
    assert not partial["complete"]

    def fault():
        raise RuntimeError("synthetic checkpoint interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        execute(tmp_path, c, s, name="resume", fault=fault)
    resumed, rs, rr, _ = execute(tmp_path, c, s, name="resume")
    assert rs == fs and rr == fr
    assert execute(tmp_path, c, s, name="resume")[0]["deduplicated"]
    assert (
        full["result"]["liquidation_marked_equity"]
        == resumed["result"]["liquidation_marked_equity"]
    )


@pytest.mark.parametrize(
    "bad", ["net_pnl", "entry_fee", "funding", "carry", "quantity", "collateral"]
)
def test_independent_auditor_rejects_financial_corruption(tmp_path, bad):
    c = flat(5)
    _, st, r, _ = execute(tmp_path, c, FixturePlans(((0, "SHORT", D(105), D(90)),)))
    corrupt = copy.deepcopy(r)
    t = next(x for x in corrupt if x["kind"] == "trade")
    t[bad] = str(D(t[bad]) + D(".00000001"))
    with pytest.raises(AssertionError):
        independent_audit(st, corrupt, c, Execution(), directional_model=MODEL)


@pytest.mark.parametrize(
    "change", [{"risk_fraction": D(".006")}, {"max_exposure": D(".26")}]
)
def test_original_risk_caps_cannot_be_increased(tmp_path, change):
    with pytest.raises(ValueError, match="caps"):
        execute(tmp_path, flat(), FixturePlans(()), e=replace(Execution(), **change))


def test_cash_defaults_identical_to_parent_and_short_rejected_without_optin():
    c = flat(3)
    s = FixturePlans(((0, "SHORT", D(105), D(90)),))
    with pytest.raises(ValueError, match="cash Spot"):
        step(
            initial_state(Execution()),
            c,
            0,
            s,
            Execution(),
            decision_function=fixture_provider,
        )
    # Cash default output and state shape have no new keys/contracts.
    state, r = step(initial_state(Execution()), c, 0, Strategy(), Execution())
    assert "directional_model" not in state and not any("direction" in e for e in r)


def test_model_marks_funding_provider_identity_and_missing_data_admission(tmp_path):
    s = FixturePlans(())
    with pytest.raises(ValueError, match="marks"):
        execute(tmp_path, flat(), s, model=replace(MODEL, mark_source="recorded_marks"))
    with pytest.raises(ValueError, match="funding"):
        execute(
            tmp_path,
            flat(),
            s,
            funding=(FundingEvent(START + timedelta(minutes=1), D(".0001"), D(100)),),
        )
    a, _, _, _ = execute(tmp_path, flat(), s, name="a")
    b, _, _, _ = execute(
        tmp_path, flat(), s, model=replace(MODEL, carry_bps_per_day=D(11)), name="a"
    )
    assert a["id"] != b["id"]
    state, _ = step(
        initial_state(Execution()),
        flat(),
        0,
        s,
        Execution(),
        directional_model=MODEL,
        decision_function=fixture_provider,
    )
    with pytest.raises(ValueError, match="already processed"):
        step(state, flat(), 0, s, Execution(), directional_model=MODEL)


def test_missing_trade_candle_preserves_unresolved_short_and_unscored_results(tmp_path):
    c = flat(5)
    c = c[:2] + c[3:]
    m = manifest(c)
    m["missing_candles"] = 1
    s = FixturePlans(((0, "SHORT", D(105), D(90)),), max_hold=5)
    store = Store(tmp_path / "gap")
    try:
        run = run_experiment(
            store,
            c,
            m,
            s,
            Execution(),
            directional_model=MODEL,
            signal_provider=fixture_provider,
        )
        assert (
            run["result"]["unresolved_gap_positions"] == 1
            and run["result"]["net_return"] is None
        )
        assert not store.records(run["id"], "trade")
    finally:
        store.close()


@pytest.mark.parametrize("side,rate", [("LONG", ".001"), ("SHORT", "-.001")])
def test_funding_debits_are_costs_and_boundary_entries_do_not_capture_credits(
    tmp_path, side, rate
):
    c = flat(4)
    levels = (D(95), D(110)) if side == "LONG" else (D(105), D(90))
    s = FixturePlans(((0, side, *levels),), max_hold=3)
    events = (
        FundingEvent(START + timedelta(hours=1), -D(rate), D(100)),
        FundingEvent(START + timedelta(hours=2), D(rate), D(100)),
    )
    run, state, records, _ = execute(tmp_path, c, s, funding=events)
    paid = [r for r in records if r["kind"] == "funding"]
    assert len(paid) == 1 and D(paid[0]["amount"]) < 0
    assert D(run["result"]["funding"]) == D(paid[0]["amount"])
    independent_audit(
        state, records, c, Execution(), directional_model=MODEL, funding_events=events
    )


def test_unexpected_carry_funding_cost_breaches_are_flagged_not_optimistically_scored(
    tmp_path,
):
    model = replace(MODEL, carry_bps_per_day=D(0), funding_reserve_bps_per_day=D(0))
    s = FixturePlans(
        ((0, "LONG", D(95), D(110)), (2, "SHORT", D(105), D(90))), max_hold=2
    )
    f = (FundingEvent(START + timedelta(hours=2), D(".01"), D(100)),)
    run, state, r, _ = execute(tmp_path, flat(5), s, model=model, funding=f)
    assert (
        state["has_cost_breach"]
        and state["filled"] == 1
        and run["result"]["net_return"] is None
    )
    assert (
        "declared_carry_funding_reserve_exceeded" in run["result"]["rejection_reasons"]
    )
    independent_audit(
        state, r, flat(5), Execution(), directional_model=model, funding_events=f
    )


def test_same_shared_equity_sizes_later_trade_after_prior_loss(tmp_path):
    c = flat(6)
    s = FixturePlans(((0, "LONG", D(95), D(110)), (2, "SHORT", D(105), D(90))))
    _, _, r, _ = execute(tmp_path, c, s)
    t = sorted((x for x in r if x["kind"] == "trade"), key=lambda x: x["entry_at"])
    assert len(t) == 2 and D(t[1]["equity_at_decision"]) == 10000 + D(t[0]["net_pnl"])
    assert D(t[1]["risk_budget"]) == D(t[1]["equity_at_decision"]) * D(".005")
    assert D(t[1]["quantity"]) < D(t[0]["quantity"])


def test_four_hour_carry_and_funding_use_actual_elapsed_time(tmp_path):
    c = tuple(
        replace(b, timeframe="4h", timestamp=START + timedelta(hours=4 * i))
        for i, b in enumerate(flat(4))
    )
    s = FixturePlans(((0, "SHORT", D(105), D(90)),), timeframe="4h")
    run, state, r, _ = execute(tmp_path, c, s)
    t = next(x for x in r if x["kind"] == "trade")
    assert t["held_bars"] == 2
    assert D(t["carry"]) > 0
    independent_audit(state, r, c, Execution(), directional_model=MODEL)


def test_recorded_marks_and_funding_are_fingerprinted_before_writes(tmp_path):
    c = flat(4)
    s = FixturePlans(())
    model = replace(MODEL, mark_source="recorded_marks")
    a, _, _, _ = execute(tmp_path, c, s, model=model, marks=c)
    m = list(c)
    m[1] = replace(m[1], high=D(102))
    b, _, _, _ = execute(tmp_path, c, s, model=model, marks=tuple(m))
    f = (FundingEvent(START, D(".0001"), D(100)),)
    d, _, _, _ = execute(tmp_path, c, s, model=model, marks=c, funding=f)
    assert len({a["id"], b["id"], d["id"]}) == 3
    with pytest.raises(ValueError, match="unique"):
        execute(tmp_path, c, s, funding=f + f)


def test_research_contract_cannot_be_promoted_to_spot_poller(tmp_path, monkeypatch):
    from trading_assistant.strategy_lab import paper

    store = Store(tmp_path / "promotion")
    store.db.execute("create table promotions(candidate text, definition text)")
    store.db.execute(
        "insert into promotions values(?,?)",
        ("x", json.dumps({"directional_execution": {}})),
    )
    monkeypatch.setattr(paper, "paper_schema", lambda store: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("must refuse before public-feed access")

    monkeypatch.setattr(paper, "public_closed", forbidden)
    try:
        with pytest.raises(ValueError, match="research-only"):
            paper.poll_paper(store, now=START)
    finally:
        store.close()


@pytest.mark.parametrize("bad", ["", "FLAT", "bearish", "SHORTISH"])
def test_ambiguous_directions_cannot_silently_become_long(tmp_path, bad):
    with pytest.raises(ValueError, match="direction"):
        execute(tmp_path, flat(), FixturePlans(((0, bad, D(95), D(110)),)))


def test_provider_cannot_see_future_candles_and_future_mutation_does_not_rewrite_prefix(
    tmp_path,
):
    c = flat(8)
    s = FixturePlans(((0, "SHORT", D(105), D(90)),))
    _, _, r, _ = execute(tmp_path, c, s, name="old")
    changed = list(c)
    changed[-1] = replace(
        changed[-1], open=D(150), high=D(151), low=D(149), close=D(150)
    )
    _, _, rr, _ = execute(tmp_path, tuple(changed), s, name="changed")
    assert sorted(
        (x for x in r if x["kind"] == "trade"), key=lambda x: x["entry_at"]
    ) == sorted((x for x in rr if x["kind"] == "trade"), key=lambda x: x["entry_at"])


def test_existing_real_strategy_runs_through_same_directional_runner(tmp_path):
    from test_strategy_lab import candles

    c = candles(1200)
    with_store = Store(tmp_path / "existing")
    try:
        e = replace(Execution(), fee_bps=D(20), spread_bps=D(8), slippage_bps=D(10))
        run = run_experiment(
            with_store, c, manifest(c), Strategy(), e, directional_model=MODEL
        )
        state = json.loads(
            with_store.db.execute(
                "select state from experiments where id=?", (run["id"],)
            ).fetchone()[0]
        )
        records = with_store.records(run["id"])
        assert run["result"]["closed_trades"] > 0
        assert run["result"]["by_direction"]["SHORT"]["closed"] == 0
        audit = independent_audit(state, records, c, e, directional_model=MODEL)
        assert audit["marks_checked"] == len(c) and D(audit["cash_error"]) < D("1e-18")
    finally:
        with_store.close()


def test_directional_cannot_be_marked_forward_paper(tmp_path):
    store = Store(tmp_path / "historical-only")
    try:
        with pytest.raises(ValueError, match="historical research-only"):
            run_experiment(
                store,
                flat(),
                manifest(flat()),
                FixturePlans(()),
                Execution(),
                stage="forward_paper",
                directional_model=MODEL,
                signal_provider=fixture_provider,
            )
    finally:
        store.close()


def test_independent_audit_rejects_missing_open_carry_and_foreign_cost(tmp_path):
    c = flat(2)
    _, state, records, _ = execute(
        tmp_path, c, FixturePlans(((0, "SHORT", D(105), D(90)),), max_hold=4)
    )
    missing = [r for r in records if r["kind"] != "carry"]
    with pytest.raises(AssertionError):
        independent_audit(state, missing, c, Execution(), directional_model=MODEL)
    foreign = records + [
        {
            "kind": "funding",
            "signal_id": "not-a-position",
            "at": c[0].timestamp.isoformat(),
            "amount": "1",
        }
    ]
    with pytest.raises(AssertionError):
        independent_audit(state, foreign, c, Execution(), directional_model=MODEL)
