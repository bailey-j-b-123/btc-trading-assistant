# BRAIN AUDIT FEED — VERIFIED FINDINGS

Independent auditor, Agent 1. Started 2026-10-14 UTC.

## Publication and safety

Audit branch: `arena/d3f9aa87-btc-trading-assistant` (session-fixed).
Base: `arena/3e360305-btc-trading-assistant`, common base of PRs #65–#67 and head of PR #62. Imported the existing base through a merge after the initial documentation commit; no application edits by this auditor. The initial generic fetch did not fetch this remote branch, so the attempted fast-forward failed. An explicit branch fetch and merge succeeded before baseline tests. The PR diff against its base contains audit files only.

Scope: read-only implementation review, isolated offline tests, reproducible audit scripts and documentation only. No orders, deployment, production database changes, risk-limit changes, or merges into main. Findings will be individually committed and pushed. Passing CI is not acceptance.

## Progress

- Pinned state/environment and branch overlap: [STATE.md](STATE.md).
- Baseline: **1182 Python passed, 1 documented xfailed**, 1 dependency warning; **425 Node passed**; **15 pre-existing Ruff findings**. No application edits.
- Individual feature heads: #65 **449 Node + 3 targeted Python**; #66 **436 Node + 74 targeted Python**; #67 **429 Node + 78 targeted Python**; all passed.
- Published findings: [0001](findings/BRAIN-AUDIT-0001.md) integration conflict; [0002](findings/BRAIN-AUDIT-0002.md) stale runner false green; [0003](findings/BRAIN-AUDIT-0003.md) wrong-timeframe fallback; [0004](findings/BRAIN-AUDIT-0004.md) retest lifecycle/time attribution.
- Offline real-browser baseline: all five chart functions exercised at 1536×864, 1440×900, 1920×1080 and 390×844; no horizontal page overflow, no page errors in corrected-locale run. Accessibility contrast findings under review. Data is explicitly synthetic.
- Repair PR #69 `arena/0090b00d-btc-trading-assistant`: fetched **f58f22d3a14e6809f5a01e778488d16adf134cc7**; 0001 marked READY FOR AUDITOR RECHECK. Combined independent recheck next.
- Known shallow-history failure reproduced without changing its marker; permanent finding/report entry next.
- Approved visual references: unavailable to this auditor; no design-match acceptance claimed.

## Browser harness caveats

Browser: HeadlessChrome/153.0.8010.0 (external npm tooling only). Disposable `.cache/brain-audit/synthetic.sqlite3`, 600 seeded 1h candles, no genuine market history. Audit-only adapter forbids writes, disables exchange quote/WebSocket paths and permits sandbox framing; application source/security configuration is untouched. Fixed audit clock 2026-10-14 08:00 UTC.

Initial harness launch needed bundled NSS/NSPR libraries. An initial browser run inherited `en-US@posix`, triggering chart locale errors; rerunning with explicit en_US.UTF-8/--lang=en-US removed them. Initial invalid-locale results are preserved as **harness/environment evidence, not an accepted application failure**. A first selector wait also targeted a non-button data attribute; corrected to `button[data-function]`. Neither tooling correction changed the application.

Latest commands and full outputs are under `evidence/`; reproducible audit-only scripts under `probes/`. Continue from the pinned commits, not from a new PR or branch.
