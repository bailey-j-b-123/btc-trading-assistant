"""Honest historical/forward reports. Missing evidence remains null."""

from collections import defaultdict
from decimal import Decimal as D
import json
from .paper import execution_from_json
from .simulation import metrics


def grouped_trades(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {
        label: {
            "closed_trades": len(rows),
            "net_pnl": str(sum(D(t["net_pnl"]) for t in rows)),
            "expectancy_r": str(sum(D(t["net_r"]) for t in rows) / len(rows)),
        }
        for label, rows in sorted(groups.items())
    }


def report(store):
    store.db.execute("BEGIN")
    try:
        return _report(store)
    finally:
        store.db.execute("COMMIT")


def _report(store):
    tables = {
        r[0]
        for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    historical = []
    for row in store.db.execute(
        "SELECT * FROM experiments WHERE result IS NOT NULL ORDER BY updated"
    ):
        definition = json.loads(row["definition"])
        trades = store.records(row["id"], "trade")
        historical.append(
            {
                "experiment": row["id"],
                "strategy": definition["strategy"],
                "stage": definition["stage"],
                "dataset_fingerprint": definition["dataset_fingerprint"],
                "result": json.loads(row["result"]),
                "by_regime": grouped_trades(
                    trades, lambda t: t.get("regime_at_decision", "unknown")
                ),
                "by_month": grouped_trades(trades, lambda t: t["entry_at"][:7]),
            }
        )
    forward = []
    query = (
        "SELECT p.*,s.state FROM promotions p LEFT JOIN paper_state s ON p.candidate=s.candidate ORDER BY p.candidate"
        if "paper_state" in tables
        else "SELECT p.*,NULL AS state FROM promotions p ORDER BY p.candidate"
    )
    for row in store.db.execute(query) if "promotions" in tables else []:
        definition = json.loads(row["definition"])
        events = [
            json.loads(r[0])
            for r in (
                store.db.execute(
                    "SELECT payload FROM paper_events WHERE candidate=?",
                    (row["candidate"],),
                )
                if "paper_events" in tables
                else []
            )
        ]
        measured = (
            metrics(
                json.loads(row["state"]),
                events,
                execution_from_json(definition["execution"]),
            )
            if row["state"]
            else None
        )
        forward.append(
            {
                "candidate": row["candidate"],
                "promoted_at": row["promoted_at"],
                "historical_evidence": json.loads(row["evidence"]),
                "forward": measured,
                "genuine_observed_decisions": sum(
                    e["kind"] == "signal" for e in events
                ),
                "missed_decisions": sum(e["kind"] == "missed_decision" for e in events),
                "comparison_limit": "historical next-open fills differ from forward observation-time delayed fills; independent periods and denominators, not paired profitability proof",
            }
        )
    receipts = (
        [
            json.loads(r[0])
            for r in store.db.execute(
                "SELECT payload FROM public_feed_receipts ORDER BY id"
            )
        ]
        if "public_feed_receipts" in tables
        else []
    )
    return {
        "paper_only": True,
        "status": store.status(),
        "historical": historical,
        "brain_policy_comparisons": [
            {
                "id": r["id"],
                "definition": json.loads(r["definition"]),
                "processed_boundaries": json.loads(r["state"])["next"],
                "counts": json.loads(r["state"])["counts"],
                "result": json.loads(r["result"]) if r["result"] else None,
            }
            for r in store.db.execute("SELECT * FROM brain_runs")
        ]
        if "brain_runs" in tables
        else [],
        "forward_candidates": forward,
        "genuine_public_feed_observations": len(receipts),
        "latest_public_observed_at": max(
            (r["observed_at"] for r in receipts), default=None
        ),
        "profitability_claim": False,
        "unimplemented_execution": [
            "shorting",
            "limit_orders",
            "derivatives",
            "real_orders",
        ],
    }
