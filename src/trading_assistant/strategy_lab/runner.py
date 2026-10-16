"""Resumable chronological experiments; bounded CPU work and atomic checkpoints."""

from .entry_policy import entry_definition

from dataclasses import asdict
import json
import time
from .models import identity, decimal_context, research_code_fingerprint
from .simulation import DEFAULT_EXIT_POLICY, initial_state, metrics, step, validate_exit_policy
from .store import exclusive
from .data import candle_fingerprint
from .holdout import require_period
from trading_assistant.market_data.validation import validate_ohlcv_rows


@exclusive
@decimal_context
def run_experiment(
    store,
    candles,
    manifest,
    strategy,
    execution,
    *,
    stage="development",
    batch=64,
    max_boundaries=None,
    stop=lambda: False,
    fault=None,
    exit_policy=DEFAULT_EXIT_POLICY,
    entry_policy=None,
):
    validate_exit_policy(exit_policy)
    entry_spec = entry_definition(entry_policy)
    from datetime import datetime
    require_period(
        store, datetime.fromisoformat(manifest["start"]),
        datetime.fromisoformat(manifest["end_exclusive"]), strategy.timeframe,
        candidate=strategy.fingerprint, stage=stage,
    )
    if type(batch) is not int or not 1 <= batch <= 1000:
        raise ValueError("checkpoint batch must be1..1000")
    if max_boundaries is not None and (
        type(max_boundaries) is not int or max_boundaries < 1
    ):
        raise ValueError("invalid work budget")
    if stage not in {"development", "walk_forward", "holdout", "forward_paper"}:
        raise ValueError("invalid research stage")
    if strategy.timeframe != manifest["timeframe"] or manifest["candles"] != len(
        candles
    ):
        raise ValueError("dataset and strategy scope mismatch")
    if candle_fingerprint(candles) != manifest["candle_sha256"]:
        raise ValueError("dataset content fingerprint mismatch")
    from datetime import datetime, timedelta
    from trading_assistant.market_data.timeframes import timeframe_to_milliseconds

    report = validate_ohlcv_rows(
        [
            [
                int(c.timestamp.timestamp() * 1000),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
            for c in candles
        ],
        exchange=manifest["exchange"],
        symbol=manifest["symbol"],
        timeframe=strategy.timeframe,
    )
    if not report.is_valid or any(
        (c.exchange, c.symbol, c.timeframe)
        != (manifest["exchange"], manifest["symbol"], strategy.timeframe)
        for c in candles
    ):
        raise ValueError("invalid dataset candles or instrument")
    if not candles or candles[0].timestamp != datetime.fromisoformat(manifest["start"]):
        raise ValueError("dataset start mismatch")
    interval = timedelta(milliseconds=timeframe_to_milliseconds(strategy.timeframe))
    if candles[-1].timestamp + interval != datetime.fromisoformat(
        manifest["end_exclusive"]
    ):
        raise ValueError("dataset end mismatch")
    missing = sum(
        int((b.timestamp - a.timestamp) / interval) - 1
        for a, b in zip(candles, candles[1:])
    )
    if missing != manifest.get("missing_candles", 0):
        raise ValueError("dataset gaps must be declared exactly")
    definition = {
        "strategy": asdict(strategy),
        "strategy_fingerprint": strategy.fingerprint,
        "dataset": manifest,
        "dataset_fingerprint": identity(manifest),
        "execution": asdict(execution),
        "exit_policy": exit_policy,
        "stage": stage,
        "simulator_version": "cash-simulator-v2",
        "closed_data_only": True,
        "decision_source_sha256": research_code_fingerprint(),
    }
    if entry_spec is not None:
        definition["entry_policy"] = entry_spec
    key, row = store.experiment(definition, initial_state(execution))
    if row["result"] is not None:
        return {"id": key, "deduplicated": True, "result": json.loads(row["result"])}
    state = json.loads(row["state"])
    events = []
    worked = 0
    started = time.perf_counter()
    for i in range(state["index"] + 1, len(candles)):
        if stop() or max_boundaries is not None and worked >= max_boundaries:
            break
        state, produced = step(state, candles, i, strategy, execution, exit_policy=exit_policy, entry_policy=entry_policy)
        events.extend(produced)
        worked += 1
        if worked % batch == 0:
            store.checkpoint(key, state, events, fault=fault)
            events = []
            store.heartbeat("research", experiment=key, boundary=i, total=len(candles))
    done = state["index"] == len(candles) - 1
    if not done:
        store.checkpoint(key, state, events, fault=fault)
        return {
            "id": key,
            "complete": False,
            "processed_this_run": worked,
            "next_index": state["index"] + 1,
        }
    # Commit event batch before coherent aggregation. A crash here resumes with
    # the same final state and no events; immutable completion is a second commit.
    store.checkpoint(key, state, events, fault=fault)
    result = metrics(state, store.records(key), execution, entry_policy=entry_policy)
    result.update(
        stage=stage,
        processed_boundaries=len(candles),
        strategy_fingerprint=strategy.fingerprint,
        dataset_fingerprint=identity(manifest),
        runtime_this_attempt_seconds=time.perf_counter() - started,
        period_start=manifest["start"],
        period_end=manifest["end_exclusive"],
    )
    store.checkpoint(key, state, [], result=result)
    return {"id": key, "complete": True, "deduplicated": False, "result": result}
