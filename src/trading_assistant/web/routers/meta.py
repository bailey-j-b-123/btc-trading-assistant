"""Application identity endpoint: what this app is, and what it cannot do."""

from __future__ import annotations

from fastapi import APIRouter, Request

from trading_assistant.journaling.parameters import (
    DECISION_RULES_VERSION,
    JOURNAL_RULES_VERSION,
)
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.setup_qualification.parameters import (
    RULES_VERSION as QUALIFICATION_RULES_VERSION,
)
from trading_assistant.statistics.config import STATISTICS_RULES_VERSION
from trading_assistant.trade_planning.parameters import PLANNING_RULES_VERSION

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("")
def get_meta(request: Request) -> dict[str, object]:
    state = request.app.state.services
    settings = state.settings
    return {
        "application": {
            "name": "BTC Trading Assistant",
            "step": 10,
            "description": (
                "Evidence-driven analysis dashboard. Software calculates, rules "
                "qualify, statistics validate, AI explains, Bailey decides, and "
                "everything gets recorded."
            ),
            "execution_disabled": True,
            "execution_note": (
                "This application never places, modifies, or cancels exchange "
                "orders. ACCEPT records a journal decision only."
            ),
            "authentication_required_before_public_deployment": True,
        },
        "exchange": settings.exchange,
        "default_symbol": settings.symbol,
        "default_timeframe": settings.default_timeframe,
        "supported_timeframes": list(settings.supported_timeframes),
        "server_time_utc": to_jsonable(state.now()),
        "rules_versions": {
            "setup_qualification": QUALIFICATION_RULES_VERSION,
            "trade_planning": PLANNING_RULES_VERSION,
            "journal": JOURNAL_RULES_VERSION,
            "decisions": DECISION_RULES_VERSION,
            "statistics": STATISTICS_RULES_VERSION,
        },
    }
