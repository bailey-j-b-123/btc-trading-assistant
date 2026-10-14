"""BRAIN AUDIT probe 3: positive control - does the linkage fire when as_of coincides
with an event's own detection frame?  Read-only."""
from __future__ import annotations
import sys
from datetime import UTC, datetime
from trading_assistant.web.dashboard_service import DashboardService, _zone_bands, zone_link_events
from trading_assistant.web.state import create_default_state

service = DashboardService(create_default_state())
timeframe = sys.argv[1] if len(sys.argv) > 1 else "4h"
as_of = datetime.fromisoformat(sys.argv[2]).replace(tzinfo=UTC) if len(sys.argv) > 2 else datetime(2026, 10, 14, tzinfo=UTC)

_snapshot, frame, _frames = service._evaluate(exchange="binance", symbol="BTC/USDT", timeframe=timeframe, as_of=as_of)
patterns, structure = frame.patterns, frame.patterns.structure
print(f"=== {timeframe} as_of={as_of.isoformat()} ===")
print("    current zone bounds:", [(str(z.band_low), str(z.band_high)) for z in structure.levels.zones])
payload = zone_link_events(patterns, timeframe=timeframe)
zone_events = [e for e in payload if e.get("reference_type") == "zone"]
print(f"    projected zone-referenced events: {len(zone_events)}")
for e in zone_events[:12]:
    print(f"      {e['kind']:15s} {e.get('band_low')!r}-{e.get('band_high')!r} known_at={e.get('known_at')}")
bands = _zone_bands(structure.levels, timeframe, events=payload)
for band in bands["bands"]:
    lk = band["linked_events"]
    print(f"    band {band['band_low']}-{band['band_high']} linked={lk['linked']} count={lk['event_count']}")
