"""Read-only probe of the Support & Resistance payloads for one or more timeframes.

Prints exactly what the API hands the chart, with no interpretation added:

* the viewed-timeframe presentation from ``DashboardService.dashboard``
  (``overlays.zone_bands``: presented/hidden accounting, test counts and their
  basis, merged members, stored-event links, fade state);
* the per-timeframe ``zone_bands`` of ``DashboardService.structure`` — the payload
  the dashboard's higher-timeframe ladder reads;
* the supported-timeframe list the timeframe ladder is built from.

Nothing is written anywhere and no plan, journal or ledger record is produced.
"""

from __future__ import annotations

import sys

from trading_assistant.web.dashboard_service import DashboardService
from trading_assistant.web.state import create_default_state

service = DashboardService(create_default_state())


def report_bands(label: str, bands: dict, *, indent: str = "  ") -> None:
    print(
        f"{indent}{label}: stored={bands.get('total_stored')} presented={bands.get('presented_count')} "
        f"drawn={len(bands.get('bands') or [])} hidden={bands.get('hidden_count')} "
        f"(above={bands.get('hidden_above')} below={bands.get('hidden_below')} "
        f"inside={bands.get('hidden_inside')}) merged={bands.get('merged_count')} "
        f"skipped_invalid={bands.get('skipped_invalid')} reason={bands.get('reason')}"
    )
    for band in bands.get("bands") or []:
        linkage = band.get("linked_events") or {}
        links = linkage.get("events") or []
        print(
            f"{indent}  [{band['position']:>12}] {band['band_low']:>10} - {band['band_high']:>10} "
            f"tf={band.get('source_timeframe')} tests={band.get('touch_count')}"
            f"({band.get('touch_count_basis')})"
            f"(swing evidences={band.get('source_swing_count')}) "
            f"distinct={band.get('distinct_test_timestamps')} shared={band.get('shared_candle_tests')} "
            f"cross={band.get('shared_timestamps_across_member_zones')} "
            f"merged={band.get('merged_zone_count')} faded={band.get('faded')} "
            f"distance={band.get('distance_from_close')} linked={linkage.get('linked')} "
            f"events={[(link.get('kind'), link.get('known_at'), link.get('position_at_event')) for link in links]}"
        )
        if linkage.get("note"):
            print(f"{indent}    link note: {linkage['note']}")


for timeframe in sys.argv[1:] or ["1h"]:
    dashboard = service.dashboard(symbol="BTC/USDT", timeframe=timeframe)
    overlays = dashboard.get("overlays") or {}
    print(
        f"=== {timeframe} as_of={dashboard['meta']['as_of']} "
        f"supported={dashboard['meta']['supported_timeframes']}"
    )
    counts = (dashboard.get("chart_evidence") or {}).get("counts")
    print(f"  chart evidence counts: {counts}")
    report_bands("viewed bands (dashboard.overlays.zone_bands)", overlays.get("zone_bands") or {})
    structure = service.structure(symbol="BTC/USDT", timeframe=timeframe)
    report_bands("structure endpoint bands (/api/market/structure)", structure.get("zone_bands") or {})
