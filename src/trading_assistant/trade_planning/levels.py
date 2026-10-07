"""Family-specific level derivation from existing frozen Step 3–5 evidence.

This module adapts facts already recorded upstream into planning levels. It
re-runs no detector, recalculates no structure, and never reaches past the
planning ``as_of``: every source timestamp is bounded at or before that instant,
and candidates whose evidence was not yet known are excluded. Confirmation
selection mirrors the exact Step 5 rule predicates so the planner consumes the
same frozen confirmation the qualification relied on, identified by event ID.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_assistant.market_structure.numeric import quantize_derived, tolerance_band
from trading_assistant.market_structure.volatility import VolatilityContext
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    Direction,
    FailedBreakout,
    Reference,
    Retest,
    Sweep,
)
from trading_assistant.pattern_liquidity.references import references
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot
from trading_assistant.setup_qualification.families import reference_for
from trading_assistant.setup_qualification.models import SetupFamily
from trading_assistant.trade_planning.parameters import (
    PlanningParameters,
    StopBufferMode,
    decimal_text,
    fingerprint,
)

Seed = Breakout | FailedBreakout | Sweep
Confirmation = Breakout | Retest


def is_available_at(timestamp: datetime | None, as_of: datetime) -> bool:
    """A known-after-``as_of`` timestamp is a future fact; ``None`` is unknown.

    Unknown timestamps are not treated as violations here: missing data is
    reported through ``missing_inputs`` by the planner, while a *future* fact
    is a hard contradiction that INVALIDates the plan.
    """
    return timestamp is None or timestamp <= as_of


def find_seed(patterns: PatternLiquiditySnapshot, seed_id: str) -> Seed | None:
    """Return the exact frozen seed event from the frame catalog, if present."""
    for event in patterns.events():
        if isinstance(event, (Breakout, FailedBreakout, Sweep)) and event.id == seed_id:
            return event
    return None


def select_confirmation(
    patterns: PatternLiquiditySnapshot,
    seed: Seed,
    family: SetupFamily,
    direction: Direction,
) -> Confirmation | None:
    """Locate the frozen family confirmation exactly as Step 5 defined it.

    Continuation: the earliest later ``held`` retest of the seed breakout.
    Reversal: the earliest later directional breakout at a different reference.
    Range reversal has no separate confirmation event (its follow-through is
    re-verified from current context), so ``None`` is expected for that family.
    """
    if family is SetupFamily.BREAKOUT_RETEST:
        candidates = (
            e
            for e in sorted(patterns.retests, key=lambda e: (e.known_at, e.id))
            if e.breakout.id == seed.id
            and e.state == "held"
            and e.known_at > seed.known_at
        )
        return next(candidates, None)
    if family is SetupFamily.LIQUIDITY_REVERSAL:
        # A FailedBreakout seed carries its reference on the wrapped breakout,
        # exactly as Step 5 resolves it; direct ``seed.reference`` access raised
        # AttributeError for that seed kind.
        seed_reference = reference_for(seed)
        candidates = (
            e
            for e in sorted(patterns.breakouts, key=lambda e: (e.known_at, e.id))
            if e.direction == direction
            and e.known_at > seed.known_at
            and e.reference.id != seed_reference.id
        )
        return next(candidates, None)
    return None


def is_long(direction: Direction) -> bool:
    return direction == "bullish"


def reference_observed_at(reference: Reference) -> datetime | None:
    """The earliest source-observation time carried by the reference itself."""
    if reference.swings:
        return min(swing.timestamp for swing in reference.swings)
    if reference.zone is not None:
        return reference.zone.first_observed_timestamp
    if reference.range is not None:
        return reference.range.start_timestamp
    return None


def usable_level(value: object) -> bool:
    """A planning level is usable only as a finite, strictly positive Decimal."""
    return (
        isinstance(value, Decimal)
        and not isinstance(value, bool)
        and value.is_finite()
        and value > 0
    )


def plan_close_level(
    volatility: VolatilityContext, timeframe: str, interval: timedelta
) -> tuple[Decimal | None, str, datetime | None, datetime | None]:
    """Latest closed close plus its exact Step 3 context provenance.

    ``timeframe`` documents which stored frame the close came from; the close
    is known once its candle's full interval has closed, so the confirmed time
    is the observed open plus the interval, derived here without invention.
    """
    close = volatility.latest_close
    observed = volatility.latest_candle_timestamp
    confirmed = observed + interval if observed is not None else None
    source_id = fingerprint("volatility_latest_close", volatility)
    return (
        close if isinstance(close, Decimal) else None,
        source_id,
        observed,
        confirmed,
    )


def frozen_confirmation_level(
    family: SetupFamily,
    seed: Seed,
    confirmation: Confirmation | None,
) -> tuple[Decimal | None, str | None, str | None, datetime | None, datetime | None]:
    """Family-specific frozen confirmation close, or ``None`` when absent.

    Continuation uses the held-retest candle close; reversal uses the
    confirmation breakout's close; range reversal uses the seed sweep's reclaim
    close or the failed breakout's re-entry close.
    """
    if family is SetupFamily.BREAKOUT_RETEST:
        if not isinstance(confirmation, Retest):
            return None, None, None, None, None
        return (
            confirmation.candle.close,
            confirmation.id,
            "step4_retest_close",
            confirmation.candle.timestamp,
            confirmation.known_at,
        )
    if family is SetupFamily.LIQUIDITY_REVERSAL:
        if not isinstance(confirmation, Breakout):
            return None, None, None, None, None
        return (
            confirmation.breakout_close,
            confirmation.id,
            "step4_breakout_close",
            confirmation.candle.timestamp,
            confirmation.known_at,
        )
    if isinstance(seed, Sweep):
        return (
            seed.reclaim_close,
            seed.id,
            "step4_sweep_reclaim_close",
            seed.candle.timestamp,
            seed.known_at,
        )
    if isinstance(seed, FailedBreakout):
        return (
            seed.candle.close,
            seed.id,
            "step4_failure_reentry_close",
            seed.candle.timestamp,
            seed.known_at,
        )
    return None, None, None, None, None


def stop_buffer(
    parameters: PlanningParameters,
    invalidation: Decimal,
    volatility: VolatilityContext,
    direction: Direction,
) -> tuple[Decimal, str, str | None]:
    """Return the buffered stop level, its exact formula text, and a gap name.

    A non-``None`` gap name means a required buffer input is missing; the
    planner must then report the gap instead of silently substituting a
    different buffer. ``invalidation`` is returned unchanged only for that
    gap-report path (the level itself is never consumed by the planner).
    """
    sign = "-" if is_long(direction) else "+"
    mode = parameters.stop_buffer_mode
    if mode is StopBufferMode.NONE:
        return (
            invalidation,
            "no buffer applied: stop equals the logical invalidation level",
            None,
        )
    if mode is StopBufferMode.ATR:
        atr = volatility.atr
        if not volatility.available or not isinstance(atr, Decimal):
            return invalidation, "", "volatility.atr"
        multiple = parameters.stop_buffer_atr_multiple
        buffer = quantize_derived(atr * multiple)
        formula = (
            f"invalidation {sign} quantize_derived(atr {decimal_text(atr)} x "
            f"stop_buffer_atr_multiple {decimal_text(multiple)}) = "
            f"buffer {decimal_text(buffer)}"
        )
        level = invalidation - buffer if is_long(direction) else invalidation + buffer
        return level, formula, None
    percentage = parameters.stop_buffer_percentage
    buffer = tolerance_band(invalidation, percentage)
    formula = (
        f"invalidation {sign} tolerance_band({decimal_text(invalidation)}, "
        f"stop_buffer_percentage {decimal_text(percentage)}%) = "
        f"buffer {decimal_text(buffer)}"
    )
    level = invalidation - buffer if is_long(direction) else invalidation + buffer
    return level, formula, None


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    """One structural target proposal with full source provenance."""

    value: Decimal
    source_id: str
    source_type: str
    observed_at: datetime | None
    confirmed_at: datetime | None


def _side_reason(value: object, entry: Decimal, long: bool) -> str | None:
    if value is None or not isinstance(value, Decimal) or not value.is_finite():
        return "missing or non-finite candidate level"
    if value <= 0:
        return "non-positive candidate level"
    if long and value <= entry:
        return "candidate level not strictly above entry"
    if not long and value >= entry:
        return "candidate level not strictly below entry"
    return None


def structural_target_candidates(
    patterns: PatternLiquiditySnapshot,
    entry: Decimal,
    direction: Direction,
    parameters: PlanningParameters,
    as_of: datetime,
) -> tuple[tuple[TargetCandidate, ...], tuple[str, ...]]:
    """Select ordered, de-duplicated structural levels known at ``as_of``.

    Sources are the existing Step 4 reference adapter over the frame's Step 3
    analysis (confirmed swings, zones, and the frozen active range) plus, when
    configured, Step 4 equal-level clusters. Selection is pure filtering and
    sorting: nearest first, then level, then source ID; exact duplicate levels
    collapse to the first-sorted source. Invalid candidates are returned as
    explicit rejection strings instead of being dropped silently, and anything
    not yet known at ``as_of`` can never become a historical target.
    """
    long = is_long(direction)
    instrument = (patterns.exchange, patterns.symbol, patterns.timeframe)
    candidates: list[TargetCandidate] = []
    rejected: list[str] = []
    for reference in references(patterns.structure, instrument):
        if not is_available_at(reference.known_at, as_of):
            rejected.append(f"{reference.id}: reference confirmed after planning as_of")
            continue
        value = reference.band_low if long else reference.band_high
        reason = _side_reason(value, entry, long)
        if reason is not None:
            rejected.append(f"{reference.id}: {reason}")
            continue
        candidates.append(
            TargetCandidate(
                value=value,
                source_id=reference.id,
                source_type=f"step4_reference_{reference.type}",
                observed_at=reference_observed_at(reference),
                confirmed_at=reference.known_at,
            )
        )
    if parameters.include_equal_levels_as_targets:
        for cluster in patterns.equal_levels:
            if not is_available_at(cluster.known_at, as_of):
                rejected.append(f"{cluster.id}: cluster confirmed after planning as_of")
                continue
            value = cluster.band_low if long else cluster.band_high
            reason = _side_reason(value, entry, long)
            if reason is not None:
                rejected.append(f"{cluster.id}: {reason}")
                continue
            candidates.append(
                TargetCandidate(
                    value=value,
                    source_id=cluster.id,
                    source_type="step4_equal_level_cluster",
                    observed_at=(
                        min(swing.timestamp for swing in cluster.members)
                        if cluster.members
                        else None
                    ),
                    confirmed_at=cluster.known_at,
                )
            )
    candidates.sort(
        key=lambda c: (
            c.value - entry if long else entry - c.value,
            c.value,
            c.source_id,
        )
    )
    selected: list[TargetCandidate] = []
    for candidate in candidates:
        if selected and selected[-1].value == candidate.value:
            rejected.append(
                f"{candidate.source_id}: duplicate of already-selected level "
                f"{decimal_text(candidate.value)}"
            )
            continue
        selected.append(candidate)
    cap = parameters.max_structural_targets
    for extra in selected[cap:]:
        rejected.append(
            f"{extra.source_id}: beyond max_structural_targets {cap} at level "
            f"{decimal_text(extra.value)}"
        )
    return tuple(selected[:cap]), tuple(rejected)
