"""Step 10 — read-only presentation layer over deterministic Steps 1–9.

This package is an application-facing service boundary: it exposes the existing
Step 2–9 outputs (stored candles, market structure, qualification, planning,
journal, statistics, grounded explanations) as validated JSON plus a static
dashboard UI. It never re-derives trading logic, never contacts an exchange,
and never executes anything: ACCEPT/REJECT/SKIP only append Step 7 journal
rows.
"""

from trading_assistant.web.app import create_app
from trading_assistant.web.freshness import FreshnessReport, evaluate_freshness
from trading_assistant.web.state import AppState

__all__ = ["AppState", "FreshnessReport", "create_app", "evaluate_freshness"]
