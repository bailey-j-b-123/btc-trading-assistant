"""Errors raised by the market-data layer."""


class MarketDataError(RuntimeError):
    """Base class for operational market-data failures."""


class ExchangeDataError(MarketDataError):
    """An exchange request failed or returned an unusable response."""


class PaginationError(MarketDataError):
    """Pagination could not safely make progress through the requested range."""


class RawDataWriteError(MarketDataError):
    """A raw source response could not be preserved on disk."""


class HistoricalCandleConflict(MarketDataError):
    """An exchange returned different values for an already-stored candle key."""
