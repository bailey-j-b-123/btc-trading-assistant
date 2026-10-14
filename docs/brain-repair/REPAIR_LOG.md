# BRAIN integration & repair log — DO NOT MERGE

## Guardrails
- Repair branch: `arena/0090b00d-btc-trading-assistant` (session-fixed; no other branches will be modified).
- No merge into main, production deployment, real-money trading, production database changes, or strategy/risk/execution changes without owner approval.
- Agent 1 audit feed: PR #68, `BRAIN AUDIT FEED — VERIFIED FINDINGS`.
- Repairs will be labelled READY FOR AUDITOR RECHECK, not independently verified here.

## Progress — 2026-10-14
- Initial checkout: `9da1d3cf7a8fcc902b734f964a30c95032ee8d0d`; clean worktree.
- Opened tracking work before application changes. PRs #62/#65/#66/#67 and audit #68 located.
- In progress: fetch ancestry, inspect changes/dependencies, establish test baseline, then integrate only compatible changes.
- Tests: not yet run. No application code changed.
- Branch constraint: preserve this session's branch; bring verified #62 ancestry into it rather than creating/switching to a different branch.

### Investigation checkpoint
- Draft repair PR #69 opened immediately; retargeted to #62 after ancestry verification. `gh pr edit` hit an installed CLI/Classic Projects compatibility error; equivalent `gh api PATCH` succeeded.
- #62 base import: `fb80c11` (normal ancestry-preserving merge). Full pinned SHAs/file manifests and overlap analysis: `INTEGRATION.md`.
- `npm test` baseline: **425 passed / 0 failed**. Python baseline still running; no result claimed yet.
- Received and reproduced **BRAIN-AUDIT-0001 (P2)**: #65/#66 dashboard merge conflict. Mechanical combined resolution identified; application integration pending baseline completion.
- Browser tooling installed locally (untracked): puppeteer-core with optional sandbox Chromium 133. npm reports 7 high-severity dependency advisories; not blindly applying breaking audit fixes. Browser harness dependencies are separate from the application.

### Baseline complete
- `.venv/bin/python -m pytest -q`: **1182 passed, 1 xfailed, 1 warning**, 257.96 s. The documented shallow-history expected failure remains untouched. Warning is the installed Starlette/httpx deprecation.
- `.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests`: **15 pre-existing findings**; log retained for comparison.
- Baseline evidence and resolved Python dependencies are in `evidence/`.
- Added a disposable offline browser-server wrapper: accepts only existing databases under ignored `.cache/`, disables HTTP writes and external display-only quote/socket calls, allows sandbox framing without altering production security headers. Application market/evidence APIs are not mocked. Synthetic 600-candle DB seeded under `.cache/brain-repair/`; no genuine market data available.
- Chromium initially failed to launch (missing system NSS/NSPR libraries). Extracted the optional Chromium package's bundled libraries into ignored `.cache/`; rerun uses `LD_LIBRARY_PATH`. No system package or application changes needed.

### BRAIN-AUDIT-0001 — integration repair
- #65 merged at `a51344db23a0ac0691afe0149bf5dde215b9add7`.
- #66 merged with mechanical conflict resolution at **`f15cd1a558a56b1e62db2fd9b6ef6595a2de0ee6`**: retained `drawnModel`, `paintPatternAnnotation`, and the extracted `updateEvidenceMeta` called after both evidence and overlay updates. No entire-file replacement.
- #67 merged cleanly at **`e07e4e6affcff4d742f336e6460e6451bb5a9583`**. All three source heads are ancestors, not cherry-picked duplicates.
- Existing combined `npm test`: **464 passed / 0 failed**. Added a combined refresh/selection regression in `tests/frontend/dashboard.test.mjs`: all three S/R tallies agree after new overlays, pattern selection remains functional, chart instance and trade decision payloads are preserved.
- Targeted regression passes. An isolated copy with the overlay-meta refresh removed fails the new assertion (see `evidence/0001-mutation.txt`); no tracked implementation was mutated for that negative control.
- Baseline and combined Breakouts browser checks: all four sizes pass, zero page/console errors. Further combined Python/browser checks running.
- Status: **READY FOR AUDITOR RECHECK** of implementation; final regression matrix still in progress.

### Incoming queue
- **BRAIN-AUDIT-0002 (P2)**: investigate existing PR #53 liveness repair; do not duplicate it.
- **BRAIN-AUDIT-0003 (P2)**: residual wrong-timeframe swing fallback in #67, reproduced by audit probe; pending repair.
- **BRAIN-AUDIT-0004 (P2)**: retest lifecycle outcome attached to observation candle; pending read-only UI repair/tests.
- Sent new non-engine `zone_bands` omission observation to Agent 1 for independent confirmation/ID; not yet labelled verified.

### BRAIN-AUDIT-0003 — wrong-timeframe swing fallback
- Reproduced **5/5 new regressions failing** before repair: model invalid/unavailable evidence plus actual 15M/4H failed and empty candle responses with a bullish 1H engine.
- Root causes: unconditional engine-trend fallback, and failure/empty paths refreshing only the Pattern panel, leaving the Swing panel stale.
- Repair: validate trend directions, require available evidence and explicit matching timeframe for legacy fallback; caller supplies engine identity only on the engine view. Unavailable candle paths refresh every function panel. Source records and trading decisions unchanged.
- The prior #67 fallback test lacked timeframe identity. It now supplies and explicitly requires same-frame identity, alongside the new adverse controls; assertions were not removed or weakened.
- Changed files: `src/trading_assistant/web/static/js/function_panel.js`, `src/trading_assistant/web/static/js/views/dashboard.js`, `tests/frontend/function-panel.test.mjs`, `tests/frontend/timeframeswitch.test.mjs`, this log and `evidence/0003-{before,after}.txt`.
- `node --test tests/frontend/function-panel.test.mjs tests/frontend/timeframeswitch.test.mjs tests/frontend/dashboard.test.mjs`: **87 passed / 0 failed** (15.77 s).
- Status: **READY FOR AUDITOR RECHECK**. Exact repair SHA recorded in the subsequent GitHub handoff/comment.

### Browser checkpoint
- Combined `breakouts-check.mjs` and `patterns-check.mjs`: exit 0.
- `mobile-layout-check.mjs` and `desktop-bottom-check.mjs`: exit 1, uncaught `Incorrect locale information provided`. Initial mobile geometry had zero overflow/issues. Investigating the error rather than suppressing assertions; not claiming these suites pass.
