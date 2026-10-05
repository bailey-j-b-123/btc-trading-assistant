"""Deterministic structural-trend classification from confirmed swings.

The classification compares consecutive confirmed swing highs and consecutive
confirmed swing lows. It never forces a direction: too few swings, conflicting
evidence, or flat extremes all produce an explicit neutral result with the
reason and the exact swing evidence that produced it.

Only swings that were already confirmed at the requested ``as_of`` instant are
considered, so a historical classification cannot change when later candles are
stored.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import pairwise

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_structure.numeric import require_int
from trading_assistant.market_structure.swings import SwingKind, SwingPoint


class TrendDirection(StrEnum):
    """Direction of the confirmed structural trend."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class TrendReason(StrEnum):
    """Deterministic reason for the returned direction."""

    HIGHER_HIGHS_AND_HIGHER_LOWS = "higher_highs_and_higher_lows"
    LOWER_HIGHS_AND_LOWER_LOWS = "lower_highs_and_lower_lows"
    INSUFFICIENT_SWINGS = "insufficient_swings"
    EQUAL_EXTREMES = "equal_extremes"
    CONFLICTING_STRUCTURE = "conflicting_structure"


@dataclass(frozen=True, slots=True)
class TrendParameters:
    """How many of the most recent confirmed swings per side are compared."""

    swing_count: int = 2

    def __post_init__(self) -> None:
        require_int(self.swing_count, name="swing_count", minimum=2)

    @property
    def comparison_count(self) -> int:
        """Number of consecutive comparisons performed per side."""

        return self.swing_count - 1


@dataclass(frozen=True, slots=True)
class TrendClassification:
    """Structural trend plus the swing evidence behind it."""

    direction: TrendDirection
    reason: TrendReason
    as_of: datetime
    parameters: TrendParameters
    swing_highs: tuple[SwingPoint, ...]
    swing_lows: tuple[SwingPoint, ...]
    higher_highs: bool | None
    higher_lows: bool | None
    lower_highs: bool | None
    lower_lows: bool | None
    equal_highs: bool | None
    equal_lows: bool | None
    confirmed_swing_count: int
    excluded_unconfirmed_swing_count: int

    @property
    def evidence(self) -> tuple[SwingPoint, ...]:
        """The swing extremes used, ordered by candle time."""

        return tuple(sorted(self.swing_highs + self.swing_lows, key=lambda swing: swing.timestamp))

    @property
    def sufficient(self) -> bool:
        """Whether enough confirmed swings existed to attempt a classification."""

        return self.reason is not TrendReason.INSUFFICIENT_SWINGS


def _strictly_rising(points: tuple[SwingPoint, ...]) -> bool:
    return all(later.price > earlier.price for earlier, later in pairwise(points))


def _strictly_falling(points: tuple[SwingPoint, ...]) -> bool:
    return all(later.price < earlier.price for earlier, later in pairwise(points))


def _all_equal(points: tuple[SwingPoint, ...]) -> bool:
    return all(later.price == earlier.price for earlier, later in pairwise(points))


def classify_trend(
    swings: Iterable[SwingPoint],
    *,
    as_of: datetime,
    parameters: TrendParameters | None = None,
) -> TrendClassification:
    """Classify bullish, bearish, or neutral structure from confirmed swings.

    Bullish requires strictly rising highs *and* strictly rising lows across the
    most recent ``swing_count`` swings of each kind; bearish requires strictly
    falling highs and lows. Anything else (too few swings, a flat sequence, or
    rising highs with falling lows) is reported as neutral with an explicit
    reason instead of a forced trend.
    """

    resolved = parameters if parameters is not None else TrendParameters()
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    ordered = tuple(sorted(swings, key=lambda swing: (swing.timestamp, swing.kind)))
    confirmed = tuple(swing for swing in ordered if swing.confirmed_at <= as_of_utc)
    excluded = len(ordered) - len(confirmed)

    recent_highs = tuple(swing for swing in confirmed if swing.kind is SwingKind.HIGH)[-resolved.swing_count :]
    recent_lows = tuple(swing for swing in confirmed if swing.kind is SwingKind.LOW)[-resolved.swing_count :]

    if len(recent_highs) < resolved.swing_count or len(recent_lows) < resolved.swing_count:
        return TrendClassification(
            direction=TrendDirection.NEUTRAL,
            reason=TrendReason.INSUFFICIENT_SWINGS,
            as_of=as_of_utc,
            parameters=resolved,
            swing_highs=recent_highs,
            swing_lows=recent_lows,
            higher_highs=None,
            higher_lows=None,
            lower_highs=None,
            lower_lows=None,
            equal_highs=None,
            equal_lows=None,
            confirmed_swing_count=len(confirmed),
            excluded_unconfirmed_swing_count=excluded,
        )

    higher_highs = _strictly_rising(recent_highs)
    lower_highs = _strictly_falling(recent_highs)
    equal_highs = _all_equal(recent_highs)
    higher_lows = _strictly_rising(recent_lows)
    lower_lows = _strictly_falling(recent_lows)
    equal_lows = _all_equal(recent_lows)

    if higher_highs and higher_lows:
        direction = TrendDirection.BULLISH
        reason = TrendReason.HIGHER_HIGHS_AND_HIGHER_LOWS
    elif lower_highs and lower_lows:
        direction = TrendDirection.BEARISH
        reason = TrendReason.LOWER_HIGHS_AND_LOWER_LOWS
    elif equal_highs and equal_lows:
        direction = TrendDirection.NEUTRAL
        reason = TrendReason.EQUAL_EXTREMES
    else:
        direction = TrendDirection.NEUTRAL
        reason = TrendReason.CONFLICTING_STRUCTURE

    return TrendClassification(
        direction=direction,
        reason=reason,
        as_of=as_of_utc,
        parameters=resolved,
        swing_highs=recent_highs,
        swing_lows=recent_lows,
        higher_highs=higher_highs,
        higher_lows=higher_lows,
        lower_highs=lower_highs,
        lower_lows=lower_lows,
        equal_highs=equal_highs,
        equal_lows=equal_lows,
        confirmed_swing_count=len(confirmed),
        excluded_unconfirmed_swing_count=excluded,
    )
