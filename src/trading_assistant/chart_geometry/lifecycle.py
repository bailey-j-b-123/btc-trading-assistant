"""Chronological lifecycle evaluation of one frozen formation.

Rules, in the order they are applied to each closed candle after the formation
became knowable:

1. Only candles whose close instant is **strictly after** ``formed_at`` are
   evaluated, and only candles inside the formation's own **gap-free run** (see
   :meth:`trading_assistant.chart_geometry.checks.CandleIndex.contiguous_run_from`).
   A candle that was still forming when the geometry became knowable can never
   confirm or invalidate it, a candle that closed before it can never be used at
   all, and a candle separated from the formation's first component by even one
   missing candle is never used either: this package does not confirm or
   invalidate across unseen history. When the run ends early the formation simply
   stays ``formed`` and the snapshot records the truncation explicitly.
2. Both frozen boundary lines are evaluated at the candle's own open time. If
   they are no longer ordered (they met at the apex or crossed), the bounded
   region the formation described no longer exists: the formation is
   **invalidated** with reason ``boundaries_converged_or_crossed``. This is a
   geometric statement and carries no observed direction.
3. Otherwise a close beyond a boundary by more than ``breakout_tolerance_pct`` of
   that boundary's own value is a transition. For the directional families
   (flags, cup and handle) the family's continuation side confirms and the
   opposite side invalidates. For the converging families (wedges, triangles)
   either side confirms, and the direction actually observed is recorded.
4. The first terminal transition wins. ``invalidated`` is final for that
   formation id: no later candle can confirm it, resurrect it, or produce another
   event for it. ``confirmed`` is equally final: this package describes the
   geometry and its observed resolution, not what happened afterwards.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_assistant.chart_geometry.checks import CandleIndex
from trading_assistant.chart_geometry.identity import event_identity, geometry_digest
from trading_assistant.chart_geometry.models import (
    CandidateFormation,
    ChartGeometryEvent,
    ObservedDirection,
)
from trading_assistant.chart_geometry.parameters import ChartGeometryParameters
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.numeric import percentage_of, tolerance_band

REASON_FORMED = "minimum_components_satisfied"
REASON_CLOSE_BEYOND_UPPER = "close_beyond_upper_boundary"
REASON_CLOSE_BEYOND_LOWER = "close_beyond_lower_boundary"
REASON_BOUNDARIES_CROSSED = "boundaries_converged_or_crossed"

TERMINAL_STATES = ("confirmed", "invalidated")


def _detail_close_beyond(
    *,
    side: str,
    close: Decimal,
    boundary: Decimal,
    penetration: Decimal,
    penetration_pct: Decimal,
    tolerance_pct: Decimal,
    when: datetime,
) -> str:
    return (
        f"closed candle at {when.isoformat()} closed at {close} which is {side} the frozen "
        f"boundary value {boundary} at that candle's own open time by {penetration} "
        f"({penetration_pct}% of {boundary}); configured tolerance {tolerance_pct}%"
    )


def lifecycle_events(
    candidate: CandidateFormation,
    formation_id: str,
    *,
    index: CandleIndex,
    parameters: ChartGeometryParameters,
    evaluation_candles: tuple[Candle, ...],
) -> tuple[ChartGeometryEvent, ...]:
    """Return the complete chronological lifecycle of one frozen formation.

    ``evaluation_candles`` must already be the gap-free run that starts at the
    formation's first component and must already be restricted to candles that
    closed strictly after ``formed_at``. This function never reaches outside it.
    """

    geometry = candidate.geometry
    frozen_digest = geometry_digest(geometry)
    upper = geometry.upper
    lower = geometry.lower

    def event(
        *,
        state: str,
        known_at: datetime,
        anchor,
        direction: ObservedDirection | None,
        reason: str,
        detail: str,
        penetration: Decimal | None,
        penetration_pct: Decimal | None,
    ) -> ChartGeometryEvent:
        return ChartGeometryEvent(
            event_id=event_identity(
                formation_id=formation_id,
                state=state,
                known_at=known_at,
                anchor_candle=anchor,
                reason=reason,
                observed_direction=direction,
            ),
            formation_id=formation_id,
            family=candidate.family,
            state=state,  # type: ignore[arg-type]
            known_at=known_at,
            anchor_candle=anchor,
            observed_direction=direction,
            reason=reason,
            reason_detail=detail,
            upper_boundary_value=upper.value_at(anchor.timestamp),
            lower_boundary_value=lower.value_at(anchor.timestamp),
            penetration=penetration,
            penetration_pct=penetration_pct,
            candles_since_formed=max(0, index.span_candles(candidate.formed_at, anchor.timestamp)),
            gap_before_anchor=index.previous(anchor.timestamp) is None,
            frozen_geometry_digest=frozen_digest,
        )

    formed_anchor = candidate.formation_anchor
    formed_detail = (
        f"{candidate.family.value} described by {len(candidate.components)} confirmed swings "
        f"from {geometry.first_component_time.isoformat()} to "
        f"{geometry.last_component_time.isoformat()}; the last component became knowable at "
        f"{candidate.formed_at.isoformat()}, the close of the candle opened at "
        f"{formed_anchor.timestamp.isoformat()}"
    )
    events = [
        event(
            state="formed",
            known_at=candidate.formed_at,
            anchor=formed_anchor,
            direction=None,
            reason=REASON_FORMED,
            detail=formed_detail,
            penetration=None,
            penetration_pct=None,
        )
    ]

    tolerance_pct = parameters.breakout_tolerance_pct
    for candle in evaluation_candles:
        upper_value = upper.value_at(candle.timestamp)
        lower_value = lower.value_at(candle.timestamp)
        if upper_value <= lower_value:
            events.append(
                event(
                    state="invalidated",
                    known_at=candle.timestamp + index.interval,
                    anchor=candle,
                    direction=None,
                    reason=REASON_BOUNDARIES_CROSSED,
                    detail=(
                        f"frozen boundary lines are no longer ordered at the candle opened at "
                        f"{candle.timestamp.isoformat()}: upper {upper_value} <= lower "
                        f"{lower_value}, so the bounded region this formation described has "
                        f"ended; invalidation is terminal for this formation id"
                    ),
                    penetration=None,
                    penetration_pct=None,
                )
            )
            break

        upper_trigger = upper_value + tolerance_band(upper_value, tolerance_pct)
        lower_trigger = lower_value - tolerance_band(lower_value, tolerance_pct)
        closed_above = candle.close > upper_trigger
        closed_below = candle.close < lower_trigger
        if not closed_above and not closed_below:
            continue

        state: str | None = None
        direction: ObservedDirection | None = None
        reason = ""
        boundary = upper_value
        if closed_above:
            direction = "above"
            reason = REASON_CLOSE_BEYOND_UPPER
            if candidate.breakout_side in ("above", "either"):
                state = "confirmed"
            elif candidate.failure_side == "above":
                state = "invalidated"
        else:
            direction = "below"
            reason = REASON_CLOSE_BEYOND_LOWER
            boundary = lower_value
            if candidate.breakout_side in ("below", "either"):
                state = "confirmed"
            elif candidate.failure_side == "below":
                state = "invalidated"
        if state is None:  # pragma: no cover - every family declares one side
            continue

        penetration = abs(candle.close - boundary)
        events.append(
            event(
                state=state,
                known_at=candle.timestamp + index.interval,
                anchor=candle,
                direction=direction,
                reason=reason,
                detail=_detail_close_beyond(
                    side=direction,
                    close=candle.close,
                    boundary=boundary,
                    penetration=penetration,
                    penetration_pct=percentage_of(penetration, boundary),
                    tolerance_pct=tolerance_pct,
                    when=candle.timestamp,
                )
                + (
                    ""
                    if state == "confirmed"
                    else "; this is the family's documented failure side, and invalidation is terminal"
                ),
                penetration=penetration,
                penetration_pct=percentage_of(penetration, boundary),
            )
        )
        break

    return tuple(events)
