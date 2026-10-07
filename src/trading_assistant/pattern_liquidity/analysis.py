"""Chronological replay. Every detector sees only its current closed prefix.

Gap boundaries discard pending candidates and all live references. Recorded
pre-gap evidence remains; post-gap structure starts from an empty segment.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime

from trading_assistant.market_data.timeframes import (
    datetime_to_milliseconds,
    latest_closed_candle_open_time,
    require_utc_datetime,
)
from trading_assistant.market_data.types import Candle
from trading_assistant.market_data.validation import validate_ohlcv_rows
from trading_assistant.market_structure.analysis import analyze_candles
from trading_assistant.market_structure.candles import (
    candles_closed_by,
    interval_for_timeframe,
)
from trading_assistant.market_structure.completeness import describe_completeness
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.pattern_liquidity.breakouts import detect_breakout
from trading_assistant.pattern_liquidity.classical_patterns import (
    advance_pattern,
    detect_shapes,
)
from trading_assistant.pattern_liquidity.equal_levels import detect_equal_levels
from trading_assistant.pattern_liquidity.failures import detect_failure
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.pattern_liquidity.references import references
from trading_assistant.pattern_liquidity.retests import detect_retest
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot
from trading_assistant.pattern_liquidity.sweeps import detect_sweep


def analyze_patterns(
    candles: Iterable[Candle],
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    parameters: PatternLiquidityParameters | None = None,
    structure_parameters: MarketStructureParameters | None = None,
) -> PatternLiquiditySnapshot:
    as_of = require_utc_datetime(as_of, field_name="as_of")
    if not exchange.strip() or not symbol.strip():
        raise ValueError("exchange and symbol must not be empty")
    p = parameters or PatternLiquidityParameters()
    sp = structure_parameters or MarketStructureParameters()
    interval = interval_for_timeframe(timeframe)
    instrument = (exchange, symbol, timeframe)
    # Filter before analysis: even excluded-future diagnostics must be prefix invariant.
    closed = candles_closed_by(candles, interval=interval, as_of=as_of)
    if any((c.exchange, c.symbol, c.timeframe) != instrument for c in closed):
        raise ValueError("candle instrument does not match requested instrument")
    expected = latest_closed_candle_open_time(as_of, timeframe)
    # Reuse Step 2 validation/gap calculation, never fabricate or repair rows.
    validated = validate_ohlcv_rows(
        [
            [
                datetime_to_milliseconds(c.timestamp),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
            for c in closed
        ],
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        expected_start=closed[0].timestamp if closed else None,
        expected_end=expected if closed else None,
    )
    if validated.rejected_count:
        raise ValueError("invalid source candles: " + str(validated.issues))
    if any(min(c.open, c.high, c.low, c.close) <= 0 for c in closed):
        raise ValueError(
            "Step 4 requires strictly positive OHLC prices for percentage geometry"
        )
    completeness = describe_completeness(
        closed,
        interval=interval,
        expected_latest_closed_open_time=expected,
        gaps=validated.gaps,
    )
    breakouts, failures, sweeps, retests, clusters, patterns = {}, {}, {}, {}, {}, {}
    pending = []
    live_breakouts, live_patterns = {}, {}
    observed, completed_retests, failed = set(), set(), set()
    segment = ()
    context = analyze_candles((), interval=interval, as_of=as_of, parameters=sp)
    # When candles are contiguous, the previous iteration's ``context`` already
    # is this iteration's ``previous_context``: same segment, and ``as_of`` equal
    # to the previous close (``analyze_candles`` is pure, so reuse is exact, not
    # approximate). A gap reset discards the carry, exactly like detector state.
    carried_context = None
    for candle in closed:
        if segment and candle.timestamp - segment[-1].timestamp != interval:
            segment = ()
            pending.clear()
            live_breakouts.clear()
            live_patterns.clear()
            carried_context = None
        if carried_context is None:
            carried_context = analyze_candles(
                segment, interval=interval, as_of=candle.timestamp, parameters=sp
            )
        previous_context = carried_context
        previous = segment[-1] if segment else None
        segment = (*segment, candle)
        now = candle.timestamp + interval
        context = analyze_candles(segment, interval=interval, as_of=now, parameters=sp)
        carried_context = context
        if previous:
            for ref in references(previous_context, instrument):
                directions = (
                    ("bullish",)
                    if ref.type in ("swing_high", "range_high")
                    else ("bearish",)
                    if ref.type in ("swing_low", "range_low")
                    else ("bullish", "bearish")
                )
                for direction in directions:
                    pending.append((ref, direction, previous, ()))
                    sweep = detect_sweep(ref, direction, previous, candle, context, p)
                    if sweep:
                        sweeps.setdefault(sweep.id, sweep)
        next_pending = []
        for ref, direction, prior, evidence in pending:
            evidence = (*evidence, candle)
            if len(evidence) < p.breakout_confirmation_candles:
                next_pending.append((ref, direction, prior, evidence))
                continue
            breakout = detect_breakout(ref, direction, prior, evidence, context, p)
            if breakout:
                breakouts.setdefault(breakout.id, breakout)
                live_breakouts.setdefault(breakout.id, breakout)
        pending = next_pending
        for breakout_id, breakout in tuple(live_breakouts.items()):
            if (
                now - breakout.known_at
                > max(p.failure_window_candles, p.retest_window_candles) * interval
            ):
                del live_breakouts[breakout_id]
                continue
            evidence = tuple(c for c in segment if c.timestamp >= breakout.known_at)
            if not evidence:
                continue
            if breakout.id not in failed:
                failure = detect_failure(breakout, evidence, interval, p)
                if failure:
                    failures.setdefault(failure.id, failure)
                    failed.add(breakout.id)
            if breakout.id not in completed_retests:
                for retest in detect_retest(
                    breakout, evidence, interval, p, breakout.id in observed
                ):
                    retests.setdefault(retest.id, retest)
                    observed.add(breakout.id)
                    if retest.state != "observed":
                        completed_retests.add(breakout.id)
            # A failure without a band visit ends the retest candidate as well.
            if breakout.id in failed:
                completed_retests.add(breakout.id)
        for cluster in detect_equal_levels(context, instrument, p):
            clusters.setdefault(cluster.id, cluster)
        for shape in detect_shapes(context, instrument, interval, p):
            if shape.id not in patterns:
                # Retain all source candles, including swing confirmation windows.
                shape = replace(
                    shape,
                    evidence_candles=tuple(
                        c
                        for c in segment
                        if shape.components[0].timestamp
                        - sp.swings.left_window * interval
                        <= c.timestamp
                        and c.timestamp + interval <= shape.formed_at
                    ),
                )
                patterns[shape.id] = shape
                live_patterns[shape.pattern_id] = shape
        for pattern_id, shape in tuple(live_patterns.items()):
            evidence = tuple(c for c in segment if c.timestamp >= shape.formed_at)
            if not evidence:
                continue
            transition = advance_pattern(shape, evidence, interval, p)
            if transition:
                patterns[transition.id] = transition
                del live_patterns[pattern_id]
    # Context is the final contiguous segment, explicitly not a cross-gap structure.
    context = analyze_candles(segment, interval=interval, as_of=as_of, parameters=sp)
    reasons = []
    if not completeness.complete:
        reasons.append(
            "no_stored_candles" if not closed else "gaps_reset_detector_state"
        )
    if not context.swings.sufficient:
        reasons.append("insufficient_contiguous_candles")
    elif not context.confirmed_swings:
        reasons.append("no_confirmed_structural_references")
    status = (
        "incomplete"
        if closed and not completeness.complete
        else "insufficient"
        if reasons
        else "evaluated"
    )

    def ordered(events):
        return tuple(sorted(events.values(), key=lambda e: (e.known_at, e.id)))

    return PatternLiquiditySnapshot(
        exchange,
        symbol,
        timeframe,
        as_of,
        ordered(breakouts),
        ordered(failures),
        ordered(sweeps),
        ordered(retests),
        ordered(clusters),
        ordered(patterns),
        context,
        completeness,
        p,
        status,
        tuple(reasons),
    )
