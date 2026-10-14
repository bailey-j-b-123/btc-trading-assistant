# Dashboard V2 — visual sign-off pack (correction pass)

Status: **awaiting human visual sign-off.** Automated tests passing is not
visual acceptance; the screenshots listed below are the artefact to judge.

## References vs this build

The six approved references (supplied 13 Oct) share one grammar: slim header,
one dominant chart card, a compact status rail, quiet navy panels with hairline
borders, coloured icon tiles, plain-language rows, almost no prose under the
candles. This pass restyled the V2 structure to that grammar, presentation
only — no engine, strategy, threshold or data change.

| Approved reference | This build (screenshot) | Notes |
| --- | --- | --- |
| Main dashboard | `docs/visual-signoff/01-main-dashboard.png` (Plan function, Simple) | Chart dominant; Current Setup = decision + 4 approved rows; hierarchy strip, plan card and waiting-for detail moved to Diagnostics |
| Support & Resistance | `docs/visual-signoff/02-support-resistance.png` | Stored bands only, nearest first; legend + records panel beside chart |
| Swing Highs / Lows | `docs/visual-signoff/03-swings.png` | HH/HL/LH/LL from stored swings only; Market Structure panel |
| Chart Patterns | `docs/visual-signoff/04-patterns.png` | Confirmed Double bottom selected; stored geometry, **no confidence %** (backend stores none — reference figure is illustrative) |
| Breakouts / Retests | `docs/visual-signoff/05-breakouts.png` | Stored breakout/retest records only; no invented continuation step |
| Trade Plan | `docs/visual-signoff/06-trade-plan.png` | Honest no-plan state on this demo series (engine built no PLANNABLE plan); entry/stop/target lines + R:R render when a plan exists (unit-tested) |

Also captured: `docs/visual-signoff/07-detailed-mode.png` (Detailed restores the
evidence catalogue, marker key and window notes that Simple hides) and
`docs/visual-signoff/08-mobile-390.png`. All eight are committed in this PR under
`docs/visual-signoff/` for direct comparison against the six approved designs.

## Demo environment

`scripts/seed_demo_dashboard.py` seeds an isolated disposable SQLite DB with a
deterministic synthetic 600-candle 1H series (double bottom → resistance tests
→ breakout → retest → continuation). It is **demonstration data, not market
history and not performance**; it refuses to write into a DB that already has
BTC/USDT candles. On this series the real detectors store: 57 swings,
4 patterns (incl. a confirmed Double bottom), 2 breakouts, 4 retests, 3 zones.
No paper outcome or P&L is invented; the Performance tiles stay honestly
unavailable until the backend can compute them.

## Fixes in this pass

1. Layout/declutter per the references (above), Simple default, one function
   at a time; technical text under Detailed/Diagnostics, nodes never deleted.
2. More menu no longer opens off-screen at ≤1180px (panel right-anchored;
   measured at 390px: right edge 363 of 390, was 482; horizontal overflow 0).
3. Returning to the engine timeframe now restores the captured chart view
   explicitly (`restoreLogicalView`/`restoreChartView`) instead of relying on
   the chart library's implicit setData behaviour.

## Known honest differences from the references

- No pattern confidence percentage and no performance figures: the backend
  stores neither; showing the references' illustrative numbers would be
  fabrication.
- Trade Plan lines need a PLANNABLE engine plan; the demo series produced
  qualified evidence but no plan at its newest boundary, so the screenshot
  shows the true no-plan panel.
- Live price reads UNAVAILABLE in the sandbox (no Binance access); on a
  connected machine it shows the spot quote, display-only.

## How to view

Disposable DB + `python -m trading_assistant.web --port 8123` (see the seed
script docstring), then compare each function view against its reference.
Full-page captures: `scripts/browser-audit/audit.mjs --functions`.

## PR #59 follow-up — mobile layout only

The original eight captures above remain untouched. This follow-up starts from
PR #59 commit `38d99dc9e2b4b61107b307d39f7cdd12a996e612` and fixes only the
narrow-screen trading-bar layout. The final slim-chrome styling had overridden
the responsive grid-area map with three columns while keeping one explicit
column track. Identity, timeframe controls and runner status then overflowed
their assigned cells even though the page reported zero horizontal overflow.

A final `max-width: 1020px` rule restores the existing stacked area map and
single-column tracks. No header content is removed, no figures are added, and
no desktop corrections or engine/backend changes are included.

Updated full-page captures (Simple / Trade Plan, same isolated synthetic demo):

- [`visual-signoff/mobile-390.png`](visual-signoff/mobile-390.png) — 390 × 844 viewport.
- [`visual-signoff/mobile-768.png`](visual-signoff/mobile-768.png) — 768 × 1024 viewport.

These are demonstration candles, not market history or performance. The live
quote is unavailable and the runner has never reported in this isolated demo;
both states remain visible. Different chart windows/modes from the original
Detailed capture are not changes to stored evidence or trading behaviour.

### Real-browser regression

Using headless Chromium 131 with the disposable database described above:

```bash
node scripts/browser-audit/mobile-layout-check.mjs http://127.0.0.1:8123 docs/visual-signoff
node scripts/browser-audit/audit.mjs --url http://127.0.0.1:8123 --out /tmp/mobile-audit/390 --width 390 --height 844 --timeout 60
node scripts/browser-audit/audit.mjs --url http://127.0.0.1:8123 --out /tmp/mobile-audit/768 --width 768 --height 1024 --timeout 60
```

Use `CHROME_PATH` for local Chrome as in the browser-audit README. The sandbox
run used the optional `@sparticuz/chromium@131` package and supplied its bundled
shared libraries via `LD_LIBRARY_PATH`, with `LANG`/`LC_ALL=en_GB.UTF-8`.

Results: both widths ready, **0px horizontal overflow**, no layout containment
or overlap failures, no page/console errors in the general audit. The focused
check also exercises all five view-only timeframe controls, the More menu and
refresh. It fails on unmodified PR #59 at 390px because identity, timeframe and
runner-status content escape their cells; it passes with this CSS correction.
Visual acceptance remains a human decision.

## Chart Patterns — correction against the approved reference

Follow-up to PR #62 (`a0f1faf`), frontend presentation only. The Chart Patterns
function is reshaped to the approved `BRAIN_Approved_Chart_Patterns.png`
(1672 × 941): a slim navy header, the chart at ~71 %, a 15 px gutter and three
cards (Pattern Detection, Other Detected Patterns, Key Levels). The selected
stored record is drawn at its own candles and prices. Confidence reads
"Not calculated", and patterns that were not detected are never shown.
Breakouts & Retests and the other functions are unchanged; the Breakouts
screenshot at 1672 is byte-identical before and after.

Full A–P pass/fail report, record ids, measurements, deviations and test output:
[`visual-signoff/patterns/REPORT.md`](visual-signoff/patterns/REPORT.md). Side by side:
[`visual-signoff/patterns/comparison-before-after-1672x941.png`](visual-signoff/patterns/comparison-before-after-1672x941.png).
Regression: `node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 <out>`.
Visual acceptance remains a human decision.
