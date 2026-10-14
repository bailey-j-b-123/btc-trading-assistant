"""BRAIN AUDIT probe 4: consolidated zone-linkage evidence.

Read-only. Real detector + real projection + real linkage on ONE synthetic demo DB
(scripts/seed_sr_demo.py). Prints:
  A. per-kind event counts and how many carry a `reference_type` in the projection
     that the linkage consumes;
  B. how many events are actually zone-referenced in the raw objects;
  C. link results for the presented bands at the demo end state and at one
     positive-control as_of (the instant an event was detected).

Run:
    TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-audit-sr.sqlite3 \
    PYTHONPATH=<worktree>/src .venv/bin/python zone-linkage-evidence.py
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from trading_assistant.web.dashboard_service import (
    DashboardService,
    _zone_bands,
    zone_link_events,
)
from trading_assistant.web.state import create_default_state

service = DashboardService(create_default_state())
SYMBOL = "BTC/USDT"

RAW_REFERENCE = {
    "breakout": lambda e: e.reference,
    "failed_breakout": lambda e: e.breakout.reference,
    "sweep": lambda e: e.reference,
    "retest": lambda e: e.breakout.reference,
}


def report(timeframe: str, as_of: datetime) -> None:
    _snapshot, frame, _frames = service._evaluate(
        exchange="binance", symbol=SYMBOL, timeframe=timeframe, as_of=as_of
    )
    patterns = frame.patterns
    structure = patterns.structure
    groups = {
        "breakout": patterns.breakouts,
        "failed_breakout": patterns.failed_breakouts,
        "sweep": patterns.sweeps,
        "retest": patterns.retests,
    }
    print(f"\n######## {timeframe} as_of={as_of.isoformat()} ########")
    print(f"current stored zones: {len(structure.levels.zones)}")
    print("current zone bounds:", [(str(z.band_low), str(z.band_high)) for z in structure.levels.zones])

    payload = zone_link_events(patterns, timeframe=timeframe)
    by_kind = Counter(str(item.get("kind")) for item in payload)
    with_reference_type = Counter(
        str(item.get("kind")) for item in payload if item.get("reference_type") is not None
    )
    print("A. projected events consumed by the linkage:")
    for kind in ("breakout", "failed_breakout", "sweep", "retest"):
        print(
            f"   {kind:16s} projected={by_kind.get(kind, 0):3d} "
            f"carrying reference_type={with_reference_type.get(kind, 0):3d}"
        )

    print("B. raw detector objects by reference type:")
    for kind, events in groups.items():
        types = Counter(RAW_REFERENCE[kind](event).type for event in events)
        print(f"   {kind:16s} n={len(events):3d} {dict(types)}")

    print("C. presented bands and their linkage:")
    bands = _zone_bands(structure.levels, timeframe, events=payload)
    linked = 0
    for band in bands["bands"]:
        linkage = band["linked_events"]
        linked += 1 if linkage["linked"] else 0
        members = [(m["band_low"], m["band_high"]) for m in band.get("member_zones") or []]
        print(
            f"   band {band['band_low']}-{band['band_high']} linked={linkage['linked']} "
            f"events={linkage['event_count']} counts={linkage['counts']} members={members}"
        )
    print(f"   -> bands with linked events: {linked} of {len(bands['bands'])}")


report("4h", datetime(2026, 10, 14, tzinfo=UTC))
report("4h", datetime(2026, 8, 26, 8, 0, tzinfo=UTC))
report("1h", datetime(2026, 10, 14, tzinfo=UTC))
