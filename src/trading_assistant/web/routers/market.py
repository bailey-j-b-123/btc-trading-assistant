"""Chart-data endpoints: stored candles and Step 3 structure overlays.

All market data served here comes from the same stored closed-candle tables the
engine uses; the browser never fetches market data independently.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, Request

from trading_assistant.web.dashboard_service import (
    DASHBOARD_CANDLE_LIMIT,
    MAX_CANDLE_LIMIT,
    DashboardService,
)
from trading_assistant.web.schemas import parse_utc_iso

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/candles")
def get_candles(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=8),
    limit: int = Query(default=DASHBOARD_CANDLE_LIMIT, ge=1, le=MAX_CANDLE_LIMIT),
    end_time: str | None = Query(default=None, min_length=20, max_length=40),
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    parsed_end: datetime | None = None
    if end_time is not None:
        parsed_end = parse_utc_iso(end_time, field_name="end_time")
    return service.candles(
        symbol=symbol, timeframe=timeframe, limit=limit, end_time=parsed_end
    )


@router.get("/structure")
def get_structure(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=8),
    as_of: str | None = Query(default=None, min_length=20, max_length=40),
) -> dict[str, object]:
    state = request.app.state.services
    service = DashboardService(state)
    parsed_as_of: datetime | None = None
    if as_of is not None:
        parsed_as_of = parse_utc_iso(as_of, field_name="as_of")
    return service.structure(symbol=symbol, timeframe=timeframe, as_of=parsed_as_of)


@router.get("/annotations")
def get_annotations(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=64),
    timeframe: str | None = Query(default=None, min_length=1, max_length=8),
    as_of: str | None = Query(default=None, min_length=20, max_length=40),
) -> dict[str, object]:
    """Read-only chart evidence (swings, patterns, breakouts, candle shapes)."""
    state = request.app.state.services
    service = DashboardService(state)
    parsed_as_of: datetime | None = None
    if as_of is not None:
        parsed_as_of = parse_utc_iso(as_of, field_name="as_of")
    return service.chart_evidence(symbol=symbol, timeframe=timeframe, as_of=parsed_as_of)


@router.get("/live-price")
def get_live_price() -> dict[str, object]:
    """Public display-only quote; never reads or writes stored candle tables."""
    from trading_assistant.web.live_price import live_price

    return live_price()
