"""Dashboard payload and the decision-recording endpoint.

``POST /api/dashboard/decisions`` is the only state-changing route reachable
from the dashboard: it journals the exact Step 5/Step 6 proposal at the
submitted ``as_of`` and appends one Step 7 decision row. It never touches an
exchange and never executes anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.schemas import DashboardDecisionRequest, parse_utc_iso

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=8),
    as_of: str | None = Query(default=None, min_length=20, max_length=40),
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    parsed_as_of = None
    if as_of is not None:
        parsed_as_of = parse_utc_iso(as_of, field_name="as_of")
    return service.dashboard(symbol=symbol, timeframe=timeframe, as_of=parsed_as_of)


@router.post("/decisions")
def post_decision(
    request: Request, body: DashboardDecisionRequest
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    return service.decide_current(
        symbol=body.symbol,
        timeframe=body.timeframe,
        as_of=parse_utc_iso(body.as_of, field_name="as_of"),
        setup_id=body.setup_id,
        decision=body.decision.value,
        reason=body.reason,
    )
