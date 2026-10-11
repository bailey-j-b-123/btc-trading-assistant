"""Read-only forensic report for BRAIN's recorded paper trades.

Run with ``python -m trading_assistant.trade_forensics``. The package reads the
SQLite database through a write-denying snapshot and never changes it.
"""

from trading_assistant.trade_forensics.analysis import (
    LedgerNotMigrated,
    PaperPlanAmbiguous,
    PaperPlanNotFound,
    PlanQuery,
    build_report,
    locate_paper_plan,
)
from trading_assistant.trade_forensics.models import Fact, Section, Table, TradeReport
from trading_assistant.trade_forensics.render import render_markdown
from trading_assistant.trade_forensics.snapshot import (
    DatabaseUnavailable,
    ReadOnlySnapshot,
)

__all__ = [
    "DatabaseUnavailable",
    "Fact",
    "LedgerNotMigrated",
    "PaperPlanAmbiguous",
    "PaperPlanNotFound",
    "PlanQuery",
    "ReadOnlySnapshot",
    "Section",
    "Table",
    "TradeReport",
    "build_report",
    "locate_paper_plan",
    "render_markdown",
]
