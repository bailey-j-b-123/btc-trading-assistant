# Support & Resistance — where the work stands

Short status for review. The measured evidence is in `README.md`; the machine-readable browser report
is `support-resistance-report.json` beside this file.

## Verified now (commands and results)

| What | Command | Result |
|---|---|---|
| Browser behaviour, 4 sizes | `node scripts/browser-audit/support-resistance-check.mjs http://127.0.0.1:8123 ./out --timeframe 1h --switch 4h` | passed at 1536×864 / 1440×900 / 1920×1080 / 390×844 |
| Frontend suites | `npm test` | 436 passed / 0 failed |
| Display band contract | `.venv/bin/python -m pytest tests/test_zone_presentation.py -q` | 34 passed |
| Event links | `.venv/bin/python -m pytest tests/test_zone_evidence.py -q` | 15 passed |
| Detector characteristics | `.venv/bin/python -m pytest tests/test_zone_detector_audit.py -q` | 25 passed |
| Payloads per timeframe | `.venv/bin/python scripts/sr_payload_probe.py 15m 1h 4h` | see README §4 |
| Performance | `DashboardService.dashboard(timeframe="1h")` | 13.1 s cold / 3.2 s warm, unchanged by this branch |
| Full Python suite | `.venv/bin/python -m pytest -q` | **1233 passed, 1 xfailed** in 255.95 s (the xfail is the pre-existing documented defect, untouched) |
| Lint | `.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests` | 15 findings — byte-identical to the same command on `a0f1faf`; this branch adds none |

## Open before this can be called finished

1. **Owner review against the approved reference screenshot.** That asset has not been available to
   this session, so no visual acceptance is claimed. The screenshots here are behavioural evidence.
3. ~~**A live `linked: true` zone-to-event link** on a database whose stored breakout/sweep/retest
   used a zone reference (the demo series' events use swing references, so the path is covered by
   fixtures, not by a stored record).~~ **Resolved (2026-10-14, BRAIN-AUDIT-0005/0006 repair).**
   The stated cause was a measurement error: the demo series' events do hold zone references
   (5/15 breakouts, 1/3 failed breakouts, 10/30 retests). No retest could reach the linkage at all
   (the retest projection omitted its reference), and the rest were dropped because their recorded
   bounds no longer equal the current frame's re-derived zone. After the repair the 4h demo
   database reports `linked: true` at `2026-08-26T08:00:00Z` (band `85958.6 – 86543.0`, one
   breakout) and reports earlier recorded bands inside the evolved bands as contained references
   (16 on this database). See `../../brain-audit/REPAIR-0005-0006.md` and
   `../../brain-audit/evidence/repair-0005-0006-stored-data.txt`.
4. **A 1D band drawn on a lower timeframe** — needs a deployment configured with `1d` and real stored
   1D candles; this session verified the ladder and the refusal to invent a frame above 4H.
5. Design question, not a defect: the default support wash strength on the 60-day demo series.

## Not touched

Chart Patterns, strategy qualification, trade plans, risk thresholds, the journal, the forward
ledger, plan identities, production databases. No orders, no credentials, no merges.
