# Pinned state and environment

Audit date: 2026-10-14 UTC. Repository: bailey-j-b-123/btc-trading-assistant.

| Subject | Exact commit | Relationship |
|---|---|---|
| Session starting main | `9da1d3cf7a8fcc902b734f964a30c95032ee8d0d` | Starting checkout; not the feature integration baseline |
| #62 / common development base | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` | Based on #61; incorporates dashboard stack #57 → #59 → #60 → #61 |
| #65 Chart Patterns | `ee6ab770b71957a7c30f5d90db119c187330bcc8` | Based on #62, not #66 or #67 |
| #66 Support & Resistance | `a5c9013c491938dbbf8317af75da3f4793a5a3a9` | Based on #62, changes Python API evidence as well as UI |
| #67 Swing fixes | `e2237723a8d9437504fdb54fa491ca1759e0215b` | Based on #62, not #65 or #66 |

GitHub PR metadata, bodies, changed paths, checks and commits are preserved in `evidence/pr-{62,65,66,67}.json`. They are claims to verify, not test evidence produced by this auditor.

## Overlap and dependency risks

- #65/#66: chart.js, chart_functions.js, function_panel.js, views/dashboard.js, styles.css and dashboard.test.mjs.
- #65/#67: chart_functions.js, function_panel.js and styles.css.
- #66/#67: chart_functions.js, function_panel.js, styles.css and both chart-functions/function-panel tests.
- Shared paths do not by themselves prove textual conflicts. The #67 description's blanket “zero conflict” assertion is not established by individual CI.
- #66 adds overlay/evidence payload fields and updates a dashboard digest fixture. Decision digest preservation must be distinguished from payload identity.
- Other open repair/reporting PRs (#49–#56, #58, #63, #64) are not assumed present in this base; they need explicit dependency decisions, not wholesale integration.

## Test environment and safety

- Linux sandbox; Python 3.11.2; Node v22.22.3; npm 10.9.8.
- Isolated `.venv`; installed with `.venv/bin/pip install -e '.[dev]' ruff`.
- Exact resolved Python dependencies: `evidence/pip-freeze.txt`. Dependencies use ranges rather than a lock, so this resolution can differ from earlier CI.
- Zero npm dependencies; static ES modules, no frontend build step.
- Offline synthetic/fake-exchange tests and temporary SQLite only. No genuine market data file found under data/backups; no exchange downloads or historical backfills run.
- No approved owner reference has yet been located. Existing signoff screenshots are implementation evidence, not automatically approved references.

## Initial results

- `npm test`: **425 passed, 0 failed**, 19.087 seconds (full log saved).
- `.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests`: **15 findings**, exit 1 (full log saved). No fixes applied.
- `.venv/bin/python -m pytest -q`: **1182 passed, 1 xfailed, 1 warning in 276.80 seconds**, exit 0 (`evidence/baseline-pytest.txt`).
- Documented shallow-history expected failure preserved unchanged; independently reproduced again with targeted test: **1 xfailed in 17.55 seconds**.

## Publication setup correction

The clone's generic fetch did not populate the feature-base tracking reference. Initial `merge --ff-only` failed, but the audit documentation commit and draft PR creation succeeded. An explicit fetch and merge of the existing development base then succeeded (`403b368`). This was an ancestry import, not an auditor code modification. `git diff <common-base> HEAD` showed only audit documentation before test evidence was added. No merge into main occurred.

## Repair phase — BRAIN-AUDIT-0005 / 0006 (2026-10-14)

The audit role was extended by the owner to repair the two outstanding S/R event-linking defects
("BRAIN — FINAL AUDIT REPAIRS"). Because this session can push only the audit-feed branch, PR #69's
head (`09f5ee2`) was merged into the feed branch — merge `87efd45`, clean — and the repair sits
directly on top of that tree, so no work is duplicated.

| Subject | Exact commit |
|---|---|
| Feed tip before the repair | `e829355` |
| Merge of PR #69 head into the feed branch | `87efd45` |
| Repair (code, tests, regenerated dashboard digest fixture) | `926fea2` |

Status: **REPAIRED — READY FOR INDEPENDENT RECHECK**; not independently verified. Full record,
acceptance-criteria mapping, exact suite results and verification limits: `REPAIR-0005-0006.md`.
Browser checks could not be run in the repairing environment (no browser system libraries, no root,
download hosts outside the allowlist) — that limitation is stated in §6 of the repair record and is
part of the recheck.
