"""Canonical, scoped identities for chart-geometry-v1.

Identities are SHA-256 digests over an explicit canonical payload — never random
UUIDs, never wall-clock time, never a database sequence. Three distinct payload
prefixes keep the three identity spaces apart:

``chart-geometry-v1:formation:``
    A formation id. It scopes the geometry to one market
    (``exchange``/``symbol``), one ``timeframe``, one rules version, one exact
    parameter fingerprint, and the formation's **actual components**: each
    component's role, pivot kind, original pivot time, exact price, and actual
    confirmation instant. The same shape on another symbol, another timeframe,
    or under another parameter set is therefore a different formation.
``chart-geometry-v1:event:``
    A lifecycle event id. It is derived from the formation id plus the event's
    own state, causal ``known_at``, real anchor candle, observed direction, and
    explicit reason, so an event id can never collide with its formation id or
    with a sibling event of the same formation.
``chart-geometry-v1:geometry:``
    A digest of the frozen geometry (both boundary lines' exact anchor points,
    gaps, convergence, and every measured metric). It is identical across all
    events of one formation, which is what makes "frozen at first detection"
    verifiable from the record alone.

Decimal values are canonicalized by value, not by spelling, so ``Decimal("1.50")``
and ``Decimal("1.5")`` describe the same price and produce the same id.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from trading_assistant.chart_geometry.families import ChartGeometryFamily
from trading_assistant.chart_geometry.lines import BoundaryLine
from trading_assistant.chart_geometry.models import (
    FormationGeometry,
    GeometryComponent,
)
from trading_assistant.chart_geometry.parameters import (
    CHART_GEOMETRY_RULES_VERSION,
    canonical_decimal,
)
from trading_assistant.market_data.types import Candle

_FORMATION_PREFIX = CHART_GEOMETRY_RULES_VERSION + ":formation:"
_EVENT_PREFIX = CHART_GEOMETRY_RULES_VERSION + ":event:"
_GEOMETRY_PREFIX = CHART_GEOMETRY_RULES_VERSION + ":geometry:"


def canonical_instant(value: datetime) -> str:
    """Return a UTC ISO-8601 ``...Z`` string for one aware datetime."""

    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> Any:
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, datetime):
        return canonical_instant(value)
    if isinstance(value, ChartGeometryFamily):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError(f"unsupported identity payload type: {type(value).__name__}")


def _digest(prefix: str, payload: Any) -> str:
    encoded = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"))
    return sha256((prefix + encoded).encode()).hexdigest()


def component_identity_payload(component: GeometryComponent) -> dict[str, Any]:
    """Return the canonical identity payload of one actual component."""

    return {
        "role": component.role,
        "pivot_kind": component.pivot_kind.value,
        "timestamp": component.timestamp,
        "price": component.price,
        "confirmed_at": component.confirmed_at,
    }


def formation_identity(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    family: ChartGeometryFamily,
    parameters_fingerprint: str,
    components: tuple[GeometryComponent, ...],
) -> str:
    """Return the immutable, scoped formation id for one exact geometry."""

    return _digest(
        _FORMATION_PREFIX,
        {
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "family": family,
            "parameters_fingerprint": parameters_fingerprint,
            "components": [component_identity_payload(component) for component in components],
        },
    )


def _boundary_payload(line: BoundaryLine) -> dict[str, Any]:
    return {
        "role": line.role,
        "start_time": line.start_time,
        "start_price": line.start_price,
        "end_time": line.end_time,
        "end_price": line.end_price,
        "interval_seconds": line.interval_seconds,
        "span_candles": line.span_candles,
        "slope_per_candle": line.slope_per_candle,
        "travel_pct": line.travel_pct,
    }


def geometry_digest(geometry: FormationGeometry) -> str:
    """Return the digest of one frozen geometry (stable across its events)."""

    return _digest(
        _GEOMETRY_PREFIX,
        {
            "upper": _boundary_payload(geometry.upper),
            "lower": _boundary_payload(geometry.lower),
            "first_component_time": geometry.first_component_time,
            "last_component_time": geometry.last_component_time,
            "span_candles": geometry.span_candles,
            "region_start_time": geometry.region_start_time,
            "region_end_time": geometry.region_end_time,
            "gap_at_region_start": geometry.gap_at_region_start,
            "gap_at_region_end": geometry.gap_at_region_end,
            "gap_pct_at_region_start": geometry.gap_pct_at_region_start,
            "gap_pct_at_region_end": geometry.gap_pct_at_region_end,
            "min_gap_pct": geometry.min_gap_pct,
            "convergence": {
                "parallel": geometry.convergence.parallel,
                "converging": geometry.convergence.converging,
                "apex_offset_candles": geometry.convergence.apex_offset_candles,
                "apex_after_last_component": geometry.convergence.apex_after_last_component,
            },
            "metrics": [
                {"name": m.name, "value": m.value, "unit": m.unit, "basis": m.basis}
                for m in geometry.metrics
            ],
        },
    )


def _candle_payload(candle: Candle) -> dict[str, Any]:
    return {
        "exchange": candle.exchange,
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "timestamp": candle.timestamp,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def event_identity(
    *,
    formation_id: str,
    state: str,
    known_at: datetime,
    anchor_candle: Candle,
    reason: str,
    observed_direction: str | None,
) -> str:
    """Return the immutable id of one lifecycle event."""

    return _digest(
        _EVENT_PREFIX,
        {
            "formation_id": formation_id,
            "state": state,
            "known_at": known_at,
            "anchor_candle": _candle_payload(anchor_candle),
            "reason": reason,
            "observed_direction": observed_direction,
        },
    )
