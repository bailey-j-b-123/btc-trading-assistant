"""Versioned identity helpers for the Step 13 multi-timeframe hierarchy.

Every stored hierarchy value is built from these helpers, so identities are
deterministic SHA-256 fingerprints of canonical JSON rather than random UUIDs,
exactly like Steps 4-7 and Step 12. A semantic change to the hierarchy contract
is a version bump, which produces different identities instead of silently
reinterpreting earlier recorded history.
"""

from __future__ import annotations

import json
from hashlib import sha256

from trading_assistant.market_structure.snapshot import to_jsonable

#: Version of the multi-timeframe hierarchy contract (identity prefix for every
#: fingerprint in this package). Bump when hierarchy semantics change.
HIERARCHY_RULES_VERSION = "multi-timeframe-hierarchy-v1"

#: Version of the hierarchy ledger rows (stored observation identity + fields).
HIERARCHY_LEDGER_RULES_VERSION = "multi-timeframe-ledger-v1"


def fingerprint(*parts: object) -> str:
    """Return a canonical, version-prefixed identity for hierarchy material."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((HIERARCHY_RULES_VERSION + ":" + payload).encode()).hexdigest()


def ledger_fingerprint(*parts: object) -> str:
    """Return a canonical identity under the ledger rules version."""

    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    return sha256((HIERARCHY_LEDGER_RULES_VERSION + ":" + payload).encode()).hexdigest()


def canonical_json(value: object) -> str:
    """Exact stored rendering of any hierarchy value (sorted keys, no whitespace)."""

    return json.dumps(to_jsonable(value), sort_keys=True, separators=(",", ":"))


__all__ = [
    "HIERARCHY_LEDGER_RULES_VERSION",
    "HIERARCHY_RULES_VERSION",
    "canonical_json",
    "fingerprint",
    "ledger_fingerprint",
]
