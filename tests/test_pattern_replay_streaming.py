"""Single-pass pattern replay must equal independent replays at every boundary.

``analyze_patterns_at`` replaces one full replay per frame with one replay that
emits every boundary. Each emitted snapshot must be equal to what
``analyze_patterns`` returns for that boundary alone, including across gaps,
before the first candle, and when a later candle is invalid.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, candle_at

from reference_pattern_replay import reference_analyze_patterns

from trading_assistant.market_structure import analysis as structure_analysis
from trading_assistant.pattern_liquidity.analysis import analyze_patterns, analyze_patterns_at


def _closes(count: int) -> list[float]:
    # Deterministic oscillation with a slow drift: rich in swings and breaks.
    return [
        round(100 + 9 * math.sin(i / 4.0) + 0.05 * i + (2.5 if i % 17 == 0 else 0.0), 1)
        for i in range(count)
    ]


def _series(count: int, skip: set[int] = frozenset(), bad_close_at: int | None = None):
    candles = []
    for i, value in enumerate(_closes(count)):
        if i in skip:
            continue
        close = "0" if i == bad_close_at else str(value)
        candles.append(
            candle_at(i, open_=str(value), high=str(value + 1.5), low=str(value - 1.5), close=close)
        )
    return tuple(candles)


def _boundary(index: int):
    """Boundary = close of candle ``index`` (candle ``index`` is then the last closed one)."""
    return EPOCH + INTERVAL * (index + 1)


def _fresh(candles, boundary):
    """Independent oracle: the pre-streaming replay frozen in reference_pattern_replay."""
    return reference_analyze_patterns(
        candles,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        as_of=boundary,
    )


def _streamed(candles, boundaries):
    return analyze_patterns_at(
        candles,
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe="1h",
        as_of_boundaries=boundaries,
    )


def _assert_streamed_equals_fresh(candles, boundaries):
    structure_analysis._ANALYSIS_MEMO.clear()
    streamed = _streamed(candles, boundaries)
    assert len(streamed) == len(boundaries)
    for boundary, snapshot in zip(boundaries, streamed):
        structure_analysis._ANALYSIS_MEMO.clear()
        assert snapshot == _fresh(candles, boundary), f"differs at {boundary.isoformat()}"
    return streamed


def test_contiguous_series_streamed_snapshots_equal_fresh_replays():
    candles = _series(140)
    boundaries = [_boundary(i) for i in range(4, 139, 9)] + [_boundary(139)]
    streamed = _assert_streamed_equals_fresh(candles, boundaries)
    # Guard against a vacuous pass: the series must exercise real structure.
    assert any(
        s.breakouts or s.failed_breakouts or s.sweeps or s.retests or s.equal_levels or s.chart_patterns
        for s in streamed
    )
    assert any(s.status == "evaluated" for s in streamed)


def test_gap_streamed_snapshots_equal_fresh_replays_before_during_and_after_the_gap():
    candles = _series(150, skip={60, 61, 62, 63})
    boundaries = [
        _boundary(30),
        _boundary(59),  # last candle before the gap closes exactly here
        _boundary(60),  # gap begins: boundary sits on the first missing open
        _boundary(64),
        _boundary(66),
        _boundary(100),
        _boundary(149),
    ]
    _assert_streamed_equals_fresh(candles, boundaries)


def test_public_single_boundary_entry_point_matches_the_reference():
    candles = _series(90, skip={40})
    for boundary in (_boundary(20), _boundary(45), _boundary(89)):
        structure_analysis._ANALYSIS_MEMO.clear()
        public = analyze_patterns(
            candles, exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", as_of=boundary
        )
        assert public == _fresh(candles, boundary)


def test_boundary_before_any_candle_and_empty_input_match_fresh_replays():
    candles = _series(40)
    early = EPOCH  # no candle has closed by this instant
    _assert_streamed_equals_fresh(candles, [early, _boundary(0), _boundary(20)])
    _assert_streamed_equals_fresh((), [early, EPOCH + timedelta(days=1)])


def test_boundaries_must_be_strictly_increasing():
    candles = _series(20)
    with pytest.raises(ValueError, match="strictly increasing"):
        _streamed(candles, [_boundary(10), _boundary(10)])
    with pytest.raises(ValueError, match="strictly increasing"):
        _streamed(candles, [_boundary(12), _boundary(10)])


def test_invalid_later_candle_rejects_the_call_exactly_as_the_fresh_replay_does():
    """Validation runs for every boundary before replay, so the first bad prefix raises."""
    candles = _series(90, bad_close_at=80)
    earlier = _boundary(70)
    later = _boundary(85)
    assert _fresh(candles, earlier).status in {"evaluated", "insufficient"}
    with pytest.raises(ValueError) as fresh_error:
        _fresh(candles, later)
    with pytest.raises(ValueError) as streamed_error:
        _streamed(candles, [earlier, later])
    assert str(streamed_error.value) == str(fresh_error.value)


def test_prefix_identity_is_exact_for_content_not_object_identity():
    base = _series(30)
    rebuilt = _series(30)  # equal content, different objects
    assert base is not rebuilt
    as_of = _boundary(29)
    structure_analysis._ANALYSIS_MEMO.clear()
    first = _fresh(base, as_of)
    second = _fresh(rebuilt, as_of)
    assert first == second
    # One changed value anywhere in the prefix must not share a memo entry.
    changed = list(base)
    changed[7] = candle_at(7, open_="100.0", high="101.5", low="98.5", close="100.1")
    assert structure_analysis._prefix_node(tuple(changed)) != structure_analysis._prefix_node(base)
    # Same content in a different order is a different sequence.
    swapped = list(base)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    assert structure_analysis._prefix_node(tuple(swapped)) != structure_analysis._prefix_node(base)
    assert structure_analysis._prefix_node(()) == 0
