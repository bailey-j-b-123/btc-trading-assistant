"""One bounded LONG 2% movement discovery experiment using the existing Strategy Lab.

Cached archives only. Learn from Apr-Sep2023; freeze before opening later data.
No new simulator, database schema, parameter campaign or extra dependency.
"""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import gzip
from hashlib import sha256
import json
from pathlib import Path
import random
import socket
import time
from urllib.parse import urlsplit

from trading_assistant.strategy_lab.alpha import safe_source
from trading_assistant.strategy_lab.data import acquire, dataset
from trading_assistant.strategy_lab.discovery import TRAIN_START, TRAIN_END, PERIODS
from trading_assistant.strategy_lab.observed_long2 import (
    discover_long2, load_opportunity_rule, VERSION,
)
from trading_assistant.strategy_lab.models import (
    Strategy,
    canonical,
    research_code_fingerprint,
)
from trading_assistant.strategy_lab.novel import costs, independent_audit, summarize
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store


def adjusted_block_lower(events, *, samples=20000, seed=173, family=176):
    marks = sorted(
        (r for r in events if r["kind"] == "equity_mark"), key=lambda r: r["known_at"]
    )
    if any(r["liquidation_equity"] is None for r in marks):
        return None
    days = {}
    last = D(10000)
    for r in marks:
        day = (
            (datetime.fromisoformat(r["known_at"]) - timedelta(hours=1))
            .date()
            .isoformat()
        )
        value = D(r["liquidation_equity"])
        delta, n = days.get(day, (D(0), 0))
        days[day] = (delta + value - last, n + 1)
        last = value
    values = [float(v / 10000) for v, n in days.values() if n == 24]
    if len(values) < 60:
        return None
    # Same seven-day circular blocks as existing Alpha, with the larger explicit
    # split-search+leaf family bound rather than treating selected leaves as iid.
    rng = random.Random(seed)
    means = []
    size = len(values)
    for _ in range(samples):
        total = 0.0
        remaining = size
        while remaining:
            start = rng.randrange(size)
            take = min(7, remaining)
            total += sum(values[(start + j) % size] for j in range(take))
            remaining -= take
        means.append(total / size)
    return sorted(means)[int(samples * 0.05 / family)]


def run(*, source, root, output):
    source, root, output = map(lambda p: Path(p).absolute(), (source, root, output))
    directory = safe_source(source)
    if root.exists() or output.exists() or root == output:
        raise ValueError("fresh separate lab and evidence paths required")
    for p in (root, output):
        if any(
            a.is_symlink() or a.name in {"data", "backups", ".env"}
            for a in (p, *p.parents)
        ):
            raise ValueError("unsafe research output")
    output.mkdir(parents=True)
    protocol = {
        "version": VERSION,
        "frozen_at": datetime.now(UTC),
        "source_sha256": research_code_fingerprint(),
        "discovery_start": TRAIN_START,
        "discovery_end_exclusive": TRAIN_END,
        "evaluation_periods": PERIODS,
        "max_leaf_candidates": 8,
        "maximum_allowed_candidates": 24,
        "max_depth": 3,
        "min_leaf": 120,
        "split_quantiles": [1, 2, 3],
        "label": "next-open +2 percent within 120h, without <=-2pct adverse excursion by first touch; all training-only",
        "admission": "leaf 2pct favorable opportunity rate > unconditional training rate",
        "costs": {c: asdict(costs(c)) for c in ("base", "stress")},
        "exit": "opt-in prior120h resistance target >=2 percent beyond stress worst accepted fill; prior24h-low ATR buffered stop <=2 percent; max120bars",
        "multiple_testing": {
            "split_bound": 168,
            "family": 176,
            "block_days": 7,
            "samples": 20000,
            "seed": 173,
        },
        "gate": "all4primaryperiods base/stress >=100closed,net>0,expectancy>0,adjustedblocklower>0; reused 2024 descriptive, not fresh validation",
        "caveat": "2023laterdata separate from this fit but previously researched;2024 reused, not untouched",
        "baselines": ["sma_cross10/30/2ATR/2R/48h", "donchian10/30/2ATR/2R/48h"],
    }
    (output / "protocol.json").write_text(canonical(protocol) + "\n")
    started = time.perf_counter()
    store = Store(root)
    read_log = []

    def cached(months):
        allowed = {
            f"BTCUSDT-1h-{month}.zip" + suffix
            for month in months
            for suffix in ("", ".CHECKSUM")
        }

        def read(url):
            name = Path(urlsplit(url).path).name
            if name not in allowed:
                raise ValueError("archive outside frozen phase")
            file = directory / name
            if file.is_symlink():
                raise ValueError("redirected archive")
            raw = file.read_bytes()
            read_log.append({"name": name, "sha256": sha256(raw).hexdigest()})
            return raw

        return acquire(store, months, download=read)

    try:
        # Evaluation archives are NOT opened until the learned specifications are frozen.
        training_months = [f"2023-{month:02}" for month in range(4, 10)]
        receipts = cached(training_months)
        training, manifest = dataset(store, TRAIN_START, TRAIN_END)
        (output / "training-manifest.json").write_text(canonical(manifest) + "\n")
        learned = discover_long2(training)
        (output / "rules.json").write_text(canonical(learned) + "\n")
        frozen_rule_sha = sha256((output / "rules.json").read_bytes()).hexdigest()
        discovery_reads = list(read_log)
        (output / "learning-isolation.json").write_text(
            canonical(
                {
                    "archives_read_before_rule_freeze": discovery_reads,
                    "rule_sha256_before_evaluation": frozen_rule_sha,
                    "last_training_label_available": TRAIN_END.isoformat(),
                    "no_evaluation_archives_opened": {
                        r["name"][11:18] for r in discovery_reads
                    }
                    == set(training_months),
                    "discovery_function": "pure tuple input, no store/file/network handles; application guard, not hostile-Python sandbox",
                }
            )
            + "\n"
        )
        # Explicitly prove every pre-freeze filename belongs to the six training months.
        assert {r["name"][11:18] for r in discovery_reads} == set(training_months)
        rules = [load_opportunity_rule(h["rule"]) for h in learned["hypotheses"] if h["accepted"]]
        assert len(rules) <= 8
        receipts += cached(
            [f"2023-{m:02}" for m in range(10, 13)]
            + [f"2024-{m:02}" for m in range(1, 13)]
        )
        (output / "archive-receipts.json").write_text(canonical(receipts) + "\n")
        named = [
            ("baseline_sma_cross", Strategy(family="sma_cross", fast=10, slow=30)),
            ("baseline_donchian", Strategy(family="donchian", fast=10, slow=30)),
        ]
        subjects = [(r.fingerprint, r) for r in rules] + named
        answers = []
        for period, first, last in PERIODS:
            cs, m = dataset(
                store,
                datetime.fromisoformat(first).replace(tzinfo=UTC),
                datetime.fromisoformat(last).replace(tzinfo=UTC),
            )
            assert all(c.timestamp.year in (2023, 2024) for c in cs)
            (output / (period + "-manifest.json")).write_text(canonical(m) + "\n")
            for name, rule in subjects:
                for cost in ("base", "stress"):
                    adapter = "observed-long2-v1" if name not in dict(named) else None
                    result = run_experiment(
                        store,
                        cs,
                        m,
                        rule,
                        costs(cost),
                        stage="walk_forward",
                        decision_adapter=adapter,
                    )
                    assert result["complete"] and not result["deduplicated"]
                    state = json.loads(
                        store.db.execute(
                            "select state from experiments where id=?", (result["id"],)
                        ).fetchone()[0]
                    )
                    events = store.records(result["id"])
                    label = f"{period}-{cost}-{name}"
                    with gzip.open(output / (label + "-events.jsonl.gz"), "wt") as f:
                        for event in events:
                            f.write(canonical(event) + "\n")
                    audit = independent_audit(state, events, cs, costs(cost))
                    lower = adjusted_block_lower(events)
                    record = dict(
                        label=label,
                        subject=name,
                        learned=adapter is not None,
                        period=period,
                        cost=cost,
                        strategy=asdict(rule),
                        execution=asdict(costs(cost)),
                        bars=len(cs),
                        state=state,
                        audit=audit,
                        summary=summarize(events),
                        adjusted_daily_block_lower=lower,
                        **result,
                    )
                    answers.append(record)
                    (output / "replays.json").write_text(canonical(answers) + "\n")
                    print(
                        canonical(
                            {
                                "label": label,
                                "closed": result["result"]["closed_trades"],
                                "return": result["result"]["net_return"],
                            }
                        ),
                        flush=True,
                    )
        assert (
            sha256((output / "rules.json").read_bytes()).hexdigest() == frozen_rule_sha
        )
        assert store.db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert not list(store.db.execute("pragma foreign_key_check"))
        receipt = dict(
            wallets=len(answers),
            admitted_rules=len(rules),
            generated=len(learned["hypotheses"]),
            steps=sum(r["bars"] for r in answers),
            elapsed_seconds=time.perf_counter() - started,
            rule_sha256_unchanged=frozen_rule_sha,
            source_sha256=research_code_fingerprint(),
            read_log=read_log,
            integrity="ok",
        )
        (output / "execution-receipt.json").write_text(canonical(receipt) + "\n")
        return receipt
    finally:
        store.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "root", "output"):
        p.add_argument("--" + name, required=True, type=Path)
    a = p.parse_args()

    def denied(*args, **kwargs):
        raise RuntimeError("network prohibited during discovery experiment")

    socket.create_connection = denied
    socket.socket.connect = denied
    print(canonical(run(source=a.source, root=a.root, output=a.output)), flush=True)


if __name__ == "__main__":
    main()
