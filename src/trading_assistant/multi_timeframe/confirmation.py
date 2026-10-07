"""CONFIRMATION layer (15M by default): whether the 1H idea is confirming.

The confirmation layer NEVER searches for its own trade. It evaluates exactly
one thing: the ACTIVE setup produced by the setup layer, using only 15M
candles that had fully closed by the decision time and that opened at or after
the instant the setup became known (its creation boundary). Candles from
before the setup existed are the setup's own history — the Step 5 engine
already consumed them — and candles after the decision time are never used.

Deterministic evidence (existing engines only, no new indicators):

* acceptance / rejection of the setup's reference level (close beyond the band
  on the setup side, or loss of the band on the wrong side);
* false-breakout behaviour (acceptance followed by loss of the level);
* local 15M structure alignment (the existing Step 3 trend on the window);
* relative volume of the latest window candle (existing Step 3 volume).

The state is one explicit enum: NOT_APPLICABLE, WAITING, CONFIRMING,
CONTRADICTING, INVALIDATED.
"""

from __future__ import annotations

from datetime import datetime

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.analysis import analyze_candles
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.multi_timeframe.boundaries import TimeframeBoundary
from trading_assistant.multi_timeframe.models import (
    ConfirmationLayerSnapshot,
    ConfirmationState,
    LayerEvidence,
    SetupLayerSnapshot,
)
from trading_assistant.setup_qualification.models import EvidenceStatus


def validate_window(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    window_start: datetime,
    decision_time: datetime,
) -> None:
    """Defence in depth: refuse any candle outside the knowable window.

    Every candle must belong to the confirmation timeframe, must have opened at
    or after the setup's creation boundary, and must have fully closed by the
    decision time. A violation is a lookahead bug and raises loudly.
    """

    interval = interval_for_timeframe(timeframe)
    for candle in candles:
        if candle.timeframe != timeframe:
            raise ValueError(
                f"confirmation candle timeframe {candle.timeframe!r} does not "
                f"match {timeframe!r}"
            )
        if candle.timestamp < window_start:
            raise ValueError(
                f"confirmation candle {candle.timestamp.isoformat()} opens before "
                f"the setup became known at {window_start.isoformat()}; using it "
                "would inspect pre-setup data as confirmation"
            )
        if candle.timestamp + interval > decision_time:
            raise ValueError(
                f"confirmation candle {candle.timestamp.isoformat()} was not "
                f"fully closed by the decision time {decision_time.isoformat()}; "
                "using it would be lookahead"
            )


def evaluate_confirmation(
    setup: SetupLayerSnapshot,
    candles: tuple[Candle, ...],
    *,
    boundary: TimeframeBoundary,
    missing_candle_count: int = 0,
    structure_parameters: MarketStructureParameters | None = None,
) -> ConfirmationLayerSnapshot:
    """Evaluate the active 1H setup against closed 15M candles.

    ``candles`` must already be restricted to the confirmation window (open at
    or after ``setup.created_at``, fully closed by the decision time);
    :func:`validate_window` re-checks that here so the pure layer itself can
    never be fed lookahead data.
    """

    timeframe = boundary.timeframe
    decision_time = boundary.decision_time
    window_start = setup.created_at if setup.created_at is not None else boundary.candle_close_time
    window_end = boundary.candle_open_time

    def _result(
        state: ConfirmationState,
        reason: str,
        evidence: tuple[LayerEvidence, ...],
    ) -> ConfirmationLayerSnapshot:
        return ConfirmationLayerSnapshot(
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
            evidence=evidence,
        )

    if not setup.has_active_setup:
        if setup.is_terminal:
            return _result(
                ConfirmationState.INVALIDATED,
                f"the setup ended at {setup.ended_at.isoformat()} "
                f"({setup.terminal_reason}); there is no live idea to confirm",
                (
                    LayerEvidence(
                        category="lifecycle",
                        status=EvidenceStatus.OPPOSING,
                        reason=f"setup terminal: {setup.terminal_reason}",
                        timeframe=timeframe,
                        observed_at=setup.ended_at,
                    ),
                ),
            )
        return _result(
            ConfirmationState.NOT_APPLICABLE,
            "no active setup exists on the setup timeframe; the confirmation "
            "layer has nothing to evaluate and creates nothing on its own",
            (),
        )

    if setup.direction not in ("bullish", "bearish"):
        raise ValueError(
            f"cannot evaluate confirmation for setup direction "
            f"{setup.direction!r}; the setup layer must state bullish or bearish"
        )
    if setup.created_at is None:
        raise ValueError(
            "cannot evaluate confirmation without the instant the setup became "
            "known; the confirmation window starts at setup creation"
        )

    validate_window(
        candles,
        timeframe=timeframe,
        window_start=window_start,
        decision_time=decision_time,
    )

    evidence: list[LayerEvidence] = []
    direction = setup.direction
    band_low = setup.reference_band_low
    band_high = setup.reference_band_high
    has_band = band_low is not None and band_high is not None

    if not candles:
        return _result(
            ConfirmationState.WAITING,
            "no closed confirmation candle exists after the setup became known; "
            "the idea is neither confirmed nor contradicted yet",
            (
                LayerEvidence(
                    category="availability",
                    status=EvidenceStatus.UNKNOWN,
                    reason=(
                        "no closed candle in the confirmation window at the "
                        "decision time"
                    ),
                    timeframe=timeframe,
                    observed_at=decision_time,
                ),
            ),
        )

    closes = [candle.close for candle in candles]

    # --- level acceptance / loss around the setup's reference band ---------
    if has_band:
        if direction == "bullish":
            lost = [c for c in closes if c < band_low]
            accepted = [c for c in closes if c >= band_high]
        else:
            lost = [c for c in closes if c > band_high]
            accepted = [c for c in closes if c <= band_low]
        if lost:
            evidence.append(
                LayerEvidence(
                    category="level_loss",
                    status=EvidenceStatus.OPPOSING,
                    reason=(
                        f"a closed {timeframe} candle closed beyond the setup "
                        f"reference level on the wrong side (band "
                        f"{band_low}–{band_high}); the setup idea is "
                        "contradicted"
                        + (
                            " after an earlier acceptance (false breakout)"
                            if accepted
                            else ""
                        )
                    ),
                    timeframe=timeframe,
                    observed_at=decision_time,
                )
            )
            if accepted:
                evidence.append(
                    LayerEvidence(
                        category="false_breakout",
                        status=EvidenceStatus.OPPOSING,
                        reason=(
                            "the level was accepted earlier in the window and "
                            "then lost: deterministic false-breakout behaviour"
                        ),
                        timeframe=timeframe,
                        observed_at=decision_time,
                    )
                )
            return _result(
                ConfirmationState.CONTRADICTING,
                f"{timeframe} price action contradicts the setup: the reference "
                f"level band {band_low}–{band_high} was lost on closed candles",
                tuple(evidence),
            )
        if accepted:
            evidence.append(
                LayerEvidence(
                    category="level_acceptance",
                    status=EvidenceStatus.SUPPORTIVE,
                    reason=(
                        f"a closed {timeframe} candle closed beyond the setup "
                        f"reference level on the setup side (band "
                        f"{band_low}–{band_high}) and the level held on every "
                        "later close in the window"
                    ),
                    timeframe=timeframe,
                    observed_at=decision_time,
                )
            )
            state = ConfirmationState.CONFIRMING
            reason = (
                f"{timeframe} price accepted the setup's reference level and "
                "held it on closed candles"
            )
        else:
            rejections = [
                candle
                for candle in candles
                if (
                    (direction == "bullish" and candle.high >= band_high and candle.close < band_high)
                    or (direction == "bearish" and candle.low <= band_low and candle.close > band_low)
                )
            ]
            if rejections:
                evidence.append(
                    LayerEvidence(
                        category="level_rejection",
                        status=EvidenceStatus.OPPOSING,
                        reason=(
                            f"{len(rejections)} closed {timeframe} candle(s) "
                            "rejected the reference level (wick beyond the band, "
                            "close back inside); no acceptance yet"
                        ),
                        timeframe=timeframe,
                        observed_at=decision_time,
                    )
                )
            evidence.append(
                LayerEvidence(
                    category="level_hold",
                    status=EvidenceStatus.NEUTRAL,
                    reason=(
                        f"{timeframe} closes stay inside the reference band "
                        f"{band_low}–{band_high}; the level is neither accepted "
                        "nor lost"
                    ),
                    timeframe=timeframe,
                    observed_at=decision_time,
                )
            )
            state = ConfirmationState.WAITING
            reason = (
                f"{timeframe} has not accepted the setup's reference level yet; "
                "the idea is still waiting for confirmation"
            )
    else:
        # No reference level available: local structure alignment only, and
        # the layer says so instead of guessing a level.
        state = None
        reason = ""
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.UNKNOWN,
                reason=(
                    "the setup's reference level is unavailable; level-based "
                    "confirmation cannot be evaluated"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )

    # --- local structure alignment (existing Step 3 trend on the window) ----
    analysis = analyze_candles(
        candles,
        interval=interval_for_timeframe(timeframe),
        as_of=decision_time,
        parameters=structure_parameters,
    )
    trend = analysis.trend
    if trend.sufficient:
        aligned = (trend.direction.value == direction)
        evidence.append(
            LayerEvidence(
                category="structure_alignment",
                status=EvidenceStatus.SUPPORTIVE if aligned else EvidenceStatus.OPPOSING,
                reason=(
                    f"local {timeframe} structure is {trend.direction.value} "
                    f"({trend.reason.value}), which "
                    + ("aligns with" if aligned else "opposes")
                    + f" the {direction} setup"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )
        if state is None:
            state = (
                ConfirmationState.CONFIRMING
                if aligned
                else ConfirmationState.CONTRADICTING
            )
            reason = (
                f"local {timeframe} structure "
                + ("aligns with" if aligned else "opposes")
                + " the setup (reference level unavailable; structure "
                "alignment only)"
            )
    elif state is None:
        state = ConfirmationState.WAITING
        reason = (
            f"local {timeframe} structure is not yet directional "
            f"({trend.reason.value}); the idea is still waiting"
        )
    else:
        evidence.append(
            LayerEvidence(
                category="structure_alignment",
                status=EvidenceStatus.NEUTRAL,
                reason=(
                    f"local {timeframe} structure is not yet directional "
                    f"({trend.reason.value})"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )

    # --- volume evidence (existing Step 3 volume context) -------------------
    if analysis.volume.sufficient and analysis.volume.relative_volume is not None:
        evidence.append(
            LayerEvidence(
                category="volume",
                status=EvidenceStatus.NEUTRAL,
                reason=(
                    f"latest {timeframe} relative volume "
                    f"{analysis.volume.relative_volume}x its average"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )

    if boundary.stale:
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    "stored confirmation candles stop before the expected "
                    f"closed candle {boundary.candle_open_time.isoformat()}"
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
                    "missing inside the confirmation window"
                ),
                timeframe=timeframe,
                observed_at=decision_time,
            )
        )

    assert state is not None
    return _result(state, reason, tuple(evidence))


__all__ = ["evaluate_confirmation", "validate_window"]
