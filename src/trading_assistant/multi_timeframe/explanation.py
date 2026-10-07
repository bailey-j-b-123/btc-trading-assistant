"""Deterministic plain-English explanation of one hierarchy snapshot.

This module renders the hierarchy the way an operator reads it: one line per
layer, one overall line, then what is being waited for and what would
invalidate the view. It is pure string composition over the immutable
:class:`HierarchySnapshot` — every price, level, timestamp, state and reason
comes from the snapshot's deterministic fields. Nothing is invented, rounded,
or inferred; when a fact is absent the sentence says so.
"""

from __future__ import annotations

from trading_assistant.multi_timeframe.models import (
    HierarchyDecision,
    HierarchySnapshot,
    alignment_label,
    confirmation_label,
    decision_label,
    execution_label,
    family_label,
    regime_label,
)


def explain_hierarchy(snapshot: HierarchySnapshot) -> dict[str, object]:
    """Render the deterministic hierarchy explanation for one snapshot.

    Returns a payload with a ``headline`` (the overall decision in plain
    English), one ``ladder`` entry per layer, and the ``waiting_for`` /
    ``invalidated_if`` lists — exactly the deterministic content the dashboard
    and the Step 9 renderer display.
    """

    hierarchy = snapshot.hierarchy
    context_tf = hierarchy.context.timeframe.upper()
    setup_tf = hierarchy.setup.timeframe.upper()
    confirmation_tf = (
        hierarchy.confirmation.timeframe.upper()
        if hierarchy.confirmation is not None
        else "—"
    )
    execution_tf = (
        hierarchy.execution.timeframe.upper() if hierarchy.execution is not None else "—"
    )

    headline = decision_label(snapshot.decision).upper()

    # --- layer sentences ------------------------------------------------------
    context = snapshot.context
    if not context.available:
        context_sentence = (
            f"{context_tf} context is unavailable at this decision time "
            f"({context.reason or 'no stored closed candle window'})."
        )
    else:
        context_sentence = (
            f"{context_tf} structure is {regime_label(context.regime).lower()}"
            + (
                f" (trend {context.trend_direction}, "
                f"{context.confirmed_swing_count} confirmed swing(s))."
                if context.trend_direction is not None
                else "."
            )
        )

    setup = snapshot.setup
    if setup.setup_id is None:
        if setup.is_terminal:
            setup_sentence = (
                f"The {setup_tf} setup ended ({setup.terminal_reason}); there "
                "is no live setup."
            )
        else:
            setup_sentence = f"No {setup_tf} setup is active."
    else:
        family = family_label(setup.family)
        direction = setup.direction or "unknown"
        if setup.setup_state == "QUALIFIED":
            state_text = "is qualified"
        elif setup.setup_state == "WATCH":
            state_text = "is being watched"
        else:
            state_text = f"is {setup.setup_state}"
        setup_sentence = (
            f"A {setup_tf} {family} setup ({direction}) {state_text}"
            + (
                f" around the reference level {setup.reference_band_low}–"
                f"{setup.reference_band_high}."
                if setup.reference_band_low is not None
                else "."
            )
        )

    confirmation = snapshot.confirmation
    confirmation_sentence = (
        f"{confirmation_tf} confirmation is "
        f"{confirmation_label(confirmation.state).lower()}"
        + (f" — {confirmation.reason}." if confirmation.reason else ".")
    )

    execution = snapshot.execution
    execution_sentence = (
        f"{execution_tf} execution is {execution_label(execution.state).lower()}"
        + (f" — {execution.reason}." if execution.reason else ".")
    )

    sentences = [context_sentence, setup_sentence, confirmation_sentence, execution_sentence]
    if (
        snapshot.status == "incomplete"
        and snapshot.decision is not HierarchyDecision.NO_SETUP
    ):
        sentences.append(
            "The recorded evaluation is incomplete: required market data is "
            "missing or stale, so the hierarchy waits for complete/current "
            "data instead of presenting a trade-ready conclusion. Nothing is "
            "inferred about what the missing candles probably contained."
        )
    if snapshot.counter_trend:
        sentences.append(
            f"This setup runs counter to the {context_tf} structure; it is "
            "flagged as counter-trend, not hidden."
        )
        if snapshot.decision is not HierarchyDecision.PLANNABLE:
            sentences.append(
                f"It is blocked below PLANNABLE: an ordinary setup opposing "
                f"the established {context_tf} structure cannot complete the "
                "hierarchy on lower-timeframe signals alone. It may only "
                "become eligible with explicit, deterministic evidence that "
                f"the {context_tf} structure has failed/transitioned, plus a "
                "specifically defined reversal setup satisfying that policy."
            )
    elif snapshot.alignment.value in ("conflicting", "unknown") and setup.setup_id is not None:
        sentences.append(
            f"The {context_tf} context is {alignment_label(snapshot.alignment).lower()}, "
            "so the hierarchy cannot complete yet."
        )

    ladder = [
        {
            "role": "context",
            "timeframe": context.timeframe,
            "label": f"{context_tf} CONTEXT",
            "state": (
                regime_label(context.regime)
                if context.available
                else "Unavailable"
            ),
            "detail": context_sentence,
            "boundary_open": context.boundary_open,
            "boundary_close": context.boundary_close,
            "available": context.available,
        },
        {
            "role": "setup",
            "timeframe": setup.timeframe,
            "label": f"{setup_tf} SETUP",
            "state": (
                (
                    f"{family_label(setup.family)} — "
                    + (
                        "qualified"
                        if setup.setup_state == "QUALIFIED"
                        else "watching"
                    )
                )
                if setup.setup_id is not None
                else "No setup"
            ),
            "detail": setup_sentence,
            "boundary_open": setup.boundary_open,
            "boundary_close": setup.boundary_close,
            "available": setup.available,
        },
        {
            "role": "confirmation",
            "timeframe": confirmation.timeframe,
            "label": f"{confirmation_tf} CONFIRMATION",
            "state": confirmation_label(confirmation.state),
            "detail": confirmation_sentence,
            "boundary_open": confirmation.boundary_open,
            "boundary_close": confirmation.boundary_close,
            "available": True,
        },
        {
            "role": "execution",
            "timeframe": execution.timeframe,
            "label": f"{execution_tf} EXECUTION",
            "state": execution_label(execution.state),
            "detail": execution_sentence,
            "boundary_open": execution.boundary_open,
            "boundary_close": execution.boundary_close,
            "available": True,
        },
    ]

    waiting_for = list(snapshot.waiting_for)
    invalidated_if = list(snapshot.invalidated_if)
    if snapshot.decision.value == "plannable" and not waiting_for:
        waiting_for_text = "Nothing — the complete hierarchy agrees."
    elif waiting_for:
        waiting_for_text = "Waiting for: " + " ".join(waiting_for)
    else:
        waiting_for_text = "Waiting for: new deterministic evidence at a later closed boundary."
    invalidated_if_text = (
        "Invalidated if: " + " ".join(invalidated_if)
        if invalidated_if
        else "Invalidated if: new deterministic evidence contradicts the recorded view."
    )

    return {
        "headline": headline,
        "overall": decision_label(snapshot.decision),
        "decision": snapshot.decision.value,
        "alignment": snapshot.alignment.value,
        "alignment_label": alignment_label(snapshot.alignment),
        "counter_trend": snapshot.counter_trend,
        "ladder": ladder,
        "sentences": sentences,
        "waiting_for": waiting_for,
        "waiting_for_text": waiting_for_text,
        "invalidated_if": invalidated_if,
        "invalidated_if_text": invalidated_if_text,
    }


__all__ = ["explain_hierarchy"]
