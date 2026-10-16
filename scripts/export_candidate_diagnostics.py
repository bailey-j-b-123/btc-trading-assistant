"""Read-only export of a versioned research candidate sidecar, not a backtest.

Usage: python scripts/export_candidate_diagnostics.py --lab PATH --diagnostic ID --output NEW_DIRECTORY
No price-table access, source writes, network or trading imports.
"""

from argparse import ArgumentParser
from collections import Counter
from datetime import datetime
from decimal import Decimal as D
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def export(root, diagnostic, output):
    root, output = Path(root).absolute(), Path(output).absolute()
    if any(
        p.is_symlink() or p.name in {"data", "backups", ".env"}
        for p in (root, *root.parents)
    ):
        raise ValueError("ordinary marked research source required")
    if (root / "LAB-ONLY.txt").read_text() != "BRAIN-STRATEGY-LAB-ONLY-v1\n":
        raise ValueError("not a marked research source")
    database = root / "research.sqlite3"
    if (
        database.is_symlink()
        or output.exists()
        or output == root
        or output.is_relative_to(root)
    ):
        raise ValueError("fresh separate export directory required")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    header = db.execute(
        "SELECT * FROM candidate_diagnostic_runs WHERE id=?", (diagnostic,)
    ).fetchone()
    if not header:
        raise ValueError("unknown diagnostic identity")
    definition = json.loads(header["definition"])
    facts = {
        r["id"]: (r["kind"], json.loads(r["payload"]))
        for r in db.execute(
            "SELECT * FROM candidate_diagnostic_facts WHERE diagnostic=? ORDER BY id",
            (diagnostic,),
        )
    }
    observations = [
        json.loads(r["payload"])
        for r in db.execute(
            "SELECT payload FROM candidate_diagnostic_observations WHERE diagnostic=? ORDER BY boundary,setup",
            (diagnostic,),
        )
    ]
    coverage = [
        json.loads(r["payload"])
        for r in db.execute(
            "SELECT payload FROM candidate_diagnostic_coverage WHERE diagnostic=? ORDER BY boundary",
            (diagnostic,),
        )
    ]
    brain = db.execute(
        "SELECT state,result FROM brain_runs WHERE id=?", (header["brain_run"],)
    ).fetchone()
    db.rollback()
    db.close()
    candidates, retests, events = {}, {}, []
    start = instant(definition["source"]["start"])
    for key, (kind, payload) in facts.items():
        if kind == "actual_execution_event":
            events.append(payload)
            continue
        setup = payload["setup"]
        sid = setup["id"]
        c = candidates.setdefault(
            sid,
            dict(
                setup_id=sid,
                timeframe=payload["instrument"][2],
                detection=setup["created_at"],
                family=setup["family"],
                direction=setup["direction"],
                seed_event_id=setup["seed_event_id"],
                reference_id=setup["reference_id"],
                left_censored=instant(setup["created_at"]) < start,
                basis_ids=[],
                observations=[],
                failed_rules=set(),
                pending_rules=set(),
                terminal_reasons=set(),
                first_qualification=None,
                first_confirmation=None,
                first_armed=None,
                first_trigger=None,
                first_qualification_price=None,
                first_confirmation_price=None,
                first_armed_price=None,
                first_trigger_price=None,
                first_hierarchy_plannable=None,
                first_actual_fill=None,
                first_actual_fill_price=None,
                selected=False,
                plannable=False,
                qualified=False,
                counter_trend=False,
                alignment_blocked=False,
                cap_only_otherwise_hierarchy_ready=False,
                confirmation_blocked=False,
                execution_blocked=False,
                qualified_not_selected=False,
                cap_gross_exclusion=False,
                cap_base_net_exclusion=False,
                cap_stress_net_exclusion=False,
                narrow_stop_below_atr=False,
                stop_cost_exceeds_gross_risk=False,
                actual_fills=0,
                actual_slot_rejections=0,
                unsupported_short_plan=False,
                filled_signal_ids=set(),
                first_plan=None,
            ),
        )
        c["basis_ids"].append(key)
        qualified = setup["state"] == "QUALIFIED"
        c["qualified"] |= qualified
        if qualified and (
            c["first_qualification"] is None
            or instant(payload["captured_at"]) < instant(c["first_qualification"])
        ):
            c["first_qualification"] = payload["captured_at"]
            c["first_qualification_price"] = payload["target_diagnostics"]["entry"]
        c["failed_rules"].update(
            r["rule_id"]
            for r in setup["rules"]
            if r["outcome"] == "failed" or r["veto"]
        )
        c["pending_rules"].update(
            r["rule_id"]
            for r in setup["rules"]
            if r["outcome"] == "pending" and r["required"]
        )
        if setup["terminal_reason"]:
            c["terminal_reasons"].add(setup["terminal_reason"])
        for retest in payload["retests"]:
            if retest["event_id"] in retests and retests[retest["event_id"]] != retest:
                raise ValueError("immutable retest changed")
            retests[retest["event_id"]] = retest
        td = payload["target_diagnostics"]
        levels = td["levels"]
        near = [t for t in levels if t["included_by_existing_cap"]]
        far = [t for t in levels if not t["included_by_existing_cap"]]
        c["cap_gross_exclusion"] |= (
            bool(near)
            and all(t["gross_r"] is not None and D(t["gross_r"]) < 1 for t in near)
            and any(t["gross_r"] is not None and D(t["gross_r"]) >= 1 for t in far)
        )
        for scenario in ("base", "stress"):
            c["cap_" + scenario + "_net_exclusion"] |= (
                bool(near)
                and all(
                    scenario in t["cost_scenarios"]
                    and not t["cost_scenarios"][scenario]["passed"]
                    for t in near
                )
                and any(
                    t["cost_scenarios"].get(scenario, {}).get("passed", False)
                    for t in far
                )
            )
        if td["risk_to_atr"] is not None:
            c["narrow_stop_below_atr"] |= D(td["risk_to_atr"]) < 1
        # Measured comparison only: 1ATR/28bps are not new qualification rules.
        if td["risk_bps"] is not None:
            c["stop_cost_exceeds_gross_risk"] |= D(td["risk_bps"]) < 28
        plan = payload["planning"]
        if plan["state"] == "PLANNABLE":
            if c["first_plan"] is None or instant(plan["as_of"]) < instant(
                c["first_plan"]["as_of"]
            ):
                c["first_plan"] = plan
            c["unsupported_short_plan"] |= setup["direction"] == "bearish"
    for observation in observations:
        sid = observation["setup_id"]
        c = candidates[sid]
        c["observations"].append(observation["as_of"])
        c["selected"] |= observation["selected"]
        c["plannable"] |= observation["decision"] == "plannable"
        basis = facts[observation["basis_id"]][1]
        qualified = basis["setup"]["state"] == "QUALIFIED"
        c["qualified_not_selected"] |= qualified and not observation["selected"]
        c["counter_trend"] |= qualified and observation["alignment"] == "counter_trend"
        c["alignment_blocked"] |= qualified and observation["alignment"] not in (
            "aligned",
            "neutral",
        )
        if observation["decision"] == "plannable":
            if c["first_hierarchy_plannable"] is None:
                c["first_hierarchy_plannable"] = observation["as_of"]
            c["cap_only_otherwise_hierarchy_ready"] |= (
                "minimum_r_multiple_not_met" in basis["planning"]["reasons"]
                and not observation["slots"]["A1"]["position_after_tracking"]
            )
        c["confirmation_blocked"] |= qualified and observation["confirmation"][
            "state"
        ] in ("waiting", "contradicting", "invalidated")
        c["execution_blocked"] |= qualified and observation["execution"][
            "state"
        ] not in ("armed", "triggered")
        for field, state, value in (
            ("first_confirmation", observation["confirmation"]["state"], "confirming"),
            ("first_armed", observation["execution"]["state"], "armed"),
            ("first_trigger", observation["execution"]["state"], "triggered"),
        ):
            if state == value and (
                c[field] is None or instant(observation["as_of"]) < instant(c[field])
            ):
                c[field] = observation["as_of"]
                c[field + "_price"] = observation["execution"]["latest_close"]
    # Only actual baseline events count as real paper admission/occupancy/fills.
    signal_to_setup = {}
    for record in sorted(events, key=lambda x: x["at"]):
        if record["policy"] != "A1":
            continue
        e = record["event"]
        sid = record.get("setup_id")
        if e["kind"] == "signal":
            signal_to_setup[e["id"]] = sid
        if (
            sid in candidates
            and e["kind"] == "rejection"
            and e["reason"] == "occupied_slot"
        ):
            candidates[sid]["actual_slot_rejections"] += 1
        if e["kind"] == "trade":
            sid = sid or signal_to_setup.get(e["signal_id"])
            if sid in candidates:
                candidates[sid]["filled_signal_ids"].add(e["signal_id"])
                c = candidates[sid]
                if c["first_actual_fill"] is None or instant(e["entry_at"]) < instant(
                    c["first_actual_fill"]
                ):
                    c["first_actual_fill"], c["first_actual_fill_price"] = (
                        e["entry_at"],
                        e["entry"],
                    )
    state = json.loads(brain["state"])
    for field in ("position", "pending"):
        position = state["portfolios"]["A1"][field]
        if field == "position" and position:
            sid = position.get("evidence", {}).get("setup_id")
            if sid in candidates:
                candidates[sid]["filled_signal_ids"].add(position["signal_id"])
                c = candidates[sid]
                if c["first_actual_fill"] is None or instant(
                    position["entry_at"]
                ) < instant(c["first_actual_fill"]):
                    c["first_actual_fill"], c["first_actual_fill_price"] = (
                        position["entry_at"],
                        position["fill"],
                    )
    for c in candidates.values():
        c["actual_fills"] = len(c["filled_signal_ids"])
        for name in (
            "failed_rules",
            "pending_rules",
            "terminal_reasons",
            "filled_signal_ids",
        ):
            c[name] = sorted(c[name])
        for field in (
            "first_confirmation",
            "first_armed",
            "first_trigger",
            "first_actual_fill",
        ):
            q = c["first_qualification"]
            v = c[field]
            c[field + "_minutes_after_first_qualification"] = (
                (instant(v) - instant(q)).total_seconds() / 60 if q and v else None
            )
            price, cost = c[field + "_price"], c["first_qualification_price"]
            c[field + "_price_movement_from_first_qualification"] = (
                str(D(price) - D(cost))
                if price is not None and cost is not None
                else None
            )
        if c["first_plan"] and c["first_qualification"]:
            c["first_plan_minutes_after_first_qualification"] = (
                instant(c["first_plan"]["as_of"]) - instant(c["first_qualification"])
            ).total_seconds() / 60
        else:
            c["first_plan_minutes_after_first_qualification"] = None
        c["counterfactual_profit"] = None
    inside = [c for c in candidates.values() if not c["left_censored"]]
    categories = {
        "never_qualified_in_observed_window": sum(not c["qualified"] for c in inside),
        "qualified_but_unselected_at_least_once": sum(
            c["qualified_not_selected"] for c in inside
        ),
        "qualified_counter_trend_at_least_once": sum(
            c["counter_trend"] for c in inside
        ),
        "qualified_alignment_blocked_at_least_once": sum(
            c["alignment_blocked"] for c in inside
        ),
        "qualified_confirmation_blocked_at_least_once": sum(
            c["confirmation_blocked"] for c in inside
        ),
        "qualified_execution_blocked_at_least_once": sum(
            c["execution_blocked"] for c in inside
        ),
        "nearest_two_fail_gross1R_but_farther_exists": sum(
            c["cap_gross_exclusion"] for c in inside
        ),
        "nearest_two_fail_base_net1R_but_farther_exists": sum(
            c["cap_base_net_exclusion"] for c in inside
        ),
        "nearest_two_fail_stress_net1R_but_farther_exists": sum(
            c["cap_stress_net_exclusion"] for c in inside
        ),
        "minimumR_refusal_when_full_hierarchy_ready_and_position_free": sum(
            c["cap_only_otherwise_hierarchy_ready"] for c in inside
        ),
        "actual_baseline_occupied_slot_rejected_candidates": sum(
            c["actual_slot_rejections"] > 0 for c in inside
        ),
        "hypothetical_plannable_short_not_supported_by_cash_model": sum(
            c["unsupported_short_plan"] for c in inside
        ),
    }
    summary = dict(
        diagnostic=diagnostic,
        version=definition["version"],
        brain_run=header["brain_run"],
        completed_brain_run=brain["result"] is not None,
        processed_boundaries=len(coverage),
        coverage_start=coverage[0]["as_of"] if coverage else None,
        coverage_end=coverage[-1]["as_of"] if coverage else None,
        coverage_unavailable=sum(not c["available"] for c in coverage),
        total_distinct_candidates=len(candidates),
        detected_in_observed_period=len(inside),
        left_censored_candidates=len(candidates) - len(inside),
        distinct_selected_candidates=sum(c["selected"] for c in inside),
        distinct_qualified_candidates=sum(c["qualified"] for c in inside),
        distinct_plannable_candidates=sum(c["plannable"] for c in inside),
        late_entry_price_basis="First qualification uses the closed setup price; lower stage prices use the then-current closed execution candle, not a fill. Actual fills are separate.",
        narrow_stop_measurements=dict(
            below_one_atr=sum(c["narrow_stop_below_atr"] for c in inside),
            risk_bps_below_normal_round_trip28=sum(
                c["stop_cost_exceeds_gross_risk"] for c in inside
            ),
        ),
        actual_baseline_fills=sum(c["actual_fills"] for c in candidates.values()),
        qualification_failures=dict(
            Counter(rule for c in inside for rule in c["failed_rules"])
        ),
        pending_requirements=dict(
            Counter(rule for c in inside for rule in c["pending_rules"])
        ),
        terminal_reasons=dict(
            Counter(rule for c in inside for rule in c["terminal_reasons"])
        ),
        restriction_ranking=sorted(categories.items(), key=lambda p: (-p[1], p[0])),
        retest_states_contact=dict(
            Counter(e["state"] + ":" + e["contact"] for e in retests.values())
        ),
        retest_observed_contact=dict(
            Counter(e["contact"] for e in retests.values() if e["state"] == "observed")
        ),
        caveats=[
            "categories overlap and are not causal attribution or profit forecasts",
            "right-censored observation window; never-qualified means not seen qualified yet, not permanently impossible",
            "stage first-times may occur in different lifecycle states; negative lags are reported not hidden",
            "unselected lower-layer gates and plans are diagnostic assessments, not admitted trades",
            "bounded source history still has the documented shallow-history limitation",
            "further targets retain all nearer levels as potential obstacles, not assumed traversable",
            "end-of-window pending unfilled entries are not counted as fills",
        ],
    )
    output.mkdir(parents=True)

    def write(name, values):
        with gzip.open(output / name, "wt") as f:
            for value in values:
                f.write(json.dumps(value, sort_keys=True) + "\n")

    write("candidates.jsonl.gz", [candidates[k] for k in sorted(candidates)])
    write(
        "facts.jsonl.gz",
        [dict(id=k, kind=v[0], payload=v[1]) for k, v in facts.items()],
    )
    write("observations.jsonl.gz", observations)
    write("coverage.jsonl.gz", coverage)
    with (output / "candidates.csv").open("w") as f:
        simple = [
            {
                k: v
                for k, v in c.items()
                if k not in {"basis_ids", "observations", "first_plan"}
            }
            for c in candidates.values()
        ]
        if simple:
            writer = csv.DictWriter(f, fieldnames=list(simple[0]))
            writer.writeheader()
            writer.writerows(simple)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "definition.json").write_text(json.dumps(definition, indent=2) + "\n")
    (output / "export-sha256.json").write_text(
        json.dumps(
            {
                x.name: hashlib.sha256(x.read_bytes()).hexdigest()
                for x in output.iterdir()
                if x.is_file()
            },
            indent=2,
        )
        + "\n"
    )
    return summary


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--diagnostic", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.lab, args.diagnostic, args.output), indent=2))
