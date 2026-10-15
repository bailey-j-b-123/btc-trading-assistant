"""Research-only A1/A2/A3; unchanged BRAIN hierarchy and structural planning.

All imported evaluation is read-only over a dedicated in-memory verified cohort.
No original forward ledger, ingestion runner, settings file or production DB.
"""

from dataclasses import dataclass
from datetime import timedelta
import json
import time
from sqlalchemy import create_engine
from trading_assistant.config import Settings
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.multi_timeframe.service import MultiTimeframeService
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.models import (
    HierarchyAlignment as Alignment,
    HierarchyDecision as Decision,
    ConfirmationState,
    ExecutionState,
)
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import SetupFamily
from trading_assistant.trade_planning.planner import plan_trade
from .data import acquire, dataset
from .campaign import months_between
from .models import (
    Execution,
    canonical,
    identity,
    decimal_context,
    research_code_fingerprint,
)
from .simulation import initial_state, metrics, step
from .store import exclusive


@dataclass(frozen=True)
class BrainPolicy:
    policy: str
    timeframe: str = "5m"
    max_hold: int = 576  # Research time exit after48h; never changes original planner.
    version: str = "brain-hierarchy-research-v1"
    warmup: int = 1  # Original hierarchy enforces each actual layer's history.

    def __post_init__(self):
        if (
            self.policy not in {"A1", "A2", "A3"}
            or self.timeframe != "5m"
            or self.max_hold != 576
            or self.version != "brain-hierarchy-research-v1"
        ):
            raise ValueError("unsupported frozen BRAIN research policy")

    @property
    def fingerprint(self):
        return identity(self)


def policy_decision(snapshot, policy):
    """Only the directional veto varies. Actual 4H facts are never rewritten."""
    if policy not in {"A1", "A2", "A3"}:
        raise ValueError("unknown research policy")
    actual = snapshot.alignment
    counter = actual is Alignment.COUNTER_TREND
    setup = snapshot.setup
    ready_reversal = (
        setup.is_qualified
        and setup.family
        in {SetupFamily.LIQUIDITY_REVERSAL.value, SetupFamily.RANGE_REVERSAL.value}
        and snapshot.confirmation.state is ConfirmationState.CONFIRMING
        and snapshot.execution.state is ExecutionState.TRIGGERED
        and snapshot.execution.latest_close is not None
        and (
            (
                setup.direction == "bullish"
                and setup.reference_band_high is not None
                and snapshot.execution.latest_close > setup.reference_band_high
            )
            or (
                setup.direction == "bearish"
                and setup.reference_band_low is not None
                and snapshot.execution.latest_close < setup.reference_band_low
            )
        )
    )
    exception = counter and (policy == "A3" or policy == "A2" and ready_reversal)
    # ALIGNED is a gate argument solely to remove ONE veto in a separate research
    # alternative. The returned evidence retains the actual counter-trend fact.
    verdict = gate_decision(
        context=snapshot.context,
        setup=setup,
        confirmation=snapshot.confirmation,
        execution=snapshot.execution,
        alignment=Alignment.ALIGNED if exception else actual,
    )
    return {
        "policy": policy,
        "decision": verdict[0].value,
        "reasons": verdict[1],
        "status": verdict[2],
        "actual_alignment": actual.value,
        "actual_counter_trend": counter,
        "directional_exception": exception,
        "reversal_rule_satisfied": ready_reversal,
    }


@exclusive
@decimal_context
def compare_brain(
    store,
    *,
    start,
    end,
    warmup_start=None,
    max_boundaries=24,
    max_seconds=60,
    prepare=True,
    stop=lambda: False,
    fault=None,
):
    if (
        start >= end
        or end - start > timedelta(days=32)
        or type(max_boundaries) is not int
        or not 1 <= max_boundaries <= 10000
        or not 1 <= max_seconds <= 3600
    ):
        raise ValueError("bounded BRAIN cohort and CPU budget required")
    warmup_start = warmup_start or (start.replace(day=1) - timedelta(days=1)).replace(
        day=1
    )
    if warmup_start >= start or start - warmup_start > timedelta(days=93):
        raise ValueError("explicit earlier bounded warmup required")
    # Whole-month source API avoids partial boundary receipts; decision range can
    # be a bounded section within a verified month.
    source_end = (
        end.replace(day=28, hour=0, minute=0, second=0, microsecond=0)
        + timedelta(days=4)
    ).replace(day=1)
    if end.day == 1 and not any((end.hour, end.minute, end.second, end.microsecond)):
        source_end = end
    sources = {}
    manifests = {}
    for tf in ("4h", "1h", "15m", "5m"):
        if prepare:
            acquire(
                store,
                months_between(warmup_start.isoformat(), source_end.isoformat()),
                tf,
                stop=stop,
            )
        if stop():
            return {"complete": False, "stage": "source_acquisition_stopped"}
        sources[tf], manifests[tf] = dataset(store, warmup_start, source_end, tf)
    definition = {
        "policies": [BrainPolicy(p) for p in ("A1", "A2", "A3")],
        "execution": Execution(),
        "source": manifests,
        "start": start,
        "end": end,
        "entry": "unchanged1h structural plan entry, next5m-open market fill, first structural target only",
        "shorts": "unsupported_cash_spot_short_explicitly_excluded",
        "a2": "qualified liquidity/range reversal AND confirming15m AND triggered5m AND5m close beyond reference band",
        "a3": "remove only counter_trend alignment veto; all other gates unchanged",
        "source_selection": "entire fixed warmup cohort retained; shallow-history limitation remains",
        "decision_source_sha256": research_code_fingerprint(),
    }
    rid = identity(definition)
    store.db.executescript("""
      CREATE TABLE IF NOT EXISTS brain_runs(id TEXT PRIMARY KEY,definition TEXT NOT NULL,state TEXT NOT NULL,result TEXT);
      CREATE TABLE IF NOT EXISTS brain_observations(run TEXT NOT NULL REFERENCES brain_runs(id),boundary TEXT NOT NULL,
        payload TEXT NOT NULL,PRIMARY KEY(run,boundary));
      CREATE TABLE IF NOT EXISTS brain_events(run TEXT NOT NULL REFERENCES brain_runs(id),policy TEXT NOT NULL,
        id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run,policy,id));
    """)
    initial = {
        "next": 0,
        "portfolios": {p: initial_state(Execution()) for p in ("A1", "A2", "A3")},
        "counts": {p: {} for p in ("A1", "A2", "A3")},
    }
    store.db.execute(
        "INSERT OR IGNORE INTO brain_runs VALUES(?,?,?,NULL)",
        (rid, canonical(definition), canonical(initial)),
    )
    row = store.db.execute("SELECT * FROM brain_runs WHERE id=?", (rid,)).fetchone()
    if row["result"]:
        return {
            "id": rid,
            "complete": True,
            "deduplicated": True,
            "result": json.loads(row["result"]),
        }
    state = json.loads(row["state"])
    engine = create_engine("sqlite:///:memory:")
    OHLCVCandleRecord.__table__.create(engine)
    repo = CandleRepository(engine)
    for candles in sources.values():
        repo.insert_unchanged_or_new(candles)
    service = MultiTimeframeService(
        engine,
        settings=Settings(
            _env_file=None,
            database_url="sqlite:///:memory:",
            raw_data_dir=store.root / "unused-public-raw",
        ),
    )
    # Exact same hour is requested12times. Reuse ONE unchanged qualification
    # tuple rather than re-evaluating identical causal inputs12times.
    original_build = service.qualification.build_frames
    cache = {}

    def cached_build(**kwargs):
        key = kwargs["as_of"]
        if key not in cache:
            cache.clear()
            cache[key] = original_build(**kwargs)
        return cache[key]

    service.qualification.build_frames = cached_build
    execution = Execution()
    execution_candles = sources["5m"]
    indices = [
        i
        for i, c in enumerate(execution_candles)
        if start <= c.timestamp + timedelta(minutes=5) <= end
    ]
    started = time.monotonic()
    count = 0
    try:
        for offset in range(state["next"], len(indices)):
            if (
                stop()
                or count >= max_boundaries
                or time.monotonic() - started >= max_seconds
            ):
                break
            index = indices[offset]
            at = execution_candles[index].timestamp + timedelta(minutes=5)
            snapshot = service.evaluate(decision_time=at)
            decisions = {p: policy_decision(snapshot, p) for p in state["portfolios"]}
            if decisions["A1"]["decision"] != snapshot.decision.value:
                raise ValueError(
                    "baseline hierarchy adapter disagrees with original gate"
                )
            plan = None
            if any(
                d["decision"] == Decision.PLANNABLE.value for d in decisions.values()
            ):
                frames = cache.get(snapshot.setup.boundary_close)
                if frames and snapshot.setup.setup_id:
                    q = enumerate_qualifications(
                        frames,
                        as_of=snapshot.setup.boundary_close,
                        parameters=service.qualification_parameters,
                    )[-1]
                    plan = plan_trade(
                        snapshot=q, frame=frames[-1], setup_id=snapshot.setup.setup_id
                    )
            observation = {
                "snapshot": snapshot.to_json_dict(),
                "policies": decisions,
                "plan": plan.to_json_dict() if plan else None,
            }
            produced = []
            for p, verdict in decisions.items():
                counts = state["counts"][p]
                counts[verdict["decision"]] = counts.get(verdict["decision"], 0) + 1
                if (
                    plan
                    and plan.actionable
                    and plan.direction == "bearish"
                    and verdict["decision"] == Decision.PLANNABLE.value
                ):
                    counts["unsupported_short_plan"] = (
                        counts.get("unsupported_short_plan", 0) + 1
                    )

                def signal(*_, **__):
                    if (
                        verdict["decision"] != Decision.PLANNABLE.value
                        or not plan
                        or not plan.actionable
                        or plan.direction != "bullish"
                    ):
                        return None
                    return {
                        "known_at": at.isoformat(),
                        "candle_open": execution_candles[index].timestamp.isoformat(),
                        "reference": str(plan.entry.value),
                        "stop": str(plan.stop.value),
                        "target": str(plan.targets[0].level.value),
                        "source_plan_id": plan.id,
                        "evidence": {
                            "regime": snapshot.context.regime.value,
                            "hierarchy_id": snapshot.identity(),
                            "actual_alignment": snapshot.alignment.value,
                            "setup_id": plan.setup_id,
                            "policy": p,
                            "plan": plan.to_json_dict(),
                        },
                    }

                state["portfolios"][p], events = step(
                    state["portfolios"][p],
                    execution_candles,
                    index,
                    BrainPolicy(p),
                    execution,
                    decision_function=signal,
                )
                produced.extend((p, event) for event in events)
            state["next"] = offset + 1
            store.db.execute("BEGIN IMMEDIATE")
            try:
                store.db.execute(
                    "INSERT INTO brain_observations VALUES(?,?,?)",
                    (rid, at.isoformat(), canonical(observation)),
                )
                for p, event in produced:
                    store.db.execute(
                        "INSERT OR IGNORE INTO brain_events VALUES(?,?,?,?)",
                        (rid, p, identity(event), canonical(event)),
                    )
                if fault:
                    fault()
                store.db.execute(
                    "UPDATE brain_runs SET state=? WHERE id=?", (canonical(state), rid)
                )
                store.db.execute("COMMIT")
            except BaseException:
                store.db.execute("ROLLBACK")
                raise
            count += 1
            store.heartbeat(
                "brain_policy_comparison",
                boundary=at.isoformat(),
                processed=state["next"],
                total=len(indices),
            )
        done = state["next"] == len(indices)
        answer = {
            "processed_this_run": count,
            "total_processed": state["next"],
            "total_boundaries": len(indices),
            "runtime_this_attempt_seconds": time.monotonic() - started,
            "counts": state["counts"],
            "performance": {},
        }
        for p, portfolio in state["portfolios"].items():
            records = [
                json.loads(r[0])
                for r in store.db.execute(
                    "SELECT payload FROM brain_events WHERE run=? AND policy=?",
                    (rid, p),
                )
            ]
            answer["performance"][p] = metrics(portfolio, records, execution)
        if done:
            store.db.execute(
                "UPDATE brain_runs SET result=? WHERE id=?", (canonical(answer), rid)
            )
        return {"id": rid, "complete": done, "result": answer}
    finally:
        engine.dispose()
