# BRAIN-AUDIT-0005 — Retests can never be linked to a zone band (the projection drops the reference)

- Severity: **P2** (incorrect interface claim; an advertised capability is unreachable).
- Status: **CONFIRMED at audit time** → **REPAIRED** in `926fea2` (branch `arena/0d6ba3f7-btc-trading-assistant`, PR #71) — **READY FOR INDEPENDENT RECHECK**; not independently verified by the repairing agent. See `../REPAIR-0005-0006.md` and `../evidence/repair-0005-0006-stored-data.txt`.
- Component: `src/trading_assistant/web/chart_evidence.py::_event_item` (retest branch, lines 247–260 at `09f5ee2`) and `src/trading_assistant/web/zone_evidence.py::link_events_to_bands` (reference-type filter, line 182).
- Affected commits: PR #66 `a5c9013c491938dbbf8317af75da3f4793a5a3a9`; still present in PR #69 `09f5ee2113b3cf35015e4d0058f45fa212cbe529`.
- Scope: exists **individually** on #66. Not caused by integration (#65/#67 do not touch this path). No trading rule, plan, journal or ledger record is affected — this is a read-only presentation/accounting path.

## Expected / actual

Expected: every event kind the support/resistance linkage advertises can be linked when the event's **own** stored reference is a `zone` reference. That set is stated in four places on #66: the `zone_evidence` module docstring ("breakout, failed breakout, sweep or retest"), `_EVENT_KINDS = ("breakout", "failed_breakout", "sweep", "retest")`, the `counts` payload rendered by the UI (`counts.retest`), and the owner-facing sentence "N stored event(s) referenced it"/"Used as the reference level of … retests".

Actual: the shared projection that the linkage consumes omits `reference_type` for retests. The `breakout` branch sets it, the `failed_breakout` branch sets it, the `sweep` branch sets it — the `retest` branch does not. `link_events_to_bands` then discards every event whose `reference_type` is not exactly `"zone"` (line 182), so **a retest can never be linked, whatever its bounds are**.

Measured on the feature's own demo database (`scripts/seed_sr_demo.py`, the same synthetic dataset #66 signed off against), 4h frame, `as_of=2026-10-14T00:00:00Z`:

| kind | projected by the linkage path | projected carrying `reference_type` | zone-referenced in the raw detector objects |
|---|---|---|---|
| breakout | 15 | 15 | 5 |
| failed_breakout | 3 | 3 | 1 |
| sweep | 0 | 0 | 0 |
| retest | 30 | **0** | **10** |

Ten of those thirty retests really do hold a `zone` reference in the detector objects; all thirty reach the linkage with no reference at all. The same pattern holds at the 15m and 1h frames and at the positive-control instant (8 retests projected, 0 with a reference; 4 zone-referenced in the objects). Full output: [`../evidence/zone-linkage-evidence.txt`](../evidence/zone-linkage-evidence.txt).

Secondary consequence: the retest projection also omits `reference_id`, which `_link_record` reads and reports, so a linked retest record could not have carried its reference id either.

## Reproduction

```sh
# 1. A worktree at the audited commit (read-only; no branch switch in the audit clone)
git worktree add --detach /tmp/pr69 09f5ee2113b3cf35015e4d0058f45fa212cbe529

# 2. Disposable demo database (synthetic by construction; nothing is fetched)
export TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-audit-sr.sqlite3
rm -f /tmp/brain-audit-sr.sqlite3
python -m alembic upgrade head
python scripts/seed_sr_demo.py

# 3. The probe (in this audit feed's probes/, not application code)
PYTHONPATH=src python docs/brain-audit/probes/zone-linkage-evidence.py
```

The probe prints, per timeframe: the projected counts, how many projected events
carry a `reference_type`, the raw detector reference types, and the per-band linkage.
The decisive lines are `retest  projected= 30 carrying reference_type=  0` against
`retest  n= 30 {'swing_high': 16, 'zone': 10, 'range_high': 4}`.

Assertion that isolates it in one line:

```sh
PYTHONPATH=src python - <<'PY'
from datetime import UTC, datetime, timedelta
from trading_assistant.web.dashboard_service import DashboardService, zone_link_events
from trading_assistant.web.state import create_default_state
svc = DashboardService(create_default_state())
_s, frame, _f = svc._evaluate(exchange="binance", symbol="BTC/USDT", timeframe="4h",
                              as_of=datetime(2026, 10, 14, tzinfo=UTC))
payload = zone_link_events(frame.patterns, timeframe="4h")
retests = [e for e in payload if e.get("kind") == "retest"]
print(len(retests), sum(1 for e in retests if e.get("reference_type")))
PY
# -> 30 0
```

## Why CI is green

`tests/test_zone_evidence.py` exercises the linkage with **hand-built** event dicts whose helper always supplies `reference_type` (default `"zone"`), and the only projection test (`test_event_projection_is_the_same_shape_the_chart_evidence_uses`) projects a **breakout**. No test builds a real `Retest` object, projects it, and passes it to the linkage, so the omission is invisible to all 481 Node + 1254 Python tests at the audited head.

## Acceptance criteria

1. `_event_item`'s retest branch carries `reference_type` (and `reference_id`) taken from `event.breakout.reference`, exactly as the `breakout` branch does for its own reference — or the linkage and every document/UI string stop advertising retests as linkable.
2. A regression test constructs a real `Retest` (real `pattern_liquidity` objects, not a hand-made dict) whose `breakout.reference.type == "zone"`, projects it with `chart_evidence.project_event`, and asserts the linkage reports it under `counts.retest`. A second case with a `swing_high` reference must stay unlinked.
3. The demo probe above shows the ten genuinely zone-referenced 4h retests under `linked_events.counts.retest` (subject to BRAIN-AUDIT-0006's identity rule being settled first).
4. No change to detector thresholds, plan, journal or ledger identities.

## Evidence files

- `../evidence/zone-linkage-evidence.txt` — consolidated per-kind counts and per-band linkage (4h end state, 4h positive control, 1h).
- `../evidence/zone-linkage-cause-4h.txt` — event reference keys vs presented member keys.
- `../evidence/session2-baseline-and-integration.txt` — the 1254-passing integration suite that does not catch this.
- `docs/visual-signoff/support-resistance/support-resistance-report.json` (in #66) — `"linked": true` occurs **0** times, `"linked": false` **36** times.
