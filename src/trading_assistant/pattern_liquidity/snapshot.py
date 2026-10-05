"""Typed chronological evidence catalog and explicit coverage diagnostics."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.completeness import DataCompleteness
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    ChartPattern,
    EqualLevelCluster,
    Event,
    FailedBreakout,
    Retest,
    Sweep,
)
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class PatternLiquiditySnapshot:
    exchange: str
    symbol: str
    timeframe: str
    as_of: datetime
    breakouts: tuple[Breakout, ...]
    failed_breakouts: tuple[FailedBreakout, ...]
    sweeps: tuple[Sweep, ...]
    retests: tuple[Retest, ...]
    equal_levels: tuple[EqualLevelCluster, ...]
    chart_patterns: tuple[ChartPattern, ...]
    structure: TimeframeStructureAnalysis
    completeness: DataCompleteness
    parameters: PatternLiquidityParameters
    status: Literal["evaluated", "insufficient", "incomplete"]
    reasons: tuple[str, ...]

    def events(self) -> tuple[Event, ...]:
        """Append-only occurrences/transitions, not just latest pattern states."""
        return tuple(
            sorted(
                (
                    *self.breakouts,
                    *self.failed_breakouts,
                    *self.sweeps,
                    *self.retests,
                    *self.equal_levels,
                    *self.chart_patterns,
                ),
                key=lambda e: (e.known_at, e.id),
            )
        )

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)
