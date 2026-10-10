"""Credential-free CCXT market-data source."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import ccxt

from trading_assistant.config import (
    DEFAULT_EXCHANGE_TIMEOUT_MS,
    MAX_EXCHANGE_TIMEOUT_MS,
    MIN_EXCHANGE_TIMEOUT_MS,
)
from trading_assistant.market_data.errors import ExchangeNetworkTimeout

logger = logging.getLogger(__name__)

# Binance Spot's public klines endpoint accepts at most 1000 candles per request.
_BINANCE_MAX_OHLCV_LIMIT = 1_000
_BINANCE_EXCHANGE_ID = "binance"

#: A single CCXT request performs several individually-bounded phases: DNS
#: resolution, one connect attempt per resolved address (an IPv6 attempt can
#: time out before the IPv4 attempt succeeds), the TLS handshake and the
#: response body. CCXT's ``timeout`` bounds each socket operation, but it
#: cannot bound DNS resolution at all - ``socket.create_connection`` resolves
#: the host name *outside* the per-address connect timeout, so during a
#: connectivity loss with an unresponsive resolver a request can block a
#: forward pass indefinitely - and it cannot bound the *sum* of the phases.
#: The watchdog therefore allows one network call up to this multiple of the
#: configured per-operation timeout before abandoning it: generous enough that
#: a healthy request (or one recovering from a single black-holed address
#: family) is never cut off, small enough that a genuinely hung request is cut
#: after a bounded time instead of stalling the runner forever.
NETWORK_CALL_DEADLINE_MULTIPLIER = 3


def resolve_exchange_timeout_ms(timeout_ms: int | None) -> int:
    """Validate and bound the CCXT request timeout, in milliseconds.

    Returns the configured value, or the documented default when none is
    given. Out-of-bounds values are rejected (never silently clamped): a tiny
    timeout false-fails healthy requests and a huge one only hides hangs.
    """

    value = DEFAULT_EXCHANGE_TIMEOUT_MS if timeout_ms is None else int(timeout_ms)
    if not MIN_EXCHANGE_TIMEOUT_MS <= value <= MAX_EXCHANGE_TIMEOUT_MS:
        raise ValueError(
            f"exchange timeout must be between {MIN_EXCHANGE_TIMEOUT_MS} and "
            f"{MAX_EXCHANGE_TIMEOUT_MS} milliseconds, got {value}"
        )
    return value


class CCXTMarketDataSource:
    """Fetch public Binance Spot OHLCV through a rate-limited CCXT client.

    Binance is the only supported live market-data source. No credentials or
    trading permissions are configured by this adapter. Two finite bounds apply
    to every network operation:

    * the project-controlled CCXT ``timeout`` (``exchange_timeout_ms``, in
      milliseconds - CCXT's timeout semantics), passed to the exchange
      constructor so it covers ``load_markets`` as well as ``fetch_ohlcv``
      (both run on the same exchange instance and its session);
    * a wall-clock watchdog deadline per network call
      (:data:`NETWORK_CALL_DEADLINE_MULTIPLIER` times the configured timeout),
      which exists because the CCXT timeout cannot bound DNS resolution. A
      call that exceeds the deadline is abandoned on a daemon thread, the
      exchange client is rebuilt for the next call, and
      :class:`ExchangeNetworkTimeout` is raised - a transient, retryable
      failure. The runner can therefore never sit indefinitely inside a network
      operation, and a shutdown is delayed by at most one watchdog deadline.

    Network errors are handled and logged by :class:`MarketDataService`.
    """

    #: Class-level defaults keep instances built without ``__init__`` (test
    #: doubles that inject a client directly) working unchanged.
    _timeout_ms: int = DEFAULT_EXCHANGE_TIMEOUT_MS
    _poisoned: bool = False
    _abandoned: tuple[Any, ...] = ()

    def __init__(self, exchange_id: str = _BINANCE_EXCHANGE_ID, *, timeout_ms: int | None = None) -> None:
        if exchange_id != _BINANCE_EXCHANGE_ID:
            raise ValueError(
                "only Binance Spot market data is supported; "
                f"got exchange id {exchange_id!r}"
            )
        exchange_class = getattr(ccxt, _BINANCE_EXCHANGE_ID, None)
        if exchange_class is None or _BINANCE_EXCHANGE_ID not in ccxt.exchanges:
            raise RuntimeError("the installed CCXT package does not provide Binance")
        self._exchange_class = exchange_class
        self._timeout_ms = resolve_exchange_timeout_ms(timeout_ms)
        self._exchange = self._build_exchange()
        self.exchange_id = self._exchange.id
        self.last_http_response: Any = None
        if not self._exchange.has.get("fetchOHLCV"):
            raise RuntimeError("the installed CCXT Binance client does not support fetchOHLCV")

    @property
    def timeout_ms(self) -> int:
        """The finite, project-controlled CCXT request timeout in milliseconds."""

        return self._timeout_ms

    def _build_exchange(self) -> Any:
        """Construct the exchange with the project-controlled finite timeout.

        CCXT's ``timeout`` is expressed in milliseconds and is applied to every
        individual socket connect/read of a request; ``enableRateLimit`` is
        kept. No API credentials are configured, so public endpoints only.
        """

        return self._exchange_class(
            {
                "enableRateLimit": True,
                "timeout": self._timeout_ms,
            }
        )

    @property
    def _network_deadline_seconds(self) -> float:
        """The finite wall-clock deadline for one network call, in seconds."""

        return (self._timeout_ms * NETWORK_CALL_DEADLINE_MULTIPLIER) / 1000.0

    def _ensure_exchange(self) -> None:
        """Rebuild the exchange client after an abandoned (timed-out) call.

        The abandoned call may still be running on a daemon thread; it only
        ever touches the exchange object it captured, so replacing the client
        here keeps the abandoned thread from sharing state with later calls.
        """

        if self._poisoned:
            logger.info(
                "Rebuilding the exchange client after an abandoned network call",
                extra={
                    "fields": {
                        "exchange": self.exchange_id,
                        "timeout_ms": str(self._timeout_ms),
                    }
                },
            )
            self._exchange = self._build_exchange()
            self._poisoned = False

    def _network_call(self, description: str, call: Callable[[], Any]) -> Any:
        """Run one CCXT network call under a finite wall-clock deadline.

        The call runs on a daemon thread. If it finishes in time its result
        (or its original exception) is returned on the calling thread. If it
        does not, the exchange is poisoned (the next call rebuilds it), the
        abandoned client is closed best-effort - which may also unstick the
        daemon thread - and :class:`ExchangeNetworkTimeout` is raised.
        """

        deadline = self._network_deadline_seconds
        outcome: dict[str, Any] = {}

        def _run() -> None:
            try:
                outcome["result"] = call()
            except BaseException as exc:  # re-raised on the calling thread
                outcome["error"] = exc

        logger.info(
            "Exchange network call started",
            extra={
                "fields": {
                    "stage": "FETCHING_MARKET_DATA",
                    "exchange": self.exchange_id,
                    "operation": description,
                    "deadline_ms": str(int(deadline * 1000)),
                }
            },
        )
        started = time.monotonic()
        worker = threading.Thread(
            target=_run,
            name=f"ccxt-{self.exchange_id}-{description}",
            daemon=True,
        )
        worker.start()
        worker.join(deadline)
        if worker.is_alive():
            elapsed_ms = int((time.monotonic() - started) * 1000)
            abandoned = self._exchange
            self._poisoned = True
            self._abandoned = (*self._abandoned, abandoned)
            try:
                abandoned.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must never mask the timeout
                logger.debug(
                    "Closing the abandoned exchange client failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
            logger.error(
                "Exchange network call exceeded its finite deadline and was abandoned",
                extra={
                    "fields": {
                        "stage": "ERROR",
                        "exchange": self.exchange_id,
                        "operation": description,
                        "deadline_ms": str(int(deadline * 1000)),
                        "elapsed_ms": str(elapsed_ms),
                        "timeout_ms": str(self._timeout_ms),
                    }
                },
            )
            raise ExchangeNetworkTimeout(
                f"{self.exchange_id} {description} did not complete within its "
                f"{int(deadline * 1000)}ms network deadline (configured exchange "
                f"timeout {self._timeout_ms}ms); the request was abandoned and "
                "will be retried - no data was fabricated"
            )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Exchange network call completed",
            extra={
                "fields": {
                    "stage": "FETCHING_MARKET_DATA",
                    "exchange": self.exchange_id,
                    "operation": description,
                    "elapsed_ms": str(elapsed_ms),
                }
            },
        )
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    @property
    def timeframes(self) -> dict[str, str] | None:
        return getattr(self._exchange, "timeframes", None)

    @property
    def max_ohlcv_limit(self) -> int:
        """Binance Spot's documented maximum klines per public request."""

        return _BINANCE_MAX_OHLCV_LIMIT

    @property
    def ohlcv_is_rolling_window(self) -> bool:
        """Binance Spot klines support date-bounded cursor pagination."""

        return False

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str,
        since_ms: int,
        limit: int,
    ) -> Any:
        """Fetch a single unified CCXT page and retain its HTTP response text.

        The locally-derived millisecond cursor is passed through unchanged to
        Binance Spot's date-bounded klines endpoint. The configured page size is
        capped at Binance's 1000-candle per-request maximum. The request runs
        under the finite watchdog deadline described on the class, so it cannot
        block the caller indefinitely.
        """

        self._ensure_exchange()
        exchange = self._exchange
        available_timeframes = self.timeframes
        if available_timeframes and timeframe not in available_timeframes:
            raise ValueError(
                f"Timeframe {timeframe!r} is not supported by CCXT exchange {self.exchange_id!r}"
            )
        request_limit = min(limit, self.max_ohlcv_limit)

        # Binance's CCXT market loader accepts Decimal number parsing. Keep it
        # enabled before metadata loading and OHLCV parsing so numeric candle
        # fields retain their source precision without a float round-trip.
        exchange.number = Decimal

        response = self._network_call(
            "fetch_ohlcv",
            lambda: exchange.fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=request_limit,
            ),
        )
        self.last_http_response = getattr(exchange, "last_http_response", None)
        return response

    def close(self) -> None:
        """Release the CCXT client's network resources if opened."""

        for exchange in (self._exchange, *self._abandoned):
            try:
                exchange.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must never raise
                logger.debug(
                    "Closing an exchange client failed (%s: %s)",
                    type(exc).__name__,
                    exc,
                )
        self._abandoned = ()
