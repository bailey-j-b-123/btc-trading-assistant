# Breakouts & Retests — browser sign-off

Scope: presentation only. No detector, threshold, paper-trading, risk or
performance code was touched. Every event shown is a record the backend had
already stored, drawn at the candle it was detected on.

The screenshots come from the **isolated synthetic demo**
(`scripts/seed_demo_dashboard.py`, disposable SQLite database), driven in real
Chromium by:

```
node scripts/browser-audit/breakouts-check.mjs http://127.0.0.1:8123 /tmp/brain-breakouts
```

Run at 2026-10-13T19:16:31Z against the demo database described in
`scripts/seed_demo_dashboard.py` (600 hourly candles, `as_of`
2026-10-13T18:00:00Z, breakout and retest events stored near 2026-09-28).

## The problem that was fixed

The demo had **six stored events** (2 breakouts, 4 retests) but the screenshot
showed **zero drawn**, and the status line read “0 drawn”. Two independent
causes:

1. `evidence.js` trimmed breakout markers to the most recent **40 candles**
   (`RECENT_EVENT_CANDLES`) and collapsed events that shared a candle to a
   single unlabelled marker. The demo events sit about 145 candles before the
   newest candle, so all six were dropped — while the status line still counted
   them as loaded.
2. Even once drawn, those candles were **off-screen**: the default view opens at
   the live edge, roughly 260 candles to the right of them.

Nothing about the records themselves changed; the chart simply never drew them.

## What changed

| Area | Change |
| --- | --- |
| `evidence.js` | `RECENT_EVENT_CANDLES` is now a **default**, overridable per call. New per-call `breakoutOptions` flags: `recentCandles` (null = draw every stored event on a loaded candle), `oneMarkerPerCandle`, `labelEvents`. Retest markers are toned by the recorded state (green held, red failed, grey as recorded). The status wording was corrected: markers are drawn on the **loaded candles**, not “in the recent window”. |
| `chart_functions.js` | `breakoutOptions(functionId)` — only the Breakouts & Retests function drops the trim, draws one marker per stored event and labels it. New `eventNavigation()` (jump targets, oldest first, only events whose candle is loaded) and `eventFrame()` (the window that fits them). |
| `chart.js` | `focusBarOnChart()` (keeps the current zoom, centres one bar) and `frameBarsOnChart()` (fits a span with padding), both clamped inside the series and to the same live-edge margin as the default view. |
| `function_panel.js` | Each stored record in the function panel is a button that jumps to its candle. |
| `views/dashboard.js` | The event navigator (**Back / Next / Frame events**) in the chart toolbar, shown only for this function; clicking a stored record jumps to it. Redraw-only paths now capture and restore the view by **bar index**, so switching a function never shifts the window. |
| Marker key | Extended with the Breakouts & Retests sentence: “Breakouts & Retests labels them `BRK` / `SWP` / `FAIL` / `RT`, one marker per stored event”. |
| `styles.css` | Styles for the navigator and the record buttons, including the 760 px / 520 px breakpoints. |

## How the kinds are told apart

| Record | Shape | Colour | Label |
| --- | --- | --- | --- |
| Breakout | arrow (up if bullish/above, down if bearish/below) | green / red by direction | `BRK` |
| Failed breakout | arrow | neutral grey — it is a non-event | `FAIL` |
| Retest, held | circle | green | `RT` |
| Retest, failed | circle | red | `RT` |
| Retest, observed | circle | neutral grey | `RT` |
| Sweep | square | amber | `SWP` |

Same-candle events stack vertically instead of hiding each other.

## Evidence from the run

Full machine-readable report: `docs/visual-signoff/breakouts/breakouts-report.json`.

**1. All six stored events are drawn.** Status line:

> `Breakouts / Retests: 2 breakout(s) · 0 failed · 0 sweep(s) · 4 retest(s) · 6 drawn on the loaded candles.`

Navigator note:

> `6 stored events · 2026-09-28 03:00 UTC → 2026-09-28 04:00 UTC · jump keeps your zoom, Frame events fits them all`

**2. Markers are on the canvas, at the recorded candles.** Pixel census against
a baseline with the breakout layer switched off — every marker column gains
pixels, inside the vertical band where lightweight-charts stacks markers:

| Candle | Events | x (px) | Pixels added | Marker band (y) |
| --- | --- | --- | --- | --- |
| `2026-09-28T03:00Z` | 2 bullish breakouts | 274.79 | +647 | 227–401 |
| `2026-09-28T04:00Z` | 4 bullish retests (held / observed) | 317.21 | +611 | 213–422 |

**3. Framing fits the events.** “Frame events” moves the window to
`from: 119 → to: 132`; both event candles (bars 125 and 126) are inside it and
inside the visible pane (594 px wide).

**4. Jumping keeps the zoom.** Six “Next” presses from the framed window:

| Press | Window | Zoom (candles) | Event x |
| --- | --- | --- | --- |
| 1 | 119 → 132 | 13 | 232.36 |
| 2 | 119 → 132 | 13 | 232.36 |
| 3 | 120 → 133 | 13 | 274.79 |
| 4 | 120 → 133 | 13 | 274.79 |
| 5 | 120 → 133 | 13 | 274.79 |
| 6 | 120 → 133 | 13 | 274.79 |

The zoom never changes (13 candles throughout). The six events sit on only two
candles, so consecutive presses legitimately repeat a window; after the full
sequence every event has been centred.

**5. Switching a function never resets the view.**

| Step | Window |
| --- | --- |
| Default view on load | `380 → 502` |
| After selecting Breakouts & Retests | `380 → 502` |
| After framing the events | `119 → 132` |
| After switching back to Trade plan | `119 → 132` |

Exact equality on both switches. This was the one real regression found during
the run: the redraw paths used a **time-based** capture/restore, which silently
dropped the right-hand edge margin, so the first switch moved the window by
three bars. It was reproduced on unmodified `main` (a pre-existing defect, not
introduced here) and fixed by capturing the view as a bar-index range.

**6. Panel text.** “6 stored events”, a legend for Retest and Breakout, six
stored records with their time and band, and the footer:

> “Every step shown was recorded by the backend, at the candle it was detected
> on. A continuation is drawn only when the backend records one — it never
> appears on its own.”

## Screenshots

| File | What it shows |
| --- | --- |
| `docs/visual-signoff/breakouts/breakouts-1440-full.png` | Whole dashboard at 1440 × 980 with the breakout markers drawn. |
| `docs/visual-signoff/breakouts/breakouts-1440-stage.png` | The chart stage only — the two marker columns with the navigator above them. |
| `docs/visual-signoff/breakouts/breakouts-1440-framed.png` | After “Frame events”: both event candles in view, markers visible. |
| `docs/visual-signoff/breakouts/breakouts-1440-after-return.png` | After switching back to Trade plan — the view is unchanged. |

## Tests

`npm test` → **420 pass / 0 fail** (410 before this change, +10 new):

* `tests/frontend/evidence.test.mjs` — every stored event drawn once and labelled; an event whose candle is not loaded is never drawn, wherever it sits; the calm density rule is unchanged for every other selection.
* `tests/frontend/chart-functions.test.mjs` — `breakoutOptions`, `eventNavigation`, `eventFrame`.
* `tests/frontend/chart-view.test.mjs` — `focusBarOnChart` keeps the zoom and clamps at both ends; `frameBarsOnChart` fits with padding.
* `tests/frontend/dashboard.test.mjs` — selecting the function does not move or re-zoom the view, framing fits the events, every jump keeps the zoom, listed records jump to their own candle, and leaving the function leaves the view where the operator put it.

## Known console output on this run

`pageErrors: []`. The four console entries are pre-existing sandbox/offline
noise, unrelated to the change and recorded (not suppressed) in the report:

* one CSP `style-src 'self'` inline-style violation (present on the baseline);
* three `wss://stream.binance.com:9443/ws/btcusdt@kline_1h` `ERR_CONNECTION_CLOSED` — the sandbox has no outbound WebSocket.
