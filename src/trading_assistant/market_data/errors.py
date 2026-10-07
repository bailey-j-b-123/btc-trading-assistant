"""Errors raised by the market-data layer."""

from __future__ import annotations

import socket


class MarketDataError(RuntimeError):
    """Base class for operational market-data failures."""


class ExchangeDataError(MarketDataError):
    """An exchange request failed or returned an unusable response."""


class ExchangeNetworkTimeout(ExchangeDataError):
    """A public exchange request exceeded its finite network deadline.

    Raised by the watchdog in
    :class:`~trading_assistant.market_data.exchange.CCXTMarketDataSource` when
    one CCXT network call (market-metadata load or OHLCV request) has not
    completed within its wall-clock deadline. CCXT's per-operation timeout
    cannot bound DNS resolution, so without this watchdog a single request
    could block a forward pass indefinitely during a connectivity loss. It is
    always classified as a transient network failure: the request is abandoned
    (never fabricated), the exchange client is rebuilt, and the failure is
    retried with bounded backoff.
    """


class PaginationError(MarketDataError):
    """Pagination could not safely make progress through the requested range."""


class RawDataWriteError(MarketDataError):
    """A raw source response could not be preserved on disk."""


class HistoricalCandleConflict(MarketDataError):
    """An exchange returned different values for an already-stored candle key."""


def _transient_network_exception_types() -> tuple[type[BaseException], ...]:
    """The exception types that mean "transient external network failure".

    The real CCXT hierarchy is used (``ccxt.NetworkError`` and its
    ``RequestTimeout`` / ``ExchangeNotAvailable`` / ``DDoSProtection``
    subclasses, plus the network errors CCXT wraps low-level I/O into), the
    ``requests`` exceptions that can escape before CCXT wraps them, and the
    operating-system network errors for connection loss, reset, refusal, DNS
    failure and timeouts. Deliberately absent: configuration, validation,
    schema/programming errors and SQLite lock failures.
    """

    import ccxt
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import Timeout as RequestsTimeout

    return (
        ExchangeNetworkTimeout,
        ccxt.NetworkError,
        RequestsConnectionError,
        RequestsTimeout,
        ConnectionError,
        TimeoutError,
        socket.timeout,
        socket.gaierror,
    )


def is_transient_network_error(exc: BaseException) -> bool:
    """True when ``exc`` is a transient external network/exchange failure.

    A transient failure is an *external* condition - connection timeout,
    network unavailable, connection reset, DNS temporary failure, the exchange
    being temporarily unavailable - that a retry after a bounded backoff can
    reasonably fix. The whole ``__cause__``/``__context__`` chain is walked
    because the layers wrap: the market-data service raises
    ``ExchangeDataError`` *from* the CCXT error, and the forward service may
    wrap that again. Classification is by exception type, never by matching
    error strings. Local/structural failures (invalid configuration, schema or
    programming errors, SQLite lock failures, deterministic invariant
    violations) are deliberately *not* transient: they must not be retried as
    if they were network outages.
    """

    transient_types = _transient_network_exception_types()
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, transient_types):
            return True
        current = current.__cause__ or current.__context__
    return False


__all__ = [
    "ExchangeDataError",
    "ExchangeNetworkTimeout",
    "HistoricalCandleConflict",
    "MarketDataError",
    "PaginationError",
    "RawDataWriteError",
    "is_transient_network_error",
]
