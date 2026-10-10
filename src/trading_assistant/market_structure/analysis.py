"""Composition of every market-structure component for one timeframe.

``analyze_candles`` is the single pure entry point: given closed candles, a
fixed interval, an explicit UTC ``as_of`` instant, and validated parameters, it
returns one immutable, fully typed analysis object. It performs no I/O, never
modifies the input candles, and drops any candle whose interval had not closed
by ``as_of`` before any component sees it.
"""

from __future__ import annotations

from collections import OrderedDict

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import (
    candles_closed_by,
    ordered_candles,
    require_positive_interval,
)
from trading_assistant.market_structure.levels import LevelDetectionResult, detect_zones
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.ranges import (
    RangeDetection,
    RangeDetectionResult,
    detect_range,
)
from trading_assistant.market_structure.swings import (
    SwingDetectionResult,
    SwingPoint,
    detect_swings,
)
from trading_assistant.market_structure.trend import TrendClassification, classify_trend
from trading_assistant.market_structure.volatility import (
    VolatilityContext,
    calculate_volatility,
)
from trading_assistant.market_structure.volume import VolumeContext, calculate_volume


@dataclass(frozen=True, slots=True)
class TimeframeStructureAnalysis:
    """Every deterministic structural fact for one timeframe at one instant."""

    as_of: datetime
    candle_count: int
    excluded_future_candle_count: int
    window_start_timestamp: datetime | None
    window_end_timestamp: datetime | None
    swings: SwingDetectionResult
    trend: TrendClassification
    range: RangeDetectionResult
    levels: LevelDetectionResult
    volatility: VolatilityContext
    volume: VolumeContext
    parameters: MarketStructureParameters

    @property
    def confirmed_swings(self) -> tuple[SwingPoint, ...]:
        return self.swings.swings

    @property
    def detected_range(self) -> RangeDetection | None:
        """The consolidation range identified in the lookback window, if any."""

        return self.range.range

    @property
    def active_range(self) -> RangeDetection | None:
        """The detected range when it is still active, otherwise ``None``."""

        detected = self.range.range
        if detected is None or not detected.active:
            return None
        return detected


#: Bounded memo for :func:`analyze_candles`. The analysis is a pure function of
#: its inputs, and the pattern replay calls it for the same growing prefixes once
#: per snapshot, so repeated prefixes are answered from here. The key is the exact
#: content: decimal values are keyed by ``str`` (which preserves exponent, unlike
#: Decimal equality), so two inputs share an entry only when every output is
#: identical. Results are frozen dataclasses, so sharing them is safe.
_ANALYSIS_MEMO: "OrderedDict[tuple, TimeframeStructureAnalysis]" = OrderedDict()
_ANALYSIS_MEMO_LIMIT = 4096


def _candle_key(candle: Candle) -> tuple:
    return (
        candle.exchange,
        candle.symbol,
        candle.timeframe,
        candle.timestamp,
        str(candle.open),
        str(candle.high),
        str(candle.low),
        str(candle.close),
        str(candle.volume),
    )


def analyze_candles(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: MarketStructureParameters | None = None,
) -> TimeframeStructureAnalysis:
    """Analyze one candle sequence without looking past ``as_of``.

    Candles are validated for chronological order and instrument identity, then
    restricted to those fully closed at ``as_of``. Every component receives only
    that closed prefix, so a result computed from a long history is identical to
    the same result computed historically from data available at that instant.
    """

    materialized = tuple(candles)
    key = (
        tuple(_candle_key(c) for c in materialized),
        interval,
        as_of,
        parameters if parameters is not None else MarketStructureParameters(),
    )
    cached = _ANALYSIS_MEMO.get(key)
    if cached is not None:
        _ANALYSIS_MEMO.move_to_end(key)
        return cached
    result = _analyze_candles_uncached(
        materialized, interval=interval, as_of=as_of, parameters=parameters
    )
    _ANALYSIS_MEMO[key] = result
    while len(_ANALYSIS_MEMO) > _ANALYSIS_MEMO_LIMIT:
        _ANALYSIS_MEMO.popitem(last=False)
    return result


def _analyze_candles_uncached(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: MarketStructureParameters | None = None,
) -> TimeframeStructureAnalysis:
    resolved = parameters if parameters is not None else MarketStructureParameters()
    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = ordered_candles(candles, interval=resolved_interval)
    closed = candles_closed_by(ordered, interval=resolved_interval, as_of=as_of_utc)
    excluded_future = len(ordered) - len(closed)

    swing_result = detect_swings(
        closed,
        interval=resolved_interval,
        as_of=as_of_utc,
        parameters=resolved.swings,
    )
    trend = classify_trend(swing_result.swings, as_of=as_of_utc, parameters=resolved.trend)
    range_result = detect_range(
        closed,
        swing_result.swings,
        interval=resolved_interval,
        as_of=as_of_utc,
        parameters=resolved.ranges,
    )
    level_result = detect_zones(
        swing_result.swings,
        as_of=as_of_utc,
        latest_close=closed[-1].close if closed else None,
        parameters=resolved.levels,
    )
    volatility = calculate_volatility(
        closed,
        interval=resolved_interval,
        as_of=as_of_utc,
        parameters=resolved.volatility,
    )
    volume = calculate_volume(
        closed,
        interval=resolved_interval,
        as_of=as_of_utc,
        parameters=resolved.volume,
    )

    return TimeframeStructureAnalysis(
        as_of=as_of_utc,
        candle_count=len(closed),
        excluded_future_candle_count=excluded_future,
        window_start_timestamp=closed[0].timestamp if closed else None,
        window_end_timestamp=closed[-1].timestamp if closed else None,
        swings=swing_result,
        trend=trend,
        range=range_result,
        levels=level_result,
        volatility=volatility,
        volume=volume,
        parameters=resolved,
    )
