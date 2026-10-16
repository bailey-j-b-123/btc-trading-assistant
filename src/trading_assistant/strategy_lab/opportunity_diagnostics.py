"""Read recorded baseline gates; no simulation, decisions or performance claims."""

from collections import Counter
from datetime import datetime
from hashlib import sha256
import argparse
import json

from .holdout import require_period
from .logic_audit import readonly_lab
from .models import canonical, identity

VERSION = "selected-setup-diagnostic-v1"
STAGES = (
    "qualified",
    "aligned",
    "confirming",
    "triggered",
    "plannable",
    "actionable_plan",
)
TIMES = {
    "decision_time",
    "created_at",
    "ended_at",
    "observed_at",
    "confirmed_at",
    "as_of",
    "setup_as_of",
    "setup_created_at",
    "boundary_close",
    "armed_at",
    "trigger_at",
}


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("explicit UTC diagnostic timestamps required")
    return result


def check_causality(value, at):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in TIMES and child is not None and instant(child) > at:
                raise ValueError("recorded evidence contains a future timestamp")
            check_causality(child, at)
    elif isinstance(value, list):
        for child in value:
            check_causality(child, at)


def summarize_observations(rows):
    """Rows are (boundary, exact stored payload JSON), ordered chronologically.

    Coverage is selected setups only: candidate_count is not an opportunity list.
    Stage flags describe independent observed facts, not a sequential causal funnel.
    """
    seen, opportunities, plans, baseline_plans = {}, {}, {}, set()
    decisions, reasons = Counter(), Counter()
    counts = Counter()
    digest = sha256()
    previous = first = None
    for boundary, raw in rows:
        at = instant(boundary)
        payload = json.loads(raw)
        normalized = canonical(payload)
        digest.update((boundary + "\0" + raw + "\n").encode())
        if at in seen:
            if seen[at] != normalized:
                raise ValueError("conflicting duplicate boundary")
            counts["identical_duplicates_ignored"] += 1
            continue
        if previous is not None and at < previous:
            raise ValueError("diagnostic observations must be chronological")
        previous = at
        first = first or at
        seen[at] = normalized
        snapshot = payload["snapshot"]
        if instant(snapshot["decision_time"]) != at:
            raise ValueError("snapshot decision and recorded boundary disagree")
        check_causality(snapshot, at)
        plan = payload.get("plan")
        if plan:
            check_causality(plan, at)
        counts["boundaries"] += 1
        decisions[snapshot["decision"]] += 1
        reasons.update(set(snapshot.get("reasons", [])))
        setup = snapshot.get("setup", {})
        if not setup.get("setup_id"):
            counts["boundaries_without_selected_setup"] += 1
            continue
        created = setup.get("created_at")
        key = identity(
            [
                snapshot.get("exchange"),
                snapshot.get("symbol"),
                setup.get("timeframe"),
                setup["setup_id"],
                instant(created).isoformat() if created else None,
                setup.get("config_fingerprint"),
            ]
        )
        if key not in opportunities:
            opportunities[key] = {
                "id": key,
                "setup_id": setup["setup_id"],
                "timeframe": setup.get("timeframe"),
                "exchange": snapshot.get("exchange"),
                "symbol": snapshot.get("symbol"),
                "config_fingerprint": setup.get("config_fingerprint"),
                "reference_id": setup.get("reference_id"),
                "created_at": created,
                "direction": setup.get("direction"),
                "family": setup.get("family"),
                "first_observed": boundary,
                "last_observed": boundary,
                "created_before_cohort": instant(created) < first if created else None,
                "observations": 0,
                "first_stage_observed": {s: None for s in STAGES},
                "baseline_decisions": Counter(),
                "recorded_reasons": Counter(),
                "plan_ids": set(),
                "gate_details": {
                    k: Counter()
                    for k in (
                        "alignment",
                        "confirmation_state",
                        "execution_state",
                        "pending_qualification_rule",
                        "opposing_qualification_rule",
                        "planning_reason",
                    )
                },
            }
        item = opportunities[key]
        if item["reference_id"] != setup.get("reference_id"):
            raise ValueError("same setup identity unexpectedly changed reference")
        item["last_observed"] = boundary
        item["observations"] += 1
        item["baseline_decisions"][snapshot["decision"]] += 1
        item["recorded_reasons"].update(set(snapshot.get("reasons", [])))
        for field, values in {
            "alignment": [snapshot.get("alignment", "unknown")],
            "confirmation_state": [
                snapshot.get("confirmation", {}).get("state", "unknown")
            ],
            "execution_state": [snapshot.get("execution", {}).get("state", "unknown")],
            "pending_qualification_rule": set(setup.get("pending_rules", [])),
            "opposing_qualification_rule": set(setup.get("opposing_rules", [])),
            "planning_reason": set(plan.get("reasons", [])) if plan else [],
        }.items():
            item["gate_details"][field].update(values)
        flags = {
            "qualified": setup.get("state") == "QUALIFIED",
            "aligned": snapshot.get("alignment") == "aligned",
            "confirming": snapshot.get("confirmation", {}).get("state") == "confirming",
            "triggered": snapshot.get("execution", {}).get("state") == "triggered",
            "plannable": snapshot["decision"] == "plannable",
            "actionable_plan": bool(
                plan
                and plan.get("state") == "PLANNABLE"
                and snapshot["decision"] == "plannable"
            ),
        }
        for stage, value in flags.items():
            if value and item["first_stage_observed"][stage] is None:
                item["first_stage_observed"][stage] = boundary
        if plan:
            if plan.get("setup_id") != setup["setup_id"]:
                raise ValueError("plan references a different selected setup")
            if plan["id"] in plans and plans[plan["id"]] != plan:
                raise ValueError("same plan ID has conflicting recorded content")
            plans[plan["id"]] = plan
            if snapshot["decision"] == "plannable":
                baseline_plans.add(plan["id"])
            item["plan_ids"].add(plan["id"])
    if first is None:
        raise ValueError("no recorded observations")
    for item in opportunities.values():
        for field in ("baseline_decisions", "recorded_reasons"):
            item[field] = dict(sorted(item[field].items()))
        item["plan_ids"] = sorted(item["plan_ids"])
        item["gate_details"] = {
            k: dict(sorted(v.items())) for k, v in item["gate_details"].items()
        }
    return {
        "version": VERSION,
        "source_observations_sha256": digest.hexdigest(),
        "counts": dict(counts),
        "unique_selected_setups": len(opportunities),
        "unique_recorded_plans": len(plans),
        "unique_baseline_observed_plans": len(baseline_plans),
        "baseline_observed_plan_ids": sorted(baseline_plans),
        "unique_setups_reaching_stage": {
            s: sum(
                i["first_stage_observed"][s] is not None for i in opportunities.values()
            )
            for s in STAGES
        },
        "baseline_decisions": dict(sorted(decisions.items())),
        "recorded_reasons": dict(sorted(reasons.items())),
        "opportunities": [opportunities[k] for k in sorted(opportunities)],
        "plans": [plans[k] for k in sorted(plans)],
        "coverage": {
            "recorded_plan_catalog": "union of recorded A1/A2/A3 planning requests; baseline_observed_plan_ids explicitly identifies strict baseline observations",
            "unselected_candidates": "not recorded; candidate_count is not a deduplicated catalog",
            "stage_times": "first observed in this cohort, not necessarily first ever; stages are independent flags",
            "retest_contact": None,
            "counterfactual_fills": None,
            "financial_outcomes": "not recomputed; use independent ledger audits",
        },
        "profitability_claim": False,
        "new_replays": 0,
        "downloads": 0,
    }


def summarize_run(root, run):
    """Read only an explicitly selected completed marked-lab cohort."""
    db = readonly_lab(root)
    try:
        row = db.execute(
            "SELECT definition,result FROM brain_runs WHERE id=?", (run,)
        ).fetchone()
        if row is None or row["result"] is None:
            raise ValueError(
                "one explicitly selected completed BRAIN research run required"
            )
        definition = json.loads(row["definition"])
        require_period(
            None, instant(definition["start"]), instant(definition["end"]), "5m"
        )
        answer = summarize_observations(
            db.execute(
                "SELECT boundary,payload FROM brain_observations WHERE run=? ORDER BY boundary",
                (run,),
            )
        )
        return {"source_run": run, "source_definition": definition, **answer}
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    print(canonical(summarize_run(args.source, args.run)))


if __name__ == "__main__":
    main()
