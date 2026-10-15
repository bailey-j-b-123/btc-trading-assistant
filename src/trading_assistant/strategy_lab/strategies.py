"""Causal long-only candidates, with the existing BRAIN volatility primitive."""

from decimal import Decimal as D
from datetime import timedelta
from trading_assistant.market_structure.volatility import (
    calculate_volatility,
    VolatilityParameters,
)


def rsi(values):
    # Explicit rolling arithmetic RSI, not a claim to implement Wilder smoothing.
    changes = [b - a for a, b in zip(values, values[1:])]
    up = sum((max(v, D(0)) for v in changes), D(0))
    down = sum((max(-v, D(0)) for v in changes), D(0))
    return (
        D(50)
        if up == down == 0
        else D(100)
        if down == 0
        else D(100) - D(100) / (1 + up / down)
    )


def decide(candles, strategy, *, known_at):
    """Use only fully closed, contiguous candles at the decision timestamp."""
    interval = timedelta(hours=1 if strategy.timeframe == "1h" else 4)
    closed = tuple(c for c in candles if c.timestamp + interval <= known_at)
    if len(closed) < strategy.warmup:
        return None
    window = closed[-strategy.warmup :]
    if window[-1].timestamp + interval != known_at or any(
        b.timestamp - a.timestamp != interval for a, b in zip(window, window[1:])
    ):
        return None
    closes = [c.close for c in window]

    def avg(vals):
        return sum(vals, D(0)) / len(vals)

    latest = window[-1]
    if strategy.family == "sma_cross":
        signal = avg(closes[-strategy.fast :]) > avg(closes[-strategy.slow :]) and avg(
            closes[-strategy.fast - 1 : -1]
        ) <= avg(closes[-strategy.slow - 1 : -1])
    elif strategy.family == "donchian":
        signal = latest.close > max(c.high for c in window[-strategy.slow - 1 : -1])
    elif strategy.family == "rsi_recovery":
        signal = (
            rsi(closes[-strategy.rsi_period - 2 : -1])
            < strategy.rsi_low
            <= rsi(closes[-strategy.rsi_period - 1 :])
        )
    else:
        signal = (
            latest.close > closes[-strategy.fast - 1]
            and closes[-2] <= closes[-strategy.fast - 2]
            and latest.close > avg(closes[-strategy.slow :])
        )
    if not signal:
        return None
    vol = calculate_volatility(
        window,
        interval=interval,
        as_of=known_at,
        parameters=VolatilityParameters(period=strategy.atr_period),
    )
    if vol.atr is None or vol.atr <= 0:
        return None
    distance = vol.atr * strategy.stop_atr
    if latest.close <= distance:
        return None
    return {
        "known_at": known_at.isoformat(),
        "candle_open": latest.timestamp.isoformat(),
        "reference": str(latest.close),
        "stop": str(latest.close - distance),
        "target": str(latest.close + distance * strategy.reward_r),
        "evidence": {
            "family": strategy.family,
            "atr": str(vol.atr),
            "close": str(latest.close),
            "window_start": window[0].timestamp.isoformat(),
            "closed_count": len(window),
            "regime": "fast_ma_above_slow_ma"
            if avg(closes[-strategy.fast :]) > avg(closes[-strategy.slow :])
            else "fast_ma_below_or_equal_slow_ma",
        },
    }
