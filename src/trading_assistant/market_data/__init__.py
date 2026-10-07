"""Deterministic OHLCV acquisition, validation, storage, and retrieval."""

from trading_assistant.market_data.errors import (
    ExchangeDataError,
    ExchangeNetworkTimeout,
    HistoricalCandleConflict,
    MarketDataError,
    PaginationError,
    RawDataWriteError,
    is_transient_network_error,
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
    "ExchangeNetworkTimeout",
    "HistoricalCandleConflict",
    "MarketDataError",
    "MarketDataService",
    "MarketDataUpdateResult",
    "PaginationError",
    "RawDataWriteError",
    "create_market_data_service",
    "is_transient_network_error",
    "parse_ohlcv_row",
    "validate_ohlcv_rows",
]
