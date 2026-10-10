"""One ingestion pass over every required timeframe, with per-timeframe isolation.

Each timeframe is refreshed independently (bounded retries, closed candles only,
idempotent storage, required-window repair) by the same code the hierarchy
runner uses. A failure on one timeframe is recorded and the remaining
timeframes still run. SQLite lock errors are never swallowed: they stop the pass,
exactly as the runners do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_assistant.database.engine import is_sqlite_lock_error
from trading_assistant.market_data.errors import is_transient_network_error
from trading_assistant.market_data.integrity import assess_required_window
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.multi_timeframe.service import MultiTimeframeService

#: Coverage states, most severe first. Only COMPLETE lets the gate proceed.
COMPLETE = "COMPLETE"
STALE = "STALE"
MISSING_CANDLES = "MISSING_CANDLES"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NO_DATA = "NO_DATA"


@dataclass(frozen=True, slots=True)
class TimeframeIngestResult:
    timeframe: str
    status: str  # "OK" or "ERROR"
    error_type: str | None = None
    error: str | None = None
    transient: bool = False
    update: dict[str, Any] = field(default_factory=dict)
    backfill: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TimeframeCoverage:
    timeframe: str
    state: str
    required_depth: int
    stored_in_window: int
    missing_count: int
    latest_stored_open: datetime | None
    expected_latest_closed_open: datetime
    first_stored_open: datetime | None
    window_start: datetime

    @property
    def complete(self) -> bool:
        return self.state == COMPLETE

    def describe(self) -> str:
        """One line a human can act on."""

        if self.state == COMPLETE:
            return f"{self.timeframe}: complete ({self.stored_in_window}/{self.required_depth} closes)"
        if self.state == NO_DATA:
            return f"{self.timeframe}: no stored Binance candles; run the ingestion refresh"
        if self.state == STALE:
            latest = self.latest_stored_open.isoformat() if self.latest_stored_open else "none"
            return (
                f"{self.timeframe}: stale; newest stored close {latest}, expected "
                f"{self.expected_latest_closed_open.isoformat()} (refresh pending or feed behind)"
            )
        if self.state == INSUFFICIENT_HISTORY:
            first = self.first_stored_open.isoformat() if self.first_stored_open else "none"
            return (
                f"{self.timeframe}: insufficient history; {self.stored_in_window}/"
                f"{self.required_depth} closes in window, earliest stored {first}"
            )
        return (
            f"{self.timeframe}: {self.missing_count} missing closed candle(s) inside the "
            f"required window (permanent gaps are not fabricated)"
        )


@dataclass(frozen=True, slots=True)
class IngestionReport:
    exchange: str
    symbol: str
    as_of: datetime
    results: tuple[TimeframeIngestResult, ...]
    coverage: tuple[TimeframeCoverage, ...]

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status == "ERROR")

    @property
    def all_complete(self) -> bool:
        return all(item.complete for item in self.coverage)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "error_count": self.error_count,
            "all_complete": self.all_complete,
            "timeframes": [
                {
                    "timeframe": result.timeframe,
                    "status": result.status,
                    "error_type": result.error_type,
                    "error": result.error,
                    "transient": result.transient,
                    "update": result.update,
                    "backfill": result.backfill,
                }
                for result in self.results
            ],
            "coverage": [
                {
                    "timeframe": item.timeframe,
                    "state": item.state,
                    "required_depth": item.required_depth,
                    "stored_in_window": item.stored_in_window,
                    "missing_count": item.missing_count,
                    "latest_stored_open": _iso(item.latest_stored_open),
                    "expected_latest_closed_open": item.expected_latest_closed_open.isoformat(),
                    "first_stored_open": _iso(item.first_stored_open),
                    "detail": item.describe(),
                }
                for item in self.coverage
            ],
        }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _summarize_update(update: Any) -> dict[str, Any]:
    return {
        "received_count": update.received_count,
        "accepted_count": update.accepted_count,
        "inserted_count": update.inserted_count,
        "already_present_count": update.already_present_count,
        "rejected_count": update.rejected_count,
        "missing_candle_count": update.missing_candle_count,
        "complete": update.complete,
    }


def coverage_for(
    service: MultiTimeframeService,
    *,
    symbol: str,
    timeframe: str,
    as_of: datetime,
) -> TimeframeCoverage:
    """Classify stored Binance coverage for one timeframe at ``as_of``.

    Read-only. Uses only candles stored for the configured exchange; Kraken or
    any other exchange's rows are never counted.
    """

    exchange = service.settings.exchange
    interval = interval_for_timeframe(timeframe)
    depth = service.required_depth()
    expected = latest_closed_candle_open_time(as_of, timeframe)
    window_start = expected - (depth - 1) * interval
    stored = service.candles.get_candles(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        start_time=window_start,
        end_time=expected,
    )
    assessment = assess_required_window(
        stored.candles,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        decision_time=as_of,
        depth=depth,
    )
    latest_stored = stored.candles[-1].timestamp if stored.candles else None
    if not stored.candles:
        state = NO_DATA
    elif latest_stored is None or latest_stored < expected:
        state = STALE
    elif assessment.first_stored_open is None or assessment.first_stored_open > window_start:
        state = INSUFFICIENT_HISTORY
    elif assessment.missing_count:
        state = MISSING_CANDLES
    else:
        state = COMPLETE
    return TimeframeCoverage(
        timeframe=timeframe,
        state=state,
        required_depth=depth,
        stored_in_window=len(stored.candles),
        missing_count=assessment.missing_count,
        latest_stored_open=latest_stored,
        expected_latest_closed_open=expected,
        first_stored_open=assessment.first_stored_open,
        window_start=window_start,
    )


def ingest_once(
    service: MultiTimeframeService,
    *,
    symbol: str,
    as_of: datetime,
    timeframes: tuple[str, ...] | None = None,
) -> IngestionReport:
    """Refresh every required timeframe once, isolating each timeframe's failure."""

    exchange = service.settings.exchange
    targets = timeframes if timeframes is not None else service.hierarchy.timeframes
    results: list[TimeframeIngestResult] = []
    for timeframe in targets:
        try:
            update, backfill = service.ingest_timeframe(
                symbol=symbol, timeframe=timeframe, as_of=as_of
            )
            results.append(
                TimeframeIngestResult(
                    timeframe=timeframe,
                    status="OK",
                    update=_summarize_update(update),
                    backfill=dict(backfill),
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported per timeframe
            if is_sqlite_lock_error(exc):
                raise
            results.append(
                TimeframeIngestResult(
                    timeframe=timeframe,
                    status="ERROR",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    transient=is_transient_network_error(exc),
                )
            )
    coverage = tuple(
        coverage_for(service, symbol=symbol, timeframe=timeframe, as_of=as_of)
        for timeframe in targets
    )
    return IngestionReport(
        exchange=exchange,
        symbol=symbol,
        as_of=as_of,
        results=tuple(results),
        coverage=coverage,
    )
