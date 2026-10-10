"""Dedicated, single-instance Binance Spot market-data ingestion.

The ingestion process is the ONLY writer of public candles for the decision
runners. It refreshes every timeframe the multi-timeframe hierarchy requires,
one timeframe at a time, and reports per-timeframe coverage. It never fabricates
candles, never touches another exchange's rows, and cannot place an order.
"""

from trading_assistant.ingestion.service import (
    IngestionReport,
    TimeframeCoverage,
    TimeframeIngestResult,
    coverage_for,
    ingest_once,
)
from trading_assistant.ingestion.lock import IngestionLockError, SingleInstanceLock

__all__ = [
    "IngestionLockError",
    "IngestionReport",
    "SingleInstanceLock",
    "TimeframeCoverage",
    "TimeframeIngestResult",
    "coverage_for",
    "ingest_once",
]
