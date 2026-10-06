"""Settings endpoint: configured defaults plus explicitly unavailable controls.

Only presentation-safe information is exposed. Exchange secrets, execution,
leverage, balances, sizing, and parameter tuning have no backend contract here
and are reported as unavailable by design rather than hidden.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/settings", tags=["settings"])

UNAVAILABLE_BY_DESIGN = (
    "exchange_api_credentials",
    "order_execution",
    "automatic_trading",
    "leverage",
    "account_balances",
    "position_management",
    "strategy_optimisation",
    "threshold_tuning",
    "new_setup_rules",
)

PRESENTATION_SETTINGS = (
    {
        "key": "preferred_symbol",
        "storage": "browser-local",
        "description": "Symbol the dashboard opens with; must be entered freely per session.",
        "editable": True,
    },
    {
        "key": "preferred_timeframe",
        "storage": "browser-local",
        "description": "Timeframe the dashboard opens with; restricted to configured supported timeframes.",
        "editable": True,
    },
    {
        "key": "chart_overlays",
        "storage": "browser-local",
        "description": "Which deterministic overlays (zones, range, equal levels, plan levels) are drawn.",
        "editable": True,
    },
    {
        "key": "explanation_detail",
        "storage": "browser-local",
        "description": "Collapsed or expanded rendering of the Step 9 explanation sections.",
        "editable": True,
    },
)


@router.get("")
def get_settings(request: Request) -> dict[str, object]:
    state = request.app.state.services
    settings = state.settings
    return {
        "configured_defaults": {
            "symbol": settings.symbol,
            "exchange": settings.exchange,
            "default_timeframe": settings.default_timeframe,
            "supported_timeframes": list(settings.supported_timeframes),
        },
        "presentation_settings": PRESENTATION_SETTINGS,
        "presentation_storage_note": (
            "Presentation preferences are stored in this browser only "
            "(localStorage); they never alter deterministic engine rules, "
            "thresholds, or stored data."
        ),
        "unavailable_by_design": list(UNAVAILABLE_BY_DESIGN),
        "unavailable_note": (
            "These controls do not exist in this application and are not "
            "exposed through any endpoint. Trading rules are versioned "
            "deterministic code, not runtime settings."
        ),
    }
