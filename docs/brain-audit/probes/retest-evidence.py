"""Generate detector-backed synthetic evidence; no exchange or DB calls."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
sys.path[:0] = [str(root / 'src'), str(root / 'tests')]
from test_pattern_liquidity import prefix, bar, snap, EPOCH, INTERVAL, EXCHANGE, SYMBOL, TIMEFRAME
from trading_assistant.web.chart_evidence import build_chart_evidence
candles = prefix() + (bar(5, 112), bar(6, 110), bar(7, 112))
snapshot = snap(candles)
evidence = build_chart_evidence(exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME,
    as_of=EPOCH + 8 * INTERVAL, snapshot=snapshot, candles=candles, interval=INTERVAL)
rows = [[int(c.timestamp.timestamp() * 1000), *map(str, [c.open, c.high, c.low, c.close, c.volume])] for c in candles]
print(json.dumps({'provenance': 'SYNTHETIC detector-generated fixture, not market history', 'evidence': evidence, 'candles': rows}, indent=2))
