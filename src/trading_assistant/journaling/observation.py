"""Deterministic, anti-lookahead outcome observation for one proposed plan.

This module is a pure function over three immutable inputs: a proposed-plan
level projection, a sequence of stored candles, and one inclusive observation
cutoff. It never reads a clock, a database, or a network, and it never looks at
a candle after the cutoff, so an observation made at a historical cutoff cannot
change when later candles are inserted into the archive.

Exact touch semantics (long/short are symmetric; ``E`` entry, ``S`` stop,
``T`` target):

* **entry** — reached when the candle traded the entry price:
  ``low <= E <= high``. A candle whose whole range lies beyond the entry did not
  trade there, so the entry is not claimed to have been reached.
* **stop** — met when the candle's adverse extreme reached the stop condition:
  long ``low <= S``, short ``high >= S``.
* **target** — met when the candle's favourable extreme reached the target
  condition: long ``high >= T``, short ``low <= T``.

A level outside the candle range is never assumed to have been touched, so the
observation never guesses a result that the OHLC data does not evidence.

Ordering and ambiguity: candles are walked in ascending open time. Within one
candle, if the entry and an exit (stop or target) are both touched, or the stop
and any target are both touched, the intra-candle order is unknowable from OHLC
and the trajectory stops with ``AMBIGUOUS`` — the favourable result is never
chosen. Multiple targets touched in one candle share a touch group; their order
relative to each other is immaterial and is not invented. Touches that happen
before the entry (possible for a target on a jump) are recorded as
``PRE_ENTRY`` and never count toward the post-entry trajectory.

1-minute ordering evidence (``journal-outcome-v2`` only): when the caller
supplies genuine, confirmed stored 1-minute candles (``resolution_candles``),
an ambiguous higher-timeframe candle is re-examined **inside that one candle
only**, using exactly the same touch, ordering, pre-entry, and
never-pick-the-winner rules at 1-minute granularity. The plan's entry, stop,
targets, planning timestamp, and minimum-1R rules are never influenced by the
1-minute series — it can only order events the higher-timeframe candle already
evidenced. Resolution is strictly conservative:

* every expected 1-minute candle of the ambiguous candle must be present
  (missing minutes keep the result ``AMBIGUOUS``);
* the 1-minute series must reproduce the higher-timeframe candle's open, high,
  low, and close exactly and stay inside its range (any conflict keeps the
  result ``AMBIGUOUS``);
* if the competing levels are touched within one and the same 1-minute candle,
  the order remains unknowable and the result stays ``AMBIGUOUS``.

When resolution succeeds, the trajectory continues (or terminates) on the
1-minute evidence; otherwise the observation is recorded exactly as the
unscored ``AMBIGUOUS`` it would have been under ``journal-outcome-v1``, with
the deterministic reason stored alongside. Without resolution candles,
``journal-outcome-v2`` behaves exactly like ``journal-outcome-v1``.

Gaps: expected candle open times are derived from the timeframe, not from the
supplied rows. At the first missing open time the evaluation stops, the missing
ranges are reported, and the result becomes ``INCOMPLETE_DATA`` unless a
terminal event was already established before the gap. Missing candles are never
bridged.

MFE/MAE: ``evaluated_low``/``evaluated_high`` are the raw extremes over the
evaluated window (window start through the terminal candle, or the cutoff).
``mfe_price_move``/``mae_price_move`` are the proposed-entry-relative favourable
and adverse moves, clamped at zero, and ``mfe_r``/``mae_r`` the same values in
proposed-risk multiples rounded with the shared ``quantize_derived`` rule. The
raw extremes are stored so nothing is lost for later statistics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_assistant.journaling.parameters import (
    OUTCOME_RESOLUTION_RULES_VERSION,
    OutcomeParameters,
    fingerprint,
)
from trading_assistant.journaling.types import (
    OutcomeEvent,
    OutcomeEventKind,
    OutcomeEventOrdering,
    OutcomeObservation,
    OutcomeStatus,
    ProposedPlanLevels,
)
from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    is_timeframe_aligned,
    milliseconds_to_datetime,
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.market_data.types import Candle, CandleGap
from trading_assistant.market_structure.numeric import quantize_derived

#: Ambiguity kind: the entry and an exit share one candle.
ENTRY_AND_EXIT_SAME_CANDLE = "entry_and_exit_same_candle"

#: Ambiguity kind: the stop and at least one target share one candle.
STOP_AND_TARGET_SAME_CANDLE = "stop_and_target_same_candle"

#: The only approved ordering-resolution granularity: genuine, confirmed
#: 1-minute candles used (in ``journal-outcome-v2``) solely to order events
#: inside one ambiguous higher-timeframe candle. Never used to re-plan.
RESOLUTION_TIMEFRAME = "1m"

#: Resolution outcome codes. ``resolution_used`` means the stored 1-minute
#: evidence decided an ordering; the other codes each explain why the result
#: stayed an explicitly unscored ``AMBIGUOUS``.
RESOLUTION_USED = "resolution_used"
RESOLUTION_NO_CANDLES = "resolution_no_candles"
RESOLUTION_COVERAGE_INCOMPLETE = "resolution_coverage_incomplete"
RESOLUTION_CONSISTENCY_CONFLICT = "resolution_consistency_conflict"
RESOLUTION_SAME_MINUTE_AMBIGUOUS = "resolution_same_minute_ambiguous"

#: Resolution reasons where newly arrived 1-minute evidence could still change
#: the observation. ``same_minute`` and ``consistency`` failures are final: the
#: evidence either existed and was insufficient, or contradicted the candle.
RESOLUTION_PENDING_REASONS = frozenset(
    {RESOLUTION_NO_CANDLES, RESOLUTION_COVERAGE_INCOMPLETE}
)

_TOUCH_ORDER = {
    OutcomeEventKind.ENTRY: 0,
    OutcomeEventKind.STOP: 1,
    OutcomeEventKind.TARGET: 2,
}


def _touch_sort_key(
    touch: tuple[OutcomeEventKind, int | None, Decimal],
) -> tuple[int, int]:
    kind, target_index, _level = touch
    return (_TOUCH_ORDER[kind], -1 if target_index is None else target_index)


def observe_outcome(
    *,
    journal_id: str,
    levels: ProposedPlanLevels,
    candles: Sequence[Candle],
    observed_through: datetime,
    parameters: OutcomeParameters | None = None,
    resolution_candles: Sequence[Candle] | None = None,
) -> OutcomeObservation:
    """Observe how the market moved relative to one proposed plan.

    ``journal_id`` is the immutable journal record the observation belongs to
    and is part of the observation identity, so two records can never share one
    stored observation row. ``candles`` must contain only candles for the plan's
    instrument/timeframe inside ``[plan.as_of, observed_through]``; anything
    outside that window is refused rather than silently filtered.

    ``resolution_candles`` is optional ordering evidence for
    ``journal-outcome-v2`` observations: genuine, confirmed stored 1-minute
    candles for the plan instrument covering any subset of the evaluated
    window's span ``[plan.as_of, observed_through + one plan interval)``. They
    are consulted **only** inside an ambiguous higher-timeframe candle to order
    the events that candle already evidenced; they never alter the plan, the
    window, or the touch rules, and anything outside the span is refused rather
    than silently filtered. Supplying them to ``journal-outcome-v1`` is an
    error, because v1 identities must never depend on resolution evidence.
    """

    if not isinstance(journal_id, str) or not journal_id.strip():
        raise ValueError("journal_id must be a non-empty string")
    if not isinstance(levels, ProposedPlanLevels):
        raise TypeError("levels must be a ProposedPlanLevels projection")
    config = parameters if parameters is not None else OutcomeParameters()
    if not isinstance(config, OutcomeParameters):
        raise TypeError("parameters must be OutcomeParameters or None")

    window_start = require_utc_datetime(levels.as_of, field_name="levels.as_of")
    cutoff = require_utc_datetime(observed_through, field_name="observed_through")
    timeframe = levels.timeframe
    interval_ms = timeframe_to_milliseconds(timeframe)
    start_ms = datetime_to_milliseconds(window_start)
    end_ms = datetime_to_milliseconds(cutoff)
    if not is_timeframe_aligned(start_ms, timeframe):
        raise ValueError("plan as_of must align to the plan timeframe")
    if not is_timeframe_aligned(end_ms, timeframe):
        raise ValueError("observed_through must be an aligned candle open time")
    if end_ms < start_ms:
        raise ValueError("observed_through must not precede the plan as_of")
    if (end_ms - start_ms) % interval_ms:
        raise ValueError("observed_through must align to the plan timeframe grid")

    resolution_by_htf_open: dict[int, list[Candle]] = {}
    if resolution_candles is not None:
        if config.rules_version != OUTCOME_RESOLUTION_RULES_VERSION:
            raise ValueError(
                "resolution_candles require the journal-outcome-v2 observation "
                "rules; journal-outcome-v1 identities never depend on "
                "resolution evidence"
            )
        minute_ms = timeframe_to_milliseconds(RESOLUTION_TIMEFRAME)
        if interval_ms % minute_ms:
            raise ValueError(
                "the plan timeframe must be an exact multiple of the "
                f"{RESOLUTION_TIMEFRAME} resolution timeframe"
            )
        if interval_ms <= minute_ms:
            raise ValueError(
                "resolution candles must be strictly finer than the plan "
                "timeframe"
            )
        resolution_end_ms = end_ms + interval_ms
        seen_minutes: set[int] = set()
        for minute in resolution_candles:
            if not isinstance(minute, Candle):
                raise TypeError(
                    "resolution_candles must contain market_data Candle values"
                )
            if (
                minute.exchange != levels.exchange
                or minute.symbol != levels.symbol
            ):
                raise ValueError(
                    f"resolution candle {minute.exchange}/{minute.symbol}/"
                    f"{minute.timeframe} does not match the plan instrument "
                    f"{levels.exchange}/{levels.symbol}/{timeframe}"
                )
            if minute.timeframe != RESOLUTION_TIMEFRAME:
                raise ValueError(
                    "resolution candles must use the "
                    f"{RESOLUTION_TIMEFRAME} timeframe, not {minute.timeframe!r}"
                )
            minute_ms_value = datetime_to_milliseconds(
                minute.timestamp, field_name="resolution candle timestamp"
            )
            if not is_timeframe_aligned(minute_ms_value, RESOLUTION_TIMEFRAME):
                raise ValueError(
                    "every resolution candle timestamp must align to the "
                    f"{RESOLUTION_TIMEFRAME} grid"
                )
            for name in ("low", "high"):
                price = getattr(minute, name)
                if not isinstance(price, Decimal):
                    raise TypeError(f"resolution candle {name} must be a Decimal")
                if not price.is_finite():
                    raise ValueError(f"resolution candle {name} must be finite")
            if minute.high < minute.low:
                raise ValueError(
                    "resolution candle high must not be below candle low"
                )
            if not start_ms <= minute_ms_value < resolution_end_ms:
                raise ValueError(
                    "every resolution candle must lie inside the evaluated "
                    "window span [plan.as_of, observed_through + one plan "
                    "interval); future or earlier minutes are never used"
                )
            if minute_ms_value in seen_minutes:
                raise ValueError(
                    "resolution candles must not repeat a minute open time"
                )
            seen_minutes.add(minute_ms_value)
            htf_open_ms = minute_ms_value - ((minute_ms_value - start_ms) % interval_ms)
            resolution_by_htf_open.setdefault(htf_open_ms, []).append(minute)
        for minutes in resolution_by_htf_open.values():
            minutes.sort(key=lambda minute: minute.timestamp)

    present: dict[int, Candle] = {}
    for candle in candles:
        if not isinstance(candle, Candle):
            raise TypeError("candles must contain market_data Candle values")
        if (
            candle.exchange != levels.exchange
            or candle.symbol != levels.symbol
            or candle.timeframe != timeframe
        ):
            raise ValueError(
                f"candle {candle.exchange}/{candle.symbol}/{candle.timeframe} "
                f"does not match the plan instrument "
                f"{levels.exchange}/{levels.symbol}/{timeframe}"
            )
        candle_ms = datetime_to_milliseconds(
            candle.timestamp, field_name="candle.timestamp"
        )
        if not is_timeframe_aligned(candle_ms, timeframe):
            raise ValueError("every candle timestamp must align to the timeframe")
        # Touch semantics read only high/low, so an inverted or non-finite
        # range would silently corrupt the terminal state; refuse it instead.
        for name in ("low", "high"):
            price = getattr(candle, name)
            if not isinstance(price, Decimal):
                raise TypeError(f"candle {name} must be a Decimal")
            if not price.is_finite():
                raise ValueError(f"candle {name} must be finite")
        if candle.high < candle.low:
            raise ValueError("candle high must not be below candle low")
        if not start_ms <= candle_ms <= end_ms:
            raise ValueError(
                "every candle must lie inside the observation window "
                "[plan.as_of, observed_through]; future or earlier candles are "
                "never used"
            )
        if candle_ms in present:
            raise ValueError("candles must not repeat an open time")
        present[candle_ms] = candle

    positions: list[int] = []
    cursor = start_ms
    while cursor <= end_ms:
        positions.append(cursor)
        cursor += interval_ms

    gaps: list[CandleGap] = []
    first_gap_index: int | None = None
    run_start: int | None = None
    run_end: int | None = None
    run_count = 0
    for index, position in enumerate(positions):
        if position in present:
            if run_start is not None:
                gaps.append(_gap(run_start, run_end, run_count))
                run_start = run_end = None
                run_count = 0
            continue
        if first_gap_index is None:
            first_gap_index = index
        if run_start is None:
            run_start = position
        run_end = position
        run_count += 1
    if run_start is not None:
        gaps.append(_gap(run_start, run_end, run_count))

    stop_index = len(positions) if first_gap_index is None else first_gap_index
    result = _evaluate(
        journal_id=journal_id,
        levels=levels,
        present=present,
        timeframe=timeframe,
        positions=positions,
        stop_index=stop_index,
        window_start=window_start,
        cutoff=cutoff,
        gaps=tuple(gaps),
        config=config,
        resolution_by_htf_open=resolution_by_htf_open,
    )
    return result


def _gap(start_ms: int, end_ms: int, missing_count: int) -> CandleGap:
    assert end_ms is not None
    return CandleGap(
        start=milliseconds_to_datetime(start_ms),
        end=milliseconds_to_datetime(end_ms),
        missing_count=missing_count,
    )


@dataclass(frozen=True)
class _MinuteGroup:
    """One ordered touch group established by one 1-minute candle."""

    bar_open: datetime
    touches: tuple[tuple[OutcomeEventKind, int | None, Decimal], ...]
    ordering: OutcomeEventOrdering


@dataclass(frozen=True)
class _MinuteResolutionOutcome:
    """The deterministic result of consulting the 1-minute evidence for one
    ambiguous higher-timeframe candle.

    ``kind`` is ``"unavailable"`` (missing/incomplete/inconsistent evidence —
    the caller keeps the exact journal-outcome-v1 AMBIGUOUS record),
    ``"ambiguous"`` (the evidence exists but the ordering is still unknowable
    at 1-minute granularity — the caller records AMBIGUOUS from the state the
    walk established) or ``"resolved"`` (the evidence ordered the events; the
    trajectory continues or terminates on ``terminal``).
    """

    kind: str
    reason: str
    expected_count: int
    present_count: int
    missing_ranges: tuple[CandleGap, ...]
    groups: tuple[_MinuteGroup, ...] = ()
    ambiguity_kind: str | None = None
    ambiguity_timestamp: datetime | None = None
    entry_newly_ordered: bool = False
    entry_bar_open: datetime | None = None
    entry_touched_ambiguous: bool = False
    stop_touched: bool = False
    stop_pre_entry: bool = False
    stop_bar_open: datetime | None = None
    new_reached_targets: tuple[int, ...] = ()
    new_pre_entry_targets: tuple[int, ...] = ()
    terminal: OutcomeStatus | None = None


def _minute_missing_ranges(
    missing_opens: list[int], minute_ms: int
) -> tuple[CandleGap, ...]:
    """Consolidate missing 1-minute open times into inclusive gap ranges."""

    ranges: list[CandleGap] = []
    run_start: int | None = None
    run_end: int | None = None
    run_count = 0
    for open_ms in missing_opens:
        if run_start is None or open_ms != run_end + minute_ms:  # type: ignore[operator]
            if run_start is not None:
                ranges.append(_gap(run_start, run_end, run_count))  # type: ignore[arg-type]
            run_start = open_ms
            run_count = 0
        run_end = open_ms
        run_count += 1
    if run_start is not None:
        ranges.append(_gap(run_start, run_end, run_count))  # type: ignore[arg-type]
    return tuple(ranges)


def _attempt_minute_resolution(
    *,
    levels: ProposedPlanLevels,
    htf_candle: Candle,
    minutes: Sequence[Candle],
    entry_ordered_start: bool,
    reached_start: Sequence[int],
) -> _MinuteResolutionOutcome:
    """Order one ambiguous higher-timeframe candle from its 1-minute candles.

    Applies exactly the same touch, ordering, pre-entry and
    never-pick-the-winner rules as the higher-timeframe walk, at 1-minute
    granularity, inside the one ambiguous candle. The plan levels are used
    unchanged: the 1-minute series can only order events the higher-timeframe
    candle already evidenced, never re-plan them. Any missing minute, any
    conflict between the 1-minute series and the higher-timeframe candle, or
    any same-minute co-touch keeps the result explicitly unresolved.
    """

    interval_ms = timeframe_to_milliseconds(levels.timeframe)
    minute_ms = timeframe_to_milliseconds(RESOLUTION_TIMEFRAME)
    expected_count = interval_ms // minute_ms
    htf_open_ms = datetime_to_milliseconds(htf_candle.timestamp)
    expected_opens = [htf_open_ms + step * minute_ms for step in range(expected_count)]
    present_minutes = {
        datetime_to_milliseconds(minute.timestamp): minute for minute in minutes
    }
    missing_opens = [
        open_ms for open_ms in expected_opens if open_ms not in present_minutes
    ]
    if missing_opens:
        return _MinuteResolutionOutcome(
            kind="unavailable",
            reason=(
                RESOLUTION_NO_CANDLES
                if len(present_minutes) == 0
                else RESOLUTION_COVERAGE_INCOMPLETE
            ),
            expected_count=expected_count,
            present_count=len(present_minutes),
            missing_ranges=_minute_missing_ranges(missing_opens, minute_ms),
        )

    ordered = [present_minutes[open_ms] for open_ms in expected_opens]
    # Integrity guard: the genuine 1-minute series must compose exactly into
    # the higher-timeframe candle it claims to refine. A conflict means the
    # two stored series cannot both witness the same price path, so no
    # ordering is taken from them.
    if (
        ordered[0].open != htf_candle.open
        or ordered[-1].close != htf_candle.close
        or max(bar.high for bar in ordered) != htf_candle.high
        or min(bar.low for bar in ordered) != htf_candle.low
    ):
        return _MinuteResolutionOutcome(
            kind="unavailable",
            reason=RESOLUTION_CONSISTENCY_CONFLICT,
            expected_count=expected_count,
            present_count=len(present_minutes),
            missing_ranges=(),
        )

    long = levels.direction == "bullish"
    entry = levels.entry
    stop = levels.stop
    targets = levels.targets
    target_count = len(targets)

    groups: list[_MinuteGroup] = []
    entry_reached = entry_ordered_start
    entry_ordered = entry_ordered_start
    entry_bar_open: datetime | None = None
    stop_reached = False
    stop_bar_open: datetime | None = None
    reached = list(reached_start)
    pre_entry: list[int] = []
    ambiguity_kind: str | None = None
    ambiguity_timestamp: datetime | None = None
    entry_touched_ambiguous = False
    stop_touched_in_ambiguity = False
    terminal: OutcomeStatus | None = None

    for bar in ordered:
        entry_touch = (not entry_reached) and bar.low <= entry <= bar.high
        stop_touch = (not stop_reached) and (
            bar.low <= stop if long else bar.high >= stop
        )
        target_touches = [
            target_index
            for target_index in range(target_count)
            if target_index not in reached
            and (
                bar.high >= targets[target_index]
                if long
                else bar.low <= targets[target_index]
            )
        ]
        target_touch_list = [
            (OutcomeEventKind.TARGET, target_index, targets[target_index])
            for target_index in target_touches
        ]

        if not entry_ordered:
            if entry_touch and (stop_touch or target_touch_list):
                # Same-minute entry and exit: still unknowable at this
                # granularity; the favourable order is never chosen.
                groups.append(
                    _MinuteGroup(
                        bar_open=bar.timestamp,
                        touches=tuple(
                            [(OutcomeEventKind.ENTRY, None, entry)]
                            + (
                                [(OutcomeEventKind.STOP, None, stop)]
                                if stop_touch
                                else []
                            )
                            + target_touch_list
                        ),
                        ordering=OutcomeEventOrdering.AMBIGUOUS,
                    )
                )
                ambiguity_kind = ENTRY_AND_EXIT_SAME_CANDLE
                ambiguity_timestamp = bar.timestamp
                entry_touched_ambiguous = True
                stop_touched_in_ambiguity = stop_touch
                terminal = OutcomeStatus.AMBIGUOUS
                break
            if entry_touch:
                groups.append(
                    _MinuteGroup(
                        bar_open=bar.timestamp,
                        touches=((OutcomeEventKind.ENTRY, None, entry),),
                        ordering=OutcomeEventOrdering.ORDERED,
                    )
                )
                entry_reached = True
                entry_ordered = True
                entry_bar_open = bar.timestamp
                continue
            if stop_touch:
                groups.append(
                    _MinuteGroup(
                        bar_open=bar.timestamp,
                        touches=tuple(
                            [(OutcomeEventKind.STOP, None, stop)]
                            + target_touch_list
                        ),
                        ordering=OutcomeEventOrdering.PRE_ENTRY,
                    )
                )
                pre_entry.extend(target_touches)
                stop_reached = True
                stop_bar_open = bar.timestamp
                terminal = OutcomeStatus.INVALIDATED_BEFORE_ENTRY
                break
            if target_touch_list:
                groups.append(
                    _MinuteGroup(
                        bar_open=bar.timestamp,
                        touches=tuple(target_touch_list),
                        ordering=OutcomeEventOrdering.PRE_ENTRY,
                    )
                )
                pre_entry.extend(target_touches)
                continue
            continue

        if stop_touch and target_touch_list:
            groups.append(
                _MinuteGroup(
                    bar_open=bar.timestamp,
                    touches=tuple(
                        [(OutcomeEventKind.STOP, None, stop)] + target_touch_list
                    ),
                    ordering=OutcomeEventOrdering.AMBIGUOUS,
                )
            )
            ambiguity_kind = STOP_AND_TARGET_SAME_CANDLE
            ambiguity_timestamp = bar.timestamp
            stop_touched_in_ambiguity = True
            terminal = OutcomeStatus.AMBIGUOUS
            break
        if stop_touch:
            groups.append(
                _MinuteGroup(
                    bar_open=bar.timestamp,
                    touches=((OutcomeEventKind.STOP, None, stop),),
                    ordering=OutcomeEventOrdering.ORDERED,
                )
            )
            stop_reached = True
            stop_bar_open = bar.timestamp
            terminal = (
                OutcomeStatus.STOPPED_AFTER_TARGETS if reached else OutcomeStatus.STOPPED
            )
            break
        if target_touch_list:
            groups.append(
                _MinuteGroup(
                    bar_open=bar.timestamp,
                    touches=tuple(target_touch_list),
                    ordering=OutcomeEventOrdering.ORDERED,
                )
            )
            reached.extend(target_touches)
            if len(reached) == target_count:
                terminal = OutcomeStatus.TARGETS_REACHED
                break
            continue

    if terminal is OutcomeStatus.AMBIGUOUS:
        assert ambiguity_kind is not None
        assert ambiguity_timestamp is not None
        return _MinuteResolutionOutcome(
            kind="ambiguous",
            reason=RESOLUTION_SAME_MINUTE_AMBIGUOUS,
            expected_count=expected_count,
            present_count=len(present_minutes),
            missing_ranges=(),
            groups=tuple(groups),
            ambiguity_kind=ambiguity_kind,
            ambiguity_timestamp=ambiguity_timestamp,
            entry_newly_ordered=entry_ordered and not entry_ordered_start,
            entry_bar_open=entry_bar_open,
            entry_touched_ambiguous=entry_touched_ambiguous,
            stop_touched=stop_touched_in_ambiguity,
            stop_bar_open=stop_bar_open if stop_touched_in_ambiguity else None,
            new_reached_targets=tuple(
                index for index in reached if index not in reached_start
            ),
            new_pre_entry_targets=tuple(pre_entry),
        )

    return _MinuteResolutionOutcome(
        kind="resolved",
        reason=RESOLUTION_USED,
        expected_count=expected_count,
        present_count=len(present_minutes),
        missing_ranges=(),
        groups=tuple(groups),
        entry_newly_ordered=entry_ordered and not entry_ordered_start,
        entry_bar_open=entry_bar_open,
        stop_touched=stop_reached,
        stop_pre_entry=terminal is OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        stop_bar_open=stop_bar_open,
        new_reached_targets=tuple(
            index for index in reached if index not in reached_start
        ),
        new_pre_entry_targets=tuple(pre_entry),
        terminal=terminal,
    )


def _evaluate(
    *,
    journal_id: str,
    levels: ProposedPlanLevels,
    present: dict[int, Candle],
    timeframe: str,
    positions: list[int],
    stop_index: int,
    window_start: datetime,
    cutoff: datetime,
    gaps: tuple[CandleGap, ...],
    config: OutcomeParameters,
    resolution_by_htf_open: dict[int, list[Candle]] | None = None,
) -> OutcomeObservation:
    long = levels.direction == "bullish"
    entry = levels.entry
    stop = levels.stop
    targets = levels.targets
    target_count = len(targets)

    events: list[OutcomeEvent] = []
    sequence = 0
    entry_reached = False
    entry_ordered = False
    entry_timestamp: datetime | None = None
    entry_candle_index: int | None = None
    stop_reached = False
    stop_pre_entry = False
    stop_timestamp: datetime | None = None
    reached_targets: list[int] = []
    pre_entry_targets: list[int] = []
    ambiguous = False
    ambiguity_kind: str | None = None
    ambiguity_timestamp: datetime | None = None
    terminal: OutcomeStatus | None = None
    evaluated_candle_count = 0
    evaluated_low: Decimal | None = None
    evaluated_low_timestamp: datetime | None = None
    evaluated_high: Decimal | None = None
    evaluated_high_timestamp: datetime | None = None

    resolution_enabled = config.rules_version == OUTCOME_RESOLUTION_RULES_VERSION
    resolution_source = resolution_by_htf_open if resolution_enabled else {}
    resolution_attempted = False
    resolution_used = False
    resolution_reason: str | None = None
    resolution_expected_total = 0
    resolution_present_total = 0
    resolution_missing_ranges: list[CandleGap] = []

    def touch_group(
        candle_index: int,
        timestamp: datetime,
        touches: list[tuple[OutcomeEventKind, int | None, Decimal]],
        ordering: OutcomeEventOrdering,
    ) -> None:
        nonlocal sequence
        for kind, target_index, level in sorted(touches, key=_touch_sort_key):
            events.append(
                OutcomeEvent(
                    sequence=sequence,
                    kind=kind,
                    target_index=target_index,
                    level=level,
                    candle_timestamp=timestamp,
                    candle_index=candle_index,
                    ordering=ordering,
                    co_touched=len(touches) > 1,
                )
            )
        sequence += 1

    def consult_minute_resolution(
        *,
        index: int,
        candle: Candle,
        entry_ordered_start: bool,
        reached_start: tuple[int, ...],
    ) -> _MinuteResolutionOutcome | None:
        """Consult the 1-minute evidence for one ambiguous candle.

        Returns ``None`` when the resolution policy is not active for this
        observation rules version (the caller then applies the exact
        journal-outcome-v1 AMBIGUOUS record). Otherwise the consultation
        statistics are recorded and the deterministic outcome is returned.
        """

        nonlocal resolution_attempted, resolution_used, resolution_reason
        nonlocal resolution_expected_total, resolution_present_total
        if not resolution_enabled:
            return None
        resolution_attempted = True
        outcome = _attempt_minute_resolution(
            levels=levels,
            htf_candle=candle,
            minutes=tuple(resolution_source.get(positions[index], ())),  # type: ignore[union-attr]
            entry_ordered_start=entry_ordered_start,
            reached_start=reached_start,
        )
        resolution_expected_total += outcome.expected_count
        resolution_present_total += outcome.present_count
        resolution_missing_ranges.extend(outcome.missing_ranges)
        resolution_reason = outcome.reason
        if outcome.kind == "resolved":
            resolution_used = True
        return outcome

    def apply_minute_state(
        index: int, outcome: _MinuteResolutionOutcome
    ) -> None:
        """Apply one minute-resolution outcome to the trajectory state."""

        nonlocal entry_reached, entry_ordered, entry_timestamp, entry_candle_index
        nonlocal stop_reached, stop_pre_entry, stop_timestamp
        for group in outcome.groups:
            touch_group(index, group.bar_open, list(group.touches), group.ordering)
        if outcome.entry_newly_ordered:
            entry_reached = True
            entry_ordered = True
            entry_timestamp = outcome.entry_bar_open
            entry_candle_index = index
        elif outcome.entry_touched_ambiguous:
            # Same-minute ambiguity touches the entry without ordering it,
            # exactly as the higher-timeframe rule does.
            entry_reached = True
            entry_timestamp = outcome.ambiguity_timestamp
            entry_candle_index = index
        if outcome.stop_touched and not stop_reached:
            stop_reached = True
            stop_timestamp = outcome.stop_bar_open
        stop_pre_entry = stop_pre_entry or outcome.stop_pre_entry
        reached_targets.extend(outcome.new_reached_targets)
        pre_entry_targets.extend(outcome.new_pre_entry_targets)

    for index in range(stop_index):
        candle = present[positions[index]]
        timestamp = candle.timestamp
        evaluated_candle_count += 1
        if evaluated_low is None or candle.low < evaluated_low:
            evaluated_low = candle.low
            evaluated_low_timestamp = timestamp
        if evaluated_high is None or candle.high > evaluated_high:
            evaluated_high = candle.high
            evaluated_high_timestamp = timestamp

        entry_touch = (not entry_reached) and candle.low <= entry <= candle.high
        stop_touch = (not stop_reached) and (
            candle.low <= stop if long else candle.high >= stop
        )
        target_touches = [
            target_index
            for target_index in range(target_count)
            if target_index not in reached_targets
            and (
                candle.high >= targets[target_index]
                if long
                else candle.low <= targets[target_index]
            )
        ]
        target_touch_list = [
            (OutcomeEventKind.TARGET, target_index, targets[target_index])
            for target_index in target_touches
        ]

        if not entry_ordered:
            if entry_touch and (stop_touch or target_touch_list):
                # Same-candle entry and exit: the order is unknowable from this
                # candle's own OHLC. journal-outcome-v2 consults genuine stored
                # 1-minute candles for this one candle only; without sufficient
                # evidence the AMBIGUOUS record below stands unchanged.
                consultation = consult_minute_resolution(
                    index=index,
                    candle=candle,
                    entry_ordered_start=False,
                    reached_start=(),
                )
                if consultation is not None and consultation.kind == "resolved":
                    apply_minute_state(index, consultation)
                    if consultation.terminal is not None:
                        terminal = consultation.terminal
                        break
                    continue
                if consultation is not None and consultation.kind == "ambiguous":
                    apply_minute_state(index, consultation)
                    ambiguous = True
                    ambiguity_kind = consultation.ambiguity_kind
                    ambiguity_timestamp = consultation.ambiguity_timestamp
                    terminal = OutcomeStatus.AMBIGUOUS
                    break
                ambiguous = True
                ambiguity_kind = ENTRY_AND_EXIT_SAME_CANDLE
                ambiguity_timestamp = timestamp
                entry_reached = True
                entry_timestamp = timestamp
                entry_candle_index = index
                stop_reached = stop_reached or stop_touch
                stop_timestamp = timestamp if stop_touch else stop_timestamp
                touch_group(
                    index,
                    timestamp,
                    [(OutcomeEventKind.ENTRY, None, entry)]
                    + ([(OutcomeEventKind.STOP, None, stop)] if stop_touch else [])
                    + target_touch_list,
                    OutcomeEventOrdering.AMBIGUOUS,
                )
                terminal = OutcomeStatus.AMBIGUOUS
                break
            if entry_touch:
                touch_group(
                    index,
                    timestamp,
                    [(OutcomeEventKind.ENTRY, None, entry)],
                    OutcomeEventOrdering.ORDERED,
                )
                entry_reached = True
                entry_ordered = True
                entry_timestamp = timestamp
                entry_candle_index = index
                continue
            if stop_touch:
                touch_group(
                    index,
                    timestamp,
                    [(OutcomeEventKind.STOP, None, stop)] + target_touch_list,
                    OutcomeEventOrdering.PRE_ENTRY,
                )
                pre_entry_targets.extend(target_touches)
                stop_reached = True
                stop_pre_entry = True
                stop_timestamp = timestamp
                terminal = OutcomeStatus.INVALIDATED_BEFORE_ENTRY
                break
            if target_touch_list:
                # Touched before the entry (for example a jump over the level):
                # recorded for audit, never counted as a reached target.
                touch_group(
                    index,
                    timestamp,
                    target_touch_list,
                    OutcomeEventOrdering.PRE_ENTRY,
                )
                pre_entry_targets.extend(target_touches)
                continue
            continue

        if stop_touch and target_touch_list:
            # Same-candle stop and target after the entry: unknowable from this
            # candle's own OHLC. journal-outcome-v2 consults genuine stored
            # 1-minute candles for this one candle only; without sufficient
            # evidence the AMBIGUOUS record below stands unchanged.
            consultation = consult_minute_resolution(
                index=index,
                candle=candle,
                entry_ordered_start=True,
                reached_start=tuple(reached_targets),
            )
            if consultation is not None and consultation.kind == "resolved":
                apply_minute_state(index, consultation)
                if consultation.terminal is not None:
                    terminal = consultation.terminal
                    break
                continue
            if consultation is not None and consultation.kind == "ambiguous":
                apply_minute_state(index, consultation)
                ambiguous = True
                ambiguity_kind = consultation.ambiguity_kind
                ambiguity_timestamp = consultation.ambiguity_timestamp
                terminal = OutcomeStatus.AMBIGUOUS
                break
            ambiguous = True
            ambiguity_kind = STOP_AND_TARGET_SAME_CANDLE
            ambiguity_timestamp = timestamp
            stop_reached = True
            stop_timestamp = timestamp
            touch_group(
                index,
                timestamp,
                [(OutcomeEventKind.STOP, None, stop)] + target_touch_list,
                OutcomeEventOrdering.AMBIGUOUS,
            )
            terminal = OutcomeStatus.AMBIGUOUS
            break
        if stop_touch:
            touch_group(
                index,
                timestamp,
                [(OutcomeEventKind.STOP, None, stop)],
                OutcomeEventOrdering.ORDERED,
            )
            stop_reached = True
            stop_timestamp = timestamp
            terminal = (
                OutcomeStatus.STOPPED_AFTER_TARGETS
                if reached_targets
                else OutcomeStatus.STOPPED
            )
            break
        if target_touch_list:
            touch_group(
                index,
                timestamp,
                target_touch_list,
                OutcomeEventOrdering.ORDERED,
            )
            reached_targets.extend(target_touches)
            if len(reached_targets) == target_count:
                terminal = OutcomeStatus.TARGETS_REACHED
                break
            continue

    missing_candle_count = sum(gap.missing_count for gap in gaps)
    incomplete = missing_candle_count > 0
    evaluated_through = (
        milliseconds_to_datetime(positions[evaluated_candle_count - 1])
        if evaluated_candle_count
        else None
    )

    if terminal is None:
        if incomplete:
            terminal = OutcomeStatus.INCOMPLETE_DATA
        elif entry_ordered:
            terminal = OutcomeStatus.OPEN_AT_CUTOFF
        else:
            terminal = OutcomeStatus.ENTRY_NOT_REACHED

    post_entry_low: Decimal | None = None
    post_entry_high: Decimal | None = None
    if entry_ordered and entry_candle_index is not None:
        for index in range(entry_candle_index, evaluated_candle_count):
            candle = present[positions[index]]
            if post_entry_low is None or candle.low < post_entry_low:
                post_entry_low = candle.low
            if post_entry_high is None or candle.high > post_entry_high:
                post_entry_high = candle.high

    mfe_price_move: Decimal | None = None
    mae_price_move: Decimal | None = None
    mfe_r: Decimal | None = None
    mae_r: Decimal | None = None
    if evaluated_low is not None and evaluated_high is not None:
        if long:
            mfe_price_move = max(Decimal(0), evaluated_high - entry)
            mae_price_move = max(Decimal(0), entry - evaluated_low)
        else:
            mfe_price_move = max(Decimal(0), entry - evaluated_low)
            mae_price_move = max(Decimal(0), evaluated_high - entry)
        mfe_r = quantize_derived(mfe_price_move / levels.risk_per_unit)
        mae_r = quantize_derived(mae_price_move / levels.risk_per_unit)

    ordered_targets = tuple(sorted(reached_targets))
    ordering_groups: list[list[str]] = []
    for event in events:
        while len(ordering_groups) <= event.sequence:
            ordering_groups.append([])
        ordering_groups[event.sequence].append(event.token)

    facts: dict[str, object] = {
        "journal_id": journal_id,
        "plan_id": levels.plan_id,
        "setup_id": levels.setup_id,
        "exchange": levels.exchange,
        "symbol": levels.symbol,
        "timeframe": timeframe,
        "direction": levels.direction,
        "entry_level": entry,
        "stop_level": stop,
        "target_levels": targets,
        "risk_per_unit": levels.risk_per_unit,
        "window_start": window_start,
        "observed_through": cutoff,
        "evaluated_through": evaluated_through,
        "status": terminal,
        "entry_reached": entry_reached,
        "entry_ordered": entry_ordered,
        "entry_timestamp": entry_timestamp,
        "entry_candle_index": entry_candle_index,
        "stop_reached": stop_reached,
        "stop_pre_entry": stop_pre_entry,
        "stop_timestamp": stop_timestamp,
        "targets_reached": ordered_targets,
        "targets_pre_entry": tuple(sorted(set(pre_entry_targets))),
        "first_touch_order": tuple(
            tuple(tokens) for tokens in ordering_groups if tokens
        ),
        "events": tuple(events),
        "ambiguous": ambiguous,
        "ambiguity_kind": ambiguity_kind,
        "ambiguity_timestamp": ambiguity_timestamp,
        "incomplete": incomplete,
        "missing_candle_count": missing_candle_count,
        "missing_ranges": gaps,
        "expected_candle_count": len(positions),
        "observed_candle_count": len(present),
        "evaluated_low": evaluated_low,
        "evaluated_low_timestamp": evaluated_low_timestamp,
        "evaluated_high": evaluated_high,
        "evaluated_high_timestamp": evaluated_high_timestamp,
        "post_entry_low": post_entry_low,
        "post_entry_high": post_entry_high,
        "mfe_price_move": mfe_price_move,
        "mae_price_move": mae_price_move,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "config_fingerprint": config.fingerprint(),
        "observation_rules_version": config.rules_version,
    }
    if resolution_enabled:
        # Resolution metadata is recorded only under journal-outcome-v2, so a
        # journal-outcome-v1 observation keeps the exact historical identity
        # and payload it always had.
        resolution_missing_total = sum(
            gap.missing_count for gap in resolution_missing_ranges
        )
        facts.update(
            {
                "resolution_timeframe": RESOLUTION_TIMEFRAME,
                "resolution_attempted": resolution_attempted,
                "resolution_used": resolution_used,
                "resolution_reason": resolution_reason,
                "resolution_expected_candles": resolution_expected_total,
                "resolution_present_candles": resolution_present_total,
                "resolution_missing_candles": resolution_missing_total,
                "resolution_missing_ranges": tuple(resolution_missing_ranges),
            }
        )
    return OutcomeObservation(id=fingerprint("outcome-observation", facts), **facts)
