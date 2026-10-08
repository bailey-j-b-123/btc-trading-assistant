"""Isolated, read-only public quote for the visual display; no DB or engine imports.

A quote is NOT a closed candle or an exchange timestamp. The fetched_at clock
records when our server received a valid public ticker response. Failure never
substitutes a quote, and callers must not route this payload into analysis.

The quote follows the configured exchange (``TRADING_ASSISTANT_EXCHANGE``):
Kraken's public ticker or Binance's public spot ticker. Exchange identities are
kept separate — the payload always names the exchange it actually queried, so
a Binance deployment can never display a Kraken quote (or vice versa) without
the label saying so.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.request import urlopen

from trading_assistant.config import get_settings

logger = logging.getLogger(__name__)
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSDT"
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
BINANCE_TICKER_SYMBOL = "BTCUSDT"
CACHE_SECONDS = 15
STALE_SECONDS = 45
_lock = threading.Lock()
_cached: dict[str, object] | None = None
_last_attempt = 0.0
_last_failed = False


def _fetch_price() -> str:
    with urlopen(KRAKEN_TICKER_URL, timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("error") != []:
        raise ValueError("Invalid Kraken ticker response")
    result = data.get("result")
    if not isinstance(result, dict) or set(result) != {"XBTUSDT"}:
        raise ValueError("Invalid Kraken ticker pair")
    ticker = result["XBTUSDT"]
    value = ticker.get("c") if isinstance(ticker, dict) else None
    if not isinstance(value, list) or not value or not isinstance(value[0], str):
        raise ValueError("Missing Kraken last trade price")
    price = float(value[0])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid Kraken last trade price")
    return value[0]


def _fetch_binance_price() -> str:
    """Binance public spot ticker: the last trade price for BTCUSDT.

    Strictly validated like the Kraken path: a single-symbol payload with a
    finite, positive price string. The exact string is returned so the display
    never rounds or reformats the exchange's own value.
    """

    with urlopen(BINANCE_TICKER_URL, timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("symbol") != BINANCE_TICKER_SYMBOL:
        raise ValueError("Invalid Binance ticker symbol")
    value = data.get("price")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing Binance last trade price")
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid Binance last trade price")
    return value


def _quote_source(exchange: str) -> tuple[Callable[[], str], str]:
    """Return the public ticker fetcher and source label for an exchange id.

    Fetcher references are resolved at call time (not captured in a module-level
    map) so tests can monkeypatch the module functions, and so an unsupported
    exchange fails closed instead of silently quoting another venue.
    """

    if exchange == "binance":
        return _fetch_binance_price, "Binance public ticker"
    if exchange == "kraken":
        return _fetch_price, "Kraken public ticker"
    raise ValueError(f"Unsupported live quote exchange: {exchange!r}")


def _configured_exchange() -> str:
    """Resolve the exchange from the same environment the application reads."""

    try:
        return get_settings().exchange
    except Exception:  # the display island fails closed, never guesses a venue
        logger.warning("Live quote exchange configuration unavailable", exc_info=True)
        return ""


def live_price(exchange: str | None = None) -> dict[str, object]:
    """Throttled independently of the dashboard; last good quote is stale, not live.

    ``exchange`` defaults to the configured ``TRADING_ASSISTANT_EXCHANGE``; the
    returned payload always carries the exchange identity of the quote it
    actually holds, so a cached quote is never mislabelled after a config change.
    """

    global _cached, _last_attempt, _last_failed
    resolved = exchange if exchange is not None else _configured_exchange()
    with _lock:
        now = time.monotonic()
        cached_exchange = _cached.get("exchange") if _cached is not None else None
        exchange_changed = _cached is not None and cached_exchange != resolved
        if (
            now - _last_attempt >= CACHE_SECONDS
            or not _last_attempt
            or exchange_changed
        ):
            # Never show a quote from the previously configured venue if the
            # newly selected venue is offline. A switch invalidates the old
            # cached identity before fetching; failure then returns UNAVAILABLE.
            if exchange_changed:
                _cached = None
                _last_failed = False
            _last_attempt = now
            try:
                fetcher, source = _quote_source(resolved)
                price = fetcher()
                _last_failed = False
                _cached = {
                    "price": price,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "received_monotonic": time.monotonic(),
                    "exchange": resolved,
                    "source": source,
                }
            except Exception:  # isolated display endpoint must fail closed
                _last_failed = True
                logger.warning(
                    "Public %s ticker unavailable", resolved or "configured", exc_info=True
                )
        if _cached is None:
            return {
                "status": "UNAVAILABLE",
                "price": None,
                "fetched_at": None,
                "exchange": resolved,
                "source": f"{resolved} public ticker" if resolved else "public ticker",
                "display_only": True,
            }
        age = time.monotonic() - float(_cached["received_monotonic"])
        return {
            "status": "STALE" if _last_failed or age >= STALE_SECONDS else "CURRENT",
            "price": _cached["price"],
            "fetched_at": _cached["fetched_at"],
            "exchange": _cached.get("exchange", resolved),
            "source": _cached.get("source", f"{resolved} public ticker"),
            "display_only": True,
        }
