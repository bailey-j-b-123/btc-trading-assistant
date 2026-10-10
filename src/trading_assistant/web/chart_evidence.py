"""Chart evidence projection: BRAIN's existing Step 3/4 facts, plus candle shapes.

This module is READ-ONLY presentation. It never creates a new structural
claim, never changes a qualification, and never feeds planning or journaling.
Its only new detector is the descriptive candle-shape classifier, which is
explicitly labelled as non-decision evidence.

Timing contract (the core of "respect when BRAIN knew it"):

* Every item carries ``known_at``: the instant the backend could first have
  known it. Swings use ``confirmed_at`` (close of their right-window candle),
  pattern states use their own transition instant, candle shapes use the close
  of the candle, sweeps/breakouts/retests use their detection instant.
* Any item whose ``known_at`` is after ``as_of`` is dropped and counted in
  ``excluded_future_count``. Nothing is ever shown as known earlier than that.
* Swing labels (HH/HL/LH/LL) compare a swing only with EARLIER confirmed swings
  of the same kind, so a label never depends on candles that were not yet
  closed when the swing became known.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from trading_assistant.candle_shapes import (
    CandleShapeParameters,
    detect_candle_shapes,
)
from trading_assistant.candle_shapes.detector import CandleShapeEvent
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.swings import SwingKind, SwingPoint
from trading_assistant.pattern_liquidity.events import ChartPattern
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot

CHART_EVIDENCE_RULES_VERSION = "chart-evidence-v1"

#: Analysis window in closed candles. Equal to the chart's candle window so the
#: evidence on screen is evaluated over exactly the candles the user can see.
CHART_EVIDENCE_WINDOW = 500

PATTERN_LABELS = {
    "double_top": "Double top",
    "double_bottom": "Double bottom",
    "head_and_shoulders": "Head and shoulders",
    "inverse_head_and_shoulders": "Inverse head and shoulders",
}
PATTERN_SIDE = {
    "double_top": "top",
    "head_and_shoulders": "top",
    "double_bottom": "bottom",
    "inverse_head_and_shoulders": "bottom",
}

LIMITATIONS = [
    "Chart evidence is a read-only projection of Step 3/4 detectors over the candle window shown.",
    "Setup qualification and trade plans use the engine's own replay, which can differ in window.",
    "Candle shapes are descriptive: they never enter qualification, planning, or journaling.",
    "Labels show what the detectors recorded; they are not predictions or trade instructions.",
]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def swing_labels(swings: Sequence[SwingPoint]) -> dict[tuple[SwingKind, datetime], str]:
    """HH/LH for highs and HL/LL for lows, compared with the previous same-kind swing.

    The first confirmed swing of each kind has nothing earlier to compare with
    and is labelled ``SH``/``SL``. Equal extremes are ``EH``/``EL``.
    """

    labels: dict[tuple[SwingKind, datetime], str] = {}
    previous: dict[SwingKind, SwingPoint] = {}
    for swing in sorted(swings, key=lambda s: (s.timestamp, s.kind.value)):
        before = previous.get(swing.kind)
        if before is None:
            label = "SH" if swing.kind is SwingKind.HIGH else "SL"
        elif swing.kind is SwingKind.HIGH:
            label = "HH" if swing.price > before.price else "LH" if swing.price < before.price else "EH"
        else:
            label = "HL" if swing.price > before.price else "LL" if swing.price < before.price else "EL"
        labels[(swing.kind, swing.timestamp)] = label
        previous[swing.kind] = swing
    return labels


def _swing_item(swing: SwingPoint, label: str, interval: timedelta) -> dict[str, Any]:
    kind = "high" if swing.kind is SwingKind.HIGH else "low"
    return {
        "id": f"swing:{kind}:{swing.timestamp.isoformat()}",
        "evidence_kind": "swing",
        "kind": kind,
        "label": label,
        "time_ms": _ms(swing.timestamp),
        "time": _iso(swing.timestamp),
        "price": _dec(swing.price),
        "known_at": _iso(swing.confirmed_at),
        "confirmed_by": _iso(swing.confirmed_by_timestamp),
        "candle_interval_seconds": int(interval.total_seconds()),
        "left_window": swing.left_window,
        "right_window": swing.right_window,
        "tie_policy": swing.tie_policy.value,
    }


def _pattern_item(pattern: ChartPattern, latest_ids: set[str], interval: timedelta) -> dict[str, Any]:
    components = [
        {
            "time_ms": _ms(point.timestamp),
            "time": _iso(point.timestamp),
            "kind": "high" if point.kind is SwingKind.HIGH else "low",
            "price": _dec(point.price),
            "known_at": _iso(point.confirmed_at),
        }
        for point in pattern.components
    ]
    geometry = pattern.geometry
    return {
        "id": pattern.id,
        "evidence_kind": "pattern",
        "pattern_id": pattern.pattern_id,
        "type": pattern.type,
        "label": PATTERN_LABELS[pattern.type],
        "side": PATTERN_SIDE[pattern.type],
        "state": pattern.state,
        "is_latest_state": pattern.id in latest_ids,
        "known_at": _iso(pattern.known_at),
        "formed_at": _iso(pattern.formed_at),
        "confirmation_time": _iso(pattern.confirmation_timestamp),
        "neckline": _dec(pattern.neckline),
        "invalidation_level": _dec(pattern.invalidation_level),
        "components": components,
        "geometry": {
            "matching_extremes_difference_pct": _dec(geometry.matching_extremes_difference_pct),
            "depth_pct": _dec(geometry.depth_pct),
            "head_prominence_pct": _dec(geometry.head_prominence_pct),
            "neckline_difference_pct": _dec(geometry.neckline_difference_pct),
            "span_candles": geometry.span_candles,
        },
        "evidence_candle_count": len(pattern.evidence_candles),
        "candle_interval_seconds": int(interval.total_seconds()),
    }


def _shape_item(event: CandleShapeEvent, interval: timedelta) -> dict[str, Any]:
    candle = event.candle
    return {
        "id": event.id,
        "evidence_kind": "candle_shape",
        "kind": event.kind,
        "direction": event.direction,
        "time_ms": _ms(candle.timestamp),
        "time": _iso(candle.timestamp),
        "known_at": _iso(event.known_at),
        "open": _dec(candle.open),
        "high": _dec(candle.high),
        "low": _dec(candle.low),
        "close": _dec(candle.close),
        "body_ratio": _dec(event.body_ratio.quantize(Decimal("0.0001"))),
        "upper_wick_ratio": _dec(event.upper_wick_ratio.quantize(Decimal("0.0001"))),
        "lower_wick_ratio": _dec(event.lower_wick_ratio.quantize(Decimal("0.0001"))),
        "previous_time": None if event.previous_candle is None else _iso(event.previous_candle.timestamp),
        "previous_time_ms": None if event.previous_candle is None else _ms(event.previous_candle.timestamp),
        "candle_interval_seconds": int(interval.total_seconds()),
        "descriptive_only": True,
    }


def _event_item(event: Any, kind: str, interval: timedelta) -> dict[str, Any]:
    """Breakouts, failures, sweeps, retests, equal levels: common band projection."""

    base: dict[str, Any] = {
        "id": event.id,
        "evidence_kind": kind,
        "kind": kind,
        "known_at": _iso(event.known_at),
    }
    if kind == "breakout":
        base.update(
            {
                "direction": event.direction,
                "reference_type": event.reference.type,
                "reference_id": event.reference.id,
                "band_low": _dec(event.reference.band_low),
                "band_high": _dec(event.reference.band_high),
                "time_ms": _ms(event.candle.timestamp),
                "time": _iso(event.candle.timestamp),
                "breakout_close": _dec(event.breakout_close),
                "penetration_pct": _dec(event.penetration_pct),
                "confirmation_candles": len(event.confirmation_candles),
            }
        )
    elif kind == "failed_breakout":
        base.update(
            {
                "breakout_id": event.breakout.id,
                "direction": event.breakout.direction,
                "reference_type": event.breakout.reference.type,
                "band_low": _dec(event.breakout.reference.band_low),
                "band_high": _dec(event.breakout.reference.band_high),
                "time_ms": _ms(event.candle.timestamp),
                "time": _iso(event.candle.timestamp),
                "reentry_distance": _dec(event.reentry_distance),
                "elapsed_candles": event.elapsed_candles,
            }
        )
    elif kind == "sweep":
        base.update(
            {
                "direction": event.direction,
                "reference_type": event.reference.type,
                "reference_id": event.reference.id,
                "band_low": _dec(event.reference.band_low),
                "band_high": _dec(event.reference.band_high),
                "time_ms": _ms(event.candle.timestamp),
                "time": _iso(event.candle.timestamp),
                "extreme": _dec(event.extreme),
                "reclaim_close": _dec(event.reclaim_close),
            }
        )
    elif kind == "retest":
        base.update(
            {
                "state": event.state,
                "breakout_id": event.breakout.id,
                "direction": event.breakout.direction,
                "band_low": _dec(event.breakout.reference.band_low),
                "band_high": _dec(event.breakout.reference.band_high),
                "time_ms": _ms(event.candle.timestamp),
                "time": _iso(event.candle.timestamp),
                "distance_from_band": _dec(event.distance_from_band),
                "elapsed_candles": event.elapsed_candles,
            }
        )
    elif kind == "equal_level":
        base.update(
            {
                "type": event.type,
                "level": _dec(event.center),
                "band_low": _dec(event.band_low),
                "band_high": _dec(event.band_high),
                "member_count": event.member_count,
                "members": [
                    {"time_ms": _ms(m.timestamp), "time": _iso(m.timestamp), "price": _dec(m.price), "known_at": _iso(m.confirmed_at)}
                    for m in event.members
                ],
                "first_known_at": _iso(event.first_known_at),
            }
        )
    base["candle_interval_seconds"] = int(interval.total_seconds())
    return base


def _zone_item(zone: Any, index: int, interval: timedelta) -> dict[str, Any]:
    return {
        "id": f"zone:{zone.role.value}:{index}:{_dec(zone.band_low)}:{_dec(zone.band_high)}",
        "evidence_kind": "zone",
        "kind": "zone",
        "role": zone.role.value,
        "band_low": _dec(zone.band_low),
        "band_high": _dec(zone.band_high),
        "center": _dec(zone.center),
        "touch_count": zone.touch_count,
        "high_source_count": zone.high_source_count,
        "low_source_count": zone.low_source_count,
        "first_observed": _iso(zone.first_observed_timestamp),
        "last_tested": _iso(zone.last_tested_timestamp),
        "source_swing_times": [_iso(t) for t in zone.source_swing_timestamps],
        "known_at": _iso(zone.as_of),
        "candle_interval_seconds": int(interval.total_seconds()),
    }


def build_chart_evidence(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    snapshot: PatternLiquiditySnapshot,
    candles: Sequence[Candle],
    interval: timedelta,
    shape_parameters: CandleShapeParameters | None = None,
) -> dict[str, Any]:
    """Assemble the chart evidence payload for one timeframe at one instant."""

    excluded = 0

    def keep(item_known_at: datetime | None) -> bool:
        nonlocal excluded
        if item_known_at is None or item_known_at <= as_of:
            return True
        excluded += 1
        return False

    structure = snapshot.structure
    swings = [s for s in structure.confirmed_swings if keep(s.confirmed_at)]
    labels = swing_labels(swings)
    swing_items = [
        _swing_item(s, labels[(s.kind, s.timestamp)], interval) for s in swings
    ]

    # Keep only pattern records that were known by as_of; then mark, per
    # pattern_id, the record with the latest known_at as the current state.
    known_patterns = [p for p in snapshot.chart_patterns if keep(p.known_at)]
    latest_by_pattern: dict[str, ChartPattern] = {}
    for pattern in sorted(known_patterns, key=lambda p: (p.known_at, p.id)):
        latest_by_pattern[pattern.pattern_id] = pattern
    latest_ids = {p.id for p in latest_by_pattern.values()}
    pattern_items = [_pattern_item(p, latest_ids, interval) for p in known_patterns]

    breakouts = [_event_item(e, "breakout", interval) for e in snapshot.breakouts if keep(e.known_at)]
    failed = [_event_item(e, "failed_breakout", interval) for e in snapshot.failed_breakouts if keep(e.known_at)]
    sweeps = [_event_item(e, "sweep", interval) for e in snapshot.sweeps if keep(e.known_at)]
    retests = [_event_item(e, "retest", interval) for e in snapshot.retests if keep(e.known_at)]
    equal = [_event_item(e, "equal_level", interval) for e in snapshot.equal_levels if keep(e.known_at)]
    zones = [_zone_item(z, i, interval) for i, z in enumerate(structure.levels.zones)]
    detected_range = structure.detected_range
    range_item = None
    if detected_range is not None and detected_range.active and keep(detected_range.current_timestamp):
        range_item = {
            "id": f"range:{_iso(detected_range.start_timestamp)}:{_iso(detected_range.current_timestamp)}",
            "evidence_kind": "range",
            "kind": "range",
            "range_high": _dec(detected_range.range_high),
            "range_low": _dec(detected_range.range_low),
            "start_time": _iso(detected_range.start_timestamp),
            "end_time": _iso(detected_range.end_timestamp),
            "current_time": _iso(detected_range.current_timestamp),
            "start_time_ms": _ms(detected_range.start_timestamp),
            "known_at": _iso(detected_range.current_timestamp),
            "touch_count": detected_range.touch_count,
            "active": detected_range.active,
            "width_pct": _dec(detected_range.width_pct),
        }

    shape_parameters = shape_parameters or CandleShapeParameters()
    window = [c for c in candles if c.timestamp + interval <= as_of][-CHART_EVIDENCE_WINDOW:]
    shape_events = detect_candle_shapes(
        window, interval=interval, as_of=as_of, parameters=shape_parameters
    )
    shapes = [_shape_item(e, interval) for e in shape_events if keep(e.known_at)]

    first_candle = window[0].timestamp if window else None
    last_candle = window[-1].timestamp if window else None
    trend = structure.trend
    return {
        "available": True,
        "rules_version": CHART_EVIDENCE_RULES_VERSION,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": _iso(as_of),
        "interval_seconds": int(interval.total_seconds()),
        "status": snapshot.status,
        "reasons": list(snapshot.reasons),
        "evidence_window": {
            "candle_limit": CHART_EVIDENCE_WINDOW,
            "candle_count": len(window),
            "first_candle": _iso(first_candle),
            "last_candle": _iso(last_candle),
            "first_candle_ms": None if first_candle is None else _ms(first_candle),
            "last_candle_ms": None if last_candle is None else _ms(last_candle),
            "structure_candle_count": structure.candle_count,
        },
        "trend": {
            "direction": trend.direction.value,
            "reason": trend.reason.value,
            "sufficient": trend.sufficient,
            "higher_highs": trend.higher_highs,
            "higher_lows": trend.higher_lows,
            "lower_highs": trend.lower_highs,
            "lower_lows": trend.lower_lows,
        },
        "swings": swing_items,
        "patterns": pattern_items,
        "breakouts": breakouts,
        "failed_breakouts": failed,
        "sweeps": sweeps,
        "retests": retests,
        "equal_levels": equal,
        "zones": zones,
        "range": range_item,
        "candle_shapes": shapes,
        "shape_rules": shape_parameters.fingerprint_payload(),
        "excluded_future_count": excluded,
        "limitations": list(LIMITATIONS),
        "counts": {
            "swings": len(swing_items),
            "patterns": len(pattern_items),
            "breakouts": len(breakouts),
            "failed_breakouts": len(failed),
            "sweeps": len(sweeps),
            "retests": len(retests),
            "equal_levels": len(equal),
            "zones": len(zones),
            "candle_shapes": len(shapes),
        },
    }


def unavailable_chart_evidence(*, exchange, symbol, timeframe, as_of, reason) -> dict[str, Any]:
    """Explicit empty state: no items, and the reason is visible to the user."""

    return {
        "available": False,
        "rules_version": CHART_EVIDENCE_RULES_VERSION,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": _iso(as_of),
        "reason": reason,
        "swings": [],
        "patterns": [],
        "breakouts": [],
        "failed_breakouts": [],
        "sweeps": [],
        "retests": [],
        "equal_levels": [],
        "zones": [],
        "range": None,
        "candle_shapes": [],
        "excluded_future_count": 0,
        "limitations": list(LIMITATIONS),
        "counts": {},
    }


__all__ = [
    "CHART_EVIDENCE_RULES_VERSION",
    "CHART_EVIDENCE_WINDOW",
    "build_chart_evidence",
    "swing_labels",
    "unavailable_chart_evidence",
]

