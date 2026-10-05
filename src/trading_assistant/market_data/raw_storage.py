"""Append-only storage for raw CCXT OHLCV pages."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_assistant.market_data.errors import RawDataWriteError
from trading_assistant.market_data.timeframes import require_utc_datetime

_SAFE_FRAGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_fragment(value: str) -> str:
    fragment = _SAFE_FRAGMENT.sub("-", value).strip("-._")
    return fragment or "unknown"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported raw response value type: {type(value).__name__}")


class RawResponseStore:
    """Write each source response to a new traceable JSON file; never overwrite."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def write_page(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        since_ms: int,
        limit: int,
        response: Any,
        http_response: Any = None,
        retrieved_at: datetime | None = None,
    ) -> Path:
        retrieval_time = require_utc_datetime(
            retrieved_at if retrieved_at is not None else datetime.now(UTC),
            field_name="retrieved_at",
        )
        retrieval_stamp = retrieval_time.strftime("%Y%m%dT%H%M%S.%fZ")
        filename = (
            f"{_safe_fragment(exchange)}_{_safe_fragment(symbol)}_{_safe_fragment(timeframe)}_"
            f"{retrieval_stamp}_since-{since_ms}_limit-{limit}_{uuid4().hex}.json"
        )
        path = self.root / _safe_fragment(exchange) / _safe_fragment(symbol) / _safe_fragment(timeframe) / filename
        payload = {
            "format_version": 1,
            "source": "ccxt.fetch_ohlcv",
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "retrieved_at_utc": retrieval_time.isoformat().replace("+00:00", "Z"),
            "request": {"since_ms": since_ms, "limit": limit},
            "ccxt_ohlcv_response": response,
            "http_response": http_response,
        }

        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                default=_json_default,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(serialized)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise RawDataWriteError(
                f"Could not preserve raw {exchange} response at {path} ({type(exc).__name__})"
            ) from exc
        return path
