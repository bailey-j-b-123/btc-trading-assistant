"""The validator's subtree-summary fast path must never change an outcome.

``_validate_available`` may skip a subtree only when the traversal would provably
pass. These tests run each case through the original traversal and through the
cached path, sharing one cache across calls with different ``now`` values, and
require identical pass/fail results and identical error messages.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from market_structure_fixtures import EPOCH, EXCHANGE, INTERVAL, SYMBOL, candle_at

from trading_assistant.setup_qualification.engine import _validate_available

INSTRUMENT = (EXCHANGE, SYMBOL, "1h")


@dataclass(frozen=True)
class _Node:
    when: datetime
    children: tuple = ()


def _outcome(value, now, instrument, summaries):
    try:
        _validate_available(value, now, instrument, None, summaries)
    except Exception as error:  # noqa: BLE001 - outcome comparison is the point
        return (type(error).__name__, str(error))
    return ("ok", "")


def _cases():
    candles = tuple(candle_at(i, open_="10", high="11", low="9", close="10") for i in range(4))
    shared = candles[0]
    other_symbol = replace(candles[1], symbol="ETH/USDT")
    bad_timeframe = replace(shared, timeframe="7m")
    naive = _Node(when=datetime(2024, 1, 1))
    return {
        "valid_tree": (_Node(EPOCH, candles), EPOCH + timedelta(hours=10), INSTRUMENT),
        "candle_closes_after_now": (candles, EPOCH + timedelta(hours=1), INSTRUMENT),
        "candle_close_equals_now": (candles[:1], EPOCH + INTERVAL, INSTRUMENT),
        # Open time is not after now, but the candle is still forming: only the close rejects it.
        "candle_open_before_now_close_after": (candles, EPOCH + timedelta(hours=3, minutes=30), INSTRUMENT),
        "future_field_datetime": (_Node(EPOCH + timedelta(hours=5)), EPOCH + timedelta(hours=4), INSTRUMENT),
        "future_datetime_in_tuple": (
            (EPOCH + timedelta(hours=2), EPOCH + timedelta(hours=9)),
            EPOCH + timedelta(hours=5),
            INSTRUMENT,
        ),
        "instrument_mismatch_deep": (
            _Node(EPOCH, (candles[0], other_symbol)),
            EPOCH + timedelta(hours=10),
            INSTRUMENT,
        ),
        "naive_datetime": (naive, EPOCH + timedelta(hours=10), INSTRUMENT),
        "shared_node_twice": ((shared, shared), EPOCH + timedelta(hours=10), INSTRUMENT),
        "invalid_timeframe": (bad_timeframe, EPOCH + timedelta(hours=10), INSTRUMENT),
    }


EXPECTED_PASS = {"valid_tree", "candle_close_equals_now", "shared_node_twice"}


@pytest.mark.parametrize("name", sorted(_cases()))
def test_cached_validation_matches_traversal_for_each_case(name):
    value, now, instrument = _cases()[name]
    summaries: dict = {}
    cached = _outcome(value, now, instrument, summaries)
    plain = _outcome(value, now, instrument, None)
    assert cached == plain
    assert (cached[0] == "ok") == (name in EXPECTED_PASS), cached


def test_one_cache_across_later_and_earlier_times_never_carries_a_pass_backwards():
    """A subtree accepted at a late ``now`` must still be rejected at an earlier one."""
    candles = tuple(candle_at(i, open_="10", high="11", low="9", close="10") for i in range(6))
    tree = _Node(EPOCH, candles)
    summaries: dict = {}
    times = [EPOCH + timedelta(hours=h) for h in (10, 1, 10, 3, 6, 2, 7)]
    for now in times:
        assert _outcome(tree, now, INSTRUMENT, summaries) == _outcome(tree, now, INSTRUMENT, None)
    assert _outcome(tree, EPOCH + timedelta(hours=1), INSTRUMENT, summaries)[0] != "ok"


def test_cache_is_scoped_by_instrument():
    candles = (candle_at(0, open_="10", high="11", low="9", close="10"),)
    summaries: dict = {}
    assert _outcome(candles, EPOCH + timedelta(hours=5), INSTRUMENT, summaries)[0] == "ok"
    wrong = (EXCHANGE, "ETH/USDT", "1h")
    assert _outcome(candles, EPOCH + timedelta(hours=5), wrong, summaries) == _outcome(
        candles, EPOCH + timedelta(hours=5), wrong, None
    )
    assert _outcome(candles, EPOCH + timedelta(hours=5), wrong, summaries)[0] != "ok"
