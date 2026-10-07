"""Closed-candle decision boundaries (Step 13, part B): strict no-lookahead.

At a decision time ``T``, each hierarchy timeframe contributes exactly one
boundary: the latest candle whose FULL interval had already closed by ``T``.
An unfinished candle is never substituted, never partially used, and never
approximated — the same closed-candle semantics Steps 2-6 already enforce.

The rule is the single anti-lookahead primitive of the whole hierarchy:

    candle(open = O, timeframe = tf) is knowable at T  iff  O + tf <= T

Because candle opens are aligned, ``latest_closed_candle_open_time(T, tf)``
returns the unique open time satisfying that predicate, and every stored
candle with ``open <= latest_closed`` is knowable at ``T``.

Awkward boundaries are therefore exact:

* at 10:00 UTC the 4H candle 08:00-12:00 is NOT known (it closes at 12:00);
* at 12:00 UTC that candle IS known, and the 4H boundary is 08:00-12:00.

The same holds for 15M and 5M boundaries. Every layer records the exact
boundary timestamps it used, so the information known at each decision is
reproducible later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.multi_timeframe.hierarchy import (
    TimeframeHierarchy,
    TimeframeRole,
)


@dataclass(frozen=True, slots=True)
class TimeframeBoundary:
    """The exact closed-candle boundary one timeframe contributes at ``T``.

    ``candle_open_time``/``candle_close_time`` describe the boundary itself
    (close = open + interval) and are always defined. ``candle`` is the stored
    candle at exactly that boundary open, or ``None`` when it is not stored.
    ``latest_stored_closed`` is the newest stored candle of this timeframe that
    had fully closed by the decision time, which makes a stale stored series
    explicit instead of silently analysing an older candle as if it were current.
    """

    timeframe: str
    role: TimeframeRole
    decision_time: datetime
    candle_open_time: datetime
    candle_close_time: datetime
    candle: Candle | None
    latest_stored_closed: Candle | None

    @property
    def candle_known(self) -> bool:
        """Whether the exact expected closed candle is stored."""

        return self.candle is not None

    @property
    def stale(self) -> bool:
        """Whether stored candles stop before the expected closed candle."""

        return self.latest_stored_closed is None or (
            self.latest_stored_closed.timestamp < self.candle_open_time
        )

    def to_json_dict(self) -> dict[str, object]:
        return {
            "timeframe": self.timeframe,
            "role": self.role.value,
            "decision_time": to_jsonable(self.decision_time),
            "candle_open_time": to_jsonable(self.candle_open_time),
            "candle_close_time": to_jsonable(self.candle_close_time),
            "candle_known": self.candle_known,
            "stale": self.stale,
            "candle": None if self.candle is None else to_jsonable(self.candle),
            "latest_stored_closed": (
                None
                if self.latest_stored_closed is None
                else to_jsonable(self.latest_stored_closed)
            ),
        }


@dataclass(frozen=True, slots=True)
class DecisionBoundary:
    """Everything one hierarchy evaluation knows about its decision instant."""

    decision_time: datetime
    boundaries: tuple[TimeframeBoundary, ...]

    def boundary_for(self, timeframe: str) -> TimeframeBoundary:
        for boundary in self.boundaries:
            if boundary.timeframe == timeframe:
                return boundary
        raise KeyError(f"timeframe {timeframe!r} is not part of this boundary")

    def boundary_for_role(self, role: TimeframeRole) -> TimeframeBoundary:
        for boundary in self.boundaries:
            if boundary.role is role:
                return boundary
        raise KeyError(f"role {role.value!r} is not part of this boundary")

    @property
    def all_candles_known(self) -> bool:
        return all(boundary.candle_known for boundary in self.boundaries)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "decision_time": to_jsonable(self.decision_time),
            "boundaries": [boundary.to_json_dict() for boundary in self.boundaries],
        }


def _select_candles(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    expected_open: datetime,
    decision_time: datetime,
) -> tuple[Candle | None, Candle | None]:
    """Return ``(candle_at_boundary, latest_stored_closed)`` for one timeframe.

    Only stored candles whose full interval ended at or before the decision
    time are considered, and only aligned opens at or before the expected
    boundary open are eligible: a candle that opened later than the boundary is
    future data at the decision time and is never selected, whatever the caller
    stored.
    """

    interval = interval_for_timeframe(timeframe)
    at_boundary: Candle | None = None
    latest_closed: Candle | None = None
    for candle in candles:
        if candle.timeframe != timeframe:
            raise ValueError("boundary candles must match their timeframe")
        if candle.timestamp + interval > decision_time:
            # Not fully closed at the decision time: never usable here.
            continue
        if candle.timestamp > expected_open:
            # Beyond the knowable boundary: future data at the decision time.
            continue
        if latest_closed is None or candle.timestamp > latest_closed.timestamp:
            latest_closed = candle
        if candle.timestamp == expected_open:
            at_boundary = candle
    return at_boundary, latest_closed


def resolve_decision_boundary(
    decision_time: datetime,
    hierarchy: TimeframeHierarchy,
    candles_by_timeframe: dict[str, tuple[Candle, ...]] | None = None,
) -> DecisionBoundary:
    """Resolve the exact closed-candle boundary of every timeframe at ``T``.

    ``candles_by_timeframe`` maps a timeframe to the stored candles the caller
    already loaded (any range; selection is by closed-candle semantics, not by
    list position). Timeframes without stored candles contribute an explicit
    boundary with ``candle=None``.
    """

    instant = require_utc_datetime(decision_time, field_name="decision_time")
    resolved: dict[str, tuple[Candle, ...]] = candles_by_timeframe or {}
    boundaries: list[TimeframeBoundary] = []
    for step in hierarchy.steps:
        timeframe = step.timeframe
        interval = interval_for_timeframe(timeframe)
        candle_open = latest_closed_candle_open_time(instant, timeframe)
        candle_close = candle_open + interval
        at_boundary, latest_closed = _select_candles(
            tuple(resolved.get(timeframe, ())),
            timeframe=timeframe,
            expected_open=candle_open,
            decision_time=instant,
        )
        boundaries.append(
            TimeframeBoundary(
                timeframe=timeframe,
                role=step.role,
                decision_time=instant,
                candle_open_time=candle_open,
                candle_close_time=candle_close,
                candle=at_boundary,
                latest_stored_closed=latest_closed,
            )
        )
    return DecisionBoundary(decision_time=instant, boundaries=tuple(boundaries))


__all__ = [
    "DecisionBoundary",
    "TimeframeBoundary",
    "resolve_decision_boundary",
]
