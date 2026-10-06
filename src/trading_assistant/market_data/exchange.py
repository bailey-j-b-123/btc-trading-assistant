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
        self.exchange_id = self._exchange.id
        self.last_http_response: Any = None
        if not self._exchange.has.get("fetchOHLCV"):
            raise ValueError(
                f"CCXT exchange {exchange_id!r} does not support fetchOHLCV"
            )

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
        """Fetch a single unified CCXT page and retain its HTTP response text.

        The locally-derived millisecond cursor is passed through unchanged for
        date-bounded endpoints.  A rolling-window endpoint (see
        :attr:`ohlcv_is_rolling_window`) cannot use it: Kraken serves only its
        newest 720 entries no matter how old ``since`` is, so the cursor can
        never retrieve older history there and is deliberately not sent.  The
        cursor-less response is a superset of any cursor-filtered response, so
        omitting it cannot remove a candle the caller asked for; the returned
        page is still validated against the requested range by
        :class:`~trading_assistant.market_data.service.MarketDataService`.
        """

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
        request_since = None if self.ohlcv_is_rolling_window else since_ms

        # Load CCXT market metadata before switching its numeric parser. Kraken
        # parses fetched currency precision into ``Decimal`` values when
        # ``number`` is Decimal, then its market loader passes those values to
        # ``safe_number``/``safe_string``; CCXT's ``safe_string`` rejects
        # Decimal and raises ``method() missing currencyPrecision``. The
        # default float parser is safe for metadata; Decimal is still applied
        # before OHLCV parsing so candle values retain their source precision.
        load_markets = getattr(self._exchange, "load_markets", None)
        if (
            self.exchange_id == "kraken"
            and getattr(self._exchange, "markets", None) is None
            and callable(load_markets)
        ):
            self._exchange.number = float
            load_markets()
        self._exchange.number = Decimal

        response = self._exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=request_since,
            limit=request_limit,
        )
        self.last_http_response = getattr(self._exchange, "last_http_response", None)
        return response

    def close(self) -> None:
        """Release the CCXT client's network resources if opened."""

        self._exchange.close()
