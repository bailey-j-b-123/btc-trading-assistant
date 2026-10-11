"""Regression tests for the descriptive candle-shape definitions (v1).

Each rule is pinned with an explicit OHLC example so a threshold or inequality
change fails loudly. Shapes are display evidence only and must never feed
qualification, planning, or journaling.
"""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL

from trading_assistant.candle_shapes import (
    CandleShapeParameters,
    candle_direction,
    detect_candle_shapes,
)
from trading_assistant.market_data.types import Candle


def c(index, o, h, l, close, *, tf_interval=INTERVAL):
    return Candle(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        timestamp=EPOCH + tf_interval * index,
        open=D(str(o)),
        high=D(str(h)),
        low=D(str(l)),
        close=D(str(close)),
        volume=D("5"),
    )


def kinds(events):
    return [e.kind for e in events]


def detect(candles, as_of=None, params=None):
    last = max(candle.timestamp for candle in candles) + INTERVAL
    return detect_candle_shapes(
        candles,
        interval=INTERVAL,
        as_of=as_of or last,
        parameters=params,
    )


def test_direction_is_exact_open_close_comparison():
    assert candle_direction(c(0, 100, 101, 99, 100.5)) == "bullish"
    assert candle_direction(c(0, 100.5, 101, 99, 100)) == "bearish"
    assert candle_direction(c(0, 100, 101, 99, 100)) == "flat"


def test_strong_bullish_body_requires_bullish_direction_and_70_percent_body():
    # body 9 of range 10 = 0.90 >= 0.70
    assert kinds(detect([c(0, 100, 105.5, 95.5, 109.5)])) == ["strong_bullish_body"]
    # body 6 of range 10 = 0.60 < 0.70 -> no strong body
    assert "strong_bullish_body" not in kinds(detect([c(0, 100, 105, 95, 106)]))


def test_strong_bearish_body_mirrors_bullish_rule():
    assert kinds(detect([c(0, 109.5, 110, 100, 100.5)])) == ["strong_bearish_body"]


def test_lower_wick_rejection_needs_wick_share_and_wick_to_body_multiple():
    # range 10, body 1 (open 100 close 101), lower wick 8 (low 92 -> body bottom 100)
    events = detect([c(0, 100, 101, 91, 101)])
    assert "lower_wick_rejection" in kinds(events)
    rejection = next(e for e in events if e.kind == "lower_wick_rejection")
    assert rejection.direction == "bullish"
    assert rejection.lower_wick_ratio == D("0.9")
    # range 10 (100..110 high/low), body 1 at 104..105, lower wick 4 = 0.40 < 0.60
    assert "lower_wick_rejection" not in kinds(detect([c(0, 104, 110, 100, 105)]))


def test_upper_wick_rejection_is_the_mirror_image():
    events = detect([c(0, 100, 110, 99, 99.5)])
    assert "upper_wick_rejection" in kinds(events)
    upper = next(e for e in events if e.kind == "upper_wick_rejection")
    assert upper.direction == "bearish"


def test_wick_must_also_dominate_the_body_by_the_configured_multiple():
    # Range 10, lower wick 6 (60%), body 5 -> 6 < 2 * 5, so not a rejection even
    # though the wick share meets the 60% threshold.
    assert "lower_wick_rejection" not in kinds(detect([c(0, 100, 105, 94, 105)]))


def test_indecision_is_small_body_relative_to_range_and_is_neutral():
    events = detect([c(0, 100, 105, 95, 100.5)])  # body 0.5 / range 10 = 0.05
    indecision = [e for e in events if e.kind == "indecision"]
    assert len(indecision) == 1
    assert indecision[0].direction == "neutral"


def test_zero_range_candle_is_never_classified():
    assert detect([c(0, 100, 100, 100, 100)]) == ()


def test_bullish_engulfing_strict_containment_and_larger_body():
    prev = c(0, 105, 106, 99, 100)  # bearish, body 100..105
    cur = c(1, 99.5, 107, 99, 106)  # bullish, body 99.5..106 contains 100..105
    events = detect([prev, cur])
    engulf = [e for e in events if e.kind == "bullish_engulfing"]
    assert len(engulf) == 1
    assert engulf[0].candle.timestamp == cur.timestamp
    assert engulf[0].previous_candle == prev
    assert engulf[0].known_at == cur.timestamp + INTERVAL


def test_engulfing_requires_strictly_larger_body_so_equal_bodies_do_not_count():
    prev = c(0, 105, 106, 99, 100)  # body 5
    cur = c(1, 100, 106, 99, 105)  # body 5 exactly, same bounds
    assert "bullish_engulfing" not in kinds(detect([prev, cur]))


def test_engulfing_requires_the_current_body_to_contain_the_previous_body():
    prev = c(0, 105, 106, 99, 100)  # body 100..105
    cur = c(1, 101, 110, 100, 106)  # body 101..106 does not reach 100
    assert "bullish_engulfing" not in kinds(detect([prev, cur]))


def test_bearish_engulfing_is_the_mirror_case():
    prev = c(0, 100, 106, 99, 105)  # bullish, body 100..105
    cur = c(1, 106, 107, 98, 99)  # bearish, body 99..106 contains 100..105
    events = detect([prev, cur])
    assert "bearish_engulfing" in kinds(events)


def test_engulfing_never_bridges_a_time_gap():
    prev = c(0, 105, 106, 99, 100)
    cur = c(2, 99.5, 107, 99, 106)  # one candle missing between them
    assert "bullish_engulfing" not in kinds(detect([prev, cur]))


def test_detection_ignores_candles_not_yet_closed_at_as_of():
    candles = [c(0, 100, 101, 99, 100.5), c(1, 100, 110, 100, 109.5)]
    # The second candle closes at index 2 boundary; as_of before it must exclude it.
    before_second_close = EPOCH + INTERVAL * 2 - timedelta(seconds=1)
    events = detect_candle_shapes(
        candles, interval=INTERVAL, as_of=before_second_close
    )
    assert all(e.candle.timestamp == EPOCH for e in events)


def test_event_ids_are_deterministic_and_distinct_by_kind():
    candles = [c(0, 100, 105.5, 95.5, 109.5)]
    first = detect(candles)
    second = detect(candles)
    assert [e.id for e in first] == [e.id for e in second]
    assert len({e.id for e in first}) == len(first)


def test_duplicate_timestamps_are_rejected_not_collapsed():
    with pytest.raises(ValueError):
        detect([c(0, 100, 101, 99, 100), c(0, 100, 102, 98, 101)])


def test_parameter_validation_rejects_inconsistent_thresholds():
    with pytest.raises(ValueError):
        CandleShapeParameters(strong_body_min_ratio=D("0"))
    with pytest.raises(ValueError):
        CandleShapeParameters(
            strong_body_min_ratio=D("0.5"), indecision_max_body_ratio=D("0.6")
        )


def test_shapes_are_marked_descriptive_and_not_imported_by_decision_layers():
    """Guard: candle shapes must never feed qualification, planning or journaling."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "trading_assistant"
    decision_packages = ["setup_qualification", "trade_planning", "journaling", "forward_testing", "statistics"]
    for package in decision_packages:
        for path in (root / package).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "candle_shapes" not in text, f"{path} must not depend on candle shapes"
