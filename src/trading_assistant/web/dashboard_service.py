"""Dashboard assembly: one authoritative payload from deterministic Steps 2–9.

The service never re-derives qualification, planning, or statistics; it calls
the existing services at one explicit UTC boundary and serializes their
canonical projections. The ``QualificationFrame`` reconstruction mirrors the
frame construction inside :class:`QualificationService` with identical default
parameters, so planning and explanation consume exactly what Step 5 saw.
"""

from __future__ import annotations

import logging
from datetime import datetime

from trading_assistant.journaling.parameters import normalize_note
from trading_assistant.journaling.types import DecisionState
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.higher_timeframe import (
    build_higher_timeframe_context,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.parameters import (
    PatternLiquidityParameters,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    SetupResult,
    SetupState,
)
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.trade_planning.models import PlanState, TradePlanResult
from trading_assistant.trade_planning.planner import plan_trade
from trading_assistant.web.freshness import FreshnessReport, evaluate_freshness
from trading_assistant.web.state import AppState

logger = logging.getLogger(__name__)

#: Candles returned with the dashboard payload for the qualification chart.
DASHBOARD_CANDLE_LIMIT = 500
MAX_CANDLE_LIMIT = 1500


class DashboardError(ValueError):
    """A caller-facing dashboard problem with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error_payload(code: str, message: str) -> dict[str, object]:
    return {"available": False, "error": {"code": code, "message": message}}


def _candle_row(candle) -> list[object]:
    """Exact decimal strings plus millisecond open time; no display rounding."""

    return [
        int(candle.timestamp.timestamp() * 1000),
        format(candle.open, "f"),
        format(candle.high, "f"),
        format(candle.low, "f"),
        format(candle.close, "f"),
        format(candle.volume, "f"),
    ]


class DashboardService:
    """Read-only assembly of the current-market screen payload."""

    def __init__(self, state: AppState) -> None:
        self.state = state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dashboard(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        state = self.state
        resolved_symbol = state.require_symbol(symbol)
        resolved_timeframe = (
            state.settings.default_timeframe if timeframe is None else timeframe
        )
        state.require_supported_timeframe(resolved_timeframe)
        exchange = state.settings.exchange
        now = state.now()

        resolved_as_of = self._resolve_as_of(
            timeframe=resolved_timeframe, as_of=as_of, now=now
        )
        if as_of is None:
            resolved_as_of = self._default_as_of(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                current_boundary=resolved_as_of,
            )
        market = self._market(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        freshness = self._freshness(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
            now=now,
            market=market,
        )
        qualification = self._qualification(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        snapshot: QualificationSnapshot | None = qualification.get("_snapshot")
        frame: QualificationFrame | None = qualification.get("_frame")
        selected: SetupResult | None = qualification.get("_selected_setup")

        plan: TradePlanResult | None = None
        planning_payload: dict[str, object] = {
            "state": None,
            "reasons": [],
            "missing_inputs": [],
            "state_detail": None,
        }
        if selected is not None and snapshot is not None and frame is not None:
            plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=selected.id)
            planning_payload = {
                "state": plan.state.value,
                "reasons": list(plan.reasons),
                "missing_inputs": list(plan.missing_inputs),
                "state_detail": plan.state_detail,
            }

        journal_payload = self._journal_status(
            snapshot=snapshot, selected=selected, plan=plan
        )
        explanation_payload = self._explanation(
            snapshot=snapshot,
            frame=frame,
            selected=selected,
            plan=plan,
            journal_id=journal_payload.get("journal_id"),
            cutoff=resolved_as_of,
        )
        overlays = self._overlays(frame=frame, snapshot=snapshot, selected=selected)

        return {
            "meta": {
                "exchange": exchange,
                "symbol": resolved_symbol,
                "timeframe": resolved_timeframe,
                "as_of": to_jsonable(resolved_as_of),
                "generated_at": to_jsonable(now),
            },
            "market": market,
            "freshness": {
                "status": freshness.status,
                "reason": freshness.reason,
                "is_current_boundary": freshness.is_current_boundary,
                "staleness_intervals": freshness.staleness_intervals,
                "expected_latest_closed": to_jsonable(freshness.expected_latest_closed),
                "latest_stored": to_jsonable(freshness.latest_stored),
            },
            "qualification": qualification["payload"],
            "plan": None if plan is None else plan.to_json_dict(),
            "planning": planning_payload,
            "overlays": overlays,
            "journal": journal_payload,
            "explanation": explanation_payload,
        }

    def candles(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        limit: int = DASHBOARD_CANDLE_LIMIT,
        end_time: datetime | None = None,
    ) -> dict[str, object]:
        state = self.state
        resolved_symbol = state.require_symbol(symbol)
        resolved_timeframe = (
            state.settings.default_timeframe if timeframe is None else timeframe
        )
        state.require_supported_timeframe(resolved_timeframe)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise DashboardError("invalid_limit", "limit must be a positive integer")
        limit = min(limit, MAX_CANDLE_LIMIT)
        exchange = state.settings.exchange
        boundary = (
            state.boundary_for(resolved_timeframe, require_utc_datetime(end_time))
            if end_time is not None
            else state.current_boundary(resolved_timeframe)
        )
        expected_latest_closed = latest_closed_candle_open_time(
            boundary, resolved_timeframe
        )
        # Read the full stored series so completeness reflects real gaps (never
        # a window that starts before the first stored candle), then slice.
        result = state.candles.get_candles(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            end_time=expected_latest_closed,
        )
        window = result.candles[-limit:]
        return {
            "exchange": exchange,
            "symbol": resolved_symbol,
            "timeframe": resolved_timeframe,
            "as_of": to_jsonable(boundary),
            "candles": [_candle_row(candle) for candle in window],
            "gaps": to_jsonable(result.gaps),
            "complete": result.complete,
            "returned_count": len(window),
        }

    def structure(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        """Step 3 structure overlays for one timeframe (never Step 4/5 facts)."""

        from trading_assistant.market_structure.service import MarketStructureService

        state = self.state
        resolved_symbol = state.require_symbol(symbol)
        resolved_timeframe = (
            state.settings.default_timeframe if timeframe is None else timeframe
        )
        state.require_supported_timeframe(resolved_timeframe)
        resolved_as_of = (
            require_utc_datetime(as_of) if as_of is not None else state.now()
        )
        service = MarketStructureService(state.engine, settings=state.settings)
        snapshot = service.snapshot(
            exchange=state.settings.exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        analysis = snapshot.analysis
        return {
            "exchange": state.settings.exchange,
            "symbol": resolved_symbol,
            "timeframe": resolved_timeframe,
            "as_of": to_jsonable(snapshot.as_of),
            "candle_count": analysis.candle_count,
            "trend": to_jsonable(analysis.trend),
            "zones": to_jsonable(analysis.levels.zones),
            "range": to_jsonable(analysis.detected_range),
            "swings": to_jsonable(analysis.confirmed_swings),
            "completeness": to_jsonable(snapshot.completeness),
        }

    def decide_current(
        self,
        *,
        symbol: str | None,
        timeframe: str | None,
        as_of: datetime,
        setup_id: str,
        decision: str,
        reason: str | None,
    ) -> dict[str, object]:
        """Journal the exact proposal at ``as_of`` and append one decision.

        Deterministic replay means the journaled snapshot is byte-identical to
        what the dashboard displayed for that instant; the decision therefore
        applies to exactly the setup/plan snapshot the user reviewed.
        """

        state = self.state
        resolved_symbol = state.require_symbol(symbol)
        resolved_timeframe = (
            state.settings.default_timeframe if timeframe is None else timeframe
        )
        state.require_supported_timeframe(resolved_timeframe)
        resolved_as_of = self._resolve_as_of(
            timeframe=resolved_timeframe,
            as_of=require_utc_datetime(as_of, field_name="as_of"),
            now=state.now(),
        )
        snapshot, frame = self._evaluate(
            exchange=state.settings.exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        selected = next((s for s in snapshot.setups if s.id == setup_id), None)
        if selected is None:
            raise DashboardError(
                "setup_not_found",
                f"setup {setup_id} does not exist in the {resolved_symbol} "
                f"{resolved_timeframe} snapshot at {resolved_as_of.isoformat()}",
            )
        if selected.state is not SetupState.QUALIFIED:
            raise DashboardError(
                "proposal_not_decidable",
                f"setup state is {selected.state.value}; only QUALIFIED proposals "
                "can receive a decision",
            )
        plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=setup_id)
        if plan.state is not PlanState.PLANNABLE:
            raise DashboardError(
                "proposal_not_decidable",
                "the deterministic planner refused a plan "
                f"({plan.state.value}: {', '.join(plan.reasons) or plan.state_detail}); "
                "there is no proposed plan to accept, reject, or skip",
            )
        record = state.journal.journal_plan(snapshot=snapshot, plan=plan)
        return self._append_decision(
            journal_id=record.journal_id, decision=decision, reason=reason
        ) | {
            "journal_id": record.journal_id,
            "setup_id": setup_id,
            "plan_id": plan.id,
            "as_of": to_jsonable(resolved_as_of),
        }

    def decide_record(
        self, *, journal_id: str, decision: str, reason: str | None
    ) -> dict[str, object]:
        state = self.state
        if state.journal.repository.find_record(journal_id) is None:
            raise DashboardError(
                "journal_record_not_found", f"no journal record {journal_id}"
            )
        return self._append_decision(
            journal_id=journal_id, decision=decision, reason=reason
        ) | {"journal_id": journal_id}

    def observe_record(
        self, *, journal_id: str, observed_through: datetime | None
    ) -> dict[str, object]:
        state = self.state
        record = state.journal.repository.find_record(journal_id)
        if record is None:
            raise DashboardError(
                "journal_record_not_found", f"no journal record {journal_id}"
            )
        if record.plan_state is not PlanState.PLANNABLE:
            raise DashboardError(
                "not_observable",
                "only journaled PLANNABLE proposals carry observable levels",
            )
        cutoff = (
            require_utc_datetime(observed_through, field_name="observed_through")
            if observed_through is not None
            else state.current_boundary(record.timeframe)
        )
        observation = state.journal.observe_outcome(
            journal_id=journal_id, observed_through=cutoff
        )
        return observation.to_json_dict()

    # ------------------------------------------------------------------
    # Assembly helpers
    # ------------------------------------------------------------------

    def _append_decision(
        self, *, journal_id: str, decision: str, reason: str | None
    ) -> dict[str, object]:
        """Append one decision with exact duplicate suppression.

        An identical effective decision (same state and same normalized note)
        returns the stored row instead of appending; any different decision is
        a correction and appends a new superseding row per Step 7 semantics.
        """

        state = self.state
        normalized = normalize_note(reason)
        latest = state.journal.latest_decision(journal_id=journal_id)
        if (
            latest is not None
            and latest.decision is DecisionState(decision)
            and latest.reason == normalized
        ):
            return {"duplicate": True, "decision": latest.to_json_dict()}
        # decided_at comes from the application clock so the append is
        # reproducible and consistent with the injected time source.
        recorded = state.journal.record_decision(
            journal_id=journal_id,
            decision=decision,
            decided_at=state.now(),
            reason=reason,
        )
        return {"duplicate": False, "decision": recorded.to_json_dict()}

    def _default_as_of(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        current_boundary: datetime,
    ) -> datetime:
        """Newest evaluable boundary: never replay through empty future frames.

        When stored data stops before the current boundary (stale series), the
        default evaluation instant snaps to the close of the latest stored
        candle. The freshness report still compares against the app clock, so
        the screen is labelled HISTORICAL/STALE — the snap is a display
        default, never hidden.
        """

        latest = self.state.candles.latest_timestamp(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        if latest is None:
            return current_boundary
        last_data_boundary = latest + interval_for_timeframe(timeframe)
        return min(current_boundary, last_data_boundary)

    def _resolve_as_of(
        self,
        *,
        timeframe: str,
        as_of: datetime | None,
        now: datetime,
    ) -> datetime:
        """Default to the newest boundary; validate explicit instants strictly.

        Historical boundaries are allowed and are labelled HISTORICAL by the
        freshness report; future or off-boundary instants are rejected.
        """

        state = self.state
        current_boundary = state.boundary_for(timeframe, now)
        if as_of is None:
            return current_boundary
        resolved = require_utc_datetime(as_of, field_name="as_of")
        interval = interval_for_timeframe(timeframe)
        expected = latest_closed_candle_open_time(resolved, timeframe)
        if expected + interval != resolved:
            raise DashboardError(
                "as_of_not_aligned",
                "as_of must be an exact candle-close boundary for timeframe "
                f"{timeframe!r} (the instant a candle finishes closing)",
            )
        if resolved > current_boundary:
            raise DashboardError(
                "as_of_in_future",
                "as_of is after the newest closed-candle boundary; the dashboard "
                "only evaluates stored closed candles",
            )
        return resolved

    def _freshness(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        now: datetime,
        market: dict[str, object],
    ) -> FreshnessReport:
        expected_latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        global_latest = self.state.candles.latest_timestamp(
            exchange=exchange, symbol=symbol, timeframe=timeframe
        )
        latest_closed_candle = market.get("latest_closed_candle")
        bounded_latest: datetime | None = None
        if isinstance(latest_closed_candle, dict):
            raw = latest_closed_candle.get("timestamp")
            if isinstance(raw, str):
                bounded_latest = datetime.fromisoformat(raw)
        if (
            bounded_latest is None
            and global_latest is not None
            and global_latest <= expected_latest_closed
        ):
            bounded_latest = global_latest
        return evaluate_freshness(
            timeframe=timeframe, as_of=as_of, now=now, latest_stored=bounded_latest
        )

    def _market(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> dict[str, object]:
        expected_latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        result = self.state.candles.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=expected_latest_closed,
        )
        latest = result.candles[-1] if result.candles else None
        return {
            "candles": [
                _candle_row(candle)
                for candle in result.candles[-DASHBOARD_CANDLE_LIMIT:]
            ],
            "latest_closed_candle": None
            if latest is None
            else {
                "timestamp": to_jsonable(latest.timestamp),
                "open": format(latest.open, "f"),
                "high": format(latest.high, "f"),
                "low": format(latest.low, "f"),
                "close": format(latest.close, "f"),
                "volume": format(latest.volume, "f"),
            },
            "gaps": to_jsonable(result.gaps),
            "complete": result.complete,
            "missing_candle_count": result.missing_candle_count,
        }

    def _qualification(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> dict[str, object]:
        try:
            snapshot, frame = self._evaluate(
                exchange=exchange, symbol=symbol, timeframe=timeframe, as_of=as_of
            )
        except DashboardError:
            raise
        except ValueError as exc:
            raise DashboardError("qualification_unavailable", str(exc)) from exc
        except Exception as exc:  # surface as structured error, never a 500 page
            logger.exception("qualification evaluation failed")
            raise DashboardError("qualification_unavailable", str(exc)) from exc

        qualified = tuple(s for s in snapshot.setups if s.state is SetupState.QUALIFIED)
        selected = min(qualified, key=lambda s: (s.created_at, s.id), default=None)
        setups_summary = [
            {
                "id": setup.id,
                "family": to_jsonable(setup.family),
                "direction": to_jsonable(setup.direction),
                "state": to_jsonable(setup.state),
                "created_at": to_jsonable(setup.created_at),
                "as_of": to_jsonable(setup.as_of),
                "ended_at": to_jsonable(setup.ended_at),
                "terminal_reason": setup.terminal_reason,
            }
            for setup in snapshot.setups
        ]
        payload = {
            "available": True,
            "state": snapshot.state.value,
            "status": snapshot.status,
            "reasons": list(snapshot.reasons),
            "rules_version": snapshot.rules_version,
            "config_fingerprint": snapshot.config_fingerprint,
            "source_timeframes": list(snapshot.source_timeframes),
            "setups": setups_summary,
            "selected_setup_id": None if selected is None else selected.id,
            "snapshot": snapshot.to_json_dict(),
        }
        return {
            "payload": payload,
            "_snapshot": snapshot,
            "_frame": frame,
            "_selected_setup": selected,
        }

    def _evaluate(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> tuple[QualificationSnapshot, QualificationFrame]:
        state = self.state
        snapshot = state.qualification.snapshot(
            exchange=exchange, symbol=symbol, timeframe=timeframe, as_of=as_of
        )
        frame = self._build_frame(
            exchange=exchange, symbol=symbol, timeframe=timeframe, as_of=as_of
        )
        return snapshot, frame

    def _build_frame(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
    ) -> QualificationFrame:
        """Reconstruct the exact frame QualificationService used at ``as_of``."""

        state = self.state
        p = parameters or QualificationParameters()
        sp = structure_parameters or MarketStructureParameters()
        source = state.patterns.snapshot(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=pattern_parameters,
            structure_parameters=sp,
        )
        higher = []
        for higher_timeframe in p.higher_timeframes:
            expected = latest_closed_candle_open_time(as_of, higher_timeframe)
            result = state.patterns.repository.get_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=higher_timeframe,
                end_time=expected,
            )
            higher.append(
                build_higher_timeframe_context(
                    higher_timeframe,
                    result.candles,
                    interval=interval_for_timeframe(higher_timeframe),
                    as_of=as_of,
                    expected_latest_closed_open_time=expected,
                    parameters=sp,
                    gaps=result.gaps,
                )
            )
        return QualificationFrame(source, tuple(higher))

    def _journal_status(
        self,
        *,
        snapshot: QualificationSnapshot | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
    ) -> dict[str, object]:
        if snapshot is None or selected is None:
            can_decide = False
            if snapshot is None:
                disabled_reason = "No qualification snapshot is available to decide."
            elif snapshot.state is SetupState.NO_SETUP:
                disabled_reason = "No qualifying setup is currently present."
            else:
                disabled_reason = (
                    "Evidence is still developing; no qualified proposal to decide."
                )
            return {
                "journal_id": None,
                "latest_decision": None,
                "decision_history_count": 0,
                "can_decide": can_decide,
                "disabled_reason": disabled_reason,
            }
        state = self.state
        records = state.journal.records_for_snapshot(snapshot_id=snapshot)
        record = next((item for item in records if item.setup_id == selected.id), None)
        latest = None
        history_count = 0
        if record is not None:
            latest = state.journal.latest_decision(journal_id=record.journal_id)
            history_count = len(
                state.journal.decision_history(journal_id=record.journal_id)
            )
        if plan is not None and plan.state is PlanState.PLANNABLE:
            can_decide = True
            disabled_reason = None
        else:
            can_decide = False
            if plan is None:
                disabled_reason = (
                    "The planner produced no result for this setup; there is no "
                    "proposed plan to accept, reject, or skip."
                )
            elif plan.state is PlanState.NO_PLAN:
                disabled_reason = (
                    "Step 6 refused a plan (NO_PLAN: "
                    + (", ".join(plan.reasons) or plan.state_detail or "missing inputs")
                    + "); there is nothing to decide."
                )
            else:
                disabled_reason = (
                    "Step 6 produced an INVALID plan (invariant violation: "
                    + (", ".join(plan.reasons) or "unknown")
                    + "); it is reported, never corrected, and cannot be decided."
                )
        return {
            "journal_id": None if record is None else record.journal_id,
            "latest_decision": None if latest is None else latest.to_json_dict(),
            "decision_history_count": history_count,
            "can_decide": can_decide,
            "disabled_reason": disabled_reason,
        }

    def _explanation(
        self,
        *,
        snapshot: QualificationSnapshot | None,
        frame: QualificationFrame | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
        journal_id: str | None,
        cutoff: datetime,
    ) -> dict[str, object]:
        if snapshot is None:
            return _error_payload(
                "no_snapshot", "No qualification snapshot is available to explain."
            )
        state = self.state
        try:
            journal_record = None
            latest_decision = None
            if journal_id is not None:
                journal_record = state.journal.get_record(journal_id=journal_id)
                latest_decision = state.journal.latest_decision(journal_id=journal_id)
            statistics_report = None
            try:
                # The statistics cutoff is the dashboard evaluation instant:
                # anything computed past it would be future data for Step 9.
                statistics_report = state.statistics.analyze(as_of=cutoff)
            except (ValueError, TypeError):
                # Statistics are optional enrichment for the explanation; a
                # dataset problem must never break the deterministic sections.
                logger.warning("statistics report unavailable for explanation")
                statistics_report = None
            context = state.explanations.build_context(
                snapshot=snapshot,
                setup_id=None if selected is None else selected.id,
                frame=frame,
                plan=plan,
                journal_record=journal_record,
                latest_decision=latest_decision,
                statistics_report=statistics_report,
                statistics_config=state.statistics_config,
            )
            result = state.explanations.explain(context)
            return result.to_json_dict()
        except Exception as exc:
            logger.exception("explanation generation failed")
            return _error_payload("explanation_unavailable", str(exc))

    def _overlays(
        self,
        *,
        frame: QualificationFrame | None,
        snapshot: QualificationSnapshot | None,
        selected: SetupResult | None,
    ) -> dict[str, object]:
        if frame is None:
            return {
                "equal_levels": [],
                "zones": [],
                "range": None,
                "swings": [],
                "setup_reference": None,
            }
        structure = frame.patterns.structure
        equal_levels = [
            {
                "id": cluster.id,
                "type": cluster.type,
                "level": format(cluster.center, "f"),
                "band_low": format(cluster.band_low, "f"),
                "band_high": format(cluster.band_high, "f"),
                "member_count": cluster.member_count,
                "known_at": to_jsonable(cluster.known_at),
            }
            for cluster in frame.patterns.equal_levels
        ]
        setup_reference = None
        if selected is not None:
            reference = self._reference_level(
                frame=frame, reference_id=selected.reference_id
            )
            if reference is not None:
                setup_reference = {
                    "reference_id": selected.reference_id,
                    "type": reference["type"],
                    "band_low": reference["band_low"],
                    "band_high": reference["band_high"],
                }
        return {
            "equal_levels": equal_levels,
            "zones": to_jsonable(structure.levels.zones),
            "range": to_jsonable(structure.detected_range),
            "swings": to_jsonable(structure.confirmed_swings),
            "setup_reference": setup_reference,
        }

    @staticmethod
    def _reference_level(
        *, frame: QualificationFrame, reference_id: str | None
    ) -> dict[str, str] | None:
        if reference_id is None:
            return None
        for event in frame.patterns.events():
            reference = getattr(event, "reference", None)
            if reference is not None and reference.id == reference_id:
                return {
                    "type": reference.type,
                    "band_low": format(reference.band_low, "f"),
                    "band_high": format(reference.band_high, "f"),
                }
        return None
