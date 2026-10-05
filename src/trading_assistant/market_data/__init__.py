"""Deterministic OHLCV acquisition, validation, storage, and retrieval."""

from trading_assistant.market_data.errors import (
    ExchangeDataError,
    HistoricalCandleConflict,
    MarketDataError,
    PaginationError,
    RawDataWriteError,
)
from trading_assistant.market_data.service import (
    MarketDataService,
    create_market_data_service,
)
from trading_assistant.market_data.types import (
    Candle,
    CandleGap,
    CandleQueryResult,
    MarketDataUpdateResult,
)
from trading_assistant.market_data.validation import (
    CandleValidationError,
    CandleValidationIssue,
    CandleValidationReport,
    parse_ohlcv_row,
    validate_ohlcv_rows,
)

__all__ = [
    "Candle",
    "CandleGap",
    "CandleQueryResult",
    "CandleValidationError",
    "CandleValidationIssue",
    "CandleValidationReport",
    "ExchangeDataError",
    "HistoricalCandleConflict",
    "MarketDataError",
    "MarketDataService",
    "MarketDataUpdateResult",
    "PaginationError",
    "RawDataWriteError",
    "create_market_data_service",
    "parse_ohlcv_row",
    "validate_ohlcv_rows",
]
