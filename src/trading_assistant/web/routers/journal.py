"""Journal endpoints: immutable history, append-only decisions, observations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.journal_query import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    JournalListQuery,
    JournalQueryService,
)
from trading_assistant.web.schemas import (
    JournalDecisionRequest,
    ObservationRequest,
    parse_utc_iso,
)

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _query_service(request: Request) -> JournalQueryService:
    state = request.app.state.services
    return JournalQueryService(state.engine, state.journal.repository)


@router.get("/records")
def list_records(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    exchange: str | None = Query(default=None, min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=8),
    setup_family: str | None = Query(default=None, min_length=1, max_length=64),
    direction: str | None = Query(default=None, min_length=1, max_length=16),
    setup_state: str | None = Query(default=None, min_length=1, max_length=16),
    plan_state: str | None = Query(default=None, min_length=1, max_length=16),
    record_kind: str | None = Query(default=None, min_length=1, max_length=16),
    setup_id: str | None = Query(default=None, min_length=1, max_length=128),
    decision_state: str | None = Query(default=None, min_length=1, max_length=16),
    outcome_status: str | None = Query(default=None, min_length=1, max_length=32),
    range_from: str | None = Query(default=None, min_length=20, max_length=40),
    range_to: str | None = Query(default=None, min_length=20, max_length=40),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    query = JournalListQuery(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        setup_family=setup_family,
        direction=direction,
        setup_state=setup_state,
        plan_state=plan_state,
        record_kind=record_kind,
        setup_id=setup_id,
        decision_state=decision_state,
        outcome_status=outcome_status,
        range_from=None
        if range_from is None
        else parse_utc_iso(range_from, field_name="range_from"),
        range_to=None
        if range_to is None
        else parse_utc_iso(range_to, field_name="range_to"),
        limit=limit,
        offset=offset,
    )
    return _query_service(request).list_records(query)


@router.get("/records/{journal_id}")
def record_detail(request: Request, journal_id: str) -> dict[str, object]:
    detail = _query_service(request).record_detail(journal_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"no journal record {journal_id}"},
        )
    return detail


@router.post("/records/{journal_id}/decisions")
def post_record_decision(
    request: Request, journal_id: str, body: JournalDecisionRequest
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    return service.decide_record(
        journal_id=journal_id, decision=body.decision.value, reason=body.reason
    )


@router.post("/records/{journal_id}/observations")
def post_record_observation(
    request: Request, journal_id: str, body: ObservationRequest
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    observed_through = None
    if body.observed_through is not None:
        observed_through = parse_utc_iso(
            body.observed_through, field_name="observed_through"
        )
    return service.observe_record(
        journal_id=journal_id, observed_through=observed_through
    )
