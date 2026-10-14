# Chart Patterns — correction against the approved reference

Branch `arena/c1b5cd5c-btc-trading-assistant`, based on `a0f1faf` (head of PR #62,
`arena/3e360305-btc-trading-assistant`). **Frontend presentation only.** No
detector, threshold, market-data, risk, trade-plan, paper-trading, schema, API,
event-contract or production-config change. Not approved until the user has
reviewed the screenshots.

**Specification:** `BRAIN_Approved_Chart_Patterns.png` (1672 × 941). It was supplied
inline in the chat and **is not in the repository or workspace**. The comparison
below therefore uses measurements taken by eye from that image, not a pixel
overlay. Committing the file to `docs/reference/` would allow an automated
overlay next time.

**Data:** isolated synthetic demo DB from `scripts/seed_demo_dashboard.py`
(`sqlite:////tmp/brain-demo.sqlite3`). These are demonstration candles, not
market history or performance. Every pattern drawn is a record the real detectors
stored for that series.

## Screenshots (this folder)

| File | What it shows |
|---|---|
| `comparison-before-after-1672x941.png` | Before (PR #62 head) on the left, after on the right, both at 1672 × 941 |
| `before-patterns-1672x941.png` | Chart Patterns before this correction |
| `patterns-1672x941-double-bottom.png` | **The reference-equivalent shot:** Double Bottom selected in Card 2 |
| `patterns-1672x941-default.png` | First open: the newest record (Double Top, invalidated) is selected |
| `patterns-1672x941-detailed.png` | Detailed mode: evidence markers and stored record facts kept |
| `patterns-1672x941-4h.png` | 4H with no stored candles: truthful empty state, no stale 1H drawing |
| `patterns-1672x941-full.png` | Full page |
| `patterns-1440x900-{default,double-bottom}.png` | 1440 × 900 |
| `patterns-1920x1080-{default,double-bottom}.png` | 1920 × 1080 |
| `patterns-390-mobile-{default,double-bottom,double-bottom-full}.png` | 390 px mobile |
| `patterns-*-breakouts.png`, `before-breakouts-1672x941.png` | Breakouts & Retests after leaving Patterns. At 1672 the after-shot is **byte-identical** to the before-shot (MD5 `133b11d55d85580bc78ca7d2314a9319` for both) |
| `patterns-report.json` | Raw measurements from `scripts/browser-audit/patterns-check.mjs` |

To reproduce: `CHROME_PATH=… node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 <outdir>`
(exits non-zero on any failed assertion).

## Reference vs build at 1672 × 941

| Measure | Reference (approx., by eye) | Build (measured) |
|---|---|---|
| Header | one slim navy bar, ~88 px | one row, 80 px, `#0c1322`-family navy |
| Timeframe | 1H filled blue | 1H filled `rgb(11,79,196)` |
| Chart panel width | ~71.3 % | 71.2 % |
| Gutter | ~15 px | 15 px |
| Right column | ~26.5 % | 26.6 % |
| Top alignment | cards level with the chart top | card top 7 px below the chart-panel top (the panel's own padding) |
| Volume | histogram along the bottom | own pane, label "Volume 1.01K" |

## Pass/fail, A–P

| # | Check | Result | Evidence |
|---|---|---|---|
| A | Header | **PASS** | Slim navy bar with BTC/USDT identity on the left, then the timeframe selector with 1H highlighted. One row at 1672 and 1920; two rows at 1440 (≤1520 px), with no overlap. Only real existing controls appear (function switch, Simple/Detailed, paper-mode, live-price and runner status, refresh, More). |
| B | Proportions | **PASS** | 1672: 71.2 % / 15 px / 26.6 %. 1440: 71.0 % / 26.5 %. 1920: 71.5 % / 26.6 %. |
| C | Three cards | **PASS** | Pattern Detection, Other Detected Patterns and Key Levels, in that order, each with an icon header. |
| D | Chart and candles | **PASS** | Green bullish and red bearish candles, subtle grid, readable price and time axes, OHLC on the top edge. No explanatory paragraphs on the chart. |
| E | Trough anchors | **PASS** | Label centre vs stored-candle x: Bottom 1 at 165.8 vs 165.7 (2026-09-23T20:00Z, 82,349.7); Bottom 2 at 823.4 vs 823.2 (2026-09-26T21:00Z, 82,461.0). Still on their candles after a resize (39.8/39.7 and 697.4/697.2). |
| F | Neckline price | **PASS** | Stored 84229.90000000. Drawn y 114 equals the price-scale y 114 (1440: 102 = 102; 1920: 134.7 = 134.7; 390: 69 = 69). |
| G | Double Bottom label | **PASS** | "Double Bottom - Confirmed" appears only because the latest stored state is `confirmed`. Green outline, 19 px; the connector ends exactly at the confirmation candle (2026-09-28T07:00Z). An invalidated or formed record never gets this label. |
| H | Lifecycle badge | **PASS** | The badge shows the backend's own state: Confirmed for the Double Bottom, Invalidated for the Double Top. |
| I | Confidence placeholder | **PASS** | "Not calculated" with an empty neutral bar. No number, no percentage and no fill (unit-tested). |
| J | Other-patterns selection | **PASS** | Only stored records are listed (Double Top, Invalidated). Selecting one redraws the chart and updates all three cards. On a timeframe with no records it reads "No other patterns recorded". |
| K | Key levels | **PASS** | Neckline (Breakout) 84,229.90, Support (Bottoms) 82,349.70, Resistance (Target) **Unavailable**. No target is derived because the backend does not store one. |
| L | Volume | **PASS** | Bottom pane with its own label; the price-axis strip beside it is masked so no stray scale shows. |
| M | No clipped labels | **PASS** | The audit's clipped-label and overflowing lists are both empty at all four viewports. |
| N | Zoom preservation | **PASS, with one note** | The candle count is kept everywhere: 122 bars at 1672 through opening Patterns, selecting a record and returning to Breakouts. The window **pans, never zooms**, when the selected pattern is not wholly on screen. See deviation 6. |
| O | Mobile 390 px | **PASS** | Chart and cards stacked, no horizontal overflow (scrollWidth 390). The pattern is wider than the 62-bar mobile window, so Bottom 2 is shown and "Fit to chart" brings in the whole drawing. |
| P | No console errors | **PASS** | 0 unexpected console errors, 0 page errors. The only messages are expected sandbox ones: the Binance WebSocket cannot connect from the sandbox, plus the pre-existing CSP "inline style" notice, which is identical on the base commit. |

Required behaviours, also checked in the browser:
- A timeframe change clears old annotations: 4H has no drawing, an empty detection card and no stale OHLC line (bug found and fixed in this pass).
- A resize redraws the annotation at the right candles.
- Simple mode shows one pattern; Detailed mode keeps the evidence markers and the stored-record facts.
- Breakouts & Retests is unchanged (byte-identical screenshot at 1672).

## Stored pattern records drawn (demo DB, 1H)

| Type | Record id | pattern_id | State | Latest | known_at | anchor | Neckline | Invalidation | Components (kind, time UTC, price) |
|---|---|---|---|---|---|---|---|---|---|
| double_bottom | `047223525f08518d6f6a31115be1076681a11f52195c25ed865bf5f3bbc26bd8` | `187318724a00…ad36d` | **confirmed** | yes | 2026-09-28T08:00Z | 2026-09-28T07:00Z | 84229.90 | 82349.7 | low 09-23 20:00 82349.7; high 09-25 14:00 84229.9; low 09-26 21:00 82461.0 |
| double_bottom | `f2d1891286b6c89c5f639d5567827f465d69267087e3007af4b755f377011e4d` | `187318724a00…ad36d` | formed | no | 2026-09-27T00:00Z | 2026-09-26T21:00Z | 84229.90 | 82349.7 | same three components |
| double_top | `67aefd670e3d0cd5d3b5af2d314b1bdb7b4d59a8319789005733d388e044cc25` | `e5dea7f53ab4…cea8` | **invalidated** | yes | 2026-09-28T18:00Z | 2026-09-28T17:00Z | 82461.00 | 84484.4 | high 09-25 14:00 84229.9; low 09-26 21:00 82461.0; high 09-28 10:00 84484.4 |
| double_top | `ba8d5f819080bf80d63993e4ae7f2cead84a1bb6894627f1126bd410c861f513` | `e5dea7f53ab4…cea8` | formed | no | 2026-09-28T13:00Z | 2026-09-28T10:00Z | 82461.00 | 84484.4 | same three components |

Stored geometry for the Double Bottom: the matching lows differ by 0.14 %, depth 2.10 %, span 73 candles. **No record carries a confidence field.**

## Deviations caused by missing backend data or absent controls

1. **Confidence:** the reference shows 87 %. The backend stores no confidence, so the build reads "Not calculated" with an empty bar.
2. **Bull Flag and Descending Triangle:** not detected or stored, so they are not shown. Card 2 lists only the stored Double Top.
3. **Resistance (Target):** no target is stored, so the row reads Unavailable. Nothing is derived from the neckline or the depth.
4. **Indicators, Layout, undo/redo, search and Trade buttons:** these are not real controls in this product, so they are not added (live trading is out of scope).
5. **Default selection:** first open selects the newest stored record, which is the invalidated Double Top (`patterns-1672x941-default.png`). The reference-equivalent view needs one click on Double Bottom in Card 2. Defaulting to the Double Bottom would mean ranking records by something other than recency; that was not done without your say-so.
6. **Recentring rule:** if the selected pattern is not wholly visible when Patterns opens or a record is selected, the window pans to centre it with the same number of candles. It never changes the zoom. "Window preserved" therefore holds strictly only when the pattern is already fully in view. Breakouts uses a looser rule (it moves only when nothing is on screen). Switching Patterns to that rule is a one-line change in `ensurePatternInView` if you prefer it.
7. **Pixel overlay:** not possible because the reference PNG is not on disk; the comparison is visual and measured.
8. **Responsive layout at ≤1520 px:** the header wraps to two rows and the "FORMING 1H" status line sits below the chart, so the cards keep their width. At 390 px the runner-health pill is hidden because the Runner health card already shows it.

## Remaining defects and notes (not fixed here)

- The "FORMING 1H — display only" status still appears while 4H is selected. This is pre-existing status-line wording from the shared status component, outside this function.
- The empty-chart heading on a timeframe with no candles is crossed by a gridline. This is the pre-existing `chartEmpty` overlay.
- The mobile header is about four rows tall (the identity, timeframe, function and mode groups wrap).
- The CSP "Refused to apply inline style" console message is pre-existing (it appears on `a0f1faf` too).

## Tests

New focused regression tests (15). In `tests/frontend/pattern-view.test.mjs`:
- the drawn bottoms, neckline and confirmation are the stored record's own times and prices;
- a component on a candle that is not loaded draws nothing, never a neighbouring candle;
- the only non-component point is a real loaded candle, capped at the neckline and never labelled;
- the smooth curve never dips below a stored bottom or rises above the stored peak;
- the chart overlay puts each label over its stored candle and the neckline at the stored price;
- the lifecycle word and colour are the backend's own state;
- the Pattern Detection card carries the stored state for every record;
- confidence is never calculated: no number, no percentage, no fill;
- Other Detected Patterns lists only stored records, never a Bull Flag or Descending Triangle;
- Key Levels show the stored neckline and bottoms; the target is always unavailable;
- the window moves only when the pattern is not wholly on screen.

In `tests/frontend/dashboard.test.mjs`:
- Chart Patterns cards show stored records only, with the confidence not calculated;
- selecting another detected pattern updates the cards and keeps the operator's zoom;
- Breakouts & Retests is unaffected by the Chart Patterns screen;
- a timeframe with no stored candles empties the Chart Patterns cards and OHLC line (fails without the fix).

Exact output:

```
$ npm test
# tests 440
# suites 0
# pass 440
# fail 0
# cancelled 0
# skipped 0
# todo 0
```
425 tests before this branch, so no existing test was removed or weakened.

```
$ python -m pytest -q
1182 passed, 1 xfailed, 1 warning in 269.87s (0:04:29)
XFAIL tests/test_decision_invariance.py::test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary  (preserved)

$ ruff check --isolated --select E4,E7,E9,F src tests
Found 15 errors.      # all pre-existing: the same 15 on a0f1faf; no Python file changed

$ node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 <out>    # exit 0, 4 viewports
$ node scripts/browser-audit/breakouts-check.mjs http://127.0.0.1:8123 <out>   # exit 0
```

## Files changed

| File | Change |
|---|---|
| `src/trading_assistant/web/static/js/pattern_view.js` | **new**: a pure model mapping a stored record to its annotation (candle index by time, curves, labels, neckline), the card model and key levels |
| `src/trading_assistant/web/static/js/chart.js` | the pattern annotation layer (curves, Bottom labels, dashed neckline, status label and connector), the volume-pane label and axis mask, and holding the window through layout resizes |
| `src/trading_assistant/web/static/js/function_panel.js` | the three Chart Patterns cards and the record selector |
| `src/trading_assistant/web/static/js/views/dashboard.js` | slim header controls for Patterns, pattern selection, keeping the pattern in view without zooming, Fit to chart, and clearing stale screens on timeframe changes |
| `src/trading_assistant/web/static/styles.css` | the Patterns composition, scoped to `body:has(.terminal-chart-card[data-chart-function="patterns"])`, so other functions are untouched |
| `tests/frontend/pattern-view.test.mjs` | **new**: 11 tests |
| `tests/frontend/dashboard.test.mjs` | 4 new tests |
| `scripts/browser-audit/patterns-check.mjs` | **new**: real-browser regression and screenshot script |
| `docs/visual-signoff/patterns/*`, `docs/dashboard-visual-signoff.md` | this report, the screenshots and the measurements |
