"""Exact Decimal boundary-line arithmetic for chart-geometry-v1.

A boundary line is always anchored on **two actual confirmed swing pivots**: its
two exact points (open time and swing price) are stored verbatim, and every
value the package reports for that line is derived from those two points by
linear interpolation in whole candle steps. No line is ever fitted by
regression, smoothed, extended from a single pivot, or copied from another
detector's level: this package deliberately never asserts that one of its lines
is the same thing as a Step 3 support/resistance zone, a Step 4 neckline, or any
other independently derived level.

Evaluation rules
----------------
* ``value_at(when)`` requires ``when`` to sit exactly on the candle grid of the
  line's first anchor; anything else raises ``ValueError`` rather than being
  rounded onto the grid.
* At the anchors the stored exact prices are returned unrounded. Between, before,
  and after them the value is
  ``start_price + (end_price - start_price) * steps / span_candles`` computed
  with :func:`trading_assistant.market_structure.numeric.divide` (28-digit
  context, ROUND_HALF_EVEN) and rounded once with the documented Step 3 quantum.
  Extrapolation past an anchor is exact arithmetic on the frozen line and is
  always labelled as such by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from trading_assistant.market_structure.numeric import (
    divide,
    mean,
    percentage_of,
    quantize_derived,
)

BoundaryRole = Literal["upper", "lower"]


def candle_offset(start: datetime, when: datetime, interval: timedelta) -> int:
    """Return the exact whole-candle offset of ``when`` from ``start``.

    Negative offsets (times before ``start``) are exact and allowed. A time that
    does not sit on the candle grid raises ``ValueError``; it is never rounded.
    """

    delta = when - start
    if delta % interval != timedelta(0):
        raise ValueError(
            "boundary lines are only evaluated on whole candles of the detection timeframe"
        )
    return int(delta // interval)


@dataclass(frozen=True, slots=True)
class BoundaryLine:
    """One frozen boundary line with its two exact anchor points."""

    role: BoundaryRole
    start_time: datetime
    start_price: Decimal
    end_time: datetime
    end_price: Decimal
    interval_seconds: int
    span_candles: int
    slope_per_candle: Decimal
    travel_pct: Decimal

    def value_at(self, when: datetime) -> Decimal:
        """Return the exact line value at a grid-aligned candle open time."""

        steps = candle_offset(self.start_time, when, timedelta(seconds=self.interval_seconds))
        if steps == 0:
            return self.start_price
        if steps == self.span_candles:
            return self.end_price
        delta = self.end_price - self.start_price
        return quantize_derived(
            self.start_price + divide(delta * Decimal(steps), Decimal(self.span_candles))
        )

    def offset_candles(self, when: datetime) -> int:
        """Return ``when`` as a whole-candle offset from this line's first anchor."""

        return candle_offset(self.start_time, when, timedelta(seconds=self.interval_seconds))


def boundary_line(
    *,
    role: BoundaryRole,
    start_time: datetime,
    start_price: Decimal,
    end_time: datetime,
    end_price: Decimal,
    interval: timedelta,
) -> BoundaryLine:
    """Build a frozen boundary line from two exact anchor points.

    Raises ``ValueError`` for a non-positive or non-grid span and for a
    non-positive price, because every percent in this package divides by a price.
    """

    if interval <= timedelta(0):
        raise ValueError("interval must be a positive duration")
    span = candle_offset(start_time, end_time, interval)
    if span <= 0:
        raise ValueError("a boundary line needs two distinct, ordered anchor points")
    if start_price <= 0 or end_price <= 0:
        raise ValueError("boundary anchor prices must be positive")
    return BoundaryLine(
        role=role,
        start_time=start_time,
        start_price=start_price,
        end_time=end_time,
        end_price=end_price,
        interval_seconds=int(interval.total_seconds()),
        span_candles=span,
        slope_per_candle=divide(end_price - start_price, Decimal(span)),
        travel_pct=percentage_of(end_price - start_price, mean([start_price, end_price])),
    )


@dataclass(frozen=True, slots=True)
class Convergence:
    """How two boundary lines relate, in exact candle units."""

    parallel: bool
    converging: bool
    #: Candle offset of the meeting point from the upper line's first anchor.
    apex_offset_candles: Decimal | None
    #: Whether the apex lies strictly after ``last_component_time``.
    apex_after_last_component: bool


def convergence(
    upper: BoundaryLine,
    lower: BoundaryLine,
    *,
    last_component_time: datetime,
) -> Convergence:
    """Return the exact relationship between the two frozen boundary lines.

    ``converging`` means the upper line rises more slowly (or falls more quickly)
    than the lower one, so the vertical gap narrows going forward and the two
    lines meet at a finite apex. ``parallel`` means identical slopes and no apex.
    Everything else (an apex behind the formation, i.e. a widening gap) is
    reported as neither parallel nor converging.
    """

    if lower.interval_seconds != upper.interval_seconds:
        raise ValueError("both boundary lines must share one timeframe interval")
    lower_origin_offset = Decimal(upper.offset_candles(lower.start_time))
    slope_delta = upper.slope_per_candle - lower.slope_per_candle
    if slope_delta == 0:
        return Convergence(
            parallel=True,
            converging=False,
            apex_offset_candles=None,
            apex_after_last_component=False,
        )
    # upper(n) = u0 + su*n ; lower(n) = l0 + sl*(n - du) ; solve upper(n) == lower(n)
    numerator = lower.start_price - upper.start_price - lower.slope_per_candle * lower_origin_offset
    apex = divide(numerator, slope_delta)
    last_offset = Decimal(upper.offset_candles(last_component_time))
    return Convergence(
        parallel=False,
        converging=slope_delta < 0 and apex > last_offset,
        apex_offset_candles=quantize_derived(apex),
        apex_after_last_component=apex > last_offset,
    )


def gap_at(upper: BoundaryLine, lower: BoundaryLine, when: datetime) -> Decimal:
    """Return the exact vertical gap between the two lines at a candle time."""

    return upper.value_at(when) - lower.value_at(when)


def gap_pct_at(upper: BoundaryLine, lower: BoundaryLine, when: datetime) -> Decimal:
    """Return the gap as a percent of the two lines' mid price at that time."""

    upper_value = upper.value_at(when)
    lower_value = lower.value_at(when)
    return percentage_of(upper_value - lower_value, mean([upper_value, lower_value]))
