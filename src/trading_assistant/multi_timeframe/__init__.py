"""Step 13 — multi-timeframe hierarchical market intelligence.

The assistant upgrades from essentially single-timeframe decision-making into
ONE deterministic hierarchical decision system:

    4H CONTEXT       — where we are (market regime / major structure)
    1H SETUP         — what we may trade (the primary opportunity)
    15M CONFIRMATION — whether the 1H idea is confirming
    5M EXECUTION     — when the entry may be ready (timing only)

Lower timeframes refine higher-timeframe information; they never override it.
A 5M pattern alone can never create a trade, and a lower timeframe can never
silently overwrite the 4H interpretation — a counter-trend setup is always
explicitly flagged.

Hard boundaries, by construction:

* strict closed-candle semantics at every layer (no lookahead, ever);
* the existing Steps 3-6 remain the only authority on structure, setups and
  plans — this layer adds no threshold and no new indicator;
* every evaluation is an immutable, idempotently recorded observation that
  preserves exactly what the hierarchy knew at the decision time;
* no real money, no orders, no exchange authentication, no private endpoints;
* no balances, positions, leverage, margin, or position sizing;
* no autonomous trading and no strategy/parameter optimisation;
* the system is decision support / paper-forward-testing only.

Paper trading and historical performance do not establish future profitability.
"""

from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.boundaries import (
    DecisionBoundary,
    TimeframeBoundary,
    resolve_decision_boundary,
)
from trading_assistant.multi_timeframe.confirmation import evaluate_confirmation
from trading_assistant.multi_timeframe.context import build_context_snapshot
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.engine import evaluate_hierarchy
from trading_assistant.multi_timeframe.errors import (
    HierarchyConflict,
    HierarchyDataUnavailable,
    HierarchyNotConfigured,
    MultiTimeframeError,
)
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.explanation import explain_hierarchy
from trading_assistant.multi_timeframe.hierarchy import (
    BTC_HIERARCHY,
    ROLE_ORDER,
    TimeframeHierarchy,
    TimeframeRole,
    TimeframeStep,
    default_hierarchy,
)
from trading_assistant.multi_timeframe.ladder import ladder_payload, overall_phrase
from trading_assistant.multi_timeframe.models import (
    PLANNABLE_ALIGNMENTS,
    ConfirmationLayerSnapshot,
    ConfirmationState,
    ContextLayerSnapshot,
    ContextRegime,
    ExecutionLayerSnapshot,
    ExecutionState,
    HierarchyAlignment,
    HierarchyDecision,
    HierarchyObservation,
    HierarchySnapshot,
    LayerEvidence,
    SetupLayerSnapshot,
    alignment_label,
    confirmation_label,
    decision_label,
    execution_label,
    family_label,
    regime_label,
)
from trading_assistant.multi_timeframe.parameters import (
    HIERARCHY_LEDGER_RULES_VERSION,
    HIERARCHY_RULES_VERSION,
    canonical_json,
    fingerprint,
    ledger_fingerprint,
)
from trading_assistant.multi_timeframe.replay import replay_hierarchy
from trading_assistant.multi_timeframe.repository import HierarchyLedgerRepository
from trading_assistant.multi_timeframe.runner import (
    MultiTimeframeRunner,
    run_forever,
    run_single_pass,
)
from trading_assistant.multi_timeframe.service import (
    HIERARCHY_LIMITATIONS,
    MultiTimeframeRunResult,
    MultiTimeframeService,
    RunnerStatus,
)
from trading_assistant.multi_timeframe.setup import (
    build_setup_snapshot,
    reference_band,
    select_active_setup,
    setup_ended_at_boundary,
)
from trading_assistant.multi_timeframe.tables import ForwardHierarchyObservationRow

__all__ = [
    "BTC_HIERARCHY",
    "HIERARCHY_LEDGER_RULES_VERSION",
    "HIERARCHY_LIMITATIONS",
    "HIERARCHY_RULES_VERSION",
    "PLANNABLE_ALIGNMENTS",
    "ROLE_ORDER",
    "ConfirmationLayerSnapshot",
    "ConfirmationState",
    "ContextLayerSnapshot",
    "ContextRegime",
    "DecisionBoundary",
    "ExecutionLayerSnapshot",
    "ExecutionState",
    "ForwardHierarchyObservationRow",
    "HierarchyAlignment",
    "HierarchyConflict",
    "HierarchyDataUnavailable",
    "HierarchyDecision",
    "HierarchyLedgerRepository",
    "HierarchyNotConfigured",
    "HierarchyObservation",
    "HierarchySnapshot",
    "LayerEvidence",
    "MultiTimeframeError",
    "MultiTimeframeRunResult",
    "MultiTimeframeRunner",
    "MultiTimeframeService",
    "RunnerStatus",
    "SetupLayerSnapshot",
    "TimeframeBoundary",
    "TimeframeHierarchy",
    "TimeframeRole",
    "TimeframeStep",
    "alignment_label",
    "build_context_snapshot",
    "build_setup_snapshot",
    "canonical_json",
    "confirmation_label",
    "decision_label",
    "default_hierarchy",
    "evaluate_alignment",
    "evaluate_confirmation",
    "evaluate_execution",
    "evaluate_hierarchy",
    "execution_label",
    "explain_hierarchy",
    "family_label",
    "fingerprint",
    "gate_decision",
    "ladder_payload",
    "ledger_fingerprint",
    "overall_phrase",
    "reference_band",
    "regime_label",
    "replay_hierarchy",
    "resolve_decision_boundary",
    "run_forever",
    "run_single_pass",
    "select_active_setup",
    "setup_ended_at_boundary",
]
