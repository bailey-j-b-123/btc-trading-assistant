"""Credential-free CCXT market-data source."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import ccxt


# Kraken's public OHLC endpoint returns at most 720 candles per request.
_KRAKEN_MAX_OHLCV_LIMIT = 720


class CCXTMarketDataSource:
    """Fetch public OHLCV through a rate-limited CCXT exchange instance.

    No credentials or trading permissions are configured by this adapter. Network
    errors are handled and logged by :class:`MarketDataService`.
    """

    def __init__(self, exchange_id: str) -> None:
        exchange_class = getattr(ccxt, exchange_id, None)
        if exchange_class is None or exchange_id not in ccxt.exchanges:
            raise ValueError(f"Unknown CCXT exchange id: {exchange_id!r}")
        self._exchange = exchange_class({"enableRateLimit": True})
        # CCXT's unified numeric parser defaults to float; use Decimal before
        # OHLCV parsing so available decimal source precision is retained.
        self._exchange.number = Decimal
        self.exchange_id = self._exchange.id
        self.last_http_response: Any = None
        if not self._exchange.has.get("fetchOHLCV"):
            raise ValueError(f"CCXT exchange {exchange_id!r} does not support fetchOHLCV")

    @property
    def timeframes(self) -> dict[str, str] | None:
        return getattr(self._exchange, "timeframes", None)

    @property
    def max_ohlcv_limit(self) -> int | None:
        """Return a known per-request OHLCV cap without constraining other exchanges."""

        if self.exchange_id == "kraken":
            return _KRAKEN_MAX_OHLCV_LIMIT
        return None

    @property
    def ohlcv_is_rolling_window(self) -> bool:
        """Whether the exchange endpoint cannot provide arbitrary date ranges.

        Kraken's public OHLC endpoint returns only its latest 720 entries,
        regardless of how old ``since`` is.  It is therefore not safe to run
        the generic date-based pagination loop against it: a later request can
        return the same rolling window instead of the next historical page.
        """

        return self.exchange_id == "kraken"

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str,
        since_ms: int,
        limit: int,
    ) -> Any:
        """Fetch a single unified CCXT page and retain its HTTP response text."""

        available_timeframes = self.timeframes
        if available_timeframes and timeframe not in available_timeframes:
            raise ValueError(
                f"Timeframe {timeframe!r} is not supported by CCXT exchange {self.exchange_id!r}"
            )
        max_limit = self.max_ohlcv_limit
        request_limit = (
            min(limit, max_limit)
            if max_limit is not None and limit is not None
            else limit
        )
        response = self._exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since_ms,
            limit=request_limit,
        )
        self.last_http_response = getattr(self._exchange, "last_http_response", None)
        return response

    def close(self) -> None:
        """Release the CCXT client's network resources if opened."""

        self._exchange.close()
