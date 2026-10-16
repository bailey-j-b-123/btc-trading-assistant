"""Research-only labels for substantial BTC LONG movements.

This code inspects future candles ONLY when learning from the designated
Apr-Sep 2023 discovery period. It must never be called by live decisions.
It does NOT change the trading simulator, stops, or take-profit levels.
"""

from decimal import Decimal as D

from .discovery import HOUR, TRAIN_START, TRAIN_END, scope


def label_long_move(candles, decision_index, *, horizon_bars, threshold=D("0.02")):
    """Retrospective next-open 2% opportunity label, not a trade or forecast.

    `worst_before_hit` includes the entire first target-touch candle, so in-bar
    high/low ordering is NEVER assumed favorable. No stop is fabricated.
    If the target is not hit, it covers all available horizon candles.
    """
    if not isinstance(candles, tuple) or not scope(candles):
        raise ValueError("only contiguous BTCUSDT 1h candles")
    if candles[0].timestamp < TRAIN_START or candles[-1].timestamp + HOUR > TRAIN_END:
        raise ValueError("training-only; evaluation and protected prices forbidden")
    if (type(decision_index) is not int or type(horizon_bars) is not int
            or not 0 <= decision_index < len(candles) - 1
            or not 24 <= horizon_bars <= 168
            or decision_index + horizon_bars >= len(candles)):
        raise ValueError("decision and complete 24..168h training horizon required")
    if not isinstance(threshold, D) or not threshold.is_finite() or not D("0.02") <= threshold <= D("0.20"):
        raise ValueError("finite movement threshold of at least 2% required")
    entry = candles[decision_index + 1].open
    if entry <= 0:
        raise ValueError("positive next-open reference required")
    target = entry * (1 + threshold)
    peak_pct = D(0)
    worst_pct = D(0)
    first_hit = None
    for offset in range(1, horizon_bars + 1):
        bar = candles[decision_index + offset]
        if not 0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("invalid OHLC geometry")
        peak_pct = max(peak_pct, bar.high / entry - 1)
        worst_pct = min(worst_pct, bar.low / entry - 1)
        if bar.high >= target:
            first_hit = offset
            break
    return {
        "decision_at": (candles[decision_index].timestamp + HOUR).isoformat(),
        "next_open": str(entry),
        "minimum_move_pct": str(threshold * 100),
        "horizon_bars": horizon_bars,
        "touched_target": first_hit is not None,
        "first_touch_bars": first_hit,
        "best_move_pct": str(peak_pct * 100),
        "worst_before_hit_pct": str(worst_pct * 100),
        "warning": "retrospective training label only; no stop/fill/PnL guarantee",
    }
