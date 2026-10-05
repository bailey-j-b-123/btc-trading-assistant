"""Boolean/unknown rules over existing facts; no detectors or confidence scores."""

from datetime import datetime

from trading_assistant.market_structure.trend import TrendDirection
from trading_assistant.pattern_liquidity.events import Breakout, ChartPattern
from trading_assistant.setup_qualification.families import (
    Seed,
    direction_for,
    family_for,
    reference_for,
)
from trading_assistant.setup_qualification.models import (
    EvidenceStatus,
    QualificationEvidence,
    QualificationFrame,
    RuleOutcome,
    RuleResult,
    SetupFamily,
)
from trading_assistant.setup_qualification.parameters import (
    QualificationParameters,
    fingerprint,
)


def rule(
    name: str,
    passed: bool | None,
    reason: str,
    *,
    timeframe: str,
    source: str | None = None,
    observed: datetime | None = None,
    confirmed: datetime | None = None,
    category: str = "context",
    required: bool = True,
    veto: bool = False,
    neutral: bool = False,
) -> RuleResult:
    outcome = (
        RuleOutcome.PENDING
        if passed is None
        else RuleOutcome.PASS
        if passed
        else RuleOutcome.FAIL
    )
    status = (
        EvidenceStatus.UNKNOWN
        if passed is None
        else EvidenceStatus.NEUTRAL
        if neutral
        else EvidenceStatus.SUPPORTIVE
        if passed
        else EvidenceStatus.OPPOSING
    )
    return RuleResult(
        name,
        required,
        outcome,
        reason,
        (
            QualificationEvidence(
                source, timeframe, observed, confirmed, category, status, reason
            ),
        ),
        veto,
    )


def evaluate_rules(
    seed: Seed, frame: QualificationFrame, p: QualificationParameters
) -> tuple[RuleResult, ...]:
    snapshot = frame.patterns
    context = snapshot.structure
    timeframe = snapshot.timeframe
    now = snapshot.as_of
    family = family_for(seed)
    direction = direction_for(seed)
    ref = reference_for(seed)
    results = []

    def add(name, passed, reason, *, fact=None, event=None, **kwargs):
        results.append(
            rule(
                name,
                passed,
                reason,
                timeframe=timeframe,
                source=event.id
                if event
                else fingerprint(name, fact)
                if fact is not None
                else None,
                observed=event.candle.timestamp
                if event
                else context.window_end_timestamp
                if fact is not None
                else None,
                confirmed=event.known_at
                if event
                else now
                if fact is not None
                else None,
                **kwargs,
            )
        )

    add(
        "seed_event",
        True,
        f"confirmed {type(seed).__name__}; reference={ref.id}",
        event=seed,
        category="event",
    )
    # The seed close can create WATCH only; confirmation is assessed on a later bar.
    add(
        "later_evaluation",
        True if now > seed.known_at else None,
        "evaluation must be strictly after seed confirmation",
        fact=seed.known_at,
        category="timing",
    )
    if family.family == SetupFamily.BREAKOUT_RETEST:
        held = next(
            (
                e
                for e in sorted(snapshot.retests, key=lambda e: (e.known_at, e.id))
                if e.breakout.id == seed.id
                and e.state == "held"
                and e.known_at > seed.known_at
            ),
            None,
        )
        add(
            "held_retest",
            True if held else None,
            "requires Step 4 held retest of the seed breakout",
            event=held,
            category="confirmation",
        )
        failed_break = next(
            (e for e in snapshot.failed_breakouts if e.breakout.id == seed.id), None
        )
        failed_retest = next(
            (
                e
                for e in snapshot.retests
                if e.breakout.id == seed.id and e.state == "failed"
            ),
            None,
        )
        add(
            "no_failed_breakout",
            failed_break is None,
            "catalog must contain no confirmed failure of seed breakout",
            event=failed_break,
            category="invalidation",
        )
        add(
            "no_failed_retest",
            failed_retest is None,
            "catalog must contain no confirmed failed retest of seed breakout",
            event=failed_retest,
            category="invalidation",
        )
    elif family.family == SetupFamily.LIQUIDITY_REVERSAL:
        confirmation = next(
            (
                e
                for e in sorted(snapshot.breakouts, key=lambda e: (e.known_at, e.id))
                if e.direction == direction
                and e.known_at > seed.known_at
                and e.reference.id != ref.id
            ),
            None,
        )
        add(
            "reversal_breakout",
            True if confirmation else None,
            "requires later directional Step 4 breakout at a different reference",
            event=confirmation,
            category="confirmation",
        )
    else:
        frozen = ref.range
        active = context.active_range
        match = (
            None
            if active is None or frozen is None
            else (
                active.range_low == frozen.range_low
                and active.range_high == frozen.range_high
            )
        )
        add(
            "active_range",
            match,
            f"active_bounds={None if active is None else (active.range_low, active.range_high)}; "
            f"frozen_bounds={None if frozen is None else (frozen.range_low, frozen.range_high)}; "
            "current active Step 3 range must match frozen seed bounds",
            fact=active,
            category="location",
        )
        close = context.volatility.latest_close
        follow = (
            None
            if close is None or frozen is None
            else (
                frozen.range_low <= close <= frozen.range_high
                and (
                    close > seed.candle.close
                    if direction == "bullish"
                    else close < seed.candle.close
                )
            )
        )
        add(
            "range_followthrough",
            follow,
            f"current_close={close}; seed_close={seed.candle.close}; "
            "later close must be inside frozen range and strictly farther inward",
            fact=context.volatility,
            category="confirmation",
        )

    trend = context.trend
    aligned = (
        None
        if not trend.sufficient
        else (
            trend.direction == direction
            if isinstance(seed, Breakout)
            else trend.direction in (direction, TrendDirection.NEUTRAL)
        )
    )
    add(
        "structure",
        aligned,
        f"trend={trend.direction}; reason={trend.reason}; continuation requires alignment, reversal allows sufficient neutral",
        fact=trend,
        category="structure",
        neutral=aligned is True and trend.direction == TrendDirection.NEUTRAL,
    )
    volume = context.volume
    relative = volume.relative_volume if volume.available else None
    add(
        "volume",
        None if relative is None else relative >= p.min_relative_volume,
        f"relative_volume={relative}; minimum={p.min_relative_volume}; source_reason={volume.reason}",
        fact=volume,
        category="volume",
    )
    volatility = context.volatility
    atr = volatility.atr_percent_of_price if volatility.available else None
    add(
        "volatility",
        None if atr is None else 0 < atr <= p.max_atr_percent,
        f"ATR_percent={atr}; requires 0 < ATR_percent <= {p.max_atr_percent}; source_reason={volatility.reason}",
        fact=volatility,
        category="volatility",
    )
    close = volatility.latest_close
    location = (
        None
        if close is None
        else (
            close >= ref.band_high if direction == "bullish" else close <= ref.band_low
        )
    )
    add(
        "location",
        location,
        f"close={close}; requires directional side of frozen band [{ref.band_low}, {ref.band_high}]",
        fact=volatility,
        category="location",
    )

    contexts = {h.timeframe: h for h in frame.higher_timeframes}
    for tf in p.higher_timeframes:
        higher = contexts.get(tf)
        trend = (
            higher.trend
            if higher
            and higher.available
            and higher.completeness.complete
            and not higher.synthesized
            else None
        )
        unavailable_reason = (
            "missing_context"
            if higher is None
            else "synthesized_context_not_usable"
            if higher.synthesized
            else higher.reason or "unavailable_context"
            if not higher.available
            else "incomplete_context"
            if not higher.completeness.complete
            else "missing_analysis"
            if trend is None
            else "insufficient_swings"
            if not trend.sufficient
            else None
        )
        usable = trend is not None and trend.sufficient
        aligned = None if not usable else trend.direction == direction
        opposite = usable and trend.direction not in (direction, TrendDirection.NEUTRAL)
        # Optional means absence/neutral cannot block, NOT that known opposition is ignored.
        results.append(
            rule(
                f"higher_timeframe:{tf}",
                aligned,
                f"requires aligned trend when mandatory; known opposite trend vetoes; "
                f"trend={trend.direction if usable else 'UNKNOWN'}; unavailable_reason={unavailable_reason}",
                timeframe=tf,
                source=fingerprint(tf, trend) if usable else None,
                observed=higher.analysis.window_end_timestamp if usable else None,
                confirmed=now if usable else None,
                category="higher_timeframe",
                required=p.require_higher_timeframe_alignment,
                veto=bool(opposite),
                neutral=usable and trend.direction == TrendDirection.NEUTRAL,
            )
        )
    if not p.higher_timeframes:
        add(
            "higher_timeframe:not_requested",
            None,
            "no higher timeframes requested; no alignment inferred",
            category="higher_timeframe",
            required=False,
        )

    # Latest occurrence for each geometry; formed/invalidated are never confirmed support.
    latest: dict[str, ChartPattern] = {}
    for event in sorted(snapshot.chart_patterns, key=lambda e: (e.known_at, e.id)):
        latest[event.pattern_id] = event
    patterns = [
        e
        for e in latest.values()
        if e.state == "confirmed" and e.known_at >= seed.known_at
    ]
    if not patterns:
        add(
            "classical_pattern",
            None,
            "no current confirmed classical pattern since seed",
            category="classical_pattern",
            required=False,
        )
    for event in sorted(patterns, key=lambda e: (e.known_at, e.id)):
        pattern_direction = (
            "bullish"
            if event.type in ("double_bottom", "inverse_head_and_shoulders")
            else "bearish"
        )
        results.append(
            rule(
                f"classical_pattern:{event.id}",
                pattern_direction == direction,
                f"{event.type}; optional only; cannot qualify or veto a setup",
                timeframe=timeframe,
                source=event.id,
                observed=event.confirmation_timestamp,
                confirmed=event.known_at,
                category="classical_pattern",
                required=False,
            )
        )
    return tuple(results)
