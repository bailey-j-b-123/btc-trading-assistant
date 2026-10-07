"""Required-window data-integrity gate (Component #1).

Every deterministic trading decision consumes a trailing window of closed
candles ending at its decision boundary. Some engine components index those
trailing candles by *count* (Wilder ATR over ``period + 1`` closes, relative
volume over ``period + 1`` closes), so a hole *inside* the consumed window
silently corrupts them: the candle after the hole is treated as adjacent to
the candle before it. Other components are gap-aware by design (swing windows
never span a gap, the Step 4 replay resets detector state at a gap while
keeping recorded pre-gap evidence, qualification seeds overlapping a gap are
terminally invalidated), so a hole *older* than everything the decision
consumes is already isolated and must not block the decision forever.

This module answers one question purely — "is the candle window required by
this evaluation contiguous?" — with no I/O, no clock, and no fabrication:

* :func:`required_trailing_depth` derives, from the live parameter objects the
  caller already holds, how many trailing closes a decision can depend on: the
  qualification replay span (a live setup was seeded within ``max_bars``
  closes) plus the deepest structure/pattern trailing span (range lookback,
  ATR/volume periods, pattern spans and confirmation windows). Older history
  feeds only preserved catalog entries that the engines' gap-reset semantics
  already isolate.
* :func:`required_window` maps a decision instant and a timeframe to the exact
  inclusive open-time window ``[window_start, window_end]`` those closes occupy.
* :func:`assess_required_window` diffs stored candles against that grid and
  reports the exact missing open times.

Leading shortfall (the required window starts before the first stored candle:
a cold start, a Kraken rolling-window truncation, genuine short history) is
reported as ``truncated_before`` but never blocks: short history is already
handled by the engines' explicit insufficient-data states and the runner
minimum-history preconditions. Only a hole *inside* the stored span blocks.

All timestamps are UTC opens aligned to the timeframe; a still-forming candle
is never part of any required window because every window ends at the latest
candle that had fully closed by the decision instant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from trading_assistant.market_data.timeframes import (
    latest_closed_candle_open_time,
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle


def _require_positive_int(value: Any, *, name: str) -> int:
    """Reject a non-integer or non-positive lookback without coercion."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


def required_trailing_depth(
    *,
    structure: Any,
    pattern: Any,
    qualification: Any,
) -> int:
    """Trailing closes a single-timeframe decision at one boundary can depend on.

    The parameter objects are read (never modified) through their documented
    attributes, so a configuration change automatically moves the gate with it
    instead of silently drifting apart from it:

    * ``structural_span`` — the deepest trailing span the market-structure
      engine indexes: the range-detection lookback and the ATR / relative
      volume ``period + 1`` close requirements;
    * ``pattern_span`` — the deepest trailing span the pattern/liquidity replay
      indexes: classical-pattern span and breakout/failure/retest windows;
    * ``replay_span`` — the qualification bounded-replay span (``max_bars + 1``
      closes): the oldest frame whose seeds can still be live.

    The required depth is ``replay_span + max(structural_span, pattern_span)``:
    the oldest consumed frame sits ``replay_span`` closes back and each
    consumed frame trails ``max_span`` further back. With default parameters
    that is ``11 + 120 = 131`` closes on every timeframe.
    """

    structural_span = max(
        _require_positive_int(structure.ranges.lookback_candles, name="ranges.lookback_candles"),
        _require_positive_int(structure.volume.period, name="volume.period") + 1,
        _require_positive_int(structure.volatility.period, name="volatility.period") + 1,
    )
    pattern_span = max(
        _require_positive_int(
            pattern.pattern_max_span_candles, name="pattern_max_span_candles"
        ),
        _require_positive_int(
            pattern.failure_window_candles, name="failure_window_candles"
        ),
        _require_positive_int(pattern.retest_window_candles, name="retest_window_candles"),
        _require_positive_int(
            pattern.breakout_confirmation_candles, name="breakout_confirmation_candles"
        ),
    )
    replay_span = (
        max(
            _require_positive_int(
                qualification.continuation_max_bars, name="continuation_max_bars"
            ),
            _require_positive_int(qualification.reversal_max_bars, name="reversal_max_bars"),
            _require_positive_int(qualification.range_max_bars, name="range_max_bars"),
        )
        + 1
    )
    return replay_span + max(structural_span, pattern_span)


def required_window(
    decision_time: datetime,
    timeframe: str,
    *,
    depth: int,
) -> tuple[datetime, datetime]:
    """Inclusive ``(window_start, window_end)`` opens required at an instant.

    The window ends at the latest candle open whose full interval had closed by
    ``decision_time`` (a still-forming candle is never required) and extends
    ``depth`` closes back. ``decision_time`` is usually a candle-close boundary
    but any instant is accepted: the end is always the latest closed open.
    """

    depth_value = _require_positive_int(depth, name="depth")
    end_open = latest_closed_candle_open_time(
        require_utc_datetime(decision_time, field_name="decision_time"), timeframe
    )
    interval = timedelta(milliseconds=timeframe_to_milliseconds(timeframe))
    return end_open - (depth_value - 1) * interval, end_open


@dataclass(frozen=True, slots=True)
class RequiredWindowAssessment:
    """Contiguity verdict for one required window; missing opens are exact."""

    exchange: str
    symbol: str
    timeframe: str
    decision_time: datetime
    required_depth: int
    window_start: datetime
    window_end: datetime
    first_stored_open: datetime | None
    effective_start: datetime | None
    stored_in_span: int
    missing_opens: tuple[datetime, ...]
    truncated_before: datetime | None

    @property
    def missing_count(self) -> int:
        return len(self.missing_opens)

    @property
    def complete(self) -> bool:
        """Whether the required span holds every expected closed candle."""

        return self.first_stored_open is not None and not self.missing_opens

    @property
    def boundary_candle_stored(self) -> bool:
        """Whether the decision boundary's own candle is stored."""

        return self.first_stored_open is not None and (
            not self.missing_opens or self.window_end not in self.missing_opens
        )

    def missing_summary(self, *, limit: int = 5) -> str:
        """One line naming the missing boundaries (bounded, deterministic)."""

        shown = ", ".join(open_time.isoformat() for open_time in self.missing_opens[:limit])
        if self.missing_count > limit:
            shown += f", … ({self.missing_count - limit} more)"
        return shown

    def to_json_dict(self) -> dict[str, object]:
        """Stable diagnostics payload; datetimes use the codebase ``Z`` form."""

        def _iso(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat().replace("+00:00", "Z")

        return {
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision_time": _iso(self.decision_time),
            "required_depth": self.required_depth,
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "first_stored_open": _iso(self.first_stored_open),
            "effective_start": _iso(self.effective_start),
            "stored_in_span": self.stored_in_span,
            "missing_count": self.missing_count,
            "missing_opens": [_iso(open_time) for open_time in self.missing_opens],
            "truncated_before": _iso(self.truncated_before),
            "complete": self.complete,
            "boundary_candle_stored": self.boundary_candle_stored,
        }


def assess_required_window(
    candles: tuple[Candle, ...] | list[Candle],
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    decision_time: datetime,
    depth: int,
) -> RequiredWindowAssessment:
    """Diff stored candles against the required grid; never fabricate any.

    ``candles`` is the stored series for exactly ``exchange/symbol/timeframe``
    (any range; only opens inside the effective span are counted). A candle
    from another instrument or timeframe is refused rather than silently
    filtered, so a cross-timeframe mix-up can never read as contiguous.
    """

    window_start, window_end = required_window(decision_time, timeframe, depth=depth)
    interval = timedelta(milliseconds=timeframe_to_milliseconds(timeframe))
    ordered = tuple(candles)
    for candle in ordered:
        if not isinstance(candle, Candle):
            raise TypeError("assessment input must consist of Candle instances")
        if (
            candle.exchange != exchange
            or candle.symbol != symbol
            or candle.timeframe != timeframe
        ):
            raise ValueError(
                "assessment candles must match "
                f"{exchange}/{symbol}/{timeframe}, got "
                f"{candle.exchange}/{candle.symbol}/{candle.timeframe}"
            )
    first_stored: datetime | None = min(
        (candle.timestamp for candle in ordered), default=None
    )
    if first_stored is None:
        return RequiredWindowAssessment(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            decision_time=require_utc_datetime(decision_time, field_name="decision_time"),
            required_depth=_require_positive_int(depth, name="depth"),
            window_start=window_start,
            window_end=window_end,
            first_stored_open=None,
            effective_start=None,
            stored_in_span=0,
            missing_opens=(),
            truncated_before=None,
        )
    effective_start = max(window_start, first_stored)
    stored_opens = {
        candle.timestamp for candle in ordered if candle.timestamp >= effective_start
    }
    missing: list[datetime] = []
    cursor = effective_start
    while cursor <= window_end:
        if cursor not in stored_opens:
            missing.append(cursor)
        cursor += interval
    return RequiredWindowAssessment(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        decision_time=require_utc_datetime(decision_time, field_name="decision_time"),
        required_depth=_require_positive_int(depth, name="depth"),
        window_start=window_start,
        window_end=window_end,
        first_stored_open=first_stored,
        effective_start=effective_start,
        stored_in_span=len(
            [open_time for open_time in stored_opens if open_time <= window_end]
        ),
        missing_opens=tuple(missing),
        truncated_before=first_stored if first_stored > window_start else None,
    )


__all__ = [
    "RequiredWindowAssessment",
    "assess_required_window",
    "required_trailing_depth",
    "required_window",
]
