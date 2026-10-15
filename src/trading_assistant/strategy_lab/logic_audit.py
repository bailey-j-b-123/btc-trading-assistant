"""Bounded offline audit of the completed January cohort; no new strategies.

Read only a marked lab. Reconstruct original decisions exactly, preserve the
two-target policy, enumerate source evidence and replay downstream repairs on
the frozen hierarchy observations. Never claim this reruns the entire hierarchy.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import types

from sqlalchemy import create_engine
from trading_assistant.costs import FrictionAssumptions
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.references import references
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.setup_qualification.service import (
    QualificationService,
    bounded_replay_start,
)
from trading_assistant.trade_planning.economics import assess
from trading_assistant.trade_planning.levels import (
    structural_target_catalog,
    structural_target_candidates,
)
from trading_assistant.trade_planning.parameters import PlanningParameters
from trading_assistant.trade_planning.planner import plan_trade
from .brain import BrainPolicy
from .data import candle_fingerprint
from .models import canonical, decimal_context, research_code_fingerprint
from .simulation import initial_state, metrics, step
from .store import Store, MARKER

BASE_HEAD = "2adb4ee243b45d10d43df3acb61be193c86dc308"
BASE_SIM_SHA = "a1867cd295796b47e21c8c93c4f65b5a739c84c638a1168389cba4e83732c7b8"
START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 2, 1, tzinfo=UTC)


def readonly_lab(root):
    path = Path(root).absolute()
    if any(
        p.is_symlink() or p.name in {"data", "backups", ".env"}
        for p in (path, *path.parents)
    ):
        raise ValueError("only ordinary lab-only storage")
    if (path / "LAB-ONLY.txt").read_text() != MARKER or (
        path / "research.sqlite3"
    ).is_symlink():
        raise ValueError("not a marked research lab")
    db = sqlite3.connect(
        "file:" + str(path / "research.sqlite3") + "?mode=ro", uri=True
    )
    db.row_factory = sqlite3.Row
    return db


def baseline_step():
    root = Path(__file__).parents[3]
    raw = subprocess.run(
        [
            "git",
            "show",
            BASE_HEAD + ":src/trading_assistant/strategy_lab/simulation.py",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    if sha256(raw).hexdigest() != BASE_SIM_SHA:
        raise ValueError("baseline simulator is not the verified original source")
    module = types.ModuleType("trading_assistant.strategy_lab._audit_verified_baseline")
    module.__package__ = "trading_assistant.strategy_lab"
    exec(compile(raw, "verified-PR76-simulation.py", "exec"), module.__dict__)
    return module.step


def cost(label):
    from .models import Execution

    if label == "base":
        return Execution()
    if label == "stress":
        return Execution(fee_bps=D(20), spread_bps=D(4), slippage_bps=D(9))
    raise ValueError("unknown frozen cost scenario")


def scenario(label):
    e = cost(label)
    return FrictionAssumptions(
        fee_bps=e.fee_bps,
        entry_slippage_bps=e.spread_bps / 2 + e.slippage_bps,
        exit_slippage_bps=e.spread_bps / 2 + e.slippage_bps,
    )


def target_evidence(frame, plan):
    at = plan.as_of
    catalog, excluded = structural_target_catalog(
        frame.patterns, plan.entry.value, plan.direction, PlanningParameters(), at
    )
    selected, _ = structural_target_candidates(
        frame.patterns, plan.entry.value, plan.direction, PlanningParameters(), at
    )
    assert tuple(catalog[:2]) == selected
    lookup = {
        r.id: r
        for r in references(frame.patterns.structure, ("binance", "BTC/USDT", "1h"))
    }
    lookup.update({r.id: r for r in frame.patterns.equal_levels})
    rows = []
    for rank, c in enumerate(catalog, 1):
        assert c.confirmed_at <= at and (c.observed_at is None or c.observed_at <= at)
        r = lookup[c.source_id]
        members = getattr(r, "swings", getattr(r, "members", ()))
        assert all(s.confirmed_at <= at for s in members)
        zone = getattr(r, "zone", None)
        reward = abs(c.value - plan.entry.value)
        grades = {
            label: assess(
                entry=plan.entry.value,
                stop=plan.stop.value,
                target=c.value,
                direction=plan.direction,
                costs=scenario(label),
            )
            for label in ("base", "stress")
        }
        rows.append(
            {
                "plan_id": plan.id,
                "as_of": at.isoformat(),
                "direction": plan.direction,
                "rank": rank,
                "value": str(c.value),
                "source_id": c.source_id,
                "source_type": c.source_type,
                "observed_at": None
                if c.observed_at is None
                else c.observed_at.isoformat(),
                "confirmed_at": c.confirmed_at.isoformat(),
                "source_age_hours": None
                if c.observed_at is None
                else (at - c.observed_at).total_seconds() / 3600,
                "raw_r": str(reward / plan.risk_per_unit),
                "within_cap": rank <= 2,
                "raw_floor_met": reward >= plan.risk_per_unit,
                "base_net_r": str(grades["base"].net_r),
                "stress_net_r": str(grades["stress"].net_r),
                "base_viable": grades["base"].passed,
                "stress_viable": grades["stress"].passed,
                "touch_count": None if zone is None else zone.touch_count,
                "source_member_opens": "|".join(
                    s.timestamp.isoformat() for s in members
                ),
                "known_broken_or_invalidated": None,
            }
        )  # No such lifecycle fact is exposed by this adapter.
    return rows, list(excluded), to_jsonable(selected)


@decimal_context
def run_audit(source_root, dest_root):
    source = readonly_lab(source_root)
    if Path(source_root).resolve() == Path(dest_root).resolve():
        source.close()
        raise ValueError("never write in the source results lab")
    dest = Store(dest_root)
    engine = create_engine("sqlite:///:memory:")
    try:
        runs = source.execute("select id,definition,result from brain_runs").fetchall()
        matching = [
            r
            for r in runs
            if datetime.fromisoformat(json.loads(r["definition"])["start"]) == START
            and datetime.fromisoformat(json.loads(r["definition"])["end"]) == END
            and r["result"] is not None
        ]
        if len(matching) != 1:
            raise ValueError("one completed exact January cohort required")
        cohort = matching[0]
        observations = []
        original = {}
        digest = sha256()
        for row in source.execute(
            "select boundary,payload from brain_observations where run=? order by boundary",
            (cohort["id"],),
        ):
            digest.update((row["boundary"] + "\0" + row["payload"] + "\n").encode())
            o = json.loads(row["payload"])
            p = o["plan"]
            if p:
                original.setdefault(p["id"], {"plan": p, "boundaries": []})[
                    "boundaries"
                ].append(row["boundary"])
            observations.append(
                (
                    row["boundary"],
                    {k: v["decision"] for k, v in o["policies"].items()},
                    None if p is None else p["id"],
                )
            )
        if len(observations) != 8929:
            raise ValueError("incomplete original evidence")
        candles = {tf: [] for tf in ("1h", "4h", "5m")}
        for r in source.execute(
            "select * from ohlcv_candles where timeframe in ('1h','4h','5m') and timestamp>='2023-12-01' and timestamp<'2024-02-01' order by timeframe,timestamp"
        ):
            candles[r["timeframe"]].append(
                Candle(
                    r["exchange"],
                    r["symbol"],
                    r["timeframe"],
                    datetime.fromisoformat(r["timestamp"]).replace(tzinfo=UTC),
                    *[D(r[k]) for k in ("open", "high", "low", "close", "volume")],
                )
            )
        manifests = json.loads(cohort["definition"])["source"]
        for tf, values in candles.items():
            assert candle_fingerprint(values) == manifests[tf]["candle_sha256"]
        OHLCVCandleRecord.__table__.create(engine)
        CandleRepository(engine).insert_unchanged_or_new(candles["1h"] + candles["4h"])
        service = QualificationService(engine)
        qp = QualificationParameters()
        reconstructed = {}
        corrected = {label: {} for label in ("base", "stress")}
        reports = []
        targets = []
        for pid, record in original.items():
            p = record["plan"]
            at = datetime.fromisoformat(p["as_of"].replace("Z", "+00:00"))
            frames = service.build_frames(
                exchange="binance",
                symbol="BTC/USDT",
                timeframe="1h",
                as_of=at,
                parameters=qp,
                start_at=bounded_replay_start(as_of=at, timeframe="1h", parameters=qp),
            )
            q = enumerate_qualifications(frames, as_of=at, parameters=qp)[-1]
            plan = plan_trade(snapshot=q, frame=frames[-1], setup_id=p["setup_id"])
            assert plan.to_json_dict() == p, (
                "unchanged plan must reproduce byte-for-byte"
            )
            reconstructed[pid] = plan
            levels, excluded, selected = target_evidence(frames[-1], plan)
            targets.extend(levels)
            grades = {}
            for label in corrected:
                new = plan_trade(
                    snapshot=q,
                    frame=frames[-1],
                    setup_id=p["setup_id"],
                    parameters=PlanningParameters(economic_costs=scenario(label)),
                )
                assert (new.entry, new.stop, new.risk_per_unit) == (
                    plan.entry,
                    plan.stop,
                    plan.risk_per_unit,
                )
                corrected[label][pid] = new
                grades[label] = {
                    "state": new.state.value,
                    "reasons": new.reasons,
                    "id": new.id,
                    "rules_version": new.planning_rules_version,
                }
            reports.append(
                {
                    "plan": p,
                    "exactly_reproduced": True,
                    "boundary_evaluations": len(record["boundaries"]),
                    "selected_before_r": selected,
                    "eligible_farther_prices": sum(
                        t["rank"] > 2 and t["raw_floor_met"] for t in levels
                    ),
                    "full_catalog_unique_prices": len(levels),
                    "source_exclusions": excluded,
                    "economic_cohorts": grades,
                    "diagnosis": "raw nearest-two floor correctly refused; farther levels do not remove intervening barriers"
                    if not plan.actionable
                    else "bearish plan unsupported by Spot cash execution; additionally fails realistic net economics",
                }
            )
        out = dest.root
        (out / "plan-diagnostics.json").write_text(canonical(reports) + "\n")
        with (out / "all-targets.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(targets[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(targets)
        source_hash = digest.hexdigest()
        old_step = baseline_step()
        bar5 = tuple(candles["5m"])
        indexes = {
            (c.timestamp + timedelta(minutes=5)).isoformat(): i
            for i, c in enumerate(bar5)
        }
        answers = []
        for variant in ("original", "lifecycle_only", "economics_only", "combined"):
            for label in ("base", "stress"):
                for policy in ("A1", "A2", "A3"):
                    definition = {
                        "audit_version": "trading-logic-audit-v1",
                        "variant": variant,
                        "cost": label,
                        "policy": policy,
                        "source_run": cohort["id"],
                        "source_observations_sha256": source_hash,
                        "original_head": BASE_HEAD,
                        "baseline_simulator_sha256": BASE_SIM_SHA,
                        "corrected_source_sha256": research_code_fingerprint(),
                        "audit_source_sha256": sha256(
                            Path(__file__).read_bytes()
                        ).hexdigest(),
                        "execution": to_jsonable(cost(label)),
                        "source_manifests": manifests,
                        "target_cap_unchanged": 2,
                        "closed_boundaries_inclusive": [
                            START.isoformat(),
                            END.isoformat(),
                        ],
                    }
                    e = cost(label)
                    with dest.writer():
                        key, row = dest.experiment(definition, initial_state(e))
                        if row["result"] is not None:
                            answers.append(
                                {
                                    "id": key,
                                    "definition": definition,
                                    "result": json.loads(row["result"]),
                                    "deduplicated": True,
                                }
                            )
                            continue
                        state = json.loads(row["state"])
                        events = []
                        step_function = (
                            step
                            if variant in ("lifecycle_only", "combined")
                            else old_step
                        )
                        considered = set()
                        actionable = set()
                        spot = set()
                        refusals = {}
                        evaluations = 0
                        for offset, (boundary, verdicts, pid) in enumerate(
                            observations
                        ):
                            plan = None
                            if pid and verdicts[policy] == "plannable":
                                considered.add(pid)
                                evaluations += 1
                                plan = (
                                    corrected[label][pid]
                                    if variant in ("economics_only", "combined")
                                    else reconstructed[pid]
                                )
                                if plan.actionable:
                                    actionable.add(plan.id)
                                    if plan.direction == "bullish":
                                        spot.add(plan.id)
                                else:
                                    for reason in plan.reasons:
                                        refusals[reason] = refusals.get(reason, 0) + 1
                            if offset <= state["index"]:
                                continue
                            index = indexes[boundary]

                            def signal(*args, **kwargs):
                                if (
                                    plan is None
                                    or not plan.actionable
                                    or plan.direction != "bullish"
                                ):
                                    return None
                                return {
                                    "known_at": boundary,
                                    "candle_open": bar5[index].timestamp.isoformat(),
                                    "reference": str(plan.entry.value),
                                    "stop": str(plan.stop.value),
                                    "target": str(plan.targets[0].level.value),
                                    "source_plan_id": plan.id,
                                    "evidence": {
                                        "regime": "original_frozen_hierarchy",
                                        "source_run": cohort["id"],
                                    },
                                }

                            # Input indices are the original full source; state.index is an
                            # audit observation cursor for atomic resume between checkpoints.
                            state, produced = step_function(
                                state,
                                bar5,
                                index,
                                BrainPolicy(policy),
                                e,
                                decision_function=signal,
                            )
                            state["index"] = offset
                            events.extend(produced)
                            if (offset + 1) % 128 == 0:
                                dest.checkpoint(key, state, events)
                                events = []
                        dest.checkpoint(key, state, events)
                        result = metrics(state, dest.records(key), e)
                        result.update(
                            total_boundaries=len(observations),
                            hierarchy_gate_evaluations=evaluations,
                            distinct_plan_snapshots=len(considered),
                            distinct_actionable_plans=len(actionable),
                            distinct_executable_spot_plans=len(spot),
                            planner_refusals=refusals,
                            net_pnl=str(D(state["equity"]) - e.initial_capital),
                            mean_holding_hours=None,
                        )
                        dest.checkpoint(key, state, [], result=result)
                        answers.append(
                            {
                                "id": key,
                                "definition": definition,
                                "result": result,
                                "deduplicated": False,
                            }
                        )
                    print(
                        canonical(
                            {
                                "variant": variant,
                                "cost": label,
                                "policy": policy,
                                "closed": result["closed_trades"],
                                "signals": result["opportunities"],
                                "pnl": result["net_pnl"],
                            }
                        ),
                        flush=True,
                    )
        after = sha256()
        for r in source.execute(
            "select boundary,payload from brain_observations where run=? order by boundary",
            (cohort["id"],),
        ):
            after.update((r["boundary"] + "\0" + r["payload"] + "\n").encode())
        assert after.hexdigest() == source_hash, (
            "source observations changed during audit"
        )
        summary = {
            "original_run": cohort["id"],
            "source_observations_sha256": source_hash,
            "source_observations_after_sha256": after.hexdigest(),
            "source_boundary_count": len(observations),
            "plans_reproduced": len(reports),
            "refused_plans": sum(p["plan"]["state"] == "NO_PLAN" for p in reports),
            "refused_with_farther_raw_1r": sum(
                p["plan"]["state"] == "NO_PLAN" and p["eligible_farther_prices"] > 0
                for p in reports
            ),
            "all_target_rows": len(targets),
            "rows": answers,
            "source_unchanged_readonly": True,
            "integrity": dest.db.execute("pragma integrity_check").fetchone()[0],
            "fk_errors": len(dest.db.execute("pragma foreign_key_check").fetchall()),
            "limitations": "Frozen original hierarchy observations, not a new full hierarchy run. Same fixed warmup; existing shallow-history defect preserved. Short execution unsupported.2024alreadyexamined. No holdout access, no synthetic performance, no target/stop/risk tuning.",
        }
        (out / "comparison.json").write_text(canonical(summary) + "\n")
        return summary
    finally:
        source.close()
        dest.close()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_audit(args.source, args.output)
    print(canonical({k: v for k, v in result.items() if k != "rows"}))


if __name__ == "__main__":
    main()
