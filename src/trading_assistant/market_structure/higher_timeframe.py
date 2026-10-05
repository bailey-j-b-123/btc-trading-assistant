"""Higher-timeframe context built from independently stored timeframe candles.

Higher-timeframe analysis never resamples or synthesizes candles from another
timeframe. Each configured higher timeframe is read from its own Step 2 stored
candles at the same ``as_of`` instant. When a timeframe has no stored candles,
too few candles to evaluate a swing window, or missing candles inside the
retrieved range, that state is reported explicitly instead of being hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.market_structure.analysis import (
    TimeframeStructureAnalysis,
    analyze_candles,
)
from trading_assistant.market_structure.completeness import (
    DataCompleteness,
    describe_completeness,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.ranges import RangeDetection
from trading_assistant.market_structure.swings import SwingPoint
from trading_assistant.market_structure.trend import TrendClassification

#: Reported when a higher timeframe has no stored candles at all.
NO_STORED_CANDLES = "no_stored_candles"

#: Reported when a higher timeframe has fewer candles than one swing window.
INSUFFICIENT_CANDLES = "insufficient_candles"

#: Reported when a higher timeframe is analyzed but its candle window has gaps.
INCOMPLETE_DATA = "incomplete_data"


@dataclass(frozen=True, slots=True)
class HigherTimeframeContext:
    """Structure context for one configured higher timeframe, or its absence.

    ``reason`` is ``None`` only when the timeframe was analyzed and every candle
    expected for its window is present. ``synthesized`` is always ``False``: the
    engine only reads candles that were stored for this timeframe directly.
    """

    timeframe: str
    available: bool
    reason: str | None
    analysis: TimeframeStructureAnalysis | None
    completeness: DataCompleteness
    synthesized: bool = False

    @property
    def trend(self) -> TrendClassification | None:
        return self.analysis.trend if self.analysis is not None else None

    @property
    def swings(self) -> tuple[SwingPoint, ...]:
        return self.analysis.confirmed_swings if self.analysis is not None else ()

    @property
    def active_range(self) -> RangeDetection | None:
        return self.analysis.active_range if self.analysis is not None else None

    @property
    def latest_close(self) -> Decimal | None:
        """Close of the latest stored higher-timeframe candle, when one exists."""

        return self.analysis.volatility.latest_close if self.analysis is not None else None


def higher_timeframes_for(timeframe: str, supported_timeframes: tuple[str, ...]) -> tuple[str, ...]:
    """Return the configured fixed-duration timeframes strictly longer than ``timeframe``.

    Candidates come from the configured supported timeframe set, are sorted by
    duration (shortest first) and then by name, and never include the requested
    timeframe itself. Non-fixed-duration entries are ignored.
    """

    base_milliseconds = timeframe_to_milliseconds(timeframe)
    candidates: list[tuple[int, str]] = []
    for candidate in supported_timeframes:
        if candidate == timeframe:
            continue
        try:
            candidate_milliseconds = timeframe_to_milliseconds(candidate)
        except (TypeError, ValueError):
            continue
        if candidate_milliseconds > base_milliseconds:
            candidates.append((candidate_milliseconds, candidate))
    return tuple(candidate for _, candidate in sorted(candidates))


def build_higher_timeframe_context(
    timeframe: str,
    candles: tuple[Candle, ...],
    *,
    interval: timedelta,
    as_of: datetime,
    expected_latest_closed_open_time: datetime,
    parameters: MarketStructureParameters,
    gaps: tuple[CandleGap, ...] = (),
) -> HigherTimeframeContext:
    """Build one higher-timeframe context, making missing data fully explicit."""

    completeness = describe_completeness(
        candles,
        interval=interval,
        expected_latest_closed_open_time=expected_latest_closed_open_time,
        gaps=gaps,
    )
    if not candles:
        return HigherTimeframeContext(
            timeframe=timeframe,
            available=False,
            reason=NO_STORED_CANDLES,
            analysis=None,
            completeness=completeness,
        )
    if len(candles) < parameters.minimum_candle_count:
        return HigherTimeframeContext(
            timeframe=timeframe,
            available=False,
            reason=INSUFFICIENT_CANDLES,
            analysis=None,
            completeness=completeness,
        )

    analysis = analyze_candles(candles, interval=interval, as_of=as_of, parameters=parameters)
    reason = INCOMPLETE_DATA if not completeness.complete else None
    return HigherTimeframeContext(
        timeframe=timeframe,
        available=True,
        reason=reason,
        analysis=analysis,
        completeness=completeness,
    )
