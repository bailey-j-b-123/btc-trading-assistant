"""Synthetic label tests. These do not demonstrate trading profitability."""

from collections import namedtuple
from decimal import Decimal as D

import pytest
from trading_assistant.strategy_lab.large_move import label_long_move
from trading_assistant.strategy_lab.discovery import TRAIN_START, TRAIN_END, HOUR

Candle = namedtuple("Candle", "timestamp exchange symbol timeframe open high low close volume")


def bars(size=170):
    return tuple(Candle(TRAIN_START + i * HOUR, "binance", "BTC/USDT", "1h",
                        D(100), D(101), D(99), D(100), D(1)) for i in range(size))


def test_reaches_two_percent_from_next_open_not_decision_close():
    cs = list(bars())
    cs[2] = cs[2]._replace(high=D("102"), low=D("98"))
    x = label_long_move(tuple(cs), 0, horizon_bars=24)
    assert x["touched_target"] is True and x["first_touch_bars"] == 2
    assert D(x["worst_before_hit_pct"]) == D(-2)
    assert D(x["minimum_move_pct"]) == D(2)


def test_future_high_beyond_horizon_does_not_count():
    cs = list(bars())
    cs[25] = cs[25]._replace(high=D(105))
    x = label_long_move(tuple(cs), 0, horizon_bars=24)
    assert not x["touched_target"] and x["first_touch_bars"] is None


def test_needs_full_future_horizon_and_cannot_read_eval():
    cs = bars()
    with pytest.raises(ValueError, match="complete"):
        label_long_move(cs, len(cs) - 24, horizon_bars=24)
    shifted = tuple(c._replace(timestamp=c.timestamp + (TRAIN_END - TRAIN_START)) for c in cs)
    with pytest.raises(ValueError, match="training-only"):
        label_long_move(shifted, 0, horizon_bars=24)


def test_invalid_thresholds_and_gaps_rejected():
    cs = bars()
    with pytest.raises(ValueError, match="threshold"):
        label_long_move(cs, 0, horizon_bars=24, threshold=D("0.002"))
    with pytest.raises(ValueError, match="contiguous"):
        label_long_move(cs[:4] + cs[5:], 0, horizon_bars=24)


def test_trading_decision_is_unchanged():
    # The new module is just an offline training label, with no runner adapter.
    from trading_assistant.strategy_lab.runner import run_experiment
    import inspect
    assert "large_move" not in inspect.getsource(run_experiment)
