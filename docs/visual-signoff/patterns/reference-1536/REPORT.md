# Chart Patterns at 1536×864: PICTURE 1 comparison and multi-pattern follow-up

Follow-up commit on draft PR #65. **Not approved until the screenshots are reviewed.**
This change touches only the frontend, the demo seed and tests. Strategy,
detectors, thresholds, signals, risk, market-data ingestion, paper trading,
DB schema, APIs and event contracts are unchanged. No detector was added.

## 0. Starting point and references

- The brief expected PR #65 at `70402fe`. Its actual head was **`600e015`**: the
  turn-3 multi-pattern commit, which is a fast-forward of `70402fe`. This
  work builds on `600e015`.
- **PICTURE 1 (IMG_3400)** and **PICTURE 2 (IMG_3405)** were visible in the
  review conversation but never reached the workspace as files. The comparison
  below was made by inspecting PICTURE 1 directly next to each 1536×864 Chromium
  screenshot. **No pixel composite was produced**, because none can be made
  honestly without the file. To get a true image side by side, commit IMG_3400
  (for example to `docs/visual-signoff/patterns/reference/IMG_3400.png`) and it
  can be paired with `01-1536-double-bottom-confirmed.png`.
- PICTURE 2 is used only as the coverage checklist for the support matrix in §5.

## 1. Side by side: PICTURE 1 vs `01-1536-double-bottom-confirmed.png`

Mismatches found at 1536×864 before this commit, and their state now:

| # | Item | PICTURE 1 | Before (600e015) | Now |
|---|---|---|---|---|
| 1 | Toolbar | one full-width navy row, ~80px | the row overflowed (content 1653px wide in 1536px), so the right-hand controls fell off-screen | one row, 0–80px, content width = 1536px (no overflow) |
| 2 | Card 1 title | "Pattern Detection" on one line | wrapped to two lines | one line |
| 3 | Chart head | OHLC on the left, nothing overlapping | the OHLC line collided with the forming-candle note | clear, with the note truncated with an ellipsis on the right |
| 4 | Price axis | sans-serif, ~15px, whole numbers ("71,000") | monospace 11px, two decimals ("84,800.00") | sans-serif 13px, "84,800" / "84,429.7" (no decimal is ever rounded away) |
| 5 | Volume | bars in the lower part of the pane, below the "Volume" label | bars reached up to the label | bars start below the label (pane top 86.5%) |
| 6 | Secondary patterns | grey dashed outlines of other genuine patterns on screen | the secondary slots went to off-screen records | the nearest records in time are drawn first. Multi demo: grey "Double Top" outline to the right of the selected Double Bottom (`02-…`) |
| 7 | Card proportions | Card 1 ≈ 41% of the column; Card 2 shows 2 rows | Card 1 395px; Card 2 showed 1 row | 350 / 165 / 194px (standard demo; multi demo 350 / 181 / 194). Card 2 shows 2 full rows |
| 8 | Annotation vs candles | the title box never covers candles | not checked | measured: 0 covered candles at every size and every selection (see §3) |

Measurements at 1536×864 (`audit-*.json`, layout check):

| | PICTURE 1 (estimated by eye, scaled to 1536) | 1536×864 now |
|---|---|---|
| Chart panel width | ~71–72% | 71.1% |
| Right column width | ~26–27% | 26.5% |
| Gap | narrow (~15px) | 15px |
| Cards top-aligned with chart panel | yes | yes (108px) |
| Neckline | one thin dashed green line from the pattern start to the breakout | the same: 1.5px dashed line at 84,229.90 that ends on the confirmation candle |
| Connector | short | 52px |
| Troughs | two small U arcs at the lows | two small U arcs on the stored lows: 132px wide in the standard demo, 66px in the multi demo (the arc spans ±30% of the gap between the lows, in bars) |
| Bottom labels | below the lows | below the lows, centred on the stored candle (±1.5px audited) |
| Shading | none large | arc wash only: ≤6% of the pane (audited) |

## 2. What changed

| File | Change |
|---|---|
| `web/static/styles.css` | A `1521–1700px` block that applies only on the Chart Patterns screen: a tighter single-row toolbar (same controls), a one-line card title, OHLC and note spacing, compact Card 1 and Card 2 spacing, and a 20px pattern name. At ≤600px the chart title is 13px so it fits beside the candles. |
| `web/static/js/chart.js` | `formatPatternPriceAxis` plus the pattern-only axis style (sans 13px, no trailing decimals), restored exactly when leaving Chart Patterns. The volume pane is lowered (0.82 → 0.865). `placeTitle` is now candle-aware: it reads the series' visible high/low extents and, if the preferred spot covers a candle, it tries the nearest clear spot, sliding sideways first and then slightly nearer to or further from the line. Display only: no stored time or price moves. |
| `web/static/js/pattern_view.js` | `secondaryAnnotations` orders candidates nearest in time to the selected record first, so the two secondary slots go to patterns on screen. Ties keep the documented order. |
| `scripts/seed_demo_chart_patterns.py` | New DEMO series for the multi-pattern demo: the newest standing record is a confirmed Double Bottom, with a genuine invalidated Double Top after it. Every record comes from the unchanged detectors; the seed labels nothing. |
| `scripts/browser-audit/patterns-check.mjs` | Adds the 1536×864 viewport (full selection pass), a "title covers no candle" check for every selection, and prefers the confirmed Double Bottom as the measured record. |
| `tests/frontend/chart.test.mjs` | +2 tests: the axis formatter, and the axis style applying only while Chart Patterns is open and being restored exactly. |
| `tests/frontend/pattern-view.test.mjs` | +1 test: the nearest-first secondary order. |
| `tests/test_seed_demo_chart_patterns.py` | Stricter checks: a confirmed Double Bottom inside the newest 122 candles, and a later invalidated Double Top as the newest standing record after it. |

## 3. Multi-pattern behaviour verified in Chromium (multi demo, 1536×864 and 1672×941)

The default is the newest standing record, as documented in the turn-3
REPORT §2. Here that is the **confirmed Double Bottom**, with the invalidated
Double Top drawn as a grey secondary. Every Card 2 row was clicked; for each
selection the audit asserted:

- the chart draws that record;
- Card 1 names it;
- it leaves Card 2;
- one title and one annotation layer;
- the labels sit on stored component candles at the stored prices;
- the neckline is at the stored price;
- one arc per stored extreme;
- shading ≤6%;
- the title covers no candle;
- the zoom is unchanged.

| Selection | Title covers candles | Secondary drawn |
|---|---|---|
| Double Bottom, confirmed (default) | 0 | Double Top (invalidated) |
| Head and Shoulders, confirmed | 0 | none (no non-overlapping record nearby) |
| Inverse Head and Shoulders, confirmed | 0 | none |
| Double Top, confirmed | 0 | none |
| Double Top, invalidated (newest) | 0 | Double Bottom |
| Double Top, invalidated (earlier) | 0 | none |
| Double Bottom, invalidated | 0 | none |

Standard demo: Double Bottom confirmed (default), Double Top invalidated and
Trading Range active were all selectable, with 0 covered candles.

Also asserted:
- a manual choice and the zoom survive a function switch;
- a resize redraws at the same candles;
- 4H clears the 1H drawing and shows honest empty cards;
- Detailed mode shows the stored record facts;
- leaving restores Breakouts with no pattern layer or volume label.

## 4. Commands and exact results

| Command | Result |
|---|---|
| `npm test` | **449/449 pass** |
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **1185 passed, 1 xfailed, 1 warning in 230.18s** (the xfail in `tests/test_decision_invariance.py` is preserved) |
| `ruff check --isolated --select E4,E7,E9,F src tests` | **15 errors**, all pre-existing; none added |
| `ruff check --isolated --select E4,E7,E9,F scripts/seed_demo_chart_patterns.py tests/test_seed_demo_chart_patterns.py` | All checks passed |
| `node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 …` (standard demo) | exit 0 at 1536×864, 1672×941, 1440×900, 1920×1080, 390×844 |
| `node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8124 …` (multi demo) | exit 0 at the same five sizes |
| `breakouts-check.mjs`: this branch vs PR #65 `600e015` (git worktree, same DB) | **20/20 screenshots byte-identical** |
| Console | 0 page errors and 0 unexpected console errors at every size. Only the known sandbox errors remain: the Binance WebSocket cannot connect, and the vendored chart library's inline style is refused by the CSP. |

Demo DBs: `/tmp/brain-demo.sqlite3` (`scripts/seed_demo_dashboard.py`) and
`/tmp/brain-patterns.sqlite3` (`scripts/seed_demo_chart_patterns.py`), both
DEMO data. They are disposable and not committed.

## 5. Support matrix

This is unchanged from the turn-3 REPORT §1
(`../multi-pattern/REPORT.md`), which has the detector modules and test files.

| Pattern (PICTURE 2 + requested extras) | Detector | Formed / Confirmed / Invalidated | Positive / negative tests | Drawn | Selectable | Class |
|---|---|---|---|---|---|---|
| Double Top | YES `pattern_liquidity/classical_patterns.py` | yes / yes / yes | yes / yes | yes | yes | **FULLY IMPLEMENTED** |
| Double Bottom | YES (same) | yes / yes / yes | yes / yes | yes | yes | **FULLY IMPLEMENTED** |
| Head and Shoulders | YES (same) | yes / yes / yes | yes / yes | yes | yes | **FULLY IMPLEMENTED** |
| Inverse Head and Shoulders | YES (same) | yes / yes / yes | yes / yes | yes | yes | **FULLY IMPLEMENTED** |
| Rectangle / Trading Range | YES `market_structure/ranges.py` (active range only) | "Active" only; no lifecycle | yes / yes | yes (box) | yes | **PARTIAL**: no formed/confirmed/invalidated lifecycle and no breakout linkage |
| Ascending, Descending, Symmetrical Triangle | NO | – | – | never shown | – | **NOT IMPLEMENTED** |
| Bull Flag, Bear Flag | NO | – | – | never shown | – | **NOT IMPLEMENTED** |
| Rising Wedge, Falling Wedge | NO | – | – | never shown | – | **NOT IMPLEMENTED** |
| Pennant | NO | – | – | never shown | – | **NOT IMPLEMENTED** |
| Rising Channel, Falling Channel | NO | – | – | never shown | – | **NOT IMPLEMENTED** |
| Cup and Handle | NO | – | – | never shown | – | **NOT IMPLEMENTED** |

Confidence shows "Not calculated" with an empty bar everywhere, because no
validated confidence exists. Targets are "Unavailable" unless the record
stores them; none does. There is no "strongest pattern" ranking.

**Missing-detector plan:** see the turn-3 REPORT §8 (engine proposal). It is
**not started** and needs separate approval, because new detectors would feed
the evidence used by strategy.

## 6. Screenshots (this folder)

| File | Shows |
|---|---|
| `01-1536-double-bottom-confirmed.png` | **Exact-comparison shot.** Standard demo, confirmed Double Bottom (default). |
| `02-1536-default-selection-with-secondary.png` | Multi demo default: confirmed Double Bottom plus the grey invalidated Double Top secondary |
| `03-1536-select-head-and-shoulders.png` | Second supported pattern selected from Card 2 |
| `04-1536-select-inverse-head-and-shoulders.png` | IH&S selected |
| `05-1536-select-double-top-invalidated.png` | Invalidated Double Top: grey; title kept clear of the candles |
| `06-1536-select-trading-range.png` | Active range (partial support) |
| `07-1536-4h-empty-state.png` | Empty state: 4H has no stored candles, honest empty cards, no stale 1H drawing |
| `08-1536-breakouts-unchanged.png` | Breakouts & Retests after leaving Chart Patterns |
| `09-1672-…`, `10-1440-…`, `11-1920-…`, `12-390-mobile-…` | Other viewports |
| `13-1536-detailed.png` | Detailed mode |
| `audit-standard-demo.json`, `audit-multi-pattern-demo.json` | Full audit output with every measurement |

## 7. Remaining differences from PICTURE 1

1. **No pixel side-by-side**, because IMG_3400 is not in the workspace (§0).
2. **Pattern content differs by design.** PICTURE 1 also shows a Bull Flag
   and a Descending Triangle. BRAIN has no detector for either, so they are
   never drawn or listed; the secondary slot shows only genuine records.
3. **Confidence:** PICTURE 1 shows a filled bar with a percentage. BRAIN
   shows "Not calculated" with an empty bar until a validated algorithm
   exists.
4. **Time axis:** PICTURE 1 shows day numbers only. BRAIN keeps intraday
   "12:00" ticks between days, because 5M/15M/1H need them.
5. **Axis font:** 13px vs about 15px in the picture. It is kept at 13px so
   five-digit prices fit the axis width at 1536.
6. **Long names:** "Inverse Head and Shoulders" wraps to two lines in Card 1.
   With its 5-line explanation, Card 2 then shows about 1½ rows and scrolls
   (`04-…`). The explanation is not truncated.
7. **Trough width** follows the record's own spacing (±30% of the gap
   between the lows, in bars). With the multi demo's closer lows the arcs
   are narrower (66px) than in the picture.
8. The volume pane's right-hand axis strip is masked, as approved in PR #65.
