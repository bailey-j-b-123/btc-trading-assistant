# Chart Patterns: exact design + multi-pattern presentation (frontend only)

Follow-up to draft PR #65 (`70402fe`). **Not approved until reviewed.**
Strategy, detector, threshold, signal, risk, execution, schema, API and event
contracts are unchanged. No detector was added: the types that are not
implemented are listed under "Engine proposal" below and need a separate,
approved engine task.

The approved layout is the reference image IMG_3400. It is **not stored in
the repository, the git history or PR attachments**, so the comparison below
uses the measurements taken from it in the previous review. It is not a pixel
overlay. To enable a true side-by-side, please commit the image to
`docs/reference/`.

## 1. Support matrix: what BRAIN genuinely detects

A name appearing in a registry or the UI does not count as support. Support
means a detector, lifecycle and tests exist.

| Pattern | Status | Detector | Detector tests | Shown on the Chart Patterns screen |
|---|---|---|---|---|
| Double Bottom | **DETECTED + TESTED** | `pattern_liquidity/classical_patterns.py` (`detect_shapes`, `advance_pattern`) | `tests/test_pattern_liquidity.py`: geometry, formation, confirmation, invalidation, near-miss rejection, span limit, no future leakage, deterministic ids | yes: 2 arcs, Bottom 1/2, neckline, support line |
| Double Top | **DETECTED + TESTED** | same | same | yes: 2 caps, Top 1/2, neckline, resistance line |
| Head and Shoulders | **DETECTED + TESTED** | same | same, plus 3 rejected near-H&S fixtures | yes: 3 caps, Left Shoulder/Head/Right Shoulder, neckline |
| Inverse Head and Shoulders | **DETECTED + TESTED** | same | same | yes: 3 troughs, labels, neckline, head as support |
| Rectangle / Trading Range | **PARTIAL** | `market_structure/ranges.py` (active range) | `tests/test_market_structure*.py`, `tests/test_chart_evidence.py` | yes, only while the range is active: faint box, dashed Range high/low. There is no formed/confirmed/invalidated lifecycle and no breakout linkage, so the badge is "Active" only and the target is "Unavailable". |
| Ascending Triangle | **NOT IMPLEMENTED** | none | none | never shown |
| Descending Triangle | **NOT IMPLEMENTED** | none | none | never shown |
| Symmetrical Triangle | **NOT IMPLEMENTED** | none | none | never shown |
| Bull Flag | **NOT IMPLEMENTED** | none | none | never shown |
| Bear Flag | **NOT IMPLEMENTED** | none | none | never shown |
| Rising Wedge | **NOT IMPLEMENTED** | none | none | never shown |
| Falling Wedge | **NOT IMPLEMENTED** | none | none | never shown |
| Pennant | **NOT IMPLEMENTED** | none | none | never shown |
| Rising Channel | **NOT IMPLEMENTED** | none | none | never shown |
| Falling Channel | **NOT IMPLEMENTED** | none | none | never shown |
| Cup and Handle | **NOT IMPLEMENTED** | none | none | never shown |

Other evidence BRAIN already detects (not classical chart patterns; shown by
their own functions and left unchanged): breakouts, failed breakouts, sweeps,
retests, equal highs/lows, support/resistance zones, swings, trend, and 7
candle shapes.

**Verified types on screen (real browser, real detector output): Double
Bottom, Double Top, Head and Shoulders, Inverse Head and Shoulders, Trading
Range (partial).** Each was selected and audited, with screenshots 01–07.

## 2. Default selection rule (deterministic, documented, tested)

`chartPatternRecords()` + `chooseChartPattern()` in `pattern_view.js`
(ordering in `patternDefaultRank()` in `chart_functions.js`):

1. Current records (`is_latest_state`) that are **not invalidated**, then
   current invalidated records, then earlier states.
2. Within each group, newest `known_at` first, then id descending.
3. The active trading range comes after every pattern record. It is the
   default only when no pattern record exists.
4. An explicit operator choice wins whenever that exact record id is in the
   current payload. A choice from another timeframe or window is absent, so
   it is never drawn (no stale patterns).

There is **no "strongest" ranking**. BRAIN computes no tested quality score,
so none is claimed and confidence reads "Not calculated". The browser audit
recomputes this default independently from `/api/market/annotations` and
asserts the drawn record matches (standard demo → Double Bottom (confirmed);
multi-pattern demo → Head and Shoulders (confirmed)).

## 3. Presentation layer (reusable)

- **One primary drawing** per chart, built only from that record's stored
  components: `patternAnnotation()` → `renderPatternAnnotation()`.
  - Double/H&S families get small arcs. An arc spans at most 8 bars per side
    (30% of the gap to the neighbouring point), so it never sweeps across the
    pattern, and it is never drawn past the stored extreme. Labels sit on the
    stored candle.
  - The dashed neckline is at the stored price and runs to the confirmation
    candle only when the record is confirmed.
  - The title box reads "Name - State", with a short connector (≤ 90px) that
    touches the neckline.
  - Tone: green for confirmed bullish, red for confirmed bearish, amber when
    formed, grey when invalidated, blue for the range.
- **Range:** a faint box (fill-opacity 0.06) with dashed high/low lines at
  the stored prices, and no arcs.
- **Future validated types:** an `outline` fallback draws a real record of an
  unknown type through its own stored points only (tested). Nothing is drawn
  without a record.
- **Secondary drawings** (Simple mode, at most 2) are grey dashed outlines
  with grey names. They show other *current* records whose time span does not
  overlap the primary. In both demos these records sit outside the default
  zoom, so the unit test is the evidence.
- **Cards** are driven by the same record:
  - Card 1: title-case name, family icon, lifecycle badge, "Not calculated"
    confidence, and a short explanation from stored facts.
  - Card 2: every other record from the same payload, each with its badge and
    a selection control (Simple mode: current records only). It scrolls
    inside its own card when there are more rows than fit.
  - Card 3: only stored levels per type. Targets are always "Unavailable".

| Type | Neckline row | Support row | Resistance row |
|---|---|---|---|
| Double Bottom | Neckline (Breakout) | Support (Bottoms) | Resistance (Target): Unavailable |
| Double Top | Neckline (Breakdown) | Support (Target): Unavailable | Resistance (Tops) |
| Head and Shoulders | Neckline (Breakdown) | Support (Target): Unavailable | Resistance (Head) |
| Inverse H&S | Neckline (Breakout) | Support (Head) | Resistance (Target): Unavailable |
| Trading Range | Breakout (Target): Unavailable | Support (Range Low) | Resistance (Range High) |

## 4. Visual comparison with IMG_3400 (1672×941)

| Measure | Reference | This build |
|---|---|---|
| Chart panel width | ≈ 71.3% | 71.2% |
| Right column width | ≈ 26.5% | 26.6% |
| Gutter | narrow | 15px |
| Card column bottom vs chart bottom | aligned | 936 / 936 (multi-pattern), 1440: 893 / 892 |
| Trough width / chart width | ≈ 130 / 1037px (12.5%) | 144 / 1109px (13.0%) |
| Trough depth | about half way to the neckline | 47–83px (bounded by the stored extreme) |
| Arc stroke | 1.5–2px green | 1.8px |
| Neckline | thin dashed green | 1.5px, dash 7/5 |
| Point labels | below the troughs | 15px, centred on the stored candle (±0.2px measured) |
| Title | green outline, above, short connector | 19px, green outline, connector 52px |
| Large shading | none | largest arc fill = 1.4% of the pane area |

## 5. Verification

Demo data are synthetic and identified as such:
- `scripts/seed_demo_dashboard.py` → `/tmp/brain-demo.sqlite3` (standard demo);
- new `scripts/seed_demo_chart_patterns.py` → `/tmp/brain-patterns.sqlite3`
  (candles only; every pattern is real detector output).

| Command | Result |
|---|---|
| `npm test` | **446 tests, 446 pass, 0 fail** (PR #65: 440) |
| `.venv/bin/python -m pytest -q` | **1185 passed, 1 xfailed, 1 warning in 233.26s** (re-run on the committed tree; an earlier run gave the same counts in 306.40s) (xfail preserved: `tests/test_decision_invariance.py::test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary`) |
| `ruff check --isolated --select E4,E7,E9,F src tests` | **Found 15 errors** (the same 15 pre-existing findings; none added) |
| `ruff check … scripts/seed_demo_chart_patterns.py tests/test_seed_demo_chart_patterns.py` | All checks passed |
| `node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 …` (standard demo; 1672×941, 1440×900, 1920×1080, 390) | exit 0, no unexpected console errors |
| `node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8124 …` (multi-pattern demo; same viewports) | exit 0, no unexpected console errors |
| `node scripts/browser-audit/breakouts-check.mjs` against this build (8123) **and** against PR #65's code (`70402fe` worktree, 8125) on the **same** DB | both exit 0; **all 20 screenshots byte-identical**, so Breakouts & Retests is unchanged |

What the browser audit asserts (see the `audit-*.json` files in this folder):
- **Default:** the drawn record equals the documented default, recomputed from the API.
- **Anchors:** every point label sits on a stored component candle at the stored price.
- **Neckline:** at the stored price (±0.6px); it ends on the confirmation candle.
- **Connector:** short, and it touches the neckline.
- **Drawing:** one arc per stored extreme; one title and one layer (no duplicates from redraws); no large fill.
- **Every Card 2 record** is selectable. On selection the chart draws that record, Card 1 names it, it leaves Card 2, and the zoom width is unchanged (122 bars every time).
- **Range:** the range box sits at the stored high/low, has no arcs and only a faint fill.
- **Function switch:** a manual choice and the zoom survive a function switch.
- **Timeframe:** 4H never shows the 1H drawing.
- **Mode switches:** Detailed mode shows the stored facts; leaving for Breakouts restores its layout and zoom.

Unit tests added or updated:
- `tests/frontend/pattern-view.test.mjs` (17), covering:
  - small arcs with real candle ends;
  - arcs never beyond the extreme;
  - overlay anchors and a short connector;
  - H&S and inverse H&S geometry and levels;
  - range box, levels and no percentage;
  - the default and operator choice;
  - secondary drawings (current only, non-overlapping);
  - unknown-type outline only from a real record.

  Three earlier tests that asserted the removed full-width curve were
  replaced by stricter versions of the same intent. None was deleted
  without a successor.
- `tests/test_seed_demo_chart_patterns.py` (3): the review series is
  deterministic and candles only. The unchanged detectors, reached through the
  dashboard's own path (last 500 candles → `analyze_patterns` →
  `build_chart_evidence`), find H&S and IH&S confirmed plus double top/bottom
  and an active range, and only the four implemented types. There is no
  confidence field, and nothing is known before the H&S confirmation.

## 6. Screenshots (this folder)

| File | Shows |
|---|---|
| `01-1672-double-bottom-default.png` | Standard demo. Default = confirmed Double Bottom (the reference subject). |
| `02-1672-head-and-shoulders-default.png` | Multi-pattern demo. Default = confirmed H&S. Card 2 lists 5 records (scrolls). |
| `03-1672-select-inverse-head-and-shoulders.png` | IH&S selected from Card 2 |
| `04-1672-select-double-top-confirmed.png` | Double Top (confirmed) selected |
| `05-1672-select-double-top-invalidated.png` | Invalidated Double Top: grey, no confirmation line |
| `06-1672-select-trading-range.png` | Active range: faint box, Range high/low |
| `07-1672-select-double-bottom-invalidated.png` | Invalidated Double Bottom (multi-pattern demo) |
| `08-1440-…`, `09-1920-…`, `10-390-mobile-…` | Other viewports |
| `11-1672-detailed.png` | Detailed mode: stored record facts and evidence overlays |
| `12-1672-4h-empty-states.png` | 4H with no stored candles: honest empty cards, no stale 1H drawing |
| `13-1672-breakouts-unchanged.png` | Breakouts after leaving Chart Patterns |

## 7. Limitations

- **No pixel side-by-side.** IMG_3400 is not available in the repo, so the
  comparison uses the earlier measurements.
- **1440×900:** Card 1's explanation wraps to more lines, so Card 2 shows its
  header and about 1½ rows and scrolls.
- **Trading range:** the detector only reports the *active* range. Past
  ranges, breakouts from a range and range targets are not available, so they
  are not shown.
- **Secondary drawings:** with the default zoom, the other current records in
  both demos lie outside the visible window, so no secondary outline appears
  in the screenshots. The behaviour is unit-tested.
- **Pre-existing from PR #65 and out of scope here:**
  - the "FORMING 1H" note shows on 4H;
  - a gridline crosses the empty-chart message;
  - the mobile header uses about 4 rows;
  - price-axis labels use 2 decimals (a shared chart setting, so Breakouts
    would change).

## 8. Engine proposal (needs approval; NOT started)

Adding any NOT IMPLEMENTED type requires engine changes that this
frontend-only task may not make:

1. **Strategy records change.** `setup_qualification/rules.py` (lines
   ~334–370) adds every current confirmed `chart_patterns` record to setup
   records as an optional `classical_pattern` rule. It treats any type other
   than `double_bottom`/`inverse_head_and_shoulders` as *bearish*, so a new
   bullish type (ascending triangle, bull flag, falling wedge, cup and handle)
   would be recorded with the wrong direction. A direction map per type is
   needed first, or new types must be kept out of `chart_patterns`.
2. **New detector and thresholds.** A `pattern_liquidity` detector over
   confirmed swings for each family. Proposed parameters (to be validated on
   fixtures, not tuned to look good):
   - minimum touches per trendline (≥ 2 per side);
   - trendline slope tolerance (flat side ≤ 0.1%/bar for ascending and
     descending triangles);
   - convergence required for triangles, wedges and pennants (apex within N
     candles);
   - parallelism tolerance for channels and flags;
   - flagpole minimum move (e.g. ≥ 3% within ≤ 15 candles) for flags and
     pennants;
   - cup depth and roundness plus handle retrace ≤ 50% for cup and handle;
   - the existing `pattern_max_span_candles` limit.
3. **Lifecycle.** Each family uses the same formed → confirmed (close beyond
   the boundary line at that candle's projected price) → invalidated states,
   with no use of future candles, as the existing `advance_pattern` does.
4. **Shared contract.**
   - Chart evidence currently carries horizontal necklines only. Sloped
     boundaries need an additive field such as `lines: [{kind, from: {time,
     price}, to: {time, price}}]`.
   - `PATTERN_LABELS`/`PATTERN_SIDE` need the new entries.
   - The DB `chart_patterns` records may need the same geometry.
5. **Tests per type.**
   - Positive fixtures and near-miss negatives.
   - Tests that every prefix is identical with future data.
   - Invalidation is not future knowledge.
   - Deterministic ids.
   - Decision-invariance runs.
6. **Frontend.** Once the API carries the lines, the presentation layer
   draws them through the existing `outline` path (or a dedicated style)
   without changing the selection, cards or default rule.

**Stop here: awaiting review and an explicit decision on the engine task.**
