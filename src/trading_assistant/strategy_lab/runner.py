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
    decision_adapter=None,
    directional_model=None,
    mark_candles=None,
    funding_events=(),
    signal_provider=None,
):
    if decision_adapter not in (None, "discovery-v1"):
        raise ValueError("unsupported versioned research decision adapter")
    adapter_kwargs = {}
    if decision_adapter is not None:
        from .discovery import Rule, decide as learned_decide
        if not isinstance(strategy, Rule) or strategy.fit_source_sha256 != research_code_fingerprint():
            raise ValueError("frozen learned rule/source mismatch")
        adapter_kwargs["decision_function"] = learned_decide
    if directional_model is None and (mark_candles is not None or funding_events or signal_provider is not None):
        raise ValueError("directional inputs/provider require research-only opt-in")
    provider_definition = None
    if signal_provider is not None:
        import inspect
        from hashlib import sha256
        if decision_adapter is not None or not inspect.isfunction(signal_provider) or signal_provider.__closure__:
            raise ValueError("one inspectable, non-closure research signal provider required")
        provider_definition = {"module": signal_provider.__module__, "name": signal_provider.__qualname__,
                               "source_sha256": sha256(inspect.getsource(signal_provider).encode()).hexdigest()}
        adapter_kwargs["decision_function"] = signal_provider
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
    directional_definition = None
    funding_by_time = {}
    if directional_model is not None:
        from .directional import DirectionalModel, FundingEvent
        if not isinstance(directional_model, DirectionalModel):
            raise ValueError("typed directional model required")
        directional_model.validate(execution, strategy, exit_policy, entry_policy)
        if stage == "forward_paper":
            raise ValueError("directional execution is historical research-only")
        if mark_candles is not None:
            if directional_model.mark_source != "recorded_marks" or not isinstance(mark_candles, tuple) or len(mark_candles) != len(candles):
                raise ValueError("recorded mark scope/count mismatch")
            for trade, mark in zip(candles, mark_candles):
                if ((trade.timestamp, trade.exchange, trade.symbol, trade.timeframe) != (mark.timestamp, mark.exchange, mark.symbol, mark.timeframe)
                        or not 0 < mark.low <= min(mark.open, mark.close) <= max(mark.open, mark.close) <= mark.high):
                    raise ValueError("invalid aligned recorded mark series")
        elif directional_model.mark_source != "trade_price_proxy_assumption":
            raise ValueError("recorded marks required before experiment admission")
        if not isinstance(funding_events, tuple):
            raise ValueError("immutable funding events required")
        if any(not isinstance(e, FundingEvent) for e in funding_events) or tuple(e.at for e in funding_events) != tuple(sorted(e.at for e in funding_events)):
            raise ValueError("funding events must be typed and chronologically ordered")
        opens = {c.timestamp for c in candles}
        for event in funding_events:
            if not isinstance(event, FundingEvent) or event.at not in opens or event.at in funding_by_time:
                raise ValueError("funding requires unique in-period bar-open timestamps")
            funding_by_time[event.at] = event
        directional_definition = {"model": asdict(directional_model),
                                  "mark_fingerprint": candle_fingerprint(mark_candles) if mark_candles is not None else "explicit_trade_proxy_assumption",
                                  "funding_events": [asdict(e) for e in funding_events],
                                  "signal_provider": provider_definition}
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
    if decision_adapter is not None:
        definition["decision_adapter"] = decision_adapter
    if directional_definition is not None:
        definition["directional_execution"] = directional_definition
        definition["simulator_version"] = "unified-directional-v1"
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
        directional_kwargs = {}
        if directional_model is not None:
            directional_kwargs = {"directional_model": directional_model,
                                  "mark_candle": mark_candles[i] if mark_candles is not None else None,
                                  "funding_event": funding_by_time.get(candles[i].timestamp)}
        state, produced = step(state, candles, i, strategy, execution, exit_policy=exit_policy, entry_policy=entry_policy, **adapter_kwargs, **directional_kwargs)
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
    result = metrics(state, store.records(key), execution, entry_policy=entry_policy, directional_model=directional_model)
    if directional_model is not None:
        from .novel import independent_audit
        result["audit"] = independent_audit(
            state, store.records(key), candles, execution, directional_model=directional_model,
            mark_candles=mark_candles, funding_events=funding_events,
        )
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
