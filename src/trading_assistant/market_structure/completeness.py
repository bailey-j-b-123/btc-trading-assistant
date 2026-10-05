"""Data-completeness reporting for a market-structure candle window.

Market-structure calculations never fabricate candles. This module records, for
the exact candle window a calculation used, which stored candles were available
and which expected candles were missing, so an incomplete window is always
visible in the result instead of being silently smoothed over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.market_structure.candles import require_positive_interval


@dataclass(frozen=True, slots=True)
class DataCompleteness:
    """Availability report for one candle window ending at a requested ``as_of``.

    ``gaps`` and ``missing_candle_count`` are the gap report produced by the
    Step 2 market-data layer for the retrieved range; when an end bound is
    supplied (market structure always supplies one) that report also covers the
    trailing candles between the latest stored candle and the expected latest
    closed candle.
    """

    candle_count: int
    window_start: datetime | None
    window_end: datetime | None
    expected_latest_closed_open_time: datetime
    missing_candles_after_latest_stored: int | None
    gaps: tuple[CandleGap, ...]
    missing_candle_count: int
    complete: bool

    @property
    def sufficient(self) -> bool:
        """Alias for :attr:`complete` used in insufficient-data reporting."""

        return self.complete


def describe_completeness(
    candles: tuple[Candle, ...],
    *,
    interval: timedelta,
    expected_latest_closed_open_time: datetime,
    gaps: tuple[CandleGap, ...] = (),
) -> DataCompleteness:
    """Summarize the stored candles actually used by a calculation.

    ``missing_candles_after_latest_stored`` is ``None`` when the window holds no
    stored candle at all, because there is no stored candle to count forward
    from. A window is only ``complete`` when it contains at least one candle, has
    no reported gaps, and reaches the expected latest closed candle.
    """

    resolved_interval = require_positive_interval(interval)
    expected = require_utc_datetime(
        expected_latest_closed_open_time,
        field_name="expected_latest_closed_open_time",
    )
    window_start = candles[0].timestamp if candles else None
    window_end = candles[-1].timestamp if candles else None
    if not candles:
        missing_after_latest: int | None = None
    elif window_end is not None and window_end >= expected:
        missing_after_latest = 0
    else:
        assert window_end is not None  # candles is non-empty
        missing_after_latest = int((expected - window_end) // resolved_interval)
    missing_candle_count = sum(gap.missing_count for gap in gaps)
    complete = bool(candles) and not gaps and missing_after_latest == 0
    return DataCompleteness(
        candle_count=len(candles),
        window_start=window_start,
        window_end=window_end,
        expected_latest_closed_open_time=expected,
        missing_candles_after_latest_stored=missing_after_latest,
        gaps=gaps,
        missing_candle_count=missing_candle_count,
        complete=complete,
    )
