"""Exactness of the replay-speed memos.

The memos (analysis, detector, validator) are pure performance work. Each test
pins that the cached path returns exactly what the uncached path returns, and
that the memo keys cannot merge inputs whose outputs could differ.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal as D

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, candle_at

import trading_assistant.market_structure.analysis as structure_analysis
from trading_assistant.market_structure.analysis import analyze_candles
from trading_assistant.pattern_liquidity.analysis import analyze_patterns
from trading_assistant.setup_qualification.engine import _validate_available

CLOSES = (100, 102, 104, 106, 108, 110, 108, 105, 102, 100, 101, 103, 105, 107, 109,
          110.2, 108, 105, 102, 99, 96, 94, 95, 96, 95, 94, 92, 90, 88, 86)


def series(closes=CLOSES):
    return tuple(
        candle_at(i, open_=str(v), high=str(v + 1), low=str(v - 1), close=str(v))
        for i, v in enumerate(closes)
    )


def test_cached_analysis_equals_uncached_result():
    candles = series()
    as_of = EPOCH + INTERVAL * len(CLOSES)
    structure_analysis._ANALYSIS_MEMO.clear()
    uncached = structure_analysis._analyze_candles_uncached(candles, interval=INTERVAL, as_of=as_of)
    first = analyze_candles(candles, interval=INTERVAL, as_of=as_of)
    second = analyze_candles(candles, interval=INTERVAL, as_of=as_of)
    assert first == uncached
    assert second is first, "an identical request is served from the memo"


def test_decimal_representation_is_part_of_the_memo_key():
    """Decimal('110.0') == Decimal('110.00') but outputs may print differently: never shared."""
    base = list(series())
    as_of = EPOCH + INTERVAL * len(CLOSES)
    a = list(base)
    b = list(base)
    a[5] = replace(a[5], close=D("110.0"), high=D("111.0"))
    b[5] = replace(b[5], close=D("110.00"), high=D("111.00"))
    left = analyze_candles(tuple(a), interval=INTERVAL, as_of=as_of)
    right = analyze_candles(tuple(b), interval=INTERVAL, as_of=as_of)
    assert left is not right


def test_pattern_replay_is_identical_with_warm_memos():
    candles = series()
    as_of = EPOCH + INTERVAL * len(CLOSES)
    kwargs = dict(exchange=EXCHANGE, symbol=SYMBOL, timeframe="1h", as_of=as_of)
    structure_analysis._ANALYSIS_MEMO.clear()
    cold = analyze_patterns(candles, **kwargs)
    warm = analyze_patterns(candles, **kwargs)
    assert warm == cold


@dataclass(frozen=True)
class _Node:
    when: datetime


def test_validator_still_rejects_future_data_inside_shared_nodes():
    now = EPOCH + INTERVAL
    future = _Node(when=now + timedelta(hours=1))
    with pytest.raises(ValueError, match="after frame as_of"):
        _validate_available((future, future), now, (EXCHANGE, SYMBOL, "1h"))


def test_validator_accepts_shared_past_nodes_once_checked():
    now = EPOCH + INTERVAL * 5
    past = _Node(when=EPOCH)
    _validate_available((past, past, (past,)), now, (EXCHANGE, SYMBOL, "1h"))
