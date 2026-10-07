"""CONTEXT layer (4H by default): where the market is.

The context layer reuses the existing market-structure engine (Step 3) without
inventing any new indicator. It summarizes the deterministic structure snapshot
for the context timeframe at the decision instant into one explicit regime:

* ``BULLISH_STRUCTURE`` / ``BEARISH_STRUCTURE`` — a sufficient confirmed-swing
  trend in that direction and no active range containing price;
* ``RANGE`` — an active detected range containing the latest close;
* ``TRANSITION`` — neutral or conflicting structure (too few swings, equal
  extremes, or mixed swing comparisons): the context is genuinely uncertain;
* ``UNKNOWN`` — the structure engine had no usable candle window.

Major support/resistance zones, the active range, the confirmed swing count
and the latest close are carried through so lower layers and the dashboard can
reference the same deterministic levels.
"""

from __future__ import annotations

from trading_assistant.market_structure.service import MarketStructureService
from trading_assistant.market_structure.snapshot import MarketStructureSnapshot
from trading_assistant.multi_timeframe.boundaries import TimeframeBoundary
from trading_assistant.multi_timeframe.models import (
    ContextLayerSnapshot,
    ContextRegime,
    LayerEvidence,
)
from trading_assistant.setup_qualification.models import EvidenceStatus


def _nearest_zones(zones, close):
    """The nearest support zone below and resistance zone above ``close``."""

    support = None
    resistance = None
    if close is None:
        return support, resistance
    below = [zone for zone in zones if zone.band_high <= close]
    above = [zone for zone in zones if zone.band_low >= close]
    if below:
        support = max(below, key=lambda zone: zone.band_high)
    if above:
        resistance = min(above, key=lambda zone: zone.band_low)
    return support, resistance


def build_context_snapshot(
    structure: MarketStructureSnapshot | None,
    *,
    boundary: TimeframeBoundary,
    missing_candle_count: int | None = None,
) -> ContextLayerSnapshot:
    """Project one Step 3 structure snapshot into the context layer.

    ``structure`` is the existing market-structure snapshot for the context
    timeframe at the decision instant (already bounded to the latest candle
    closed by that instant). ``None`` means the engine had no usable window.

    ``missing_candle_count`` scopes the layer's incompleteness signal to the
    required context window (Component #1 gate input): when provided it
    replaces the whole-series missing count. ``None`` preserves the legacy
    whole-series behaviour for direct callers; the hierarchy service always
    supplies the scoped required-window count.
    """

    if missing_candle_count is not None and (
        isinstance(missing_candle_count, bool)
        or not isinstance(missing_candle_count, int)
        or missing_candle_count < 0
    ):
        raise ValueError("missing_candle_count must be an integer >= 0 or None")

    timeframe = boundary.timeframe
    evidence: list[LayerEvidence] = []
    scoped = missing_candle_count is not None
    if structure is None or structure.analysis.candle_count == 0:
        legacy_missing = 0 if structure is None else structure.missing_candle_count
        return ContextLayerSnapshot(
            timeframe=timeframe,
            decision_time=boundary.decision_time,
            boundary_open=boundary.candle_open_time,
            boundary_close=boundary.candle_close_time,
            available=False,
            reason=(
                "no stored closed candle window is available for the context "
                "timeframe at the decision time"
                if structure is None or not structure.completeness.candle_count
                else "the stored candle window for the context timeframe is empty"
            ),
            regime=ContextRegime.UNKNOWN,
            trend_direction=None,
            trend_reason=None,
            confirmed_swing_count=0,
            active_range_low=None,
            active_range_high=None,
            nearest_support_band_low=None,
            nearest_support_band_high=None,
            nearest_resistance_band_low=None,
            nearest_resistance_band_high=None,
            latest_close=None,
            candle_count=0,
            missing_candle_count=(
                missing_candle_count if scoped else legacy_missing
            ),
            stale=boundary.stale,
            evidence=(
                LayerEvidence(
                    category="availability",
                    status=EvidenceStatus.UNKNOWN,
                    reason="context structure window unavailable",
                    timeframe=timeframe,
                    observed_at=boundary.decision_time,
                ),
            ),
        )

    analysis = structure.analysis
    trend = analysis.trend
    active_range = analysis.active_range
    latest_close = analysis.volatility.latest_close
    support, resistance = _nearest_zones(analysis.levels.zones, latest_close)

    if active_range is not None and latest_close is not None and (
        active_range.range_low <= latest_close <= active_range.range_high
    ):
        regime = ContextRegime.RANGE
        regime_reason = (
            f"price is inside the active detected range "
            f"{active_range.range_low}–{active_range.range_high}"
        )
    elif trend.direction.value == "bullish" and trend.sufficient:
        regime = ContextRegime.BULLISH_STRUCTURE
        regime_reason = f"confirmed swing structure is bullish ({trend.reason.value})"
    elif trend.direction.value == "bearish" and trend.sufficient:
        regime = ContextRegime.BEARISH_STRUCTURE
        regime_reason = f"confirmed swing structure is bearish ({trend.reason.value})"
    else:
        regime = ContextRegime.TRANSITION
        regime_reason = (
            f"structure is not directional ({trend.reason.value}); the context "
            "layer reports transition/uncertain instead of guessing a regime"
        )

    evidence.append(
        LayerEvidence(
            category="regime",
            status=EvidenceStatus.SUPPORTIVE,
            reason=regime_reason,
            timeframe=timeframe,
            observed_at=boundary.decision_time,
        )
    )
    evidence.append(
        LayerEvidence(
            category="swing_structure",
            status=EvidenceStatus.NEUTRAL,
            reason=(
                f"{trend.confirmed_swing_count} confirmed swing(s); trend "
                f"{trend.direction.value} ({trend.reason.value})"
            ),
            timeframe=timeframe,
            observed_at=boundary.decision_time,
        )
    )
    if support is not None:
        evidence.append(
            LayerEvidence(
                category="support_zone",
                status=EvidenceStatus.NEUTRAL,
                reason=(
                    f"nearest support zone band {support.band_low}–{support.band_high} "
                    f"({support.touch_count} touch(es))"
                ),
                timeframe=timeframe,
                observed_at=boundary.decision_time,
            )
        )
    if resistance is not None:
        evidence.append(
            LayerEvidence(
                category="resistance_zone",
                status=EvidenceStatus.NEUTRAL,
                reason=(
                    f"nearest resistance zone band "
                    f"{resistance.band_low}–{resistance.band_high} "
                    f"({resistance.touch_count} touch(es))"
                ),
                timeframe=timeframe,
                observed_at=boundary.decision_time,
            )
        )
    if boundary.stale:
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    "stored context candles stop before the expected closed "
                    f"candle {boundary.candle_open_time.isoformat()}; the "
                    "context describes the latest stored data, not the newest "
                    "market"
                ),
                timeframe=timeframe,
                observed_at=boundary.decision_time,
            )
        )
    effective_missing = missing_candle_count if scoped else structure.missing_candle_count
    window_incomplete = (effective_missing > 0) if scoped else (
        not structure.completeness.complete
    )
    if window_incomplete:
        evidence.append(
            LayerEvidence(
                category="availability",
                status=EvidenceStatus.OPPOSING,
                reason=(
                    f"the context candle window is incomplete "
                    f"({effective_missing} missing candle(s))"
                ),
                timeframe=timeframe,
                observed_at=boundary.decision_time,
            )
        )

    return ContextLayerSnapshot(
        timeframe=timeframe,
        decision_time=boundary.decision_time,
        boundary_open=boundary.candle_open_time,
        boundary_close=boundary.candle_close_time,
        available=True,
        reason=(
            "stored context candles stop before the expected closed candle"
            if boundary.stale
            else ("the context candle window is incomplete" if window_incomplete else None)
        ),
        regime=regime,
        trend_direction=trend.direction.value,
        trend_reason=trend.reason.value,
        confirmed_swing_count=trend.confirmed_swing_count,
        active_range_low=None if active_range is None else active_range.range_low,
        active_range_high=None if active_range is None else active_range.range_high,
        nearest_support_band_low=None if support is None else support.band_low,
        nearest_support_band_high=None if support is None else support.band_high,
        nearest_resistance_band_low=(
            None if resistance is None else resistance.band_low
        ),
        nearest_resistance_band_high=(
            None if resistance is None else resistance.band_high
        ),
        latest_close=latest_close,
        candle_count=analysis.candle_count,
        missing_candle_count=effective_missing,
        stale=boundary.stale,
        evidence=tuple(evidence),
    )


def context_snapshot(
    service: MarketStructureService,
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of,
) -> MarketStructureSnapshot:
    """Build the context-timeframe structure snapshot at an explicit instant."""

    return service.snapshot(
        exchange=exchange, symbol=symbol, timeframe=timeframe, as_of=as_of
    )


__all__ = ["build_context_snapshot", "context_snapshot"]
