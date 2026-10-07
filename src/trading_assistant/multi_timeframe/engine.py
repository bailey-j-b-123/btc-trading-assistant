"""Pure hierarchy evaluation (Step 13, part C): one deterministic snapshot.

:func:`evaluate_hierarchy` is the single decision function of the hierarchy.
It is pure: given the already-produced Step 3 structure snapshot (context
timeframe), the already-produced Step 5 qualification snapshot and frame (setup
timeframe), and the stored lower-timeframe candles, it produces exactly one
immutable :class:`HierarchySnapshot` — no I/O, no clock, no randomness.

Lookahead is prevented structurally, in three independent places:

1. the decision boundary (``resolve_decision_boundary``) only ever selects
   candles whose full interval closed by the decision time;
2. the confirmation/execution windows start at the setup's creation boundary
   and end at the decision time, and both layer evaluators re-validate every
   candle against that window (raising on any violation);
3. the setup snapshot is the existing Step 5 replay at the setup boundary,
   which already enforces closed-candle semantics.
"""

from __future__ import annotations

from datetime import datetime

from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.market_structure.snapshot import MarketStructureSnapshot
from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.boundaries import (
    DecisionBoundary,
)
from trading_assistant.multi_timeframe.confirmation import evaluate_confirmation
from trading_assistant.multi_timeframe.context import build_context_snapshot
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.hierarchy import TimeframeHierarchy
from trading_assistant.multi_timeframe.models import HierarchySnapshot
from trading_assistant.multi_timeframe.setup import (
    build_setup_snapshot,
    select_active_setup,
    setup_ended_at_boundary,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
)


def _window_candles(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    window_start: datetime,
    decision_time: datetime,
) -> tuple[tuple[Candle, ...], int]:
    """Restrict stored candles to the layer window and count missing ones.

    The window is ``[window_start, latest closed open at decision_time]``.
    Candles outside it (earlier than the setup, or not fully closed by the
    decision time) are excluded — never used, never counted as missing. The
    missing count is the number of expected aligned opens inside the window
    that are not stored, so a gapped series is visible.
    """

    interval = interval_for_timeframe(timeframe)
    # The latest aligned open whose full interval closed by the decision time.
    latest_open = latest_closed_candle_open_time(decision_time, timeframe)
    expected_opens = []
    cursor = window_start
    while cursor <= latest_open:
        expected_opens.append(cursor)
        cursor += interval
    stored = {}
    for candle in candles:
        if candle.timeframe != timeframe:
            raise ValueError(
                f"candle timeframe {candle.timeframe!r} does not match {timeframe!r}"
            )
        if candle.timestamp < window_start:
            continue
        if candle.timestamp + interval > decision_time:
            continue
        stored[candle.timestamp] = candle
    ordered = tuple(stored[open_time] for open_time in sorted(stored))
    missing = sum(1 for open_time in expected_opens if open_time not in stored)
    return ordered, missing


def evaluate_hierarchy(
    *,
    hierarchy: TimeframeHierarchy,
    exchange: str,
    symbol: str,
    decision_time: datetime,
    boundary: DecisionBoundary,
    context_structure: MarketStructureSnapshot | None,
    qualification: QualificationSnapshot | None,
    qualification_frame: QualificationFrame | None,
    confirmation_candles: tuple[Candle, ...],
    execution_candles: tuple[Candle, ...],
    strategy_versions: tuple[tuple[str, str], ...],
    structure_parameters: MarketStructureParameters | None = None,
    context_missing_candle_count: int | None = None,
    setup_window_status: str | None = None,
) -> HierarchySnapshot:
    """Evaluate the complete hierarchy at one decision instant.

    The four-layer contract (CONTEXT, SETUP, CONFIRMATION, EXECUTION) is what
    this version evaluates; a hierarchy configuration without the confirmation
    or execution role is reserved for a future version and is rejected here
    loudly instead of being silently evaluated as a weaker system.

    ``context_missing_candle_count`` / ``setup_window_status`` scope the
    context/setup incompleteness signals to the required windows (Component #1
    gate inputs). ``None`` preserves the legacy whole-series behaviour; the
    hierarchy service always supplies the scoped required-window values. The
    confirmation/execution windows are already scoped to the setup's life.
    """

    if hierarchy.confirmation is None or hierarchy.execution is None:
        raise ValueError(
            "the hierarchy evaluation engine implements the four-layer contract "
            "(context, setup, confirmation, execution); this hierarchy lacks a "
            "confirmation or execution timeframe"
        )

    context_boundary = boundary.boundary_for(hierarchy.context.timeframe)
    setup_boundary = boundary.boundary_for(hierarchy.setup.timeframe)
    confirmation_boundary = (
        boundary.boundary_for(hierarchy.confirmation.timeframe)
        if hierarchy.confirmation is not None
        else None
    )
    execution_boundary = (
        boundary.boundary_for(hierarchy.execution.timeframe)
        if hierarchy.execution is not None
        else None
    )

    # --- CONTEXT layer -------------------------------------------------------
    context = build_context_snapshot(
        context_structure,
        boundary=context_boundary,
        missing_candle_count=context_missing_candle_count,
    )

    # --- SETUP layer ----------------------------------------------------------
    selected = select_active_setup(qualification) if qualification is not None else None
    ended = (
        setup_ended_at_boundary(qualification, setup_boundary.candle_close_time)
        if qualification is not None
        else None
    )
    setup = build_setup_snapshot(
        qualification,
        selected=selected,
        ended=ended,
        frame=qualification_frame,
        boundary=setup_boundary,
        window_status=setup_window_status,
    )

    # --- CONFIRMATION / EXECUTION windows -------------------------------------
    window_start = setup.created_at if setup.created_at is not None else None
    if window_start is None:
        confirmation_window: tuple[Candle, ...] = ()
        confirmation_missing = 0
        execution_window: tuple[Candle, ...] = ()
        execution_missing = 0
    else:
        confirmation_window, confirmation_missing = _window_candles(
            confirmation_candles,
            timeframe=confirmation_boundary.timeframe,
            window_start=window_start,
            decision_time=decision_time,
        )
        execution_window, execution_missing = _window_candles(
            execution_candles,
            timeframe=execution_boundary.timeframe,
            window_start=window_start,
            decision_time=decision_time,
        )

    confirmation = evaluate_confirmation(
        setup,
        confirmation_window,
        boundary=confirmation_boundary,
        missing_candle_count=confirmation_missing,
        structure_parameters=structure_parameters,
    )
    execution = evaluate_execution(
        setup,
        confirmation,
        execution_window,
        boundary=execution_boundary,
        missing_candle_count=execution_missing,
    )

    # --- alignment + final gate ------------------------------------------------
    alignment = evaluate_alignment(context, setup)
    decision, reasons, status, counter_trend, waiting_for, invalidated_if = gate_decision(
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
    )

    return HierarchySnapshot(
        hierarchy=hierarchy,
        exchange=exchange,
        symbol=symbol,
        decision_time=decision_time,
        boundary=boundary,
        context=context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
        counter_trend=counter_trend,
        decision=decision,
        status=status,
        reasons=reasons,
        strategy_versions=strategy_versions,
        waiting_for=waiting_for,
        invalidated_if=invalidated_if,
    )


__all__ = ["evaluate_hierarchy"]
