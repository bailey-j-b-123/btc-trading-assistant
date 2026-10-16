"""Conservative chronological cash simulation. No order or account interface."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D, ROUND_FLOOR
from .models import identity
from .strategies import decide

DEFAULT_EXIT_POLICY = "strategy_default"
EXIT_POLICIES = (DEFAULT_EXIT_POLICY, "stop_target_only")


def validate_exit_policy(value):
    if value not in EXIT_POLICIES:
        raise ValueError("exit_policy must be strategy_default or stop_target_only")
    return value


def initial_state(execution):
    return {
        "index": -1,
        "cash": str(execution.initial_capital),
        "peak": str(execution.initial_capital),
        "drawdown": "0",
        "position": None,
        "pending": None,
        "opportunities": 0,
        "filled": 0,
        "occupied_bars": 0,
        "rejections": {},
        "last_timestamp": None,
        "processed_bars": 0,
    }


def source_state(state, signal, status):
    """An offered plan is not consumed until a fill; terminal fills stay spent.

    This state is research-only and lives in a new source-fingerprinted cohort.
    A retry requires a new decision-boundary evaluation of the same plan.
    """
    plan_id = signal.get("source_plan_id")
    if plan_id:
        state.setdefault("source_plan_states", {})[plan_id] = status


def reject(state, reason, signal, events):
    state["rejections"][reason] = state["rejections"].get(reason, 0) + 1
    events.append({"kind": "rejection", "reason": reason, "signal": signal})
    if signal.get("source_plan_id") not in state.get("seen_source_plans", []):
        source_state(state, signal, "rejected:" + reason)


def finish(state, price, when, reason, execution, events, *, ambiguous=False):
    position = state["position"]
    quantity = D(position["quantity"])
    fill = D(position["fill"])
    exit_fill = price * (1 - execution.impact)
    exit_fee = quantity * exit_fill * execution.fee
    entry_fee = D(position["entry_fee"])
    pnl = quantity * (exit_fill - fill) - entry_fee - exit_fee
    state["cash"] = str(D(state["cash"]) + quantity * exit_fill - exit_fee)
    events.append(
        {
            "kind": "trade",
            "signal_id": position["signal_id"],
            "decision": position["decision"],
            "actual_decision_time": position.get(
                "actual_decision_time", position["decision"]
            ),
            "entry_at": position["entry_at"],
            "exit_at": when.isoformat(),
            "entry": str(fill),
            "exit": str(exit_fill),
            "raw_exit": str(price),
            "stop": position["stop"],
            "target": position["target"],
            "quantity": str(quantity),
            "entry_fee": str(entry_fee),
            "exit_fee": str(exit_fee),
            "net_pnl": str(pnl),
            "net_r": str(pnl / (quantity * (fill - D(position["stop"])))),
            "initial_price_risk": str(quantity * (fill - D(position["stop"]))),
            "risk_budget": position["risk_budget"],
            "exit_reason": reason,
            "ambiguous": ambiguous,
            "held_bars": position["held"],
            "regime_at_decision": position["evidence"].get("regime", "unknown"),
            "equity_at_decision": position["equity_at_decision"],
        }
    )
    state["position"] = None
    source_state(state, position, "closed")


def step(
    previous,
    candles,
    index,
    strategy,
    execution,
    *,
    signal_enabled=True,
    recorded_at=None,
    decision_function=decide,
    exit_policy=DEFAULT_EXIT_POLICY,
):
    validate_exit_policy(exit_policy)
    state = deepcopy(previous)
    events = []
    c = candles[index]
    from trading_assistant.market_data.timeframes import timeframe_to_milliseconds

    interval = timedelta(milliseconds=timeframe_to_milliseconds(strategy.timeframe))
    known = c.timestamp + interval
    if state["last_timestamp"] is not None:
        from datetime import datetime

        if c.timestamp - datetime.fromisoformat(state["last_timestamp"]) != interval:
            # Price through the outage is unknowable. Mark any active trade as
            # unresolved, freeze promotion; do not insert a fabricated exit.
            was_occupied = state["position"] is not None
            if state["position"]:
                source_state(state, state["position"], "unresolved")
                events.append(
                    {
                        "kind": "unresolved",
                        "reason": "market_data_gap",
                        "position": state["position"],
                        "at": c.timestamp.isoformat(),
                    }
                )
                state["position"] = None
            if state["pending"]:
                reject(state, "data_gap_before_fill", state["pending"], events)
            state["pending"] = None
            if was_occupied:
                state["has_unresolved_gap"] = True
            state["data_gap_boundaries"] = state.get("data_gap_boundaries", 0) + 1
            events.append(
                {
                    "kind": "data_gap",
                    "from_open": previous["last_timestamp"],
                    "next_open": c.timestamp.isoformat(),
                    "position_unresolved": was_occupied,
                }
            )
    pending = state["pending"]
    state["pending"] = None
    if pending and pending.get("not_before"):
        from datetime import datetime

        if c.timestamp < datetime.fromisoformat(pending["not_before"]):
            state["pending"] = pending
            pending = None
    if pending:
        fill = c.open * (1 + execution.impact)
        qty = D(pending["quantity"])
        stop = D(pending["stop"])
        target = D(pending["target"])
        debit = qty * fill * (1 + execution.fee)
        if pending.get("source_plan_id") in state.get("seen_source_plans", []):
            reject(state, "source_plan_already_filled", pending, events)
        elif (
            c.open <= stop
            or fill > D(pending["entry_ceiling"])
            or fill <= stop
            or fill >= target
        ):
            reject(state, "entry_gap_or_invalid_levels", pending, events)
        elif qty * fill < execution.minimum_notional:
            reject(state, "entry_below_minimum_notional", pending, events)
        elif (
            debit > D(state["cash"])
            or qty * fill > D(pending["equity_at_decision"]) * execution.max_exposure
        ):
            reject(state, "capital_or_exposure_limit", pending, events)
        else:
            state["cash"] = str(D(state["cash"]) - debit)
            state["filled"] += 1
            plan_id = pending.get("source_plan_id")
            if plan_id:
                state.setdefault("seen_source_plans", []).append(plan_id)
                source_state(state, pending, "filled")
            state["position"] = {
                **pending,
                "fill": str(fill),
                "entry_fee": str(qty * fill * execution.fee),
                "entry_at": c.timestamp.isoformat(),
                "held": 0,
            }
    p = state["position"]
    if p:
        state["occupied_bars"] += 1
        p["held"] += 1
        stop = D(p["stop"])
        target = D(p["target"])
        if c.open <= stop:
            finish(state, c.open, known, "gap_stop", execution, events)
        elif c.open >= target:
            finish(
                state,
                target,
                known,
                "gap_target_no_favourable_improvement",
                execution,
                events,
            )
        elif c.low <= stop:
            finish(
                state,
                stop,
                known,
                "stop",
                execution,
                events,
                ambiguous=c.high >= target,
            )
        elif c.high >= target:
            finish(state, target, known, "target", execution, events)
        elif exit_policy == DEFAULT_EXIT_POLICY and p["held"] >= strategy.max_hold:
            finish(state, c.close, known, "time_exit", execution, events)
    p = state["position"]
    equity = D(state["cash"])
    if p:
        equity += (
            D(p["quantity"]) * c.close * (1 - execution.impact) * (1 - execution.fee)
        )
    if not state.get("has_unresolved_gap"):
        peak = max(D(state["peak"]), equity)
        state["peak"] = str(peak)
        state["drawdown"] = str(max(D(state["drawdown"]), (peak - equity) / peak))
    signal = (
        decision_function(
            candles[max(0, index - strategy.warmup + 1) : index + 1],
            strategy,
            known_at=known,
        )
        if signal_enabled
        else None
    )
    if signal:
        plan_id = signal.get("source_plan_id")
        offered = state.setdefault("source_plan_last_offered", {}) if plan_id else {}
        if plan_id and (
            plan_id in state.get("seen_source_plans", [])
            or offered.get(plan_id) == known.isoformat()
            or state["pending"] and state["pending"].get("source_plan_id") == plan_id
        ):
            signal = None
        elif plan_id:
            offered[plan_id] = known.isoformat()
    if signal:
        state["opportunities"] += 1
        signal_id = identity({"strategy": strategy.fingerprint, "signal": signal})
        events.append({"kind": "signal", "id": signal_id, **signal})
        if state["position"] or state["pending"]:
            reject(state, "occupied_slot", signal, events)
        elif state.get("has_unresolved_gap"):
            reject(state, "unresolved_gap_freezes_new_entries", signal, events)
        else:
            ceiling = (
                D(signal["reference"])
                * (1 + execution.max_entry_gap_bps / D(10000))
                * (1 + execution.impact)
            )
            stop = D(signal["stop"])
            budget = equity * execution.risk_fraction
            loss_per_unit = (
                ceiling
                - stop
                + execution.impact * stop
                + execution.fee * (ceiling + stop * (1 - execution.impact))
            )
            quantity = min(
                budget / loss_per_unit,
                equity * execution.max_exposure / ceiling,
                D(state["cash"]) / (ceiling * (1 + execution.fee)),
            )
            quantity = (quantity / execution.quantity_step).to_integral_value(
                rounding=ROUND_FLOOR
            ) * execution.quantity_step
            if quantity <= 0 or quantity * ceiling < execution.minimum_notional:
                reject(state, "insufficient_capital_or_lot_notional", signal, events)
            else:
                state["pending"] = {
                    **signal,
                    "signal_id": signal_id,
                    "decision": known.isoformat(),
                    "quantity": str(quantity),
                    "entry_ceiling": str(ceiling),
                    "risk_budget": str(budget),
                    "equity_at_decision": str(equity),
                }
                source_state(state, signal, "pending")
                if recorded_at is not None:
                    state["pending"]["not_before"] = recorded_at.isoformat()
                    state["pending"]["actual_decision_time"] = recorded_at.isoformat()
    state.update(
        index=index, last_timestamp=c.timestamp.isoformat(), equity=str(equity)
    )
    state.setdefault("first_timestamp", c.timestamp.isoformat())
    state["interval_seconds"] = int(interval.total_seconds())
    events.append(
        {
            "kind": "equity_mark",
            "known_at": known.isoformat(),
            "liquidation_equity": None
            if state.get("has_unresolved_gap")
            else str(equity),
            "occupied": state["position"] is not None,
        }
    )
    state["processed_bars"] = state.get("processed_bars", previous["index"] + 1) + 1
    return state, events


def metrics(state, records, execution):
    from datetime import datetime

    duration_hours = None
    if state.get("first_timestamp") and state["last_timestamp"]:
        duration_hours = D(
            str(
                (
                    datetime.fromisoformat(state["last_timestamp"])
                    - datetime.fromisoformat(state["first_timestamp"])
                ).total_seconds()
                + state["interval_seconds"]
            )
        ) / D(3600)
    trades = sorted(
        (r for r in records if r["kind"] == "trade"),
        key=lambda trade: (trade["entry_at"], trade["signal_id"]),
    )
    pnl = [D(r["net_pnl"]) for r in trades]
    wins = [v for v in pnl if v > 0]
    losses = [v for v in pnl if v < 0]
    r = [D(t["net_r"]) for t in trades]
    ambiguous = sum(t["ambiguous"] for t in trades)
    equity = D(state.get("equity", execution.initial_capital))
    net = (equity - execution.initial_capital) / execution.initial_capital
    excluded = sum(rec["kind"] == "unresolved" for rec in records)
    return {
        "opportunities": state["opportunities"],
        "filled_trades": state["filled"],
        "closed_trades": len(trades),
        "open_positions": int(state["position"] is not None),
        "pending_entries": int(state["pending"] is not None),
        "ambiguous_adverse_trades": ambiguous,
        "unresolved_gap_positions": excluded,
        "rejections": state["rejections"],
        "win_rate": None if not trades else str(D(len(wins)) / len(trades)),
        "average_win": None if not wins else str(sum(wins) / len(wins)),
        "average_loss": None if not losses else str(sum(losses) / len(losses)),
        "expectancy_r": None if not r else str(sum(r) / len(r)),
        "profit_factor": None if not losses else str(sum(wins) / -sum(losses)),
        "profit_factor_reason": None if losses else "no_losing_trades_or_no_sample",
        "liquidation_marked_equity": None if excluded else str(equity),
        "net_return": None if excluded else str(net),
        "maximum_close_mark_drawdown": None if excluded else state["drawdown"],
        "return_to_drawdown": None
        if excluded or D(state["drawdown"]) == 0
        else str(net / D(state["drawdown"])),
        "exposure_fraction": None
        if excluded
        else str(
            D(state["occupied_bars"])
            / max(1, state.get("processed_bars", state["index"] + 1))
        ),
        "sample_size": len(trades),
        "observed_period_hours": None
        if duration_hours is None
        else str(duration_hours),
        "closed_trades_per_30_days": None
        if not duration_hours
        else str(D(len(trades)) * 720 / duration_hours),
        "net_r_values": [str(x) for x in r],
        "assumptions": "cash Spot long-only; next-open market entry; adverse spread/slippage; fees on both notionals; both stop/target => adverse stop; gaps unresolved; close-mark drawdown only",
        "status": "rejected"
        if excluded or len(trades) < 100 or not r or sum(r) <= 0
        else "awaiting_validation",
        "rejection_reasons": [
            reason
            for applies, reason in (
                (excluded > 0, "unresolved_data"),
                (len(trades) < 100, "insufficient_sample"),
                (bool(r) and sum(r) <= 0, "nonpositive_net_expectancy"),
            )
            if applies
        ],
    }
