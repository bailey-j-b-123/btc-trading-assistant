"""Step 3 market-structure engine tests (pure, deterministic components).

Every fixture in this file is synthetic and generated from explicit numbers;
no test contacts an exchange or reads project data.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from market_structure_fixtures import (
    EPOCH,
    INTERVAL,
    analytic_candles,
    candle_at,
    candles_from_rows,
    constant_volume_candles,
    flat_candles,
    sample_swing,
    zigzag_candles,
)

from trading_assistant.market_structure import (
    ATR_SMOOTHING,
    LevelParameters,
    MarketStructureParameters,
    RangeParameters,
    RangeRejectionReason,
    SwingKind,
    SwingParameters,
    SwingTiePolicy,
    TrendDirection,
    TrendParameters,
    TrendReason,
    VolatilityParameters,
    VolumeParameters,
    analyze_candles,
    calculate_volatility,
    calculate_volume,
    classify_trend,
    detect_range,
    detect_swings,
    detect_zones,
    higher_timeframes_for,
    to_jsonable,
    true_range,
    wilder_average,
)

RISING_PIVOTS = ("100", "104", "102", "106", "104", "108", "106", "110")
FALLING_PIVOTS = ("110", "106", "108", "104", "106", "102", "104", "100")
CONFLICTING_PIVOTS = ("100", "110", "108", "112", "106", "114", "104", "116")
RANGE_PIVOTS = ("100", "104", "100", "104", "100", "104", "100", "104", "100", "104")


def as_of_after(candles, *, extra: int = 0):
    """Return the instant at which the last candle (plus ``extra``) has closed."""

    return candles[-1].timestamp + INTERVAL * (1 + extra)


def analyze(candles, *, as_of=None, parameters=None, interval=INTERVAL):
    return analyze_candles(
        candles,
        interval=interval,
        as_of=as_of_after(candles) if as_of is None else as_of,
        parameters=parameters,
    )


# --------------------------------------------------------------------------- #
# 1. Swing structure
# --------------------------------------------------------------------------- #


def test_confirmed_swing_highs_and_lows_are_detected_at_explicit_pivots():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    result = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles))

    kinds_and_indices = [
        (swing.kind, (swing.timestamp - EPOCH) // INTERVAL) for swing in result.swings
    ]
    assert kinds_and_indices == [
        (SwingKind.HIGH, 5),
        (SwingKind.LOW, 10),
        (SwingKind.HIGH, 15),
        (SwingKind.LOW, 20),
        (SwingKind.HIGH, 25),
        (SwingKind.LOW, 30),
    ]
    assert [swing.price for swing in result.swings] == [
        Decimal("104.475"),
        Decimal("101.550"),
        Decimal("106.425"),
        Decimal("103.600"),
        Decimal("108.375"),
        Decimal("105.650"),
    ]
    assert result.sufficient
    assert result.unconfirmed_count == 0
    # 36 candles: the first and last two can never hold a complete window.
    assert result.evaluated_candidate_count == 32
    assert result.insufficient_window_count == 4
    assert result.gap_window_count == 0
    # The final fixture candle is a pivot but cannot be confirmed yet.
    assert all((swing.timestamp - EPOCH) // INTERVAL != 35 for swing in result.swings)


def test_swing_is_not_confirmed_before_its_right_window_candles_close():
    candles = zigzag_candles(("100", "104", "98"), leg=2)  # peak at candle index 2
    peak_index = 2
    peak = candles[peak_index]
    assert peak.high == max(candle.high for candle in candles)

    # Only candles 0..2 exist: the peak cannot be confirmed or even evaluated.
    early = detect_swings(candles[:3], interval=INTERVAL, as_of=as_of_after(candles[:3]))
    assert early.swings == ()
    assert early.insufficient_window_count == 3
    assert not early.sufficient

    # Exactly the required right-window candles have closed.
    confirming = candles[: peak_index + 3]
    at_confirmation = detect_swings(
        confirming,
        interval=INTERVAL,
        as_of=confirming[-1].timestamp + INTERVAL,
    )
    swing = at_confirmation.swings[0]
    assert swing.kind is SwingKind.HIGH
    assert swing.confirmed_by_timestamp == candles[peak_index + 2].timestamp
    assert swing.confirmed_at == candles[peak_index + 2].timestamp + INTERVAL

    # One microsecond before that close instant it is still not knowable.
    just_before = detect_swings(
        confirming,
        interval=INTERVAL,
        as_of=swing.confirmed_at - timedelta(microseconds=1),
    )
    assert just_before.swings == ()
    assert just_before.unconfirmed_count == 1


def test_swing_requires_the_full_right_window_to_be_closed_by_as_of():
    candles = zigzag_candles(("100", "104", "98"), leg=2)
    peak = next(swing for swing in detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings)

    # Candles beyond the peak exist in the fixture, but a historical as_of that
    # predates the confirming close must not expose the swing.
    historical = detect_swings(
        candles,
        interval=INTERVAL,
        as_of=peak.timestamp + INTERVAL * 2,
    )
    assert historical.swings == ()
    assert historical.unconfirmed_count == 1


def test_equal_highs_are_handled_by_the_strict_tie_policy():
    candles = candles_from_rows(
        [
            ("9.4", "9.9", "9.0", "9.4"),  # 0
            ("9.4", "9.8", "9.2", "9.5"),  # 1
            ("9.5", "9.9", "9.3", "9.6"),  # 2
            ("9.6", "100", "9.5", "10"),  # 3  <- tied high
            ("10", "100", "9.7", "99"),  # 4  <- tied high (same value as candle 3)
            ("99", "99.5", "98.5", "99.4"),  # 5
            ("99.4", "99.8", "98.8", "99.5"),  # 6
        ]
    )
    strict = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles))
    assert strict.highs == ()
    assert strict.tie_rejection_count == 2

    earliest = detect_swings(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=SwingParameters(tie_policy=SwingTiePolicy.EARLIEST_EQUAL),
    )
    assert [swing.price for swing in earliest.highs] == [Decimal(100)]
    assert [swing.timestamp for swing in earliest.highs] == [candles[3].timestamp]
    assert earliest.tie_rejection_count == 1


def test_equal_lows_are_handled_by_the_strict_tie_policy():
    candles = candles_from_rows(
        [
            ("10.6", "11.0", "10.1", "10.6"),  # 0
            ("10.6", "10.8", "10.2", "10.5"),  # 1
            ("10.5", "10.7", "10.1", "10.4"),  # 2
            ("10.4", "10.5", "5", "10"),  # 3  <- tied low
            ("10", "10.3", "5", "5.2"),  # 4  <- tied low (same value as candle 3)
            ("5.2", "5.6", "5.1", "5.5"),  # 5
            ("5.5", "5.9", "5.3", "5.7"),  # 6
        ]
    )
    strict = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles))
    assert strict.lows == ()
    assert strict.tie_rejection_count == 2

    earliest = detect_swings(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=SwingParameters(tie_policy=SwingTiePolicy.EARLIEST_EQUAL),
    )
    assert [swing.price for swing in earliest.lows] == [Decimal(5)]
    assert [swing.timestamp for swing in earliest.lows] == [candles[3].timestamp]
    assert earliest.tie_rejection_count == 1


def test_windows_spanning_a_candle_gap_are_not_evaluated():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    with_gap = tuple(candle for candle in candles if candle.timestamp != candles[10].timestamp)

    result = detect_swings(with_gap, interval=INTERVAL, as_of=as_of_after(candles))
    assert result.gap_window_count == 4
    # The low at index 10 (whose window spans the hole) is absent, and no swing
    # is invented at either edge of the gap.
    assert all(
        (swing.timestamp - EPOCH) // INTERVAL not in {8, 9, 10, 11, 12}
        for swing in result.swings
    )


def test_inconsistent_candle_input_is_rejected_not_reordered():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    with pytest.raises(ValueError, match="strictly ordered"):
        detect_swings(tuple(reversed(candles)), interval=INTERVAL, as_of=as_of_after(candles))
    with pytest.raises(ValueError, match="whole multiple"):
        detect_swings(
            candles,
            interval=timedelta(minutes=25),
            as_of=as_of_after(candles),
        )
    other_instrument = candle_at(
        0,
        open_="1",
        high="2",
        low="1",
        close="2",
        timeframe="4h",
    )
    with pytest.raises(ValueError, match="share exchange, symbol, and timeframe"):
        detect_swings(
            (candles[0], other_instrument),
            interval=INTERVAL,
            as_of=as_of_after(candles),
        )


# --------------------------------------------------------------------------- #
# 2. Structural trend
# --------------------------------------------------------------------------- #


def test_bullish_structure_is_classified_from_rising_highs_and_lows():
    analysis = analyze(zigzag_candles(RISING_PIVOTS, leg=5))
    trend = analysis.trend

    assert trend.direction is TrendDirection.BULLISH
    assert trend.reason is TrendReason.HIGHER_HIGHS_AND_HIGHER_LOWS
    assert trend.higher_highs is True
    assert trend.higher_lows is True
    assert trend.lower_highs is False
    assert trend.lower_lows is False
    assert [swing.price for swing in trend.swing_highs] == [Decimal("106.425"), Decimal("108.375")]
    assert [swing.price for swing in trend.swing_lows] == [Decimal("103.600"), Decimal("105.650")]
    assert trend.evidence
    assert trend.excluded_unconfirmed_swing_count == 0


def test_bearish_structure_is_classified_from_falling_highs_and_lows():
    analysis = analyze(zigzag_candles(FALLING_PIVOTS, leg=5))
    trend = analysis.trend

    assert trend.direction is TrendDirection.BEARISH
    assert trend.reason is TrendReason.LOWER_HIGHS_AND_LOWER_LOWS
    assert trend.lower_highs is True
    assert trend.lower_lows is True
    assert trend.higher_highs is False
    assert trend.higher_lows is False
    assert [swing.price for swing in trend.swing_highs] == [
        Decimal("106.400"),
        Decimal("104.350"),
    ]
    assert [swing.price for swing in trend.swing_lows] == [
        Decimal("103.575"),
        Decimal("101.625"),
    ]


def test_neutral_structure_when_swing_evidence_is_insufficient():
    analysis = analyze(zigzag_candles(("100", "104", "102"), leg=3))
    trend = analysis.trend

    assert trend.direction is TrendDirection.NEUTRAL
    assert trend.reason is TrendReason.INSUFFICIENT_SWINGS
    assert trend.higher_highs is None
    assert trend.lower_lows is None
    assert not trend.sufficient


def test_neutral_structure_when_swing_evidence_conflicts():
    analysis = analyze(zigzag_candles(CONFLICTING_PIVOTS, leg=5))
    trend = analysis.trend

    assert trend.direction is TrendDirection.NEUTRAL
    assert trend.reason is TrendReason.CONFLICTING_STRUCTURE
    assert trend.higher_highs is True
    assert trend.lower_lows is True
    assert trend.higher_lows is False
    assert trend.lower_highs is False


def test_flat_equal_extremes_are_reported_as_equal_not_as_a_trend():
    swings = (
        sample_swing(SwingKind.HIGH, 3, "100"),
        sample_swing(SwingKind.LOW, 8, "90"),
        sample_swing(SwingKind.HIGH, 13, "100"),
        sample_swing(SwingKind.LOW, 18, "90"),
    )
    trend = classify_trend(swings, as_of=EPOCH + INTERVAL * 30)

    assert trend.direction is TrendDirection.NEUTRAL
    assert trend.reason is TrendReason.EQUAL_EXTREMES
    assert trend.equal_highs is True
    assert trend.equal_lows is True


def test_trend_ignores_swings_that_were_not_confirmed_at_the_requested_instant():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    late_swings = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings
    early_as_of = EPOCH + INTERVAL * 12

    trend = classify_trend(late_swings, as_of=early_as_of)
    assert trend.direction is TrendDirection.NEUTRAL
    assert trend.reason is TrendReason.INSUFFICIENT_SWINGS
    assert trend.excluded_unconfirmed_swing_count > 0


def test_trend_swing_count_is_configurable_and_monotonicity_is_required():
    # Last two highs rise, but the full three-high sequence is not monotonic.
    candles = zigzag_candles(("100", "108", "102", "106", "104", "110", "108", "112"), leg=5)
    swings = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings

    two_swing = classify_trend(
        swings,
        as_of=as_of_after(candles),
        parameters=TrendParameters(swing_count=2),
    )
    three_swing = classify_trend(
        swings,
        as_of=as_of_after(candles),
        parameters=TrendParameters(swing_count=3),
    )
    assert two_swing.direction is TrendDirection.BULLISH
    assert [swing.price for swing in two_swing.swing_highs] == [
        Decimal("106.425"),
        Decimal("110.375"),
    ]
    assert three_swing.direction is TrendDirection.NEUTRAL
    assert three_swing.reason is TrendReason.CONFLICTING_STRUCTURE
    assert three_swing.higher_highs is False


# --------------------------------------------------------------------------- #
# 3. Range detection
# --------------------------------------------------------------------------- #


def test_consolidation_range_is_detected_with_boundary_evidence():
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    analysis = analyze(candles)
    result = analysis.range

    assert result.reason is None
    detected = result.range
    assert detected is not None
    assert detected.range_high == Decimal("104.475")
    assert detected.range_low == Decimal("99.55")
    assert detected.width == Decimal("4.92500000")
    assert detected.upper_touch_count == 4
    assert detected.lower_touch_count == 4
    assert detected.touch_count == 8
    assert detected.start_timestamp == EPOCH + INTERVAL * 5
    assert detected.end_timestamp == EPOCH + INTERVAL * 40
    assert detected.current_timestamp == candles[-1].timestamp
    assert result.span_candles == 35
    assert detected.close_within_bounds is True
    assert detected.closes_outside_band_count == 0
    assert detected.band_broken is False
    assert detected.active is True
    assert analysis.active_range is detected
    assert result.sufficient


def test_detected_range_is_reported_but_not_active_once_price_leaves_the_window():
    candles = zigzag_candles(RANGE_PIVOTS, leg=5) + flat_candles(
        45, start_index=len(zigzag_candles(RANGE_PIVOTS, leg=5)), price="104"
    )
    analysis = analyze(candles)

    detected = analysis.detected_range
    assert detected is not None
    assert detected.active is False
    assert detected.candles_since_last_touch > detected.parameters.active_max_candles_since_last_touch
    assert detected.close_within_bounds is True
    assert analysis.active_range is None


def test_range_is_rejected_when_touches_are_insufficient():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    result = detect_range(
        candles,
        detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings,
        interval=INTERVAL,
        as_of=as_of_after(candles),
    )

    assert result.range is None
    assert result.reason is RangeRejectionReason.INSUFFICIENT_TOUCHES
    assert result.upper_touch_count == 1
    assert result.lower_touch_count == 1


def test_range_is_rejected_when_width_exceeds_the_configured_maximum():
    candles = zigzag_candles(("100", "130", "100", "130", "100", "130", "100", "130"), leg=5)
    result = detect_range(
        candles,
        detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings,
        interval=INTERVAL,
        as_of=as_of_after(candles),
    )

    assert result.range is None
    assert result.reason is RangeRejectionReason.WIDTH_EXCEEDS_MAX
    assert result.width_pct is not None and result.width_pct > Decimal(10)


def test_range_is_rejected_when_the_span_is_too_short_or_lookback_has_too_few_swings():
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    swings = detect_swings(candles, interval=INTERVAL, as_of=as_of_after(candles)).swings

    short_span = detect_range(
        candles,
        swings,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=RangeParameters(min_span_candles=100),
    )
    assert short_span.range is None
    assert short_span.reason is RangeRejectionReason.SPAN_TOO_SHORT

    tiny_lookback = detect_range(
        candles,
        swings,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=RangeParameters(lookback_candles=3),
    )
    assert tiny_lookback.range is None
    assert tiny_lookback.reason is RangeRejectionReason.INSUFFICIENT_SWINGS


def test_range_reports_no_candles_rather_than_a_range():
    result = detect_range((), (), interval=INTERVAL, as_of=EPOCH)
    assert result.range is None
    assert result.reason is RangeRejectionReason.NO_CANDLES
    assert result.candle_count == 0
    assert not result.sufficient


# --------------------------------------------------------------------------- #
# 4. Support / resistance evidence
# --------------------------------------------------------------------------- #


def test_near_identical_swings_cluster_into_one_zone():
    swings = (
        sample_swing(SwingKind.LOW, 2, "100.0"),
        sample_swing(SwingKind.LOW, 7, "100.4"),
        sample_swing(SwingKind.LOW, 12, "100.8"),
        sample_swing(SwingKind.HIGH, 17, "110.0"),
        sample_swing(SwingKind.HIGH, 22, "110.4"),
    )
    result = detect_zones(
        swings,
        as_of=EPOCH + INTERVAL * 30,
        latest_close=Decimal(105),
        parameters=LevelParameters(tolerance_pct=Decimal(1)),
    )

    assert len(result.zones) == 2
    support, resistance = result.zones
    assert support.band_low == Decimal("100.0")
    assert support.band_high == Decimal("100.8")
    assert support.center == Decimal("100.40000000")
    assert support.touch_count == 3
    assert support.low_source_count == 3
    assert support.high_source_count == 0
    assert support.first_observed_timestamp == EPOCH + INTERVAL * 2
    assert support.last_tested_timestamp == EPOCH + INTERVAL * 12
    assert support.source_swing_timestamps == (
        EPOCH + INTERVAL * 2,
        EPOCH + INTERVAL * 7,
        EPOCH + INTERVAL * 12,
    )
    assert support.role.value == "support"
    assert support.tested_as_support is True
    assert support.tested_as_resistance is False
    assert support.distance_pct_from_latest_close == Decimal("-4.38095238")

    assert resistance.role.value == "resistance"
    assert resistance.touch_count == 2
    assert resistance.high_source_count == 2
    assert resistance.band_low == Decimal("110.0")
    assert resistance.band_high == Decimal("110.4")


def test_tolerance_controls_clustering_and_gap_creates_separate_zones():
    swings = (
        sample_swing(SwingKind.HIGH, 2, "100"),
        sample_swing(SwingKind.HIGH, 7, "103"),
    )
    loose = detect_zones(
        swings,
        as_of=EPOCH + INTERVAL * 20,
        latest_close=Decimal(95),
        parameters=LevelParameters(tolerance_pct=Decimal(5)),
    )
    tight = detect_zones(
        swings,
        as_of=EPOCH + INTERVAL * 20,
        latest_close=Decimal(95),
        parameters=LevelParameters(tolerance_pct=Decimal(1)),
    )

    assert len(loose.zones) == 1
    assert loose.zones[0].touch_count == 2
    assert len(tight.zones) == 2


def test_zones_respect_min_touches_max_zones_and_deterministic_order():
    swings = (
        sample_swing(SwingKind.HIGH, 2, "100"),
        sample_swing(SwingKind.HIGH, 4, "100.5"),
        sample_swing(SwingKind.HIGH, 12, "120"),
        sample_swing(SwingKind.HIGH, 14, "120.5"),
        sample_swing(SwingKind.HIGH, 16, "121"),
        sample_swing(SwingKind.LOW, 20, "80"),
    )
    result = detect_zones(
        swings,
        as_of=EPOCH + INTERVAL * 30,
        latest_close=Decimal(100),
        parameters=LevelParameters(min_touches=2, max_zones=1),
    )

    assert result.cluster_count == 3
    assert result.discarded_below_min_touches == 1
    assert result.discarded_beyond_max_zones == 1
    assert len(result.zones) == 1
    # Most-touched zone first, then most recently tested, then lowest center.
    assert result.zones[0].touch_count == 3
    assert result.zones[0].band_low == Decimal(120)
    assert result.zones[0].last_tested_timestamp == EPOCH + INTERVAL * 16


def test_zone_role_is_measurable_and_relative_to_the_latest_close():
    swings = (
        sample_swing(SwingKind.HIGH, 2, "100"),
        sample_swing(SwingKind.LOW, 7, "100"),
    )
    result = detect_zones(swings, as_of=EPOCH + INTERVAL * 20, latest_close=Decimal(100))
    assert [zone.role.value for zone in result.zones] == ["at_price"]

    zones_without_close = detect_zones(swings, as_of=EPOCH + INTERVAL * 20, latest_close=None)
    assert zones_without_close.zones[0].role.value == "at_price"
    assert zones_without_close.zones[0].distance_pct_from_latest_close is None


def test_unconfirmed_swings_are_excluded_from_zone_derivation():
    swings = (
        sample_swing(SwingKind.HIGH, 2, "100"),
        sample_swing(SwingKind.HIGH, 6, "100.5"),
    )
    result = detect_zones(swings, as_of=EPOCH + INTERVAL * 8, latest_close=Decimal(95))

    assert result.swing_count == 1
    assert len(result.zones) == 1
    assert result.zones[0].touch_count == 1


# --------------------------------------------------------------------------- #
# 5. Volatility
# --------------------------------------------------------------------------- #


def test_true_range_uses_previous_close_and_the_largest_span():
    candles = analytic_candles(
        [
            ("100", "101", "99", "100", "1"),
            ("100", "110", "100", "105", "1"),
        ]
    )
    assert true_range(candles[1], candles[0].close) == Decimal(10)


def test_atr_uses_wilder_smoothing_on_exact_true_ranges():
    candles = analytic_candles(
        [
            ("100", "110", "100", "100", "1"),
            ("100", "110", "100", "100", "1"),
            ("100", "110", "100", "100", "1"),
            ("100", "110", "100", "100", "1"),
            ("100", "110", "100", "100", "1"),
        ]
    )
    context = calculate_volatility(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=VolatilityParameters(period=3),
    )

    assert context.available is True
    assert context.smoothing == ATR_SMOOTHING == "wilder_rma"
    assert context.true_range_count == 4
    assert context.latest_true_range == Decimal(10)
    assert context.atr == Decimal(10)
    assert context.atr_percent_of_price == Decimal(10)
    assert context.required_candle_count == 4
    assert context.sufficient is True


def test_wilder_atr_recursion_matches_the_documented_formula():
    candles = analytic_candles(
        [
            ("100", "101", "99", "100", "1"),
            ("100", "110", "100", "105", "1"),
            ("105", "112", "104", "106", "1"),
            ("106", "115", "105", "110", "1"),
            ("110", "112", "100", "101", "1"),
        ]
    )
    context = calculate_volatility(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=VolatilityParameters(period=3),
    )

    # True ranges: 10, 8, 10, 12.
    # first ATR = mean(10, 8, 10) = 9.33333333
    # next      = 9.33333333 + (12 - 9.33333333) / 3 = 10.22222222
    assert context.atr == Decimal("10.22222222")
    assert context.latest_true_range == Decimal(12)
    assert context.atr_percent_of_price.quantize(Decimal("0.0001")) == Decimal("10.1210")
    assert wilder_average(
        (Decimal(10), Decimal(8), Decimal(10), Decimal(12)), period=3
    ) == Decimal("10.22222222")


def test_atr_reports_insufficient_data_without_shortening_the_lookback():
    candles = analytic_candles(
        [
            ("100", "101", "99", "100", "1"),
            ("100", "110", "100", "105", "1"),
            ("105", "112", "104", "106", "1"),
        ]
    )
    context = calculate_volatility(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=VolatilityParameters(period=14),
    )

    assert context.available is False
    assert context.reason == "insufficient_candles"
    assert context.period == 14
    assert context.candle_count == 3
    assert context.required_candle_count == 15
    assert context.atr is None
    assert context.atr_percent_of_price is None
    # The single True Range that is fully determined is still reported.
    assert context.latest_true_range == Decimal(8)

    empty = calculate_volatility((), interval=INTERVAL, as_of=EPOCH)
    assert empty.available is False
    assert empty.candle_count == 0
    assert empty.latest_true_range is None


def test_atr_ignores_candles_that_had_not_closed_at_as_of():
    candles = analytic_candles([("100", "110", "100", "100", "1")] * 5)
    context = calculate_volatility(
        candles,
        interval=INTERVAL,
        as_of=candles[2].timestamp + INTERVAL,
        parameters=VolatilityParameters(period=2),
    )
    assert context.candle_count == 3
    assert context.required_candle_count == 3
    assert context.atr == Decimal(10)


# --------------------------------------------------------------------------- #
# 6. Volume context
# --------------------------------------------------------------------------- #


def test_rolling_average_and_relative_volume_use_documented_windows():
    volumes = ["10"] * 20 + ["30"]
    candles = constant_volume_candles(
        [str(value) for value in range(100, 100 + len(volumes))],
        volumes=volumes,
    )
    context = calculate_volume(
        candles,
        interval=INTERVAL,
        as_of=as_of_after(candles),
        parameters=VolumeParameters(period=20),
    )

    assert context.sufficient is True
    assert context.reason is None
    assert context.relative_volume_basis == "latest_volume_over_prior_period_average"
    assert context.current_volume == Decimal(30)
    assert context.rolling_average_volume == Decimal(11)
    assert context.reference_average_volume == Decimal(10)
    assert context.relative_volume == Decimal(3)
    assert context.required_candle_count == 21
    assert context.candle_count == 21


def test_volume_reports_insufficient_data_for_each_window():
    twenty = constant_volume_candles([str(value) for value in range(100, 120)])
    partial = calculate_volume(
        twenty,
        interval=INTERVAL,
        as_of=as_of_after(twenty),
        parameters=VolumeParameters(period=20),
    )
    assert partial.sufficient is False
    assert partial.reason == "insufficient_candles"
    assert partial.current_volume == Decimal(10)
    assert partial.rolling_average_volume == Decimal(10)
    assert partial.reference_average_volume is None
    assert partial.relative_volume is None

    ten = constant_volume_candles([str(value) for value in range(100, 110)])
    short = calculate_volume(
        ten,
        interval=INTERVAL,
        as_of=as_of_after(ten),
        parameters=VolumeParameters(period=20),
    )
    assert short.rolling_average_volume is None
    assert short.current_volume == Decimal(10)

    empty = calculate_volume((), interval=INTERVAL, as_of=EPOCH)
    assert empty.sufficient is False
    assert empty.current_volume is None


def test_volume_context_does_not_invent_values_from_beyond_as_of():
    candles = constant_volume_candles(
        [str(value) for value in range(100, 106)],
        volumes=["10", "10", "10", "10", "10", "999"],
    )
    at_five_closed = calculate_volume(
        candles,
        interval=INTERVAL,
        as_of=candles[4].timestamp + INTERVAL,
        parameters=VolumeParameters(period=4),
    )
    assert at_five_closed.candle_count == 5
    assert at_five_closed.current_volume == Decimal(10)
    assert at_five_closed.relative_volume == Decimal(1)


# --------------------------------------------------------------------------- #
# 7. Composition, determinism, and anti-lookahead
# --------------------------------------------------------------------------- #


def test_analysis_of_full_history_equals_analysis_of_prefix_available_at_as_of():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    as_of = candles[24].timestamp + INTERVAL
    future_count = len(candles) - 25

    full = analyze(candles, as_of=as_of)
    prefix = analyze(candles[:25], as_of=as_of)

    assert full.excluded_future_candle_count == future_count
    assert prefix.excluded_future_candle_count == 0
    assert full.swings == prefix.swings
    assert full.trend == prefix.trend
    assert full.range == prefix.range
    assert full.levels == prefix.levels
    assert full.volatility == prefix.volatility
    assert full.volume == prefix.volume
    assert full.as_of == prefix.as_of


def test_analysis_is_deterministic_and_chronological():
    candles = zigzag_candles(RANGE_PIVOTS, leg=5)
    first = analyze(candles)
    second = analyze(candles)

    assert first == second
    timestamps = [swing.timestamp for swing in first.confirmed_swings]
    assert timestamps == sorted(timestamps)
    loaded = json.loads(json.dumps(to_jsonable(first)))
    assert loaded["swings"]["swings"][0]["kind"] == "high"
    assert loaded["as_of"].endswith("Z")


def test_analysis_rejects_out_of_order_candles_instead_of_repairing_them():
    candles = zigzag_candles(RISING_PIVOTS, leg=5)
    shuffled = (candles[1], candles[0]) + candles[2:]
    with pytest.raises(ValueError, match="strictly ordered"):
        analyze(shuffled)


def test_analysis_handles_empty_input_without_inventing_structure():
    analysis = analyze_candles((), interval=INTERVAL, as_of=EPOCH)

    assert analysis.candle_count == 0
    assert analysis.confirmed_swings == ()
    assert analysis.trend.direction is TrendDirection.NEUTRAL
    assert analysis.trend.reason is TrendReason.INSUFFICIENT_SWINGS
    assert analysis.detected_range is None
    assert analysis.levels.zones == ()
    assert analysis.volatility.available is False
    assert analysis.volume.sufficient is False
    assert analysis.window_start_timestamp is None
    assert analysis.window_end_timestamp is None


def test_parameters_are_recorded_and_validated():
    parameters = MarketStructureParameters(
        swings=SwingParameters(left_window=3, right_window=1, tie_policy="earliest_equal"),
        trend=TrendParameters(swing_count=3),
        ranges=RangeParameters(tolerance_pct="0.5", max_width_pct="5"),
        levels=LevelParameters(tolerance_pct="0.25", min_touches=2),
        volatility=VolatilityParameters(period=7),
        volume=VolumeParameters(period=9),
        higher_timeframes=("4h", "1d"),
    )
    assert parameters.swings.left_window == 3
    assert parameters.swings.tie_policy is SwingTiePolicy.EARLIEST_EQUAL
    assert parameters.ranges.tolerance_pct == Decimal("0.5")
    assert parameters.higher_timeframes == ("4h", "1d")
    assert parameters.minimum_candle_count == 5

    analysis = analyze(zigzag_candles(RISING_PIVOTS, leg=5), parameters=parameters)
    assert analysis.parameters == parameters

    with pytest.raises(ValueError):
        SwingParameters(left_window=0)
    with pytest.raises(ValueError):
        SwingParameters(tie_policy="unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        TrendParameters(swing_count=1)
    with pytest.raises(ValueError):
        RangeParameters(min_touches_per_side=0)
    with pytest.raises(ValueError):
        RangeParameters(tolerance_pct="0")
    with pytest.raises(ValueError):
        LevelParameters(tolerance_pct="100")
    with pytest.raises(ValueError):
        VolatilityParameters(period=0)
    with pytest.raises(ValueError):
        VolumeParameters(period=-1)
    with pytest.raises(ValueError):
        MarketStructureParameters(higher_timeframes=())
    with pytest.raises(ValueError):
        MarketStructureParameters(higher_timeframes=("4h", "4h"))


def test_higher_timeframes_for_uses_configured_supported_timeframes():
    supported = ("5m", "15m", "1h", "4h", "1d", "1w")

    assert higher_timeframes_for("1h", supported) == ("4h", "1d", "1w")
    assert higher_timeframes_for("1d", supported) == ("1w",)
    assert higher_timeframes_for("1w", supported) == ()
    assert higher_timeframes_for("15m", ("5m", "15m")) == ()
    # Irregular entries are ignored rather than guessed at.
    assert higher_timeframes_for("1h", ("5m", "1h", "4h", "1M")) == ("4h",)
