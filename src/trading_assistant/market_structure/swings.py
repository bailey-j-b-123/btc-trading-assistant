"""Deterministic confirmed swing-high and swing-low detection.

A swing is a candle that is the extreme of a symmetric left/right window. It is
only *confirmed* once every candle in its right window has fully closed, which
is recorded as ``confirmed_at`` (the close instant of the right-window edge
candle). No market-structure component may use a swing before that instant.

Equal highs/lows are resolved by an explicit, documented tie policy; windows are
never evaluated across a candle gap, and no candle is ever invented to make a
window contiguous.
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
    ordered_candles,
    require_positive_interval,
)
from trading_assistant.market_structure.numeric import require_int


class SwingKind(StrEnum):
    """Which extreme a swing point represents."""

    HIGH = "high"
    LOW = "low"


class SwingTiePolicy(StrEnum):
    """Deterministic policy for equal highs or equal lows inside a window.

    ``STRICT``
        A swing high must be *strictly* greater than every other high in the
        window (a swing low strictly lower than every other low). If any other
        candle in the window shares the extreme value, no swing is reported for
        any candle of that tied group.
    ``EARLIEST_EQUAL``
        A candle qualifies when it attains the window extreme and no earlier
        candle inside the same window attains it. The earliest candle of a tied
        plateau is therefore the swing and later equal candles are not.
    """

    STRICT = "strict"
    EARLIEST_EQUAL = "earliest_equal"


@dataclass(frozen=True, slots=True)
class SwingParameters:
    """Left/right confirmation windows and the equal-extreme tie policy."""

    left_window: int = 2
    right_window: int = 2
    tie_policy: SwingTiePolicy = SwingTiePolicy.STRICT

    def __post_init__(self) -> None:
        for name in ("left_window", "right_window"):
            require_int(getattr(self, name), name=name, minimum=1)
        if not isinstance(self.tie_policy, SwingTiePolicy):
            try:
                object.__setattr__(self, "tie_policy", SwingTiePolicy(self.tie_policy))
            except ValueError as exc:
                raise ValueError(
                    "tie_policy must be one of: " + ", ".join(policy.value for policy in SwingTiePolicy)
                ) from exc

    @property
    def window_size(self) -> int:
        """Total candles required to evaluate one swing candidate."""

        return self.left_window + self.right_window + 1


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """One confirmed swing extreme with its exact confirmation timing."""

    kind: SwingKind
    timestamp: datetime
    price: Decimal
    confirmed_at: datetime
    confirmed_by_timestamp: datetime
    left_window: int
    right_window: int
    tie_policy: SwingTiePolicy


@dataclass(frozen=True, slots=True)
class SwingDetectionResult:
    """Confirmed swings plus deterministic diagnostics about the evaluation."""

    swings: tuple[SwingPoint, ...]
    parameters: SwingParameters
    as_of: datetime
    candle_count: int
    evaluated_candidate_count: int
    insufficient_window_count: int
    gap_window_count: int
    tie_rejection_count: int
    unconfirmed_count: int

    @property
    def required_candle_count(self) -> int:
        """Candles needed before any candidate can be evaluated."""

        return self.parameters.window_size

    @property
    def sufficient(self) -> bool:
        """Whether the window was long enough to evaluate at least one candidate."""

        return self.candle_count >= self.required_candle_count

    @property
    def highs(self) -> tuple[SwingPoint, ...]:
        return tuple(swing for swing in self.swings if swing.kind is SwingKind.HIGH)

    @property
    def lows(self) -> tuple[SwingPoint, ...]:
        return tuple(swing for swing in self.swings if swing.kind is SwingKind.LOW)

    @property
    def unconfirmed_swings(self) -> int:
        """Detected-but-not-yet-knowable swings excluded by the ``as_of`` filter."""

        return self.unconfirmed_count


def _contiguous_window(
    candles: tuple[Candle, ...],
    first_index: int,
    last_index: int,
    interval: timedelta,
) -> bool:
    """Return whether every neighbouring pair in a window is exactly one interval apart."""

    return all(
        candles[index + 1].timestamp - candles[index].timestamp == interval
        for index in range(first_index, last_index)
    )


def _swing_point(
    candles: tuple[Candle, ...],
    index: int,
    kind: SwingKind,
    parameters: SwingParameters,
    interval: timedelta,
) -> SwingPoint:
    confirming_index = index + parameters.right_window
    return SwingPoint(
        kind=kind,
        timestamp=candles[index].timestamp,
        price=candles[index].high if kind is SwingKind.HIGH else candles[index].low,
        confirmed_at=candles[confirming_index].timestamp + interval,
        confirmed_by_timestamp=candles[confirming_index].timestamp,
        left_window=parameters.left_window,
        right_window=parameters.right_window,
        tie_policy=parameters.tie_policy,
    )


def detect_swings(
    candles: Iterable[Candle],
    *,
    interval: timedelta,
    as_of: datetime,
    parameters: SwingParameters | None = None,
) -> SwingDetectionResult:
    """Detect confirmed swing highs and lows using only candles closed by ``as_of``.

    Candidates are evaluated in the exact left-to-right order of the input. A
    candidate is skipped when the window is incomplete (not enough candles on
    either side) or when the window spans a candle gap, so structure is never
    inferred across missing data. Swings whose confirming candle had not closed
    by ``as_of`` are excluded and counted in ``unconfirmed_count``.
    """

    resolved = parameters if parameters is not None else SwingParameters()
    resolved_interval = require_positive_interval(interval)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = ordered_candles(candles, interval=resolved_interval)

    left = resolved.left_window
    right = resolved.right_window
    detected: list[SwingPoint] = []
    evaluated = 0
    insufficient = 0
    gapped = 0
    ties = 0

    for index in range(len(ordered)):
        first_index = index - left
        last_index = index + right
        if first_index < 0 or last_index >= len(ordered):
            insufficient += 1
            continue
        if not _contiguous_window(ordered, first_index, last_index, resolved_interval):
            gapped += 1
            continue
        evaluated += 1

        window_indices = [position for position in range(first_index, last_index + 1) if position != index]
        high_others = [ordered[position].high for position in window_indices]
        if ordered[index].high > max(high_others):
            detected.append(_swing_point(ordered, index, SwingKind.HIGH, resolved, resolved_interval))
        elif ordered[index].high == max(high_others):
            if resolved.tie_policy is SwingTiePolicy.EARLIEST_EQUAL and not any(
                ordered[position].high == ordered[index].high
                for position in range(first_index, index)
            ):
                detected.append(_swing_point(ordered, index, SwingKind.HIGH, resolved, resolved_interval))
            else:
                ties += 1

        low_others = [ordered[position].low for position in window_indices]
        if ordered[index].low < min(low_others):
            detected.append(_swing_point(ordered, index, SwingKind.LOW, resolved, resolved_interval))
        elif ordered[index].low == min(low_others):
            if resolved.tie_policy is SwingTiePolicy.EARLIEST_EQUAL and not any(
                ordered[position].low == ordered[index].low
                for position in range(first_index, index)
            ):
                detected.append(_swing_point(ordered, index, SwingKind.LOW, resolved, resolved_interval))
            else:
                ties += 1

    confirmed = tuple(swing for swing in detected if swing.confirmed_at <= as_of_utc)
    return SwingDetectionResult(
        swings=confirmed,
        parameters=resolved,
        as_of=as_of_utc,
        candle_count=len(ordered),
        evaluated_candidate_count=evaluated,
        insufficient_window_count=insufficient,
        gap_window_count=gapped,
        tie_rejection_count=ties,
        unconfirmed_count=len(detected) - len(confirmed),
    )
