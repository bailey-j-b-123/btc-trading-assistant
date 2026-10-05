"""Typed market-structure snapshot and its JSON-safe projection.

A snapshot is a complete, immutable, machine-readable record of every
deterministic structural fact for one exchange/symbol/timeframe at one UTC
``as_of`` instant. It contains no wall-clock timestamps of its own, so the same
requested instant always produces an equal snapshot from equal candle data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.completeness import DataCompleteness
from trading_assistant.market_structure.higher_timeframe import HigherTimeframeContext
from trading_assistant.market_structure.levels import SupportResistanceZone
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.ranges import RangeDetection
from trading_assistant.market_structure.swings import SwingPoint
from trading_assistant.market_structure.trend import TrendClassification
from trading_assistant.market_structure.volatility import VolatilityContext
from trading_assistant.market_structure.volume import VolumeContext


def to_jsonable(value: Any) -> Any:
    """Convert a market-structure value into JSON-compatible primitives.

    Datetimes are rendered as UTC ISO-8601 strings, ``Decimal`` values as exact
    decimal strings, and enums as their documented string values. Unknown types
    raise ``TypeError`` so no value is ever silently dropped or coerced.
    """

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"unsupported market-structure value type: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class MarketStructureSnapshot:
    """Complete deterministic structure record for one as-of instant."""

    exchange: str
    symbol: str
    timeframe: str
    as_of: datetime
    latest_closed_candle: Candle | None
    analysis: TimeframeStructureAnalysis
    completeness: DataCompleteness
    higher_timeframes: tuple[HigherTimeframeContext, ...]

    @property
    def parameters(self) -> MarketStructureParameters:
        """Calculation parameters used for this snapshot."""

        return self.analysis.parameters

    @property
    def swings(self) -> tuple[SwingPoint, ...]:
        return self.analysis.confirmed_swings

    @property
    def trend(self) -> TrendClassification:
        return self.analysis.trend

    @property
    def detected_range(self) -> RangeDetection | None:
        return self.analysis.detected_range

    @property
    def active_range(self) -> RangeDetection | None:
        return self.analysis.active_range

    @property
    def zones(self) -> tuple[SupportResistanceZone, ...]:
        return self.analysis.levels.zones

    @property
    def volatility(self) -> VolatilityContext:
        return self.analysis.volatility

    @property
    def volume(self) -> VolumeContext:
        return self.analysis.volume

    @property
    def gaps(self) -> tuple[CandleGap, ...]:
        return self.completeness.gaps

    @property
    def missing_candle_count(self) -> int:
        return self.completeness.missing_candle_count

    @property
    def complete(self) -> bool:
        return self.completeness.complete

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary with one key per documented section.

        Keys: ``exchange``, ``symbol``, ``timeframe``, ``as_of``,
        ``latest_closed_candle``, ``swings`` (confirmed points),
        ``swing_detection`` (full diagnostics), ``trend``, ``range``
        (``detection`` / ``detected`` / ``active``), ``zones``, ``volatility``,
        ``volume``, ``higher_timeframes``, ``completeness``, ``parameters``.
        """

        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": to_jsonable(self.as_of),
            "latest_closed_candle": to_jsonable(self.latest_closed_candle),
            "swings": to_jsonable(self.analysis.confirmed_swings),
            "swing_detection": to_jsonable(self.analysis.swings),
            "trend": to_jsonable(self.trend),
            "range": {
                "detection": to_jsonable(self.analysis.range),
                "detected": to_jsonable(self.detected_range),
                "active": to_jsonable(self.active_range),
            },
            "zones": to_jsonable(self.zones),
            "volatility": to_jsonable(self.volatility),
            "volume": to_jsonable(self.volume),
            "higher_timeframes": to_jsonable(self.higher_timeframes),
            "completeness": to_jsonable(self.completeness),
            "parameters": to_jsonable(self.parameters),
        }
