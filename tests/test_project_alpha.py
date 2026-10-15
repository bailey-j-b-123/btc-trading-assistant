"""Synthetic mechanism tests only. No fixtures count as historical profits."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
import pytest
from trading_assistant.market_data.types import Candle
from trading_assistant.strategy_lab import alpha as a
from trading_assistant.strategy_lab.models import Execution
from trading_assistant.strategy_lab.simulation import initial_state, step
from trading_assistant.strategy_lab.data import candle_fingerprint
from trading_assistant.strategy_lab.store import Store, MARKER


def fixture(name):
    start = datetime(2023, 4, 1, 22, tzinfo=UTC)
    bars = [
        Candle(
            "binance",
            "BTC/USDT",
            "1h",
            start + timedelta(hours=i),
            D(100),
            D(101),
            D(99),
            D(100),
            D(10),
        )
        for i in range(169)
    ]
    if name == a.NAMES[0]:
        for i, p in enumerate([98, 96, 94, 93, 92, 93], 163):
            bars[i] = replace(
                bars[i],
                open=D(p),
                high=D(p) + D(".2"),
                low=D(p) - D(".2"),
                close=D(p),
                volume=D(1),
            )
    else:
        bars[146] = replace(bars[146], close=D("100.5"))
        bars[-1] = replace(bars[-1], close=D("100.75"))
    return tuple(bars)


@pytest.mark.parametrize("name", a.NAMES)
def test_positive_mechanism_uses_exact_anchors_and_preregistered_levels(name):
    c = fixture(name)
    known = c[-1].timestamp + timedelta(hours=1)
    s = a.decide(c, a.Hypothesis(name), known_at=known)
    assert s and D(s["reference"]) == c[-1].close
    assert D(s["target"]) - D(s["reference"]) == 2 * (D(s["reference"]) - D(s["stop"]))
    assert (
        s["known_at"] == known.isoformat()
        and s["candle_open"] == c[-1].timestamp.isoformat()
    )
    assert datetime.fromisoformat(s["evidence"]["atr_last_close"]) <= known
    if name == a.NAMES[0]:
        assert (
            s["evidence"]["anchor"] == "100" and s["evidence"]["trough_close"] == "92"
        )
        assert (
            s["evidence"]["shock_volume_mean"] == "1"
            and s["evidence"]["baseline48_volume_median"] == "10"
        )
        assert D(s["stop"]) == D("91.8") - D(s["evidence"]["atr"]) / 4
    else:
        assert (
            known.hour == 23
            and s["evidence"]["first_open"] == c[146].timestamp.isoformat()
        )
        assert a.Hypothesis(name).max_hold == 1


@pytest.mark.parametrize("name", a.NAMES)
def test_insufficient_duplicate_gap_future_wrong_timeframe_and_order_refused(name):
    c = fixture(name)
    h = a.Hypothesis(name)
    at = c[-1].timestamp + timedelta(hours=1)
    for bad in (
        c[1:],
        c[:50] + (c[49],) + c[51:],
        c[:50] + c[51:],
        tuple(reversed(c)),
        tuple(replace(x, timeframe="4h") for x in c),
        c[1:] + (replace(c[-1], timestamp=at),),
    ):
        assert a.decide(bad, h, known_at=at) is None
    assert a.decide(c, h, known_at=at - timedelta(microseconds=1)) is None
    assert a.decide(c, h, known_at=at + timedelta(hours=1)) is None


def test_shock_without_thin_volume_or_without_recovery_is_not_evidence():
    c = fixture(a.NAMES[0])
    h = a.Hypothesis(a.NAMES[0])
    at = c[-1].timestamp + timedelta(hours=1)
    assert a.decide(tuple(replace(x, volume=D(10)) for x in c), h, known_at=at) is None
    assert a.decide(c[:-1] + (replace(c[-1], close=D(91)),), h, known_at=at) is None
    flat = tuple(
        replace(x, open=D(100), low=D(99), high=D(101), close=D(100)) for x in c
    )
    assert a.decide(flat, h, known_at=at) is None


def test_utc_clock_and_first_bar_are_required_not_generic_momentum():
    c = fixture(a.NAMES[1])
    h = a.Hypothesis(a.NAMES[1])
    at = c[-1].timestamp + timedelta(hours=1)
    bad = list(c)
    bad[146] = replace(bad[146], close=D(100))
    assert a.decide(tuple(bad), h, known_at=at) is None
    bad = list(c)
    bad[-1] = replace(bad[-1], close=D("100.1"))
    assert a.decide(tuple(bad), h, known_at=at) is None
    later = tuple(replace(x, timestamp=x.timestamp + timedelta(hours=1)) for x in c)
    assert a.decide(later, h, known_at=at + timedelta(hours=1)) is None


@pytest.mark.parametrize("name", a.NAMES)
@pytest.mark.parametrize(
    "outcome", ["target", "stop", "ambiguous", "gap_stop", "time_exit", "entry_gap"]
)
def test_actual_signals_enter_next_open_and_settle_conservatively(name, outcome):
    c = fixture(name)
    h = a.Hypothesis(name)
    e = Execution()
    state, events = step(initial_state(e), c, 168, h, e, decision_function=a.decide)
    assert (
        state["filled"] == 0
        and state["pending"]
        and sum(x["kind"] == "signal" for x in events) == 1
    )
    p = state["pending"]
    ref, stop, target = (D(p[k]) for k in ("reference", "stop", "target"))
    low, high, op = ref - D(".01"), ref + D(".01"), ref
    if outcome in ("target", "ambiguous"):
        high = target + 1
    if outcome in ("stop", "ambiguous"):
        low = stop - 1
    if outcome == "gap_stop":
        op = low = stop - 1
    if outcome == "entry_gap":
        op = ref * D("1.02")
        high = op + 1
        low = op - D(".01")
    nextbar = replace(
        c[-1],
        timestamp=c[-1].timestamp + timedelta(hours=1),
        open=op,
        low=low,
        high=high,
        close=op,
    )
    if outcome == "gap_stop":
        # Opening below stop on the first execution bar is rejected, not filled.
        state, events = step(
            state, c + (nextbar,), 169, h, e, decision_function=a.decide
        )
        assert state["filled"] == 0 and state["pending"] is None
        assert any(x.get("reason") == "entry_gap_or_invalid_levels" for x in events)
        return
    if outcome == "time_exit":
        # Use real holding limit; do not change strategy parameters.
        bars = list(c)
        for _ in range(h.max_hold):
            bars.append(
                replace(nextbar, timestamp=bars[-1].timestamp + timedelta(hours=1))
            )
        for i in range(169, len(bars)):
            state, events = step(state, tuple(bars), i, h, e, signal_enabled=False)
    else:
        state, events = step(
            state, c + (nextbar,), 169, h, e, decision_function=a.decide
        )
    if outcome == "entry_gap":
        assert state["filled"] == 0 and any(
            x.get("reason") == "entry_gap_or_invalid_levels" for x in events
        )
        return
    trades = [x for x in events if x["kind"] == "trade"]
    assert len(trades) == 1
    t = trades[0]
    assert t["entry_at"] == nextbar.timestamp.isoformat()
    assert D(t["entry"]) == ref * (1 + e.impact)
    assert D(t["entry_fee"]) == D(t["quantity"]) * D(t["entry"]) * e.fee
    assert t["exit_reason"] == ("stop" if outcome == "ambiguous" else outcome)
    assert t["ambiguous"] == (outcome == "ambiguous")
    assert D(t["net_pnl"]) == D(t["quantity"]) * (D(t["exit"]) - D(t["entry"])) - D(
        t["entry_fee"]
    ) - D(t["exit_fee"])
    assert D(t["initial_price_risk"]) <= D(t["risk_budget"])


@pytest.mark.parametrize("name", a.NAMES)
def test_source_duplicate_protection_and_future_suffix_cannot_change_earlier_decision(
    name,
):
    c = fixture(name)
    h = a.Hypothesis(name)
    e = Execution()
    one, ev = step(initial_state(e), c, 168, h, e, decision_function=a.decide)
    repeated, ev = step(one, c, 168, h, e, decision_function=a.decide)
    assert not any(x["kind"] == "signal" for x in ev)
    assert repeated["opportunities"] == one["opportunities"] == 1
    s = a.decide(c, h, known_at=c[-1].timestamp + timedelta(hours=1))
    future = replace(
        c[-1], timestamp=c[-1].timestamp + timedelta(hours=1), close=D(99999)
    )
    state, _ = step(
        initial_state(e), c + (future,), 168, h, e, decision_function=a.decide
    )
    assert state["pending"]["source_plan_id"] == s["source_plan_id"]


def test_resume_atomic_rollback_records_identity_and_independent_money(
    tmp_path, monkeypatch
):
    c = fixture(a.NAMES[1])
    last = replace(
        c[-1],
        timestamp=c[-1].timestamp + timedelta(hours=1),
        open=D("100.75"),
        high=D(110),
        low=D(100),
        close=D(105),
    )
    c = c + (last,)
    monkeypatch.setattr(
        a.novel,
        "PERIODS",
        (
            (
                "development",
                c[0].timestamp.isoformat(),
                (c[-1].timestamp + timedelta(hours=1)).isoformat(),
            ),
        ),
    )
    m = {
        "timeframe": "1h",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "candles": len(c),
        "start": c[0].timestamp.isoformat(),
        "end_exclusive": (c[-1].timestamp + timedelta(hours=1)).isoformat(),
        "candle_sha256": candle_fingerprint(c),
        "missing_candles": 0,
        "source_receipts": [],
        "synthetic_test_only": True,
    }
    one, two = Store(tmp_path / "one"), Store(tmp_path / "two")
    h = a.Hypothesis(a.NAMES[1])
    try:
        full = a.run(one, c, m, h, "base", "development")
        assert full["result"]["closed_trades"] == 1

        def fail():
            raise RuntimeError("injected interruption")

        with pytest.raises(RuntimeError, match="injected"):
            a.run(two, c, m, h, "base", "development", fault=fail)
        assert two.db.execute("select count(*) from events").fetchone()[0] == 0
        a.run(two, c, m, h, "base", "development", max_boundaries=169)
        two.close()
        two = Store(tmp_path / "two")
        recovered = a.run(two, c, m, h, "base", "development")
        assert recovered == full
        assert two.records(recovered["id"]) == one.records(full["id"])
        assert a.run(two, c, m, h, "base", "development")["deduplicated"]
        assert D(full["result"]["audit"]["cash_error"]) == 0
        assert D(full["result"]["details"]["money_residual"]) == 0
        assert two.db.execute("pragma integrity_check").fetchone()[0] == "ok"
    finally:
        one.close()
        two.close()


def test_sealed_dates_and_unsupported_models_refuse_before_database_work(tmp_path):
    c = fixture(a.NAMES[0])
    s = Store(tmp_path / "lab")
    try:
        for period in ("holdout", "2025", "2026", "forward"):
            with pytest.raises(ValueError, match="unapproved"):
                a.run(s, c, {}, a.Hypothesis(a.NAMES[0]), "base", period)
        assert s.db.execute("select count(*) from experiments").fetchone()[0] == 0
        with pytest.raises(ValueError):
            a.Hypothesis("seventh hypothesis")
        with pytest.raises(ValueError):
            a.Hypothesis(a.NAMES[0], timeframe="4h")
        with pytest.raises(ValueError):
            a.novel.costs("free")
    finally:
        s.close()
    root = tmp_path / "unmarked"
    root.mkdir()
    with pytest.raises(FileNotFoundError):
        a.safe_source(root)
    (root / "LAB-ONLY.txt").write_text(MARKER)
    (root / "archives").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="marked"):
        a.safe_source(root)


def test_frozen_spec_identity_and_dependence_diagnostic_controls():
    spec = Path(__file__).parents[1] / "docs/research/project-alpha-v1-spec.md"
    assert sha256(spec.read_bytes()).hexdigest() == a.SPEC_SHA
    assert a.Hypothesis(a.NAMES[0]).fingerprint != a.Hypothesis(a.NAMES[1]).fingerprint
    assert a.block_lower([D(1)] * 59) is None
    assert a.block_lower([D(1)] * 70) == 1
    assert a.block_lower([D(-1)] * 70) == -1
    assert a.block_lower([D(-1), D(2)] * 35) == a.block_lower([D(-1), D(2)] * 35)
    assert a.verdict([])["decision"] == "B"


def test_frozen_promotion_standards_do_not_select_positive_headline_only():
    rows = []
    for period, _, _ in a.PERIODS:
        for cost in ("base", "stress"):
            rows.append(
                {
                    "result": {
                        "period": period,
                        "cost": cost,
                        "closed_trades": 110,
                        "net_return": "0.1",
                        "expectancy_r": "0.2",
                        "maximum_close_mark_drawdown": "0.05",
                        "unresolved_gap_positions": 0,
                        "open_positions": 0,
                        "pending_entries": 0,
                        "ambiguous_adverse_trades": 0,
                        "details": {
                            "block7_lower_daily_usdt": 0.01,
                            "net_without_top2_wins": "1",
                            "positive_months": 4,
                            "observed_months": 4,
                        },
                    }
                }
            )
    assert a.verdict(rows)["decision"] == "A"
    rows[-1]["result"]["details"]["block7_lower_daily_usdt"] = -0.01
    assert a.verdict(rows)["decision"] == "B"
    rows[-1]["result"]["net_return"] = "-0.01"
    assert a.verdict(rows)["decision"] == "C"


def test_active_trade_at_data_gap_stays_unresolved_never_favourable_exit():
    c = fixture(a.NAMES[0])
    h = a.Hypothesis(a.NAMES[0])
    e = Execution()
    s, _ = step(initial_state(e), c, 168, h, e, decision_function=a.decide)
    normal = replace(
        c[-1],
        timestamp=c[-1].timestamp + timedelta(hours=1),
        open=D(93),
        close=D(93),
        low=D("92.9"),
        high=D("93.1"),
    )
    s, events = step(s, c + (normal,), 169, h, e, signal_enabled=False)
    assert s["filled"] == 1 and s["position"]
    gap = replace(normal, timestamp=normal.timestamp + timedelta(hours=2), high=D(999))
    s, events = step(s, c + (normal, gap), 170, h, e, signal_enabled=False)
    assert s["has_unresolved_gap"] and s["position"] is None
    assert any(r["kind"] == "unresolved" for r in events) and not any(
        r["kind"] == "trade" for r in events
    )
    assert all(
        r["liquidation_equity"] is None for r in events if r["kind"] == "equity_mark"
    )
