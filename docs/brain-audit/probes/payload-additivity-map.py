"""Dump a worktree's dashboard payload as a flat path -> value map, for A/B comparison.

Usage:
    PYTHONPATH=src <worktree>/.venv/bin/python payload-additivity-map.py <out.json> <worktree-root>

Run once per worktree (e.g. the merged HEAD and the repaired tree), then compare the two maps
with ``payload-additivity-check.py``. The dashboard payload is stripped with the digest module's
own ``_strip_audit``, so this sees exactly what the decision-output regression test sees.
"""

import json, sys, tempfile
from datetime import timedelta
from pathlib import Path

root = Path(sys.argv[2])
sys.path.insert(0, str(root / "tests"))
sys.path.insert(0, str(root / "src"))

from decision_digests import build_state, _strip_audit, DASHBOARD_CANDLES, boundary_offsets
from test_decision_invariance import EPOCH
from trading_assistant.web.dashboard_service import DashboardService

def walk(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(walk(v, f"{prefix}/{k}"))
    elif isinstance(value, list):
        out[f"{prefix}/#len"] = len(value)
        for i, v in enumerate(value):
            out.update(walk(v, f"{prefix}/{i}"))
    else:
        out[prefix] = value
    return out

tmp = Path(tempfile.mkdtemp())
service = DashboardService(build_state(tmp, DASHBOARD_CANDLES))
result = {}
for offset in boundary_offsets(DASHBOARD_CANDLES):
    payload = service.dashboard(symbol="BTC/USDT", timeframe="1h", as_of=EPOCH + timedelta(hours=offset))
    result[str(offset)] = walk(json.loads(json.dumps(_strip_audit(payload), sort_keys=True, default=str)))
Path(sys.argv[1]).write_text(json.dumps(result, sort_keys=True, default=str))
print("boundaries:", list(result))
