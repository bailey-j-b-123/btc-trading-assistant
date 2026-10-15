"""Frozen bounded job plan, independent validation and one-use holdout custody."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal as D
import json
import time
from .data import acquire, dataset
from .models import (
    Execution,
    candidates,
    canonical,
    identity,
    research_code_fingerprint,
)
from .runner import run_experiment
from .store import exclusive
from .validation import ValidationRules, gate


def months_between(start, end):
    result = []
    current = datetime.fromisoformat(start)
    stop = datetime.fromisoformat(end)
    while current < stop:
        result.append(current.strftime("%Y-%m"))
        current = current.replace(
            year=current.year + int(current.month == 12),
            month=current.month % 12 + 1,
            day=1,
        )
    return result


@dataclass(frozen=True)
class ResearchPlan:
    development: tuple[str, str] = (
        "2023-01-01T00:00:00+00:00",
        "2024-01-01T00:00:00+00:00",
    )
    folds: tuple[tuple[str, str], ...] = (
        ("2024-01-01T00:00:00+00:00", "2024-05-01T00:00:00+00:00"),
        ("2024-05-01T00:00:00+00:00", "2024-09-01T00:00:00+00:00"),
        ("2024-09-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
    )
    holdout: tuple[str, str] = (
        "2025-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
    )
    search_size: int = 16
    execution: Execution = Execution()
    rules: ValidationRules = ValidationRules()
    version: str = "research-campaign-v2-manual-holdout"

    def __post_init__(self):
        if type(self.search_size) is not int or not 1 <= self.search_size <= 16:
            raise ValueError("search budget must be1..16")
        if len(self.folds) < 3:
            raise ValueError("at least three predetermined validation folds required")
        previous = None
        for start, end in (self.development, *self.folds, self.holdout):
            a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
            if (
                a.utcoffset() is None
                or b.utcoffset() is None
                or a.utcoffset().total_seconds() != 0
                or b.utcoffset().total_seconds() != 0
            ):
                raise ValueError("UTC research periods required")
            if (
                a.day != 1
                or b.day != 1
                or any(
                    (
                        a.hour,
                        a.minute,
                        a.second,
                        a.microsecond,
                        b.hour,
                        b.minute,
                        b.second,
                        b.microsecond,
                    )
                )
            ):
                raise ValueError("whole-month splits required")
            if a >= b or previous is not None and a < previous:
                raise ValueError(
                    "chronological nonoverlapping research windows required"
                )
            previous = b


def schema(store):
    store.db.executescript("""
    CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY,definition TEXT NOT NULL,state TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS validations(experiment TEXT NOT NULL REFERENCES experiments(id),
      rules TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(experiment,rules));
    CREATE TABLE IF NOT EXISTS holdout_custody(scope TEXT PRIMARY KEY,campaign TEXT NOT NULL,
      candidate TEXT NOT NULL,experiment TEXT);
    CREATE TABLE IF NOT EXISTS promotions(candidate TEXT PRIMARY KEY,definition TEXT NOT NULL,
      evidence TEXT NOT NULL,promoted_at TEXT NOT NULL);
    """)


@exclusive
def run_campaign(
    store, plan=None, *, max_jobs=4, max_seconds=300, stop=lambda: False, prepare=True,
    approve_holdout_for=None,
):
    plan = plan or ResearchPlan()
    schema(store)
    if (
        type(max_jobs) is not int
        or not 1 <= max_jobs <= 256
        or not 1 <= max_seconds <= 86400
    ):
        raise ValueError("bounded job/time budgets required")
    grid = candidates(plan.search_size)
    definition = {
        **json.loads(canonical(plan)),
        "candidates": [json.loads(canonical(c)) for c in grid],
        "simulator": "cash-simulator-v2",
        "decision_source_sha256": research_code_fingerprint(),
        "bootstrap_limit": "exploratory, not proof of stationarity or guaranteed family-wise control",
    }
    cid = identity(definition)
    store.db.execute(
        "INSERT OR IGNORE INTO campaigns VALUES(?,?,?)",
        (cid, canonical(definition), canonical({"jobs": {}, "finished": False})),
    )
    row = store.db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if row["definition"] != canonical(definition):
        raise ValueError("campaign definition mismatch")
    state = json.loads(row["state"])
    started = time.monotonic()
    worked = 0

    def save():
        store.db.execute(
            "UPDATE campaigns SET state=? WHERE id=?", (canonical(state), cid)
        )

    costs = (
        plan.execution,
        replace(
            plan.execution,
            fee_bps=plan.execution.fee_bps * 2,
            spread_bps=plan.execution.spread_bps * 2,
            slippage_bps=plan.execution.slippage_bps * 3,
        ),
    )

    def expired():
        return stop() or time.monotonic() - started >= max_seconds

    loaded = {}

    def perform(strategy, period, execution, stage, label):
        nonlocal worked
        job = identity((strategy, period, execution, stage))
        if job in state["jobs"]:
            return state["jobs"][job]
        if worked >= max_jobs or expired():
            return None
        a, b = map(datetime.fromisoformat, period)
        source_key = (period, strategy.timeframe)
        if source_key not in loaded:
            if prepare:
                acquire(
                    store, months_between(*period), strategy.timeframe, stop=expired
                )
                if expired():
                    return None
            loaded[source_key] = dataset(store, a, b, strategy.timeframe)
        c, m = loaded[source_key]
        result = run_experiment(
            store, c, m, strategy, execution, stage=stage, stop=expired
        )
        if not result.get("complete", result.get("deduplicated", False)):
            return None
        measured = result["result"]
        verdict = gate(
            measured,
            search_size=len(grid),
            rules=plan.rules,
            stage=stage,
            seed=result["id"],
        )
        store.db.execute(
            "INSERT OR IGNORE INTO validations VALUES(?,?,?)",
            (result["id"], identity(plan.rules), canonical(verdict)),
        )
        answer = {
            "experiment": result["id"],
            "candidate": strategy.fingerprint,
            "stage": stage,
            "label": label,
            "gate": verdict,
            "result": {k: v for k, v in measured.items() if k != "net_r_values"},
        }
        state["jobs"][job] = answer
        worked += 1
        save()
        return answer

    # Development and cost stress first, then chronological unchanged-rule folds.
    survivors = []
    unfinished = False
    for strategy in grid:
        checks = []
        for k, cost in enumerate(costs):
            answer = perform(
                strategy,
                plan.development,
                cost,
                "development",
                "base" if k == 0 else "stress",
            )
            if answer is None:
                unfinished = True
                break
            checks.append(answer)
        if unfinished:
            break
        if not all(a["gate"]["passed"] for a in checks):
            continue
        for fold, period in enumerate(plan.folds):
            for k, cost in enumerate(costs):
                answer = perform(
                    strategy,
                    period,
                    cost,
                    "walk_forward",
                    f"fold{fold + 1}-" + ("base" if k == 0 else "stress"),
                )
                if answer is None:
                    unfinished = True
                    break
                checks.append(answer)
            if unfinished:
                break
        if unfinished:
            break
        if all(a["gate"]["passed"] for a in checks):
            survivors.append((strategy, checks))
    if unfinished:
        state["stage"] = "research_in_progress"
        save()
        return {
            "campaign": cid,
            "finished": False,
            "jobs_this_run": worked,
            "completed_jobs": len(state["jobs"]),
        }
    # Selection precedes access to holdout performance; never tune on a failed holdout.
    if survivors:
        strategy, proof = max(
            survivors,
            key=lambda pair: min(
                D(x["gate"]["block_bootstrap_lower_r"]) for x in pair[1]
            ),
        )
        # This receipt freezes the exact candidate and completed validation
        # jobs. A config file or an overnight worker never grants access.
        frozen = {
            "campaign": cid,
            "candidate": strategy.fingerprint,
            "execution": json.loads(canonical(plan.execution)),
            "holdout": plan.holdout,
            "validation_experiments": sorted(x["experiment"] for x in proof),
        }
        approval_id = identity(frozen)
        if approve_holdout_for != approval_id:
            if approve_holdout_for is not None:
                raise ValueError("holdout approval does not match frozen candidate")
            state.update(stage="awaiting_explicit_holdout_approval",
                         frozen_holdout_candidate=strategy.fingerprint,
                         holdout_approval_id=approval_id, frozen_validation=frozen)
            save()
            return {"campaign": cid, "finished": False,
                    "stage": state["stage"], "approval_id": approval_id,
                    "frozen_validation": frozen, "jobs_this_run": worked,
                    "completed_jobs": len(state["jobs"])}
        scope = identity(
            {
                "exchange": "binance",
                "symbol": "BTC/USDT",
                "period": plan.holdout,
            }
        )
        custody = store.db.execute(
            "SELECT * FROM holdout_custody WHERE scope=?", (scope,)
        ).fetchone()
        # The same BTC history is not untouched just because a new timeframe,
        # split boundary, candidate or config fingerprint was chosen later.
        hstart, hend = map(datetime.fromisoformat, plan.holdout)
        reused = False
        for reserved in store.db.execute(
            "SELECT h.campaign,c.definition FROM holdout_custody h JOIN campaigns c ON c.id=h.campaign"
        ):
            other = json.loads(reserved["definition"])["holdout"]
            a, b = map(datetime.fromisoformat, other)
            if reserved["campaign"] != cid and max(a, hstart) < min(b, hend):
                reused = True
        owned_experiments = {j["experiment"] for j in state["jobs"].values()}
        for observed in store.db.execute("SELECT id,definition FROM experiments"):
            if observed["id"] in owned_experiments:
                continue
            source = json.loads(observed["definition"])["dataset"]
            a, b = (
                datetime.fromisoformat(source["start"]),
                datetime.fromisoformat(source["end_exclusive"]),
            )
            if max(a, hstart) < min(b, hend):
                reused = True
        if reused:
            state.update(
                stage="holdout_unavailable_previously_observed_or_reserved",
                finished=True,
            )
            save()
            return {"campaign": cid, "finished": True, "reason": state["stage"]}
        if custody and (custody["campaign"], custody["candidate"]) != (
            cid,
            strategy.fingerprint,
        ):
            state.update(stage="holdout_unavailable_already_used", finished=True)
            save()
            return {"campaign": cid, "finished": True, "reason": state["stage"]}
        store.db.execute(
            "INSERT OR IGNORE INTO holdout_custody VALUES(?,?,?,NULL)",
            (scope, cid, strategy.fingerprint),
        )
        state["frozen_holdout_candidate"] = strategy.fingerprint
        save()
        answers = []
        for k, cost in enumerate(costs):
            answer = perform(
                strategy, plan.holdout, cost, "holdout", "base" if k == 0 else "stress"
            )
            if answer is None:
                state["stage"] = "holdout_in_progress"
                save()
                return {
                    "campaign": cid,
                    "finished": False,
                    "jobs_this_run": worked,
                    "completed_jobs": len(state["jobs"]),
                }
            answers.append(answer)
        store.db.execute(
            "UPDATE holdout_custody SET experiment=? WHERE scope=?",
            (answers[0]["experiment"], scope),
        )
        if all(a["gate"]["passed"] for a in answers):
            from datetime import UTC

            store.db.execute(
                "INSERT OR IGNORE INTO promotions VALUES(?,?,?,?)",
                (
                    strategy.fingerprint,
                    canonical(
                        {
                            "strategy": strategy,
                            "execution": plan.execution,
                            "simulator": "cash-simulator-v2",
                            "decision_source_sha256": research_code_fingerprint(),
                        }
                    ),
                    canonical(
                        {"campaign": cid, "validation": proof, "holdout": answers}
                    ),
                    datetime.now(UTC).isoformat(),
                ),
            )
            state["promoted_candidate"] = strategy.fingerprint
        else:
            state["holdout_rejected_candidate"] = strategy.fingerprint
    state.update(
        stage="finished", finished=True, surviving_validation_candidates=len(survivors)
    )
    save()
    return {
        "campaign": cid,
        "finished": True,
        "jobs_this_run": worked,
        "completed_jobs": len(state["jobs"]),
        "promoted": state.get("promoted_candidate"),
        "holdout_rejected": state.get("holdout_rejected_candidate"),
        "validation_survivors": len(survivors),
    }
