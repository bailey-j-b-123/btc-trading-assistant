"""Step 13 multi-timeframe service: hierarchy evaluation, ledger, and runner.

One pass over newly closed EXECUTION-timeframe candles does exactly this and
nothing more:

1. refresh stored market data for EVERY hierarchy timeframe through the
   existing Step 2 service — one correctly managed client serves all
   timeframes (public OHLCV only, closed candles only, duplicates safe);
2. at each pending closed execution boundary, evaluate the complete hierarchy
   (4H context, 1H setup, 15M confirmation, 5M execution) from stored closed
   candles only;
3. append the immutable hierarchy observation to the forward ledger.

Nothing here decides anything beyond the deterministic hierarchy: the existing
Steps 3-6 remain the only authority on structure, setups and plans, and this
layer adds no threshold, no optimisation, no order, no account, no position,
and no money. Repeated passes, restarts, and duplicate boundaries cannot
create a duplicate observation, because the identity is a deterministic
fingerprint of the evaluation content (including the hierarchy fingerprint).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy.engine import Engine

from trading_assistant.ai_explanation.parameters import EXPLANATION_RULES_VERSION
from trading_assistant.config import Settings, get_settings
from trading_assistant.database.engine import is_memory_database
from trading_assistant.forward_testing.parameters import RunnerSettings
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
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.service import MarketStructureService
from trading_assistant.multi_timeframe.boundaries import resolve_decision_boundary
from trading_assistant.multi_timeframe.engine import evaluate_hierarchy
from trading_assistant.multi_timeframe.errors import (
    HierarchyNotConfigured,
)
from trading_assistant.multi_timeframe.hierarchy import (
    TimeframeHierarchy,
    default_hierarchy,
)
from trading_assistant.multi_timeframe.ladder import ladder_payload
from trading_assistant.multi_timeframe.models import (
    HierarchyObservation,
    HierarchySnapshot,
)
from trading_assistant.multi_timeframe.parameters import (
    HIERARCHY_RULES_VERSION,
    canonical_json,
    fingerprint,
)
from trading_assistant.multi_timeframe.repository import HierarchyLedgerRepository
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
)
from trading_assistant.setup_qualification.parameters import (
    RULES_VERSION as QUALIFICATION_RULES_VERSION,
    QualificationParameters,
)
from trading_assistant.setup_qualification.service import (
    QualificationService,
    bounded_replay_start,
)

logger = logging.getLogger(__name__)


class RunnerStatus(StrEnum):
    """Multi-timeframe runner pass outcomes."""

    PROCESSED = "PROCESSED"
    IDLE = "IDLE"
    NO_DATA = "NO_DATA"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class MultiTimeframeRunResult:
    """Outcome of one hierarchy runner pass; every count is exact."""

    status: RunnerStatus
    detail: str
    exchange: str
    symbol: str
    processed_boundaries: tuple[datetime, ...]
    observations_recorded: int
    observations_created: int
    pending_boundaries: int
    latest_decision_time: datetime | None
    market_data_json: str
    market_data_error: str | None
    market_data_error_type: str | None

    def to_json_dict(self) -> dict[str, Any]:
        from trading_assistant.market_structure.snapshot import to_jsonable

        return to_jsonable(self)


class MultiTimeframeService:
    """Deterministic multi-timeframe hierarchy evaluation over stored candles."""

    def __init__(
        self,
        engine: Engine,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        hierarchy: TimeframeHierarchy | None = None,
        structure_parameters: MarketStructureParameters | None = None,
        qualification_parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        market_data_service: MarketDataService | None = None,
        market_data_factory: Callable[[], MarketDataService] | None = None,
        ledger_start: datetime | None = None,
        runner_settings: RunnerSettings | None = None,
        max_catch_up_boundaries: int = 720,
    ) -> None:
        self.engine = engine
        self.settings = settings if settings is not None else get_settings()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self.hierarchy = hierarchy if hierarchy is not None else default_hierarchy()
        self.structure_parameters = (
            structure_parameters
            if structure_parameters is not None
            else MarketStructureParameters()
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
        self.ledger = HierarchyLedgerRepository(engine)
        self.candles = CandleRepository(engine)
        self.structure = MarketStructureService(
            engine, settings=self.settings, clock=self._clock
        )
        self.qualification = QualificationService(engine)
        self._market_data = market_data_service
        self._market_data_factory = market_data_factory
        self.ledger_start = (
            None
            if ledger_start is None
            else require_utc_datetime(ledger_start, field_name="ledger_start")
        )
        self.runner_settings = (
            runner_settings if runner_settings is not None else RunnerSettings()
        )
        if not 1 <= max_catch_up_boundaries <= 100_000:
            raise ValueError("max_catch_up_boundaries must be between 1 and 100000")
        self.max_catch_up_boundaries = max_catch_up_boundaries
        self._sleep = time.sleep
        for timeframe in self.hierarchy.timeframes:
            if timeframe not in self.settings.supported_timeframes:
                raise ValueError(
                    f"hierarchy timeframe {timeframe!r} is not in configured "
                    f"supported_timeframes {list(self.settings.supported_timeframes)}"
                )

    # ------------------------------------------------------------------
    # Instrument resolution
    # ------------------------------------------------------------------

    def resolve_instrument(self, *, symbol: str | None) -> tuple[str, str]:
        resolved = self.settings.symbol if symbol is None else symbol
        if not isinstance(resolved, str) or not resolved.strip():
            raise ValueError("symbol must be a non-empty string")
        return self.settings.exchange, resolved

    # ------------------------------------------------------------------
    # Evaluation (read-only)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        *,
        symbol: str | None = None,
        decision_time: datetime | None = None,
    ) -> HierarchySnapshot:
        """Evaluate the complete hierarchy at one decision instant.

        Every layer reads only stored candles that had fully closed by the
        decision time. The setup layer is the existing Step 5 replay at the
        latest closed setup-timeframe boundary at or before the decision time.
        """

        instant = require_utc_datetime(
            decision_time if decision_time is not None else self._clock(),
            field_name="decision_time",
        )
        exchange, resolved_symbol = self.resolve_instrument(symbol=symbol)
        hierarchy = self.hierarchy

        candles_by_timeframe: dict[str, tuple[Candle, ...]] = {}
        for timeframe in hierarchy.timeframes:
            expected_open = latest_closed_candle_open_time(instant, timeframe)
            result = self.candles.get_candles(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=timeframe,
                end_time=expected_open,
            )
            candles_by_timeframe[timeframe] = result.candles
        boundary = resolve_decision_boundary(
            instant, hierarchy, candles_by_timeframe
        )

        context_structure = self.structure.snapshot(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=hierarchy.context.timeframe,
            as_of=instant,
            parameters=self.structure_parameters,
        )

        setup_boundary = boundary.boundary_for(hierarchy.setup.timeframe)
        qualification: QualificationSnapshot | None = None
        qualification_frame: QualificationFrame | None = None
        if setup_boundary.candle_known:
            setup_as_of = setup_boundary.candle_close_time
            try:
                frames = self.qualification.build_frames(
                    exchange=exchange,
                    symbol=resolved_symbol,
                    timeframe=hierarchy.setup.timeframe,
                    as_of=setup_as_of,
                    parameters=self.qualification_parameters,
                    pattern_parameters=self.pattern_parameters,
                    structure_parameters=self.structure_parameters,
                    start_at=bounded_replay_start(
                        as_of=setup_as_of,
                        timeframe=hierarchy.setup.timeframe,
                        parameters=self.qualification_parameters,
                    ),
                )
                snapshots = enumerate_qualifications(
                    frames, as_of=setup_as_of, parameters=self.qualification_parameters
                )
                qualification = snapshots[-1]
                qualification_frame = frames[-1]
            except ValueError as exc:
                logger.warning(
                    "Hierarchy setup layer could not be evaluated at the boundary",
                    extra={
                        "fields": {
                            "exchange": exchange,
                            "symbol": resolved_symbol,
                            "setup_timeframe": hierarchy.setup.timeframe,
                            "setup_as_of": setup_as_of.isoformat(),
                            "error": str(exc),
                        }
                    },
                )
                qualification = None
                qualification_frame = None

        confirmation_candles = (
            candles_by_timeframe[hierarchy.confirmation.timeframe]
            if hierarchy.confirmation is not None
            else ()
        )
        execution_candles = (
            candles_by_timeframe[hierarchy.execution.timeframe]
            if hierarchy.execution is not None
            else ()
        )

        return evaluate_hierarchy(
            hierarchy=hierarchy,
            exchange=exchange,
            symbol=resolved_symbol,
            decision_time=instant,
            boundary=boundary,
            context_structure=context_structure,
            qualification=qualification,
            qualification_frame=qualification_frame,
            confirmation_candles=confirmation_candles,
            execution_candles=execution_candles,
            strategy_versions=self._strategy_versions(qualification),
            structure_parameters=self.structure_parameters,
        )

    def ladder_payload(
        self,
        *,
        symbol: str | None = None,
        decision_time: datetime | None = None,
    ) -> dict[str, Any]:
        """The dashboard's multi-timeframe ladder for one decision instant."""

        snapshot = self.evaluate(symbol=symbol, decision_time=decision_time)
        exchange, resolved_symbol = self.resolve_instrument(symbol=symbol)
        latest = self.ledger.latest_observation(
            exchange=exchange,
            symbol=resolved_symbol,
            hierarchy_fingerprint=self.hierarchy.fingerprint(),
        )
        payload = ladder_payload(snapshot)
        payload["latest_recorded"] = (
            None
            if latest is None
            else {
                "observation_id": latest.observation_id,
                "decision_time": latest.decision_time.isoformat().replace("+00:00", "Z"),
                "decision": latest.decision,
                "status": latest.status,
                "recorded_at": latest.recorded_at.isoformat().replace("+00:00", "Z"),
            }
        )
        return payload

    def status(
        self,
        *,
        symbol: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Recorded hierarchy state plus live data-health facts."""

        exchange, resolved_symbol = self.resolve_instrument(symbol=symbol)
        instant = require_utc_datetime(now or self._clock(), field_name="now")
        latest = self.ledger.latest_observation(
            exchange=exchange,
            symbol=resolved_symbol,
            hierarchy_fingerprint=self.hierarchy.fingerprint(),
        )
        counts = self.ledger.counts(
            exchange=exchange,
            symbol=resolved_symbol,
            hierarchy_fingerprint=self.hierarchy.fingerprint(),
        )
        execution_tf = self.hierarchy.execution.timeframe
        interval = interval_for_timeframe(execution_tf)
        target_boundary = (
            latest_closed_candle_open_time(instant, execution_tf) + interval
        )
        pending = self._pending_boundaries(
            exchange=exchange,
            symbol=resolved_symbol,
            target_boundary=target_boundary,
        )
        health: dict[str, Any] = {}
        for timeframe in self.hierarchy.timeframes:
            expected_open = latest_closed_candle_open_time(instant, timeframe)
            latest_stored = self.candles.latest_timestamp(
                exchange=exchange, symbol=resolved_symbol, timeframe=timeframe
            )
            if latest_stored is None:
                state, detail = "UNKNOWN", "no stored closed candle exists"
            elif latest_stored < expected_open:
                staleness = int((expected_open - latest_stored) // interval_for_timeframe(timeframe))
                state, detail = "STALE", (
                    f"stored candles stop {staleness} interval(s) before the "
                    f"expected latest closed candle"
                )
            else:
                state, detail = "CURRENT", "the latest expected closed candle is stored"
            health[timeframe] = {
                "data_health": state,
                "data_health_detail": detail,
                "latest_stored_candle_open": (
                    None if latest_stored is None else latest_stored
                ),
                "expected_latest_closed_candle_open": expected_open,
            }
        return {
            "exchange": exchange,
            "symbol": resolved_symbol,
            "hierarchy": self.hierarchy.to_json_dict(),
            "as_of": target_boundary,
            "now": instant,
            "market_data": health,
            "latest_observation": (
                None if latest is None else latest.to_json_dict()
            ),
            "sample": {
                "observations": counts["observations"],
                "by_decision": counts["by_decision"],
                "pending_boundaries": pending,
            },
            "boundaries": {
                "latest_recorded_decision_time": (
                    None if latest is None else latest.decision_time
                ),
                "next_boundary_to_process": target_boundary if pending else None,
            },
            "limitations": HIERARCHY_LIMITATIONS,
        }

    # ------------------------------------------------------------------
    # Recording (the only write path)
    # ------------------------------------------------------------------

    def record(
        self,
        snapshot: HierarchySnapshot,
        *,
        recorded_at: datetime | None = None,
    ) -> tuple[HierarchyObservation, bool]:
        """Append one immutable hierarchy observation (idempotent)."""

        instant = require_utc_datetime(
            recorded_at if recorded_at is not None else self._clock(),
            field_name="recorded_at",
        )
        observation = HierarchyObservation.from_snapshot(
            snapshot, recorded_at=instant
        )
        return self.ledger.record_observation(observation)

    # ------------------------------------------------------------------
    # Runner entry point
    # ------------------------------------------------------------------

    def run_once(
        self,
        *,
        now: datetime | None = None,
        symbol: str | None = None,
        refresh_market_data: bool = True,
    ) -> MultiTimeframeRunResult:
        """Process every pending closed execution boundary chronologically, once."""

        instant = require_utc_datetime(now or self._clock(), field_name="now")
        exchange, resolved_symbol = self.resolve_instrument(symbol=symbol)
        execution_tf = self.hierarchy.execution.timeframe
        interval = interval_for_timeframe(execution_tf)
        target_boundary = (
            latest_closed_candle_open_time(instant, execution_tf) + interval
        )

        market_data_json = canonical_json(
            {"refreshed": False, "reason": "market-data refresh not requested"}
        )
        market_error: str | None = None
        market_error_type: str | None = None
        if refresh_market_data:
            try:
                updates = self._refresh_market_data(
                    symbol=resolved_symbol, as_of=instant
                )
                market_data_json = canonical_json(
                    {
                        "refreshed": True,
                        "timeframes": {
                            timeframe: {
                                "received_count": update.received_count,
                                "accepted_count": update.accepted_count,
                                "inserted_count": update.inserted_count,
                                "already_present_count": update.already_present_count,
                                "excluded_open_count": update.excluded_open_count,
                                "rejected_count": update.rejected_count,
                                "missing_candle_count": update.missing_candle_count,
                                "complete": update.complete,
                            }
                            for timeframe, update in updates.items()
                        },
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
                            "already-stored closed candles may be evaluated"
                        ),
                    }
                )
                logger.error(
                    "Multi-timeframe runner could not refresh public market data",
                    extra={
                        "fields": {
                            "exchange": exchange,
                            "symbol": resolved_symbol,
                            "error_type": type(exc).__name__,
                        }
                    },
                )

        pending = self._pending_boundaries(
            exchange=exchange,
            symbol=resolved_symbol,
            target_boundary=target_boundary,
        )
        if pending == 0:
            return MultiTimeframeRunResult(
                status=RunnerStatus.IDLE,
                detail=(
                    "no new closed execution boundary since the last recorded "
                    "hierarchy decision"
                    + ("" if market_error is None else f"; market-data error: {market_error}")
                ),
                exchange=exchange,
                symbol=resolved_symbol,
                processed_boundaries=(),
                observations_recorded=0,
                observations_created=0,
                pending_boundaries=0,
                latest_decision_time=self._latest_recorded_decision_time(
                    exchange=exchange, symbol=resolved_symbol
                ),
                market_data_json=market_data_json,
                market_data_error=market_error,
                market_data_error_type=market_error_type,
            )

        opens = self._pending_opens(
            exchange=exchange,
            symbol=resolved_symbol,
            target_boundary=target_boundary,
        )
        to_process = opens[: self.max_catch_up_boundaries]
        remaining = len(opens) - len(to_process)
        stored = self.candles.get_candles(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=execution_tf,
            end_time=target_boundary - interval,
        )
        if not stored.candles:
            return MultiTimeframeRunResult(
                status=RunnerStatus.NO_DATA,
                detail=(
                    "no stored closed candle exists for the execution timeframe; "
                    "no hierarchy decision was produced"
                ),
                exchange=exchange,
                symbol=resolved_symbol,
                processed_boundaries=(),
                observations_recorded=0,
                observations_created=0,
                pending_boundaries=remaining,
                latest_decision_time=None,
                market_data_json=market_data_json,
                market_data_error=market_error,
                market_data_error_type=market_error_type,
            )

        processed: list[datetime] = []
        recorded = 0
        created = 0
        for open_time in to_process:
            boundary_instant = open_time + interval
            snapshot = self.evaluate(symbol=resolved_symbol, decision_time=boundary_instant)
            _, was_created = self.record(snapshot, recorded_at=instant)
            recorded += 1
            created += 1 if was_created else 0
            processed.append(boundary_instant)
            logger.info(
                "Hierarchy decision recorded",
                extra={
                    "fields": {
                        "exchange": exchange,
                        "symbol": resolved_symbol,
                        "decision_time": boundary_instant.isoformat(),
                        "decision": snapshot.decision.value,
                        "alignment": snapshot.alignment.value,
                        "created": was_created,
                    }
                },
            )

        return MultiTimeframeRunResult(
            status=RunnerStatus.PROCESSED,
            detail=(
                f"processed {len(processed)} closed execution boundary(ies); "
                f"{created} new hierarchy observation(s)"
                + (f"; {remaining} boundary(ies) still pending" if remaining else "")
                + ("" if market_error is None else f"; market-data error: {market_error}")
            ),
            exchange=exchange,
            symbol=resolved_symbol,
            processed_boundaries=tuple(processed),
            observations_recorded=recorded,
            observations_created=created,
            pending_boundaries=remaining,
            latest_decision_time=processed[-1] if processed else None,
            market_data_json=market_data_json,
            market_data_error=market_error,
            market_data_error_type=market_error_type,
        )

    def release_database_connections(self) -> None:
        """Close pooled SQLite connections without touching a single stored row.

        Same contract as the Step 12 runner: called only from failure and
        shutdown paths, never on the success path, and never for in-memory
        databases (where disposing would destroy the schema).
        """

        if is_memory_database(self.engine):
            return
        self.engine.dispose()

    # ------------------------------------------------------------------
    # Market data (existing Step 2 service, one client for all timeframes)
    # ------------------------------------------------------------------

    def _market_data_service(self) -> MarketDataService:
        """The public market-data service, created only when one was provided.

        There is deliberately no implicit default: a hierarchy runner started
        without an explicit public data source refuses to run instead of opening
        a hidden connection. The read-only dashboard never reaches this path.
        """

        if self._market_data is not None:
            return self._market_data
        if self._market_data_factory is not None:
            self._market_data = self._market_data_factory()
            return self._market_data
        raise HierarchyNotConfigured(
            "no market-data source is configured for multi-timeframe acquisition; "
            "the runner must be started with an explicit public OHLCV source"
        )

    def bootstrap_start(self, *, timeframe: str, as_of: datetime) -> datetime:
        """First candle open the initial public download starts from.

        Mirrors the Step 12 bootstrap: ``backfill_start``/``ledger_start`` wins
        when configured; otherwise the runner seeds itself with one bounded
        window of the newest closed candles (never fewer than the configured
        runner precondition).
        """

        interval = interval_for_timeframe(timeframe)
        configured = self.ledger_start
        if configured is not None:
            requested = require_utc_datetime(configured, field_name="ledger_start")
            aligned = _next_candle_open(requested, timeframe)
            return aligned
        latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        depth = max(
            self.runner_settings.bootstrap_candles,
            self._minimum_history_candles(),
        )
        return latest_closed - (depth - 1) * interval

    def _minimum_history_candles(self) -> int:
        from trading_assistant.forward_testing.parameters import ForwardParameters

        return ForwardParameters().minimum_history_candles

    def _refresh_market_data(
        self, *, symbol: str, as_of: datetime
    ) -> dict[str, Any]:
        """Refresh every hierarchy timeframe through ONE market-data service.

        The same correctly managed client (metadata before Decimal mode,
        closed-candle filtering, aligned pagination, rolling-window handling,
        raw archiving, idempotent storage) safely supplies all timeframes;
        there is never a second exchange client per timeframe.
        """

        service = self._market_data_service()
        updates: dict[str, Any] = {}
        for timeframe in self.hierarchy.timeframes:
            updates[timeframe] = self._refresh_timeframe(
                service, symbol=symbol, timeframe=timeframe, as_of=as_of
            )
        return updates

    def _refresh_timeframe(
        self, service: MarketDataService, *, symbol: str, timeframe: str, as_of: datetime
    ):
        """Fetch one timeframe with conservative bounded retries."""

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
                    "Multi-timeframe runner retrying public market-data request",
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
    # Pending boundaries (execution-timeframe clock)
    # ------------------------------------------------------------------

    def _latest_recorded_decision_time(
        self, *, exchange: str, symbol: str
    ) -> datetime | None:
        latest = self.ledger.latest_observation(
            exchange=exchange,
            symbol=symbol,
            hierarchy_fingerprint=self.hierarchy.fingerprint(),
        )
        return None if latest is None else latest.decision_time

    def _pending_opens(
        self,
        *,
        exchange: str,
        symbol: str,
        target_boundary: datetime,
    ) -> tuple[datetime, ...]:
        """Execution-candle open times whose close boundary is pending."""

        execution_tf = self.hierarchy.execution.timeframe
        interval = interval_for_timeframe(execution_tf)
        last_open = target_boundary - interval
        start_boundary = self._start_boundary(
            exchange=exchange, symbol=symbol
        )
        start_open = last_open if start_boundary is None else start_boundary - interval
        if start_open > last_open:
            return ()
        count = int((last_open - start_open) // interval) + 1
        return tuple(start_open + index * interval for index in range(count))

    def _start_boundary(self, *, exchange: str, symbol: str) -> datetime | None:
        """The first decision boundary this pass must (re)process.

        Boundaries recorded as ``incomplete`` are retried: when missing candles
        later arrive, the boundary is re-evaluated and the recovered conclusion
        is recorded as its own row — the earlier row is never rewritten. A
        boundary that already has a complete observation is never recomputed.
        """

        execution_tf = self.hierarchy.execution.timeframe
        interval = interval_for_timeframe(execution_tf)
        fingerprint = self.hierarchy.fingerprint()
        complete: set[datetime] = set()
        unfinished: set[datetime] = set()
        for observation in self.ledger.observations(
            exchange=exchange, symbol=symbol
        ):
            if observation.hierarchy_fingerprint != fingerprint:
                continue
            if observation.status == "evaluated":
                complete.add(observation.decision_time)
            else:
                unfinished.add(observation.decision_time)
        if complete:
            next_boundary = max(complete) + interval
        elif self.ledger_start is not None:
            latest_closed = latest_closed_candle_open_time(
                self.ledger_start, execution_tf
            )
            next_boundary = (
                self.ledger_start
                if latest_closed + interval == self.ledger_start
                else latest_closed + interval
            )
        else:
            next_boundary = None
        retryable = [
            boundary
            for boundary in sorted(unfinished)
            if next_boundary is None or boundary < next_boundary
        ]
        if retryable:
            return retryable[0]
        return next_boundary

    def _pending_boundaries(
        self, *, exchange: str, symbol: str, target_boundary: datetime
    ) -> int:
        return len(
            self._pending_opens(
                exchange=exchange,
                symbol=symbol,
                target_boundary=target_boundary,
            )
        )

    # ------------------------------------------------------------------
    # Version material
    # ------------------------------------------------------------------

    def _strategy_versions(
        self, qualification: QualificationSnapshot | None
    ) -> tuple[tuple[str, str], ...]:
        """Exact version/fingerprint material recorded on every observation.

        The hierarchy fingerprint is included deliberately: a hierarchy
        configuration change must produce visibly different identities and
        cohorts, never a silent reinterpretation of decisions recorded under a
        different hierarchy. The setup/structure/pattern versions keep the
        existing Step 12 separation semantics intact for the layers this
        hierarchy reuses.
        """

        return (
            ("multi_timeframe_hierarchy_rules", HIERARCHY_RULES_VERSION),
            ("multi_timeframe_hierarchy_fingerprint", self.hierarchy.fingerprint()),
            (
                "setup_qualification_rules",
                (
                    QUALIFICATION_RULES_VERSION
                    if qualification is None
                    else qualification.rules_version
                ),
            ),
            (
                "setup_config_fingerprint",
                "" if qualification is None else qualification.config_fingerprint,
            ),
            (
                "structure_config_fingerprint",
                fingerprint("structure-parameters", self.structure_parameters),
            ),
            (
                "pattern_config_fingerprint",
                fingerprint("pattern-parameters", self.pattern_parameters),
            ),
            ("explanation_rules", EXPLANATION_RULES_VERSION),
        )


#: Safety and honesty limitations recorded on every status payload.
HIERARCHY_LIMITATIONS: tuple[str, ...] = (
    "The multi-timeframe hierarchy is decision support only. No order was "
    "placed, no fill happened, and no money exists anywhere in this system.",
    "PLANNABLE means the complete deterministic hierarchy agreed (context, "
    "setup, confirmation, execution); it is not advice, not a recommendation, "
    "and never an execution.",
    "Only fully closed candles are analysed at every layer; an unfinished "
    "candle is never used.",
    "Lower timeframes refine the setup layer; they never create a trade on "
    "their own and never overwrite the higher-timeframe context.",
    "Missing or stale market data is reported and never filled in with a guess.",
    "Hierarchy observations are separated by the exact recorded "
    "hierarchy/config version fingerprints that produced them.",
)


def _next_candle_open(value: datetime, timeframe: str) -> datetime:
    """Round an instant up to the candle open at or after it (never earlier)."""

    interval_ms = timeframe_to_milliseconds(timeframe)
    anchor_ms = timeframe_anchor_milliseconds(timeframe)
    value_ms = datetime_to_milliseconds(value, field_name="ledger_start")
    remainder = (value_ms - anchor_ms) % interval_ms
    aligned_ms = value_ms if remainder == 0 else value_ms + (interval_ms - remainder)
    return milliseconds_to_datetime(aligned_ms)


def _is_retryable(exc: Exception) -> bool:
    """Only transient public-market-data failures are retried."""

    from trading_assistant.market_data.errors import ExchangeDataError

    return isinstance(exc, ExchangeDataError)


__all__ = [
    "HIERARCHY_LIMITATIONS",
    "MultiTimeframeRunResult",
    "MultiTimeframeService",
    "RunnerStatus",
]
