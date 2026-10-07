"""Dashboard ladder payload: the multi-timeframe ladder in plain English.

This is the compact MULTI-TIMEFRAME LADDER the dashboard shows:

    4H CONTEXT        Bearish structure
    ↓
    1H SETUP          Breakout / retest — watching
    ↓
    15M CONFIRMATION  Waiting
    ↓
    5M EXECUTION      Not armed
    ─────────────────────────────
    OVERALL           NO TRADE YET

The payload carries the plain-English labels for instant reading plus the full
deterministic snapshot for audit. Internal enum names are never shown where a
normal trading phrase exists; the raw values stay inside ``snapshot``.
"""

from __future__ import annotations

from typing import Any

from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.multi_timeframe.explanation import explain_hierarchy
from trading_assistant.multi_timeframe.models import (
    HierarchyDecision,
    HierarchySnapshot,
    alignment_label,
    decision_label,
)

#: The one-line overall phrases shown on the dashboard.
_OVERALL_PHRASES = {
    HierarchyDecision.NO_SETUP: "NO TRADE YET",
    HierarchyDecision.WATCH: "WATCHING A SETUP",
    HierarchyDecision.AWAITING_CONFIRMATION: "WAITING FOR CONFIRMATION",
    HierarchyDecision.AWAITING_EXECUTION: "WAITING FOR ENTRY TIMING",
    HierarchyDecision.PLANNABLE: "PLAN READY — HIERARCHY COMPLETE",
    HierarchyDecision.INVALIDATED: "SETUP INVALIDATED",
}


def overall_phrase(decision: HierarchyDecision) -> str:
    return _OVERALL_PHRASES[decision]


#: Deterministic presentation tones (CSS hints only; never evidence).
_TONE_GOOD = "good"
_TONE_WARN = "warn"
_TONE_BAD = "bad"
_TONE_NEUTRAL = "neutral"

#: Per-role state → tone. The state strings are the plain-English labels the
#: ladder already carries; the tone only picks a colour for the dashboard.
_ROLE_TONES: dict[str, dict[str, str]] = {
    "context": {
        "Bullish structure": _TONE_NEUTRAL,
        "Bearish structure": _TONE_NEUTRAL,
        "Range": _TONE_NEUTRAL,
        "Transition": _TONE_WARN,
        "Unavailable": _TONE_BAD,
    },
    "setup": {
        "No setup": _TONE_NEUTRAL,
    },
    "confirmation": {
        "Confirming": _TONE_GOOD,
        "Waiting": _TONE_WARN,
        "Contradicting": _TONE_BAD,
        "Not applicable": _TONE_NEUTRAL,
        "Invalidated": _TONE_BAD,
    },
    "execution": {
        "Armed": _TONE_GOOD,
        "Ready — entry timing met": _TONE_GOOD,
        "Waiting": _TONE_WARN,
        "Not armed": _TONE_NEUTRAL,
        "Invalidated": _TONE_BAD,
    },
}


def _tone_for(role: str, state: str) -> str:
    if role == "setup":
        if state.endswith("— qualified"):
            return _TONE_GOOD
        if state.endswith("— watching"):
            return _TONE_WARN
        return _TONE_NEUTRAL
    return _ROLE_TONES.get(role, {}).get(state, _TONE_NEUTRAL)


def ladder_payload(snapshot: HierarchySnapshot) -> dict[str, Any]:
    """Build the dashboard's multi-timeframe payload from one snapshot."""

    explanation = explain_hierarchy(snapshot)
    ladder = [
        {
            "role": step["role"],
            "timeframe": step["timeframe"],
            "label": step["label"],
            "state": step["state"],
            "tone": _tone_for(step["role"], step["state"]),
            "detail": step["detail"],
            "boundary_open": to_jsonable(step["boundary_open"]),
            "boundary_close": to_jsonable(step["boundary_close"]),
            "available": step["available"],
        }
        for step in explanation["ladder"]  # type: ignore[index]
    ]
    # A blocked counter-trend setup gets an unmistakable overall phrase: the
    # decision enum stays AWAITING_CONFIRMATION, but the dashboard must show
    # exactly why an otherwise-complete lower-timeframe chain cannot complete.
    overall = overall_phrase(snapshot.decision)
    if snapshot.counter_trend and snapshot.decision is not HierarchyDecision.PLANNABLE:
        overall = "COUNTER-TREND SETUP — BLOCKED BELOW PLANNABLE"
    elif (
        snapshot.status == "incomplete"
        and snapshot.decision is not HierarchyDecision.NO_SETUP
    ):
        # Incomplete/stale required market data: the hierarchy is waiting
        # for complete/current data, not presenting a trade-ready conclusion.
        overall = "WAITING FOR COMPLETE MARKET DATA"
    return {
        "available": True,
        "hierarchy": snapshot.hierarchy.to_json_dict(),
        "decision_time": to_jsonable(snapshot.decision_time),
        "decision": snapshot.decision.value,
        "decision_label": decision_label(snapshot.decision),
        "overall": overall,
        "alignment": snapshot.alignment.value,
        "alignment_label": alignment_label(snapshot.alignment),
        "counter_trend": snapshot.counter_trend,
        "status": snapshot.status,
        "ladder": ladder,
        "waiting_for": list(snapshot.waiting_for),
        "waiting_for_text": explanation["waiting_for_text"],  # type: ignore[index]
        "invalidated_if": list(snapshot.invalidated_if),
        "invalidated_if_text": explanation["invalidated_if_text"],  # type: ignore[index]
        "reasons": list(snapshot.reasons),
        "explanation": {
            "headline": explanation["headline"],  # type: ignore[index]
            "sentences": list(explanation["sentences"]),  # type: ignore[index]
        },
        "snapshot": snapshot.to_json_dict(),
        "limitations": (
            "The hierarchy is decision support only: PLANNABLE means the complete "
            "deterministic hierarchy agreed, never that an order exists or that "
            "anything was executed.",
            "Only fully closed candles are analysed; an unfinished candle is "
            "never used at any layer.",
            "Lower timeframes refine the setup layer; they never create a trade "
            "on their own and never overwrite the higher-timeframe context.",
            "A counter-trend setup is explicitly flagged, never hidden, and it "
            "stays below PLANNABLE: an ordinary setup opposing the established "
            "4H structure cannot complete the hierarchy on lower-timeframe "
            "signals alone.",
        ),
    }


__all__ = ["ladder_payload", "overall_phrase"]
