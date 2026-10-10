"""Isolated, read-only Binance Spot quote for the visual display.

A quote is NOT a closed candle or an exchange timestamp. ``fetched_at`` records
when our server received a valid public ticker response. Failure never
substitutes another venue or a fabricated value, and callers must not route
this display-only payload into analysis.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.request import urlopen

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
BINANCE_TICKER_SYMBOL = "BTCUSDT"
BINANCE_EXCHANGE_ID = "binance"
BINANCE_TICKER_SOURCE = "Binance Spot public ticker"
CACHE_SECONDS = 15
STALE_SECONDS = 45

_lock = threading.Lock()
_cached: dict[str, object] | None = None
_last_attempt = 0.0
_last_failed = False


def _fetch_binance_price() -> str:
    """Read and strictly validate the public BTCUSDT last-trade price."""

    with urlopen(BINANCE_TICKER_URL, timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("symbol") != BINANCE_TICKER_SYMBOL:
        raise ValueError("Invalid Binance Spot ticker symbol")
    value = data.get("price")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing Binance Spot last-trade price")
    try:
        decimal_price = Decimal(value)
        numeric_price = float(decimal_price)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise ValueError("Invalid Binance Spot last-trade price") from exc
    if not decimal_price.is_finite() or decimal_price <= 0 or not math.isfinite(numeric_price):
        raise ValueError("Invalid Binance Spot last-trade price")
    return value


def live_price() -> dict[str, object]:
    """Return a throttled Binance ticker, marking the last good value stale on failure."""

    global _cached, _last_attempt, _last_failed
    with _lock:
        now = time.monotonic()
        if not _last_attempt or now - _last_attempt >= CACHE_SECONDS:
            _last_attempt = now
            try:
                price = _fetch_binance_price()
                _last_failed = False
                _cached = {
                    "price": price,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "received_monotonic": time.monotonic(),
                }
            except Exception:  # the isolated display endpoint fails closed
                _last_failed = True
                logger.warning("Binance Spot public ticker unavailable", exc_info=True)

        if _cached is None:
            return {
                "status": "UNAVAILABLE",
                "price": None,
                "fetched_at": None,
                "exchange": BINANCE_EXCHANGE_ID,
                "source": BINANCE_TICKER_SOURCE,
                "display_only": True,
            }

        age = time.monotonic() - float(_cached["received_monotonic"])
        return {
            "status": "STALE" if _last_failed or age >= STALE_SECONDS else "CURRENT",
            "price": _cached["price"],
            "fetched_at": _cached["fetched_at"],
            "exchange": BINANCE_EXCHANGE_ID,
            "source": BINANCE_TICKER_SOURCE,
            "display_only": True,
        }
