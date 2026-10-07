"""Higher-timeframe conflict: the deterministic relationship between layers.

The hierarchy never lets a lower timeframe silently overwrite the 4H
interpretation, and it never pretends every disagreement invalidates a trade.
The relationship between the CONTEXT regime and the SETUP direction is one
explicit enum:

* ``ALIGNED`` — the setup direction agrees with the context structure;
* ``COUNTER_TREND`` — the setup direction disagrees with a directional
  context structure. Always flagged and never hidden, but an ordinary
  counter-trend setup stays BELOW PLANNABLE: lower timeframes refine, they
  never override the 4H structure. A counter-trend trade may only become
  eligible in the future with explicit, deterministic evidence that the
  higher-timeframe structure has failed/transitioned AND a specifically
  defined reversal setup satisfying that policy (no such policy exists yet);
* ``NEUTRAL`` — the context is a range: no directional context to agree or
  disagree with;
* ``CONFLICTING`` — the context itself is in transition (conflicting or
  insufficient swing structure): the higher timeframe cannot support a
  complete decision;
* ``UNKNOWN`` — the context or the setup is unavailable, so no relationship
  can be stated.
"""

from __future__ import annotations

from trading_assistant.multi_timeframe.models import (
    ContextLayerSnapshot,
    ContextRegime,
    HierarchyAlignment,
    SetupLayerSnapshot,
)


def evaluate_alignment(
    context: ContextLayerSnapshot, setup: SetupLayerSnapshot
) -> HierarchyAlignment:
    """Classify the context/setup relationship deterministically."""

    if not context.available or context.regime is ContextRegime.UNKNOWN:
        return HierarchyAlignment.UNKNOWN
    if not setup.has_active_setup or setup.direction is None:
        return HierarchyAlignment.UNKNOWN
    if context.regime is ContextRegime.TRANSITION:
        return HierarchyAlignment.CONFLICTING
    if context.regime is ContextRegime.RANGE:
        return HierarchyAlignment.NEUTRAL
    context_bullish = context.regime is ContextRegime.BULLISH_STRUCTURE
    setup_bullish = setup.direction == "bullish"
    if context_bullish == setup_bullish:
        return HierarchyAlignment.ALIGNED
    return HierarchyAlignment.COUNTER_TREND


__all__ = ["evaluate_alignment"]
