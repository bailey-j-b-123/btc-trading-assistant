"""Final decision gating: one deterministic overall state for the hierarchy.

The gate is strictly hierarchical. A lower timeframe can never create a trade,
upgrade a setup, or overwrite the context:

* ``PLANNABLE`` requires the COMPLETE hierarchy on COMPLETE, CURRENT data:
  an evaluated context, a QUALIFIED setup, a CONFIRMING confirmation, an
  ARMED or TRIGGERED execution, an alignment that permits completion
  (ALIGNED, or NEUTRAL range context), and no stale or missing required
  market data at any layer. A 5M trigger alone never produces PLANNABLE, an
  ordinary COUNTER_TREND setup never produces PLANNABLE, and incomplete or
  stale required market data never produces PLANNABLE: lower timeframes
  refine, they never override established 4H directional structure, and a
  subset of available candles never substitutes for the complete required
  sequence.
* ``WATCH`` — a setup exists but is not QUALIFIED yet; lower layers cannot
  upgrade it.
* ``AWAITING_CONFIRMATION`` — the setup is QUALIFIED but the confirmation layer
  has not confirmed (WAITING or CONTRADICTING), or the alignment cannot
  support a complete decision (COUNTER_TREND / CONFLICTING / UNKNOWN).
* ``AWAITING_EXECUTION`` — the setup is QUALIFIED and CONFIRMING, but the
  execution layer has not reached ARMED/TRIGGERED.
* ``INVALIDATED`` — the setup ended, or a lower layer invalidated the idea.
* ``NO_SETUP`` — the hierarchy evaluated completely and no setup is active.

When the context or setup layer itself is unavailable, the evaluation is
recorded as ``incomplete`` with an explicit reason: a missing candle never
becomes a trading conclusion.
"""

from __future__ import annotations

from typing import Literal

from trading_assistant.multi_timeframe.models import (
    PLANNABLE_ALIGNMENTS,
    ConfirmationLayerSnapshot,
    ConfirmationState,
    ContextLayerSnapshot,
    ExecutionLayerSnapshot,
    ExecutionState,
    HierarchyAlignment,
    HierarchyDecision,
    SetupLayerSnapshot,
)


def gate_decision(
    *,
    context: ContextLayerSnapshot,
    setup: SetupLayerSnapshot,
    confirmation: ConfirmationLayerSnapshot,
    execution: ExecutionLayerSnapshot,
    alignment: HierarchyAlignment,
) -> tuple[
    HierarchyDecision,
    tuple[str, ...],
    Literal["evaluated", "incomplete"],
    bool,
    tuple[str, ...],
    tuple[str, ...],
]:
    """Return ``(decision, reasons, status, counter_trend, waiting_for, invalidated_if)``."""

    reasons: list[str] = []
    waiting_for: list[str] = []
    invalidated_if: list[str] = []
    counter_trend = alignment is HierarchyAlignment.COUNTER_TREND

    # --- layer availability --------------------------------------------------
    incomplete = False
    if not context.available:
        reasons.append("context_layer_unavailable")
        incomplete = True
    if not setup.available:
        reasons.append("setup_layer_unavailable")
        incomplete = True
    if context.stale:
        reasons.append("context_data_stale")
    if context.missing_candle_count:
        reasons.append("context_window_incomplete")
    if setup.stale:
        reasons.append("setup_data_stale")
    if setup.snapshot_status == "incomplete":
        reasons.append("setup_window_incomplete")
    if confirmation.stale:
        reasons.append("confirmation_data_stale")
    if execution.stale:
        reasons.append("execution_data_stale")
    if confirmation.missing_candle_count:
        reasons.append("confirmation_window_incomplete")
    if execution.missing_candle_count:
        reasons.append("execution_window_incomplete")

    # --- invalidation sources -------------------------------------------------
    if setup.invalidation:
        invalidated_if.append(f"the deterministic setup invalidation: {setup.invalidation}")
    if setup.reference_band_low is not None and setup.reference_band_high is not None:
        band = f"{setup.reference_band_low}–{setup.reference_band_high}"
        if setup.direction == "bullish":
            invalidated_if.append(
                f"a closed lower-timeframe candle closing below the setup "
                f"reference level {band}"
            )
        elif setup.direction == "bearish":
            invalidated_if.append(
                f"a closed lower-timeframe candle closing above the setup "
                f"reference level {band}"
            )
    if execution.state is ExecutionState.INVALIDATED:
        invalidated_if.append(execution.reason)
    if confirmation.state is ConfirmationState.INVALIDATED:
        invalidated_if.append(confirmation.reason)

    # --- the gate -------------------------------------------------------------
    if incomplete:
        return (
            HierarchyDecision.NO_SETUP,
            tuple(dict.fromkeys(reasons)),
            "incomplete",
            counter_trend,
            (
                "stored closed candles for the context and setup timeframes "
                "at the decision time",
            ),
            tuple(invalidated_if),
        )

    if not setup.has_active_setup:
        if setup.is_terminal:
            reasons.append(f"setup_terminal:{setup.terminal_reason}")
            return (
                HierarchyDecision.INVALIDATED,
                tuple(dict.fromkeys(reasons)),
                "evaluated",
                counter_trend,
                (),
                tuple(invalidated_if),
            )
        reasons.append("no_active_setup_on_the_setup_timeframe")
        return (
            HierarchyDecision.NO_SETUP,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            (
                "a fresh setup seed on the setup timeframe (breakout, failed "
                "breakout, or liquidity sweep)",
            ),
            tuple(invalidated_if),
        )

    if not setup.is_qualified:
        # WATCH: the setup exists but its own rules are not satisfied. Lower
        # timeframes can never upgrade it, whatever they show.
        reasons.append(f"setup_state:{setup.setup_state}")
        if setup.next_required:
            waiting_for.extend(setup.next_required)
            reasons.append("setup_rules_pending")
        if setup.opposing_rules:
            reasons.append("setup_rules_opposing:" + ",".join(setup.opposing_rules))
        return (
            HierarchyDecision.WATCH,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(dict.fromkeys(waiting_for)),
            tuple(invalidated_if),
        )

    # QUALIFIED setup: the lower layers may now refine, never create.
    # An execution-layer invalidation (the setup's level lost on a closed
    # lower-timeframe candle) ends the idea outright, even when the
    # confirmation layer only reports a disagreement: both layers observed the
    # same lost level on closed candles.
    if execution.state is ExecutionState.INVALIDATED:
        reasons.append("execution_invalidated")
        return (
            HierarchyDecision.INVALIDATED,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            (),
            tuple(invalidated_if),
        )
    if confirmation.state is ConfirmationState.INVALIDATED:
        reasons.append("confirmation_invalidated")
        return (
            HierarchyDecision.INVALIDATED,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            (),
            tuple(invalidated_if),
        )
    if confirmation.state is ConfirmationState.CONTRADICTING:
        reasons.append("confirmation_contradicting")
        waiting_for.append(
            f"{confirmation.timeframe} price action to stop contradicting the "
            "setup (the reference level must hold on closed candles)"
        )
        return (
            HierarchyDecision.AWAITING_CONFIRMATION,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(waiting_for),
            tuple(invalidated_if),
        )
    if confirmation.state is ConfirmationState.WAITING:
        reasons.append("confirmation_waiting")
        if setup.direction == "bullish":
            waiting_for.append(
                f"{confirmation.timeframe} acceptance of the setup's reference "
                "level (a closed candle beyond the band, level held)"
            )
        elif setup.direction == "bearish":
            waiting_for.append(
                f"{confirmation.timeframe} acceptance of the setup's reference "
                "level (a closed candle beyond the band, level held)"
            )
        else:
            waiting_for.append(
                f"{confirmation.timeframe} confirmation of the setup idea"
            )
        return (
            HierarchyDecision.AWAITING_CONFIRMATION,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(waiting_for),
            tuple(invalidated_if),
        )

    # Confirmation is CONFIRMING.
    if alignment is HierarchyAlignment.COUNTER_TREND:
        # An ordinary setup opposing established 4H directional structure
        # must NOT reach PLANNABLE merely because the lower layers produced
        # confirmation/entry signals. It stays below PLANNABLE — visibly —
        # until explicit, deterministic evidence shows the higher-timeframe
        # structure has failed/transitioned AND a specifically defined
        # reversal setup satisfies that policy. No such reversal policy
        # exists in the deterministic system yet, so this path always
        # blocks. The flag and the reason stay recorded, never hidden.
        reasons.append("counter_trend_blocked_below_plannable")
        waiting_for.append(
            f"explicit deterministic evidence that the {context.timeframe} "
            "structure has failed/transitioned, plus a specifically defined "
            "reversal setup satisfying that policy (an ordinary counter-trend "
            "setup stays below PLANNABLE)"
        )
        return (
            HierarchyDecision.AWAITING_CONFIRMATION,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(waiting_for),
            tuple(invalidated_if),
        )
    if alignment not in PLANNABLE_ALIGNMENTS:
        reasons.append(f"alignment_not_plannable:{alignment.value}")
        waiting_for.append(
            f"the {context.timeframe} context to resolve into a regime that "
            "can support a complete decision (currently "
            f"{alignment.value})"
        )
        return (
            HierarchyDecision.AWAITING_CONFIRMATION,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(waiting_for),
            tuple(invalidated_if),
        )

    # --- required-data quality gate ------------------------------------------
    # INCOMPLETE OR STALE REQUIRED MARKET DATA MUST NEVER PRODUCE PLANNABLE
    # (or any completion): a subset of available candles never silently
    # substitutes for the complete required confirmation/execution sequence,
    # and no missing candle is ever fabricated or inferred. The evaluation is
    # recorded as incomplete — the hierarchy is waiting for complete/current
    # data — and the exact data-quality problem stays visible in the reasons
    # and the waiting-for items. Once the complete closed-candle sequence is
    # genuinely available, normal deterministic evaluation may proceed (the
    # runner retries boundaries recorded as incomplete).
    data_reasons, data_waiting, data_decision = _data_quality_issues(
        context, setup, confirmation, execution
    )
    if data_decision is not None:
        reasons.extend(data_reasons)
        waiting_for.extend(data_waiting)
        return (
            data_decision,
            tuple(dict.fromkeys(reasons)),
            "incomplete",
            counter_trend,
            tuple(dict.fromkeys(waiting_for)),
            tuple(invalidated_if),
        )

    if execution.state in (ExecutionState.ARMED, ExecutionState.TRIGGERED):
        reasons.append("hierarchy_complete")
        return (
            HierarchyDecision.PLANNABLE,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            (),
            tuple(invalidated_if),
        )
    if execution.state is ExecutionState.WAITING:
        reasons.append("execution_waiting")
        waiting_for.append(
            f"{execution.timeframe} price to reach the entry zone (the setup's "
            "reference band) and hold the setup's level"
        )
        return (
            HierarchyDecision.AWAITING_EXECUTION,
            tuple(dict.fromkeys(reasons)),
            "evaluated",
            counter_trend,
            tuple(waiting_for),
            tuple(invalidated_if),
        )
    # NOT_ARMED with a confirming confirmation cannot happen (the execution
    # layer evaluates whenever its prerequisites are met); stay honest.
    reasons.append("execution_not_armed")
    waiting_for.append(execution.reason)
    return (
        HierarchyDecision.AWAITING_EXECUTION,
        tuple(dict.fromkeys(reasons)),
        "evaluated",
        counter_trend,
        tuple(waiting_for),
        tuple(invalidated_if),
    )


def _data_quality_issues(
    context: ContextLayerSnapshot,
    setup: SetupLayerSnapshot,
    confirmation: ConfirmationLayerSnapshot,
    execution: ExecutionLayerSnapshot,
) -> tuple[tuple[str, ...], tuple[str, ...], HierarchyDecision | None]:
    """Assess required-market-data quality for the completion path.

    Returns ``(reason_codes, waiting_for_items, blocking_decision)``. The
    blocking decision is ``AWAITING_CONFIRMATION`` when the context, setup or
    confirmation data is stale/incomplete, ``AWAITING_EXECUTION`` when only the
    execution data is, and ``None`` when every required layer rests on
    complete, current closed candles. A subset of available candles never
    substitutes for the complete required sequence, and nothing is inferred
    about what missing candles probably contained.
    """

    reasons: list[str] = []
    waiting: list[str] = []
    confirmation_side = False
    execution_side = False

    if context.stale:
        reasons.append("context_data_stale")
        waiting.append(
            f"the latest closed {context.timeframe} context candle "
            f"(stored {context.timeframe} data is stale)"
        )
        confirmation_side = True
    elif context.missing_candle_count:
        reasons.append("context_window_incomplete")
        waiting.append(
            f"{context.missing_candle_count} missing closed "
            f"{context.timeframe} context candle(s)"
        )
        confirmation_side = True

    if setup.stale:
        reasons.append("setup_data_stale")
        waiting.append(
            f"the latest closed {setup.timeframe} setup candle "
            f"(stored {setup.timeframe} data is stale)"
        )
        confirmation_side = True
    elif setup.snapshot_status == "incomplete":
        reasons.append("setup_window_incomplete")
        waiting.append(
            f"complete closed {setup.timeframe} setup-window candles "
            "(the Step 5 snapshot reports an incomplete source window)"
        )
        confirmation_side = True

    if confirmation.stale:
        reasons.append("confirmation_data_stale")
        waiting.append(
            f"the latest closed {confirmation.timeframe} confirmation candle "
            f"(stored {confirmation.timeframe} data is stale)"
        )
        confirmation_side = True
    elif confirmation.missing_candle_count:
        reasons.append("confirmation_window_incomplete")
        waiting.append(
            f"{confirmation.missing_candle_count} missing closed "
            f"{confirmation.timeframe} candle(s) inside the confirmation window"
        )
        confirmation_side = True

    if execution.stale:
        reasons.append("execution_data_stale")
        waiting.append(
            f"the latest closed {execution.timeframe} execution candle "
            f"(stored {execution.timeframe} data is stale)"
        )
        execution_side = True
    elif execution.missing_candle_count:
        reasons.append("execution_window_incomplete")
        waiting.append(
            f"{execution.missing_candle_count} missing closed "
            f"{execution.timeframe} candle(s) inside the execution window"
        )
        execution_side = True

    if confirmation_side:
        return tuple(reasons), tuple(waiting), HierarchyDecision.AWAITING_CONFIRMATION
    if execution_side:
        return tuple(reasons), tuple(waiting), HierarchyDecision.AWAITING_EXECUTION
    return tuple(reasons), tuple(waiting), None


__all__ = ["gate_decision"]
