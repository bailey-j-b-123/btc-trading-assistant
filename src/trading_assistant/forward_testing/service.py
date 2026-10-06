"""Step 12 forward-testing service: the closed-candle forward event loop.

One pass over one newly confirmed base-timeframe candle close does exactly this
and nothing more:

1. update stored market data through the existing Step 2 service (public OHLCV
   only, closed candles only, duplicates safe, failures reported);
2. build the Step 3 market structure and Step 4 pattern/liquidity evidence for
   that exact close boundary (existing engines, unchanged rules);
3. run the existing Step 5 qualification replay and, for QUALIFIED setups, the
   existing Step 6 planner;
4. build the Step 9 explanation from those deterministic facts and record its
   fingerprints;
5. append the immutable forward observation and, when the Step 6 result is
   PLANNABLE, the frozen paper plan;
6. append new outcome versions for earlier paper plans using the newly closed
   candles (existing Step 7 observation semantics);
7. record a runner heartbeat.

Nothing here decides anything: Steps 3–6 remain the only authority on setups,
plans and levels, and Step 12 adds no threshold, no optimisation, no order, no
account, no position, and no money. Repeated passes, restarts, and duplicate
candles cannot create a duplicate cycle, observation, paper plan, or outcome
version, because every identity is a deterministic fingerprint of the closed
data and the recorded versions.
"""

from __future__ import annotations

import logging
import time
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine

from trading_assistant.ai_explanation import ExplanationService
from trading_assistant.ai_explanation.parameters import EXPLANATION_RULES_VERSION
from trading_assistant.config import Settings, get_settings
from trading_assistant.forward_testing.errors import (
    ForwardConflict,
    ForwardDataUnavailable,
    ForwardNotConfigured,
)
from trading_assistant.forward_testing.models import (
    ForwardCycle,
    ForwardHeartbeat,
    ForwardLedger,
    ForwardObservation,
    PaperOutcome,
    PaperPlan,
    paper_outcome_is_settled,
)
from trading_assistant.forward_testing.parameters import (
    FORWARD_LEDGER_RULES_VERSION,
    FORWARD_RUNNER_RULES_VERSION,
    CycleStatus,
    DataHealth,
    ForwardParameters,
    HeartbeatStatus,
    RunnerSettings,
    canonical_json,
    fingerprint,
)
from trading_assistant.forward_testing.repository import ForwardLedgerRepository
from trading_assistant.journaling import (
    OUTCOME_RULES_VERSION,
    OutcomeParameters,
    ProposedPlanLevels,
    observe_outcome,
    snapshot_identity,
)
from trading_assistant.journaling.types import OutcomeStatus
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.service import MarketDataService
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    latest_closed_candle_open_time,
    milliseconds_to_datetime,
    require_utc_datetime,
    timeframe_anchor_milliseconds,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle, MarketDataUpdateResult
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.pattern_liquidity.service import PatternLiquidityService
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    SetupResult,
    SetupState,
)
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.setup_qualification.service import (
    QualificationService,
    bounded_replay_start,
)
from trading_assistant.trade_planning import (
    PLANNING_RULES_VERSION,
    PlanningParameters,
    PlanState,
    TradePlanResult,
    plan_trade,
)

logger = logging.getLogger(__name__)

#: Outcomes after which a paper plan's trajectory can no longer change.

# See ``models.paper_outcome_is_settled``: ``ENTRY_NOT_REACHED`` only settles once
# the configured horizon has been fully observed, so forward tracking keeps
# observing instead of freezing a premature verdict.


@dataclass(frozen=True, slots=True)
class ForwardRunResult:
    """Outcome of one forward pass; every count is an exact stored row count."""

    status: HeartbeatStatus
    detail: str
    exchange: str
    symbol: str
    timeframe: str
    target_boundary: datetime | None
    processed_boundaries: tuple[datetime, ...]
    cycles_recorded: int
    observations_recorded: int
    paper_plans_created: int
    outcomes_recorded: int
    pending_boundaries: int
    data_health: DataHealth
    latest_cycle_as_of: datetime | None
    market_data_error: str | None
    market_data_error_type: str | None
    heartbeat: ForwardHeartbeat

    def to_json_dict(self) -> dict[str, Any]:
        from trading_assistant.market_structure.snapshot import to_jsonable

        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class _BoundaryContext:
    """Everything one boundary needs, resolved once per pass."""

    frame: QualificationFrame | None
    snapshot: QualificationSnapshot | None
    candle: Candle | None
    candles_known: int
    data_health: DataHealth
    data_health_detail: str
    staleness_intervals: int | None
    missing_candle_count: int
    latest_stored_candle: datetime | None
    structure_fingerprint: str | None
    pattern_fingerprint: str | None
    source_candle_count: int
    strategy_versions: tuple[tuple[str, str], ...]
    version_fingerprint: str


class ForwardTestService:
    """Deterministic closed-candle forward testing over the existing pipeline."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        parameters: ForwardParameters | None = None,
        runner_settings: RunnerSettings | None = None,
        market_data_service: MarketDataService | None = None,
        market_data_factory: Callable[[], MarketDataService] | None = None,
        ledger_start: datetime | None = None,
        backfill_start: datetime | None = None,
        qualification_parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
        planning_parameters: PlanningParameters | None = None,
        explanation_service: ExplanationService | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings if settings is not None else get_settings()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        # Strictly-monotonic per-process counter that disambiguates heartbeat
        # fingerprints when two lifecycle events are recorded at the same wall
        # clock instant (common in fast retry loops).
        self._heartbeat_sequence = 0
        self.parameters = parameters if parameters is not None else ForwardParameters()
        self.runner_settings = (
            runner_settings if runner_settings is not None else RunnerSettings()
        )
        self.ledger_start = (
            None
            if ledger_start is None
            else require_utc_datetime(ledger_start, field_name="ledger_start")
        )
        self.backfill_start = (
            None
            if backfill_start is None
            else require_utc_datetime(backfill_start, field_name="backfill_start")
        )
        self.qualification_parameters = (
            qualification_parameters
            if qualification_parameters is not None
            else QualificationParameters()
        )
        self.pattern_parameters = (
            pattern_parameters
            if pattern_parameters is not None
            else PatternLiquidityParameters()
        )
        self.structure_parameters = (
            structure_parameters
            if structure_parameters is not None
            else MarketStructureParameters()
        )
        self.planning_parameters = (
            planning_parameters
            if planning_parameters is not None
            else PlanningParameters()
        )
        self.ledger = ForwardLedgerRepository(engine)
        self.candles = CandleRepository(engine)
        self.patterns = PatternLiquidityService(engine)
        self.qualification = QualificationService(engine)
        self.explanations = (
            explanation_service if explanation_service is not None else ExplanationService()
        )
        self._market_data = market_data_service
        self._market_data_factory = market_data_factory
        self._sleep = time.sleep

    # ------------------------------------------------------------------
    # Public read API (never fetches, never writes)
    # ------------------------------------------------------------------

    def status(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Current forward state: recorded facts plus live data-health verdict.

        The recorded state comes from the newest stored cycle; the health
        verdict is recomputed from stored candles at ``now`` so a stale runner
        or a stale feed is visible instead of being hidden behind old rows.
        """

        exchange, resolved_symbol, resolved_timeframe = self.resolve_instrument(
            symbol=symbol, timeframe=timeframe
        )
        instant = require_utc_datetime(now or self._clock(), field_name="now")
        interval = interval_for_timeframe(resolved_timeframe)
        target_boundary = (
            latest_closed_candle_open_time(instant, resolved_timeframe) + interval
        )
        latest_cycle = self.ledger.latest_cycle(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        heartbeat = self.ledger.latest_heartbeat(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        counts = self.ledger.counts(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        latest_stored = self.candles.latest_timestamp(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        health, detail, staleness, missing = self._live_health(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            target_boundary=target_boundary,
            now=instant,
        )
        paper_plans = self.ledger.paper_plans(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        latest_outcomes = {
            outcome.paper_plan_id: outcome
            for outcome in self.ledger.latest_outcomes(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
            )
        }
        unresolved = tuple(
            plan
            for plan in paper_plans
            if plan.paper_plan_id not in latest_outcomes
            or not paper_outcome_is_settled(latest_outcomes[plan.paper_plan_id], plan)
        )
        current_paper_plan = self._current_paper_plan(
            plans=paper_plans, latest_outcomes=latest_outcomes
        )
        pending = self._pending_boundaries(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            target_boundary=target_boundary,
        )
        return {
            "step": 12,
            "label": "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE",
            "paper_label": "PAPER OBSERVATION — NO REAL ORDER",
            "market_data_label": "LIVE MARKET DATA",
            "exchange": exchange,
            "symbol": resolved_symbol,
            "timeframe": resolved_timeframe,
            "as_of": target_boundary,
            "now": instant,
            "market_data": {
                "latest_closed_candle_open": (
                    None if latest_stored is None else latest_stored
                ),
                "latest_stored_candle_open": (
                    None if latest_stored is None else latest_stored
                ),
                "expected_latest_closed_candle_open": target_boundary - interval,
                "data_health": health.value,
                "data_health_detail": detail,
                "staleness_intervals": staleness,
                "missing_candle_count": missing,
                "closed_candle_policy": (
                    "Only candles whose full interval had closed are ever analysed. "
                    "An unfinished candle is never used."
                ),
            },
            "runner": None
            if heartbeat is None
            else {
                "status": heartbeat.status.value,
                "recorded_at": heartbeat.recorded_at,
                "heartbeat_age_seconds": _seconds_between(
                    heartbeat.recorded_at, instant
                ),
                "detail": heartbeat.detail,
                "cycles_processed": heartbeat.cycles_processed,
                "pending_boundaries": heartbeat.pending_boundaries,
                "latest_cycle_as_of": heartbeat.latest_cycle_as_of,
                "last_error": heartbeat.last_error,
                "error_type": heartbeat.error_type,
            },
            "latest_cycle": None if latest_cycle is None else latest_cycle.to_json_dict(),
            "current_state": {
                "available": latest_cycle is not None,
                "unavailable_reason": (
                    None
                    if latest_cycle is not None
                    else "the forward runner has not recorded any candle close yet"
                ),
                "as_of": None if latest_cycle is None else latest_cycle.as_of,
                "recorded_at": None if latest_cycle is None else latest_cycle.recorded_at,
                "setup_state": (
                    None
                    if latest_cycle is None or latest_cycle.snapshot_state is None
                    else latest_cycle.snapshot_state.value
                ),
                "setup_state_counts": (
                    {} if latest_cycle is None else dict(latest_cycle.setup_state_counts)
                ),
                "plan_state_counts": (
                    {} if latest_cycle is None else dict(latest_cycle.plan_state_counts)
                ),
                "cycle_status": (
                    None if latest_cycle is None else latest_cycle.status.value
                ),
                "explanation_headline": (
                    None if latest_cycle is None else latest_cycle.explanation_headline
                ),
                "notes": [] if latest_cycle is None else list(latest_cycle.notes),
            },
            "current_paper_plan": (
                None if current_paper_plan is None else current_paper_plan.to_json_dict()
            ),
            "unresolved_paper_plan_count": len(unresolved),
            "unresolved_paper_plan_ids": [plan.paper_plan_id for plan in unresolved],
            "sample": {
                "cycles": counts["cycles"],
                "observations": counts["observations"],
                "paper_plans": counts["paper_plans"],
                "outcome_versions": counts["outcome_versions"],
                "pending_catch_up_boundaries": pending,
            },
            "boundaries": {
                "first_recorded_as_of": (
                    None
                    if not self.ledger.cycles(
                        exchange=exchange,
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                        limit=1,
                    )
                    else self._first_cycle_as_of(
                        exchange=exchange,
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                    )
                ),
                "latest_recorded_as_of": (
                    None if latest_cycle is None else latest_cycle.as_of
                ),
                "next_boundary_to_process": target_boundary
                if pending
                else None,
            },
            "limitations": FORWARD_LIMITATIONS,
        }

    def observations_payload(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = 50,
        include_cycles: bool = False,
    ) -> dict[str, Any]:
        """Latest forward observations with their paper plans and outcomes."""

        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        exchange, resolved_symbol, resolved_timeframe = self.resolve_instrument(
            symbol=symbol, timeframe=timeframe
        )
        cycles = self.ledger.cycles(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            limit=limit,
            newest_first=True,
        )
        observations = self.ledger.observations(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            limit=limit,
            newest_first=True,
        )
        paper_plans = self.ledger.paper_plans(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            limit=limit,
            newest_first=True,
        )
        latest_outcomes = {
            outcome.paper_plan_id: outcome
            for outcome in self.ledger.latest_outcomes(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
            )
        }
        return {
            "label": "PAPER OBSERVATION — NO REAL ORDER",
            "disclaimer": (
                "Forward paper observations record what the deterministic engine "
                "produced and what closed candles did afterwards. Nothing here was "
                "executed, filled, sized, or profited on."
            ),
            "exchange": exchange,
            "symbol": resolved_symbol,
            "timeframe": resolved_timeframe,
            "cycles": (
                [cycle.to_json_dict() for cycle in cycles] if include_cycles else []
            ),
            "observations": [item.to_json_dict() for item in observations],
            "paper_plans": [
                {
                    **plan.to_json_dict(),
                    "latest_outcome": (
                        None
                        if plan.paper_plan_id not in latest_outcomes
                        else latest_outcomes[plan.paper_plan_id].to_json_dict()
                    ),
                    "outcome_version_count": len(
                        self.ledger.outcome_versions(plan.paper_plan_id)
                    ),
                }
                for plan in paper_plans
            ],
        }

    def ledger_snapshot(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> ForwardLedger:
        """Everything the reporting layer needs, read once, in chronological order."""

        interval = interval_for_timeframe(timeframe)
        target_boundary = (
            latest_closed_candle_open_time(self._clock(), timeframe) + interval
        )
        return ForwardLedger(
            cycles=self.ledger.cycles(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            ),
            observations=self.ledger.observations(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            ),
            paper_plans=self.ledger.paper_plans(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            ),
            latest_outcomes=self.ledger.latest_outcomes(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            ),
            version_fingerprints=self._version_fingerprints(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            ),
            pending_catch_up_boundaries=self._pending_boundaries(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                target_boundary=target_boundary,
            ),
        )

    # ------------------------------------------------------------------
    # Runner entry point (the only write path)
    # ------------------------------------------------------------------

    def run_once(
        self,
        *,
        now: datetime | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        refresh_market_data: bool = True,
        runner_id: str | None = None,
    ) -> ForwardRunResult:
        """Process every pending closed candle chronologically, once.

        Market-data refresh failures are retried conservatively and then
        reported: the pass may still process closed candles that are already
        stored (they are real closed candles), but it never invents a candle,
        never analyses an unfinished candle, and never hides a stale feed.
        """

        instant = require_utc_datetime(now or self._clock(), field_name="now")
        exchange, resolved_symbol, resolved_timeframe = self.resolve_instrument(
            symbol=symbol, timeframe=timeframe
        )
        interval = interval_for_timeframe(resolved_timeframe)
        target_boundary = (
            latest_closed_candle_open_time(instant, resolved_timeframe) + interval
        )
        market_data_json = canonical_json(
            {"refreshed": False, "reason": "market-data refresh not requested"}
        )
        market_error: str | None = None
        market_error_type: str | None = None
        if refresh_market_data:
            try:
                update = self._refresh_market_data(
                    symbol=resolved_symbol,
                    timeframe=resolved_timeframe,
                    as_of=instant,
                )
                market_data_json = canonical_json(
                    {
                        "refreshed": True,
                        "received_count": update.received_count,
                        "accepted_count": update.accepted_count,
                        "inserted_count": update.inserted_count,
                        "already_present_count": update.already_present_count,
                        "excluded_open_count": update.excluded_open_count,
                        "rejected_count": update.rejected_count,
                        "missing_candle_count": update.missing_candle_count,
                        "range_start": update.range_start,
                        "range_end": update.range_end,
                        "complete": update.complete,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                market_error = f"{type(exc).__name__}: {exc}"
                market_error_type = type(exc).__name__
                market_data_json = canonical_json(
                    {
                        "refreshed": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "note": (
                            "public market data could not be refreshed; only "
                            "already-stored closed candles may be processed"
                        ),
                    }
                )
                logger.error(
                    "Forward runner could not refresh public market data",
                    extra={
                        "fields": {
                            "exchange": exchange,
                            "symbol": resolved_symbol,
                            "timeframe": resolved_timeframe,
                            "error_type": type(exc).__name__,
                        }
                    },
                )

        latest_cycle = self.ledger.latest_cycle(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        pending = self._pending_boundaries(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            target_boundary=target_boundary,
        )
        if pending == 0:
            heartbeat = self._heartbeat(
                status=HeartbeatStatus.IDLE,
                detail=(
                    "no new closed candle since the last recorded boundary"
                    + ("" if market_error is None else f"; market-data error: {market_error}")
                ),
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                recorded_at=instant,
                cycles_processed=0,
                observations_recorded=0,
                paper_plans_created=0,
                outcomes_recorded=0,
                pending_boundaries=0,
                latest_cycle_as_of=None if latest_cycle is None else latest_cycle.as_of,
                last_error=market_error,
                error_type=market_error_type,
                market_data_json=market_data_json,
                runner_id=runner_id,
            )
            return ForwardRunResult(
                status=HeartbeatStatus.IDLE,
                detail=heartbeat.detail,
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                target_boundary=target_boundary,
                processed_boundaries=(),
                cycles_recorded=0,
                observations_recorded=0,
                paper_plans_created=0,
                outcomes_recorded=0,
                pending_boundaries=0,
                data_health=self._live_health(
                    exchange=exchange,
                    symbol=resolved_symbol,
                    timeframe=resolved_timeframe,
                    target_boundary=target_boundary,
                    now=instant,
                )[0],
                latest_cycle_as_of=None if latest_cycle is None else latest_cycle.as_of,
                market_data_error=market_error,
                market_data_error_type=market_error_type,
                heartbeat=heartbeat,
            )

        opens = self._pending_opens(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            latest_cycle=latest_cycle,
            target_boundary=target_boundary,
            interval=interval,
        )
        to_process = opens[: self.parameters.max_catch_up_candles]
        remaining = len(opens) - len(to_process)

        candle_window = self.candles.get_candles(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            end_time=target_boundary - interval,
        )
        stored_opens = tuple(candle.timestamp for candle in candle_window.candles)
        candles_by_open = {candle.timestamp: candle for candle in candle_window.candles}
        latest_stored = stored_opens[-1] if stored_opens else None

        if not stored_opens:
            heartbeat = self._heartbeat(
                status=HeartbeatStatus.NO_DATA,
                detail=(
                    "no stored closed candle exists for this instrument/timeframe; "
                    "no forward conclusion was produced"
                ),
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                recorded_at=instant,
                cycles_processed=0,
                observations_recorded=0,
                paper_plans_created=0,
                outcomes_recorded=0,
                pending_boundaries=remaining,
                latest_cycle_as_of=None,
                last_error=market_error,
                error_type=market_error_type,
                market_data_json=market_data_json,
                runner_id=runner_id,
            )
            return ForwardRunResult(
                status=HeartbeatStatus.NO_DATA,
                detail=heartbeat.detail,
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                target_boundary=target_boundary,
                processed_boundaries=(),
                cycles_recorded=0,
                observations_recorded=0,
                paper_plans_created=0,
                outcomes_recorded=0,
                pending_boundaries=remaining,
                data_health=DataHealth.UNKNOWN,
                latest_cycle_as_of=None,
                market_data_error=market_error,
                market_data_error_type=market_error_type,
                heartbeat=heartbeat,
            )

        # One bounded replay for the whole pass: candidates live at the first
        # pending close were seeded within the replay window, and the ledger
        # floor extends the replay back to any still-unresolved setup seeded
        # earlier, so its first terminal transition still records exactly once.
        first_as_of = to_process[0] + interval
        last_as_of = to_process[-1] + interval
        recorded_setups, unresolved_floor = self._ledger_setup_index(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        window_start = bounded_replay_start(
            as_of=first_as_of,
            timeframe=resolved_timeframe,
            parameters=self.qualification_parameters,
        )
        if unresolved_floor is None:
            replay_start = window_start
        else:
            # Align down to a close boundary: a stored creation time is always
            # a seed boundary, but aligning defensively can only widen the
            # replay (never narrow it), so coverage is preserved either way.
            floor_boundary = latest_closed_candle_open_time(
                unresolved_floor, resolved_timeframe
            ) + interval
            replay_start = min(window_start, floor_boundary)
        frames = self.qualification.build_frames(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=last_as_of,
            parameters=self.qualification_parameters,
            pattern_parameters=self.pattern_parameters,
            structure_parameters=self.structure_parameters,
            start_at=replay_start,
        )
        frame_by_as_of = {frame.patterns.as_of: frame for frame in frames}
        snapshots = enumerate_qualifications(
            frames, as_of=last_as_of, parameters=self.qualification_parameters
        )
        snapshot_by_as_of = {snapshot.as_of: snapshot for snapshot in snapshots}

        first_ledger_as_of = self._first_cycle_as_of(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )

        cycles_recorded = 0
        observations_recorded = 0
        paper_plans_created = 0
        outcomes_recorded = 0
        processed_boundaries: list[datetime] = []
        last_health = DataHealth.UNKNOWN
        for open_time in to_process:
            boundary = open_time + interval
            context = self._boundary_context(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                boundary=boundary,
                target_boundary=target_boundary,
                now=instant,
                frame=frame_by_as_of.get(boundary),
                snapshot=snapshot_by_as_of.get(boundary),
                candle=candles_by_open.get(open_time),
                candles_known=bisect_left(stored_opens, boundary),
                latest_stored=latest_stored,
            )
            last_health = context.data_health
            (
                cycle,
                created,
                plan_created,
                observation_count,
                recorded_setups,
            ) = self._record_cycle(
                context=context,
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                boundary=boundary,
                open_time=open_time,
                recorded_at=instant,
                recorded_setups=recorded_setups,
                first_ledger_as_of=first_ledger_as_of,
                market_data_json=market_data_json,
            )
            if created:
                cycles_recorded += 1
            observations_recorded += observation_count
            paper_plans_created += plan_created
            processed_boundaries.append(boundary)
            outcomes_recorded += self._update_outcomes(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                boundary=boundary,
                interval=interval,
                recorded_at=instant,
            )

        status = HeartbeatStatus.PROCESSED
        detail = (
            f"processed {len(processed_boundaries)} closed candle(s); "
            f"{observations_recorded} forward observation(s), "
            f"{paper_plans_created} new paper plan(s), "
            f"{outcomes_recorded} outcome version(s)"
            + (f"; {remaining} boundary(ies) still pending" if remaining else "")
            + ("" if market_error is None else f"; market-data error: {market_error}")
        )
        heartbeat = self._heartbeat(
            status=status,
            detail=detail,
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            recorded_at=instant,
            cycles_processed=len(processed_boundaries),
            observations_recorded=observations_recorded,
            paper_plans_created=paper_plans_created,
            outcomes_recorded=outcomes_recorded,
            pending_boundaries=remaining,
            latest_cycle_as_of=processed_boundaries[-1] if processed_boundaries else None,
            last_error=market_error,
            error_type=market_error_type,
            market_data_json=market_data_json,
            runner_id=runner_id,
        )
        return ForwardRunResult(
            status=status,
            detail=detail,
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            target_boundary=target_boundary,
            processed_boundaries=tuple(processed_boundaries),
            cycles_recorded=cycles_recorded,
            observations_recorded=observations_recorded,
            paper_plans_created=paper_plans_created,
            outcomes_recorded=outcomes_recorded,
            pending_boundaries=remaining,
            data_health=last_health,
            latest_cycle_as_of=(
                processed_boundaries[-1] if processed_boundaries else None
            ),
            market_data_error=market_error,
            market_data_error_type=market_error_type,
            heartbeat=heartbeat,
        )

    def record_runner_event(
        self,
        *,
        status: HeartbeatStatus,
        detail: str,
        symbol: str | None = None,
        timeframe: str | None = None,
        now: datetime | None = None,
        last_error: str | None = None,
        error_type: str | None = None,
        runner_id: str | None = None,
    ) -> ForwardHeartbeat:
        """Record a runner lifecycle event (start, stop, fatal error)."""

        exchange, resolved_symbol, resolved_timeframe = self.resolve_instrument(
            symbol=symbol, timeframe=timeframe
        )
        instant = require_utc_datetime(now or self._clock(), field_name="now")
        latest_cycle = self.ledger.latest_cycle(
            exchange=exchange, symbol=resolved_symbol, timeframe=resolved_timeframe
        )
        return self._heartbeat(
            status=status,
            detail=detail,
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            recorded_at=instant,
            cycles_processed=0,
            observations_recorded=0,
            paper_plans_created=0,
            outcomes_recorded=0,
            pending_boundaries=0,
            latest_cycle_as_of=None if latest_cycle is None else latest_cycle.as_of,
            last_error=last_error,
            error_type=error_type,
            market_data_json=canonical_json({"refreshed": False, "reason": "lifecycle"}),
            runner_id=runner_id,
        )

    # ------------------------------------------------------------------
    # Instrument resolution
    # ------------------------------------------------------------------

    def resolve_instrument(
        self, *, symbol: str | None, timeframe: str | None
    ) -> tuple[str, str, str]:
        resolved_symbol = self.settings.symbol if symbol is None else symbol
        if not isinstance(resolved_symbol, str) or not resolved_symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        resolved_timeframe = (
            self.settings.default_timeframe if timeframe is None else timeframe
        )
        if resolved_timeframe not in self.settings.supported_timeframes:
            raise ValueError(
                f"timeframe {resolved_timeframe!r} is not in configured "
                f"supported_timeframes {list(self.settings.supported_timeframes)}"
            )
        interval_for_timeframe(resolved_timeframe)
        return self.settings.exchange, resolved_symbol, resolved_timeframe

    # ------------------------------------------------------------------
    # Market data (existing Step 2 service)
    # ------------------------------------------------------------------

    def _market_data_service(self) -> MarketDataService:
        """The public market-data service, created only when one was provided.

        There is deliberately no implicit default: a forward runner started
        without an explicit public data source refuses to run instead of opening
        a hidden connection. The read-only dashboard never reaches this path.
        """

        if self._market_data is not None:
            return self._market_data
        if self._market_data_factory is not None:
            self._market_data = self._market_data_factory()
            return self._market_data
        raise ForwardNotConfigured(
            "no market-data source is configured for forward testing; the runner "
            "must be started with an explicit public OHLCV source "
            "(python -m trading_assistant.forward_testing run)"
        )

    def bootstrap_start(
        self, *, timeframe: str, as_of: datetime
    ) -> datetime:
        """First candle open the initial public download starts from.

        ``backfill_start`` wins when it is configured; an instant that is not a
        candle open is only ever moved *up* to the next open, never earlier, so a
        normal ISO instant such as ``10:22`` no longer makes Step 2 reject the
        whole pass ("start_time must align to the requested timeframe").

        Without an explicit start the runner seeds itself with one bounded window
        of the newest closed candles: exactly ``RunnerSettings.bootstrap_candles``
        closes ending at the latest fully closed candle, and never fewer than the
        configured ``minimum_history_candles`` precondition. This only chooses how
        far back the *download* starts. Every downloaded row still has to pass the
        unchanged Step 2 validation and the unchanged closed-candle rules, and the
        still-forming candle is still excluded before anything is stored.
        """

        interval = interval_for_timeframe(timeframe)
        if self.backfill_start is not None:
            requested = require_utc_datetime(
                self.backfill_start, field_name="backfill_start"
            )
            aligned = _next_candle_open(requested, timeframe)
            if aligned != requested:
                logger.info(
                    "Forward runner adjusted the requested backfill start to a candle open",
                    extra={
                        "fields": {
                            "exchange": self.settings.exchange,
                            "timeframe": timeframe,
                            "requested_backfill_start": requested.isoformat(),
                            "aligned_backfill_start": aligned.isoformat(),
                        }
                    },
                )
            return aligned

        latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        depth = max(
            self.runner_settings.bootstrap_candles,
            self.parameters.minimum_history_candles,
        )
        start = latest_closed - (depth - 1) * interval
        logger.info(
            "Forward runner bootstrapping stored market history",
            extra={
                "fields": {
                    "exchange": self.settings.exchange,
                    "timeframe": timeframe,
                    "bootstrap_candles": str(depth),
                    "bootstrap_start": start.isoformat(),
                    "latest_closed_open": latest_closed.isoformat(),
                }
            },
        )
        return start

    def _refresh_market_data(
        self, *, symbol: str, timeframe: str, as_of: datetime
    ) -> MarketDataUpdateResult:
        """Fetch closed candles through Step 2 with conservative bounded retries.

        With nothing stored yet the pass performs an initial bounded download
        (see :meth:`bootstrap_start`) so the documented first run can seed its own
        history instead of refusing to fetch. Once history exists, only the newly
        closed candles after the latest stored one are requested.
        """

        service = self._market_data_service()
        latest = self.candles.latest_timestamp(
            exchange=self.settings.exchange, symbol=symbol, timeframe=timeframe
        )
        attempts = self.runner_settings.fetch_max_attempts
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                if latest is None:
                    return service.download_history(
                        start_time=self.bootstrap_start(
                            timeframe=timeframe, as_of=as_of
                        ),
                        symbol=symbol,
                        timeframe=timeframe,
                        as_of=as_of,
                    )
                return service.update_history(
                    symbol=symbol, timeframe=timeframe, as_of=as_of
                )
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if not _is_retryable(exc) or attempt == attempts:
                    raise
                backoff = self.runner_settings.fetch_retry_backoff_seconds * attempt
                logger.warning(
                    "Forward runner retrying public market-data request",
                    extra={
                        "fields": {
                            "exchange": self.settings.exchange,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "attempt": attempt,
                            "max_attempts": attempts,
                            "error_type": type(exc).__name__,
                            "backoff_seconds": str(backoff),
                        }
                    },
                )
                self._sleep(float(backoff))
        assert last_error is not None  # unreachable: the loop re-raises
        raise last_error

    # ------------------------------------------------------------------
    # Pending boundaries
    # ------------------------------------------------------------------

    def _first_cycle_as_of(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> datetime | None:
        cycles = self.ledger.cycles(
            exchange=exchange, symbol=symbol, timeframe=timeframe, limit=1
        )
        return cycles[0].as_of if cycles else None

    def _version_fingerprints(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> tuple[str, ...]:
        seen: list[str] = []
        for cycle in self.ledger.cycles(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        ):
            if cycle.version_fingerprint not in seen:
                seen.append(cycle.version_fingerprint)
        return tuple(seen)

    def _pending_opens(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        latest_cycle: ForwardCycle | None,
        target_boundary: datetime,
        interval,
    ) -> tuple[datetime, ...]:
        last_open = target_boundary - interval
        start_boundary = self._start_boundary(
            exchange=exchange, symbol=symbol, timeframe=timeframe, interval=interval
        )
        start_open = last_open if start_boundary is None else start_boundary - interval
        if start_open > last_open:
            return ()
        count = int((last_open - start_open) // interval) + 1
        return tuple(start_open + index * interval for index in range(count))

    def _start_boundary(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        interval,
    ) -> datetime | None:
        """The first close this pass must (re)process, chronologically.

        Boundaries that were recorded without a complete conclusion are retried:
        when a missing candle later arrives, the close is re-analysed and the
        recovered conclusion is recorded as its own row. A boundary that already
        has a complete cycle is never recomputed from changed data.
        """

        cycles = self.ledger.cycles(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        complete = {cycle.as_of for cycle in cycles if cycle.complete}
        unfinished = sorted(
            {cycle.as_of for cycle in cycles if not cycle.complete} - complete
        )
        if complete:
            next_boundary = max(complete) + interval
        elif self.ledger_start is not None:
            latest_closed = latest_closed_candle_open_time(self.ledger_start, timeframe)
            next_boundary = (
                self.ledger_start
                if latest_closed + interval == self.ledger_start
                else latest_closed + interval
            )
        else:
            next_boundary = None
        retryable = [
            boundary
            for boundary in unfinished
            if next_boundary is None or boundary < next_boundary
        ]
        if retryable:
            return retryable[0]
        return next_boundary

    def _pending_boundaries(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        target_boundary: datetime,
    ) -> int:
        latest_cycle = self.ledger.latest_cycle(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        interval = interval_for_timeframe(timeframe)
        return len(
            self._pending_opens(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                latest_cycle=latest_cycle,
                target_boundary=target_boundary,
                interval=interval,
            )
        )

    # ------------------------------------------------------------------
    # Per-boundary context
    # ------------------------------------------------------------------

    def _live_health(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        target_boundary: datetime,
        now: datetime,
    ) -> tuple[DataHealth, str, int | None, int]:
        """Health of stored market data for the newest closed boundary."""

        interval = interval_for_timeframe(timeframe)
        expected_open = target_boundary - interval
        latest_stored = self.candles.latest_timestamp(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        if latest_stored is None:
            return (
                DataHealth.UNKNOWN,
                "no stored closed candle exists for this instrument/timeframe",
                None,
                0,
            )
        if latest_stored < expected_open:
            difference = int((expected_open - latest_stored) // interval)
            return (
                DataHealth.STALE,
                (
                    f"stored candles stop {difference} interval(s) before the "
                    f"expected latest closed candle {expected_open.isoformat()}"
                ),
                difference,
                difference,
            )
        window = self.candles.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            start_time=expected_open,
            end_time=expected_open,
        )
        missing = window.missing_candle_count
        if missing:
            return (
                DataHealth.INCOMPLETE,
                f"{missing} expected candle(s) missing at the analysed boundary",
                0,
                missing,
            )
        return (
            DataHealth.CURRENT,
            f"latest expected closed candle {expected_open.isoformat()} is stored",
            0,
            0,
        )

    def _boundary_context(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        boundary: datetime,
        target_boundary: datetime,
        now: datetime,
        frame: QualificationFrame | None,
        snapshot: QualificationSnapshot | None,
        candle: Candle | None,
        candles_known: int,
        latest_stored: datetime | None,
    ) -> _BoundaryContext:
        interval = interval_for_timeframe(timeframe)
        expected_open = boundary - interval
        staleness: int | None
        if latest_stored is None:
            health = DataHealth.UNKNOWN
            detail = "no stored closed candle exists for this instrument/timeframe"
            staleness = None
        elif latest_stored < expected_open:
            staleness = int((expected_open - latest_stored) // interval)
            health = DataHealth.STALE
            detail = (
                f"stored candles stop {staleness} interval(s) before this "
                f"boundary's own candle"
            )
        elif boundary < target_boundary:
            staleness = 0
            health = DataHealth.HISTORICAL
            detail = (
                "catch-up pass over an earlier closed candle; the recorded state "
                "describes that close, not the newest market"
            )
        else:
            staleness = 0
            completeness = None if frame is None else frame.patterns.completeness
            if completeness is not None and not completeness.complete:
                health = DataHealth.INCOMPLETE
                detail = (
                    "the analysed window is missing expected candles "
                    f"({completeness.missing_candle_count} missing)"
                )
            elif snapshot is not None and snapshot.status == "incomplete":
                health = DataHealth.INCOMPLETE
                detail = "the Step 5 snapshot reports an incomplete source window"
            else:
                health = DataHealth.CURRENT
                detail = "the analysed closed-candle window is complete"
        missing = (
            0
            if frame is None
            else frame.patterns.completeness.missing_candle_count
        )
        structure_fingerprint = (
            None
            if frame is None
            else fingerprint(
                "forward-step3-structure",
                to_jsonable(frame.patterns.structure),
            )
        )
        pattern_fingerprint = (
            None
            if frame is None
            else fingerprint("forward-step4-evidence", frame.patterns.events())
        )
        versions = self._strategy_versions(frame=frame, snapshot=snapshot)
        return _BoundaryContext(
            frame=frame,
            snapshot=snapshot,
            candle=candle,
            candles_known=candles_known,
            data_health=health,
            data_health_detail=detail,
            staleness_intervals=staleness,
            missing_candle_count=missing,
            latest_stored_candle=latest_stored,
            structure_fingerprint=structure_fingerprint,
            pattern_fingerprint=pattern_fingerprint,
            source_candle_count=(
                0 if frame is None else frame.patterns.completeness.candle_count
            ),
            strategy_versions=versions,
            version_fingerprint=fingerprint("forward-versions", versions),
        )

    def _strategy_versions(
        self,
        *,
        frame: QualificationFrame | None,
        snapshot: QualificationSnapshot | None,
    ) -> tuple[tuple[str, str], ...]:
        """Exact version/fingerprint material recorded on every forward row."""

        pattern_parameters = (
            None if frame is None else frame.patterns.parameters
        )
        structure_parameters = (
            None if frame is None else frame.patterns.structure.parameters
        )
        return (
            ("forward_ledger_rules", FORWARD_LEDGER_RULES_VERSION),
            ("forward_runner_rules", FORWARD_RUNNER_RULES_VERSION),
            ("forward_parameters", self.parameters.fingerprint()),
            ("friction_assumptions", self.parameters.friction.fingerprint()),
            ("explanation_rules", EXPLANATION_RULES_VERSION),
            ("outcome_observation_rules", OUTCOME_RULES_VERSION),
            ("setup_qualification_rules", self.snapshot_rules_version(snapshot)),
            ("trade_planning_rules", PLANNING_RULES_VERSION),
            (
                "setup_config_fingerprint",
                None if snapshot is None else snapshot.config_fingerprint,
            ),
            ("planning_config_fingerprint", self.planning_parameters.fingerprint()),
            (
                "pattern_config_fingerprint",
                None
                if pattern_parameters is None
                else fingerprint("pattern-parameters", pattern_parameters),
            ),
            (
                "structure_config_fingerprint",
                None
                if structure_parameters is None
                else fingerprint("structure-parameters", structure_parameters),
            ),
        )

    @staticmethod
    def snapshot_rules_version(snapshot: QualificationSnapshot | None) -> str:
        from trading_assistant.setup_qualification.parameters import RULES_VERSION

        return RULES_VERSION if snapshot is None else snapshot.rules_version

    # ------------------------------------------------------------------
    # Cycle recording
    # ------------------------------------------------------------------

    def _record_cycle(
        self,
        *,
        context: _BoundaryContext,
        exchange: str,
        symbol: str,
        timeframe: str,
        boundary: datetime,
        open_time: datetime,
        recorded_at: datetime,
        recorded_setups: frozenset[str],
        first_ledger_as_of: datetime | None,
        market_data_json: str,
    ) -> tuple[ForwardCycle, bool, int, int, frozenset[str]]:
        """Record one closed-candle cycle (and its candidate observations).

        Returns the updated recorded-setup ids so the pass carries them
        forward without re-reading the ledger after every boundary; the
        union mirrors exactly what this cycle recorded.
        """

        notes: list[str] = []
        status: CycleStatus
        snapshot = context.snapshot
        frame = context.frame
        if context.candle is None:
            status = CycleStatus.MISSING_CANDLE
            notes.append(
                "no stored candle exists for this boundary's own interval; no "
                "candle was invented and no conclusion was produced"
            )
        elif context.candles_known < self.parameters.minimum_history_candles:
            status = CycleStatus.INSUFFICIENT_HISTORY
            notes.append(
                f"only {context.candles_known} closed candle(s) were stored before "
                f"this boundary; the configured runner precondition is "
                f"{self.parameters.minimum_history_candles}"
            )
        elif frame is None or snapshot is None:
            status = CycleStatus.MISSING_FRAME
            notes.append(
                "no exact Step 3/4 frame or Step 5 snapshot could be built for this "
                "boundary; nothing was concluded"
            )
        else:
            status = CycleStatus.COMPLETE

        # A close that reached no conclusion stores no analytical identity: a
        # missing candle or an unusable window must never look like a fresh
        # analysis of that close.
        concluded = status is CycleStatus.COMPLETE
        snapshot_id_value: str | None = (
            None
            if (not concluded or frame is None or snapshot is None)
            else snapshot_identity(snapshot)
        )
        cycle_id = fingerprint(
            "forward-cycle",
            exchange,
            symbol,
            timeframe,
            boundary,
            open_time,
            status,
            snapshot_id_value,
            context.missing_candle_count > 0,
            context.structure_fingerprint if concluded else None,
            context.pattern_fingerprint if concluded else None,
            context.version_fingerprint,
        )
        recorded_items: list[ForwardObservation] = []
        pending_plans: list[PaperPlan | None] = []
        plans_created = 0
        # An incomplete analysed window never produces a fresh conclusion: the
        # cycle and its candidate evidence are recorded, but Step 6 planning is
        # withheld so a gap can never become a paper observation.
        planning_withheld = (
            status is CycleStatus.COMPLETE
            and context.data_health is DataHealth.INCOMPLETE
        )
        if planning_withheld:
            notes.append(
                "the analysed closed-candle window is incomplete; Step 6 planning "
                "was withheld for this close so missing candles cannot produce a "
                "paper conclusion"
            )
        if status is CycleStatus.COMPLETE:
            assert frame is not None and snapshot is not None
            snapshot_id = snapshot_identity(snapshot)
            for setup in snapshot.setups:
                if not self._should_record_setup(
                    setup=setup,
                    recorded_setups=recorded_setups,
                    first_ledger_as_of=first_ledger_as_of,
                ):
                    continue
                plan = (
                    self._plan(snapshot=snapshot, frame=frame, setup=setup)
                    if setup.state is SetupState.QUALIFIED and not planning_withheld
                    else None
                )
                observation, frozen_plan = self._build_observation(
                    setup=setup,
                    plan=plan,
                    snapshot=snapshot,
                    snapshot_id=snapshot_id,
                    cycle_id=cycle_id,
                    frame=frame,
                    context=context,
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=timeframe,
                    boundary=boundary,
                    open_time=open_time,
                    recorded_at=recorded_at,
                )
                recorded_items.append(observation)
                pending_plans.append(frozen_plan)
                recorded_setups = recorded_setups | {setup.id}

        snapshot_state: SetupState | None = (
            None if (not concluded or snapshot is None) else snapshot.state
        )
        observation_count = len(recorded_items)
        explanation = self._explanation(
            frame=frame if concluded else None,
            snapshot=snapshot if concluded else None,
            at=recorded_at,
        )
        setup_state_counts: dict[str, int] = {}
        for observation in recorded_items:
            key = observation.setup_state.value
            setup_state_counts[key] = setup_state_counts.get(key, 0) + 1
        plan_state_counts: dict[str, int] = {}
        for observation in recorded_items:
            key = (
                "NO_PLAN_PROPOSED"
                if observation.plan_state is None
                else observation.plan_state.value
            )
            plan_state_counts[key] = plan_state_counts.get(key, 0) + 1
        if not recorded_items and snapshot_state is not None:
            notes.append(
                "no candidate needed a new record at this close (no live setup and "
                "no first-time terminal transition); the snapshot state is recorded "
                "on the cycle row"
            )
        cycle = ForwardCycle(
            cycle_id=cycle_id,
            rules_version=FORWARD_LEDGER_RULES_VERSION,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=boundary,
            candle_open_time=open_time,
            recorded_at=recorded_at,
            status=status,
            data_health=context.data_health,
            data_health_detail=context.data_health_detail,
            staleness_intervals=context.staleness_intervals,
            missing_candle_count=context.missing_candle_count,
            latest_stored_candle=context.latest_stored_candle,
            snapshot_state=snapshot_state,
            snapshot_id=snapshot_id_value,
            snapshot_json=(
                None
                if (not concluded or snapshot is None)
                else canonical_json(snapshot.to_json_dict())
            ),
            observation_count=observation_count,
            paper_plan_count=sum(
                1 for item in recorded_items if item.paper_plan_id is not None
            ),
            setup_state_counts=tuple(
                (key, setup_state_counts[key]) for key in sorted(setup_state_counts)
            ),
            plan_state_counts=tuple(
                (key, plan_state_counts[key]) for key in sorted(plan_state_counts)
            ),
            structure_fingerprint=(
                context.structure_fingerprint if concluded else None
            ),
            pattern_fingerprint=context.pattern_fingerprint if concluded else None,
            source_candle_count=context.source_candle_count,
            strategy_versions=context.strategy_versions,
            version_fingerprint=context.version_fingerprint,
            explanation_context_fingerprint=explanation[0],
            explanation_manifest_fingerprint=explanation[1],
            explanation_id=explanation[2],
            explanation_headline=explanation[3],
            explanation_renderer_version=explanation[4],
            market_data_json=market_data_json,
            notes=tuple(notes),
        )
        stored, created = self.ledger.insert_cycle(cycle)
        for observation in recorded_items:
            self.ledger.insert_observation(observation)
        for frozen in pending_plans:
            if frozen is None:
                continue
            stored_plan, plan_created = self.ledger.insert_paper_plan(frozen)
            if stored_plan.paper_plan_id != frozen.paper_plan_id:
                raise ForwardConflict(
                    "a different paper plan id is already stored for setup "
                    f"{frozen.setup_id}; refusing to record a mismatched link"
                )
            plans_created += 1 if plan_created else 0
        logger.info(
            "Forward cycle recorded",
            extra={
                "fields": {
                    "exchange": exchange,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "as_of": boundary.isoformat(),
                    "status": status.value,
                    "data_health": context.data_health.value,
                    "observations": observation_count,
                    "paper_plans": cycle.paper_plan_count,
                }
            },
        )
        return stored, created, plans_created, observation_count, recorded_setups

    def _should_record_setup(
        self,
        *,
        setup: SetupResult,
        recorded_setups: frozenset[str],
        first_ledger_as_of: datetime | None,
    ) -> bool:
        """Record live candidates, and each candidate's first terminal transition."""

        if setup.state in (SetupState.WATCH, SetupState.QUALIFIED):
            return True
        if setup.id in recorded_setups:
            return False
        if setup.ended_at is None:
            return False
        if first_ledger_as_of is None:
            return setup.ended_at == setup.as_of
        return setup.ended_at >= first_ledger_as_of

    def _plan(
        self,
        *,
        snapshot: QualificationSnapshot,
        frame: QualificationFrame,
        setup: SetupResult,
    ) -> TradePlanResult:
        """Step 6 planning, unchanged, for one QUALIFIED setup at this close."""

        return plan_trade(
            snapshot=snapshot,
            frame=frame,
            setup_id=setup.id,
            parameters=self.planning_parameters,
        )

    def _build_observation(
        self,
        *,
        setup: SetupResult,
        plan: TradePlanResult | None,
        snapshot: QualificationSnapshot,
        snapshot_id: str,
        cycle_id: str,
        frame: QualificationFrame,
        context: _BoundaryContext,
        exchange: str,
        symbol: str,
        timeframe: str,
        boundary: datetime,
        open_time: datetime,
        recorded_at: datetime,
    ) -> tuple[ForwardObservation, PaperPlan | None]:
        observation_id = fingerprint(
            "forward-observation",
            exchange,
            symbol,
            timeframe,
            boundary,
            snapshot_id,
            setup.id,
            setup.state,
            None if plan is None else plan.id,
            context.version_fingerprint,
        )
        frozen_plan: PaperPlan | None = None
        paper_plan_id: str | None = None
        if plan is not None and plan.state is PlanState.PLANNABLE:
            paper_plan_id, frozen_plan = self._build_paper_plan(
                observation_id=observation_id,
                cycle_id=cycle_id,
                plan=plan,
                setup=setup,
                snapshot_id=snapshot_id,
                context=context,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                recorded_at=recorded_at,
            )
        targets = (
            ()
            if plan is None
            else tuple(
                target.level.value
                for target in plan.targets
                if target.level.value is not None
            )
        )
        target_r = (
            ()
            if plan is None
            else tuple(target.r_multiple for target in plan.targets)
        )
        return (
            ForwardObservation(
                observation_id=observation_id,
                cycle_id=cycle_id,
                observation_rules_version=FORWARD_LEDGER_RULES_VERSION,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                source_timeframes=setup.source_timeframes,
                as_of=boundary,
                candle_open_time=open_time,
                recorded_at=recorded_at,
                snapshot_state=snapshot.state,
                snapshot_id=snapshot_id,
                setup_id=setup.id,
                setup_family=setup.family,
                setup_direction=setup.direction,
                setup_state=setup.state,
                setup_created_at=setup.created_at,
                setup_seed_event_id=setup.seed_event_id,
                setup_reference_id=setup.reference_id,
                setup_terminal_reason=setup.terminal_reason,
                setup_ended_at=setup.ended_at,
                passed_rules=setup.passed_rules,
                failed_rules=setup.failed_rules,
                pending_rules=setup.pending_rules,
                veto_rules=tuple(rule.rule_id for rule in setup.rules if rule.veto),
                setup_json=canonical_json(to_jsonable(setup)),
                plan_id=None if plan is None else plan.id,
                plan_state=None if plan is None else plan.state,
                plan_json=None if plan is None else canonical_json(plan.to_json_dict()),
                plan_entry=(
                    None if plan is None or plan.entry.value is None else plan.entry.value
                ),
                plan_invalidation=(
                    None
                    if plan is None or plan.invalidation.value is None
                    else plan.invalidation.value
                ),
                plan_stop=(
                    None if plan is None or plan.stop.value is None else plan.stop.value
                ),
                plan_risk_per_unit=None if plan is None else plan.risk_per_unit,
                plan_targets=targets,
                plan_target_r_multiples=target_r,
                plan_config_fingerprint=(
                    None if plan is None else plan.config_fingerprint
                ),
                planning_rules_version=(
                    None if plan is None else plan.planning_rules_version
                ),
                paper_plan_id=paper_plan_id,
                data_health=context.data_health,
                missing_candle_count=context.missing_candle_count,
                market_trend=frame.patterns.structure.trend.direction.value.upper(),
                atr_percent_of_price=(
                    frame.patterns.structure.volatility.atr_percent_of_price
                ),
                calendar_period=boundary.strftime("%Y-%m"),
                structure_fingerprint=context.structure_fingerprint,
                pattern_fingerprint=context.pattern_fingerprint,
                version_fingerprint=context.version_fingerprint,
            ),
            frozen_plan,
        )

    def _build_paper_plan(
        self,
        *,
        observation_id: str,
        cycle_id: str,
        plan: TradePlanResult,
        setup: SetupResult,
        snapshot_id: str,
        context: _BoundaryContext,
        exchange: str,
        symbol: str,
        timeframe: str,
        recorded_at: datetime,
    ) -> tuple[str, PaperPlan | None]:
        """Build the frozen PLANNABLE projection for one setup instance.

        Returns ``(paper_plan_id, frozen_plan)``. The frozen plan is ``None`` when
        a plan for this setup instance is already stored: the first PLANNABLE
        projection stays frozen, so a setup that remains plannable across many
        candles is linked to the same paper plan and never counted twice.

        A paper observation exists only here: WATCH and NO_SETUP never reach this
        method, and a plan that is NO_PLAN or INVALID is recorded as a refusal in
        the observation instead. The id is deterministic per setup instance, and
        when a plan is already stored for that instance it is reused verbatim: a
        setup that stays plannable for many candles keeps exactly one paper plan.
        """

        assert plan.as_of is not None
        assert plan.direction is not None
        assert plan.entry.value is not None
        assert plan.stop.value is not None
        assert plan.risk_per_unit is not None
        targets, target_r = _plan_targets(plan)
        existing = self.ledger.paper_plan_for_setup(
            exchange=exchange, symbol=symbol, timeframe=timeframe, setup_id=setup.id
        )
        if existing is not None:
            return existing.paper_plan_id, None
        plan_key = fingerprint(
            "forward-paper-plan",
            exchange,
            symbol,
            timeframe,
            setup.id,
        )
        return plan_key, PaperPlan(
            paper_plan_id=plan_key,
            observation_id=observation_id,
            cycle_id=cycle_id,
            ledger_rules_version=FORWARD_LEDGER_RULES_VERSION,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            setup_id=setup.id,
            snapshot_id=snapshot_id,
            family=setup.family,
            direction=plan.direction,
            plan_id=plan.id,
            plan_as_of=plan.as_of,
            recorded_at=recorded_at,
            entry=plan.entry.value,
            stop=plan.stop.value,
            invalidation=(
                plan.invalidation.value
                if plan.invalidation.value is not None
                else plan.stop.value
            ),
            risk_per_unit=plan.risk_per_unit,
            targets=targets,
            target_r_multiples=target_r,
            plan_json=canonical_json(plan.to_json_dict()),
            plan_config_fingerprint=plan.config_fingerprint,
            planning_rules_version=plan.planning_rules_version,
            strategy_versions=context.strategy_versions,
            version_fingerprint=context.version_fingerprint,
            friction=self.parameters.friction,
            friction_fingerprint=self.parameters.friction.fingerprint(),
            forward_parameters_fingerprint=self.parameters.fingerprint(),
            observation_horizon_candles=self.parameters.observation_horizon_candles,
            data_health=context.data_health,
        )

    # ------------------------------------------------------------------
    # Outcome updates (existing Step 7 observation semantics)
    # ------------------------------------------------------------------

    def _update_outcomes(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        boundary: datetime,
        interval,
        recorded_at: datetime,
    ) -> int:
        """Append new outcome versions for unresolved paper plans."""

        written = 0
        plans = self.ledger.paper_plans(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        latest = {
            outcome.paper_plan_id: outcome
            for outcome in self.ledger.latest_outcomes(
                exchange=exchange, symbol=symbol, timeframe=timeframe
            )
        }
        for plan in plans:
            previous = latest.get(plan.paper_plan_id)
            horizon_last_open = plan.plan_as_of + interval * (
                plan.observation_horizon_candles - 1
            )
            if previous is not None and paper_outcome_is_settled(previous, plan):
                continue
            last_closed_open = boundary - interval
            observed_through = min(horizon_last_open, last_closed_open)
            if observed_through < plan.plan_as_of:
                continue
            if previous is not None:
                if previous.observed_through > observed_through:
                    # A retried earlier close cannot shorten a recorded window.
                    continue
                if (
                    previous.observed_through == observed_through
                    and previous.observation.status
                    is not OutcomeStatus.INCOMPLETE_DATA
                ):
                    # Nothing new was observed; only a previously gapped window
                    # is re-checked, in case the missing candle has arrived.
                    continue
            candles = self.candles.get_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                start_time=plan.plan_as_of,
                end_time=observed_through,
            ).candles
            outcome = self._observe(plan=plan, candles=candles, observed_through=observed_through, recorded_at=recorded_at)
            _, created = self.ledger.append_outcome(outcome)
            written += 1 if created else 0
        return written

    def _observe(
        self,
        *,
        plan: PaperPlan,
        candles: Sequence[Candle],
        observed_through: datetime,
        recorded_at: datetime,
    ) -> PaperOutcome:
        observation = observe_outcome(
            journal_id=f"forward-paper:{plan.paper_plan_id}",
            levels=ProposedPlanLevels(
                plan_id=plan.plan_id,
                exchange=plan.exchange,
                symbol=plan.symbol,
                timeframe=plan.timeframe,
                direction=plan.direction,
                entry=plan.entry,
                stop=plan.stop,
                targets=plan.targets,
                risk_per_unit=plan.risk_per_unit,
                as_of=plan.plan_as_of,
                setup_id=plan.setup_id,
            ),
            candles=candles,
            observed_through=observed_through,
            parameters=OutcomeParameters(),
        )
        return PaperOutcome(
            outcome_id="",  # assigned by the repository from the payload identity
            paper_plan_id=plan.paper_plan_id,
            observation_id=plan.observation_id,
            sequence=0,  # assigned by the repository
            supersedes_outcome_id=None,
            ledger_rules_version=FORWARD_LEDGER_RULES_VERSION,
            observation_rules_version=observation.observation_rules_version,
            config_fingerprint=observation.config_fingerprint,
            recorded_at=recorded_at,
            payload_json=canonical_json(observation.to_json_dict()),
            observation=observation,
        )

    # ------------------------------------------------------------------
    # Explanation (Step 9, explanation-only)
    # ------------------------------------------------------------------

    def _explanation(
        self,
        *,
        frame: QualificationFrame | None,
        snapshot: QualificationSnapshot | None,
        at: datetime,
    ) -> tuple[str | None, str | None, str | None, str | None, str | None]:
        """Build the Step 9 explanation and return its fingerprints and headline.

        Step 9 stays explanation-only: it is given the exact deterministic facts
        already produced and may not add, alter, or decide anything. Only its
        identifiers and its deterministic headline are stored; the headline is
        explanatory text, never evidence.
        """

        if snapshot is None:
            return (None, None, None, None, None)
        try:
            context = self.explanations.build_context(snapshot=snapshot, frame=frame)
            result = self.explanations.explain(context, generated_at=at)
        except Exception as exc:  # noqa: BLE001 - explanation is optional, never silent
            logger.warning(
                "Step 9 explanation could not be built for a forward cycle",
                extra={"fields": {"error_type": type(exc).__name__}},
            )
            return (None, None, None, None, None)
        return (
            result.context_fingerprint,
            result.manifest_fingerprint,
            result.explanation_id,
            result.headline,
            result.renderer_version,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ledger_setup_index(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> tuple[frozenset[str], datetime | None]:
        """Recorded setup ids, plus the oldest creation time still unresolved.

        The floor is the earliest ``setup_created_at`` among setups with no
        terminal (``NO_SETUP`` with an end time) observation yet. The bounded
        replay starts at or before it, so even a candidate seeded before the
        replay window replays and records its first terminal transition
        exactly once. ``None`` when every recorded setup already resolved.
        """

        recorded: set[str] = set()
        terminal: set[str] = set()
        created: dict[str, datetime] = {}
        for item in self.ledger.observations(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        ):
            recorded.add(item.setup_id)
            previous = created.get(item.setup_id)
            if previous is None or item.setup_created_at < previous:
                created[item.setup_id] = item.setup_created_at
            if (
                item.setup_state is SetupState.NO_SETUP
                and item.setup_ended_at is not None
            ):
                terminal.add(item.setup_id)
        floors = [
            created[setup_id] for setup_id in recorded - terminal if setup_id in created
        ]
        return frozenset(recorded), (min(floors) if floors else None)

    def _current_paper_plan(
        self, *, plans: Sequence[PaperPlan], latest_outcomes
    ) -> PaperPlan | None:
        if not plans:
            return None
        unresolved = [
            plan
            for plan in plans
            if plan.paper_plan_id not in latest_outcomes
            or not paper_outcome_is_settled(latest_outcomes[plan.paper_plan_id], plan)
        ]
        candidates = unresolved or list(plans)
        return max(candidates, key=lambda plan: (plan.plan_as_of, plan.paper_plan_id))

    def _heartbeat(
        self,
        *,
        status: HeartbeatStatus,
        detail: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        recorded_at: datetime,
        cycles_processed: int,
        observations_recorded: int,
        paper_plans_created: int,
        outcomes_recorded: int,
        pending_boundaries: int,
        latest_cycle_as_of: datetime | None,
        last_error: str | None,
        error_type: str | None,
        market_data_json: str,
        runner_id: str | None,
    ) -> ForwardHeartbeat:
        # Include error context in the fingerprint so two distinct failure
        # events with identical details (very common in retry loops) are
        # recorded separately instead of silently collapsing to one row.
        self._heartbeat_sequence += 1
        heartbeat_id = fingerprint(
            "forward-heartbeat",
            recorded_at,
            status,
            exchange,
            symbol,
            timeframe,
            detail,
            cycles_processed,
            observations_recorded,
            paper_plans_created,
            outcomes_recorded,
            pending_boundaries,
            latest_cycle_as_of,
            runner_id,
            last_error,
            error_type,
            self._heartbeat_sequence,
        )
        return self.ledger.append_heartbeat(
            ForwardHeartbeat(
                heartbeat_id=heartbeat_id,
                runner_rules_version=FORWARD_RUNNER_RULES_VERSION,
                recorded_at=recorded_at,
                status=status,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                detail=detail,
                cycles_processed=cycles_processed,
                observations_recorded=observations_recorded,
                paper_plans_created=paper_plans_created,
                outcomes_recorded=outcomes_recorded,
                pending_boundaries=pending_boundaries,
                latest_cycle_as_of=latest_cycle_as_of,
                last_error=last_error,
                error_type=error_type,
                market_data_json=market_data_json,
            )
        )


#: Safety and honesty limitations recorded on every status/history payload.
FORWARD_LIMITATIONS: tuple[str, ...] = (
    "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE. No order was placed, no "
    "fill happened, and no money exists anywhere in this system.",
    "PAPER OBSERVATION — NO REAL ORDER. A paper observation is a deterministic "
    "statement about closed candles relative to Step 6 proposed levels.",
    "Paper trading and historical performance do not establish future profitability.",
    "Only fully closed candles are analysed; an unfinished candle is never used.",
    "Missing or stale market data is reported and never filled in with a guess.",
    "Forward results are separated by the exact recorded strategy/config "
    "version fingerprints that produced them.",
)


def _plan_targets(plan: TradePlanResult) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    targets: list[Any] = []
    target_r: list[Any] = []
    for target in plan.targets:
        if target.level.value is None:
            continue
        targets.append(target.level.value)
        target_r.append(target.r_multiple)
    return tuple(targets), tuple(target_r)


def _next_candle_open(value: datetime, timeframe: str) -> datetime:
    """Round an instant up to the candle open at or after it (never earlier).

    Candle open times are the only bounds Step 2 accepts, in addition to being
    the only thing the closed-candle policy reasons about, so an operator-supplied
    instant is aligned here rather than rejected.
    """

    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    value_ms = datetime_to_milliseconds(value, field_name="backfill_start")
    remainder = (value_ms - anchor_ms) % interval_ms
    aligned_ms = value_ms if remainder == 0 else value_ms + (interval_ms - remainder)
    return milliseconds_to_datetime(aligned_ms)


def _seconds_between(earlier: datetime, later: datetime) -> int:
    return int((require_utc_datetime(later) - require_utc_datetime(earlier)).total_seconds())


def _is_retryable(exc: Exception) -> bool:
    """Only transient public-market-data failures are retried."""

    from trading_assistant.market_data.errors import ExchangeDataError

    if isinstance(exc, ForwardDataUnavailable):
        return False
    return isinstance(exc, ExchangeDataError)


__all__ = ["FORWARD_LIMITATIONS", "ForwardRunResult", "ForwardTestService"]
