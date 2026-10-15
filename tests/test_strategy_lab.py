"""Offline synthetic correctness tests, never evidence of a trading edge."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import json
import math
import pytest
from trading_assistant.market_data.types import Candle
from trading_assistant.strategy_lab.models import (
    Strategy,
    Execution,
    candidates,
)
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.simulation import initial_state, step, metrics
from trading_assistant.strategy_lab.strategies import decide
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.data import acquire, dataset, candle_fingerprint
from test_public_archive import archive


def candles(n=1200):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for i in range(n):
        value = D(str(100 + 3 * math.sin(i / 11) + 0.02 * i))
        result.append(
            Candle(
                "binance",
                "BTC/USDT",
                "1h",
                start + timedelta(hours=i),
                value,
                value + D("1"),
                value - D("1"),
                value,
                D("10"),
            )
        )
    return tuple(result)


def manifest(c):
    return {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "start": c[0].timestamp.isoformat(),
        "end_exclusive": (c[-1].timestamp + timedelta(hours=1)).isoformat(),
        "candles": len(c),
        "candle_sha256": candle_fingerprint(c),
        "test_only": "SYNTHETIC; NOT PERFORMANCE EVIDENCE",
    }


def test_actual_strategy_end_to_end_reproducible_deduplicated_and_resumable(tmp_path):
    c = candles()
    s = Strategy()
    e = Execution()
    a = Store(tmp_path / "a")
    b = Store(tmp_path / "b")
    try:
        full = run_experiment(a, c, manifest(c), s, e)
        assert full["result"]["filled_trades"] > 0
        for budget in (13, 57, 139):
            partial = run_experiment(b, c, manifest(c), s, e, max_boundaries=budget)
            assert not partial["complete"]
            b.close()
            b = Store(tmp_path / "b")
        recovered = run_experiment(b, c, manifest(c), s, e)
        before = {
            k: v
            for k, v in full["result"].items()
            if k != "runtime_this_attempt_seconds"
        }
        after = {
            k: v
            for k, v in recovered["result"].items()
            if k != "runtime_this_attempt_seconds"
        }
        assert before == after
        assert a.records(full["id"]) == b.records(recovered["id"])
        assert run_experiment(b, c, manifest(c), s, e)["deduplicated"]
        assert b.status()["integrity"] == "ok" and b.status()["foreign_key_errors"] == 0
    finally:
        a.close()
        b.close()


def test_interrupted_database_batch_rolls_back_and_then_recovers(tmp_path):
    c = candles(140)
    store = Store(tmp_path / "lab")
    s = Strategy()
    e = Execution()

    def fail():
        raise RuntimeError("injected transaction interruption")

    with pytest.raises(RuntimeError, match="injected"):
        run_experiment(store, c, manifest(c), s, e, batch=100, fault=fail)
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert (
        json.loads(store.db.execute("SELECT state FROM experiments").fetchone()[0])[
            "index"
        ]
        == -1
    )
    result = run_experiment(store, c, manifest(c), s, e)
    assert result["complete"]
    store.close()


def test_research_excludes_partial_source_interval_and_preserves_raw_receipt(tmp_path):
    raw, checksum = archive(
        month="2025-01",
        timeframe="1h",
        mutate=lambda rows: rows[12].__setitem__(6, rows[12][6] - 1000000),
    )
    from trading_assistant.market_data.public_archive import decode_archive

    with pytest.raises(ValueError, match="closed-candle"):
        decode_archive(raw, checksum=checksum, month="2025-01", timeframe="1h")
    store = Store(tmp_path / "lab")
    receipt = acquire(
        store,
        ["2025-01"],
        download=lambda url: checksum.encode() if url.endswith(".CHECKSUM") else raw,
    )[0]
    excluded = receipt["excluded_source_rows"]
    assert len(excluded) == 1 and excluded[0]["row_index"] == 12
    assert excluded[0]["recorded_close"] == int(excluded[0]["raw_row"][6])
    assert receipt["candles"] == 743 and receipt["declared_missing_candles"] == 1
    store.close()


def test_forged_end_and_undeclared_gap_are_refused(tmp_path):
    c = candles(100)
    store = Store(tmp_path / "lab")
    with pytest.raises(ValueError, match="end"):
        run_experiment(
            store,
            c,
            {**manifest(c), "end_exclusive": c[-1].timestamp.isoformat()},
            Strategy(),
            Execution(),
        )
    gap = c[:80] + c[81:]
    with pytest.raises(ValueError, match="gaps"):
        run_experiment(store, gap, manifest(gap), Strategy(), Execution())
    store.close()


def test_causality_future_mutations_and_no_manual_signals():
    c = candles()
    s = Strategy()
    found = False
    for i in range(s.warmup, len(c)):
        at = c[i].timestamp + timedelta(hours=1)
        prefix = decide(c[: i + 1], s, known_at=at)
        future = tuple(replace(x, open=D("NaN"), close=D("999999")) for x in c[i + 1 :])
        assert decide(c[: i + 1] + future, s, known_at=at) == prefix
        found |= prefix is not None
    assert found


def test_independently_recompute_a_real_synthetic_strategy_trade(tmp_path):
    c = candles()
    store = Store(tmp_path / "lab")
    e = Execution()
    result = run_experiment(store, c, manifest(c), Strategy(), e)
    trade = store.records(result["id"], "trade")[0]
    qty = D(trade["quantity"])
    entry = D(trade["entry"])
    exit_ = D(trade["exit"])
    expected = qty * (exit_ - entry) - qty * entry * e.fee - qty * exit_ * e.fee
    assert D(trade["net_pnl"]) == expected
    assert D(trade["net_r"]) == expected / (qty * (entry - D(trade["stop"])))
    assert datetime.fromisoformat(trade["entry_at"]) >= datetime.fromisoformat(
        trade["decision"]
    )
    assert qty * entry <= e.max_exposure * e.initial_capital
    assert D(trade["entry_fee"]) == qty * entry * e.fee
    store.close()


def test_stop_target_same_candle_uses_adverse_fill_with_costs():
    e = Execution()
    s = Strategy()
    c = candles(3)
    state = initial_state(e)
    state["pending"] = {
        "reference": "100",
        "stop": "90",
        "target": "120",
        "quantity": "1",
        "entry_ceiling": "101",
        "risk_budget": "50",
        "equity_at_decision": "10000",
        "signal_id": "test-only",
        "decision": c[0].timestamp.isoformat(),
        "evidence": {"regime": "SYNTHETIC TEST ONLY"},
    }
    c = list(c)
    c[1] = replace(c[1], open=D("100"), high=D("130"), low=D("80"), close=D("110"))
    new, events = step(state, c, 1, s, e)
    trade = next(x for x in events if x["kind"] == "trade")
    assert trade["ambiguous"] is True and trade["exit_reason"] == "stop"
    assert D(trade["exit"]) == D("90") * (1 - e.impact)
    assert D(trade["net_pnl"]) < 0 and new["position"] is None


def test_actual_signal_rejects_open_below_invalidation_even_if_adverse_ask_exceeds_stop():
    c = list(candles(160))
    s, e = Strategy(), Execution()
    state = initial_state(e)
    for i in range(len(c) - 1):
        state, _ = step(state, c, i, s, e)
        if state["pending"]:
            stop = D(state["pending"]["stop"])
            opened = stop * (1 - D(".0002"))
            assert opened < stop < opened * (1 + e.impact)
            c[i + 1] = replace(
                c[i + 1],
                open=opened,
                high=opened + D(".1"),
                low=opened - D(".1"),
                close=opened,
            )
            new, events = step(state, c, i + 1, s, e)
            assert new["filled"] == 0 and new["cash"] == state["cash"]
            assert new["rejections"]["entry_gap_or_invalid_levels"] == 1
            assert not any(r["kind"] == "trade" for r in events)
            return
    pytest.fail("actual detector must produce the test signal")


def test_actual_signal_checks_minimum_notional_at_fill_not_only_sizing_ceiling():
    c = [
        replace(x, high=x.close + D(".3"), low=x.close - D(".3")) for x in candles(160)
    ]
    s, e = Strategy(), Execution(initial_capital=D("40.5"))
    state = initial_state(e)
    for i in range(len(c) - 1):
        state, _ = step(state, c, i, s, e)
        if state["pending"]:
            p = state["pending"]
            qty = D(p["quantity"])
            opened = e.minimum_notional / qty / (1 + e.impact) - D(".001")
            assert opened > D(p["stop"])
            assert (
                qty * D(p["entry_ceiling"])
                >= e.minimum_notional
                > qty * opened * (1 + e.impact)
            )
            c[i + 1] = replace(
                c[i + 1],
                open=opened,
                high=opened + D(".01"),
                low=opened - D(".01"),
                close=opened,
            )
            new, events = step(state, c, i + 1, s, e)
            assert new["filled"] == 0 and new["cash"] == state["cash"]
            assert new["rejections"]["entry_below_minimum_notional"] == 1
            assert not any(r["kind"] == "trade" for r in events)
            return
    pytest.fail("actual detector must produce the small-wallet test signal")


def test_gap_rejects_entry_and_active_gap_never_fabricates_a_profitable_exit():
    e = Execution()
    s = Strategy()
    c = candles(3)
    state = initial_state(e)
    state["last_timestamp"] = c[0].timestamp.isoformat()
    state["position"] = {"quantity": "1", "fill": "100", "stop": "90", "target": "120"}
    c = (c[0], replace(c[1], timestamp=c[1].timestamp + timedelta(hours=2)))
    new, events = step(state, c, 1, s, e)
    result = metrics(new, events, e)
    assert result["unresolved_gap_positions"] == 1
    assert result["net_return"] is None and result["liquidation_marked_equity"] is None
    assert not any(x["kind"] == "trade" for x in events)


def test_worker_exclusion_and_directory_guards(tmp_path):
    a = Store(tmp_path / "lab")
    b = Store(tmp_path / "lab")
    with a.writer():
        with pytest.raises(RuntimeError, match="another lab worker"):
            with b.writer():
                pass
    with b.writer():
        pass
    a.close()
    b.close()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing.txt").write_text("preserve")
    with pytest.raises(ValueError, match="nonempty"):
        Store(occupied)
    with pytest.raises(ValueError):
        Store(tmp_path / "data")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "lab", target_is_directory=True)
    with pytest.raises(ValueError):
        Store(link)


def test_public_archive_pipeline_idempotent_exact_storage_and_provenance(tmp_path):
    raw, checksum = archive(month="2025-01", timeframe="1h")
    store = Store(tmp_path / "lab")

    def download(url):
        return checksum.encode() if url.endswith(".CHECKSUM") else raw

    first = acquire(store, ["2025-01"], download=download)
    again = acquire(store, ["2025-01"], download=download)
    assert first[0]["inserted"] == 744 and again[0]["inserted"] == 0
    c, m = dataset(
        store, datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)
    )
    assert len(c) == 744 and c[0].close == D("101") and len(m["source_receipts"]) == 1
    assert m["source_receipts"][0]["sha256"] == first[0]["sha256"]
    assert (
        run_experiment(store, c, m, Strategy(), Execution())["result"]["filled_trades"]
        == 0
    )
    store.close()


def test_strategy_grid_is_frozen_bounded_and_input_validation():
    a = candidates()
    assert a == candidates() and len({s.fingerprint for s in a}) == len(a)
    assert len(candidates(2)) == 2
    for kwargs in (
        {"fast": 50, "slow": 20},
        {"family": "invented_pattern"},
        {"stop_atr": D("NaN")},
    ):
        with pytest.raises(ValueError):
            Strategy(**kwargs)
    with pytest.raises(ValueError):
        Execution(fee_bps=D("-1"))
    with pytest.raises(ValueError):
        Execution(max_positions=2)


def test_flat_data_gap_does_not_invent_loss_or_permanently_freeze_the_portfolio():
    c = candles(100)
    state = initial_state(Execution())
    state["last_timestamp"] = c[97].timestamp.isoformat()
    updated, events = step(state, c, 99, Strategy(), Execution())
    assert not updated.get("has_unresolved_gap", False)
    assert updated["data_gap_boundaries"] == 1
    assert not any(e["kind"] in {"trade", "unresolved"} for e in events)


def test_dataset_fingerprint_and_api_writer_guards(tmp_path):
    c = candles(100)
    a = Store(tmp_path / "lab")
    b = Store(tmp_path / "lab")
    with a.writer():
        with pytest.raises(RuntimeError, match="another lab worker"):
            run_experiment(b, c, manifest(c), Strategy(), Execution())
    with pytest.raises(ValueError, match="fingerprint"):
        run_experiment(
            a, c, {**manifest(c), "candle_sha256": "fake"}, Strategy(), Execution()
        )
    a.close()
    b.close()


def test_research_admits_and_declares_real_gaps_without_repairing_them(tmp_path):
    raw, checksum = archive(
        month="2025-01", timeframe="1h", mutate=lambda rows: rows.pop(12)
    )
    from trading_assistant.market_data.public_archive import decode_archive

    with pytest.raises(ValueError, match="missing"):
        decode_archive(raw, checksum=checksum, month="2025-01", timeframe="1h")
    store = Store(tmp_path / "lab")

    def download(url):
        return checksum.encode() if url.endswith(".CHECKSUM") else raw

    receipt = acquire(store, ["2025-01"], download=download)[0]
    c, m = dataset(
        store, datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)
    )
    assert (
        len(c) == 743
        and receipt["declared_missing_candles"] == 1
        and m["missing_candles"] == 1
    )
    assert len(m["gaps"]) == 1 and m["gaps"][0]["missing_count"] == 1
    store.close()
