# BRAIN-AUDIT-0006 — Linked-event accounting cannot fire for historical references, and the UI states an absolute negative

- Severity: **P2** (misleading operator-facing evidence statement; the advertised read fails on every realistic view).
- Status: **CONFIRMED**. Residual after BRAIN-AUDIT-0005 is repaired: these are separate causes.
- Component: `src/trading_assistant/web/zone_evidence.py::link_events_to_bands` (exact-bounds index), wired at `src/trading_assistant/web/dashboard_service.py` (`_zone_bands(..., events=events)`, `zone_link_events`), presented via `src/trading_assistant/web/zone_presentation.py`; operator strings at `src/trading_assistant/web/static/js/explain.js` (`linkedEventSentence`, `linkedRows`) and `src/trading_assistant/web/static/js/views/dashboard.js::zoneRow`.
- Affected commits: PR #66 `a5c9013c491938dbbf8317af75da3f4793a5a3a9`; still present at PR #69 `09f5ee2113b3cf35015e4d0058f45fa212cbe529`.
- Scope: exists **individually** on #66; not integration-caused. Read-only presentation; no plan, journal or ledger record is affected.

## Expected / actual

Expected: a displayed band that was broken or retested shows the stored events whose own reference **is that level**, or the operator is told only what the stored evidence actually establishes.

Actual: a link requires **exact equality of decimal bounds** between the event's reference and the band's (or a member zone's) stored bounds. Zone bounds are re-derived per analysis frame — the detector's clusters evolve as further swings confirm — while each historical event keeps the reference it was detected with. Historical references therefore almost never equal the current band. On the demo database #66 signed off against (4h, `as_of=2026-10-14T00:00:00Z`):

- 6 projected events hold a `zone` reference (5 breakouts, 1 failed breakout; plus 10 retests invisible per BRAIN-AUDIT-0005).
- **All 6 overlap a currently displayed band, and 5 are strictly contained in one.** None links.
- Every one of the 5 presented bands reports `linked=false`, so the explanation panel prints **"No stored breakout, sweep or retest used this zone as its own reference level."** and the fact row prints **"none recorded"**.

Worked examples from `../evidence/zone-linkage-cause-4h.txt` and `../evidence/zone-linkage-evidence.txt`:

| displayed band | zone-referenced events that actually used that level | reported |
|---|---|---|
| `85958.6 – 87052.5` | `85958.6 – 86543.0` (1 breakout, 2 retests — same lower edge) | no link |
| `87180.3 – 88340.1` | `87369.0 – 88340.1` (1 breakout, 2 retests — same upper edge) | no link |
| `88363.8 – 89574.0` | `88363.8 – 89027.9` (1 breakout, 2 retests — same lower edge) | no link |
| `85958.6 – 87052.5` | `86543.0 – 86543.0`, `86076.2 – 86543.0` (2 breakouts, 4 retests) | no link |

Positive control (proves the mechanism itself works and isolates the cause to the identity rule): re-evaluating the **same detector** at `as_of=2026-08-26T08:00:00Z`, the instant of the `85958.6 – 86543.0` breakout, links that band — `linked=True`, `event_count=1`, `counts={'breakout': 1, ...}` (`../evidence/zone-linkage-positive-control-4h.txt`). At the demo end state the same band has evolved to `85958.6 – 87052.5` and reports no link (`../evidence/zone-linkage-endstate-4h.txt`).

The absolute negative is therefore unsupported by the stored evidence the same payload carries: the level *was* broken and retested; only the re-derived bounds differ by 509.9 points at one edge.

## Evidence-accuracy correction to #66's own status

`docs/visual-signoff/support-resistance/README.md` (§ "never observed in the `linked: true` state", lines ~208–211) and `STATUS.md` (open item 3) attribute the absence of a live link to *"the demo series' events use swing references"*. That reason is factually wrong for the dataset they describe: on the 4h demo frame the detector objects contain **5 zone-referenced breakouts, 1 zone-referenced failed breakout and 10 zone-referenced retests**. The path is not merely "covered by fixtures, not by a stored record" — with the current identity rule it cannot produce a link for any event older than the current frame. The feature's own browser sign-off report records `"linked": true` zero times across 36 checks.

## Reproduction

```sh
# same disposable demo database as BRAIN-AUDIT-0005
export TRADING_ASSISTANT_DATABASE_URL=sqlite:////tmp/brain-audit-sr.sqlite3
PYTHONPATH=src python docs/brain-audit/probes/zone-linkage-cause.py 4h
PYTHONPATH=src python docs/brain-audit/probes/zone-linkage-asof.py 4h 2026-08-26T08:00:00   # positive control
PYTHONPATH=src python docs/brain-audit/probes/zone-linkage-asof.py 4h 2026-10-14T00:00:00   # end state
PYTHONPATH=src python docs/brain-audit/probes/zone-linkage-evidence.py
```

No server, exchange, credential or production database is involved; the demo data is
explicitly synthetic and is labelled as such by its own seed script.

## Acceptance criteria

1. The identity rule is stated explicitly in code and documentation, and the operator-visible sentence claims only what that rule proves. In particular, the string "No stored breakout, sweep or retest used this zone as its own reference level." must not be emitted from a bound-equality test alone; a band whose displayed bounds contain or are contained by a stored reference must say *that* (for example an explicit "overlapping, non-identical reference" fact) instead of an absolute negative.
2. An end-to-end regression test on **stored detector output** (not hand-built payloads) demonstrates at least one true positive and one true negative at a single fixed `as_of`, so the rule is pinned against the pipeline's own time model.
3. `linked_events` counts must be re-derived after the rule changes, and #66's `README.md`/`STATUS.md` statements corrected to match the measured evidence (this finding supersedes the "events use swing references" explanation).
4. No detector threshold, plan, journal or ledger change.

## Evidence files

- `../evidence/zone-linkage-evidence.txt`
- `../evidence/zone-linkage-cause-4h.txt`
- `../evidence/zone-linkage-positive-control-4h.txt`
- `../evidence/zone-linkage-endstate-4h.txt`
