"""Bounded exploratory cash-Spot research, separate from the BRAIN engine.

No downloads, accounts, production databases, promotion, or holdout entry point.
The four definitions and their rejection rules were frozen before evaluation.
"""

from .entry_policy import entry_definition

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D, localcontext
from functools import lru_cache
from hashlib import sha256
import argparse
import json
from pathlib import Path
import random
import resource
import time

from trading_assistant.market_data.validation import validate_ohlcv_rows
from trading_assistant.market_structure.volatility import (
    VolatilityParameters, calculate_volatility,
)
from .data import candle_fingerprint, dataset
from .models import Execution, canonical, decimal_context, identity, research_code_fingerprint
from .simulation import DEFAULT_EXIT_POLICY, EXIT_POLICIES, initial_state, metrics, step, validate_exit_policy
from .store import Store, exclusive

SPEC_SHA = "439e3837eabed3ae47afb813e153b19a46c7966535136325d61b12ec3e298a25"
NAMES = ("trend_pullback", "breakout_retest", "compression_expansion", "range_sweep_reclaim")
PERIODS = (
    ("development", "2023-04-01", "2024-01-01"),
    ("diagnostic_gap_2023", "2023-01-01", "2024-01-01"),
    ("reused_validation_1", "2024-01-01", "2024-05-01"),
    ("reused_validation_2", "2024-05-01", "2024-09-01"),
    ("reused_validation_3", "2024-09-01", "2025-01-01"),
)


@lru_cache(maxsize=1)
def source_sha():
    return sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Hypothesis:
    name: str
    timeframe: str = "1h"
    version: str = "bounded-hypotheses-v1"

    def __post_init__(self):
        if self.name not in NAMES or self.timeframe != "1h" or self.version != "bounded-hypotheses-v1":
            raise ValueError("only four frozen hourly cash-long hypotheses")

    @property
    def warmup(self):
        return 121

    @property
    def max_hold(self):
        return 24 if self.name == "range_sweep_reclaim" else 48

    @property
    def fingerprint(self):
        return identity({"definition": asdict(self), "spec": SPEC_SHA,
                         "research_source": source_sha(), "engine_source": research_code_fingerprint()})


def mean(values):
    return sum(values, D(0)) / len(values)


def sd(values):
    average = mean(values)
    return mean([(v - average) ** 2 for v in values]).sqrt()


@decimal_context
def decide(candles, strategy, *, known_at):
    """Only confirmed contiguous candles; raw price anchors retained in evidence."""
    window = tuple(c for c in candles if c.timestamp + timedelta(hours=1) <= known_at)[-121:]
    if (len(window) < 121 or window[-1].timestamp + timedelta(hours=1) != known_at
            or any(c.timeframe != "1h" for c in window)
            or len({(c.exchange, c.symbol) for c in window}) != 1
            or any(b.timestamp - a.timestamp != timedelta(hours=1) for a, b in zip(window, window[1:]))):
        return None
    closes = [c.close for c in window]
    c = window[-1]
    atr = calculate_volatility(window, interval=timedelta(hours=1), as_of=known_at,
                               parameters=VolatilityParameters(period=14)).atr
    if atr is None or atr <= 0:
        return None
    fast, slow = mean(closes[-20:]), mean(closes[-100:])
    earlier_slow = mean(closes[-105:-5])
    evidence = {"family": strategy.name, "regime": "sma20_above_sma100" if fast > slow else "sma20_below_or_equal_sma100",
                "atr": str(atr), "sma20": str(fast), "sma100": str(slow),
                "window_start": window[0].timestamp.isoformat(), "closed_count": len(window)}
    source_id = None
    if strategy.name == "trend_pullback":
        if not (fast > slow > earlier_slow and closes[-2] <= mean(closes[-21:-1])
                and c.close > fast and c.close > c.open):
            return None
        stop = min(x.low for x in window[-3:]) - atr / 4
        target = c.close + 2 * (c.close - stop)
    elif strategy.name == "breakout_retest":
        # Select only the most recent earlier breakout; level is frozen there.
        breakout = None
        for j in range(len(window) - 2, len(window) - 8, -1):
            level = max(x.high for x in window[j-20:j])
            if window[j].close > level and window[j-1].close <= level:
                breakout = j, level
                break
        if breakout is None:
            return None
        j, level = breakout
        following = window[j+1:]
        if not (min(x.low for x in following) >= level - atr / 2
                and c.low <= level + atr / 4 and c.close > level and c.close > c.open):
            return None
        stop = min(x.low for x in following) - atr / 4
        target = c.close + 2 * (c.close - stop)
        evidence.update(breakout_open=window[j].timestamp.isoformat(), frozen_level=str(level))
        source_id = identity({"hypothesis": strategy.fingerprint, "breakout": evidence["breakout_open"], "level": str(level)})
    elif strategy.name == "compression_expansion":
        # 100 dispersion windows, each ending before the decision candle.
        dispersions = [sd(closes[end-20:end]) for end in range(21, 121)]
        if not (sd(closes[-21:-1]) < D("0.75") * mean(dispersions)
                and c.close > max(x.high for x in window[-21:-1])
                and c.high - c.low > D("1.5") * atr):
            return None
        stop = c.close - 2 * atr
        target = c.close + 3 * (c.close - stop)
    else:
        level = min(x.low for x in window[-21:-1])
        if not (abs(slow - earlier_slow) < atr / 2 and c.low < level
                and c.close > level and c.close > c.open and c.close < fast):
            return None
        stop, target = c.low - atr / 4, fast
        if target - c.close < D("1.5") * (c.close - stop):
            return None
        evidence["reclaimed_range_low"] = str(level)
    if not 0 < stop < c.close < target:
        return None
    signal = {"known_at": known_at.isoformat(), "candle_open": c.timestamp.isoformat(),
              "reference": str(c.close), "stop": str(stop), "target": str(target), "evidence": evidence}
    if source_id:
        signal["source_plan_id"] = source_id
    return signal


def costs(label):
    if label == "base":
        return Execution()
    if label == "stress":
        return Execution(fee_bps=D(20), spread_bps=D(4), slippage_bps=D(9))
    raise ValueError("only frozen base/stress costs")


def validate_source(candles, manifest, period):
    if period not in {p[0] for p in PERIODS}:
        raise ValueError("unapproved research period")
    _, start, end = next(p for p in PERIODS if p[0] == period)
    start, end = (datetime.fromisoformat(s).replace(tzinfo=UTC) for s in (start, end))
    if (not candles or manifest["timeframe"] != "1h" or manifest["exchange"] != "binance"
            or manifest["symbol"] != "BTC/USDT" or manifest["candles"] != len(candles)
            or datetime.fromisoformat(manifest["start"]) != start
            or datetime.fromisoformat(manifest["end_exclusive"]) != end
            or candles[0].timestamp != start or candles[-1].timestamp + timedelta(hours=1) != end
            or any(c.timestamp < start or c.timestamp >= end or (c.exchange, c.symbol, c.timeframe)
                   != ("binance", "BTC/USDT", "1h") for c in candles)
            or candle_fingerprint(candles) != manifest["candle_sha256"]):
        raise ValueError("frozen period/content/instrument mismatch; holdout is inaccessible")
    report = validate_ohlcv_rows([[int(c.timestamp.timestamp()*1000), c.open, c.high, c.low, c.close, c.volume]
                                 for c in candles], exchange="binance", symbol="BTC/USDT", timeframe="1h")
    missing = sum(int((b.timestamp-a.timestamp)/timedelta(hours=1))-1 for a, b in zip(candles, candles[1:]))
    if not report.is_valid or missing != manifest["missing_candles"]:
        raise ValueError("invalid geometry or undeclared gaps")
    for receipt in manifest["source_receipts"]:
        if not ("2023-01" <= receipt["month"] <= "2024-12"):
            raise ValueError("forbidden source year")


@exclusive
@decimal_context
def run(store, candles, manifest, strategy, cost, period, *, max_boundaries=None, fault=None,
        exit_policy=DEFAULT_EXIT_POLICY, entry_policy=None):
    """Same verified simulator, new content-addressed experiments, atomic resume."""
    if max_boundaries is not None and (type(max_boundaries) is not int or max_boundaries < 1):
        raise ValueError("positive work budget required")
    validate_exit_policy(exit_policy)
    entry_spec = entry_definition(entry_policy)
    validate_source(candles, manifest, period)
    execution = costs(cost)
    definition = {"hypothesis": asdict(strategy), "hypothesis_fingerprint": strategy.fingerprint,
                  "dataset": manifest, "execution": asdict(execution), "cost": cost,
                  "exit_policy": exit_policy,
                  "period": period, "stage": "exploratory_reused_chronological",
                  "spec_sha256": SPEC_SHA, "research_source_sha256": source_sha(),
                  "engine_source_sha256": research_code_fingerprint(), "simulator": "cash-simulator-v2"}
    if entry_spec is not None:
        definition["entry_policy"] = entry_spec
    key, row = store.experiment(definition, initial_state(execution))
    if row["result"] is not None:
        return {"id": key, "complete": True, "deduplicated": True, "result": json.loads(row["result"])}
    state, events = json.loads(row["state"]), []
    worked = 0
    for i in range(state["index"] + 1, len(candles)):
        if max_boundaries is not None and worked >= max_boundaries:
            break
        state, produced = step(state, candles, i, strategy, execution, decision_function=decide, exit_policy=exit_policy, entry_policy=entry_policy)
        events.extend(produced)
        worked += 1
        if worked % 128 == 0:
            store.checkpoint(key, state, events, fault=fault)
            events = []
    store.checkpoint(key, state, events, fault=fault)
    if state["index"] != len(candles)-1:
        return {"id": key, "complete": False, "next_index": state["index"]+1}
    records = store.records(key)
    result = metrics(state, records, execution, entry_policy=entry_policy)
    result.update(audit=independent_audit(state, records, candles, execution, exit_policy=exit_policy, entry_policy=entry_policy),
                  summary=summarize(records), processed_boundaries=len(candles), period=period,
                  cost=cost, hypothesis=strategy.name)
    store.checkpoint(key, state, [], result=result)
    return {"id": key, "complete": True, "deduplicated": False, "result": result}


def summarize(records):
    trades = [r for r in records if r["kind"] == "trade"]
    pnl = [D(t["net_pnl"]) for t in trades]
    hours = [D(str((datetime.fromisoformat(t["exit_at"])-datetime.fromisoformat(t["entry_at"])).total_seconds()))/3600 for t in trades]
    groups = {}
    for label, field in (("by_exit_reason", "exit_reason"), ("by_regime", "regime_at_decision"), ("by_year", "entry_at")):
        groups[label] = {}
        for t in trades:
            key = t[field][:4] if label == "by_year" else t[field]
            item = groups[label].setdefault(key, {"closed": 0, "net_pnl": D(0)})
            item["closed"] += 1
            item["net_pnl"] += D(t["net_pnl"])
    return {"realized_net_pnl": str(sum(pnl, D(0))),
            "gross_fill_pnl": str(sum((D(t["quantity"])*(D(t["exit"])-D(t["entry"])) for t in trades), D(0))),
            "fees": str(sum((D(t["entry_fee"])+D(t["exit_fee"]) for t in trades), D(0))),
            "mean_net_pnl": str(mean(pnl)) if pnl else None,
            "mean_holding_hours": str(mean(hours)) if hours else None,
            "max_holding_hours": str(max(hours)) if hours else None, **json.loads(canonical(groups))}


def independent_audit(state, records, candles, execution, *, exit_policy=DEFAULT_EXIT_POLICY, entry_policy=None):
    """Independent 60-digit fills/cash/mark accounting, no simulator calls."""
    validate_exit_policy(exit_policy)
    if exit_policy == "stop_target_only":
        assert not any(r["kind"] == "trade" and r["exit_reason"] == "time_exit" for r in records)
    entry_definition(entry_policy)
    market = {c.timestamp: c for c in candles}
    trades = sorted((r for r in records if r["kind"] == "trade"), key=lambda t: t["entry_at"])
    signals = {r["id"]: r for r in records if r["kind"] == "signal"}
    unresolved = [r["position"] for r in records if r["kind"] == "unresolved"]
    max_error, gross_raw, impact_cost, fees = D(0), D(0), D(0), D(0)
    with localcontext() as ctx:
        ctx.prec = 60
        cash = execution.initial_capital
        last_exit = None
        for t in trades:
            entry_at, exit_at = (datetime.fromisoformat(t[k]) for k in ("entry_at", "exit_at"))
            assert last_exit is None or entry_at >= last_exit
            last_exit = exit_at
            q, entry, exit_price, stop, target, raw_exit = (D(t[k]) for k in ("quantity", "entry", "exit", "stop", "target", "raw_exit"))
            first, last = market[entry_at], market[exit_at-timedelta(hours=1)]
            assert t["signal_id"] in signals
            signal = signals[t["signal_id"]]
            decision_at = datetime.fromisoformat(signal["known_at"])
            assert D(signal["reference"]) == market[decision_at-timedelta(hours=1)].close
            if entry_policy is None:
                assert decision_at == entry_at
            else:
                assert D(t["stop"]) == D(signal["stop"]) and D(t["target"]) == D(signal["target"])
                # Deliberately independent of PullbackEntry.limit and simulator.
                cap = D(signal["reference"]) - entry_policy.retracement_fraction * (D(signal["reference"])-D(signal["stop"]))
                assert decision_at <= entry_at < decision_at + timedelta(hours=entry_policy.max_wait_bars)
                assert entry <= cap
                at = decision_at
                while at < entry_at:
                    missed = market[at]
                    assert missed.open*(1+execution.impact) > cap
                    assert missed.low > stop and missed.high < target
                    at += timedelta(hours=1)
            assert entry == first.open*(1+execution.impact)
            assert exit_price == raw_exit*(1-execution.impact)
            assert D(t["entry_fee"]) == q*entry*execution.fee
            assert D(t["exit_fee"]) == q*exit_price*execution.fee
            assert q % execution.quantity_step == 0
            assert q*entry >= execution.minimum_notional
            assert q*entry <= D(t["equity_at_decision"])*execution.max_exposure
            assert int((exit_at-entry_at)/timedelta(hours=1)) == t["held_bars"]
            reason = t["exit_reason"]
            if reason == "gap_stop":
                assert last.open <= stop and raw_exit == last.open
            elif reason == "gap_target_no_favourable_improvement":
                assert last.open >= target and raw_exit == target
            elif reason == "stop":
                assert last.low <= stop < last.open < target and raw_exit == stop
                assert t["ambiguous"] == (last.high >= target)
            elif reason == "target":
                assert last.high >= target > last.open > stop and last.low > stop and raw_exit == target
            else:
                assert reason == "time_exit" and raw_exit == last.close and last.low > stop and last.high < target
            pnl = q*(exit_price-entry)-q*(entry+exit_price)*execution.fee
            error = abs(pnl-D(t["net_pnl"]))
            assert error < D("1e-20")
            assert abs(pnl/(q*(entry-stop))-D(t["net_r"])) < D("1e-20")
            max_error = max(max_error, error)
            cash += D(t["net_pnl"])
            gross_raw += q*(raw_exit-first.open)
            impact_cost += q*((entry-first.open)+(raw_exit-exit_price))
            fees += q*(entry+exit_price)*execution.fee
        for p in unresolved + ([state["position"]] if state["position"] else []):
            cash -= D(p["quantity"])*D(p["fill"])+D(p["entry_fee"])
        cash_error = abs(cash-D(state["cash"]))
        assert cash_error < D("1e-18")
        # All liquidation marks independently reconstructed, including entry fees.
        peak, drawdown, mark_error = execution.initial_capital, D(0), D(0)
        for m in sorted((r for r in records if r["kind"] == "equity_mark"), key=lambda r: r["known_at"]):
            if m["liquidation_equity"] is None:
                continue  # unresolved remains unscored, not a fabricated exit.
            at = datetime.fromisoformat(m["known_at"])
            closed = [t for t in trades if datetime.fromisoformat(t["exit_at"]) <= at]
            value = execution.initial_capital + sum((D(t["net_pnl"]) for t in closed), D(0))
            active = [t for t in trades if datetime.fromisoformat(t["entry_at"]) < at < datetime.fromisoformat(t["exit_at"])]
            for p in unresolved + ([state["position"]] if state["position"] else []):
                if datetime.fromisoformat(p["entry_at"]) < at:
                    active.append({"quantity": p["quantity"], "entry": p["fill"], "entry_fee": p["entry_fee"]})
            assert len(active) <= 1
            for t in active:
                q = D(t["quantity"])
                value += q*(market[at-timedelta(hours=1)].close*(1-execution.impact)*(1-execution.fee)-D(t["entry"]))-D(t["entry_fee"])
            error = abs(value-D(m["liquidation_equity"]))
            mark_error = max(mark_error, error)
            assert error < D("1e-18")
            peak = max(peak, value)
            drawdown = max(drawdown, (peak-value)/peak)
        if not unresolved:
            assert abs(drawdown-D(state["drawdown"])) < D("1e-20")
        return {"trades_checked": len(trades), "maximum_trade_error": str(max_error),
                "cash_error": str(cash_error), "maximum_mark_error": str(mark_error),
                "gross_raw_pnl": str(gross_raw), "spread_slippage_cost": str(impact_cost), "fees": str(fees),
                "unresolved_unscored": len(unresolved), "precision": 60}


def bootstrap_lower(values):
    """Exploratory circular-block5 mean, fixed seed; NOT untouched evidence."""
    if not values:
        return None
    values = [float(v) for v in values]
    rng = random.Random(173)
    means = []
    for _ in range(2000):
        sample = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start+i) % len(values)] for i in range(5))
        means.append(sum(sample[:len(values)])/len(values))
    return sorted(means)[5]  # floor(2000 * (0.05 / 20))


def verdict(rows):
    economic, insufficient = [], []
    for row in rows:
        r = row["result"]
        if r["period"] == "diagnostic_gap_2023":
            continue
        label = r["period"] + "/" + r["cost"]
        if (r["unresolved_gap_positions"] or r["net_return"] is None
                or D(r["net_return"]) <= 0 or (r["expectancy_r"] is not None and D(r["expectancy_r"]) <= 0)
                or D(r["maximum_close_mark_drawdown"] or 0) > D("0.2")
                or D(r["ambiguous_adverse_trades"])/max(1,r["closed_trades"]) > D("0.1")):
            economic.append(label)
        floor = 100 if r["period"] == "development" else 20
        if (r["closed_trades"] < floor or r["open_positions"] or r["pending_entries"]
                or r.get("bootstrap_lower_mean_r") is None or r["bootstrap_lower_mean_r"] <= 0):
            insufficient.append(label)
    expected = {(p[0], c) for p in PERIODS if p[0] != "diagnostic_gap_2023" for c in ("base", "stress")}
    if {(r["result"]["period"], r["result"]["cost"]) for r in rows if r["result"]["period"] != "diagnostic_gap_2023"} != expected:
        insufficient.append("incomplete_frozen_matrix")
    return {"decision": "REJECTED" if economic else "INSUFFICIENT EVIDENCE" if insufficient else "ELIGIBLE FOR FURTHER VALIDATION",
            "economic_failures": economic, "insufficient_evidence": insufficient}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="dedicated lab-only DB with cached verified2023–24 hourly candles")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exit-policy", choices=EXIT_POLICIES, default=DEFAULT_EXIT_POLICY)
    args = parser.parse_args()
    if sha256(args.spec.read_bytes()).hexdigest() != SPEC_SHA:
        raise ValueError("predeclared spec changed; new reviewed version required")
    args.output.mkdir(parents=True, exist_ok=True)
    store = Store(args.root)
    started = time.perf_counter()
    rows = []
    try:
        for period, start, end in PERIODS:
            candles, manifest = dataset(store, datetime.fromisoformat(start).replace(tzinfo=UTC), datetime.fromisoformat(end).replace(tzinfo=UTC))
            for name in NAMES:
                for cost in ("base", "stress"):
                    answer = run(store, candles, manifest, Hypothesis(name), cost, period, exit_policy=args.exit_policy)
                    rows.append(answer)
                    (args.output/"checkpoint.json").write_text(canonical({"spec": SPEC_SHA, "source": source_sha(), "rows": rows})+"\n")
                    print(canonical({"completed": len(rows), "hypothesis": name, "period": period, "cost": cost, "trades": answer["result"]["closed_trades"], "return": answer["result"]["net_return"]}), flush=True)
        # Reporting statistics do not mutate completed immutable experiment rows.
        for row in rows:
            r = row["result"]
            r["bootstrap_lower_mean_r"] = bootstrap_lower(r["net_r_values"]) if r["expectancy_r"] is not None and D(r["expectancy_r"]) > 0 else None
        report = {"spec_sha256": SPEC_SHA, "research_source_sha256": source_sha(), "engine_source_sha256": research_code_fingerprint(),
                  "rows": rows, "verdicts": {n: verdict([r for r in rows if r["result"]["hypothesis"] == n]) for n in NAMES},
                  "runtime_seconds": time.perf_counter()-started, "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if __import__('sys').platform == 'darwin' else 1024),
                  "sqlite_bytes": (store.root/"research.sqlite3").stat().st_size,
                  "integrity": store.db.execute("pragma integrity_check").fetchone()[0], "foreign_key_errors": len(store.db.execute("pragma foreign_key_check").fetchall()),
                  "limitations": "2024 already examined; exploratory reused validation; correlated trades/models/costs; block bootstrap not proof of independent edge; 2025sealed; no2026; coldstart121bars each wallet; no shorts/orderbook/historical fee-tier verification; gaps and open positions not fabricated as exits."}
        (args.output/"results.json").write_text(canonical(report)+"\n")
        print(canonical({k:v for k,v in report.items() if k not in {"rows"}}), flush=True)
    finally:
        store.close()


if __name__ == "__main__":
    main()
