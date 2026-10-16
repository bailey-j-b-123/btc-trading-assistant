"""Conservative chronological cash simulation. No order or account interface."""

from copy import deepcopy
from dataclasses import asdict
import json
from datetime import timedelta
from decimal import Decimal as D, ROUND_FLOOR
from .models import identity, canonical
from .strategies import decide
from .entry_policy import entry_definition

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


def finish(state, price, when, reason, execution, events, *, ambiguous=False, directional_model=None):
    position = state["position"]
    quantity = D(position["quantity"])
    fill = D(position["fill"])
    sign = int(position.get("side", 1)) if directional_model else 1
    exit_fill = price * (1 - sign * execution.impact)
    exit_fee = quantity * exit_fill * execution.fee
    entry_fee = D(position["entry_fee"])
    if directional_model is None:
        pnl = quantity * (exit_fill - fill) - entry_fee - exit_fee
        state["cash"] = str(D(state["cash"]) + quantity * exit_fill - exit_fee)
    else:
        funding = D(position["funding"])
        carry = D(position["carry"])
        clearance = quantity * exit_fill * directional_model.liquidation_fee_bps / 10000 if reason == "liquidation" else D(0)
        pnl = sign * quantity * (exit_fill - fill) - entry_fee - exit_fee + funding - carry - clearance
        settlement = D(position["collateral"]) + pnl
        state["cash"] = str(D(state["cash"]) + settlement)
        if settlement < 0:
            state["collateral_deficit"] = str(D(state.get("collateral_deficit", "0")) - settlement)
            state["has_insolvency"] = True
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
            "net_r": str(pnl / (quantity * abs(fill - D(position["stop"])))),
            "initial_price_risk": str(quantity * abs(fill - D(position["stop"]))),
            "risk_budget": position["risk_budget"],
            "exit_reason": reason,
            "ambiguous": ambiguous,
            "held_bars": position["held"],
            "regime_at_decision": position["evidence"].get("regime", "unknown"),
            "equity_at_decision": position["equity_at_decision"],
        }
    )
    if directional_model is not None:
        events[-1].update(direction="LONG" if sign == 1 else "SHORT", collateral=position["collateral"],
                          funding=position["funding"], carry=position["carry"], liquidation_fee=str(clearance),
                          collateral_settlement=str(settlement), funding_reserve=position["funding_reserve"],
                          execution_model=directional_model.version, hold_limit=position["hold_limit"])
    if "entry_policy" in position:
        events[-1].update(entry_policy=position["entry_policy"],
                          entry_limit=position["entry_limit"],
                          waited_bars=position["waited_bars"])
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
    entry_policy=None,
    directional_model=None,
    mark_candle=None,
    funding_event=None,
):
    validate_exit_policy(exit_policy)
    entry_spec = entry_definition(entry_policy)
    state = deepcopy(previous)
    events = []
    c = candles[index]
    from trading_assistant.market_data.timeframes import timeframe_to_milliseconds

    interval = timedelta(milliseconds=timeframe_to_milliseconds(strategy.timeframe))
    known = c.timestamp + interval
    if directional_model is not None:
        from .directional import DirectionalModel, FundingEvent
        if not isinstance(directional_model, DirectionalModel):
            raise ValueError("typed directional model required")
        directional_model.validate(execution, strategy, exit_policy, entry_policy)
        if state.get("directional_model", directional_model.fingerprint) != directional_model.fingerprint:
            raise ValueError("directional checkpoint model mismatch")
        if index <= previous["index"]:
            raise ValueError("directional boundary already processed or out of order")
        state["directional_model"] = directional_model.fingerprint
        if mark_candle is None:
            if directional_model.mark_source != "trade_price_proxy_assumption":
                raise ValueError("recorded mark candle required")
            mark_candle = c
        if (mark_candle.timestamp != c.timestamp or mark_candle.timeframe != c.timeframe
                or (mark_candle.exchange, mark_candle.symbol) != (c.exchange, c.symbol)
                or not 0 < mark_candle.low <= min(mark_candle.open, mark_candle.close)
                <= max(mark_candle.open, mark_candle.close) <= mark_candle.high):
            raise ValueError("invalid aligned mark candle")
        if funding_event is not None and (not isinstance(funding_event, FundingEvent) or funding_event.at != c.timestamp):
            raise ValueError("funding event must have verified mark and align to bar open")
    elif state.get("directional_model") or mark_candle is not None or funding_event is not None:
        raise ValueError("directional state/inputs require explicit opt-in")
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
    # Scheduled funding belongs to the position held BEFORE this open. New
    # next-open fills do not capture a same-instant favourable payment.
    if directional_model is not None and state["position"] and funding_event is not None:
        p = state["position"]
        amount = -int(p["side"]) * D(p["quantity"]) * funding_event.mark * funding_event.rate
        p["funding"] = str(D(p["funding"]) + amount)
        p["funding_debits"] = str(D(p["funding_debits"]) + max(D(0), -amount))
        events.append({"kind": "funding", "signal_id": p["signal_id"], "at": funding_event.at.isoformat(),
                       "rate": str(funding_event.rate), "mark": str(funding_event.mark), "amount": str(amount)})
    pending = state["pending"]
    state["pending"] = None
    if pending and pending.get("not_before"):
        from datetime import datetime

        if c.timestamp < datetime.fromisoformat(pending["not_before"]):
            state["pending"] = pending
            pending = None
    if pending and pending.get("entry_policy") != entry_spec:
        raise ValueError("pending entry policy differs from checkpoint")
    if pending and entry_policy is not None:
        pending["waited_bars"] += 1
        limit = D(pending["entry_limit"])
        if c.open * (1 + execution.impact) > limit:
            # No position: these are cancellation observations, never exits.
            reason = (
                "pullback_structural_invalidation" if c.low <= D(pending["stop"])
                else "pullback_target_before_entry" if c.high >= D(pending["target"])
                else "pullback_timeout" if pending["waited_bars"] >= entry_policy.max_wait_bars
                else None
            )
            if reason:
                reject(state, reason, pending, events)
                events[-1]["cancelled_at"] = known.isoformat()
            else:
                state["pending"] = pending
                events.append({"kind": "entry_wait", "signal_id": pending["signal_id"],
                               "known_at": known.isoformat(), "entry_limit": str(limit),
                               "waited_bars": pending["waited_bars"]})
            pending = None
    if pending:
        sign = int(pending.get("side", 1)) if directional_model else 1
        fill = c.open * (1 + sign * execution.impact)
        qty = D(pending["quantity"])
        stop = D(pending["stop"])
        target = D(pending["target"])
        debit = qty * fill * (1 + execution.fee)
        invalid = (c.open <= stop or fill > D(pending["entry_ceiling"]) or fill <= stop or fill >= target)
        if directional_model is not None:
            invalid = (sign * (c.open - stop) <= 0 or sign * (fill - stop) <= 0
                       or sign * (target - fill) <= 0
                       or sign * (fill - D(pending["entry_bound"])) > 0)
            # Lock full notional PLUS entry fee; no spendable short proceeds.
            if qty * (abs(fill - stop * (1 - sign * execution.impact))
                      + execution.fee * (fill + stop * (1 - sign * execution.impact))) + D(pending["funding_reserve"]) > D(pending["risk_budget"]):
                invalid = True
        if pending.get("source_plan_id") in state.get("seen_source_plans", []):
            reject(state, "source_plan_already_filled", pending, events)
        elif invalid:
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
            if directional_model is not None:
                state["position"].update(collateral=str(qty * fill * (1 + execution.fee)), funding="0", carry="0", funding_debits="0")
    p = state["position"]
    if p:
        state["occupied_bars"] += 1
        p["held"] += 1
        stop = D(p["stop"])
        target = D(p["target"])
        if directional_model is not None:
            sign = int(p["side"])
            carry = D(p["quantity"]) * max(D(p["fill"]), mark_candle.high, c.high) * directional_model.carry_bps_per_day / 10000 * D(str(interval.total_seconds())) / 86400
            p["carry"] = str(D(p["carry"]) + carry)
            if D(p["carry"]) + D(p["funding_debits"]) > D(p["funding_reserve"]):
                state["has_cost_breach"] = True
            events.append({"kind": "carry", "signal_id": p["signal_id"], "at": known.isoformat(), "amount": str(carry)})
            # Reserve clearance at the worst mark. If liquidation and a stop
            # could both occur, never assume the protective exit happened first.
            worst_mark = mark_candle.low if sign == 1 else mark_candle.high
            margin = (D(p["collateral"]) - D(p["entry_fee"]) + D(p["funding"]) - D(p["carry"])
                      + sign * D(p["quantity"]) * (worst_mark - D(p["fill"])))
            required = D(p["quantity"]) * worst_mark * (directional_model.maintenance_rate + directional_model.liquidation_fee_bps / 10000)
            if margin <= required:
                raw = min(c.low, worst_mark) if sign == 1 else max(c.high, worst_mark)
                finish(state, raw, known, "liquidation", execution, events, ambiguous=True, directional_model=directional_model)
            elif sign * (c.open - stop) <= 0:
                finish(state, c.open, known, "gap_stop", execution, events, directional_model=directional_model)
            elif sign * (c.open - target) >= 0:
                finish(state, target, known, "gap_target_no_favourable_improvement", execution, events, directional_model=directional_model)
            elif (c.low <= stop if sign == 1 else c.high >= stop):
                finish(state, stop, known, "stop", execution, events,
                       ambiguous=c.high >= target if sign == 1 else c.low <= target, directional_model=directional_model)
            elif (c.high >= target if sign == 1 else c.low <= target):
                finish(state, target, known, "target", execution, events, directional_model=directional_model)
            elif p["held"] >= strategy.max_hold:
                finish(state, c.close, known, "time_exit", execution, events, directional_model=directional_model)
        elif c.open <= stop:
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
        if directional_model is None:
            equity += D(p["quantity"]) * c.close * (1 - execution.impact) * (1 - execution.fee)
        else:
            sign = int(p["side"])
            # Mark unrealised value with an adverse exit reserve. Collateral is
            # a segregated part of THIS account, never an additional account.
            exit_mark = mark_candle.close * (1 - sign * execution.impact)
            equity += (D(p["collateral"]) - D(p["entry_fee"]) + D(p["funding"]) - D(p["carry"])
                       + sign * D(p["quantity"]) * (exit_mark - D(p["fill"]))
                       - D(p["quantity"]) * exit_mark * execution.fee)
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
    if signal and signal.get("direction") == "NO_TRADE":
        signal = None
    if signal:
        if directional_model is not None:
            from .directional import validate_signal
            validate_signal(signal, c, known)
        elif signal.get("direction", "LONG") != "LONG":
            raise ValueError("cash Spot supports LONG only; directional opt-in required")
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
        elif state.get("has_unresolved_gap") or state.get("has_insolvency") or state.get("has_cost_breach"):
            reject(state, "unresolved_gap_or_cost_or_insolvency_freezes_entries" if directional_model else "unresolved_gap_freezes_new_entries", signal, events)
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
            if directional_model is not None:
                from .directional import direction
                sign = direction(signal)
                reference = D(signal["reference"])
                bound = reference * (1 + sign * execution.max_entry_gap_bps / 10000) * (1 + sign * execution.impact)
                collateral_ceiling = reference * (1 + execution.max_entry_gap_bps / 10000) * (1 + execution.impact)
                stop_exit = stop * (1 - sign * execution.impact)
                reserve_per_unit = max(collateral_ceiling, stop, D(signal["target"])) * (directional_model.carry_bps_per_day + directional_model.funding_reserve_bps_per_day) / 10000 * strategy.max_hold * D(str(interval.total_seconds())) / 86400
                loss_per_unit = sign * (bound - stop_exit) + execution.fee * (bound + stop_exit) + reserve_per_unit
                if not bound.is_finite() or bound <= 0 or loss_per_unit <= 0 or sign * (bound - stop) <= 0:
                    raise ValueError("invalid directional sizing bound")
                ceiling = collateral_ceiling
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
                if directional_model is not None:
                    state["pending"].update(side=sign, direction="LONG" if sign == 1 else "SHORT",
                                            entry_bound=str(bound), funding_reserve=str(quantity * reserve_per_unit), hold_limit=strategy.max_hold)
                if entry_policy is not None:
                    state["pending"].update(
                        entry_policy=entry_spec,
                        entry_limit=str(entry_policy.limit(signal)), waited_bars=0,
                    )
                source_state(state, signal, "pending")
                if recorded_at is not None:
                    state["pending"]["not_before"] = recorded_at.isoformat()
                    state["pending"]["actual_decision_time"] = recorded_at.isoformat()
                if entry_policy is not None:
                    events.append({"kind": "entry_intent", "signal_id": signal_id,
                                   "known_at": known.isoformat(),
                                   "pending": deepcopy(state["pending"])})
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


def metrics(state, records, execution, *, entry_policy=None, directional_model=None):
    entry_definition(entry_policy)
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
    if bool(state.get("directional_model")) != bool(directional_model):
        raise ValueError("metrics require the recorded directional contract")
    result = {
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
        "assumptions": ("research pullback capped next-open entry; " if entry_policy else "") + "cash Spot long-only; next-open market entry; adverse spread/slippage; fees on both notionals; both stop/target => adverse stop; gaps unresolved; close-mark drawdown only",
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

    if directional_model is not None:
        result.update(execution_model=directional_model.version, assumptions="1x segregated collateral; declared carry/funding; conservative worst-mark liquidation before stop; full deficits debited and frozen; NOT verified exchange execution",
                      directional_model=json.loads(canonical(asdict(directional_model))),
                      funding=str(sum((D(t["amount"]) for t in records if t["kind"] == "funding"), D(0))),
                      carry=str(sum((D(t["amount"]) for t in records if t["kind"] == "carry"), D(0))),
                      paid_trade_fees=str(sum((D(t["entry_fee"]) + D(t["exit_fee"]) for t in trades), D(0)) + (D(state["position"]["entry_fee"]) if state["position"] else D(0)) + sum((D(t["position"]["entry_fee"]) for t in records if t["kind"] == "unresolved"), D(0))),
                      free_cash=state["cash"],
                      locked_collateral=str((D(state["position"]["collateral"]) if state["position"] else D(0)) + sum((D(t["position"]["collateral"]) for t in records if t["kind"] == "unresolved"), D(0))),
                      liquidation_fees=str(sum((D(t["liquidation_fee"]) for t in trades), D(0))),
                      liquidations=sum(t["exit_reason"] == "liquidation" for t in trades),
                      collateral_deficit=state.get("collateral_deficit", "0"),
                      by_direction={d: {"closed": sum(t["direction"] == d for t in trades), "net_pnl": str(sum((D(t["net_pnl"]) for t in trades if t["direction"] == d), D(0)))} for d in ("LONG", "SHORT")})
        if state.get("has_insolvency"):
            result.update(status="rejected", rejection_reasons=result["rejection_reasons"] + ["collateral_deficit"], net_return=None)
        if state.get("has_cost_breach"):
            result.update(status="rejected", rejection_reasons=result["rejection_reasons"] + ["declared_carry_funding_reserve_exceeded"], net_return=None)
    return result
