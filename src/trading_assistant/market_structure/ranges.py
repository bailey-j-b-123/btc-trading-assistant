"""Deterministic consolidation/range detection from confirmed swings and closes.

At most one range candidate is evaluated per analysis: the boundaries are the
highest confirmed swing high and the lowest confirmed swing low inside a fixed
candle lookback window. The candidate is only reported when it satisfies every
documented rule (minimum qualifying touches per side, bounded width, minimum
span, non-inverted bounds). A sideways-looking sequence that fails any rule is
rejected with an explicit reason instead of being labelled a range.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import (
    candle_span_count,
    candles_closed_by,
    candles_since,
    require_positive_interval,
)
from trading_assistant.market_structure.numeric import (
    as_decimal,
    is_within_tolerance,
    percentage_width,
    quantize_derived,
    require_int,
    tolerance_band,
)
from trading_assistant.market_structure.swings import SwingKind, SwingPoint


class RangeRejectionReason(StrEnum):
    """Why no consolidation range was detected."""

    NO_CANDLES = "no_candles"
    INSUFFICIENT_SWINGS = "insufficient_swings"
    INVERTED_BOUNDS = "inverted_bounds"
    INSUFFICIENT_TOUCHES = "insufficient_touches"
    SPAN_TOO_SHORT = "span_too_short"
    WIDTH_EXCEEDS_MAX = "width_exceeds_max"


@dataclass(frozen=True, slots=True)
class RangeParameters:
    """Documented, configurable rules for range detection."""

    lookback_candles: int = 120
    min_touches_per_side: int = 2
    tolerance_pct: Decimal = Decimal(1)
    max_width_pct: Decimal = Decimal(10)
    min_span_candles: int = 20
    active_max_candles_since_last_touch: int = 40

    def __post_init__(self) -> None:
        for name, minimum in (
            ("lookback_candles", 2),
            ("min_touches_per_side", 1),
            ("min_span_candles", 1),
            ("active_max_candles_since_last_touch", 0),
        ):
            require_int(getattr(self, name), name=name, minimum=minimum)
        tolerance = as_decimal(self.tolerance_pct, name="tolerance_pct")
        max_width = as_decimal(self.max_width_pct, name="max_width_pct")
        if not Decimal(0) < tolerance < Decimal(100):
            raise ValueError("tolerance_pct must be greater than 0 and less than 100")
        if not Decimal(0) < max_width <= Decimal(100):
            raise ValueError("max_width_pct must be greater than 0 and at most 100")
        object.__setattr__(self, "tolerance_pct", tolerance)
        object.__setattr__(self, "max_width_pct", max_width)


@dataclass(frozen=True, slots=True)
class RangeDetection:
    """A detected consolidation range with measurable boundary evidence."""

    range_high: Decimal
    range_low: Decimal
    width: Decimal
    width_pct: Decimal
    start_timestamp: datetime
    end_timestamp: datetime
    current_timestamp: datetime
    upper_touch_timestamps: tuple[datetime, ...]
    lower_touch_timestamps: tuple[datetime, ...]
    upper_touch_count: int
    lower_touch_count: int
    touch_count: int
    candles_since_last_touch: int
    latest_close: Decimal
    close_within_bounds: bool
    closes_outside_band_count: int
    band_broken: bool
    active: bool
    parameters: RangeParameters
    as_of: datetime

    @property
    def is_active(self) -> bool:
        """Alias for :attr:`active`."""

        return self.active


@dataclass(frozen=True, slots=True)
class RangeDetectionResult:
    """The detected range (if any), the rejection reason, and rule diagnostics."""

    range: RangeDetection | None
    reason: RangeRejectionReason | None
    parameters: RangeParameters
    as_of: datetime
    candle_count: int
    window_start_timestamp: datetime | None
    window_end_timestamp: datetime | None
    swing_high_count: int
    swing_low_count: int
    upper_touch_count: int
    lower_touch_count: int
    width_pct: Decimal | None
    span_candles: int | None

    @property
    def sufficient(self) -> bool:
        """Whether the lookback window held enough swings for a range candidate."""

        return (
            self.swing_high_count >= self.parameters.min_touches_per_side
            and self.swing_low_count >= self.parameters.min_touches_per_side
        )


def detect_range(
    candles: Iterable[Candle],
    swings: Iterable[SwingPoint],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: RangeParameters | None = None,
) -> RangeDetectionResult:
    """Evaluate the single documented range candidate for the lookback window."""

    resolved = parameters if parameters is not None else RangeParameters()
    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = candles_closed_by(candles, interval=resolved_interval, as_of=as_of_utc)
    confirmed_swings = tuple(swing for swing in swings if swing.confirmed_at <= as_of_utc)

    if not ordered:
        return RangeDetectionResult(
            range=None,
            reason=RangeRejectionReason.NO_CANDLES,
            parameters=resolved,
            as_of=as_of_utc,
            candle_count=0,
            window_start_timestamp=None,
            window_end_timestamp=None,
            swing_high_count=0,
            swing_low_count=0,
            upper_touch_count=0,
            lower_touch_count=0,
            width_pct=None,
            span_candles=None,
        )

    current_timestamp = ordered[-1].timestamp
    window_start = current_timestamp - (resolved.lookback_candles - 1) * resolved_interval
    window_candles = candles_since(ordered, start=window_start)
    window_swings = tuple(swing for swing in confirmed_swings if swing.timestamp >= window_start)
    window_highs = tuple(swing for swing in window_swings if swing.kind is SwingKind.HIGH)
    window_lows = tuple(swing for swing in window_swings if swing.kind is SwingKind.LOW)

    def rejected(
        reason: RangeRejectionReason,
        *,
        upper_touch_count: int = 0,
        lower_touch_count: int = 0,
        width_pct: Decimal | None = None,
        span_candles: int | None = None,
    ) -> RangeDetectionResult:
        return RangeDetectionResult(
            range=None,
            reason=reason,
            parameters=resolved,
            as_of=as_of_utc,
            candle_count=len(window_candles),
            window_start_timestamp=window_candles[0].timestamp,
            window_end_timestamp=current_timestamp,
            swing_high_count=len(window_highs),
            swing_low_count=len(window_lows),
            upper_touch_count=upper_touch_count,
            lower_touch_count=lower_touch_count,
            width_pct=width_pct,
            span_candles=span_candles,
        )

    if len(window_highs) < resolved.min_touches_per_side or len(window_lows) < resolved.min_touches_per_side:
        return rejected(RangeRejectionReason.INSUFFICIENT_SWINGS)

    upper = max(swing.price for swing in window_highs)
    lower = min(swing.price for swing in window_lows)
    if upper <= lower:
        return rejected(RangeRejectionReason.INVERTED_BOUNDS)

    upper_touches = tuple(
        swing for swing in window_highs if is_within_tolerance(swing.price, upper, resolved.tolerance_pct)
    )
    lower_touches = tuple(
        swing for swing in window_lows if is_within_tolerance(swing.price, lower, resolved.tolerance_pct)
    )
    if (
        len(upper_touches) < resolved.min_touches_per_side
        or len(lower_touches) < resolved.min_touches_per_side
    ):
        return rejected(
            RangeRejectionReason.INSUFFICIENT_TOUCHES,
            upper_touch_count=len(upper_touches),
            lower_touch_count=len(lower_touches),
        )

    touch_timestamps = sorted(
        [swing.timestamp for swing in upper_touches] + [swing.timestamp for swing in lower_touches]
    )
    start_timestamp = touch_timestamps[0]
    end_timestamp = touch_timestamps[-1]
    span_candles = candle_span_count(start_timestamp, end_timestamp, interval=resolved_interval)
    if span_candles < resolved.min_span_candles:
        return rejected(
            RangeRejectionReason.SPAN_TOO_SHORT,
            upper_touch_count=len(upper_touches),
            lower_touch_count=len(lower_touches),
            span_candles=span_candles,
        )

    width = upper - lower
    width_pct = percentage_width(upper, lower)
    if width_pct > resolved.max_width_pct:
        return rejected(
            RangeRejectionReason.WIDTH_EXCEEDS_MAX,
            upper_touch_count=len(upper_touches),
            lower_touch_count=len(lower_touches),
            width_pct=width_pct,
            span_candles=span_candles,
        )

    latest_close = ordered[-1].close
    band_candles = candles_since(ordered, start=start_timestamp)
    upper_margin = tolerance_band(upper, resolved.tolerance_pct)
    lower_margin = tolerance_band(lower, resolved.tolerance_pct)
    closes_outside_band_count = sum(
        1 for candle in band_candles if candle.close < lower or candle.close > upper
    )
    band_broken = any(
        candle.close < lower - lower_margin or candle.close > upper + upper_margin
        for candle in band_candles
    )
    close_within_bounds = lower <= latest_close <= upper
    candles_since_last_touch = candle_span_count(end_timestamp, current_timestamp, interval=resolved_interval)
    active = (
        close_within_bounds
        and not band_broken
        and candles_since_last_touch <= resolved.active_max_candles_since_last_touch
    )

    return RangeDetectionResult(
        range=RangeDetection(
            range_high=upper,
            range_low=lower,
            width=quantize_derived(width),
            width_pct=width_pct,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            current_timestamp=current_timestamp,
            upper_touch_timestamps=tuple(swing.timestamp for swing in upper_touches),
            lower_touch_timestamps=tuple(swing.timestamp for swing in lower_touches),
            upper_touch_count=len(upper_touches),
            lower_touch_count=len(lower_touches),
            touch_count=len(upper_touches) + len(lower_touches),
            candles_since_last_touch=candles_since_last_touch,
            latest_close=latest_close,
            close_within_bounds=close_within_bounds,
            closes_outside_band_count=closes_outside_band_count,
            band_broken=band_broken,
            active=active,
            parameters=resolved,
            as_of=as_of_utc,
        ),
        reason=None,
        parameters=resolved,
        as_of=as_of_utc,
        candle_count=len(window_candles),
        window_start_timestamp=window_candles[0].timestamp,
        window_end_timestamp=current_timestamp,
        swing_high_count=len(window_highs),
        swing_low_count=len(window_lows),
        upper_touch_count=len(upper_touches),
        lower_touch_count=len(lower_touches),
        width_pct=width_pct,
        span_candles=span_candles,
    )
