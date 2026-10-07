"""EXECUTION layer (5M by default): when the entry may be ready.

The execution layer becomes relevant only when the higher layers are complete:
the context layer was evaluated, the setup layer produced a QUALIFIED setup,
and the confirmation layer CONFIRMED it. Its only job is entry refinement
timing around the setup's own reference band — the deterministic level the
Step 5 setup already established. It never places, sends, or authorises
anything: TRIGGERED means "the deterministic entry-timing condition is met",
never "an order exists".

Deterministic states:

* ``NOT_ARMED`` — a prerequisite is missing (no qualified setup, or the
  confirmation has not confirmed). A 5M candle pattern alone can never arm it.
* ``WAITING`` — prerequisites are met, but no closed 5M candle has reached the
  entry zone (the setup's reference band) yet.
* ``ARMED`` — price has entered the entry zone and is holding the setup's
  level; the entry timing is being refined.
* ``TRIGGERED`` — while armed, the latest closed 5M candle closed through the
  far side of the entry zone: the deterministic entry-timing condition.
* ``INVALIDATED`` — a closed 5M candle lost the setup's level, or a higher
  layer invalidated.
"""

from __future__ import annotations

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.multi_timeframe.boundaries import TimeframeBoundary
from trading_assistant.multi_timeframe.models import (
    ConfirmationLayerSnapshot,
    ConfirmationState,
    ExecutionLayerSnapshot,
    ExecutionState,
    LayerEvidence,
    SetupLayerSnapshot,
)
from trading_assistant.setup_qualification.models import EvidenceStatus


def evaluate_execution(
    setup: SetupLayerSnapshot,
    confirmation: ConfirmationLayerSnapshot,
    candles: tuple[Candle, ...],
    *,
    boundary: TimeframeBoundary,
    missing_candle_count: int = 0,
) -> ExecutionLayerSnapshot:
    """Evaluate entry-refinement timing for the active setup.

    ``candles`` must already be restricted to the execution window (open at or
    after ``setup.created_at``, fully closed by the decision time); the same
    window rule as the confirmation layer applies, so 5M data from before the
    setup existed is never used as an execution signal.
    """

    timeframe = boundary.timeframe
    decision_time = boundary.decision_time
    interval = interval_for_timeframe(timeframe)
    window_start = (
        setup.created_at if setup.created_at is not None else boundary.candle_close_time
    )
    window_end = boundary.candle_open_time

    def _result(
        state: ExecutionState,
        reason: str,
        evidence: tuple[LayerEvidence, ...] = (),
        *,
        armed_at=None,
        trigger_at=None,
        latest_close=None,
    ) -> ExecutionLayerSnapshot:
        return ExecutionLayerSnapshot(
            timeframe=timeframe,
            decision_time=decision_time,
            boundary_open=boundary.candle_open_time,
            boundary_close=boundary.candle_close_time,
            state=state,
            reason=reason,
            window_start=window_start,
            window_end=window_end,
            candle_count=len(candles),
            missing_candle_count=missing_candle_count,
            stale=boundary.stale,
            armed_at=armed_at,
            trigger_at=trigger_at,
            entry_zone_low=setup.reference_band_low,
            entry_zone_high=setup.reference_band_high,
            latest_close=latest_close,
            evidence=evidence,
        )

    # --- prerequisites: the higher layers must be complete ------------------
    missing: list[str] = []
    if not setup.has_active_setup:
        missing.append("no active setup on the setup timeframe")
    elif not setup.is_qualified:
        missing.append(
            f"the setup is {setup.setup_state}, not QUALIFIED; a lower timeframe "
            "cannot upgrade it"
        )
    if confirmation.state is not ConfirmationState.CONFIRMING:
        if confirmation.state is ConfirmationState.INVALIDATED:
            return _result(
                ExecutionState.INVALIDATED,
                f"the confirmation layer invalidated the setup: "
                f"{confirmation.reason}",
                (
                    LayerEvidence(
                        category="lifecycle",
                        status=EvidenceStatus.OPPOSING,
                        reason=confirmation.reason,
                        timeframe=confirmation.timeframe,
                        observed_at=decision_time,
                    ),
                ),
            )
        missing.append(
            f"the confirmation layer is {confirmation.state.value}, not "
            "CONFIRMING"
        )
    if setup.reference_band_low is None or setup.reference_band_high is None:
        missing.append("the setup's reference level is unavailable")
    if missing:
        return _result(
            ExecutionState.NOT_ARMED,
            "the execution layer is not armed: " + "; ".join(missing),
            (
                LayerEvidence(
                    category="prerequisite",
                    status=EvidenceStatus.UNKNOWN,
                    reason="; ".join(missing),
                    timeframe=timeframe,
                    observed_at=decision_time,
                ),
            ),
        )
    if setup.is_terminal:
        return _result(
            ExecutionState.INVALIDATED,
            f"the setup ended ({setup.terminal_reason}); execution timing is moot",
        )

    # --- window validation (no lookahead, no pre-setup data) ----------------
    for candle in candles:
        if candle.timeframe != timeframe:
            raise ValueError(
                f"execution candle timeframe {candle.timeframe!r} does not match "
                f"{timeframe!r}"
            )
        if candle.timestamp < window_start:
            raise ValueError(
                f"execution candle {candle.timestamp.isoformat()} opens before "
                f"the setup became known at {window_start.isoformat()}"
            )
        if candle.timestamp + interval > decision_time:
            raise ValueError(
                f"execution candle {candle.timestamp.isoformat()} was not fully "
                f"closed by the decision time {decision_time.isoformat()}; "
                "using it would be lookahead"
            )

    band_low = setup.reference_band_low
    band_high = setup.reference_band_high
    direction = setup.direction
    latest_close = candles[-1].close if candles else None

    evidence: list[LayerEvidence] = []
    if boundary.stale:
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    "stored execution candles stop before the expected closed "
                    f"candle {boundary.candle_open_time.isoformat()}"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )
    if missing_candle_count:
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    f"{missing_candle_count} expected {timeframe} candle(s) are "
                    "missing inside the execution window"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )

    if not candles:
        return _result(
            ExecutionState.WAITING,
            "prerequisites are met, but no closed execution candle exists after "
            "the setup became known; entry timing cannot be refined yet",
            tuple(evidence),
        )

    # --- invalidation: the setup's level lost on the execution timeframe ----
    if direction == "bullish":
        lost = [c for c in candles if c.close < band_low]
    else:
        lost = [c for c in candles if c.close > band_high]
    if lost:
        evidence.append(
            LayerEvidence(
                category="level_loss",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    f"a closed {timeframe} candle closed beyond the setup "
                    f"reference level on the wrong side (band {band_low}–"
                    f"{band_high})"
                ),
                timeframe=timeframe,
                observed_at=lost[-1].timestamp,
            )
        )
        return _result(
            ExecutionState.INVALIDATED,
            f"{timeframe} price lost the setup's reference level on a closed "
            f"candle; the entry timing is invalidated",
            tuple(evidence),
            latest_close=latest_close,
        )

    # --- entry zone: the setup's own reference band --------------------------
    def _in_zone(candle: Candle) -> bool:
        return candle.low <= band_high and candle.high >= band_low

    entered = [c for c in candles if _in_zone(c)]
    if not entered:
        evidence.append(
            LayerEvidence(
                category="entry_zone",
                status=EvidenceStatus.NEUTRAL,
                reason=(
                    f"no closed {timeframe} candle has reached the entry zone "
                    f"(band {band_low}–{band_high}) yet"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )
        return _result(
            ExecutionState.WAITING,
            f"waiting for {timeframe} price to reach the entry zone (the "
            f"setup's reference band {band_low}–{band_high})",
            tuple(evidence),
            latest_close=latest_close,
        )

    armed_at = entered[0].timestamp
    evidence.append(
        LayerEvidence(
            category="entry_zone",
            status=EvidenceStatus.SUPPORTIVE,
            reason=(
                f"{timeframe} price entered the entry zone (band "
                f"{band_low}–{band_high}) at {armed_at.isoformat()} and the "
                "setup's level held on every closed candle since"
            ),
            timeframe=timeframe,
            observed_at=armed_at,
        )
    )

    # --- trigger: close through the far side of the entry zone --------------
    # The entry zone is the setup's reference band [band_low, band_high].
    # Swing references are POINT levels (band_low == band_high): "holding the
    # level" then means the latest close sits at or beyond the level on the
    # setup side, and "triggered" means a close strictly through the level.
    # Wide (zone) references keep the natural reading: holding inside the band
    # is ARMED, a close through the far side is TRIGGERED.
    if direction == "bullish":
        holding = latest_close is not None and latest_close >= band_low
        triggered = (
            latest_close is not None
            and latest_close >= band_high
            and latest_close > band_low
        )
    else:
        holding = latest_close is not None and latest_close <= band_high
        triggered = (
            latest_close is not None
            and latest_close <= band_low
            and latest_close < band_high
        )

    if triggered:
        evidence.append(
            LayerEvidence(
                category="trigger",
                status=EvidenceStatus.SUPPORTIVE,
                reason=(
                    f"the latest closed {timeframe} candle closed through the "
                    f"far side of the entry zone (close {latest_close}, band "
                    f"{band_low}–{band_high}): the deterministic entry-timing "
                    "condition is met"
                ),
                timeframe=timeframe,
                observed_at=candles[-1].timestamp,
            )
        )
        return _result(
            ExecutionState.TRIGGERED,
            f"{timeframe} entry timing is ready: price is holding the setup's "
            f"level and the latest closed candle closed through the entry zone "
            f"(close {latest_close})",
            tuple(evidence),
            armed_at=armed_at,
            trigger_at=candles[-1].timestamp,
            latest_close=latest_close,
        )

    if holding:
        return _result(
            ExecutionState.ARMED,
            f"{timeframe} price is inside the entry zone and holding the "
            f"setup's level (latest close {latest_close}, band "
            f"{band_low}–{band_high}); entry timing is being refined",
            tuple(evidence),
            armed_at=armed_at,
            latest_close=latest_close,
        )

    return _result(
        ExecutionState.WAITING,
        f"{timeframe} price entered the entry zone but the latest close "
        f"({latest_close}) is not holding the setup's level yet",
        tuple(evidence),
        armed_at=armed_at,
        latest_close=latest_close,
    )


__all__ = ["evaluate_execution"]
