# Dashboard presentation review — 14 October 2026

Scope: chart behaviour, evidence rendering, timeframe/refresh lifecycle, dashboard
status accuracy, and responsive layout. No detector, qualification, planning,
outcome, fingerprint, database schema, or stored-record changes.

## Faults found and repaired

| Fault | Repair |
| --- | --- |
| Pattern line series participated in the main price autoscale; distant guide prices could flatten candles. Marker stacks also changed scale margins. | Pattern lines explicitly share the right/candle scale but return no autoscale range. Confirmed candles supply their OHLC range, not marker-driven margins. Fixed pane margins reserve annotation/volume space. |
| Null/blank pattern prices could convert to zero. Dropping a missing component connected the remaining peaks across an absent trough. | Only finite positive pattern prices are renderable. Missing components break contiguous outline runs; they are never bridged. |
| Confirmed patterns left invalidation guides running to the latest candle; invalidated patterns left necklines running there. | Both guides end at the resolution candle. With a gap at that candle, the guide ends at the last loaded candle before it; without a terminal anchor no ongoing guide is invented. |
| Every pattern painted two long guides over the chart. Many patterns on one candle stacked markers vertically. | Overview shows recorded outlines. Inspecting one exact record adds only its neckline/invalidation guides. Coincident patterns share a counted marker; every record remains independently accessible in the evidence list. |
| A timeframe switch reused the old logical range and left old candles/zones/close text visible under the new heading during loading or failure. | Clear the old view immediately, unsubscribe history, invalidate pending reads, and initialise a recent-candle view for the new timeframe. Explicit Reset view restores recent bars and automatic price scale. |
| Non-engine structure responses lost their `zone_bands` between the API and renderer. | Forward the viewed timeframe's own display bands, with no engine-level substitution. |
| Zone overlays spanned the price/time axes, missed price-axis-only drags, and recreated label buttons on unchanged crosshair moves. | Clip to the plot dimensions. A lifecycle-managed, viewless v4 primitive synchronises price transforms. Unchanged redraws preserve button nodes; labels remain separated and inside the pane. |
| Reported zone counts ignored frontend selection caps; turning off trade planning left the setup band. | Counts distinguish selected/hidden bands, and setup shading follows the planning layer toggle. Higher-TF availability no longer claims every band is on-screen. |
| Refresh captured only the visible time range, losing fractional zoom and the blank right margin. Non-engine refresh discarded older loaded rows; old async history/HTF requests could survive a new engine snapshot. | Capture logical offsets with candle-time anchors; restore exact zoom/margins. Merge previously loaded rows on same-timeframe refresh and invalidate older requests by generation. Re-project evidence after history arrives. |
| Volume printed a number beside the price axis, easily mistaken for a price level. | Keep volume on its own scale without its own last-value/price-line label. |
| An empty paper ledger said “ALL SETTLED”; missing plan rows could be called “no trade recorded”. | Explicit NO PAPER TRADES only with a reported zero count. Missing counts/rows remain unknown/unavailable; existing recorded outcomes stay untouched. |
| A phone looked contained with disclosures closed but expanded diagnostics overflowed by 176px. | Bound nested grid columns and wrap long setup/seed IDs and technical reasons. Regression opens every dashboard disclosure at all tested widths. |

## Verification

- Baseline: `npm test` — **298 passed**; Python — **1182 passed, 1 xfailed**.
- After repairs: `npm test` — **312 passed, 0 failed**.
- `.venv/bin/python -m pytest` — **1182 passed, 1 xfailed, 1 warning**.
  The existing shallow-history expected failure is unchanged. The warning is
  the existing Starlette TestClient/httpx deprecation, not a new failure.
- `scripts/browser-audit/chart-check.mjs` — passed using Chromium and the real
  vendored Lightweight Charts library against a disposable synthetic SQLite
  fixture containing 5M, 15M, 1H and 4H candles. Checks cover all layer toggles,
  extreme guides/marker stacks, timeframe switches, keyboard pattern inspection,
  manual price-axis scaling, Reset view, zone clipping/focus stability, and
  stalled/failed timeframe reads.
- Stress case: a guide spanning synthetic prices 1 to 1,000,000 plus forty
  coincident markers left the candle's Y coordinate exactly **212.61333333333334px**
  before and after, with logical range **30–152** unchanged.
- Desktop **1440×1000**, tablet **834×1112**, phone **390×844**: zero horizontal
  page overflow, including with every dashboard disclosure expanded; chart
  widths **996 / 748 / 340px**, respectively; no browser page errors.
- Existing `audit.mjs`: fresh loads reached ready at each size, no page/console
  errors. One sandbox Chromium launch failed during a concurrent test run; an
  isolated rerun passed. The initial phone audit exposed the technical-detail
  overflow above; the expanded-disclosure regression verifies its repair.
- Existing `retry-check.mjs`: forced dashboard request failure displayed the
  failure state, Retry recovered, no page errors.

Browser instructions and required fixture characteristics are in
[`scripts/browser-audit/README.md`](../../scripts/browser-audit/README.md).
Screenshots, the disposable database, browser binaries and preview-only server
are ignored local artifacts, not repository data. The preview is labelled
**OFFLINE SYNTHETIC REVIEW** so its generated values cannot be mistaken for live BTC.

## Boundaries and remaining limits

- Original reference images were not attached to this checkout/session. This is
  a behavioural repair against actual stored candle/evidence coordinates, not a
  claim of pixel-matching those images.
- Live Binance connectivity and the user's production dataset were not tested;
  this sandbox has restricted network access. No production database was opened,
  and no exchange credentials were read. Live/forming-data separation is covered
  by the existing offline frontend/backend suites.
- Pattern labels still use candle-based density gating rather than font-measured
  collision detection. At unusually dense zoom levels some labels are hidden;
  the evidence list remains the complete accessible alternative for the model.
- Evidence availability remains limited to the backend's reported analysis
  window. Paging more candles does not invent older detections.
- This work does not fix the documented shallow-history decision defect, change
  detection rules to resemble pictures, add execution, rewrite records, or make
  any profitability claim. No automatic deployment or merge is introduced.
