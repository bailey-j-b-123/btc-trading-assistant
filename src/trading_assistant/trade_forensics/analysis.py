"""Read-only forensic reconstruction of one recorded paper trade.

This module explains what BRAIN already recorded. It does not re-run detectors,
re-qualify setups, re-plan trades, backfill candles, or change any row. It reads:

* the forward ledger, through the existing :class:`ForwardLedgerRepository`
  read API and the stored Step 5/6 JSON (``setup_json``, ``plan_json``);
* stored closed OHLCV candles, at exact open times;
* the stored hierarchy and runner-heartbeat rows that exist.

Anti-lookahead rule. Every *decision* section (identity, breakout, retest,
state timeline, entry, timeframe context, competing setups/slot) uses only
ledger rows with ``as_of`` or ``recorded_at`` at or before the planning instant
and only candles whose full interval closed at or before it. The one section
that uses later data is the outcome section, which is labelled
``post_decision``: the stop was, by definition, observed after the entry.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from trading_assistant.forward_testing.models import (
    ForwardCycle,
    ForwardObservation,
    PaperOutcome,
    PaperPlan,
    paper_trade_occupies_slot,
)
from trading_assistant.forward_testing.parameters import PAPER_TRADE_ACTIVE_REASON
from trading_assistant.forward_testing.repository import ForwardLedgerRepository
from trading_assistant.forward_testing.tables import ForwardRunnerHeartbeatRow
from trading_assistant.journaling.types import OutcomeEventKind, OutcomeEventOrdering
from trading_assistant.market_data.integrity import (
    required_trailing_depth,
    required_window,
)
from trading_assistant.market_data.models import OHLCVCandleRecord
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.multi_timeframe.tables import ForwardHierarchyObservationRow
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.trade_forensics.models import Fact, Section, Table, TradeReport

#: Timeframes whose stored coverage is reported. 1H is the base; the others are
#: the Step 13 hierarchy layers (4H context, 15M confirmation, 5M execution).
CONTEXT_TIMEFRAMES: tuple[str, ...] = ("4h", "1h", "15m", "5m")

#: Rows of hierarchy evaluations shown at or before the signal.
HIERARCHY_ROWS_SHOWN = 10

ENTRY_RULE_TEXT = (
    "Entry is the close of the latest closed candle at the planning boundary "
    "(Step 6 `plan_close_level`, verbatim copy, no transformation). The planner "
    "does not search for a pullback, a lower limit, or a retest price."
)

STOP_RULE_TEXT = (
    "Stop equals the reference band's lower side with no buffer "
    "(stop_buffer_mode none), so the stop is the invalidation level."
)


class PaperPlanNotFound(LookupError):
    """No stored paper plan matches the requested trade."""


class PaperPlanAmbiguous(LookupError):
    """More than one stored paper plan matches; a paper-plan id is required."""


class LedgerNotMigrated(RuntimeError):
    """The database has no forward-ledger tables."""


@dataclass(frozen=True, slots=True)
class PlanQuery:
    exchange: str
    symbol: str
    timeframe: str
    signal_time: datetime
    entry: Decimal
    stop: Decimal
    target: Decimal
    paper_plan_id: str | None = None
    expected_outcome: str | None = "STOPPED"


@dataclass(frozen=True, slots=True)
class _Candle:
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


# ----------------------------------------------------------------------------
# Small formatting helpers
# ----------------------------------------------------------------------------


def _ts(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _parse_ts(text_value: str | None) -> datetime | None:
    if text_value is None:
        return None
    return datetime.fromisoformat(text_value).astimezone(UTC)


def _enum(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _pct(part: Decimal, whole: Decimal) -> str:
    return format((part / whole * 100).quantize(Decimal("0.0001"), ROUND_HALF_UP), "f")


def _short(identifier: str | None) -> str:
    return "—" if identifier is None else identifier[:12]


def _json(text_value: str | None) -> Any:
    if text_value is None:
        return None
    return json.loads(text_value)


# ----------------------------------------------------------------------------
# Locating the paper plan
# ----------------------------------------------------------------------------


def locate_paper_plan(engine: Engine, query: PlanQuery) -> PaperPlan:
    """The single stored paper plan that matches the requested trade exactly.

    Matching is exact: the planning instant, base timeframe, entry, stop and
    one target must all equal the stored values (Decimal equality). Nothing is
    rounded or nearest-matched.
    """

    repo = ForwardLedgerRepository(engine)
    plans = repo.paper_plans(
        exchange=query.exchange, symbol=query.symbol, timeframe=None
    )
    matches = [
        plan
        for plan in plans
        if plan.timeframe == query.timeframe
        and plan.plan_as_of == query.signal_time
        and plan.entry == query.entry
        and plan.stop == query.stop
        and query.target in plan.targets
        and (query.paper_plan_id is None or plan.paper_plan_id == query.paper_plan_id)
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(plan.paper_plan_id for plan in matches)
        raise PaperPlanAmbiguous(
            f"{len(matches)} stored paper plans match exactly; pass --paper-plan-id "
            f"to choose one: {ids}"
        )
    nearby = [
        plan
        for plan in plans
        if plan.plan_as_of == query.signal_time or plan.entry == query.entry
    ]
    detail = "; ".join(
        f"{_short(plan.paper_plan_id)} {plan.timeframe} as_of={_ts(plan.plan_as_of)} "
        f"entry={_dec(plan.entry)} stop={_dec(plan.stop)} "
        f"targets={[_dec(t) for t in plan.targets]}"
        for plan in nearby[:10]
    )
    raise PaperPlanNotFound(
        "no stored paper plan matches "
        f"timeframe={query.timeframe} signal={_ts(query.signal_time)} "
        f"entry={_dec(query.entry)} stop={_dec(query.stop)} target={_dec(query.target)}"
        + (
            f". Nearby stored plans: {detail}"
            if detail
            else ". No stored plan shares the signal instant or the entry level."
        )
    )


# ----------------------------------------------------------------------------
# Context loading (read-only; decision inputs are bounded at the signal)
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Context:
    engine: Engine
    query: PlanQuery
    plan: PaperPlan
    plan_obs: ForwardObservation
    setup_obs: tuple[ForwardObservation, ...]
    cycle_obs: tuple[ForwardObservation, ...]
    cycle: ForwardCycle | None
    cycles_up_to_signal: int
    ledger_start: datetime | None
    setup: dict[str, Any]
    plan_doc: dict[str, Any]
    interval: timedelta

    @property
    def signal(self) -> datetime:
        return self.plan.plan_as_of


def _load_context(engine: Engine, query: PlanQuery, plan: PaperPlan) -> _Context:
    repo = ForwardLedgerRepository(engine)
    observations = repo.observations(
        exchange=plan.exchange, symbol=plan.symbol, timeframe=plan.timeframe
    )
    plan_obs = next(
        (o for o in observations if o.observation_id == plan.observation_id), None
    )
    if plan_obs is None:
        raise LookupError(
            f"frozen observation {plan.observation_id} is not stored for paper plan "
            f"{plan.paper_plan_id}; the ledger is inconsistent and nothing was inferred"
        )
    signal = plan.plan_as_of
    setup_obs = tuple(
        sorted(
            (
                o
                for o in observations
                if o.setup_id == plan.setup_id and o.as_of <= signal
            ),
            key=lambda o: (o.as_of, o.recorded_at, o.observation_id),
        )
    )
    cycle_obs = tuple(o for o in observations if o.cycle_id == plan.cycle_id)
    cycles = repo.cycles(
        exchange=plan.exchange, symbol=plan.symbol, timeframe=plan.timeframe
    )
    cycle = next((c for c in cycles if c.cycle_id == plan.cycle_id), None)
    ledger_start = min((c.as_of for c in cycles), default=None)
    return _Context(
        engine=engine,
        query=query,
        plan=plan,
        plan_obs=plan_obs,
        setup_obs=setup_obs,
        cycle_obs=cycle_obs,
        cycle=cycle,
        cycles_up_to_signal=sum(1 for c in cycles if c.as_of <= signal),
        ledger_start=ledger_start,
        setup=_json(plan_obs.setup_json) or {},
        plan_doc=_json(plan_obs.plan_json) or {},
        interval=interval_for_timeframe(plan.timeframe),
    )


def _rule(setup: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    return next(
        (r for r in setup.get("rules", []) if r.get("rule_id") == rule_id), None
    )


def _rule_outcome(setup: dict[str, Any], rule_id: str) -> str | None:
    rule = _rule(setup, rule_id)
    return None if rule is None else rule.get("outcome")


def _first_evidence(setup: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    rule = _rule(setup, rule_id)
    if rule is None or not rule.get("evidence"):
        return None
    return rule["evidence"][0]


def _candle_row(row: OHLCVCandleRecord, timeframe: str, interval: timedelta) -> _Candle:
    return _Candle(
        timeframe=timeframe,
        open_time=row.timestamp,
        close_time=row.timestamp + interval,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


def _candle_at(
    ctx_or_engine: _Context | Engine,
    timeframe: str,
    open_time: datetime,
    *,
    exchange: str | None = None,
    symbol: str | None = None,
) -> _Candle | None:
    """The stored candle with exactly this open time, or ``None``. Never nearest."""

    engine = (
        ctx_or_engine.engine if isinstance(ctx_or_engine, _Context) else ctx_or_engine
    )
    if isinstance(ctx_or_engine, _Context):
        exchange = ctx_or_engine.plan.exchange
        symbol = ctx_or_engine.plan.symbol
    assert exchange is not None and symbol is not None
    with Session(engine) as session:
        row = session.scalars(
            select(OHLCVCandleRecord).where(
                OHLCVCandleRecord.exchange == exchange,
                OHLCVCandleRecord.symbol == symbol,
                OHLCVCandleRecord.timeframe == timeframe,
                OHLCVCandleRecord.timestamp == open_time,
            )
        ).first()
    if row is None:
        return None
    return _candle_row(row, timeframe, interval_for_timeframe(timeframe))


def _candles_between(
    ctx: _Context, timeframe: str, first_open: datetime, last_open: datetime
) -> list[_Candle]:
    with Session(ctx.engine) as session:
        rows = session.scalars(
            select(OHLCVCandleRecord)
            .where(
                OHLCVCandleRecord.exchange == ctx.plan.exchange,
                OHLCVCandleRecord.symbol == ctx.plan.symbol,
                OHLCVCandleRecord.timeframe == timeframe,
                OHLCVCandleRecord.timestamp >= first_open,
                OHLCVCandleRecord.timestamp <= last_open,
            )
            .order_by(OHLCVCandleRecord.timestamp.asc())
        ).all()
    return [
        _candle_row(row, timeframe, interval_for_timeframe(timeframe)) for row in rows
    ]


def _candle_text(candle: _Candle) -> tuple[str, str, str, str, str, str, str]:
    return (
        _dec(candle.open) or "",
        _dec(candle.high) or "",
        _dec(candle.low) or "",
        _dec(candle.close) or "",
        _dec(candle.volume) or "",
        _ts(candle.open_time) or "",
        _ts(candle.close_time) or "",
    )


def _candle_row_for(
    role: str,
    candle: _Candle | None,
    open_time: datetime | None,
    interval: timedelta,
    signal: datetime | None,
) -> tuple[str, ...]:
    if candle is None:
        expected_open = _ts(open_time) or "UNAVAILABLE"
        return (
            role,
            expected_open,
            (_ts(open_time + interval) or "UNAVAILABLE")
            if open_time
            else "UNAVAILABLE",
            "UNAVAILABLE",
            "UNAVAILABLE",
            "UNAVAILABLE",
            "UNAVAILABLE",
            "UNAVAILABLE",
            "no stored candle at this exact open time",
        )
    o, h, low, c, v, opened, closed = _candle_text(candle)
    if signal is None:
        knowable = "post-decision (after the signal)"
    else:
        knowable = (
            "yes (closed by signal)"
            if candle.close_time <= signal
            else "NO — after signal"
        )
    return (role, opened, closed, o, h, low, c, v, knowable)


CANDLE_TABLE_HEADERS = (
    "Role",
    "Open (UTC)",
    "Close (UTC)",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Knowable at signal",
)


# ----------------------------------------------------------------------------
# Sections
# ----------------------------------------------------------------------------

_BAND_RE = re.compile(r"frozen band \[(-?[0-9.]+), (-?[0-9.]+)\]")
_RELVOL_RE = re.compile(r"relative_volume=([0-9.]+)")
_ATR_RE = re.compile(r"ATR_percent=([0-9.]+)")


def _identity_section(ctx: _Context) -> Section:
    plan = ctx.plan
    entry_doc = ctx.plan_doc.get("entry", {})
    lag = (plan.recorded_at - plan.plan_as_of).total_seconds()
    timing = (
        f"{int(lag)} s after the signal close (live: within one candle interval)"
        if lag < ctx.interval.total_seconds()
        else f"{int(lag)} s after the signal close — recorded more than one candle late"
    )
    facts = (
        Fact.recorded("Paper plan id", plan.paper_plan_id, "forward_paper_plans"),
        Fact.recorded("Setup id", plan.setup_id, "forward_paper_plans"),
        Fact.recorded("Step 6 plan id", plan.plan_id, "forward_paper_plans"),
        Fact.recorded(
            "Frozen observation id", plan.observation_id, "forward_paper_plans"
        ),
        Fact.recorded("Ledger cycle id", plan.cycle_id, "forward_paper_plans"),
        Fact.recorded("Family", _enum(plan.family), "forward_paper_plans"),
        Fact.recorded("Direction", _enum(plan.direction), "forward_paper_plans"),
        Fact.recorded("Base timeframe", plan.timeframe, "forward_paper_plans"),
        Fact.recorded(
            "Signal instant (plan as_of = candle close)",
            _ts(plan.plan_as_of),
            "forward_paper_plans.plan_as_of",
        ),
        Fact.recorded(
            "Planning candle open",
            entry_doc.get("observed_at"),
            "plan_json.entry.observed_at",
        ),
        Fact.recorded("Entry", _dec(plan.entry), "forward_paper_plans.entry_level"),
        Fact.recorded("Stop", _dec(plan.stop), "forward_paper_plans.stop_level"),
        Fact.recorded(
            "Target(s)",
            ", ".join(_dec(t) or "" for t in plan.targets),
            "forward_paper_plans.targets_json",
        ),
        Fact.recorded(
            "Risk per unit (entry − stop)",
            _dec(plan.risk_per_unit),
            "forward_paper_plans.risk_per_unit",
            note="unit-neutral price distance only; no sizing exists",
        ),
        Fact.recorded(
            "Recorded R to target(s)",
            ", ".join(_dec(r) or "" for r in plan.target_r_multiples),
            "forward_paper_plans.target_r_json",
        ),
        Fact.recorded(
            "Ledger write time (recorded_at)",
            _ts(plan.recorded_at),
            "forward_paper_plans.recorded_at",
        ),
        Fact.computed("Ledger write timing", timing, "recorded_at − plan_as_of"),
        Fact.recorded(
            "Data health at planning",
            _enum(plan.data_health),
            "forward_paper_plans.data_health",
        ),
        Fact.recorded(
            "Observation horizon (candles)",
            str(plan.observation_horizon_candles),
            "forward_paper_plans",
        ),
        Fact.recorded(
            "Planning rules version", plan.planning_rules_version, "forward_paper_plans"
        ),
        Fact.recorded(
            "Ledger rules version", plan.ledger_rules_version, "forward_paper_plans"
        ),
    )
    return Section(
        key="identity",
        title="1. Identity of the recorded paper trade",
        facts=facts,
        notes=(
            "The paper trade was matched by exact planning instant, timeframe, entry, stop and target. Nothing was rounded or nearest-matched.",
        ),
    )


def _breakout_section(ctx: _Context) -> tuple[Section, dict[str, Any]]:
    seed = _first_evidence(ctx.setup, "seed_event") or {}
    breakout_open = _parse_ts(seed.get("observed_at"))
    breakout_close = _parse_ts(seed.get("confirmed_at"))
    candle = (
        _candle_at(ctx, ctx.plan.timeframe, breakout_open)
        if breakout_open is not None
        else None
    )
    location = _first_evidence(ctx.setup, "location") or {}
    band_match = _BAND_RE.search(location.get("reason", ""))
    band_low = band_high = None
    if band_match:
        band_low = Decimal(band_match.group(1))
        band_high = Decimal(band_match.group(2))
    invalidation = ctx.plan_doc.get("invalidation", {})
    facts: list[Fact] = [
        Fact.recorded(
            "Breakout candle open",
            _ts(breakout_open),
            "setup_json rule seed_event: observed_at",
        ),
        Fact.recorded(
            "Breakout candle close (breakout confirmed)",
            _ts(breakout_close),
            "setup_json rule seed_event: confirmed_at",
        ),
        Fact.recorded(
            "Breakout event id",
            seed.get("source_reference"),
            "setup_json rule seed_event: source_reference",
        ),
        Fact.recorded(
            "Reference id",
            ctx.plan_obs.setup_reference_id,
            "forward_observations.setup_reference_id",
        ),
    ]
    if band_low is not None and band_high is not None:
        facts.append(
            Fact.recorded(
                "Reference level (frozen band low – high)",
                f"{_dec(band_low)} – {_dec(band_high)}",
                "setup_json rule location: reason text",
            )
        )
    else:
        facts.append(
            Fact.unavailable(
                "Reference level (frozen band low – high)",
                "setup_json rule location",
                "the location rule's reason does not carry a parseable band",
            )
        )
    facts.append(
        Fact.recorded(
            "Reference band low (plan invalidation)",
            invalidation.get("value"),
            "plan_json.invalidation.value",
        )
    )
    facts.append(
        Fact.recorded(
            "Reference observed_at (candle open)",
            invalidation.get("observed_at"),
            "plan_json.invalidation.observed_at",
        )
    )
    facts.append(
        Fact.recorded(
            "Reference confirmed_at (candle close)",
            invalidation.get("confirmed_at"),
            "plan_json.invalidation.confirmed_at",
        )
    )
    facts.append(
        Fact.unavailable(
            "Reference type (swing high / zone / range)",
            "forward_observations, plan_json",
            "the ledger stores the reference id only; its type is not recorded",
        )
    )
    if band_low is not None and invalidation.get("value") is not None:
        same = Decimal(str(invalidation["value"])) == band_low
        facts.append(
            Fact.computed(
                "Band low consistency (location rule vs plan invalidation)",
                "MATCH" if same else "MISMATCH",
                "computed from the two recorded values",
            )
        )
    if candle is not None and band_high is not None:
        above = candle.close - band_high
        facts.append(
            Fact.computed(
                "Breakout candle close relative to band high",
                f"{'+' if above >= 0 else ''}{_dec(above)} ({_pct(above, band_high)}%)",
                "stored breakout close − recorded band high",
                note="descriptive only: the close is the Step 4 confirmation price",
            )
        )
    else:
        facts.append(
            Fact.unavailable(
                "Breakout candle close relative to band high",
                "",
                "needs the stored breakout candle and a parsed band high",
            )
        )
    notes: tuple[str, ...] = (
        "The breakout candle is the candle opening at the recorded observed_at and closing at confirmed_at. Its OHLC is in section 6.",
    )
    if (
        breakout_close is not None
        and breakout_open is not None
        and breakout_close != breakout_open + ctx.interval
    ):
        notes += (
            "WARNING: recorded confirmation time is not one candle interval after the open.",
        )
    return (
        Section(
            key="breakout",
            title="2. Breakout candle and reference level",
            facts=tuple(facts),
            notes=notes,
        ),
        {
            "open": breakout_open,
            "close": breakout_close,
            "band_low": band_low,
            "band_high": band_high,
            "candle": candle,
        },
    )


def _retest_section(
    ctx: _Context, breakout: dict[str, Any]
) -> tuple[Section, dict[str, Any]]:
    held = _first_evidence(ctx.setup, "held_retest") or {}
    retest_open = _parse_ts(held.get("observed_at"))
    retest_close = _parse_ts(held.get("confirmed_at"))
    first_held = next(
        (o for o in ctx.setup_obs if "held_retest" in o.passed_rules), None
    )
    facts: list[Fact] = [
        Fact.recorded(
            "Held retest candle open (observed_at)",
            _ts(retest_open),
            "setup_json rule held_retest: observed_at",
        ),
        Fact.recorded(
            "Held retest confirmed (candle close)",
            _ts(retest_close),
            "setup_json rule held_retest: confirmed_at",
        ),
        Fact.recorded(
            "Retest event id",
            held.get("source_reference"),
            "setup_json rule held_retest: source_reference",
        ),
        Fact.recorded(
            "Held-retest rule outcome at planning",
            _rule_outcome(ctx.setup, "held_retest"),
            "setup_json rule held_retest: outcome",
        ),
    ]
    if first_held is not None:
        facts.append(
            Fact.recorded(
                "First ledger close where the held retest passed",
                _ts(first_held.as_of),
                "forward_observations.passed_rules (as_of)",
                note="",
            )
        )
    else:
        facts.append(
            Fact.unavailable(
                "First ledger close where the held retest passed",
                "forward_observations",
                "no ledger close records a passed held_retest at or before the signal",
            )
        )
    if (
        retest_close is not None
        and ctx.ledger_start is not None
        and retest_close < ctx.ledger_start
    ):
        facts.append(
            Fact.computed(
                "Held retest confirmed before the ledger's first cycle",
                f"yes — confirmed {_ts(retest_close)}, first cycle {_ts(ctx.ledger_start)}",
                "ledger coverage",
                note="closes between these instants were not recorded, so the first ledger row is not the first possible detection",
            )
        )
    facts.append(
        Fact.unavailable(
            "Touch-only retest states before the held one",
            "setup_json (held_retest rule only)",
            "the stored evidence records only the held retest; earlier touches or observed-only states are not stored",
        )
    )
    failed_at = [
        o.as_of
        for o in ctx.setup_obs
        if "no_failed_retest" in o.failed_rules
        or "no_failed_breakout" in o.failed_rules
    ]
    if failed_at:
        facts.append(
            Fact.recorded(
                "Failed retest or failed breakout recorded",
                ", ".join(_ts(t) or "" for t in failed_at),
                "forward_observations.failed_rules",
            )
        )
    else:
        facts.append(
            Fact.computed(
                "Failed retest or failed breakout recorded up to the signal",
                "none",
                "forward_observations.failed_rules over all setup closes ≤ signal",
            )
        )
    return (
        Section(
            key="retest",
            title="3. Retest: when it was first detected and held",
            facts=tuple(facts),
            notes=(
                "The retest candle's OHLC is in section 6. A held retest is the Step 4 rule that the candle closed through the level without failing it; the ledger does not restate the Step 4 thresholds.",
            ),
        ),
        {"open": retest_open, "close": retest_close},
    )


def _timeline_section(
    ctx: _Context, breakout: dict[str, Any], retest: dict[str, Any]
) -> Section:
    rows = ctx.setup_obs
    runs: list[list[Any]] = []
    for obs in rows:
        key = (
            _enum(obs.setup_state),
            _enum(obs.plan_state) or "NO_PLAN_PROPOSED",
            obs.no_trade_reason or "",
            _enum(obs.data_health),
        )
        if runs and runs[-1][0] == key:
            runs[-1][2] = obs.as_of
            runs[-1][3] += 1
        else:
            runs.append([key, obs.as_of, obs.as_of, 1])
    table = Table(
        headers=(
            "From (as_of)",
            "To (as_of)",
            "Closes",
            "Setup state",
            "Plan state",
            "Data health",
            "No-trade reason",
        ),
        rows=tuple(
            (
                _ts(first) or "",
                _ts(last) or "",
                str(count),
                key[0] or "",
                key[1],
                key[3] or "",
                key[2] or "—",
            )
            for (key, first, last, count) in runs
        ),
    )

    def first_state(state: str) -> Fact:
        hit = next((o for o in rows if _enum(o.setup_state) == state), None)
        if hit is None:
            return Fact.unavailable(
                f"First recorded {state}",
                "forward_observations",
                f"no {state} row at or before the signal in the ledger",
            )
        return Fact.recorded(
            f"First recorded {state} (as_of)",
            _ts(hit.as_of),
            "forward_observations.setup_state",
        )

    first_plannable = next(
        (o for o in rows if _enum(o.plan_state) == "PLANNABLE"), None
    )
    facts: list[Fact] = [
        Fact.recorded(
            "Cycles recorded up to the signal (this timeframe)",
            str(ctx.cycles_up_to_signal),
            "forward_cycles",
            note="",
        ),
        Fact.recorded(
            "First ledger cycle (as_of)", _ts(ctx.ledger_start), "forward_cycles.as_of"
        ),
        first_state("WATCH"),
        first_state("QUALIFIED"),
        Fact.recorded(
            "First recorded PLANNABLE (as_of)",
            _ts(first_plannable.as_of) if first_plannable else None,
            "forward_observations.plan_state",
        ),
        Fact.recorded(
            "Frozen paper plan (as_of)",
            _ts(ctx.plan.plan_as_of),
            "forward_paper_plans.plan_as_of",
        ),
        Fact.computed(
            "Frozen paper plan is the first recorded PLANNABLE",
            "yes"
            if first_plannable is not None
            and first_plannable.observation_id == ctx.plan.observation_id
            else "no — see table",
            "forward_observations",
        ),
    ]
    notes: list[str] = [
        "Only ledger closes at or before the signal are shown. Later closes (outcome period) are in section 9 and do not change these states.",
    ]
    breakout_close = breakout.get("close")
    if (
        ctx.ledger_start is not None
        and breakout_close is not None
        and ctx.ledger_start > breakout_close
    ):
        notes.append(
            f"States between the breakout confirmation ({_ts(breakout_close)}) and the first ledger cycle ({_ts(ctx.ledger_start)}) are NOT recorded. The ledger cannot show when they first became WATCH or QUALIFIED."
        )
    notes.append(
        "The ledger records one row per candidate state per close. Consecutive closes with the same state are grouped in the table."
    )
    return Section(
        key="timeline",
        title="4. Setup state timeline (QUALIFIED and PLANNABLE)",
        facts=tuple(facts),
        tables=(table,),
        notes=tuple(notes),
    )


def _entry_section(
    ctx: _Context,
    breakout: dict[str, Any],
    retest: dict[str, Any],
    planning: _Candle | None,
) -> Section:
    entry_doc = ctx.plan_doc.get("entry", {})
    entry_value = (
        Decimal(str(entry_doc["value"])) if entry_doc.get("value") is not None else None
    )
    facts: list[Fact] = [
        Fact.recorded(
            "Entry source type",
            entry_doc.get("source_type"),
            "plan_json.entry.source_type",
        ),
        Fact.recorded(
            "Entry source value",
            entry_doc.get("source_value"),
            "plan_json.entry.source_value",
        ),
        Fact.recorded(
            "Entry transformation",
            entry_doc.get("transformation")
            or "none — the source close is copied verbatim",
            "plan_json.entry.transformation",
        ),
        Fact.recorded(
            "Entry observed_at (planning candle open)",
            entry_doc.get("observed_at"),
            "plan_json.entry.observed_at",
        ),
        Fact.recorded(
            "Entry confirmed_at (planning candle close)",
            entry_doc.get("confirmed_at"),
            "plan_json.entry.confirmed_at",
        ),
    ]
    if planning is None:
        facts.append(
            Fact.unavailable(
                "Planning candle close (stored)",
                "ohlcv_candles",
                "no stored candle at the planning candle open",
            )
        )
    else:
        same = entry_value == planning.close
        facts.append(
            Fact.computed(
                "Stored planning candle close equals recorded entry",
                "MATCH" if same else f"MISMATCH (stored close {_dec(planning.close)})",
                "ohlcv_candles close vs plan_json.entry.value",
            )
        )
    rules = {r.get("rule_id"): r.get("reason") for r in ctx.plan_doc.get("rules", [])}
    facts.append(
        Fact.recorded(
            "Planner reason: entry level",
            rules.get("entry_level_available"),
            "plan_json.rules entry_level_available",
        )
    )
    facts.append(
        Fact.recorded(
            "Planner reason: entry on trade side",
            rules.get("entry_on_trade_side"),
            "plan_json.rules entry_on_trade_side",
        )
    )

    window_start = retest.get("open")
    window = []
    if window_start is not None and planning is not None:
        window = _candles_between(
            ctx, ctx.plan.timeframe, window_start, planning.open_time
        )
        expected = int((planning.open_time - window_start) / ctx.interval) + 1
        facts.append(
            Fact.computed(
                "Stored candles from held retest to planning candle",
                f"{len(window)} of {expected} present",
                "ohlcv_candles open times in the window",
                note=""
                if len(window) == expected
                else "window incomplete: missing candles are not filled",
            )
        )
        if window:
            low_candle = min(window, key=lambda c: (c.low, c.open_time))
            close_candle = min(window, key=lambda c: (c.close, c.open_time))
            high_candle = max(window, key=lambda c: (c.high, -c.open_time.timestamp()))
            facts.extend(
                [
                    Fact.computed(
                        "Lowest low in that window",
                        f"{_dec(low_candle.low)} at candle {_ts(low_candle.open_time)}",
                        "stored candles",
                        note="context only",
                    ),
                    Fact.computed(
                        "Lowest close in that window",
                        f"{_dec(close_candle.close)} at candle {_ts(close_candle.open_time)}",
                        "stored candles",
                        note="context only",
                    ),
                    Fact.computed(
                        "Highest high in that window",
                        f"{_dec(high_candle.high)} at candle {_ts(high_candle.open_time)}",
                        "stored candles",
                        note="context only",
                    ),
                ]
            )
    else:
        facts.append(
            Fact.unavailable(
                "Stored candles from held retest to planning candle",
                "ohlcv_candles",
                "needs the held retest and planning candles",
            )
        )

    band_high = breakout.get("band_high")
    if entry_value is not None and band_high is not None:
        distance = entry_value - band_high
        facts.append(
            Fact.computed(
                "Entry relative to reference band high",
                f"{'+' if distance >= 0 else ''}{_dec(distance)} ({_pct(distance, band_high)}%)",
                "recorded entry − parsed band high",
            )
        )
    facts.append(
        Fact.computed(
            "Entry minus stop (risk per unit)",
            format(ctx.plan.entry - ctx.plan.stop, "f"),
            "recorded entry − recorded stop",
        )
    )
    stop_doc = ctx.plan_doc.get("stop", {})
    facts.append(
        Fact.recorded(
            "Stop source type",
            stop_doc.get("source_type"),
            "plan_json.stop.source_type",
        )
    )
    facts.append(
        Fact.recorded(
            "Stop transformation",
            stop_doc.get("transformation"),
            "plan_json.stop.transformation",
        )
    )
    facts.append(
        Fact.recorded(
            "Stop derivation (planner rule)",
            rules.get("stop_buffer_inputs_available"),
            "plan_json.rules stop_buffer_inputs_available",
        )
    )
    preferred = ctx.plan_doc.get("preferred_r_multiple_met")
    facts.append(
        Fact.recorded(
            "Preferred 1.5R met (informational only)",
            None if preferred is None else ("yes" if preferred else "no"),
            "plan_json.preferred_r_multiple_met",
            note="the mandatory floor is 1R; the preferred 1.5R never gates a plan",
        )
    )
    facts.append(
        Fact.recorded(
            "Targets refused by the planner",
            str(len(ctx.plan_doc.get("excluded_targets", []))),
            "plan_json.excluded_targets",
            note="",
        )
    )
    notes = (
        ENTRY_RULE_TEXT,
        STOP_RULE_TEXT,
        "The ledger records one entry per plan. No alternative entry (pullback, limit, retest price) was modelled or evaluated, so the stored data cannot show whether a lower price would have filled. The lowest stored prices above are context, not a fill.",
    )
    return Section(
        key="entry",
        title="5. Why the entry was this price",
        facts=tuple(facts),
        notes=notes,
    )


def _candles_section(
    ctx: _Context,
    breakout: dict[str, Any],
    retest: dict[str, Any],
    planning: _Candle | None,
) -> Section:
    rows = [
        _candle_row_for(
            "Breakout",
            breakout.get("candle"),
            breakout.get("open"),
            ctx.interval,
            ctx.signal,
        ),
    ]
    retest_candle = None
    if retest.get("open") is not None:
        retest_candle = _candle_at(ctx, ctx.plan.timeframe, retest["open"])
    rows.append(
        _candle_row_for(
            "Held retest", retest_candle, retest.get("open"), ctx.interval, ctx.signal
        )
    )
    rows.append(
        _candle_row_for(
            "Planning / entry",
            planning,
            _parse_ts((ctx.plan_doc.get("entry") or {}).get("observed_at")),
            ctx.interval,
            ctx.signal,
        )
    )
    table = Table(headers=CANDLE_TABLE_HEADERS, rows=tuple(rows))
    facts: list[Fact] = []
    atr = ctx.plan_obs.atr_percent_of_price
    facts.append(
        Fact.recorded(
            "ATR as % of price at planning",
            _dec(atr),
            "forward_observations.atr_percent_of_price",
            note="recorded for the planning close only",
        )
    )
    facts.append(
        Fact.unavailable(
            "Absolute ATR (price units) for breakout, retest and planning candles",
            "forward_observations",
            "only ATR as a percent of price is stored; absolute ATR is not recorded and was not recomputed",
        )
    )
    facts.append(
        Fact.unavailable(
            "ATR as % of price at breakout and retest closes",
            "forward_observations",
            "ATR is recorded for the planning close only",
        )
    )
    volume = _first_evidence(ctx.setup, "volume") or {}
    rel = _RELVOL_RE.search(volume.get("reason", ""))
    facts.append(
        Fact.recorded(
            "Relative volume at planning (volume rule)",
            rel.group(1) if rel else None,
            "setup_json rule volume: reason",
            note="the rule compares the planning candle's volume with its recorded baseline",
        )
    )
    facts.append(
        Fact.unavailable(
            "Relative volume at breakout and retest closes",
            "setup_json",
            "the Step 5 volume rule is evaluated on the current close only; earlier values are not stored",
        )
    )
    return Section(
        key="candles",
        title="6. Breakout, retest and entry candles: OHLC, volume and ATR",
        facts=tuple(facts),
        tables=(table,),
        notes=(
            "Every candle is looked up at its exact recorded open time. A missing candle is shown as UNAVAILABLE, never replaced by a neighbour.",
        ),
    )


def _competition_section(ctx: _Context) -> Section:
    repo = ForwardLedgerRepository(ctx.engine)
    cycle_rows = sorted(ctx.cycle_obs, key=lambda o: o.setup_id)
    table = Table(
        headers=(
            "Setup",
            "Family",
            "Direction",
            "Setup state",
            "Plan state",
            "Paper plan",
            "No-trade reason",
        ),
        rows=tuple(
            (
                _short(o.setup_id)
                + (" ← frozen" if o.observation_id == ctx.plan.observation_id else ""),
                _enum(o.setup_family) or "",
                _enum(o.setup_direction) or "",
                _enum(o.setup_state) or "",
                _enum(o.plan_state) or "NO_PLAN_PROPOSED",
                _short(o.paper_plan_id) if o.paper_plan_id else "—",
                o.no_trade_reason or "—",
            )
            for o in cycle_rows
        ),
    )
    refused_here = [
        o for o in cycle_rows if o.no_trade_reason == PAPER_TRADE_ACTIVE_REASON
    ]
    others = [o for o in cycle_rows if o.setup_id != ctx.plan.setup_id]

    prior_plans = [
        p
        for p in repo.paper_plans(
            exchange=ctx.plan.exchange, symbol=ctx.plan.symbol, timeframe=None
        )
        if p.paper_plan_id != ctx.plan.paper_plan_id
        and p.recorded_at <= ctx.plan.recorded_at
    ]
    occupancy_rows = []
    occupied_by: list[str] = []
    for prior in prior_plans:
        outcomes = [
            o
            for o in repo.outcome_versions(prior.paper_plan_id)
            if o.recorded_at <= ctx.plan.recorded_at
        ]
        latest: PaperOutcome | None = outcomes[-1] if outcomes else None
        occupies = paper_trade_occupies_slot(latest, prior, now=ctx.signal)
        if occupies:
            occupied_by.append(_short(prior.paper_plan_id))
        occupancy_rows.append(
            (
                _short(prior.paper_plan_id),
                prior.timeframe,
                _ts(prior.plan_as_of) or "",
                _ts(prior.recorded_at) or "",
                (_enum(latest.status) or "unknown")
                if latest is not None
                else "no outcome recorded yet",
                "YES" if occupies else "no",
            )
        )
    slot_tables: tuple[Table, ...] = ()
    if occupancy_rows:
        slot_tables = (
            Table(
                headers=(
                    "Earlier paper plan",
                    "Timeframe",
                    "Plan as_of",
                    "Recorded at",
                    "Latest outcome at recording",
                    "Occupied the slot at signal",
                ),
                rows=tuple(occupancy_rows),
            ),
        )

    earlier_refusals = [
        o
        for o in ctx.setup_obs
        if o.no_trade_reason == PAPER_TRADE_ACTIVE_REASON and o.paper_plan_id is None
    ]
    facts: list[Fact] = [
        Fact.computed(
            "Earlier paper plans recorded on this instrument",
            str(len(occupancy_rows)) if occupancy_rows else "none",
            "forward_paper_plans (recorded at or before this plan)",
        ),
        Fact.recorded(
            "Other candidates at this close",
            str(len(others)),
            "forward_observations (same cycle)",
        ),
        Fact.recorded(
            "Candidates refused a paper trade by an occupied slot at this close",
            str(len(refused_here)),
            "forward_observations.no_trade_reason",
            note="the first PLANNABLE setup in a close takes the slot; later setups in the same close are competitors refused by it, not evidence of an earlier trade",
        ),
        Fact.computed(
            "Instrument slot free when this paper plan was recorded",
            "yes — no earlier paper plan occupied it"
            if not occupied_by
            else f"NO — occupied by {', '.join(occupied_by)}",
            "paper_trade_occupies_slot over outcomes recorded at or before the plan",
            note="occupancy is judged at the signal instant",
        ),
    ]
    if earlier_refusals:
        first, last = earlier_refusals[0], earlier_refusals[-1]
        facts.append(
            Fact.recorded(
                "This setup was PLANNABLE but refused by the occupied slot before the paper plan",
                f"YES — {len(earlier_refusals)} close(s), {_ts(first.as_of)} to {_ts(last.as_of)}",
                "forward_observations.no_trade_reason",
            )
        )
    else:
        facts.append(
            Fact.recorded(
                "This setup was PLANNABLE but refused by the occupied slot before the paper plan",
                "NO — no recorded slot refusal for this setup before the frozen plan",
                "forward_observations.no_trade_reason",
            )
        )
    return Section(
        key="competition",
        title="7. Competing setups and occupied paper-trade slot",
        facts=tuple(facts),
        tables=(table, *slot_tables),
        notes=(
            "The instrument-wide rule allows one unresolved BTC paper trade on any timeframe. A candidate refused by that rule is still recorded; it is not a second trade.",
        ),
    )


def _timeframe_context_section(ctx: _Context) -> Section:
    facts: list[Fact] = [
        Fact.recorded(
            "Timeframes read by the setup snapshot",
            ",".join(ctx.plan_obs.source_timeframes),
            "forward_observations.source_timeframes",
            note="the forward ledger's base timeframe is the only one it records per close",
        ),
    ]
    if ctx.cycle is not None:
        facts.extend(
            [
                Fact.recorded(
                    "Cycle status at planning",
                    _enum(ctx.cycle.status),
                    "forward_cycles.status",
                ),
                Fact.recorded(
                    "Data health detail at planning",
                    ctx.cycle.data_health_detail,
                    "forward_cycles.data_health_detail",
                ),
                Fact.recorded(
                    "Missing candles in the analysed window",
                    str(ctx.cycle.missing_candle_count),
                    "forward_cycles.missing_candle_count",
                ),
                Fact.recorded(
                    "Latest stored candle when recorded",
                    _ts(ctx.cycle.latest_stored_candle),
                    "forward_cycles.latest_stored_candle",
                ),
                Fact.recorded(
                    "Market-data refresh at recording",
                    ctx.cycle.market_data_json,
                    "forward_cycles.market_data_json",
                ),
            ]
        )
    else:
        facts.append(
            Fact.unavailable(
                "Cycle record",
                "forward_cycles",
                "the cycle row for this close is not stored",
            )
        )

    with Session(ctx.engine) as session:
        heartbeat = session.scalars(
            select(ForwardRunnerHeartbeatRow)
            .where(
                ForwardRunnerHeartbeatRow.exchange == ctx.plan.exchange,
                ForwardRunnerHeartbeatRow.symbol == ctx.plan.symbol,
                ForwardRunnerHeartbeatRow.timeframe == ctx.plan.timeframe,
                ForwardRunnerHeartbeatRow.recorded_at <= ctx.plan.recorded_at,
            )
            .order_by(ForwardRunnerHeartbeatRow.recorded_at.desc(), text("rowid DESC"))
            .limit(1)
        ).first()
        hierarchy = session.scalars(
            select(ForwardHierarchyObservationRow)
            .where(
                ForwardHierarchyObservationRow.exchange == ctx.plan.exchange,
                ForwardHierarchyObservationRow.symbol == ctx.plan.symbol,
                ForwardHierarchyObservationRow.decision_time <= ctx.signal,
            )
            .order_by(ForwardHierarchyObservationRow.decision_time.desc())
            .limit(HIERARCHY_ROWS_SHOWN)
        ).all()
        coverage_rows = _coverage_rows(session, ctx)

    if heartbeat is not None:
        facts.append(
            Fact.recorded(
                "Latest runner heartbeat written at or before the paper plan",
                f"{_ts(heartbeat.recorded_at)} {heartbeat.status}: {heartbeat.detail}",
                "forward_runner_heartbeats",
            )
        )
    else:
        facts.append(
            Fact.unavailable(
                "Latest runner heartbeat before the paper plan",
                "forward_runner_heartbeats",
                "no heartbeat was stored at or before the recording instant",
            )
        )

    tables: list[Table] = []
    if hierarchy:
        first = hierarchy[0]
        tables.append(
            Table(
                headers=(
                    "Decision time",
                    "Recorded at",
                    "Decision",
                    "Alignment",
                    "Status",
                    f"{first.context_timeframe} context close",
                    f"{first.setup_timeframe} setup close",
                    f"{first.confirmation_timeframe} confirmation close",
                    f"{first.execution_timeframe} execution close",
                    "Recorded after signal?",
                ),
                rows=tuple(
                    (
                        _ts(h.decision_time) or "",
                        _ts(h.recorded_at) or "",
                        h.decision,
                        h.alignment,
                        h.status,
                        _ts(h.context_boundary_close) or "",
                        _ts(h.setup_boundary_close) or "",
                        _ts(h.confirmation_boundary_close) or "",
                        _ts(h.execution_boundary_close) or "",
                        "yes — recorded late"
                        if h.recorded_at > ctx.plan.recorded_at
                        else "no",
                    )
                    for h in hierarchy
                ),
            )
        )
        facts.append(
            Fact.recorded(
                "Hierarchy (4H/1H/15M/5M) evaluations at or before the signal",
                str(len(hierarchy)) + " shown",
                "forward_hierarchy_observations",
            )
        )
    else:
        facts.append(
            Fact.unavailable(
                "4H / 15M / 5M hierarchy evaluation at the signal",
                "forward_hierarchy_observations",
                "no hierarchy evaluation is stored at or before the signal; the forward paper ledger does not read these layers",
            )
        )

    tables.append(
        Table(
            headers=(
                "Timeframe",
                "Required window (open, UTC)",
                "Stored in window",
                "Missing",
                "Latest stored open ≤ window end",
                "Verdict",
            ),
            rows=tuple(coverage_rows),
        )
    )
    notes = (
        "Coverage is the stored state now, restricted to candles that had closed by the signal. The ohlcv_candles table has no insertion timestamp, so the database alone cannot prove each candle was downloaded before the signal. The raw archive under data/raw records retrieval times.",
        "Hierarchy evaluations are shown only when they exist. Layers that were never stored are reported as UNAVAILABLE, not inferred from the candles.",
    )
    return Section(
        key="timeframes",
        title="8. 4H, 15M and 5M data available at the time",
        facts=tuple(facts),
        tables=tuple(tables),
        notes=notes,
    )


def _coverage_rows(session: Session, ctx: _Context) -> list[tuple[str, ...]]:
    depth = required_trailing_depth(
        structure=MarketStructureParameters(),
        pattern=PatternLiquidityParameters(),
        qualification=QualificationParameters(),
    )
    rows: list[tuple[str, ...]] = []
    for timeframe in CONTEXT_TIMEFRAMES:
        window_start, window_end = required_window(ctx.signal, timeframe, depth=depth)
        interval = interval_for_timeframe(timeframe)
        expected = int((window_end - window_start) / interval) + 1
        stored_opens = session.scalars(
            select(OHLCVCandleRecord.timestamp).where(
                OHLCVCandleRecord.exchange == ctx.plan.exchange,
                OHLCVCandleRecord.symbol == ctx.plan.symbol,
                OHLCVCandleRecord.timeframe == timeframe,
                OHLCVCandleRecord.timestamp >= window_start,
                OHLCVCandleRecord.timestamp <= window_end,
            )
        ).all()
        present = len(stored_opens)
        latest_stored = session.scalar(
            select(func.max(OHLCVCandleRecord.timestamp)).where(
                OHLCVCandleRecord.exchange == ctx.plan.exchange,
                OHLCVCandleRecord.symbol == ctx.plan.symbol,
                OHLCVCandleRecord.timeframe == timeframe,
                OHLCVCandleRecord.timestamp <= window_end,
            )
        )
        verdict = (
            "COMPLETE"
            if present == expected
            else f"INCOMPLETE ({expected - present} missing; not filled)"
        )
        rows.append(
            (
                timeframe,
                f"{_ts(window_start)} → {_ts(window_end)} ({int(interval.total_seconds() // 60)} min bars)",
                f"{present} of {expected}",
                str(expected - present),
                _ts(latest_stored) or "none stored",
                verdict,
            )
        )
    return rows


def _outcome_section(ctx: _Context) -> Section:
    repo = ForwardLedgerRepository(ctx.engine)
    versions = repo.outcome_versions(ctx.plan.paper_plan_id)
    table = Table(
        headers=(
            "Version",
            "Recorded at",
            "Observed through",
            "Status",
            "Entry touch (candle open)",
            "Stop touch (candle open)",
            "Ambiguous",
            "Incomplete",
            "Resolution used",
        ),
        rows=tuple(
            (
                str(v.sequence),
                _ts(v.recorded_at) or "",
                _ts(v.observation.observed_through) or "",
                _enum(v.observation.status) or "",
                _ts(v.observation.entry_timestamp) or "—",
                _ts(v.observation.stop_timestamp) or "—",
                ("yes (" + (v.observation.ambiguity_kind or "unspecified") + ")")
                if v.observation.ambiguous
                else "no",
                "yes" if v.observation.incomplete else "no",
                (
                    f"yes ({v.observation.resolution_timeframe})"
                    if v.observation.resolution_used
                    else "no"
                ),
            )
            for v in versions
        ),
    )
    facts: list[Fact] = []
    if not versions:
        facts.append(
            Fact.unavailable(
                "Outcome versions",
                "forward_paper_outcomes",
                "no outcome version is stored for this paper plan",
            )
        )
        return Section(
            key="outcome",
            title="9. Stop: candle and evidence",
            facts=tuple(facts),
            tables=(table,),
            post_decision=True,
        )

    latest = versions[-1].observation
    facts.append(
        Fact.recorded(
            "Latest recorded outcome status",
            _enum(latest.status),
            "forward_paper_outcomes (latest version)",
        )
    )
    expected = ctx.query.expected_outcome
    if expected is not None:
        facts.append(
            Fact.computed(
                f"Matches the expected outcome ({expected})",
                "yes"
                if _enum(latest.status) == expected
                else f"no — latest is {_enum(latest.status)}",
                "compared with the requested outcome",
            )
        )
    stop_version = next(
        (v for v in versions if _enum(v.observation.status) == "STOPPED"), None
    )
    if stop_version is None:
        facts.append(
            Fact.unavailable(
                "STOPPED version",
                "forward_paper_outcomes",
                "no version is recorded as STOPPED",
            )
        )
        return Section(
            key="outcome",
            title="9. Stop: candle and evidence",
            facts=tuple(facts),
            tables=(table,),
            post_decision=True,
        )

    obs = stop_version.observation
    stop_event = next(
        (
            e
            for e in obs.events
            if e.kind is OutcomeEventKind.STOP
            and e.ordering is OutcomeEventOrdering.ORDERED
        ),
        None,
    )
    stop_timeframe = (
        obs.resolution_timeframe
        if obs.resolution_used and obs.resolution_timeframe
        else ctx.plan.timeframe
    )
    stop_candle = None
    stop_rows: list[tuple[str, ...]] = []
    if stop_event is not None:
        stop_candle = _candle_at(
            ctx.engine,
            stop_timeframe,
            stop_event.candle_timestamp,
            exchange=ctx.plan.exchange,
            symbol=ctx.plan.symbol,
        )
        stop_rows.append(
            _candle_row_for(
                f"Stop touch ({stop_timeframe})",
                stop_candle,
                stop_event.candle_timestamp,
                interval_for_timeframe(stop_timeframe),
                None,
            )
        )
        facts.append(
            Fact.recorded(
                "Stop touch candle open (recorded event)",
                _ts(stop_event.candle_timestamp),
                "outcome payload events (kind stop)",
                note="",
            )
        )
        facts.append(
            Fact.recorded(
                "Stop level", _dec(stop_event.level), "outcome payload events"
            )
        )
        if stop_candle is not None:
            touched = stop_candle.low <= stop_event.level
            facts.append(
                Fact.computed(
                    "Stop touched by the candle's low (wick)",
                    f"{'yes' if touched else 'NO'} — low {_dec(stop_candle.low)} vs stop {_dec(stop_event.level)}",
                    "stored candle low vs recorded stop level",
                )
            )
            facts.append(
                Fact.computed(
                    "Candle close relative to stop",
                    f"{_dec(stop_candle.close)} ({'at or below' if stop_candle.close <= stop_event.level else 'above'} the stop)",
                    "stored candle close vs recorded stop level",
                )
            )
        else:
            facts.append(
                Fact.unavailable(
                    "Stop touch candle OHLC",
                    "ohlcv_candles",
                    f"no stored {stop_timeframe} candle at {_ts(stop_event.candle_timestamp)}",
                )
            )
    else:
        facts.append(
            Fact.unavailable(
                "Stop touch event",
                "outcome payload events",
                "the stored STOPPED version has no ordered stop event",
            )
        )

    if obs.entry_timestamp is not None:
        entry_candle = _candle_at(
            ctx.engine,
            ctx.plan.timeframe,
            obs.entry_timestamp,
            exchange=ctx.plan.exchange,
            symbol=ctx.plan.symbol,
        )
        stop_rows.insert(
            0,
            _candle_row_for(
                f"Entry touch ({ctx.plan.timeframe})",
                entry_candle,
                obs.entry_timestamp,
                ctx.interval,
                None,
            ),
        )
        if entry_candle is not None:
            contains = entry_candle.low <= ctx.plan.entry <= entry_candle.high
            facts.append(
                Fact.computed(
                    "Entry level lies inside the entry-touch candle's range",
                    "yes" if contains else "NO",
                    "stored candle low/high vs recorded entry",
                )
            )
    if stop_candle is not None:
        lag = (stop_version.recorded_at - stop_candle.close_time).total_seconds()
        facts.append(
            Fact.computed(
                "STOPPED recorded after the stop candle closed",
                f"{int(lag)} s",
                "forward_paper_outcomes.recorded_at − stop candle close",
            )
        )
    facts.append(
        Fact.recorded(
            "Ambiguity flags on the STOPPED version",
            f"ambiguous={obs.ambiguous}, incomplete={obs.incomplete}",
            "forward_paper_outcomes",
        )
    )
    if obs.resolution_used:
        facts.append(
            Fact.recorded(
                "1-minute resolution used",
                f"{obs.resolution_reason or 'used'}",
                "forward_paper_outcomes payload",
            )
        )
    else:
        facts.append(
            Fact.recorded(
                "1-minute resolution used",
                "no"
                if obs.resolution_attempted is False
                else f"not used: {obs.resolution_reason}",
                "forward_paper_outcomes payload",
            )
        )

    return Section(
        key="outcome",
        title="9. Stop: candle and evidence",
        facts=tuple(facts),
        tables=(
            table,
            Table(headers=CANDLE_TABLE_HEADERS, rows=tuple(stop_rows))
            if stop_rows
            else Table(headers=(), rows=()),
        ),
        notes=(
            "This section uses candles after the signal by design: a stop can only be observed after the entry. It is labelled post-decision and never explains the decision in sections 1–8.",
            "Paper observation only: a touch is an OHLC level event, not a fill, a position, or realised P&L.",
        ),
        post_decision=True,
    )


def _missing_items(sections: tuple[Section, ...]) -> tuple[str, ...]:
    items: list[str] = []
    for section in sections:
        for fact in section.facts:
            if fact.status == "UNAVAILABLE":
                items.append(f"{section.title}: {fact.label} — {fact.note}")
        for table in section.tables:
            for row in table.rows:
                if any(cell == "UNAVAILABLE" for cell in row):
                    items.append(
                        f"{section.title}: table row missing: {row[0]} — no stored candle at that instant"
                    )
    return tuple(dict.fromkeys(items))


def build_report(
    engine: Engine,
    query: PlanQuery,
    *,
    header: tuple[Fact, ...],
) -> TradeReport:
    """Assemble the forensic report for one recorded paper trade."""

    _require_ledger(engine)
    plan = locate_paper_plan(engine, query)
    ctx = _load_context(engine, query, plan)
    identity = _identity_section(ctx)
    breakout_section, breakout = _breakout_section(ctx)
    retest_section, retest = _retest_section(ctx, breakout)
    timeline = _timeline_section(ctx, breakout, retest)
    planning_open = _parse_ts((ctx.plan_doc.get("entry") or {}).get("observed_at"))
    planning = (
        _candle_at(ctx, ctx.plan.timeframe, planning_open)
        if planning_open is not None
        else None
    )
    entry = _entry_section(ctx, breakout, retest, planning)
    candles = _candles_section(ctx, breakout, retest, planning)
    competition = _competition_section(ctx)
    timeframes = _timeframe_context_section(ctx)
    outcome = _outcome_section(ctx)
    sections = (
        identity,
        breakout_section,
        retest_section,
        timeline,
        entry,
        candles,
        competition,
        timeframes,
        outcome,
    )
    return TradeReport(
        header=header, sections=sections, missing=_missing_items(sections)
    )


def _require_ledger(engine: Engine) -> None:
    try:
        names = set(inspect(engine).get_table_names())
    except (
        DBAPIError
    ) as exc:  # pragma: no cover - defensive, SQLite reads do not fail here
        raise LedgerNotMigrated(str(exc)) from exc
    required = {
        "forward_cycles",
        "forward_observations",
        "forward_paper_plans",
        "forward_paper_outcomes",
        "ohlcv_candles",
    }
    missing = sorted(required - names)
    if missing:
        raise LedgerNotMigrated(
            "the database has no forward-ledger tables "
            f"({', '.join(missing)} missing); nothing was created or migrated"
        )


def schema_revision(engine: Engine) -> str | None:
    """The Alembic revision stored in the snapshot, or ``None`` if absent."""

    if "alembic_version" not in set(inspect(engine).get_table_names()):
        return None
    with Session(engine) as session:
        return session.scalar(text("select version_num from alembic_version"))


__all__ = [
    "ENTRY_RULE_TEXT",
    "LedgerNotMigrated",
    "PaperPlanAmbiguous",
    "PaperPlanNotFound",
    "PlanQuery",
    "build_report",
    "locate_paper_plan",
    "schema_revision",
]
