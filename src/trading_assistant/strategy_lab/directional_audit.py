"""Independent Decimal60 reconciliation for the SAME runner's opt-in ledger.

No simulation/finance-helper calls. Reconstruct fills, costs, equity and risk
from recorded plans, candles, funding inputs and declared contract assumptions.
"""

from datetime import datetime, timedelta
from decimal import Decimal as D, localcontext, ROUND_FLOOR


def audit(
    state, records, candles, execution, model, *, mark_candles=None, funding_events=()
):
    market = {c.timestamp: c for c in candles}
    marks = market if mark_candles is None else {c.timestamp: c for c in mark_candles}
    signals = {r["id"]: r for r in records if r["kind"] == "signal"}
    trades = sorted(
        (r for r in records if r["kind"] == "trade"), key=lambda r: r["entry_at"]
    )
    unresolved = [r["position"] for r in records if r["kind"] == "unresolved"]
    positions = trades + unresolved + ([state["position"]] if state["position"] else [])
    assert len({p["signal_id"] for p in positions}) == len(positions)
    assert all(
        r["signal_id"] in {p["signal_id"] for p in positions}
        for r in records
        if r["kind"] in {"funding", "carry"}
    )
    dt = datetime.fromisoformat
    interval = timedelta(seconds=state["interval_seconds"])
    with localcontext() as context:
        context.prec = 60
        impact = (execution.spread_bps / 2 + execution.slippage_bps) / 10000
        fee = execution.fee_bps / 10000
        maximum = D(0)

        def near(a, b):
            nonlocal maximum
            error = abs(D(a) - D(b))
            maximum = max(maximum, error)
            assert error < D("1e-18"), (a, b)

        cash = execution.initial_capital
        prior_exit = None
        for p in sorted(positions, key=lambda r: r["entry_at"]):
            start = dt(p["entry_at"])
            assert prior_exit is None or start >= prior_exit
            prior_exit = (
                dt(p["exit_at"])
                if "exit_at" in p
                else datetime.max.replace(tzinfo=start.tzinfo)
            )
            sign = 1 if p.get("direction") == "LONG" else -1
            assert p.get("direction") in {"LONG", "SHORT"}
            q = D(p["quantity"])
            entry = D(p.get("entry", p.get("fill")))
            stop, target = D(p["stop"]), D(p["target"])
            signal = signals[p["signal_id"]]
            assert dt(signal["known_at"]) == start
            assert D(signal["reference"]) == market[start - interval].close
            assert (stop, target) == (D(signal["stop"]), D(signal["target"]))
            assert sign * (entry - stop) > 0 and sign * (target - entry) > 0
            near(entry, market[start].open * (1 + sign * impact))
            near(p["entry_fee"], q * entry * fee)
            near(p["collateral"], q * entry * (1 + fee))
            assert q > 0 and q % execution.quantity_step == 0
            assert (
                execution.minimum_notional
                <= q * entry
                <= D(p["equity_at_decision"]) * execution.max_exposure
            )
            near(p["equity_at_decision"], cash)
            near(p["risk_budget"], D(p["equity_at_decision"]) * execution.risk_fraction)
            assert execution.risk_fraction <= D(".005") and execution.max_exposure <= D(
                ".25"
            )
            assert q * (
                abs(entry - stop * (1 - sign * impact))
                + fee * (entry + stop * (1 - sign * impact))
            ) + D(p["funding_reserve"]) <= D(p["risk_budget"]) + D("1e-18")
            # Independent frozen quantity: metadata already records the reserve
            # per chosen quantity, never increase size after a cheaper fill.
            ref = D(signal["reference"])
            bound = (
                ref
                * (1 + sign * execution.max_entry_gap_bps / 10000)
                * (1 + sign * impact)
            )
            ceiling = ref * (1 + execution.max_entry_gap_bps / 10000) * (1 + impact)
            reserve_per_unit = (
                max(ceiling, stop, target)
                * (model.carry_bps_per_day + model.funding_reserve_bps_per_day)
                / 10000
                * p["hold_limit"]
                * D(str(interval.total_seconds()))
                / 86400
            )
            near(p["funding_reserve"], q * reserve_per_unit)
            # Match the public Decimal28 contract only at lot-floor arithmetic;
            # all independent cash/fee/PnL reconciliation above/below uses60digits.
            with localcontext() as lot_context:
                lot_context.prec = 28
                perunit = (
                    sign * (bound - stop * (1 - sign * impact))
                    + fee * (bound + stop * (1 - sign * impact))
                    + reserve_per_unit
                )
                size = min(
                    D(p["risk_budget"]) / perunit,
                    D(p["equity_at_decision"]) * execution.max_exposure / ceiling,
                    D(p["equity_at_decision"]) / (ceiling * (1 + fee)),
                )
                size = (size / execution.quantity_step).to_integral_value(
                    rounding=ROUND_FLOOR
                ) * execution.quantity_step
            assert size == q
            funding = [
                r
                for r in records
                if r["kind"] == "funding" and r["signal_id"] == p["signal_id"]
            ]
            carry = [
                r
                for r in records
                if r["kind"] == "carry" and r["signal_id"] == p["signal_id"]
            ]
            assert len({r["at"] for r in funding}) == len(funding)
            assert len({r["at"] for r in carry}) == len(carry)
            unresolved_end = next(
                (
                    dt(r["at"])
                    for r in records
                    if r["kind"] == "unresolved"
                    and r["position"]["signal_id"] == p["signal_id"]
                ),
                None,
            )
            funding_end = (
                dt(p["exit_at"])
                if "exit_at" in p
                else unresolved_end or dt(state["last_timestamp"]) + interval
            )
            assert {dt(r["at"]) for r in carry} == {
                c.timestamp + interval
                for c in candles
                if start <= c.timestamp < funding_end
            }
            expected_funding = {
                e.at for e in funding_events if start < e.at < funding_end
            }
            assert {dt(f["at"]) for f in funding} == expected_funding
            for f in funding:
                event = next(e for e in funding_events if e.at == dt(f["at"]))
                assert event.at > start and (
                    "exit_at" not in p or event.at < dt(p["exit_at"])
                )
                near(f["rate"], event.rate)
                near(f["mark"], event.mark)
                near(f["amount"], -sign * q * event.mark * event.rate)
            for r in carry:
                c, m = market[dt(r["at"]) - interval], marks[dt(r["at"]) - interval]
                near(
                    r["amount"],
                    q
                    * max(entry, c.high, m.high)
                    * model.carry_bps_per_day
                    / 10000
                    * D(str(interval.total_seconds()))
                    / 86400,
                )
            near(p["funding"], sum((D(r["amount"]) for r in funding), D(0)))
            near(p["carry"], sum((D(r["amount"]) for r in carry), D(0)))
            if "exit_at" not in p:
                cash -= D(p["collateral"])
                continue
            end = dt(p["exit_at"])
            c, m = market[end - interval], marks[end - interval]
            assert int((end - start) / interval) == p["held_bars"] == len(carry)
            raw, exit = D(p["raw_exit"]), D(p["exit"])
            near(exit, raw * (1 - sign * impact))
            near(p["exit_fee"], q * exit * fee)
            clearance = (
                q * exit * model.liquidation_fee_bps / 10000
                if p["exit_reason"] == "liquidation"
                else D(0)
            )
            near(p["liquidation_fee"], clearance)
            value = (
                sign * q * (exit - entry)
                - q * (entry + exit) * fee
                + D(p["funding"])
                - D(p["carry"])
                - clearance
            )
            near(value, p["net_pnl"])
            near(p["net_r"], value / (q * abs(entry - stop)))
            near(p["collateral_settlement"], D(p["collateral"]) + value)
            worst = m.low if sign == 1 else m.high
            margin = (
                D(p["collateral"])
                - D(p["entry_fee"])
                + D(p["funding"])
                - D(p["carry"])
                + sign * q * (worst - entry)
            )
            threshold = (
                q * worst * (model.maintenance_rate + model.liquidation_fee_bps / 10000)
            )
            reason = p["exit_reason"]
            if reason == "liquidation":
                assert margin <= threshold
                assert raw == (min(c.low, worst) if sign == 1 else max(c.high, worst))
            else:
                assert margin > threshold
                if reason == "gap_stop":
                    assert sign * (c.open - stop) <= 0 and raw == c.open
                elif reason == "gap_target_no_favourable_improvement":
                    assert sign * (c.open - target) >= 0 and raw == target
                elif reason == "stop":
                    assert (
                        c.low <= stop if sign == 1 else c.high >= stop
                    ) and raw == stop
                elif reason == "target":
                    assert (
                        c.high >= target and c.low > stop
                        if sign == 1
                        else c.low <= target and c.high < stop
                    ) and raw == target
                else:
                    assert (
                        reason == "time_exit"
                        and raw == c.close
                        and p["held_bars"] == p["hold_limit"]
                    )
            cash += value
        near(cash, state["cash"])
        peak, drawdown = execution.initial_capital, D(0)
        marks_checked = 0
        for m in sorted(
            (r for r in records if r["kind"] == "equity_mark"),
            key=lambda r: r["known_at"],
        ):
            if m["liquidation_equity"] is None:
                continue
            at = dt(m["known_at"])
            value = execution.initial_capital + sum(
                (D(p["net_pnl"]) for p in trades if dt(p["exit_at"]) <= at), D(0)
            )
            active = [
                p
                for p in positions
                if dt(p["entry_at"]) < at
                and ("exit_at" not in p or at < dt(p["exit_at"]))
            ]
            assert len(active) <= 1
            for p in active:
                sign = 1 if p["direction"] == "LONG" else -1
                q = D(p["quantity"])
                exitmark = marks[at - interval].close * (1 - sign * impact)
                value += (
                    sign * q * (exitmark - D(p.get("entry", p.get("fill"))))
                    - D(p["entry_fee"])
                    - q * exitmark * fee
                )
                value += sum(
                    (
                        D(r["amount"])
                        for r in records
                        if r["kind"] == "funding"
                        and r["signal_id"] == p["signal_id"]
                        and dt(r["at"]) < at
                    ),
                    D(0),
                )
                value -= sum(
                    (
                        D(r["amount"])
                        for r in records
                        if r["kind"] == "carry"
                        and r["signal_id"] == p["signal_id"]
                        and dt(r["at"]) <= at
                    ),
                    D(0),
                )
            near(value, m["liquidation_equity"])
            peak = max(peak, value)
            drawdown = max(drawdown, (peak - value) / peak)
            marks_checked += 1
        if not unresolved:
            near(drawdown, state["drawdown"])
        return {
            "trades_checked": len(trades),
            "marks_checked": marks_checked,
            "maximum_currency_error": str(maximum),
            "cash_error": str(abs(cash - D(state["cash"]))),
            "precision": 60,
            "unresolved_unscored": len(unresolved),
            "execution_model": model.version,
            "assumptions": "independently reconciled declared research contract; NOT exchange verification",
        }
