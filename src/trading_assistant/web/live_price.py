"""Isolated, read-only public quote for the visual display; no DB or engine imports.

A quote is NOT a closed candle or an exchange timestamp. The fetched_at clock
records when our server received a valid public ticker response. Failure never
substitutes a quote, and callers must not route this payload into analysis.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from datetime import UTC, datetime
from urllib.request import urlopen

logger = logging.getLogger(__name__)
KRAKEN_TICKER_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSDT"
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


def live_price() -> dict[str, object]:
    """Throttled independently of the dashboard; last good quote is stale, not live."""
    global _cached, _last_attempt, _last_failed
    with _lock:
        now = time.monotonic()
        if now - _last_attempt >= CACHE_SECONDS or not _last_attempt:
            _last_attempt = now
            try:
                price = _fetch_price()
                _last_failed = False
                _cached = {
                    "price": price,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "received_monotonic": time.monotonic(),
                }
            except Exception:  # isolated display endpoint must fail closed
                _last_failed = True
                logger.warning("Public Kraken ticker unavailable", exc_info=True)
        if _cached is None:
            return {
                "status": "UNAVAILABLE",
                "price": None,
                "fetched_at": None,
                "source": "Kraken public ticker",
                "display_only": True,
            }
        age = time.monotonic() - float(_cached["received_monotonic"])
        return {
            "status": "STALE" if _last_failed or age >= STALE_SECONDS else "CURRENT",
            "price": _cached["price"],
            "fetched_at": _cached["fetched_at"],
            "source": "Kraken public ticker",
            "display_only": True,
        }
