# BRAIN-AUDIT-0001 — Chart Patterns and S/R do not integrate cleanly

- Severity: **P2** (integration blocker).
- Status: **CONFIRMED**, awaiting repair integration.
- Component: dashboard composition, `src/trading_assistant/web/static/js/views/dashboard.js`.
- Affected commits: #65 `ee6ab770b71957a7c30f5d90db119c187330bcc8` and #66 `a5c9013c491938dbbf8317af75da3f4793a5a3a9`; common base #62 `a0f1fafd7ed6841eaec14809c03b7556ff0949c4`.
- Scope: integration-only; neither individual branch alone has this merge conflict.

## Expected / actual

Expected: combined feature work must preserve both pattern selection/rendering and truthful zone accounting, with no conflict markers and passing combined tests.

Actual: Git's read-only merge-tree exits **1**, reporting a content conflict in `views/dashboard.js`. Individual PR CI cannot establish combined readiness. Pairwise #65/#67 and #66/#67 merge-tree runs exit 0; those are textual checks, not runtime verification.

## Reproduction

From a repository with the pinned commits fetched:

```sh
git merge-tree --write-tree ee6ab770b71957a7c30f5d90db119c187330bcc8 a5c9013c491938dbbf8317af75da3f4793a5a3a9
```

No checkout, application edits or merge commit are required. Evidence: `../evidence/merge-tree-65-66.txt` (full conflict output), with the two other pairwise outputs alongside it.

## Acceptance

1. Repairer publishes the combined application SHA and explicit conflict resolution.
2. Actual code inspection shows both features retained, not one entire version replacing the other.
3. Combined frontend suite, Python zone/evidence and decision regression tests pass.
4. Combined chart selection, timeframe switching and S/R accounting rechecked independently.
