# Breakouts & Retests — browser sign-off

Scope: presentation only. No detector, threshold, paper-trading, risk or
performance code was touched. Every price, band, timestamp, count and status on
this screen is a value the backend had already stored, passed through formatting
only.

The screenshots come from the **isolated synthetic demo**
(`scripts/seed_demo_dashboard.py`, disposable SQLite database), driven in real
Chromium by:

```
node scripts/browser-audit/breakouts-check.mjs http://127.0.0.1:8123 /tmp/brain-breakouts
```

## The screen

| Area | What it is |
| --- | --- |
| Left: the chart | Candlestick chart with volume bars along the bottom. |
| Across the chart | A shaded **RESISTANCE ZONE** band, from the stored breakout's own `band_low` / `band_high`. |
| On the chart | Exactly three numbered blue callouts — **1 Breakout**, **2 Retest**, **3 Continuation** — each with a leader line and an arrow at the candle its own step belongs to. |
| Right, top | **BTC/USDT** summary card: last stored close, its candle time, the stored trend and the data-freshness state. |
| Right, below | **Breakout Logic**: the three steps, in order, each with the status the backend recorded. |

## What each callout says, and why

The three callouts are always drawn, in the same places, whatever the data
says — that is the layout. Only the statuses change.

| Step | Status on this run | Where it comes from |
| --- | --- | --- |
| 1 Breakout | **Confirmed** (green) | The stored breakout: close 84,328.00 broke the 84,229.90 band by 0.12%, with 1 confirming candle. A stored `failed_breakout` for the same breakout would read **Failed** instead. |
| 2 Retest | **Held** (green) | The stored retest records for that breakout: `state: "held"`. `failed` reads **Failed**, a retest with no state reads **Observed**, and no stored retest reads **Awaiting**. |
| 3 Continuation | **In Progress** (amber) | The backend stores no continuation event of its own, so this step is **never** marked complete. If one is ever stored it reads **Confirmed**; until then the callout points at where the move went on from, and the step says so in as many words. |

A step whose candle is not loaded is not drawn — it is never snapped to a
neighbouring bar to make it visible.

## Evidence from the run

Full machine-readable report: `docs/visual-signoff/breakouts/breakouts-report.json`.
Run at 1440 × 980 against the demo database (600 hourly synthetic candles;
breakout at 2026-09-28 05:00 UTC, retest at 06:00 UTC, band 84,229.90).

**Status line**

> `Breakouts / Retests: 2 breakout(s) · 0 failed · 0 sweep(s) · 4 retest(s) · 1 Breakout: Confirmed · 2 Retest: Held · 3 Continuation: In Progress.`

**Right-hand column** — the summary card is above the panel, and both sit right
of the chart:

* card symbol `BTC/USDT`; rows: Last close, Candle, Trend, Data.
* panel heading `Breakout Logic`, chip `Bullish sequence`, blocks
  `Reference band` (84,229.90) and `Steps`:

| # | Step | Status | Detail |
| --- | --- | --- | --- |
| 1 | Breakout | Confirmed | Close 84,328.00 broke the 84,229.90 band by 0.12% |
| 2 | Retest | Held | 2 retest records are stored for this breakout and 1 of 2 held the band |
| 3 | Continuation | In Progress | The backend has stored no continuation for this breakout, so this step is not marked complete |

Panel note: *“Every status above is a stored record. Continuation stays In
Progress until the backend records one — it is never marked complete because
price moved.”*

**Three callouts, each on its own candle.** After “Frame all three steps”
(window `from: 119 → to: 133`):

| Callout | Arrow x | Arrow y | Candle |
| --- | --- | --- | --- |
| 1 Breakout | 256.4 | 268.1 | 2026-09-28 05:00 UTC |
| 2 Retest | 296.0 | 273.2 | 2026-09-28 06:00 UTC |
| 3 Continuation | 335.6 | 211.1 | 2026-09-28 07:00 UTC |

The arrow x values are compared with the chart's own `timeToCoordinate` for the
stored candles (256.4 and 296.0 — identical). Three leader lines, no callout
left outside the window, and the badges are stacked at y 14 / 54 / 94 so they
never overlap.

**Shaded band.** One band, labelled `RESISTANCE ZONE`, 674 px wide (the full
pane) and 14 px thick — a stored level is given a minimum thickness so it reads
as a zone rather than a hairline.

**Volume.** 41,588 coloured pixels in the bottom band of the pane.

**Zoom is never reset for you.**

| Step | Window |
| --- | --- |
| Default view on load | `380 → 502` |
| After selecting Breakouts & Retests | `380 → 502` |
| After “Frame all three steps” | `119 → 133` |
| After switching to Trade plan | `119 → 133` |
| After switching back | `119 → 133` |

Exact equality on every switch; only the explicit “Frame all three steps”
control moves the view.

## Screenshots

| File | What it shows |
| --- | --- |
| `docs/visual-signoff/breakouts/breakouts-1440-full.png` | The whole dashboard at 1440 × 980. |
| `docs/visual-signoff/breakouts/breakouts-1440-stage.png` | The chart card: band, three callouts, volume. |
| `docs/visual-signoff/breakouts/breakouts-1440-framed.png` | After “Frame all three steps”. |
| `docs/visual-signoff/breakouts/breakouts-1440-after-return.png` | After switching away and back — the view is unchanged. |

## Tests

`npm test` → **424 pass / 0 fail**. New `tests/frontend/breakout-stages.test.mjs`
pins the sequence model: the three steps are always returned in order; each is
anchored to its own candle; a step whose candle is not loaded is never snapped to
a neighbour; a failed breakout reads Failed; the retest status is the backend's
own state; continuation is In Progress until a continuation is stored and
Confirmed when one is; the layout keeps its three steps when nothing is stored or
the evidence is unavailable. `tests/frontend/function-panel.test.mjs` pins the
Breakout Logic panel, and `tests/frontend/chart-view.test.mjs` pins that a jump
keeps the zoom while framing is the only control that changes it.

## Known console output on this run

`pageErrors: []`. The four console entries are pre-existing sandbox/offline
noise, unrelated to the change and recorded (not suppressed) in the report:

* one CSP `style-src 'self'` inline-style violation (present on the baseline);
* three `wss://stream.binance.com:9443/ws/btcusdt@kline_1h` `ERR_CONNECTION_CLOSED` — the sandbox has no outbound WebSocket.
