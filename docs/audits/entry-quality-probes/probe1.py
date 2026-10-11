"""READ-ONLY probe. SYNTHETIC candles only (NOT the first trade). Runs the unchanged engine."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from trading_assistant.market_data.types import Candle
from trading_assistant.pattern_liquidity.analysis import analyze_patterns_at
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.models import QualificationFrame
from trading_assistant.trade_planning.planner import plan_trade

EPOCH = datetime(2026, 1, 1, tzinfo=UTC); H = timedelta(hours=1)

def build(path, vols=None):
    """path: list of (low, high, close); open=prev close."""
    out = []; prev = path[0][2]
    for i, (lo, hi, cl) in enumerate(path):
        op = prev
        hi = max(hi, op, cl) + (i*7 % 11)*0.5; lo = min(lo, op, cl) - (i*5 % 13)*0.5
        out.append(Candle("binance", "BTC/USDT", "1h", EPOCH + H*i, D(str(op)), D(str(hi)), D(str(lo)), D(str(cl)), D(str((vols or {}).get(i, 10)))))
        prev = cl
    return tuple(out)

def ramp(a, b, n, wick=15):
    step = (b - a) / n
    return [(min(a+step*k, a+step*(k+1)) - wick, max(a+step*k, a+step*(k+1)) + wick, a+step*(k+1)) for k in range(n)]

def run(candles, label, show_from=None):
    ivl = H
    bounds = [candles[0].timestamp + ivl*(k+1) for k in range(len(candles))]
    snaps = analyze_patterns_at(candles, exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of_boundaries=bounds)
    frames = [QualificationFrame(s, ()) for s in snaps]
    start = show_from or 0
    res = []
    for k in range(start, len(frames)):
        qs = enumerate_qualifications(frames[:k+1], as_of=bounds[k])
        snap = qs[-1]
        res.append((k, bounds[k], snap, frames[k]))
    return snaps, frames, res
