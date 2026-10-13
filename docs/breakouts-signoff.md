# Breakouts & Retests — layout sign-off

Scope: **presentation only**. No detector, threshold, paper-trading, risk rule,
performance figure, database or API contract was touched. Every price, band,
timestamp, count and status on this screen is a value the backend had already
stored, passed through formatting only.

Evidence comes from the **isolated synthetic demo**
(`scripts/seed_demo_dashboard.py`, disposable SQLite database), driven in real
Chromium at four viewport sizes by:

```
node scripts/browser-audit/breakouts-check.mjs http://127.0.0.1:8123 /tmp/brain-breakouts
```

Screenshots and the machine-readable report of this run are in
[`docs/visual-signoff/breakouts/`](visual-signoff/breakouts/):

| Size | Default view | Framed on the three steps | Whole page / composition |
| --- | --- | --- | --- |
| 1536 × 864 (primary) | `breakouts-1536x864-default.png` | `breakouts-1536x864-framed.png` | `breakouts-1536x864-full.png`, `breakouts-1536x864-stage.png` |
| 1440 × 900 | `breakouts-1440x900-default.png` | `breakouts-1440x900-framed.png` | — |
| 1920 × 1080 | `breakouts-1920x1080-default.png` | `breakouts-1920x1080-framed.png` | — |
| 390 × 844 (phone) | `breakouts-390-mobile-default.png` | `breakouts-390-mobile-framed.png` | `breakouts-390-mobile-full.png` |

`breakouts-report.json` holds every number quoted below.

## The composition

| Area | What it is |
| --- | --- |
| Top | The existing dark-navy bar: the instrument on the left, the timeframe controls with 1H selected. Unobtrusive, and it never sits over the chart (the chart card carries a `scroll-margin-top`, and the callouts are placed against their candles, not pinned to the top edge of the pane). |
| Left, ~75 % | The candlestick chart: green/red candles, subtle grid, price scale on the right edge, time scale along the bottom, volume histogram across the lower part of the pane. |
| Across the chart | A **blue-grey translucent** band labelled **“Resistance Zone”**, 30 px thick, drawn from the stored breakout's own `band_low` / `band_high`. |
| On the chart | Exactly three prominent blue circular callouts — **1 Breakout**, **2 Retest**, **3 Continuation** — each with a thin leader line and an arrow at the candle its own step belongs to. |
| Right, ~23 % | **BTC/USDT** card on top, **Breakout Logic** card below it. Both start on the same line as the chart. |

## Measured at each size

| Check | 1536 × 864 | 1440 × 900 | 1920 × 1080 | 390 × 844 |
| --- | --- | --- | --- | --- |
| Chart share of the row | 75.4 % | 75.3 % | 75.5 % | 100 % (stacked) |
| Card column share | 23.1 % | 23.1 % | 23.2 % | 100 % (under the chart) |
| Chart top vs cards top | 400 / 400 | 400 / 400 | 400 / 400 | stacked |
| Chart pane (px) | 730 × 646 | 658 × 674 | 840 × 812 | 260 × 410 |
| Chart pane as a share of the card | 41.3 % | 40.4 % | 52.5 % | 18.7 % |
| Band thickness / fill | 30 px `rgba(122,156,196,0.16)` | 30 px, same | 30 px, same | 30 px, same |
| Band label | `Resistance Zone` | same | same | same |
| Callout font / number circle | 13 px / 26 px | 13 px / 26 px | 13 px / 26 px | 13 px / 26 px |
| Callouts overlapping each other | 0 | 0 | 0 | 0 |
| Callouts clipped or covered | none | none | none | none |
| Smallest font in the right column | 11 px | 11 px | 11 px | 11 px |
| Raw metadata rows in Simple mode | 0 | 0 | 0 | 0 |
| Volume pixels in the lower pane | 58,337 | 54,681 | 84,725 | 12,715 |
| Uncaught page errors | 0 | 0 | 0 | 0 |

## The three callouts

They are always drawn, in the same arrangement, whatever the data says — that is
the layout. Only the statuses change.

| Step | Placement | Status on this run | Where it comes from |
| --- | --- | --- | --- |
| 1 Breakout | Above the breakout candle, arrow down at it | **Completed** (green) | The stored breakout: close 84,328.00 broke the 84,229.90 band by 0.12 %, with 1 confirming candle. A stored `failed_breakout` for the same breakout reads **Failed**. |
| 2 Retest | Below and to the right of the retest candle, arrow back up at it | **Held** (green) | The stored retest records for that breakout: `state: "held"`. `failed` reads **Failed**; a retest with no state reads **Awaiting Confirmation**; no stored retest at all reads **Awaiting Confirmation**. |
| 3 Continuation | Above the later price action, arrow down at it | **In Progress** (amber) | The backend stores no continuation event of its own, so this step is **never** marked complete. |

Where two callouts would land on the same place, the later one is stepped
further from its candle until they clear each other, and every callout is
clamped inside the pane — nothing is clipped, and number 1 is drawn whole at
every size.

A step with **no stored record** is drawn differently from a confirmed one: an
outlined (not filled) number circle, a dashed leader line and a dashed border,
plus `recorded="false"` on the element. On this run that is step 3.

A step whose candle is not loaded is not drawn at all — it is never snapped to a
neighbouring bar to make it visible.

## The two right-hand cards

**BTC/USDT** — the Bitcoin mark, the symbol, the exchange and timeframe, the
last stored close in large type (which candle it belongs to stays on the
figure's tooltip), then change, 24 h high, 24 h low and 24 h volume. This
view's payload carries no 24-hour roll-up, so those four rows read
**“Unavailable”** — a placeholder is written, never a number, and never a value
taken from the reference screenshot.

**Breakout Logic** — the three numbered steps down the card, joined by a
subtle vertical progression line. Each step carries one short sentence; the
stored numbers, prices and times stay on the row's tooltip and in Detailed /
Diagnostics, so Simple mode has no microscopic metadata. The status of each
step is a pill on the right edge of the card:

* 1 Breakout — **Completed**
* 2 Retest — **Held**
* 3 Continuation — **In Progress**

Under them a compact **Key Idea**: “The broken band is now support. The move on
from the retest is what is being watched.” Below that, the one control that
changes the zoom (“Frame all three steps”) and the integrity note: *No step is
marked complete without a stored record.*

## Zoom and the window

* Switching to this function **never changes the zoom**: the number of candles
  on screen is preserved exactly (122 on this run).
* The window is moved to the stored steps **only when not one of them is on
  screen** — otherwise this screen would open on an empty stretch of chart with
  all three callouts off the side. It happens once per visit; it never drags the
  view back while the operator is looking at something else.
* Switching away to another function and back leaves the range exactly where the
  operator put it (119 → 133 before, during and after this run).

## Known differences from the reference

Stated plainly, because the approved screenshot is not available in this
sandbox and cannot be compared side by side:

1. **24 h figures are unavailable.** This view's payload carries no rolling
   24-hour high, low, volume or change, so those rows read “Unavailable”.
2. **The right-hand column is ~140 px taller than the chart at 1536 × 864**
   (815 px against 674 px) — the two cards carry the three steps, the reference
   band, the Key Idea, the frame control and the integrity note.
3. The band is drawn **30 px thick** with a minimum thickness, because the
   stored breakout band is a single level (`band_low == band_high`).
