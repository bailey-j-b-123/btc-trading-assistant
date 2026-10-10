"""Dashboard assembly: one authoritative payload from deterministic Steps 2–9.

The service never re-derives qualification, planning, or statistics; it calls
the existing services at one explicit UTC boundary and serializes their
canonical projections. The ``QualificationFrame`` reconstruction mirrors the
frame construction inside :class:`QualificationService` with identical default
parameters, so planning and explanation consume exactly what Step 5 saw.
"""

from __future__ import annotations

import copy
import logging
from collections import OrderedDict
from datetime import datetime

from trading_assistant.journaling.parameters import normalize_note
from trading_assistant.journaling.types import DecisionState
from trading_assistant.market_data.integrity import (
    RequiredWindowAssessment,
    assess_required_window,
    required_trailing_depth,
)
from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    FailedBreakout,
    Retest,
    Sweep,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.market_structure.trend import TrendDirection
from trading_assistant.multi_timeframe import (
    HIERARCHY_LIMITATIONS,
    HierarchySnapshot,
    ladder_payload as hierarchy_ladder_payload,
)
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    RuleOutcome,
    SetupFamily,
    SetupResult,
    SetupState,
)
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.setup_qualification.service import bounded_replay_start
from trading_assistant.trade_planning.models import PlanState, TradePlanResult
from trading_assistant.trade_planning.planner import plan_trade
from trading_assistant.web.freshness import FreshnessReport, evaluate_freshness
from trading_assistant.web.state import AppState

#: Small in-process memo for chart evidence (bounded; keyed on the exact window).
_CHART_EVIDENCE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CHART_EVIDENCE_CACHE_LIMIT = 32

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
        frames: tuple[QualificationFrame, ...] = qualification.get("_frames", ())
        selected: SetupResult | None = qualification.get("_selected_setup")

        # Component #1 gate: the dashboard may describe the evidence state,
        # but no plan is produced and no decision is accepted when the
        # required window at this exact close is incomplete — mirroring the
        # forward runner's planning-withheld verdict for the same instant.
        required_window = self._required_window(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        window_gate: str | None = None
        if not required_window.complete:
            window_gate = (
                f"planning withheld: the required {resolved_timeframe} window "
                f"is missing {required_window.missing_count} expected "
                f"candle(s): {required_window.missing_summary()} — the stored "
                "history is incomplete at this close, so no plan is produced "
                "and no decision is accepted"
            )
        plan: TradePlanResult | None = None
        planning_payload: dict[str, object] = {
            "state": None,
            "reasons": [],
            "missing_inputs": [],
            "state_detail": None,
        }
        if (
            selected is not None
            and snapshot is not None
            and frame is not None
            and window_gate is None
        ):
            plan = plan_trade(snapshot=snapshot, frame=frame, setup_id=selected.id)
            planning_payload = {
                "state": plan.state.value,
                "reasons": list(plan.reasons),
                "missing_inputs": list(plan.missing_inputs),
                "state_detail": plan.state_detail,
            }
        if window_gate is not None:
            planning_payload = {
                "state": None,
                "reasons": [window_gate],
                "missing_inputs": [],
                "state_detail": window_gate,
            }

        journal_payload = self._journal_status(
            snapshot=snapshot, selected=selected, plan=plan, gate=window_gate
        )
        # Step 13: evaluate the multi-timeframe hierarchy once, at this same
        # decision instant, and feed the identical snapshot to the ladder
        # payload and the Step 9 explanation (one evaluation, two readers).
        hierarchy_snapshot, multi_timeframe_payload = self._multi_timeframe(
            symbol=resolved_symbol, as_of=resolved_as_of
        )
        explanation_payload = self._explanation(
            snapshot=snapshot,
            frame=frame,
            selected=selected,
            plan=plan,
            journal_id=journal_payload.get("journal_id"),
            cutoff=resolved_as_of,
            hierarchy=hierarchy_snapshot,
        )
        overlays = self._overlays(frame=frame, snapshot=snapshot, selected=selected)
        market_state = self._market_state(
            frame=frame,
            frames=frames,
            snapshot=snapshot,
            timeframe=resolved_timeframe,
        )
        scenario = self._scenario(
            snapshot=snapshot,
            selected=selected,
            plan=plan,
            market_state=market_state,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )

        looking_for = self._looking_for(
            frame=frame, snapshot=snapshot, selected=selected, plan=plan
        )

        return {
            "looking_for": looking_for,
            "meta": {
                "exchange": exchange,
                "symbol": resolved_symbol,
                "timeframe": resolved_timeframe,
                "as_of": to_jsonable(resolved_as_of),
                "generated_at": to_jsonable(now),
            },
            "market_state": market_state,
            "scenario": scenario,
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
            "chart_evidence": self._chart_evidence_at(
                exchange=exchange,
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                as_of=resolved_as_of,
            ),
            "journal": journal_payload,
            "explanation": explanation_payload,
            "multi_timeframe": multi_timeframe_payload,
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

    def chart_evidence(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, object]:
        """Chart evidence for one timeframe at one instant (read-only).

        Runs the existing Step 3/4 detectors over the last
        ``CHART_EVIDENCE_WINDOW`` closed candles at ``as_of`` (default: the
        current closed boundary) and projects them for the chart. It does not
        alter the engine's qualification, plan, journal, or forward ledger.
        """

        state = self.state
        resolved_symbol = state.require_symbol(symbol)
        resolved_timeframe = (
            state.settings.default_timeframe if timeframe is None else timeframe
        )
        state.require_supported_timeframe(resolved_timeframe)
        exchange = state.settings.exchange
        now = state.now()
        if as_of is None:
            resolved_as_of = state.current_boundary(resolved_timeframe)
        else:
            resolved_as_of = self._resolve_as_of(
                timeframe=resolved_timeframe,
                as_of=require_utc_datetime(as_of, field_name="as_of"),
                now=now,
            )
        return self._chart_evidence_at(
            exchange=exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )

    def _chart_evidence_at(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> dict[str, object]:
        from trading_assistant.market_data.timeframes import (
            latest_closed_candle_open_time as _latest_open,
        )
        from trading_assistant.market_structure.candles import interval_for_timeframe
        from trading_assistant.pattern_liquidity.analysis import analyze_patterns
        from trading_assistant.web.chart_evidence import (
            CHART_EVIDENCE_WINDOW,
            build_chart_evidence,
            unavailable_chart_evidence,
        )

        interval = interval_for_timeframe(timeframe)
        stored = self.state.candles.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=_latest_open(as_of, timeframe),
        )
        window = tuple(stored.candles[-CHART_EVIDENCE_WINDOW:])
        if not window:
            return unavailable_chart_evidence(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
                reason="No stored closed candles exist at this decision time.",
            )
        # Chart evidence is a pure function of (instant, exact candle window):
        # the same inputs always give the same payload, so repeat reads (dashboard
        # refreshes, switching back to a timeframe) reuse it. Failures are never cached.
        cache_key = (exchange, symbol, timeframe, as_of, window)
        cached = _CHART_EVIDENCE_CACHE.get(cache_key)
        if cached is not None:
            _CHART_EVIDENCE_CACHE.move_to_end(cache_key)
            return copy.deepcopy(cached)
        try:
            snapshot = analyze_patterns(
                window,
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
            )
        except ValueError as exc:
            return unavailable_chart_evidence(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
                reason=f"Stored candles could not be analysed: {exc}",
            )
        payload = build_chart_evidence(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            snapshot=snapshot,
            candles=window,
            interval=interval,
        )
        _CHART_EVIDENCE_CACHE[cache_key] = copy.deepcopy(payload)
        while len(_CHART_EVIDENCE_CACHE) > _CHART_EVIDENCE_CACHE_LIMIT:
            _CHART_EVIDENCE_CACHE.popitem(last=False)
        return payload

    def _required_window(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> RequiredWindowAssessment:
        expected_latest_closed = latest_closed_candle_open_time(as_of, timeframe)
        result = self.state.candles.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=expected_latest_closed,
        )
        return assess_required_window(
            result.candles,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            decision_time=as_of,
            depth=required_trailing_depth(
                structure=MarketStructureParameters(),
                pattern=PatternLiquidityParameters(),
                qualification=QualificationParameters(),
            ),
        )

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
        snapshot, frame, _frames = self._evaluate(
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
        required_window = self._required_window(
            exchange=state.settings.exchange,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            as_of=resolved_as_of,
        )
        if not required_window.complete:
            raise DashboardError(
                "proposal_not_decidable",
                f"planning withheld: the required {resolved_timeframe} window "
                f"is missing {required_window.missing_count} expected "
                f"candle(s): {required_window.missing_summary()} — the stored "
                "history is incomplete at this close, so no plan is produced "
                "and no decision is accepted",
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
            "required_window": self._required_window(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=as_of,
            ).to_json_dict(),
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
            snapshot, frame, frames = self._evaluate(
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
            "_frames": frames,
            "_selected_setup": selected,
        }

    def _evaluate(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
    ) -> tuple[
        QualificationSnapshot, QualificationFrame, tuple[QualificationFrame, ...]
    ]:
        # One bounded frame build shared by the snapshot and the plan: the
        # frame handed to Step 6 is the replay frame itself, not a separately
        # reconstructed copy, and the replay never grows with stored history.
        state = self.state
        parameters = QualificationParameters()
        frames = state.qualification.build_frames(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            parameters=parameters,
            start_at=bounded_replay_start(
                as_of=as_of, timeframe=timeframe, parameters=parameters
            ),
        )
        snapshots = enumerate_qualifications(
            frames, as_of=as_of, parameters=parameters
        )
        return snapshots[-1], frames[-1], frames

    def _journal_status(
        self,
        *,
        snapshot: QualificationSnapshot | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
        gate: str | None = None,
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
            if gate is not None:
                disabled_reason = gate
            elif plan is None:
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

    def _multi_timeframe(
        self, *, symbol: str, as_of: datetime
    ) -> tuple[HierarchySnapshot | None, dict[str, object]]:
        """Evaluate the Step 13 hierarchy at the dashboard decision instant.

        The ladder is one compact card: it never redesigns the dashboard and
        never replaces any existing section. A hierarchy evaluation failure is
        degraded to an explicit unavailable payload: the rest of the dashboard
        is unaffected, and the failure is never hidden.
        """

        state = self.state
        service = state.multi_timeframe
        if service is None:
            return None, {
                "available": False,
                "error": {
                    "type": "HierarchyNotConfigured",
                    "message": (
                        state.multi_timeframe_unavailable_reason
                        or "the multi-timeframe hierarchy service is not configured"
                    ),
                },
                "limitations": HIERARCHY_LIMITATIONS,
            }
        try:
            snapshot = service.evaluate(symbol=symbol, decision_time=as_of)
        except Exception as exc:
            logger.exception("multi-timeframe hierarchy evaluation failed")
            return None, {
                "available": False,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "limitations": HIERARCHY_LIMITATIONS,
            }
        payload = hierarchy_ladder_payload(snapshot)
        latest = service.ledger.latest_observation(
            exchange=state.settings.exchange,
            symbol=symbol,
            hierarchy_fingerprint=service.hierarchy.fingerprint(),
        )
        payload["latest_recorded"] = (
            None
            if latest is None
            else {
                "observation_id": latest.observation_id,
                "decision_time": latest.decision_time.isoformat().replace(
                    "+00:00", "Z"
                ),
                "decision": latest.decision,
                "status": latest.status,
                "recorded_at": latest.recorded_at.isoformat().replace("+00:00", "Z"),
            }
        )
        return snapshot, payload

    def _explanation(
        self,
        *,
        snapshot: QualificationSnapshot | None,
        frame: QualificationFrame | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
        journal_id: str | None,
        cutoff: datetime,
        hierarchy: HierarchySnapshot | None = None,
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
                hierarchy=hierarchy,
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

    def _looking_for(
        self,
        *,
        frame: QualificationFrame | None,
        snapshot: QualificationSnapshot | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
    ) -> dict[str, object]:
        """Read-only chart projection. Never selects a trade or changes a setup.

        A single WATCH can be described, but multiple WATCH setups have no
        uniquely relevant reference. In that case show no scenario rather than
        arbitrarily privileging one. Only Step 6 PLANNABLE levels are exposed.
        """
        if frame is None or snapshot is None or snapshot.status != "evaluated":
            return {"available": False, "reason": "Setup information unavailable."}
        watches = [s for s in snapshot.setups if s.state is SetupState.WATCH]
        subject = selected or (watches[0] if len(watches) == 1 else None)
        if subject is None:
            return {
                "available": False,
                "reason": (
                    "Several setups are developing; no single chart scenario is selected."
                    if watches
                    else "No active setup at this close."
                ),
            }
        reference = self._reference_level(
            frame=frame, reference_id=subject.reference_id
        )
        seed = next(
            (e for e in frame.patterns.events() if e.id == subject.seed_event_id), None
        )
        seed_kind = {
            Breakout: "breakout",
            FailedBreakout: "failed_breakout",
            Sweep: "sweep",
            Retest: "retest",
        }.get(type(seed))
        return {
            "available": True,
            "setup_id": subject.id,
            "timeframe": snapshot.timeframe,
            "family": subject.family.value,
            "direction": subject.direction,
            "state": subject.state.value,
            "reference": {"reference_id": subject.reference_id, **reference}
            if reference
            else None,
            "seed_event": None
            if seed_kind is None
            else {
                "kind": seed_kind,
                "known_at": to_jsonable(seed.known_at),
            },
            "pending_required": [
                {"rule_id": r.rule_id, "reason": r.reason}
                for r in subject.rules
                if r.required and r.outcome is RuleOutcome.PENDING
            ],
            "invalidation": (
                format(plan.invalidation.value, "f")
                if selected is not None
                and subject.id == selected.id
                and plan is not None
                and plan.state is PlanState.PLANNABLE
                and plan.invalidation.value is not None
                else None
            ),
        }

    @staticmethod
    def _reference_level(
        *, frame: QualificationFrame, reference_id: str | None
    ) -> dict[str, str] | None:
        if reference_id is None:
            return None
        for event in frame.patterns.events():
            # Resolve per event kind instead of guessing an attribute: a
            # FailedBreakout carries its reference on the wrapped breakout and
            # a Retest on its source breakout. Event kinds without a reference
            # (equal-level clusters, chart patterns) are skipped explicitly.
            if isinstance(event, FailedBreakout):
                reference = event.breakout.reference
            elif isinstance(event, Retest):
                reference = event.breakout.reference
            elif isinstance(event, (Breakout, Sweep)):
                reference = event.reference
            else:
                continue
            if reference.id == reference_id:
                return {
                    "type": reference.type,
                    "band_low": format(reference.band_low, "f"),
                    "band_high": format(reference.band_high, "f"),
                }
        return None

    # ------------------------------------------------------------------
    # Market state + scenario: deterministic projections, never new analysis
    # ------------------------------------------------------------------

    def _market_state(
        self,
        *,
        frame: QualificationFrame | None,
        frames: tuple[QualificationFrame, ...],
        snapshot: QualificationSnapshot | None,
        timeframe: str,
    ) -> dict[str, object]:
        """Current market facts the stored data genuinely supports.

        Every field projects Step 3/4 objects at ``as_of`` (plus the previous
        frame for transitions). Nothing is scored, smoothed, or inferred:
        unavailable inputs stay visibly unavailable.
        """

        if frame is None or snapshot is None:
            return {
                "available": False,
                "reason": "no Step 3/4 frame could be built at this boundary",
            }
        structure = frame.patterns.structure
        previous = structure_for_previous_frame(frames)
        trend = structure.trend
        volatility = structure.volatility
        volume = structure.volume
        detected_range = structure.detected_range
        close = volatility.latest_close
        return {
            "available": True,
            "as_of": to_jsonable(frame.patterns.as_of),
            "trend": {
                "direction": trend.direction.value,
                "reason": trend.reason.value,
                "sufficient": trend.sufficient,
                "swing_highs": len(trend.swing_highs),
                "swing_lows": len(trend.swing_lows),
                "higher_highs": trend.higher_highs,
                "higher_lows": trend.higher_lows,
                "lower_highs": trend.lower_highs,
                "lower_lows": trend.lower_lows,
                "transition": trend_transition(
                    None if previous is None else previous.trend, trend
                ),
                "momentum": trend_momentum(
                    None if previous is None else previous.trend, trend
                ),
            },
            "volatility": {
                "available": volatility.available,
                "reason": volatility.reason,
                "period": volatility.period,
                "atr": to_jsonable(volatility.atr),
                "atr_percent_of_price": to_jsonable(
                    volatility.atr_percent_of_price
                ),
                "direction": metric_direction(
                    None
                    if previous is None
                    else previous.volatility.atr_percent_of_price,
                    volatility.atr_percent_of_price,
                    up="expanding",
                    down="contracting",
                ),
            },
            "volume": {
                "sufficient": volume.sufficient,
                "reason": volume.reason,
                "period": volume.period,
                "current": to_jsonable(volume.current_volume),
                "rolling_average": to_jsonable(volume.rolling_average_volume),
                "relative_volume": to_jsonable(volume.relative_volume),
                "direction": metric_direction(
                    None
                    if previous is None
                    else previous.volume.relative_volume,
                    volume.relative_volume,
                    up="strengthening",
                    down="weakening",
                ),
            },
            "range": {
                "active": detected_range is not None
                and detected_range.active,
                "detected": to_jsonable(detected_range),
                "transition": range_transition(
                    None
                    if previous is None
                    else previous.detected_range,
                    detected_range,
                ),
            },
            "levels": nearest_levels(
                zones=structure.levels.zones,
                clusters=frame.patterns.equal_levels,
                close=close,
            ),
            "events": event_summary(frame),
            "breakout_state": breakout_state(frame),
            "higher_timeframes": htf_summary(frame),
            "last_close": to_jsonable(close),
        }

    def _scenario(
        self,
        *,
        snapshot: QualificationSnapshot | None,
        selected: SetupResult | None,
        plan: TradePlanResult | None,
        market_state: dict[str, object],
        timeframe: str,
        as_of: datetime,
    ) -> dict[str, object]:
        """Answer the six scenario questions from backend facts only.

        Waiting-for and invalidate content is projected from the live setups'
        own rule outcomes (pending required rules, invalidation/lifecycle
        evidence, bars remaining before expiry) and, for the selected setup,
        the exact Step 6 levels. Nothing is predicted or guessed.
        """

        if snapshot is None or not market_state.get("available"):
            return {
                "available": False,
                "reason": "no evaluated snapshot at this boundary",
            }
        parameters = QualificationParameters()
        interval = interval_for_timeframe(timeframe)
        live = [
            s
            for s in snapshot.setups
            if s.state in (SetupState.WATCH, SetupState.QUALIFIED)
        ]
        live_payload = [
            live_setup_payload(
                setup, parameters=parameters, as_of=as_of, interval=interval
            )
            for setup in live
        ]
        return {
            "available": True,
            "doing_now": describe_doing_now(market_state, snapshot),
            "bot_seeing": {
                "state": snapshot.state.value,
                "status": snapshot.status,
                "live_count": len(live),
                "live_setups": live_payload,
            },
            "strengthen_bullish": strengthen_case(
                live_payload, direction="bullish"
            ),
            "strengthen_bearish": strengthen_case(
                live_payload, direction="bearish"
            ),
            "waiting_for": waiting_for(live_payload),
            "invalidate": invalidate_cases(
                live_payload, selected=selected, plan=plan
            ),
        }


#: What must still happen for a fresh setup of each family to even exist.
#: These restate the family confirmation rules from setup_qualification;
#: they are documentation of code constants, not predictions.
_FAMILY_CONFIRMATION_TEXT = {
    SetupFamily.BREAKOUT_RETEST.value: (
        "a fresh breakout of a structural band, then a retest that holds it"
    ),
    SetupFamily.LIQUIDITY_REVERSAL.value: (
        "a failed breakout or liquidity sweep, then a directional breakout "
        "at a different reference"
    ),
    SetupFamily.RANGE_REVERSAL.value: (
        "a range-boundary seed, then a later close inside the frozen range "
        "and strictly farther inward"
    ),
}


def _max_bars_for(family: SetupFamily, parameters: QualificationParameters) -> int:
    if family is SetupFamily.BREAKOUT_RETEST:
        return parameters.continuation_max_bars
    if family is SetupFamily.LIQUIDITY_REVERSAL:
        return parameters.reversal_max_bars
    return parameters.range_max_bars


def structure_for_previous_frame(frames):
    """The Step 3 analysis of the frame before the current one, if any."""

    if len(frames) < 2:
        return None
    return frames[-2].patterns.structure


def trend_transition(previous, current) -> str:
    """Mechanical trend change between two consecutive frames."""

    if previous is None or current is None:
        return "unknown"
    if not previous.sufficient or not current.sufficient:
        return "unknown"
    if previous.direction == current.direction:
        return "unchanged"
    if current.direction is TrendDirection.NEUTRAL:
        return "to_neutral"
    if previous.direction is TrendDirection.NEUTRAL:
        return "from_neutral"
    return "reversed"


def trend_momentum(previous, current) -> str:
    """Strengthening/weakening within one trend direction, else unknown.

    Compares the directional swing comparisons (higher highs/lows for a bull
    trend, lower highs/lows for a bear trend) between two consecutive frames:
    a comparison flipping false→true is strengthening, true→false is
    weakening. Anything else (direction change, insufficient structure,
    unknown comparisons) is honestly unknown rather than inferred.
    """

    if previous is None or current is None:
        return "unknown"
    if not previous.sufficient or not current.sufficient:
        return "unknown"
    if previous.direction != current.direction:
        return "unknown"
    if current.direction is TrendDirection.BULLISH:
        keys = ("higher_highs", "higher_lows")
    elif current.direction is TrendDirection.BEARISH:
        keys = ("lower_highs", "lower_lows")
    else:
        return "unknown"
    before = [getattr(previous, key) for key in keys]
    after = [getattr(current, key) for key in keys]
    if any(value is None for value in (*before, *after)):
        return "unknown"
    strengthened = any(not b and a for b, a in zip(before, after))
    weakened = any(b and not a for b, a in zip(before, after))
    if strengthened and not weakened:
        return "strengthening"
    if weakened and not strengthened:
        return "weakening"
    if not strengthened and not weakened:
        return "steady"
    return "mixed"


def metric_direction(previous, current, *, up: str, down: str) -> dict[str, object]:
    """Direction of change between two optional Decimal readings."""

    if previous is None or current is None:
        return {"label": "unknown", "previous": to_jsonable(previous)}
    if current > previous:
        return {"label": up, "previous": to_jsonable(previous)}
    if current < previous:
        return {"label": down, "previous": to_jsonable(previous)}
    return {"label": "unchanged", "previous": to_jsonable(previous)}


def range_transition(previous, current) -> str:
    """Mechanical range-state change between two consecutive frames."""

    if previous is None and current is None:
        return "absent"
    was_active = previous is not None and previous.active
    is_active = current is not None and current.active
    if not was_active and is_active:
        return "formed"
    if was_active and not is_active:
        return "broken"
    if was_active and is_active:
        if (
            previous.range_low == current.range_low
            and previous.range_high == current.range_high
        ):
            return "held"
        return "redefined"
    return "absent"


def nearest_levels(*, zones, clusters, close) -> dict[str, object]:
    """Zone/cluster counts plus the bands nearest the latest close."""

    support = None
    resistance = None
    if close is not None:
        below = [z for z in zones if z.band_high <= close]
        above = [z for z in zones if z.band_low >= close]
        if below:
            zone = max(below, key=lambda z: z.band_high)
            support = {
                "band_low": format(zone.band_low, "f"),
                "band_high": format(zone.band_high, "f"),
                "center": format(zone.center, "f"),
                "touch_count": zone.touch_count,
            }
        if above:
            zone = min(above, key=lambda z: z.band_low)
            resistance = {
                "band_low": format(zone.band_low, "f"),
                "band_high": format(zone.band_high, "f"),
                "center": format(zone.center, "f"),
                "touch_count": zone.touch_count,
            }
    level_below = None
    level_above = None
    if close is not None:
        below = [c for c in clusters if c.center <= close]
        above = [c for c in clusters if c.center >= close]
        if below:
            cluster = max(below, key=lambda c: c.center)
            level_below = {
                "level": format(cluster.center, "f"),
                "type": cluster.type,
                "member_count": cluster.member_count,
            }
        if above:
            cluster = min(above, key=lambda c: c.center)
            level_above = {
                "level": format(cluster.center, "f"),
                "type": cluster.type,
                "member_count": cluster.member_count,
            }
    return {
        "zone_count": len(zones),
        "nearest_support": support,
        "nearest_resistance": resistance,
        "equal_level_count": len(clusters),
        "nearest_level_below": level_below,
        "nearest_level_above": level_above,
    }


def _latest(events, key):
    if not events:
        return None
    return max(events, key=key)


def event_summary(frame) -> dict[str, object]:
    """Catalog counts plus the latest event of each Step 4 kind."""

    patterns = frame.patterns
    latest_breakout = _latest(patterns.breakouts, key=lambda e: (e.known_at, e.id))
    latest_failure = _latest(
        patterns.failed_breakouts, key=lambda e: (e.known_at, e.id)
    )
    latest_sweep = _latest(patterns.sweeps, key=lambda e: (e.known_at, e.id))
    latest_retest = _latest(patterns.retests, key=lambda e: (e.known_at, e.id))
    held = sum(1 for e in patterns.retests if e.state == "held")
    failed = sum(1 for e in patterns.retests if e.state == "failed")
    confirmed_patterns = sum(
        1 for e in patterns.chart_patterns if e.state == "confirmed"
    )
    return {
        "breakouts": {
            "count": len(patterns.breakouts),
            "latest": None
            if latest_breakout is None
            else {
                "id": latest_breakout.id,
                "direction": latest_breakout.direction,
                "known_at": to_jsonable(latest_breakout.known_at),
                "reference_type": latest_breakout.reference.type,
            },
        },
        "failed_breakouts": {
            "count": len(patterns.failed_breakouts),
            "latest": None
            if latest_failure is None
            else {
                "id": latest_failure.id,
                "known_at": to_jsonable(latest_failure.known_at),
            },
        },
        "sweeps": {
            "count": len(patterns.sweeps),
            "latest": None
            if latest_sweep is None
            else {
                "id": latest_sweep.id,
                "direction": latest_sweep.direction,
                "known_at": to_jsonable(latest_sweep.known_at),
            },
        },
        "retests": {
            "count": len(patterns.retests),
            "held_count": held,
            "failed_count": failed,
            "latest": None
            if latest_retest is None
            else {
                "id": latest_retest.id,
                "state": latest_retest.state,
                "known_at": to_jsonable(latest_retest.known_at),
            },
        },
        "chart_patterns": {
            "count": len(patterns.chart_patterns),
            "confirmed_count": confirmed_patterns,
        },
    }


def breakout_state(frame) -> dict[str, object]:
    """Fresh-at-this-close attempts, acceptances, rejections, and sweeps."""

    as_of = frame.patterns.as_of
    patterns = frame.patterns
    attempts = [
        {
            "id": e.id,
            "direction": e.direction,
            "reference_type": e.reference.type,
            "close": format(e.breakout_close, "f"),
        }
        for e in patterns.breakouts
        if e.known_at == as_of
    ]
    acceptances = [
        {
            "id": e.id,
            "breakout_id": e.breakout.id,
            "direction": e.breakout.direction,
        }
        for e in patterns.retests
        if e.known_at == as_of and e.state == "held"
    ]
    rejections = [
        {"id": e.id, "breakout_id": e.breakout.id, "kind": "failed_breakout"}
        for e in patterns.failed_breakouts
        if e.known_at == as_of
    ] + [
        {"id": e.id, "breakout_id": e.breakout.id, "kind": "failed_retest"}
        for e in patterns.retests
        if e.known_at == as_of and e.state == "failed"
    ]
    sweeps = [
        {
            "id": e.id,
            "direction": e.direction,
            "reclaim_close": format(e.reclaim_close, "f"),
        }
        for e in patterns.sweeps
        if e.known_at == as_of
    ]
    return {
        "attempts": attempts,
        "acceptances": acceptances,
        "rejections": rejections,
        "sweeps": sweeps,
    }


def htf_summary(frame) -> dict[str, object]:
    """Higher-timeframe contexts exactly as the frame carries them."""

    contexts = frame.higher_timeframes
    if not contexts:
        return {
            "requested": [],
            "note": (
                "no higher timeframes requested; no alignment inferred "
                "and none required"
            ),
        }
    return {
        "requested": [h.timeframe for h in contexts],
        "contexts": {
            h.timeframe: {
                "available": bool(h.available),
                "trend": h.trend.direction.value
                if h.available and h.trend is not None
                else "UNKNOWN",
                "reason": h.reason,
            }
            for h in contexts
        },
    }


def live_setup_payload(setup, *, parameters, as_of, interval) -> dict[str, object]:
    """One live setup's rules, age, and expiry projected for the scenario."""

    max_bars = _max_bars_for(setup.family, parameters)
    age_bars = int((as_of - setup.created_at) // interval)
    pending_required = []
    invalidation_evidence = []
    vetoed_by = []
    for rule in setup.rules:
        entry = {"rule": rule.rule_id, "reason": rule.reason}
        if rule.outcome is RuleOutcome.PENDING and rule.required:
            pending_required.append(entry)
        categories = {
            evidence.category
            for evidence in rule.evidence
            if evidence.category in ("invalidation", "lifecycle")
        }
        if categories:
            invalidation_evidence.append(
                {
                    "rule": rule.rule_id,
                    "outcome": rule.outcome.value,
                    "reason": rule.reason,
                }
            )
        if rule.veto and rule.outcome is RuleOutcome.FAIL:
            vetoed_by.append(rule.rule_id)
    return {
        "setup_id": setup.id,
        "family": setup.family.value,
        "direction": setup.direction,
        "state": setup.state.value,
        "created_at": to_jsonable(setup.created_at),
        "age_bars": age_bars,
        "max_bars": max_bars,
        "bars_remaining": max(max_bars - age_bars, 0),
        "vetoed": bool(vetoed_by),
        "vetoed_by": vetoed_by,
        "passed_rules": list(setup.passed_rules),
        "failed_rules": list(setup.failed_rules),
        "pending_required": pending_required,
        "invalidation_evidence": invalidation_evidence,
    }


def describe_doing_now(market_state, snapshot) -> str:
    """One deterministic paragraph: trend, volatility, volume, range, events."""

    trend = market_state["trend"]
    volatility = market_state["volatility"]
    volume = market_state["volume"]
    range_state = market_state["range"]
    breakout = market_state["breakout_state"]
    parts = []
    if trend["sufficient"]:
        parts.append(
            f"Trend is {trend['direction'].upper()} ({trend['reason']})."
        )
    else:
        parts.append(
            f"Trend is UNKNOWN ({trend['reason']}); structure is insufficient "
            "to classify direction."
        )
    if volatility["available"]:
        direction = volatility["direction"]["label"]
        if direction == "unknown":
            parts.append(
                f"Volatility is at ATR {volatility['atr_percent_of_price']}% "
                "of price (trend unknown: previous close insufficient)."
            )
        else:
            parts.append(
                f"Volatility {direction} (ATR "
                f"{volatility['atr_percent_of_price']}% of price)."
            )
    else:
        parts.append(f"Volatility is UNKNOWN ({volatility['reason']}).")
    if volume["sufficient"]:
        direction = volume["direction"]["label"]
        if direction == "unknown":
            parts.append(
                f"Volume is {volume['relative_volume']}x its average "
                "(trend unknown: previous close insufficient)."
            )
        else:
            parts.append(
                f"Volume is {direction} at "
                f"{volume['relative_volume']}x its average."
            )
    else:
        parts.append(f"Volume is UNKNOWN ({volume['reason']}).")
    detected = range_state["detected"]
    if range_state["active"] and detected is not None:
        parts.append(
            f"Price is inside an active range "
            f"{detected['range_low']}–{detected['range_high']}."
        )
    else:
        parts.append("No active range.")
    fresh = []
    if breakout["attempts"]:
        fresh.append(f"{len(breakout['attempts'])} breakout attempt(s)")
    if breakout["acceptances"]:
        fresh.append(f"{len(breakout['acceptances'])} acceptance(s)")
    if breakout["rejections"]:
        fresh.append(f"{len(breakout['rejections'])} rejection(s)")
    if breakout["sweeps"]:
        fresh.append(f"{len(breakout['sweeps'])} sweep(s)")
    if fresh:
        parts.append("Fresh at this close: " + ", ".join(fresh) + ".")
    parts.append(f"Qualification state: {snapshot.state.value}.")
    return " ".join(parts)


def strengthen_case(live_payload, *, direction: str) -> dict[str, object]:
    """What would strengthen one side: pending required rules of live setups."""

    setups = [
        {
            "setup_id": item["setup_id"],
            "state": item["state"],
            "pending_required": item["pending_required"],
        }
        for item in live_payload
        if item["direction"] == direction
    ]
    return {
        "direction": direction,
        "developing_setups": setups,
        "none_developing": not setups,
        "to_start_a_setup": dict(_FAMILY_CONFIRMATION_TEXT),
    }


def waiting_for(live_payload) -> dict[str, object]:
    """Merged pending required evidence across every live setup."""

    merged: dict[str, dict[str, object]] = {}
    for item in live_payload:
        for pending in item["pending_required"]:
            entry = merged.setdefault(
                pending["rule"], {"reason": pending["reason"], "setup_ids": []}
            )
            entry["setup_ids"].append(item["setup_id"])  # type: ignore[attr-defined]
    return {
        "pending": [
            {"rule": rule, **entry} for rule, entry in sorted(merged.items())
        ],
        "note": None
        if merged
        else (
            "No live setups: waiting for a fresh seed event (breakout, "
            "failed breakout, or sweep)."
        ),
    }


def invalidate_cases(live_payload, *, selected, plan) -> dict[str, object]:
    """Per-setup invalidation: expiry, terminal evidence, exact plan levels."""

    cases = []
    for item in live_payload:
        case: dict[str, object] = {
            "setup_id": item["setup_id"],
            "state": item["state"],
            "bars_remaining": item["bars_remaining"],
            "max_bars": item["max_bars"],
            "invalidation_evidence": item["invalidation_evidence"],
            "vetoed": item["vetoed"],
            "vetoed_by": item["vetoed_by"],
        }
        if (
            selected is not None
            and plan is not None
            and plan.state is PlanState.PLANNABLE
            and item["setup_id"] == selected.id
        ):
            case["plan_invalidation"] = to_jsonable(plan.invalidation)
            case["plan_stop"] = to_jsonable(plan.stop)
            case["plan_entry"] = to_jsonable(plan.entry)
        cases.append(case)
    return {"cases": cases}
