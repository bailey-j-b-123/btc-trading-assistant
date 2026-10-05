"""Deterministic data-freshness evaluation for current-market screens.

The UI must never claim live operation unless stored data satisfies an exact
rule. Freshness is computed only from:

* the evaluation instant ``as_of`` (explicit, or the app clock),
* the timeframe's candle-close boundary arithmetic, and
* the latest stored candle open time for that series.

Statuses:

``CURRENT``
    ``as_of`` is the newest boundary at or before the clock, and the latest
    expected closed candle is stored.
``STALE``
    ``as_of`` is current, but stored candles stop before the expected latest
    closed candle; ``staleness_intervals`` counts the missing boundaries.
``HISTORICAL``
    ``as_of`` is strictly older than the newest boundary: the screen describes
    a past instant and must never be labelled live.
``UNKNOWN``
    No stored candles exist for the series (or the stored latest candle is
    after the expected boundary, which cannot represent current data).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
)
from trading_assistant.market_structure.candles import interval_for_timeframe

FreshnessStatus = Literal["CURRENT", "STALE", "HISTORICAL", "UNKNOWN"]


@dataclass(frozen=True, slots=True)
class FreshnessReport:
    """One freshness verdict plus the exact inputs behind it."""

    status: FreshnessStatus
    reason: str
    as_of: datetime
    now: datetime
    expected_latest_closed: datetime
    latest_stored: datetime | None
    staleness_intervals: int | None
    is_current_boundary: bool


def evaluate_freshness(
    *,
    timeframe: str,
    as_of: datetime,
    now: datetime,
    latest_stored: datetime | None,
) -> FreshnessReport:
    """Evaluate freshness deterministically; never raises for missing data."""

    interval = interval_for_timeframe(timeframe)
    expected_latest_closed = latest_closed_candle_open_time(as_of, timeframe)
    current_boundary = latest_closed_candle_open_time(now, timeframe) + interval
    is_current_boundary = as_of >= current_boundary

    if latest_stored is None:
        return FreshnessReport(
            status="UNKNOWN",
            reason="no_stored_candles",
            as_of=as_of,
            now=now,
            expected_latest_closed=expected_latest_closed,
            latest_stored=None,
            staleness_intervals=None,
            is_current_boundary=is_current_boundary,
        )
    if latest_stored > expected_latest_closed:
        return FreshnessReport(
            status="UNKNOWN",
            reason="stored_candle_after_expected_boundary",
            as_of=as_of,
            now=now,
            expected_latest_closed=expected_latest_closed,
            latest_stored=latest_stored,
            staleness_intervals=None,
            is_current_boundary=is_current_boundary,
        )
    if not is_current_boundary:
        interval_ms = int(interval.total_seconds() * 1000)
        current_boundary = latest_closed_candle_open_time(now, timeframe) + interval
        behind_ms = int((current_boundary - as_of).total_seconds() * 1000)
        return FreshnessReport(
            status="HISTORICAL",
            reason="as_of_is_not_the_current_boundary",
            as_of=as_of,
            now=now,
            expected_latest_closed=expected_latest_closed,
            latest_stored=latest_stored,
            staleness_intervals=max(0, behind_ms // interval_ms),
            is_current_boundary=False,
        )
    if latest_stored == expected_latest_closed:
        return FreshnessReport(
            status="CURRENT",
            reason="latest_expected_closed_candle_is_stored",
            as_of=as_of,
            now=now,
            expected_latest_closed=expected_latest_closed,
            latest_stored=latest_stored,
            staleness_intervals=0,
            is_current_boundary=True,
        )
    interval_ms = int(interval.total_seconds() * 1000)
    delta_ms = int((expected_latest_closed - latest_stored).total_seconds() * 1000)
    return FreshnessReport(
        status="STALE",
        reason="stored_candles_stop_before_expected_boundary",
        as_of=as_of,
        now=now,
        expected_latest_closed=expected_latest_closed,
        latest_stored=latest_stored,
        staleness_intervals=delta_ms // interval_ms,
        is_current_boundary=True,
    )


def freshness_to_json(report: FreshnessReport) -> dict[str, object]:
    return {
        "status": report.status,
        "reason": report.reason,
        "is_current_boundary": report.is_current_boundary,
        "staleness_intervals": report.staleness_intervals,
    }
