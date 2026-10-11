"""Deterministic candle-shape events over CLOSED candles only.

Definitions (v1, see :mod:`parameters` for thresholds):

``strong_bullish_body`` / ``strong_bearish_body``
    close > open (resp. close < open) and body ratio >= strong_body_min_ratio.
``lower_wick_rejection``
    lower wick >= rejection_wick_min_ratio x range AND
    lower wick >= rejection_wick_to_body_min x body. Describes a long lower
    wick (prices were pushed down and closed back inside the range). Not a
    buy signal by itself.
``upper_wick_rejection``
    Mirror image using the upper wick. Not a sell signal by itself.
``indecision``
    body ratio <= indecision_max_body_ratio.
``bullish_engulfing`` / ``bearish_engulfing``
    Two consecutive closed candles (no time gap). The previous candle is bearish
    (resp. bullish) with a non-zero body; the current candle is bullish (resp.
    bearish); the current body strictly contains the previous body
    (bullish: open <= prev.close and close >= prev.open; bearish: open >=
    prev.close and close <= prev.open); and the current body is strictly larger
    than the previous body. Shadows are ignored.

Every event is stamped ``known_at = candle open + interval`` (the instant the
candle closed) and is emitted only when that instant is <= ``as_of``. The
forming candle is never inspected because it does not close before ``as_of``.
Candles with zero range are never classified.

These events are descriptive evidence for the chart. They are not inputs to
setup qualification, planning, journaling or outcome evaluation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from trading_assistant.candle_shapes.parameters import CandleShapeParameters
from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle

CandleShapeKind = Literal[
    "strong_bullish_body",
    "strong_bearish_body",
    "lower_wick_rejection",
    "upper_wick_rejection",
    "indecision",
    "bullish_engulfing",
    "bearish_engulfing",
]

_ZERO = Decimal(0)

#: ``neutral`` is used only for indecision: it carries no bullish/bearish lean.
ShapeSide = Literal["bullish", "bearish", "neutral"]


def candle_direction(candle: Candle) -> Literal["bullish", "bearish", "flat"]:
    """Ordinary candle colour from exact open/close: close > open is bullish."""

    if candle.close > candle.open:
        return "bullish"
    if candle.close < candle.open:
        return "bearish"
    return "flat"


@dataclass(frozen=True, slots=True)
class CandleShapeEvent:
    id: str
    kind: CandleShapeKind
    direction: ShapeSide
    candle: Candle
    known_at: datetime
    body_ratio: Decimal
    upper_wick_ratio: Decimal
    lower_wick_ratio: Decimal
    previous_candle: Candle | None
    parameters: CandleShapeParameters


def _identity(kind: str, candle: Candle, interval: timedelta) -> str:
    payload = "|".join(
        [
            "candle-shapes-v1",
            kind,
            candle.exchange,
            candle.symbol,
            candle.timeframe,
            candle.timestamp.isoformat(),
            str(int(interval.total_seconds())),
        ]
    )
    return sha256(payload.encode()).hexdigest()


def _geometry(candle: Candle) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Return (range, body, upper_wick, lower_wick) in exact decimal price units."""

    body_top = max(candle.open, candle.close)
    body_bottom = min(candle.open, candle.close)
    candle_range = candle.high - candle.low
    body = body_top - body_bottom
    upper = candle.high - body_top
    lower = body_bottom - candle.low
    return candle_range, body, upper, lower


def single_candle_shapes(
    candle: Candle,
    *,
    interval: timedelta,
    parameters: CandleShapeParameters,
) -> list[CandleShapeEvent]:
    """Every single-candle shape that the closed candle satisfies."""

    candle_range, body, upper, lower = _geometry(candle)
    if candle_range <= _ZERO:
        return []
    body_ratio = body / candle_range
    upper_ratio = upper / candle_range
    lower_ratio = lower / candle_range
    direction = candle_direction(candle)
    known_at = candle.timestamp + interval
    events: list[CandleShapeEvent] = []

    def add(kind: CandleShapeKind, side: ShapeSide) -> None:
        events.append(
            CandleShapeEvent(
                id=_identity(kind, candle, interval),
                kind=kind,
                direction=side,
                candle=candle,
                known_at=known_at,
                body_ratio=body_ratio,
                upper_wick_ratio=upper_ratio,
                lower_wick_ratio=lower_ratio,
                previous_candle=None,
                parameters=parameters,
            )
        )

    if direction == "bullish" and body_ratio >= parameters.strong_body_min_ratio:
        add("strong_bullish_body", "bullish")
    if direction == "bearish" and body_ratio >= parameters.strong_body_min_ratio:
        add("strong_bearish_body", "bearish")
    if (
        lower_ratio >= parameters.rejection_wick_min_ratio
        and lower >= parameters.rejection_wick_to_body_min * body
    ):
        add("lower_wick_rejection", "bullish")
    if (
        upper_ratio >= parameters.rejection_wick_min_ratio
        and upper >= parameters.rejection_wick_to_body_min * body
    ):
        add("upper_wick_rejection", "bearish")
    if body_ratio <= parameters.indecision_max_body_ratio:
        add("indecision", "neutral")
    return events


def engulfing_shape(
    previous: Candle,
    current: Candle,
    *,
    interval: timedelta,
    parameters: CandleShapeParameters,
) -> CandleShapeEvent | None:
    """Two-candle engulfing per the module definition; None if not satisfied."""

    if current.timestamp - previous.timestamp != interval:
        return None  # never bridge a gap
    prev_direction = candle_direction(previous)
    cur_direction = candle_direction(current)
    prev_body = abs(previous.close - previous.open)
    cur_body = abs(current.close - current.open)
    if prev_body <= _ZERO or cur_body <= prev_body:
        return None
    kind: CandleShapeKind | None = None
    if prev_direction == "bearish" and cur_direction == "bullish":
        if current.open <= previous.close and current.close >= previous.open:
            kind = "bullish_engulfing"
    elif prev_direction == "bullish" and cur_direction == "bearish":
        if current.open >= previous.close and current.close <= previous.open:
            kind = "bearish_engulfing"
    if kind is None:
        return None
    candle_range, body, upper, lower = _geometry(current)
    if candle_range <= _ZERO:
        return None
    return CandleShapeEvent(
        id=_identity(kind, current, interval),
        kind=kind,
        direction="bullish" if kind == "bullish_engulfing" else "bearish",
        candle=current,
        known_at=current.timestamp + interval,
        body_ratio=body / candle_range,
        upper_wick_ratio=upper / candle_range,
        lower_wick_ratio=lower / candle_range,
        previous_candle=previous,
        parameters=parameters,
    )


def detect_candle_shapes(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: CandleShapeParameters | None = None,
) -> tuple[CandleShapeEvent, ...]:
    """Chronological shape events for candles whose close is <= ``as_of``.

    Input may be unsorted; it is ordered by timestamp. Duplicate timestamps for
    the same instrument are rejected rather than silently collapsed.
    """

    resolved = parameters if parameters is not None else CandleShapeParameters()
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = sorted(candles, key=lambda c: c.timestamp)
    for left, right in zip(ordered, ordered[1:]):
        if left.timestamp == right.timestamp:
            raise ValueError("duplicate candle timestamp in candle-shape input")
    closed = [c for c in ordered if c.timestamp + interval <= as_of_utc]
    events: list[CandleShapeEvent] = []
    for index, candle in enumerate(closed):
        events.extend(single_candle_shapes(candle, interval=interval, parameters=resolved))
        if index > 0:
            pair = engulfing_shape(
                closed[index - 1], candle, interval=interval, parameters=resolved
            )
            if pair is not None:
                events.append(pair)
    return tuple(sorted(events, key=lambda e: (e.known_at, e.kind)))
