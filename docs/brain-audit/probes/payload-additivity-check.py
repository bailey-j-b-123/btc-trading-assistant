"""Compare two payload maps from ``payload-additivity-map.py`` and classify every difference.

Usage:
    python3 payload-additivity-check.py <head-map.json> <repaired-map.json>

Reports, per boundary:

* paths whose value changed although both sides have them (excluding array-length markers,
  which grow when records are added);
* records in the zone-band linkage that are new, and pre-existing records that vanished;
* every changed path outside the two evidence blocks the BRAIN-AUDIT-0005/0006 repair touches
  (``overlays.zone_bands.*.linked_events`` and ``chart_evidence.retests`` /
  ``chart_evidence.failed_breakouts``).

The repair is additive by contract: pre-existing exact-link record *content* must not change or
vanish, and nothing outside those blocks may move.
"""

from __future__ import annotations

import json
import re
import sys

# Keys the repair adds to every linkage record. They are stripped before records are compared,
# so a record that only gained these keys still counts as unchanged content.
ADDED_RECORD_KEY_PREFIXES = ("match", "linked_via_bounds")

ALLOWED = re.compile(
    r"^/overlays/zone_bands/bands/\d+/linked_events/"
    r"|^/chart_evidence/(retests|failed_breakouts)/\d+/"
)


def records(flat: dict, base: str, count: int) -> set[str]:
    out = set()
    for index in range(count):
        prefix = f"{base}/{index}/"
        record = {
            key[len(prefix):]: value
            for key, value in flat.items()
            if key.startswith(prefix) and not key[len(prefix):].startswith(ADDED_RECORD_KEY_PREFIXES)
        }
        out.add(json.dumps(record, sort_keys=True))
    return out


def main(head_path: str, repaired_path: str) -> int:
    head = json.load(open(head_path))
    repaired = json.load(open(repaired_path))
    outside: list[str] = []
    lost_total = added_total = 0
    for boundary in sorted(head, key=int):
        a, b = head[boundary], repaired[boundary]
        outside += [
            key for key in set(a) | set(b)
            if a.get(key, "<missing>") != b.get(key, "<missing>") and not ALLOWED.match(key)
        ]
        for path in (k for k in a if k.endswith("/linked_events/events/#len")):
            base = path.rsplit("/#len", 1)[0]
            lost = records(a, base, a[path]) - records(b, base, b.get(f"{base}/#len", 0))
            added_total += len(records(b, base, b.get(f"{base}/#len", 0)) - records(a, base, a[path]))
            lost_total += len(lost)
            if lost:
                print(f"boundary {boundary}: {len(lost)} pre-existing records changed or vanished at {base}")
    print(f"models compared: {len(head)} boundaries {sorted(head, key=int)}")
    print(f"pre-existing exact-link records whose content changed or vanished: {lost_total}")
    print(f"newly linked records (the repair's intended effect): {added_total}")
    print(f"paths changed outside the repaired evidence blocks: {len(outside)}")
    for key in outside[:20]:
        print("   OUTSIDE:", key)
    return 0 if (not outside and not lost_total) else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
