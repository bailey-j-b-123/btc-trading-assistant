"""Typed, validated parameters for every market-structure component."""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_assistant.market_structure.levels import LevelParameters
from trading_assistant.market_structure.ranges import RangeParameters
from trading_assistant.market_structure.swings import SwingParameters
from trading_assistant.market_structure.trend import TrendParameters
from trading_assistant.market_structure.volatility import VolatilityParameters
from trading_assistant.market_structure.volume import VolumeParameters


@dataclass(frozen=True, slots=True)
class MarketStructureParameters:
    """Every parameter used to produce one market-structure result.

    The aggregate is embedded in each snapshot so a stored result can always be
    re-derived exactly, and so consumers never have to guess default values.
    """

    swings: SwingParameters = field(default_factory=SwingParameters)
    trend: TrendParameters = field(default_factory=TrendParameters)
    ranges: RangeParameters = field(default_factory=RangeParameters)
    levels: LevelParameters = field(default_factory=LevelParameters)
    volatility: VolatilityParameters = field(default_factory=VolatilityParameters)
    volume: VolumeParameters = field(default_factory=VolumeParameters)
    higher_timeframes: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.higher_timeframes is None:
            return
        normalized = tuple(str(timeframe) for timeframe in self.higher_timeframes)
        if not normalized:
            raise ValueError("higher_timeframes must be None or a non-empty sequence of timeframe strings")
        if any(not timeframe.strip() for timeframe in normalized):
            raise ValueError("higher_timeframes entries must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("higher_timeframes must not contain duplicates")
        object.__setattr__(self, "higher_timeframes", normalized)

    @property
    def minimum_candle_count(self) -> int:
        """Smallest candle count that can produce at least one confirmed swing."""

        return self.swings.window_size
