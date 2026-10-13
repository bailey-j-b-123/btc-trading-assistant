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
