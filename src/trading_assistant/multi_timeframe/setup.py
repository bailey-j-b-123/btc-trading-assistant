"""SETUP layer (1H by default): what may be traded.

The setup layer is the existing Step 5 setup-qualification engine, unchanged:
the same replay, the same rules, the same thresholds. This module only projects
one qualification snapshot into the hierarchy's setup-layer contract — setup
family, direction, status, the relevant level/zone, supporting evidence,
opposing evidence, invalidation, and what must happen next.

The active setup is selected by the same deterministic rule the dashboard
uses: the earliest-created QUALIFIED setup, else the earliest-created WATCH
setup. A lower timeframe can never create or upgrade this selection.
"""

from __future__ import annotations

from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    FailedBreakout,
    Retest,
    Sweep,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    RuleOutcome,
    SetupResult,
    SetupState,
)
from trading_assistant.multi_timeframe.boundaries import TimeframeBoundary
from trading_assistant.multi_timeframe.models import SetupLayerSnapshot


def select_active_setup(snapshot: QualificationSnapshot) -> SetupResult | None:
    """The one setup the hierarchy tracks: earliest QUALIFIED, else earliest WATCH.

    Terminal (NO_SETUP with an end time) candidates are not active: they are
    history. When nothing is live, ``None`` is returned and the caller decides
    between NO_SETUP (nothing ever qualified here) and INVALIDATED (a setup
    ended exactly at this boundary).
    """

    live = [
        setup
        for setup in snapshot.setups
        if setup.state in (SetupState.WATCH, SetupState.QUALIFIED)
    ]
    if not live:
        return None
    qualified = [setup for setup in live if setup.state is SetupState.QUALIFIED]
    pool = qualified if qualified else live
    return min(pool, key=lambda setup: (setup.created_at, setup.id))


def setup_ended_at_boundary(
    snapshot: QualificationSnapshot, boundary_close
) -> SetupResult | None:
    """A terminal setup that ended exactly at this boundary, if any."""

    for setup in snapshot.setups:
        if (
            setup.state is SetupState.NO_SETUP
            and setup.ended_at is not None
            and setup.ended_at == boundary_close
        ):
            return setup
    return None


def reference_band(
    frame: QualificationFrame | None, reference_id: str | None
) -> tuple[str, str, str] | None:
    """Resolve the setup's reference band ``(type, band_low, band_high)``.

    Mirrors the dashboard overlay resolution: the reference is looked up on
    the frame's Step 4 events by id, per event kind, never guessed. ``None``
    when the frame or the reference is unavailable.
    """

    if frame is None or reference_id is None:
        return None
    for event in frame.patterns.events():
        if isinstance(event, FailedBreakout):
            reference = event.breakout.reference
        elif isinstance(event, Retest):
            reference = event.breakout.reference
        elif isinstance(event, (Breakout, Sweep)):
            reference = event.reference
        else:
            continue
        if reference.id == reference_id:
            return (
                reference.type,
                format(reference.band_low, "f"),
                format(reference.band_high, "f"),
            )
    return None


def build_setup_snapshot(
    snapshot: QualificationSnapshot | None,
    *,
    selected: SetupResult | None,
    ended: SetupResult | None,
    frame: QualificationFrame | None,
    boundary: TimeframeBoundary,
) -> SetupLayerSnapshot:
    """Project one Step 5 qualification snapshot into the setup layer."""

    timeframe = boundary.timeframe
    if snapshot is None:
        return SetupLayerSnapshot(
            timeframe=timeframe,
            decision_time=boundary.decision_time,
            boundary_open=boundary.candle_open_time,
            boundary_close=boundary.candle_close_time,
            available=False,
            reason=(
                "no Step 5 qualification snapshot could be built for the setup "
                "timeframe at the decision time"
            ),
            state=None,
            snapshot_status=None,
            snapshot_id=None,
            rules_version=None,
            config_fingerprint=None,
            setup_id=None,
            family=None,
            direction=None,
            setup_state=None,
            created_at=None,
            terminal_reason=None,
            ended_at=None,
            reference_id=None,
            reference_band_low=None,
            reference_band_high=None,
            supporting_rules=(),
            opposing_rules=(),
            pending_rules=(),
            invalidation=None,
            next_required=(),
            candidate_count=0,
            stale=boundary.stale,
        )

    active = selected if selected is not None else ended
    reference = None
    if active is not None:
        reference = reference_band(frame, active.reference_id)
    supporting: tuple[str, ...] = ()
    opposing: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    next_required: tuple[str, ...] = ()
    invalidation: str | None = None
    if active is not None:
        supporting = active.passed_rules
        opposing = tuple(
            sorted(
                set(active.failed_rules)
                | {rule.rule_id for rule in active.rules if rule.veto}
            )
        )
        pending = active.pending_rules
        pending_required = [
            rule
            for rule in active.rules
            if rule.required and rule.outcome is RuleOutcome.PENDING
        ]
        next_required = tuple(
            f"{rule.rule_id}: {rule.reason}" for rule in pending_required
        )
        if active.terminal_reason is not None:
            invalidation = active.terminal_reason
        else:
            invalidation_evidence = [
                evidence.reason
                for rule in active.rules
                for evidence in rule.evidence
                if evidence.category == "invalidation"
            ]
            if invalidation_evidence:
                invalidation = "; ".join(invalidation_evidence)

    return SetupLayerSnapshot(
        timeframe=timeframe,
        decision_time=boundary.decision_time,
        boundary_open=boundary.candle_open_time,
        boundary_close=boundary.candle_close_time,
        available=True,
        reason=(
            "stored setup candles stop before the expected closed candle"
            if boundary.stale
            else (
                "the Step 5 snapshot reports an incomplete source window"
                if snapshot.status == "incomplete"
                else None
            )
        ),
        state=snapshot.state,
        snapshot_status=snapshot.status,
        snapshot_id=_snapshot_id(snapshot),
        rules_version=snapshot.rules_version,
        config_fingerprint=snapshot.config_fingerprint,
        setup_id=None if active is None else active.id,
        family=None if active is None else active.family.value,
        direction=None if active is None else str(active.direction),
        setup_state=None if active is None else active.state.value,
        created_at=None if active is None else active.created_at,
        terminal_reason=None if active is None else active.terminal_reason,
        ended_at=None if active is None else active.ended_at,
        reference_id=None if active is None else active.reference_id,
        reference_band_low=(
            None
            if reference is None
            else _decimal(reference[1])
        ),
        reference_band_high=(
            None
            if reference is None
            else _decimal(reference[2])
        ),
        supporting_rules=supporting,
        opposing_rules=opposing,
        pending_rules=pending,
        invalidation=invalidation,
        next_required=next_required,
        candidate_count=len(snapshot.setups),
        stale=boundary.stale,
    )


def _snapshot_id(snapshot: QualificationSnapshot) -> str:
    from trading_assistant.journaling import snapshot_identity

    return snapshot_identity(snapshot)


def _decimal(text: str):
    from decimal import Decimal

    return Decimal(text)


__all__ = [
    "build_setup_snapshot",
    "reference_band",
    "select_active_setup",
    "setup_ended_at_boundary",
    "to_jsonable",
]
