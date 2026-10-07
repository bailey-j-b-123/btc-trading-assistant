"""Immutable Step 13 contracts: hierarchy layer snapshots and decisions.

Every layer of the hierarchy produces one frozen, JSON-serializable snapshot.
The final :class:`HierarchySnapshot` is the single deterministic record of one
hierarchy evaluation: what each timeframe knew at the decision instant, how the
layers relate, and the one overall decision the hierarchy produced.

Nothing here is an order, a fill, a position, or an execution instruction. The
hierarchy is decision support only: ``PLANNABLE`` means the *complete*
hierarchy agreed, never that a 5M trigger alone created a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.multi_timeframe.boundaries import DecisionBoundary
from trading_assistant.multi_timeframe.hierarchy import TimeframeHierarchy
from trading_assistant.multi_timeframe.parameters import (
    canonical_json,
    ledger_fingerprint,
)
from trading_assistant.setup_qualification.models import EvidenceStatus, SetupState


# ---------------------------------------------------------------------------
# Layer state vocabularies (explicit enums, never free-form strings)
# ---------------------------------------------------------------------------


class ContextRegime(StrEnum):
    """What the CONTEXT layer says about where the market is."""

    BULLISH_STRUCTURE = "bullish_structure"
    BEARISH_STRUCTURE = "bearish_structure"
    RANGE = "range"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class ConfirmationState(StrEnum):
    """Whether the CONFIRMATION layer confirms the active SETUP-layer idea."""

    NOT_APPLICABLE = "not_applicable"
    WAITING = "waiting"
    CONFIRMING = "confirming"
    CONTRADICTING = "contradicting"
    INVALIDATED = "invalidated"


class ExecutionState(StrEnum):
    """Whether the EXECUTION layer is ready to refine an entry (never to place one)."""

    NOT_ARMED = "not_armed"
    WAITING = "waiting"
    ARMED = "armed"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"


class HierarchyAlignment(StrEnum):
    """The deterministic relationship between CONTEXT and SETUP layers."""

    ALIGNED = "aligned"
    COUNTER_TREND = "counter_trend"
    NEUTRAL = "neutral"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class HierarchyDecision(StrEnum):
    """The one overall decision the complete hierarchy produces."""

    NO_SETUP = "no_setup"
    WATCH = "watch"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_EXECUTION = "awaiting_execution"
    PLANNABLE = "plannable"
    INVALIDATED = "invalidated"


#: Alignment values under which a QUALIFIED setup may still complete the
#: hierarchy. ``ALIGNED`` proceeds normally; ``NEUTRAL`` (range context) keeps
#: its intended behaviour. ``COUNTER_TREND`` is deliberately EXCLUDED: an
#: ordinary setup opposing established 4H directional structure must never
#: reach PLANNABLE merely because the lower layers produced confirmation/entry
#: signals — lower timeframes refine, they never override the 4H structure.
#: A counter-trend trade may only become eligible in the future if there is
#: explicit, deterministic evidence that the higher-timeframe structure has
#: failed/transitioned AND a specifically defined reversal setup satisfies that
#: policy. No such reversal policy exists in the deterministic system yet, so
#: counter-trend setups stay below PLANNABLE (always flagged, never hidden).
#: ``CONFLICTING`` and ``UNKNOWN`` mean the higher-timeframe context cannot
#: support a complete decision, so the hierarchy stops below PLANNABLE.
PLANNABLE_ALIGNMENTS = frozenset(
    {
        HierarchyAlignment.ALIGNED,
        HierarchyAlignment.NEUTRAL,
    }
)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayerEvidence:
    """One deterministic evidence item produced by one hierarchy layer."""

    category: str
    status: EvidenceStatus
    reason: str
    timeframe: str | None = None
    observed_at: datetime | None = None

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


# ---------------------------------------------------------------------------
# Layer snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextLayerSnapshot:
    """The CONTEXT layer: where the market is, from existing structure only."""

    timeframe: str
    decision_time: datetime
    boundary_open: datetime
    boundary_close: datetime
    available: bool
    reason: str | None
    regime: ContextRegime
    trend_direction: str | None
    trend_reason: str | None
    confirmed_swing_count: int
    active_range_low: Decimal | None
    active_range_high: Decimal | None
    nearest_support_band_low: Decimal | None
    nearest_support_band_high: Decimal | None
    nearest_resistance_band_low: Decimal | None
    nearest_resistance_band_high: Decimal | None
    latest_close: Decimal | None
    candle_count: int
    missing_candle_count: int
    stale: bool
    evidence: tuple[LayerEvidence, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class SetupLayerSnapshot:
    """The SETUP layer: what may be traded, from the existing Step 5 authority.

    The setup layer reuses the existing setup-qualification engine unchanged.
    It exposes the active setup's family, direction, status, relevant
    level/zone, supporting evidence, opposing evidence, invalidation, and what
    must happen next — all copied from the deterministic Step 5 result.
    """

    timeframe: str
    decision_time: datetime
    boundary_open: datetime
    boundary_close: datetime
    available: bool
    reason: str | None
    state: SetupState | None
    snapshot_status: str | None
    snapshot_id: str | None
    rules_version: str | None
    config_fingerprint: str | None
    setup_id: str | None
    family: str | None
    direction: str | None
    setup_state: str | None
    created_at: datetime | None
    terminal_reason: str | None
    ended_at: datetime | None
    reference_id: str | None
    reference_band_low: Decimal | None
    reference_band_high: Decimal | None
    supporting_rules: tuple[str, ...]
    opposing_rules: tuple[str, ...]
    pending_rules: tuple[str, ...]
    invalidation: str | None
    next_required: tuple[str, ...]
    candidate_count: int
    stale: bool

    @property
    def has_active_setup(self) -> bool:
        return self.setup_id is not None and self.setup_state in (
            SetupState.WATCH.value,
            SetupState.QUALIFIED.value,
        )

    @property
    def is_qualified(self) -> bool:
        return self.setup_state == SetupState.QUALIFIED.value

    @property
    def is_terminal(self) -> bool:
        return (
            self.setup_id is not None
            and self.setup_state == SetupState.NO_SETUP.value
            and self.ended_at is not None
        )

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ConfirmationLayerSnapshot:
    """The CONFIRMATION layer: whether lower-timeframe action confirms the 1H idea."""

    timeframe: str
    decision_time: datetime
    boundary_open: datetime
    boundary_close: datetime
    state: ConfirmationState
    reason: str
    window_start: datetime | None
    window_end: datetime | None
    candle_count: int
    missing_candle_count: int
    stale: bool
    evidence: tuple[LayerEvidence, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ExecutionLayerSnapshot:
    """The EXECUTION layer: entry refinement timing only (never an order)."""

    timeframe: str
    decision_time: datetime
    boundary_open: datetime
    boundary_close: datetime
    state: ExecutionState
    reason: str
    window_start: datetime | None
    window_end: datetime | None
    candle_count: int
    missing_candle_count: int
    stale: bool
    armed_at: datetime | None
    trigger_at: datetime | None
    entry_zone_low: Decimal | None
    entry_zone_high: Decimal | None
    latest_close: Decimal | None
    evidence: tuple[LayerEvidence, ...]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


# ---------------------------------------------------------------------------
# The complete hierarchy snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HierarchySnapshot:
    """One immutable, reproducible multi-timeframe hierarchy evaluation.

    ``decision_time`` is the instant the hierarchy was evaluated at. Each layer
    snapshot carries its own closed-candle boundary, so the exact information
    known at the decision is reproducible later. ``identity`` is the
    deterministic fingerprint of the whole snapshot (excluding write-time
    metadata), which is what makes ledger recording idempotent across restarts.
    """

    hierarchy: TimeframeHierarchy
    exchange: str
    symbol: str
    decision_time: datetime
    boundary: DecisionBoundary
    context: ContextLayerSnapshot
    setup: SetupLayerSnapshot
    confirmation: ConfirmationLayerSnapshot
    execution: ExecutionLayerSnapshot
    alignment: HierarchyAlignment
    counter_trend: bool
    decision: HierarchyDecision
    status: Literal["evaluated", "incomplete"]
    reasons: tuple[str, ...]
    strategy_versions: tuple[tuple[str, str], ...]
    waiting_for: tuple[str, ...]
    invalidated_if: tuple[str, ...]

    @property
    def hierarchy_fingerprint(self) -> str:
        return self.hierarchy.fingerprint()

    @property
    def rules_version(self) -> str:
        return self.hierarchy.rules_version

    def identity(self) -> str:
        """Deterministic identity of this exact evaluation.

        Deliberately includes the hierarchy fingerprint and rules version: a
        different hierarchy configuration must never share a ledger row with
        an older one. Write-time metadata (``recorded_at``) is excluded on
        purpose so a restart at the same boundary reproduces the identity.
        """

        return ledger_fingerprint(
            "hierarchy-observation",
            self.exchange,
            self.symbol,
            self.decision_time,
            self.hierarchy_fingerprint,
            self.rules_version,
            self.status,
            self.alignment.value,
            self.counter_trend,
            self.decision.value,
            canonical_json(self.to_json_dict()),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "hierarchy": self.hierarchy.to_json_dict(),
            "exchange": self.exchange,
            "symbol": self.symbol,
            "decision_time": to_jsonable(self.decision_time),
            "boundary": self.boundary.to_json_dict(),
            "context": self.context.to_json_dict(),
            "setup": self.setup.to_json_dict(),
            "confirmation": self.confirmation.to_json_dict(),
            "execution": self.execution.to_json_dict(),
            "alignment": self.alignment.value,
            "counter_trend": self.counter_trend,
            "decision": self.decision.value,
            "status": self.status,
            "reasons": list(self.reasons),
            "strategy_versions": [
                {"name": name, "version": version}
                for name, version in self.strategy_versions
            ],
            "waiting_for": list(self.waiting_for),
            "invalidated_if": list(self.invalidated_if),
        }


# ---------------------------------------------------------------------------
# Plain-English labels (presentation only; never evidence)
# ---------------------------------------------------------------------------

_FAMILY_LABELS = {
    "breakout_retest_continuation": "breakout / retest",
    "failed_breakout_sweep_reversal": "failed breakout / sweep reversal",
    "range_rejection_reversal": "range rejection reversal",
}


def family_label(family: str | None) -> str:
    if family is None:
        return "setup"
    return _FAMILY_LABELS.get(family, family.replace("_", " "))


def regime_label(regime: ContextRegime) -> str:
    return {
        ContextRegime.BULLISH_STRUCTURE: "Bullish structure",
        ContextRegime.BEARISH_STRUCTURE: "Bearish structure",
        ContextRegime.RANGE: "Range-bound",
        ContextRegime.TRANSITION: "Transition / uncertain",
        ContextRegime.UNKNOWN: "Unknown",
    }[regime]


def confirmation_label(state: ConfirmationState) -> str:
    return {
        ConfirmationState.NOT_APPLICABLE: "Not applicable",
        ConfirmationState.WAITING: "Waiting",
        ConfirmationState.CONFIRMING: "Confirming",
        ConfirmationState.CONTRADICTING: "Contradicting",
        ConfirmationState.INVALIDATED: "Invalidated",
    }[state]


def execution_label(state: ExecutionState) -> str:
    return {
        ExecutionState.NOT_ARMED: "Not armed",
        ExecutionState.WAITING: "Waiting",
        ExecutionState.ARMED: "Armed — entry zone reached",
        ExecutionState.TRIGGERED: "Triggered — entry timing ready",
        ExecutionState.INVALIDATED: "Invalidated",
    }[state]


def alignment_label(alignment: HierarchyAlignment) -> str:
    return {
        HierarchyAlignment.ALIGNED: "Aligned with the higher-timeframe context",
        HierarchyAlignment.COUNTER_TREND: "Counter-trend vs the higher-timeframe context",
        HierarchyAlignment.NEUTRAL: "Neutral higher-timeframe context",
        HierarchyAlignment.CONFLICTING: "Higher-timeframe context conflicting",
        HierarchyAlignment.UNKNOWN: "Unknown",
    }[alignment]


def decision_label(decision: HierarchyDecision) -> str:
    return {
        HierarchyDecision.NO_SETUP: "No trade yet",
        HierarchyDecision.WATCH: "Watching a setup",
        HierarchyDecision.AWAITING_CONFIRMATION: "Waiting for lower-timeframe confirmation",
        HierarchyDecision.AWAITING_EXECUTION: "Waiting for entry timing",
        HierarchyDecision.PLANNABLE: "Plan ready — hierarchy complete",
        HierarchyDecision.INVALIDATED: "Setup invalidated",
    }[decision]


# ---------------------------------------------------------------------------
# The immutable ledger record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HierarchyObservation:
    """One append-only, immutable hierarchy evaluation in the forward ledger.

    The row carries the complete :class:`HierarchySnapshot` as canonical JSON
    plus the denormalized columns the dashboard and reporting read directly.
    ``observation_id`` is the snapshot's deterministic identity, so recording
    the same evaluation twice (a restart, a repeated pass, a duplicate
    boundary) is a verified no-op, and a different hierarchy configuration can
    never collide with an older row. ``recorded_at`` is write-time audit
    metadata only and is deliberately excluded from the identity.
    """

    observation_id: str
    rules_version: str
    exchange: str
    symbol: str
    decision_time: datetime
    hierarchy_fingerprint: str
    hierarchy_json: str
    context_timeframe: str
    context_boundary_open: datetime
    context_boundary_close: datetime
    context_json: str
    setup_timeframe: str
    setup_boundary_open: datetime
    setup_boundary_close: datetime
    setup_json: str
    confirmation_timeframe: str
    confirmation_boundary_open: datetime
    confirmation_boundary_close: datetime
    confirmation_json: str
    execution_timeframe: str
    execution_boundary_open: datetime
    execution_boundary_close: datetime
    execution_json: str
    alignment: str
    counter_trend: bool
    decision: str
    status: str
    reasons_json: str
    strategy_versions_json: str
    waiting_for_json: str
    invalidated_if_json: str
    boundary_json: str
    snapshot_json: str
    recorded_at: datetime

    @classmethod
    def from_snapshot(
        cls, snapshot: HierarchySnapshot, *, recorded_at: datetime
    ) -> "HierarchyObservation":
        """Build the ledger row for one evaluated snapshot.

        The row's ``rules_version`` is the LEDGER rules version (the record
        format this table is written under); the hierarchy configuration
        version that produced the decision stays inside the identity, the
        strategy versions and the hierarchy JSON.
        """

        from trading_assistant.multi_timeframe.parameters import (
            HIERARCHY_LEDGER_RULES_VERSION,
        )

        return cls(
            observation_id=snapshot.identity(),
            rules_version=HIERARCHY_LEDGER_RULES_VERSION,
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            decision_time=snapshot.decision_time,
            hierarchy_fingerprint=snapshot.hierarchy_fingerprint,
            hierarchy_json=canonical_json(snapshot.hierarchy.to_json_dict()),
            context_timeframe=snapshot.context.timeframe,
            context_boundary_open=snapshot.context.boundary_open,
            context_boundary_close=snapshot.context.boundary_close,
            context_json=canonical_json(snapshot.context.to_json_dict()),
            setup_timeframe=snapshot.setup.timeframe,
            setup_boundary_open=snapshot.setup.boundary_open,
            setup_boundary_close=snapshot.setup.boundary_close,
            setup_json=canonical_json(snapshot.setup.to_json_dict()),
            confirmation_timeframe=snapshot.confirmation.timeframe,
            confirmation_boundary_open=snapshot.confirmation.boundary_open,
            confirmation_boundary_close=snapshot.confirmation.boundary_close,
            confirmation_json=canonical_json(snapshot.confirmation.to_json_dict()),
            execution_timeframe=snapshot.execution.timeframe,
            execution_boundary_open=snapshot.execution.boundary_open,
            execution_boundary_close=snapshot.execution.boundary_close,
            execution_json=canonical_json(snapshot.execution.to_json_dict()),
            alignment=snapshot.alignment.value,
            counter_trend=snapshot.counter_trend,
            decision=snapshot.decision.value,
            status=snapshot.status,
            reasons_json=canonical_json(list(snapshot.reasons)),
            strategy_versions_json=canonical_json(
                [
                    {"name": name, "version": version}
                    for name, version in snapshot.strategy_versions
                ]
            ),
            waiting_for_json=canonical_json(list(snapshot.waiting_for)),
            invalidated_if_json=canonical_json(list(snapshot.invalidated_if)),
            boundary_json=canonical_json(snapshot.boundary.to_json_dict()),
            snapshot_json=canonical_json(snapshot.to_json_dict()),
            recorded_at=recorded_at,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


__all__ = [
    "PLANNABLE_ALIGNMENTS",
    "ConfirmationLayerSnapshot",
    "ConfirmationState",
    "ContextLayerSnapshot",
    "ContextRegime",
    "ExecutionLayerSnapshot",
    "ExecutionState",
    "HierarchyAlignment",
    "HierarchyDecision",
    "HierarchyObservation",
    "HierarchySnapshot",
    "LayerEvidence",
    "SetupLayerSnapshot",
    "alignment_label",
    "confirmation_label",
    "decision_label",
    "execution_label",
    "family_label",
    "regime_label",
]
