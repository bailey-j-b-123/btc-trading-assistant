# Integration investigation — 2026-10-14

## Pinned ancestry

| PR | Base SHA | Head SHA |
|---|---|---|
| #62 | `344c8cfa2b34633343eac871c46aaf93c8d21b84` | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` |
| #65 | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` | `ee6ab770b71957a7c30f5d90db119c187330bcc8` |
| #66 | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` | `a5c9013c491938dbbf8317af75da3f4793a5a3a9` |
| #67 | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` | `e2237723a8d9437504fdb54fa491ca1759e0215b` |

#62 is stacked on #61 → #60 → #59 → #57 → main. All of #65/#66/#67 have the exact #62 head as merge-base; none contains another feature head. Use normal merges, not cherry-picks. Session branch retains its initial progress-log commit and merges #62 ancestry.

## Shared files requiring review

- `src/trading_assistant/web/static/js/chart.js`: PRs 65, 66
- `src/trading_assistant/web/static/js/chart_functions.js`: PRs 65, 66, 67
- `src/trading_assistant/web/static/js/function_panel.js`: PRs 65, 66, 67
- `src/trading_assistant/web/static/js/views/dashboard.js`: PRs 65, 66
- `src/trading_assistant/web/static/styles.css`: PRs 65, 66, 67
- `tests/frontend/chart-functions.test.mjs`: PRs 66, 67
- `tests/frontend/dashboard.test.mjs`: PRs 65, 66
- `tests/frontend/function-panel.test.mjs`: PRs 66, 67

## Complete changed-file manifests (relative to each PR base)

### PR #62

- `docs/breakouts-signoff.md`
- `docs/visual-signoff/breakouts/breakouts-1440x900-default.png`
- `docs/visual-signoff/breakouts/breakouts-1440x900-framed.png`
- `docs/visual-signoff/breakouts/breakouts-1536x864-default.png`
- `docs/visual-signoff/breakouts/breakouts-1536x864-framed.png`
- `docs/visual-signoff/breakouts/breakouts-1536x864-full.png`
- `docs/visual-signoff/breakouts/breakouts-1536x864-stage.png`
- `docs/visual-signoff/breakouts/breakouts-1920x1080-default.png`
- `docs/visual-signoff/breakouts/breakouts-1920x1080-framed.png`
- `docs/visual-signoff/breakouts/breakouts-390-mobile-default.png`
- `docs/visual-signoff/breakouts/breakouts-390-mobile-framed.png`
- `docs/visual-signoff/breakouts/breakouts-390-mobile-full.png`
- `docs/visual-signoff/breakouts/breakouts-report.json`
- `scripts/browser-audit/README.md`
- `scripts/browser-audit/breakouts-check.mjs`
- `src/trading_assistant/web/static/js/breakout_stages.js`
- `src/trading_assistant/web/static/js/chart.js`
- `src/trading_assistant/web/static/js/chart_functions.js`
- `src/trading_assistant/web/static/js/function_panel.js`
- `src/trading_assistant/web/static/js/views/dashboard.js`
- `src/trading_assistant/web/static/styles.css`
- `tests/frontend/breakout-stages.test.mjs`
- `tests/frontend/chart-view.test.mjs`
- `tests/frontend/dashboard.test.mjs`
- `tests/frontend/function-panel.test.mjs`

### PR #65

- `docs/dashboard-visual-signoff.md`
- `docs/visual-signoff/patterns/REPORT.md`
- `docs/visual-signoff/patterns/before-breakouts-1672x941.png`
- `docs/visual-signoff/patterns/before-patterns-1672x941.png`
- `docs/visual-signoff/patterns/comparison-before-after-1672x941.png`
- `docs/visual-signoff/patterns/multi-pattern/01-1672-double-bottom-default.png`
- `docs/visual-signoff/patterns/multi-pattern/02-1672-head-and-shoulders-default.png`
- `docs/visual-signoff/patterns/multi-pattern/03-1672-select-inverse-head-and-shoulders.png`
- `docs/visual-signoff/patterns/multi-pattern/04-1672-select-double-top-confirmed.png`
- `docs/visual-signoff/patterns/multi-pattern/05-1672-select-double-top-invalidated.png`
- `docs/visual-signoff/patterns/multi-pattern/06-1672-select-trading-range.png`
- `docs/visual-signoff/patterns/multi-pattern/07-1672-select-double-bottom-invalidated.png`
- `docs/visual-signoff/patterns/multi-pattern/08-1440-double-bottom.png`
- `docs/visual-signoff/patterns/multi-pattern/09-1920-double-bottom.png`
- `docs/visual-signoff/patterns/multi-pattern/10-390-mobile-double-bottom.png`
- `docs/visual-signoff/patterns/multi-pattern/11-1672-detailed.png`
- `docs/visual-signoff/patterns/multi-pattern/12-1672-4h-empty-states.png`
- `docs/visual-signoff/patterns/multi-pattern/13-1672-breakouts-unchanged.png`
- `docs/visual-signoff/patterns/multi-pattern/REPORT.md`
- `docs/visual-signoff/patterns/multi-pattern/audit-multi-pattern-demo.json`
- `docs/visual-signoff/patterns/multi-pattern/audit-standard-demo.json`
- `docs/visual-signoff/patterns/patterns-1440x900-breakouts.png`
- `docs/visual-signoff/patterns/patterns-1440x900-default.png`
- `docs/visual-signoff/patterns/patterns-1440x900-double-bottom.png`
- `docs/visual-signoff/patterns/patterns-1672x941-4h.png`
- `docs/visual-signoff/patterns/patterns-1672x941-breakouts.png`
- `docs/visual-signoff/patterns/patterns-1672x941-default.png`
- `docs/visual-signoff/patterns/patterns-1672x941-detailed.png`
- `docs/visual-signoff/patterns/patterns-1672x941-double-bottom.png`
- `docs/visual-signoff/patterns/patterns-1672x941-full.png`
- `docs/visual-signoff/patterns/patterns-1920x1080-breakouts.png`
- `docs/visual-signoff/patterns/patterns-1920x1080-default.png`
- `docs/visual-signoff/patterns/patterns-1920x1080-double-bottom.png`
- `docs/visual-signoff/patterns/patterns-390-mobile-breakouts.png`
- `docs/visual-signoff/patterns/patterns-390-mobile-default.png`
- `docs/visual-signoff/patterns/patterns-390-mobile-double-bottom-full.png`
- `docs/visual-signoff/patterns/patterns-390-mobile-double-bottom.png`
- `docs/visual-signoff/patterns/patterns-report.json`
- `docs/visual-signoff/patterns/reference-1536/01-1536-double-bottom-confirmed.png`
- `docs/visual-signoff/patterns/reference-1536/02-1536-default-selection-with-secondary.png`
- `docs/visual-signoff/patterns/reference-1536/03-1536-select-head-and-shoulders.png`
- `docs/visual-signoff/patterns/reference-1536/04-1536-select-inverse-head-and-shoulders.png`
- `docs/visual-signoff/patterns/reference-1536/05-1536-select-double-top-invalidated.png`
- `docs/visual-signoff/patterns/reference-1536/06-1536-select-trading-range.png`
- `docs/visual-signoff/patterns/reference-1536/07-1536-4h-empty-state.png`
- `docs/visual-signoff/patterns/reference-1536/08-1536-breakouts-unchanged.png`
- `docs/visual-signoff/patterns/reference-1536/09-1672-double-bottom.png`
- `docs/visual-signoff/patterns/reference-1536/10-1440-double-bottom.png`
- `docs/visual-signoff/patterns/reference-1536/11-1920-double-bottom.png`
- `docs/visual-signoff/patterns/reference-1536/12-390-mobile-double-bottom.png`
- `docs/visual-signoff/patterns/reference-1536/13-1536-detailed.png`
- `docs/visual-signoff/patterns/reference-1536/REPORT.md`
- `docs/visual-signoff/patterns/reference-1536/audit-multi-pattern-demo.json`
- `docs/visual-signoff/patterns/reference-1536/audit-standard-demo.json`
- `scripts/browser-audit/patterns-check.mjs`
- `scripts/seed_demo_chart_patterns.py`
- `src/trading_assistant/web/static/js/chart.js`
- `src/trading_assistant/web/static/js/chart_functions.js`
- `src/trading_assistant/web/static/js/function_panel.js`
- `src/trading_assistant/web/static/js/pattern_view.js`
- `src/trading_assistant/web/static/js/views/dashboard.js`
- `src/trading_assistant/web/static/styles.css`
- `tests/frontend/chart.test.mjs`
- `tests/frontend/dashboard.test.mjs`
- `tests/frontend/pattern-view.test.mjs`
- `tests/test_seed_demo_chart_patterns.py`

### PR #66

- `docs/visual-signoff/support-resistance/README.md`
- `docs/visual-signoff/support-resistance/STATUS.md`
- `docs/visual-signoff/support-resistance/support-resistance-1440x900-stage.png`
- `docs/visual-signoff/support-resistance/support-resistance-1536x864-4h.png`
- `docs/visual-signoff/support-resistance/support-resistance-1536x864-full.png`
- `docs/visual-signoff/support-resistance/support-resistance-1536x864-selected.png`
- `docs/visual-signoff/support-resistance/support-resistance-1536x864-stage.png`
- `docs/visual-signoff/support-resistance/support-resistance-1920x1080-stage.png`
- `docs/visual-signoff/support-resistance/support-resistance-390-mobile-full.png`
- `docs/visual-signoff/support-resistance/support-resistance-390-mobile-stage.png`
- `docs/visual-signoff/support-resistance/support-resistance-report.json`
- `scripts/browser-audit/support-resistance-check.mjs`
- `scripts/seed_sr_demo.py`
- `scripts/sr_payload_probe.py`
- `src/trading_assistant/web/chart_evidence.py`
- `src/trading_assistant/web/dashboard_service.py`
- `src/trading_assistant/web/static/js/chart.js`
- `src/trading_assistant/web/static/js/chart_functions.js`
- `src/trading_assistant/web/static/js/explain.js`
- `src/trading_assistant/web/static/js/function_panel.js`
- `src/trading_assistant/web/static/js/views/dashboard.js`
- `src/trading_assistant/web/static/js/zone-position.js`
- `src/trading_assistant/web/static/styles.css`
- `src/trading_assistant/web/zone_evidence.py`
- `src/trading_assistant/web/zone_presentation.py`
- `tests/fixtures/decision_digests/dashboard_n500.json`
- `tests/frontend/chart-functions.test.mjs`
- `tests/frontend/dashboard.test.mjs`
- `tests/frontend/explain.test.mjs`
- `tests/frontend/function-panel.test.mjs`
- `tests/frontend/zone-bands.test.mjs`
- `tests/frontend/zone-position.test.mjs`
- `tests/test_zone_detector_audit.py`
- `tests/test_zone_evidence.py`
- `tests/test_zone_presentation.py`

### PR #67

- `src/trading_assistant/web/static/js/chart_functions.js`
- `src/trading_assistant/web/static/js/function_panel.js`
- `src/trading_assistant/web/static/styles.css`
- `tests/frontend/chart-functions.test.mjs`
- `tests/frontend/function-panel.test.mjs`

## Scope/dependencies

- #65: pattern presentation/model/rendering, synthetic demo generator and frontend/Python tests; no engine change.
- #66: S/R presentation, additive supported-timeframe and zone/event evidence fields; dashboard digest changes require decision-preservation validation. No detector/strategy changes in its diff.
- #67: swing trend presentation, optional chronological connector helper (default channel format preserved), shared fact-grid CSS and tests.
- Browser harness uses puppeteer-core separately from dependency-free Node tests. Real stored market data is not supplied in tracked data directories; do not access production data.
- Missing visual references and unimplemented pattern families remain owner/design questions, not permission to invent features.

## Conflict handling

Read-only `git merge-tree --write-tree ee6ab77 a5c9013` exits 1: one content conflict in dashboard.js. #65 filters legacy pattern shapes into `drawnModel` and paints annotations; #66 extracts the evidence-meta refresh so later overlay responses update the tally. These operations are compatible: retain the filtered drawnModel and annotation paint, followed by the extracted meta update and other existing rendering calls. No calculation/strategy/event/safety choice is required. This reproduces Agent 1 finding BRAIN-AUDIT-0001 (P2), audit commit fe0fa0dcc59027bb2d623c1eb8bbc4c169626467. Retain all independent assertions and behaviours.

## Additional confirmed-defect dependency: PR #53

Reviewed read-only liveness repair for BRAIN-AUDIT-0002; base main `9da1d3c`, head `cd0df0499a2d44dd8f0af397d2d9f05c3e17cca6`. Merged without conflicts or duplicate commits at `04b076db4d3fa3a73ea4848ea397454b3a57c58e`. No polling/execution/strategy change. Changed-file manifest:

- `.env.example`
- `README.md`
- `docs/runner-health.md`
- `src/trading_assistant/config.py`
- `src/trading_assistant/forward_testing/parameters.py`
- `src/trading_assistant/forward_testing/repository.py`
- `src/trading_assistant/forward_testing/runner_health.py`
- `src/trading_assistant/forward_testing/service.py`
- `src/trading_assistant/web/static/js/topbar.js`
- `tests/frontend/dashboard.test.mjs`
- `tests/frontend/runner-heartbeat-health.test.mjs`
- `tests/frontend/timeframeswitch.test.mjs`
- `tests/frontend/topbar.test.mjs`
- `tests/test_runner_heartbeat_health.py`
