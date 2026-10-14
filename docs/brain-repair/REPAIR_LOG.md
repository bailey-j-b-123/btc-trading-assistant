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
