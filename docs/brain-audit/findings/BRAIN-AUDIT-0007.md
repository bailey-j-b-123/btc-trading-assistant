# BRAIN-AUDIT-0007 — Retest and failed-breakout projections carry no close, so a linked event can never state where price sat

- Severity: **P3** (evidence completeness; no wrong number is shown, but a documented per-event
  fact — "where price sat relative to the band at that instant" — was impossible for two of the
  four event kinds).
- Status: **CONFIRMED while repairing BRAIN-AUDIT-0005/0006** → **REPAIRED** in `926fea2` on branch
  `arena/0d6ba3f7-btc-trading-assistant` (PR #71) — **READY FOR INDEPENDENT RECHECK**; not
  independently verified by the repairing agent. Record: `../REPAIR-0005-0006.md`.
- Component: `src/trading_assistant/web/chart_evidence.py::_event_item` (retest and failed-breakout
  branches); consumer `src/trading_assistant/web/zone_evidence.py` (`_event_close`, and the
  `close_at_event` / `position_at_event` fields it feeds); documented claim in
  `docs/visual-signoff/support-resistance/README.md` §1.
- Affected commits: PR #66 `a5c9013c…`, unchanged at PR #69 `09f5ee2…`.
- Scope: exists **individually** (a projection gap, not a linkage bug); discovered on the repaired
  tree while verifying 0005, because only then did retest records actually reach the linkage.

## Expected / actual

Expected: a reported event states the close it was detected with, so its `position_at_event` can be
read against its own recorded band — the README's own description of the feature.

Actual: `_event_item` emits `breakout_close` only on the `breakout` branch and `reclaim_close` only
on the `sweep` branch. Retests and failed breakouts carried **no** close of any kind, so
`zone_evidence._event_close` returned `None` for every record of those two kinds and every such
record reported `"close_at_event": null, "position_at_event": null`. With 0005 unfixed the omission
was invisible (no retest ever reached the linkage); repairing 0005 alone would have surfaced up to
10 retest records per view whose band position read `null` while the documentation promised it.

## Reproduction (before the fix, on the merged tree)

1. Seed the demo database and project the payload (commands in `../REPAIR-0005-0006.md` §7).
2. Inspect `chart_evidence.retests`: 30 records, none with a `close`, `breakout_close` or
   `reclaim_close` key; `chart_evidence.failed_breakouts`: 3 records, none with a close.
3. Link them: every record reports `close_at_event == None` and `position_at_event == None`.

## Fix

Both branches now emit the event's own candle close (`"close": _dec(event.candle.close)`), which
`_event_close` already reads. Regression coverage:
`tests/test_zone_evidence.py::test_a_real_retest_projects_the_reference_it_was_measured_against`
asserts the projected close and a concrete `position_at_event`, and
`tests/test_zone_event_links_stored.py` asserts every reported record on stored data has a
non-null close and one of the three position values.

## Acceptance criteria

1. Every event kind that can link reports the close its own detector recorded.
   **Met** — `tests/test_zone_evidence.py` (real `Retest` and `FailedBreakout` objects) and
   `tests/test_zone_event_links_stored.py` (stored synthetic demo candles).
2. A linked record's `position_at_event` is computed from the event's own recorded close and its own
   recorded reference bounds. **Met** — `_link_record` reads the event's own bounds; asserted in
   both suites.
3. No change to detector thresholds, plan, journal or ledger identities. **Met** — the decision
   digest fixture is byte-identical (`decision_n1000.json`), and the path-by-path audit shows no
   payload change outside the two evidence blocks.

## Evidence files

- `../evidence/repair-0005-0006-stored-data.txt` (payload composition after the fix).
- `../evidence/repair-0005-0006-payload-additivity.txt` (no decision-bearing change).
