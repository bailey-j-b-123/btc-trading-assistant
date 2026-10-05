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
from datetime import datetime
from decimal import Decimal

from trading_assistant.journaling.parameters import (
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
) -> OutcomeObservation:
    """Observe how the market moved relative to one proposed plan.

    ``journal_id`` is the immutable journal record the observation belongs to
    and is part of the observation identity, so two records can never share one
    stored observation row. ``candles`` must contain only candles for the plan's
    instrument/timeframe inside ``[plan.as_of, observed_through]``; anything
    outside that window is refused rather than silently filtered.
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
    )
    return result


def _gap(start_ms: int, end_ms: int, missing_count: int) -> CandleGap:
    assert end_ms is not None
    return CandleGap(
        start=milliseconds_to_datetime(start_ms),
        end=milliseconds_to_datetime(end_ms),
        missing_count=missing_count,
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
                # Same-candle entry and exit: the order is unknowable from OHLC.
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
    return OutcomeObservation(id=fingerprint("outcome-observation", facts), **facts)
