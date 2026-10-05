"""Credential-free CCXT market-data source."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import ccxt


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
        response = self._exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since_ms,
            limit=limit,
        )
        self.last_http_response = getattr(self._exchange, "last_http_response", None)
        return response

    def close(self) -> None:
        """Release the CCXT client's network resources if opened."""

        self._exchange.close()
