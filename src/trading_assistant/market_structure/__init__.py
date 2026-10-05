"""Deterministic market-structure engine built on validated closed OHLCV candles.

The engine turns stored candles into reproducible structural facts: confirmed
swings, structural trend, consolidation ranges, support/resistance zones,
volatility and volume context, higher-timeframe context, and a typed snapshot.

Every calculation is a pure function of candles, parameters, and an explicit UTC
``as_of`` instant. Nothing here guesses, interpolates missing candles, mutates
source history, or produces trading setups, signals, alerts, or AI output.
"""

from trading_assistant.market_structure.analysis import (
    TimeframeStructureAnalysis,
    analyze_candles,
)
from trading_assistant.market_structure.candles import (
    candle_span_count,
    candles_closed_by,
    candles_since,
    interval_for_timeframe,
    ordered_candles,
)
from trading_assistant.market_structure.completeness import (
    DataCompleteness,
    describe_completeness,
)
from trading_assistant.market_structure.higher_timeframe import (
    INCOMPLETE_DATA,
    INSUFFICIENT_CANDLES,
    NO_STORED_CANDLES,
    HigherTimeframeContext,
    build_higher_timeframe_context,
    higher_timeframes_for,
)
from trading_assistant.market_structure.levels import (
    LevelDetectionResult,
    LevelParameters,
    SupportResistanceZone,
    ZoneRole,
    detect_zones,
)
from trading_assistant.market_structure.numeric import (
    DERIVED_QUANTUM,
    as_decimal,
    divide,
    mean,
    percentage_of,
    percentage_width,
    quantize_derived,
    tolerance_band,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.ranges import (
    RangeDetection,
    RangeDetectionResult,
    RangeParameters,
    RangeRejectionReason,
    detect_range,
)
from trading_assistant.market_structure.service import (
    MarketStructureService,
    create_market_structure_service,
)
from trading_assistant.market_structure.snapshot import (
    MarketStructureSnapshot,
    to_jsonable,
)
from trading_assistant.market_structure.swings import (
    SwingDetectionResult,
    SwingKind,
    SwingParameters,
    SwingPoint,
    SwingTiePolicy,
    detect_swings,
)
from trading_assistant.market_structure.trend import (
    TrendClassification,
    TrendDirection,
    TrendParameters,
    TrendReason,
    classify_trend,
)
from trading_assistant.market_structure.volatility import (
    ATR_SMOOTHING,
    VolatilityContext,
    VolatilityParameters,
    calculate_volatility,
    true_range,
    wilder_average,
)
from trading_assistant.market_structure.volume import (
    RELATIVE_VOLUME_BASIS,
    VolumeContext,
    VolumeParameters,
    calculate_volume,
)

__all__ = [
    "ATR_SMOOTHING",
    "DERIVED_QUANTUM",
    "INCOMPLETE_DATA",
    "INSUFFICIENT_CANDLES",
    "NO_STORED_CANDLES",
    "RELATIVE_VOLUME_BASIS",
    "DataCompleteness",
    "HigherTimeframeContext",
    "LevelDetectionResult",
    "LevelParameters",
    "MarketStructureParameters",
    "MarketStructureService",
    "MarketStructureSnapshot",
    "RangeDetection",
    "RangeDetectionResult",
    "RangeParameters",
    "RangeRejectionReason",
    "SupportResistanceZone",
    "SwingDetectionResult",
    "SwingKind",
    "SwingParameters",
    "SwingPoint",
    "SwingTiePolicy",
    "TimeframeStructureAnalysis",
    "TrendClassification",
    "TrendDirection",
    "TrendParameters",
    "TrendReason",
    "VolatilityContext",
    "VolatilityParameters",
    "VolumeContext",
    "VolumeParameters",
    "ZoneRole",
    "analyze_candles",
    "as_decimal",
    "build_higher_timeframe_context",
    "calculate_volatility",
    "calculate_volume",
    "candle_span_count",
    "candles_closed_by",
    "candles_since",
    "classify_trend",
    "create_market_structure_service",
    "describe_completeness",
    "detect_range",
    "detect_swings",
    "detect_zones",
    "divide",
    "higher_timeframes_for",
    "interval_for_timeframe",
    "mean",
    "ordered_candles",
    "percentage_of",
    "percentage_width",
    "quantize_derived",
    "to_jsonable",
    "tolerance_band",
    "true_range",
    "wilder_average",
]
