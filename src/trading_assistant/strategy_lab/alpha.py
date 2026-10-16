"""Project Alpha v1: two frozen, isolated, cash-Spot exploratory hypotheses.

No download or holdout entry point. Existing production and research rules
remain unchanged. See the committed protocol before interpreting results.
"""

from .entry_policy import entry_definition
from .stop_quality import quality_definition

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from functools import lru_cache
from hashlib import sha256
import argparse
import csv
import gzip
import json
from pathlib import Path
import random
import resource
import sys
import time

from trading_assistant.market_data.public_archive import decode_archive
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_structure.volatility import (
    VolatilityParameters,
    calculate_volatility,
)
from sqlalchemy import create_engine
from . import novel
from .data import dataset
from .models import canonical, decimal_context, identity, research_code_fingerprint
from .simulation import DEFAULT_EXIT_POLICY, EXIT_POLICIES, initial_state, metrics, step, validate_exit_policy
from .store import MARKER, Store, exclusive

SPEC_SHA = "7ea73beedfabe7a27104bff442ddce9fcc3bc2f41f057620b0581426d49fad8a"
NAMES = ("thin_volume_shock_reversal", "utc_open_close_continuation")
PERIODS = novel.PERIODS
HOUR = timedelta(hours=1)


@lru_cache(maxsize=1)
def source_sha():
    root = Path(__file__).parent
    files = [root / n for n in ("alpha.py", "novel.py", "data.py", "store.py")]
    files.append(root.parent / "market_data" / "public_archive.py")
    return sha256(
        b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files)
    ).hexdigest()


@dataclass(frozen=True)
class Hypothesis:
    name: str
    timeframe: str = "1h"
    version: str = "project-alpha-v1"

    def __post_init__(self):
        if (
            self.name not in NAMES
            or self.timeframe != "1h"
            or self.version != "project-alpha-v1"
        ):
            raise ValueError("only the two frozen hourly Alpha hypotheses")

    @property
    def warmup(self):
        return 169

    @property
    def max_hold(self):
        return 24 if self.name == NAMES[0] else 1

    @property
    def fingerprint(self):
        return identity(
            {
                "definition": asdict(self),
                "spec": SPEC_SHA,
                "source": source_sha(),
                "engine": research_code_fingerprint(),
            }
        )


def median(values):
    v = sorted(values)
    if not v:
        raise ValueError("empty median")
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


@decimal_context
def decide(candles, strategy, *, known_at):
    # Do not silently reorder or deduplicate. Reject contaminated input.
    w = tuple(candles)
    if (
        len(w) != 169
        or known_at.utcoffset() != timedelta(0)
        or w[-1].timestamp + HOUR != known_at
        or any(
            c.timeframe != "1h"
            or (c.exchange, c.symbol) != ("binance", "BTC/USDT")
            or c.timestamp + HOUR > known_at
            for c in w
        )
        or any(b.timestamp - a.timestamp != HOUR for a, b in zip(w, w[1:]))
    ):
        return None
    c = w[-1]
    if strategy.name == NAMES[1] and known_at.hour != 23:
        return None
    prior = w[:-6] if strategy.name == NAMES[0] else w[:-1]
    atr = calculate_volatility(
        prior,
        interval=HOUR,
        as_of=prior[-1].timestamp + HOUR,
        parameters=VolatilityParameters(period=14),
    ).atr
    if atr is None or atr <= 0:
        return None
    regime_mean = sum((x.close for x in w[:-1]), D(0)) / 168
    evidence = {
        "family": strategy.name,
        "atr": str(atr),
        "atr_last_close": (prior[-1].timestamp + HOUR).isoformat(),
        "window_start": w[0].timestamp.isoformat(),
        "closed_count": 169,
        "regime": "above_prior168_mean"
        if c.close > regime_mean
        else "at_or_below_prior168_mean",
    }
    if strategy.name == NAMES[0]:
        shock = w[-6:]
        anchor, trough = w[-7].close, min(x.close for x in shock)
        volume = sum((x.volume for x in shock), D(0)) / 6
        baseline_volume = median([x.volume for x in w[-54:-6]])
        if not (
            anchor - trough >= 3 * atr
            and volume < baseline_volume
            and c.close > w[-2].close
        ):
            return None
        low = min(x.low for x in shock)
        event = next(x.timestamp for x in shock if x.low == low).isoformat()
        stop = low - atr / 4
        evidence.update(
            anchor=str(anchor),
            trough_close=str(trough),
            shock_volume_mean=str(volume),
            baseline48_volume_median=str(baseline_volume),
            trough_open=event,
        )
    else:
        first = next(
            x
            for x in w
            if x.timestamp.date() == c.timestamp.date() and x.timestamp.hour == 0
        )
        if not (first.close > first.open and c.close >= first.close):
            return None
        stop = c.close - 2 * atr
        event = c.timestamp.date().isoformat()
        evidence.update(
            first_open=first.timestamp.isoformat(),
            first_price=str(first.open),
            first_close=str(first.close),
            utc_entry_hour=23,
        )
    target = c.close + 2 * (c.close - stop)
    if not 0 < stop < c.close < target:
        return None
    return {
        "known_at": known_at.isoformat(),
        "candle_open": c.timestamp.isoformat(),
        "reference": str(c.close),
        "stop": str(stop),
        "target": str(target),
        "source_plan_id": identity(
            {
                "hypothesis": strategy.fingerprint,
                "instrument": "binance/BTCUSDT/1h",
                "event": event,
            }
        ),
        "evidence": evidence,
    }


def safe_source(root):
    p = Path(root).absolute()
    if any(
        x.is_symlink() or x.name in {"data", "backups", ".env"} for x in (p, *p.parents)
    ):
        raise ValueError("ordinary marked research archives required")
    if (p / "LAB-ONLY.txt").read_text() != MARKER or (p / "archives").is_symlink():
        raise ValueError("not a marked archive source")
    return p / "archives"


@exclusive
def import_archives(store, source):
    """Reverify only exact allowed archive names; never open source SQLite."""
    directory = safe_source(source)
    if directory.parent.resolve() == store.root.resolve():
        raise ValueError("new destination required; source is read-only")
    engine = create_engine("sqlite:///" + str(store.root / "research.sqlite3"))
    OHLCVCandleRecord.__table__.create(engine, checkfirst=True)
    repo = CandleRepository(engine)
    receipts = []
    try:
        for year in (2023, 2024):
            for month in range(1, 13):
                label = f"{year}-{month:02}"
                name = f"BTCUSDT-1h-{label}.zip"
                archive, checksum_file = (
                    directory / name,
                    directory / (name + ".CHECKSUM"),
                )
                if archive.is_symlink() or checksum_file.is_symlink():
                    raise ValueError("redirected archive")
                raw, checksum = archive.read_bytes(), checksum_file.read_text()
                quality = []
                candles = decode_archive(
                    raw,
                    checksum=checksum,
                    month=label,
                    timeframe="1h",
                    allow_gaps=True,
                    quality_issues=quality,
                )
                inserted, repeated = repo.insert_unchanged_or_new(candles)
                receipt = {
                    "source_url": "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/"
                    + name,
                    "month": label,
                    "timeframe": "1h",
                    "exchange": "binance",
                    "symbol": "BTC/USDT",
                    "sha256": sha256(raw).hexdigest(),
                    "checksum_sha256": sha256(checksum_file.read_bytes()).hexdigest(),
                    "candles": len(candles),
                    "quality_issues": quality,
                    "declared_missing_candles": sum(
                        int((b.timestamp - a.timestamp) / HOUR) - 1
                        for a, b in zip(candles, candles[1:])
                    ),
                }
                store.db.execute(
                    "INSERT OR IGNORE INTO datasets VALUES(?,?)",
                    (identity(receipt), canonical(receipt)),
                )
                receipts.append(
                    {**receipt, "inserted": inserted, "already_present": repeated}
                )
    finally:
        engine.dispose()
    return receipts


@exclusive
@decimal_context
def run(
    store, candles, manifest, strategy, cost, period, *, max_boundaries=None, fault=None,
    exit_policy=DEFAULT_EXIT_POLICY, entry_policy=None, stop_quality=None,
):
    if max_boundaries is not None and (
        type(max_boundaries) is not int or max_boundaries < 1
    ):
        raise ValueError("positive bounded work limit required")
    validate_exit_policy(exit_policy)
    entry_spec = entry_definition(entry_policy)
    quality_spec = quality_definition(stop_quality)
    novel.validate_source(candles, manifest, period)  # exact fixed periods, no holdout
    execution = novel.costs(cost)
    definition = {
        "hypothesis": asdict(strategy),
        "fingerprint": strategy.fingerprint,
        "dataset": manifest,
        "execution": asdict(execution),
        "exit_policy": exit_policy,
        "cost": cost,
        "period": period,
        "spec": SPEC_SHA,
        "alpha_source": source_sha(),
        "engine_source": research_code_fingerprint(),
    }
    if entry_spec is not None:
        definition["entry_policy"] = entry_spec
    if quality_spec is not None:
        definition["stop_quality"] = quality_spec
    key, row = store.experiment(definition, initial_state(execution))
    if row["result"] is not None:
        return {
            "id": key,
            "complete": True,
            "deduplicated": True,
            "result": json.loads(row["result"]),
        }
    state, batch, worked = json.loads(row["state"]), [], 0
    for i in range(state["index"] + 1, len(candles)):
        if max_boundaries is not None and worked >= max_boundaries:
            break
        state, events = step(
            state, candles, i, strategy, execution, decision_function=decide, exit_policy=exit_policy, entry_policy=entry_policy, stop_quality=stop_quality
        )
        batch.extend(events)
        worked += 1
        if worked % 128 == 0:
            store.checkpoint(key, state, batch, fault=fault)
            batch = []
    store.checkpoint(key, state, batch, fault=fault)
    if state["index"] != len(candles) - 1:
        return {"id": key, "complete": False, "next_index": state["index"] + 1}
    records = store.records(key)
    result = metrics(state, records, execution, entry_policy=entry_policy)
    result.update(
        hypothesis=strategy.name,
        period=period,
        cost=cost,
        audit=novel.independent_audit(state, records, candles, execution, exit_policy=exit_policy, entry_policy=entry_policy),
        summary=novel.summarize(records),
        details=diagnostics(records, candles, execution),
    )
    store.checkpoint(key, state, [], result=result)
    return {"id": key, "complete": True, "deduplicated": False, "result": result}


def block_lower(daily, *, seed=173, samples=2000):
    """Fixed seven-calendar-day blocks, cash days included; no iid trade CI."""
    if len(daily) < 60:
        return None
    values = [float(v) for v in daily]
    rng, means = random.Random(seed), []
    for _ in range(samples):
        sample = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start + j) % len(values)] for j in range(7))
        means.append(sum(sample[: len(values)]) / len(values))
    return sorted(means)[int(samples * 0.05 / len(NAMES))]


@decimal_context
def diagnostics(records, candles, execution):
    trades = sorted(
        (r for r in records if r["kind"] == "trade"), key=lambda r: r["exit_at"]
    )
    groups = {}
    streak = worst = 0
    for t in trades:
        pnl = D(t["net_pnl"])
        streak = streak + 1 if pnl < 0 else 0
        worst = max(streak, worst)
        for label in (t["entry_at"][:7], "regime:" + t["regime_at_decision"]):
            g = groups.setdefault(label, {"closed": 0, "net_pnl": D(0)})
            g["closed"] += 1
            g["net_pnl"] += pnl
    gross = sum(
        (
            D(t["quantity"])
            * (
                D(t["raw_exit"])
                - next(
                    c.open for c in candles if c.timestamp.isoformat() == t["entry_at"]
                )
            )
            for t in trades
        ),
        D(0),
    )
    fees = sum((D(t["entry_fee"]) + D(t["exit_fee"]) for t in trades), D(0))
    fillgross = sum(
        (D(t["quantity"]) * (D(t["exit"]) - D(t["entry"])) for t in trades), D(0)
    )
    net = sum((D(t["net_pnl"]) for t in trades), D(0))
    impact = gross - fillgross
    residual = gross - impact - fees - net
    assert abs(residual) < D("1e-18")
    turnover = sum(
        (
            D(t["quantity"])
            * (
                D(t["raw_exit"])
                + next(
                    c.open for c in candles if c.timestamp.isoformat() == t["entry_at"]
                )
            )
            / 2
            for t in trades
        ),
        D(0),
    )
    top = sorted((max(D(0), D(t["net_pnl"])) for t in trades), reverse=True)[:2]
    marks = sorted(
        (r for r in records if r["kind"] == "equity_mark"), key=lambda r: r["known_at"]
    )
    byday = {}
    previous = execution.initial_capital
    valid = True
    for m in marks:
        at = datetime.fromisoformat(m["known_at"])
        day = (at - HOUR).date().isoformat()
        d = byday.setdefault(day, {"changes": D(0), "hours": 0})
        if m["liquidation_equity"] is None:
            valid = False
            continue
        value = D(m["liquidation_equity"])
        d["changes"] += value - previous
        d["hours"] += 1
        previous = value
    complete_days = [g["changes"] for g in byday.values() if g["hours"] == 24]
    # An interval gap or active terminal portfolio makes inference incomplete.
    contiguous = all(
        b.timestamp - a.timestamp == HOUR for a, b in zip(candles, candles[1:])
    )
    terminal_clean = (
        not any(r["kind"] == "unresolved" for r in records)
        and not marks[-1]["occupied"]
    )
    lower = (
        block_lower(complete_days) if valid and contiguous and terminal_clean else None
    )
    first, last = candles[0].open, candles[-1].close
    bench = {}
    for fraction in (D("0.25"), D(1)):
        cash = execution.initial_capital
        q = (
            (cash * fraction / (first * (1 + execution.impact) * (1 + execution.fee)))
            / execution.quantity_step
        ).to_integral_value(rounding="ROUND_FLOOR") * execution.quantity_step
        equity = (
            cash
            - q * first * (1 + execution.impact) * (1 + execution.fee)
            + q * last * (1 - execution.impact) * (1 - execution.fee)
        )
        peak = execution.initial_capital
        dd = D(0)
        debit = q * first * (1 + execution.impact) * (1 + execution.fee)
        for c in candles:
            v = (
                cash
                - debit
                + q * c.close * (1 - execution.impact) * (1 - execution.fee)
            )
            peak = max(peak, v)
            dd = max(dd, (peak - v) / peak)
        bench[str(fraction)] = {
            "net_return": str(equity / cash - 1),
            "close_mark_drawdown": str(dd),
            "quantity": str(q),
            "gap_caveat": not contiguous,
        }
    months = [
        g["net_pnl"] for label, g in groups.items() if not label.startswith("regime:")
    ]
    observed_months = sorted({c.timestamp.strftime("%Y-%m") for c in candles})
    return json.loads(
        canonical(
            {
                "groups": groups,
                "worst_losing_streak": worst,
                "gross_raw_pnl": gross,
                "spread_slippage": impact,
                "fees": fees,
                "realized_net_pnl": net,
                "money_residual": residual,
                "cost_fraction_of_positive_gross": (impact + fees) / gross
                if gross > 0
                else None,
                "break_even_round_trip_bps": gross / turnover * 10000
                if turnover
                else None,
                "net_without_top2_wins": net - sum(top, D(0)),
                "positive_months": sum(v > 0 for v in months),
                "observed_months": len(observed_months),
                "daily_changes": [str(x) for x in complete_days],
                "block7_lower_daily_usdt": lower,
                "benchmark": bench,
                "cash_return": "0",
            }
        )
    )


def verdict(rows):
    eligible = [
        x["result"] for x in rows if x["result"]["period"] != "diagnostic_gap_2023"
    ]
    expected = {
        (p[0], c)
        for p in PERIODS
        if p[0] != "diagnostic_gap_2023"
        for c in ("base", "stress")
    }
    if {(r["period"], r["cost"]) for r in eligible} != expected:
        return {"decision": "B", "reasons": ["incomplete campaign"]}
    bad = []
    weak = []
    for r in eligible:
        label = r["period"] + "/" + r["cost"]
        d = r["details"]
        if (
            r["unresolved_gap_positions"]
            or r["net_return"] is None
            or D(r["net_return"]) <= 0
            or r["expectancy_r"] is None
            or D(r["expectancy_r"]) <= 0
            or D(r["maximum_close_mark_drawdown"] or 0) > D(".2")
        ):
            bad.append(label + ": nonpositive economics, unresolved, or drawdown")
        if (
            r["open_positions"]
            or r["pending_entries"]
            or D(r["ambiguous_adverse_trades"]) / max(1, r["closed_trades"]) > D(".1")
            or (r["period"] == "development" and r["closed_trades"] < 100)
            or (r["period"] != "development" and r["closed_trades"] < 20)
            or d["block7_lower_daily_usdt"] is None
            or d["block7_lower_daily_usdt"] <= 0
            or D(d["net_without_top2_wins"]) <= 0
            or d["positive_months"] * 2 <= d["observed_months"]
        ):
            weak.append(label + ": sample/uncertainty/concentration/incomplete")
    for cost in ("base", "stress"):
        if (
            sum(
                r["closed_trades"]
                for r in eligible
                if r["cost"] == cost and r["period"] != "development"
            )
            < 100
        ):
            weak.append(cost + ": fewer than100 nonoverlapping2024 trades")
    return {
        "decision": "C" if bad else "B" if weak else "A",
        "economic_failures": bad,
        "uncertainty": weak,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-jobs", type=int, default=20)
    p.add_argument("--exit-policy", choices=EXIT_POLICIES, default=DEFAULT_EXIT_POLICY)
    a = p.parse_args()
    if not 1 <= a.max_jobs <= 20 or sha256(a.spec.read_bytes()).hexdigest() != SPEC_SHA:
        raise ValueError("frozen specification and bounded1..20 jobs required")
    safe_source(a.source)
    if a.root.resolve() == a.source.resolve():
        raise ValueError("source must remain read-only")
    store = Store(a.root)
    a.output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    rows = []
    new = 0
    try:
        receipts = import_archives(store, a.source)
        for period, begin, end in PERIODS:
            candles, manifest = dataset(
                store,
                datetime.fromisoformat(begin).replace(tzinfo=UTC),
                datetime.fromisoformat(end).replace(tzinfo=UTC),
            )
            for name in NAMES:
                for cost in ("base", "stress"):
                    answer = run(
                        store, candles, manifest, Hypothesis(name), cost, period, exit_policy=a.exit_policy
                    )
                    rows.append(answer)
                    new += not answer["deduplicated"]
                    if not answer["deduplicated"]:
                        with gzip.open(
                            a.output / (answer["id"] + ".jsonl.gz"), "wt"
                        ) as f:
                            for record in store.records(answer["id"]):
                                f.write(canonical(record) + "\n")
                    (a.output / "checkpoint.json").write_text(canonical(rows) + "\n")
                    print(
                        canonical(
                            {
                                "jobs": len(rows),
                                "hypothesis": name,
                                "period": period,
                                "cost": cost,
                                "trades": answer["result"]["closed_trades"],
                                "return": answer["result"]["net_return"],
                            }
                        ),
                        flush=True,
                    )
                    if new >= a.max_jobs:
                        break
                if new >= a.max_jobs:
                    break
            if new >= a.max_jobs:
                break
        result = {
            "spec": SPEC_SHA,
            "source": source_sha(),
            "engine": research_code_fingerprint(),
            "rows": rows,
            "verdicts": {
                name: verdict([x for x in rows if x["result"]["hypothesis"] == name])
                for name in NAMES
            },
            "data_receipts": receipts,
            "new_jobs": new,
            "seconds": time.perf_counter() - start,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "sqlite_bytes": (store.root / "research.sqlite3").stat().st_size,
            "integrity": store.db.execute("pragma integrity_check").fetchone()[0],
            "foreign_keys": store.db.execute("pragma foreign_key_check").fetchall(),
        }
        (a.output / "results.json").write_text(canonical(result) + "\n")
        print(
            canonical(
                {k: v for k, v in result.items() if k not in {"rows", "data_receipts"}}
            ),
            flush=True,
        )
        trades = []
        for answer in rows:
            for t in store.records(answer["id"], "trade"):
                trades.append(
                    {
                        "experiment": answer["id"],
                        "hypothesis": answer["result"]["hypothesis"],
                        "period": answer["result"]["period"],
                        "cost": answer["result"]["cost"],
                        **t,
                    }
                )
        if trades:
            with (a.output / "trades.csv").open("w") as f:
                writer = csv.DictWriter(f, fieldnames=list(trades[0]))
                writer.writeheader()
                writer.writerows(trades)
    finally:
        store.close()


if __name__ == "__main__":
    main()
