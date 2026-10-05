"""Immutable types shared by market-data validation, storage, and retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Candle:
    """A normalized OHLCV candle with UTC open time and exact decimal values."""

    exchange: str
    symbol: str
    timeframe: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class CandleGap:
    """An inclusive range of expected candle open times absent from source data."""

    start: datetime
    end: datetime
    missing_count: int


@dataclass(frozen=True, slots=True)
class CandleQueryResult:
    """Chronologically ordered stored candles plus an explicit completeness report."""

    exchange: str
    symbol: str
    timeframe: str
    candles: tuple[Candle, ...]
    gaps: tuple[CandleGap, ...]

    @property
    def missing_candle_count(self) -> int:
        return sum(gap.missing_count for gap in self.gaps)

    @property
    def complete(self) -> bool:
        return not self.gaps


@dataclass(frozen=True, slots=True)
class MarketDataUpdateResult:
    """Outcome summary; ``complete`` is false whenever expected timestamps are missing."""

    exchange: str
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    received_count: int
    accepted_count: int
    rejected_count: int
    inserted_count: int
    already_present_count: int
    excluded_open_count: int
    gaps: tuple[CandleGap, ...]
    raw_files: tuple[Path, ...]

    @property
    def missing_candle_count(self) -> int:
        return sum(gap.missing_count for gap in self.gaps)

    @property
    def complete(self) -> bool:
        return not self.gaps
