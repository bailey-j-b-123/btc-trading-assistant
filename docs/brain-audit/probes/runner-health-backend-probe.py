"""BRAIN AUDIT probe (independent): exercise the repaired runner-health policy directly.

Read-only. Calls the real backend function, not the project's own tests.

Usage: PYTHONPATH=<worktree>/src python runner-health-backend-probe.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trading_assistant.forward_testing.runner_health import assess_runner_health

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def beat(seconds_ago: float, status: str = "IDLE", last_error: str | None = None):
    return SimpleNamespace(
        recorded_at=NOW - timedelta(seconds=seconds_ago),
        status=status,
        last_error=last_error,
    )


def row(seconds_ago: float, status: str = "IDLE"):
    return SimpleNamespace(recorded_at=NOW - timedelta(seconds=seconds_ago), status=status, last_error=None)


cadence = [row(60 * i) for i in range(1, 9)]  # one heartbeat a minute

cases = [
    ("recent", beat(30), cadence),
    ("just under floor", beat(359), cadence),
    ("just over floor", beat(361), ()),
    ("long stale", beat(604800), ()),
    ("future", beat(-3600), ()),
    ("error", beat(30, status="IDLE", last_error="boom"), ()),
    ("error status", beat(30, status="ERROR"), ()),
    ("stopped", beat(30, status="STOPPED"), ()),
    ("odd status", beat(30, status="THINKING"), ()),
    ("never run", None, ()),
    ("slow cadence trusted (10 min gaps)", beat(400), [row(600 * i) for i in range(1, 9)]),
]

for name, heartbeat, recent in cases:
    result = assess_runner_health(heartbeat, now=NOW, recent_heartbeats=recent)
    print(
        f"{name:36s} state={result['state']:18s} alive={str(result['alive_confirmed']):5s} "
        f"age={result['age_seconds']} tol={result['tolerance_seconds']} "
        f"src={result['tolerance_source']:18s} cadence={result['cadence_seconds']} ({result['cadence_source']})"
    )

# Operator tolerance override must widen, and a bad override must be refused.
print("\noperator override:")
print(
    " ",
    assess_runner_health(beat(700), now=NOW, tolerance_seconds=900)["state"],
    assess_runner_health(beat(700), now=NOW, tolerance_seconds=900)["tolerance_source"],
)
for bad in (0, -1, 86401, True, 1.5, "600"):
    try:
        assess_runner_health(beat(30), now=NOW, tolerance_seconds=bad)
        print("  ACCEPTED invalid override:", repr(bad))
    except (ValueError, TypeError) as exc:
        print("  refused:", repr(bad), "->", type(exc).__name__)

# Naive datetime must be refused, not silently interpreted.
try:
    assess_runner_health(SimpleNamespace(recorded_at=datetime(2026, 10, 14, 11, 0), status="IDLE", last_error=None), now=NOW)
    print("  ACCEPTED naive recorded_at (timezone bug)")
except (ValueError, TypeError) as exc:
    print("  refused naive recorded_at ->", type(exc).__name__)
