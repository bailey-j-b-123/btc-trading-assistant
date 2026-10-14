"""BRAIN AUDIT probe 2: why do real zone-referenced events fail to link to their band?

Read-only. Real detector, real projection, real linkage. Nothing is written.

Run:
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-audit-sr.sqlite3 \
    PYTHONPATH=<worktree>/src .venv/bin/python zone-linkage-cause.py 4h
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from trading_assistant.web.dashboard_service import (
    DashboardService,
    _zone_bands,
    zone_link_events,
)
from trading_assistant.web.state import create_default_state

SYMBOL = "BTC/USDT"
AS_OF = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)

service = DashboardService(create_default_state())

for timeframe in sys.argv[1:] or ["4h"]:
    _snapshot, frame, _frames = service._evaluate(
        exchange="binance", symbol=SYMBOL, timeframe=timeframe, as_of=AS_OF
    )
    patterns = frame.patterns
    structure = patterns.structure

    # 1. Every zone-referenced event, with the bounds the event carries.
    print(f"=== {timeframe}: zone-referenced events (raw objects) ===")
    raw = []
    for event in patterns.breakouts:
        if event.reference.type == "zone":
            raw.append(("breakout", str(event.reference.band_low), str(event.reference.band_high)))
    for event in patterns.retests:
        try:
            reference = event.breakout.reference
        except AttributeError:
            continue
        if reference.type == "zone":
            raw.append(("retest", str(reference.band_low), str(reference.band_high)))
    for kind, low, high in raw:
        print(f"    {kind:9s} object bounds: low={low!r} high={high!r}")

    # 2. Same facts as the payload the linkage actually receives.
    payload = zone_link_events(patterns, timeframe=timeframe)
    zone_payload = [e for e in payload if e.get("reference_type") == "zone"]
    print(f"    projected payload zone-referenced events: {len(zone_payload)}")
    for item in zone_payload:
        print(
            f"      kind={item['kind']:9s} band_low={item.get('band_low')!r} "
            f"band_high={item.get('band_high')!r} known_at={item.get('known_at')!r}"
        )

    # 3. Member bounds exactly as the presented bands expose them.
    bands = _zone_bands(structure.levels, timeframe, events=payload)
    print(f"=== {timeframe}: presented bands and their member bounds ===")
    for band in bands["bands"]:
        linkage = band["linked_events"]
        print(
            f"    band {band['band_low']!r}-{band['band_high']!r} "
            f"linked={linkage['linked']} count={linkage['event_count']}"
        )
        for member in band.get("member_zones") or []:
            print(f"        member id={member['id']!r} low={member['band_low']!r} high={member['band_high']!r}")

    # 4. Direct string comparison, the way the index does it.
    print(f"=== {timeframe}: direct key comparison ===")
    presented_keys = {
        (member["band_low"], member["band_high"])
        for band in bands["bands"]
        for member in (band.get("member_zones") or [])
    }
    for item in zone_payload:
        key = (item.get("band_low"), item.get("band_high"))
        print(f"    event key={key!r} present_in_presented_member_keys={key in presented_keys}")
    print(f"    presented member keys ({len(presented_keys)}):")
    for key in sorted(presented_keys, key=str):
        print(f"        {key!r}")
