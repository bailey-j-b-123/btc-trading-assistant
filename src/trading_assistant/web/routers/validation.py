"""Read-only Step 11 historical validation endpoint.

The endpoint computes an isolated derived report from stored candles only.  It
never records Bailey decisions, writes outcome observations, or exposes any
execution/account functionality.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Query, Request

from trading_assistant.historical_validation import (
    ChronologicalSplit,
    FrictionAssumptions,
    ValidationConfig,
)
from trading_assistant.web.schemas import parse_utc_iso

router = APIRouter(prefix="/api/validation", tags=["historical-validation"])
ZERO_BPS = Decimal(0)


def _time(value: str | None, *, name: str) -> datetime | None:
    return parse_utc_iso(value, field_name=name) if value is not None else None


@router.get("")
def get_validation(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=128),
    timeframe: str | None = Query(default=None, min_length=1, max_length=32),
    development_start: str | None = Query(default=None, min_length=20, max_length=40),
    development_end: str | None = Query(default=None, min_length=20, max_length=40),
    out_of_sample_start: str | None = Query(default=None, min_length=20, max_length=40),
    out_of_sample_end: str | None = Query(default=None, min_length=20, max_length=40),
    observation_horizon_candles: int = Query(default=20, ge=1, le=100_000),
    minimum_sample_size: int = Query(default=20, ge=1, le=1_000_000),
    fee_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    entry_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    exit_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
) -> dict[str, object]:
    """Return a deterministic, non-persisted HISTORICAL VALIDATION report."""

    supplied = (
        development_start,
        development_end,
        out_of_sample_start,
        out_of_sample_end,
    )
    if any(value is not None for value in supplied) and any(
        value is None for value in supplied
    ):
        raise ValueError(
            "explicit splitting requires development_start, development_end, "
            "out_of_sample_start, and out_of_sample_end together"
        )
    split = None
    if development_start is not None:
        split = ChronologicalSplit(
            development_start=_time(development_start, name="development_start"),
            development_end=_time(development_end, name="development_end"),
            out_of_sample_start=_time(out_of_sample_start, name="out_of_sample_start"),
            out_of_sample_end=_time(out_of_sample_end, name="out_of_sample_end"),
        )
    state = request.app.state.services
    resolved_timeframe = (
        state.settings.default_timeframe if timeframe is None else timeframe
    )
    state.require_supported_timeframe(resolved_timeframe)
    report = state.validation.validate(
        exchange=state.settings.exchange,
        symbol=state.require_symbol(symbol),
        timeframe=resolved_timeframe,
        config=ValidationConfig(
            split=split,
            observation_horizon_candles=observation_horizon_candles,
            minimum_sample_size=minimum_sample_size,
            friction=FrictionAssumptions(
                fee_bps=fee_bps,
                entry_slippage_bps=entry_slippage_bps,
                exit_slippage_bps=exit_slippage_bps,
            ),
        ),
    )
    return report.to_json_dict()
