# BRAIN-AUDIT-0002 — Dead runner can still show green system health

- Severity: **P2** (operational reliability / misleading paper-runner liveness).
- Status: **CONFIRMED** on audit baseline; existing candidate repair PR #53 is not integrated or independently accepted here.
- Component: `src/trading_assistant/web/static/js/topbar.js:127–183`, `forward_testing/service.py:306–442`.
- Affected commit: #62 `a0f1fafd7ed6841eaec14809c03b7556ff0949c4`; topbar is untouched by #65–#67.
- Scope: exists individually; not introduced by feature integration.

## Expected / actual

With current candles, complete stored window, zero pending boundaries and an old IDLE heartbeat, market-data currency must not imply the runner is alive. A stale or unverifiable runner must not be green/healthy. Liveness policy should be owned by the backend with a documented/configured threshold, not an invented browser constant.

Actual: the production `systemHealthViewModel` ignores heartbeat age and `recorded_at`. A **604800-second-old** heartbeat returns `healthy: true`, `tone: green`, `DATA CURRENT · RUNNER IDLE`. Missing and future heartbeat contract inputs also return green. Those two malformed-input cases are defensive contract probes, not a claim that the database ordinarily produces null recorded_at. Backend status reports a raw heartbeat age but no authoritative liveness verdict.

## Reproduction / evidence

```sh
node docs/brain-audit/probes/runner-health.mjs
# optional final argument: root of an exported repair snapshot
```

No server, exchange or database access. Synthetic API-shaped inputs exercise the actual production function. Full outputs: `../evidence/runner-health-baseline.txt`. Recent control and all three adverse cases returned healthy. Independent current ingestion plus a stopped forward process can leave the historical IDLE record unchanged; the next boundary's backlog is not an adequate liveness check between boundaries.

## Acceptance

- A server-computed liveness verdict distinguishes recent, stale, missing and future heartbeat evidence, with explicit policy and reasons.
- Every UI health surface consumes that verdict and cannot fall back to green on unknown liveness.
- Tests reproduce stale IDLE with current data/zero backlog, missing/future timestamps, and recent healthy control.
- No changes to trading rules, ledger identities or stored history.
