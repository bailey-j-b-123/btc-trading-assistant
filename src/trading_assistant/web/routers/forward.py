"""Read-only Step 12 forward-testing endpoints.

The dashboard shows what the forward runner already recorded: immutable forward
observations, frozen paper plans, outcome versions, data-health verdicts, the
runner heartbeat, and derived forward statistics. Nothing here fetches candles,
runs a forward pass, writes to the ledger, or exposes any execution/account
surface. Starting the runner is an operator action on the command line.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Request

from trading_assistant.forward_testing import (
    FORWARD_REPORT_LIMITATIONS,
    FrictionAssumptions,
    ForwardParameters,
    build_forward_comparison,
    build_forward_report,
)
from trading_assistant.historical_validation import (
    FrictionAssumptions as ValidationFrictionAssumptions,
)
from trading_assistant.historical_validation import ValidationConfig

router = APIRouter(prefix="/api/forward", tags=["forward-testing"])
ZERO_BPS = Decimal(0)


def _friction(
    *, fee_bps: Decimal, entry_slippage_bps: Decimal, exit_slippage_bps: Decimal
) -> FrictionAssumptions:
    return FrictionAssumptions(
        fee_bps=fee_bps,
        entry_slippage_bps=entry_slippage_bps,
        exit_slippage_bps=exit_slippage_bps,
    )


def _recorded_friction(
    service, *, symbol: str, timeframe: str
) -> tuple[FrictionAssumptions, bool]:
    """The friction assumptions the stored paper plans were recorded under.

    The runner may have been started with explicit slippage/fee flags. Reporting
    under different assumptions than the ledger was recorded with would silently
    mix two cost models, so the recorded assumptions are reused whenever every
    stored plan agrees on them; otherwise the configured defaults are used and
    the report carries an explicit warning about the mixed fingerprints.

    Returns ``(friction, from_recorded_plans)``.
    """

    plans = service.ledger.paper_plans(
        exchange=service.settings.exchange, symbol=symbol, timeframe=timeframe
    )
    fingerprints = {plan.friction_fingerprint for plan in plans}
    if plans and len(fingerprints) == 1:
        return plans[0].friction, True
    return service.parameters.friction, False


def _resolve(request: Request, symbol, timeframe):
    state = request.app.state.services
    resolved_timeframe = (
        state.settings.default_timeframe if timeframe is None else timeframe
    )
    state.require_supported_timeframe(resolved_timeframe)
    resolved_symbol = state.require_symbol(symbol)
    return state, resolved_symbol, resolved_timeframe


def _parameters(
    *,
    observation_horizon_candles: int,
    minimum_sample_size: int,
    fee_bps: Decimal,
    entry_slippage_bps: Decimal,
    exit_slippage_bps: Decimal,
) -> tuple[ForwardParameters, FrictionAssumptions]:
    friction = _friction(
        fee_bps=fee_bps,
        entry_slippage_bps=entry_slippage_bps,
        exit_slippage_bps=exit_slippage_bps,
    )
    return (
        ForwardParameters(
            observation_horizon_candles=observation_horizon_candles,
            minimum_sample_size=minimum_sample_size,
            friction=friction,
        ),
        friction,
    )


@router.get("")
def get_forward(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=128),
    timeframe: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=25, ge=1, le=500),
    observation_horizon_candles: int = Query(default=20, ge=1, le=100_000),
    minimum_sample_size: int = Query(default=20, ge=1, le=1_000_000),
    fee_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    entry_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    exit_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
) -> dict[str, object]:
    """Live forward state, latest paper observations, and forward statistics."""

    state, resolved_symbol, resolved_timeframe = _resolve(request, symbol, timeframe)
    parameters, friction = _parameters(
        observation_horizon_candles=observation_horizon_candles,
        minimum_sample_size=minimum_sample_size,
        fee_bps=fee_bps,
        entry_slippage_bps=entry_slippage_bps,
        exit_slippage_bps=exit_slippage_bps,
    )
    service = state.forward
    status = service.status(symbol=resolved_symbol, timeframe=resolved_timeframe)
    observations = service.observations_payload(
        symbol=resolved_symbol, timeframe=resolved_timeframe, limit=limit
    )
    ledger = service.ledger_snapshot(
        exchange=state.settings.exchange,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
    )
    recorded_friction, friction_is_recorded = _recorded_friction(
        service, symbol=resolved_symbol, timeframe=resolved_timeframe
    )
    report = build_forward_report(
        ledger=ledger,
        exchange=state.settings.exchange,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
        parameters=parameters,
        friction=recorded_friction,
    )
    return {
        "step": 12,
        "label": "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE",
        "paper_label": "PAPER OBSERVATION — NO REAL ORDER",
        "market_data_label": "LIVE MARKET DATA",
        "disclaimer": (
            "Paper trading and historical performance do not establish future "
            "profitability."
        ),
        "execution_disabled": True,
        "exchange": state.settings.exchange,
        "symbol": resolved_symbol,
        "timeframe": resolved_timeframe,
        "friction_from_recorded_plans": friction_is_recorded,
        "friction_fingerprint": recorded_friction.fingerprint(),
        "status": status,
        "observations": observations,
        "report": report.to_json_dict(),
        "limitations": list(FORWARD_REPORT_LIMITATIONS),
    }


@router.get("/comparison")
def get_forward_comparison(
    request: Request,
    symbol: str | None = Query(default=None, min_length=1, max_length=128),
    timeframe: str | None = Query(default=None, min_length=1, max_length=32),
    observation_horizon_candles: int = Query(default=20, ge=1, le=100_000),
    minimum_sample_size: int = Query(default=20, ge=1, le=1_000_000),
    fee_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    entry_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
    exit_slippage_bps: Decimal = Query(default=ZERO_BPS, ge=0, lt=10_000),  # noqa: B008
) -> dict[str, object]:
    """HISTORICAL VALIDATION next to LIVE FORWARD PAPER OBSERVATIONS.

    The historical side is the existing read-only Step 11 replay over stored
    candles with the same horizon and friction assumptions. The two sides are
    never merged into one number: every row carries both denominators.
    """

    state, resolved_symbol, resolved_timeframe = _resolve(request, symbol, timeframe)
    parameters, friction = _parameters(
        observation_horizon_candles=observation_horizon_candles,
        minimum_sample_size=minimum_sample_size,
        fee_bps=fee_bps,
        entry_slippage_bps=entry_slippage_bps,
        exit_slippage_bps=exit_slippage_bps,
    )
    service = state.forward
    ledger = service.ledger_snapshot(
        exchange=state.settings.exchange,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
    )
    recorded_friction, _ = _recorded_friction(
        service, symbol=resolved_symbol, timeframe=resolved_timeframe
    )
    forward = build_forward_report(
        ledger=ledger,
        exchange=state.settings.exchange,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
        parameters=parameters,
        friction=recorded_friction,
    )
    historical = state.validation.validate(
        exchange=state.settings.exchange,
        symbol=resolved_symbol,
        timeframe=resolved_timeframe,
        config=ValidationConfig(
            observation_horizon_candles=observation_horizon_candles,
            minimum_sample_size=minimum_sample_size,
            friction=ValidationFrictionAssumptions(
                fee_bps=fee_bps,
                entry_slippage_bps=entry_slippage_bps,
                exit_slippage_bps=exit_slippage_bps,
            ),
        ),
    )
    comparison = build_forward_comparison(
        forward=forward,
        historical=historical,
        generated_at=state.now(),
    )
    return {
        "step": 12,
        "label": "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE",
        "disclaimer": (
            "Paper trading and historical performance do not establish future "
            "profitability."
        ),
        "execution_disabled": True,
        "comparison": comparison.to_json_dict(),
    }
