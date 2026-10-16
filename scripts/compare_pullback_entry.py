"""Run one frozen research comparison using the existing simulator and cached archives.

No downloads, 2025 prices, account interface, parameter search or new simulator.
"""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal as D
import gzip
import json
from pathlib import Path
import socket
import time

from trading_assistant.strategy_lab import alpha, novel
from trading_assistant.strategy_lab.data import dataset
from trading_assistant.strategy_lab.entry_policy import PullbackEntry, entry_definition
from trading_assistant.strategy_lab.stop_quality import StopQuality, quality_definition
from trading_assistant.strategy_lab.models import canonical, research_code_fingerprint
from trading_assistant.strategy_lab.store import Store


def compare(*, source, root, output, stop_quality=None):
    source, root, output = map(lambda p: Path(p).absolute(), (source, root, output))
    # Strictly new output/storage: do not rewrite any completed research results.
    if root.exists() or output.exists() or root == output:
        raise ValueError("fresh separate research and output directories required")
    alpha.safe_source(source)
    for path in (root, output):
        for ancestor in (path, *path.parents):
            if ancestor.is_symlink() or ancestor.name in {"data", "backups", ".env"}:
                raise ValueError("unsafe or redirected output")
    output.mkdir(parents=True)
    periods = [p for p in novel.PERIODS if p[0] != "diagnostic_gap_2023"]
    policy = PullbackEntry()
    quality_spec = quality_definition(stop_quality)
    variants = [("original", None, None), (policy.version, policy, None)]
    if stop_quality is not None:
        variants = [
            ("original", None, None),
            (stop_quality.version, None, stop_quality),
        ]
    protocol = {
        "frozen_at": datetime.now(UTC),
        "source_sha256": research_code_fingerprint(),
        "strategy": asdict(novel.Hypothesis("breakout_retest")),
        "entry_policies": [entry_definition(v[1]) for v in variants],
        "stop_quality": quality_spec,
        "periods": periods,
        "costs": {c: asdict(novel.costs(c)) for c in ("base", "stress")},
        "exit_policy": "strategy_default",
        "wallets": 16,
        "validation": "2024 reused chronological validation, NOT untouched",
        "decision": "same causal existing breakout_retest, original levels and quantity algorithm",
        "pass_criteria": "default parity; causal cap fills; independent cash/fee/marks; recovery; "
        "no promotion absent positive base+stress expectancy in all four periods",
        "limits": "not full hierarchical BRAIN or earlier5m trigger; no tuning, holdout, orders or production",
    }
    (output / "protocol.json").write_text(canonical(protocol) + "\n")
    started = time.perf_counter()
    store = Store(root)
    answers = []
    try:
        receipts = alpha.import_archives(store, source)
        (output / "archive-receipts.json").write_text(canonical(receipts) + "\n")
        for period, first, last in periods:
            bars, manifest = dataset(
                store,
                datetime.fromisoformat(first).replace(tzinfo=UTC),
                datetime.fromisoformat(last).replace(tzinfo=UTC),
            )
            assert all(c.timestamp.year in (2023, 2024) for c in bars)
            (output / (period + "-manifest.json")).write_text(
                canonical(manifest) + "\n"
            )
            for cost in ("base", "stress"):
                for name, entry, quality in variants:
                    label = f"{period}-{cost}-{name}"
                    result = novel.run(
                        store,
                        bars,
                        manifest,
                        novel.Hypothesis("breakout_retest"),
                        cost,
                        period,
                        entry_policy=entry,
                        stop_quality=quality,
                    )
                    assert result["complete"] and not result["deduplicated"]
                    state = json.loads(
                        store.db.execute(
                            "select state from experiments where id=?", (result["id"],)
                        ).fetchone()[0]
                    )
                    events = store.records(result["id"])
                    with gzip.open(output / (label + "-events.jsonl.gz"), "wt") as f:
                        for event in events:
                            f.write(canonical(event) + "\n")
                    answers.append(
                        dict(
                            label=label,
                            period=period,
                            cost=cost,
                            entry_policy=entry_definition(entry),
                            stop_quality=quality_definition(quality),
                            bars=len(bars),
                            execution=asdict(novel.costs(cost)),
                            state=state,
                            **result,
                        )
                    )
                    (output / "replays.json").write_text(canonical(answers) + "\n")
                    print(
                        canonical(
                            {
                                "label": label,
                                "id": result["id"],
                                "fills": result["result"]["filled_trades"],
                                "net_return": result["result"]["net_return"],
                            }
                        ),
                        flush=True,
                    )
        assert store.db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert not list(store.db.execute("pragma foreign_key_check"))
    finally:
        store.close()
    receipt = {
        "wallets": len(answers),
        "processing_steps": sum(a["bars"] for a in answers),
        "elapsed_seconds": time.perf_counter() - started,
        "source_sha256": research_code_fingerprint(),
    }
    (output / "execution-receipt.json").write_text(canonical(receipt) + "\n")
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--source", required=True, type=Path, help="marked archive-only source lab"
    )
    p.add_argument(
        "--root", required=True, type=Path, help="NEW isolated lab directory"
    )
    p.add_argument(
        "--output", required=True, type=Path, help="NEW separate evidence directory"
    )
    p.add_argument(
        "--stop-quality-v1",
        action="store_true",
        help="separate frozen stop-quality comparison; original entries, no pullback",
    )
    a = p.parse_args()
    quality = (
        StopQuality(
            D("0.25"),
            D("1"),
            D("0.25"),
            D("1"),
            14,
        )
        if a.stop_quality_v1
        else None
    )

    def denied(*args, **kwargs):
        raise RuntimeError("network prohibited during historical comparison")

    socket.create_connection = denied
    socket.socket.connect = denied
    print(
        canonical(
            compare(source=a.source, root=a.root, output=a.output, stop_quality=quality)
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
