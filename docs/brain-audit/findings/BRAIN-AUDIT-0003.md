# BRAIN-AUDIT-0003 — Viewed-timeframe trend falls back to an unrelated engine trend

- Severity: **P2** (incorrect chart interface / timeframe attribution).
- Status: **PARTIALLY REPAIRED in #67; STILL FAILING when viewed evidence is unavailable**.
- Component: `web/static/js/function_panel.js::swingsModel`, called by `web/static/js/views/dashboard.js` with viewed evidence and engine market state.
- Affected commits: baseline #62 `a0f1fafd7ed6841eaec14809c03b7556ff0949c4`; residual present in #67 `e2237723a8d9437504fdb54fa491ca1759e0215b`.
- Scope: individual branches, not integration-only. No backend trading-decision change demonstrated.

## Expected / actual

The Market Structure / Current bias panel on a 15m chart must use 15m evidence. If that evidence is unavailable, show UNKNOWN/unavailable; an engine 1h trend may only appear with explicit independent timeframe labeling, not as the chart's bias.

Production function results with 1h bullish engine state:

| Viewed 15m evidence | #62 result | #67 result | Expected |
|---|---|---|---|
| Available, bearish | Bullish | Bearish | Bearish |
| Unavailable, no trend | Bullish | Bullish | UNKNOWN/unavailable |

#67's new preference fixes the populated case, independently verified. Its unconditional fallback still presents wrong-timeframe data when there are no stored candles or trend evidence for the viewed frame. The existing added fallback test actually codifies the unsafe behavior without checking timeframe identity.

## Reproduction / evidence

```sh
node docs/brain-audit/probes/swing-timeframe.mjs
node docs/brain-audit/probes/swing-timeframe.mjs /path/to/exported/pr67
```

This uses synthetic contract inputs and the real production function. Full outputs: `../evidence/swing-timeframe-baseline.txt` and `swing-timeframe-pr67.txt`. Independent full #67 Node suite: 429 passed, demonstrating why ordinary CI alone misses the residual.

## Acceptance

- Available viewed trend wins over conflicting engine trend.
- Missing/unavailable/malformed viewed trend cannot silently borrow a different-timeframe trend.
- Add caller-level timeframe-switch failure/empty-data regression, including 15m/4h versus 1h engine.
- Same-timeframe fallback, if retained, must prove identity and validity explicitly.
