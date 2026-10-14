# Support & Resistance — evidence report (draft, pre-review)

**Status: implementation complete for review on an isolated branch; NOT merged, NOT deployed, and
NOT visually accepted.** The owner's approved reference screenshot has not been supplied to this
session, so nothing here claims that the screen matches it. What *is* claimed below is measured, and
every claim names the command or record it came from.

| | |
|---|---|
| Branch | `arena/d11df194-btc-trading-assistant` |
| Base | `a0f1faf` — the head of PR #62's branch `arena/3e360305-btc-trading-assistant` |
| Pull request | draft, targeting `arena/3e360305-btc-trading-assistant` (not `main`) |
| Scope | Support & Resistance only. Chart Patterns, strategy qualification, execution, risk, journal and the forward ledger are untouched. |

Why the base is PR #62 instead of `main`: PR #62 carries the owner-approved dashboard composition
(slim dark navy nav, dominant candlestick chart, the chart stage, the function panel beside it). The
Support & Resistance presentation is a change *to that composition*, so building it on `main` would
either duplicate or fight PR #62's approved layout. The Chart Patterns session is working on its own
branch; this branch neither contains nor touches it.

## 1. What the feature now does

* The shaded bands are the **stored Step-3 zones** — the same objects the engine already computes on
  the last 40 confirmed swings. No second detector, no re-derivation in the browser, no invented
  levels. Presentation reads `market_structure.levels.SupportResistanceZone` and nothing else.
* A band's **edges are the stored `band_low`/`band_high`**, drawn straight from the chart's own price
  scale. The band's role (support / resistance / price-inside) comes from the stored position
  relative to the last confirmed close; the reasons a swing is there are never rewritten.
* **Test counts are truthful.** `touch_count` is the number of confirmed swing evidences; one candle
  can supply two of them (a swing high *and* a swing low), so the UI shows the number of **separate
  instants** (`distinct_test_timestamps`) and states the swing-evidence count separately whenever the
  two differ. Merged bands keep their member zones' own numbers visible and are never presented as
  one zone with the sum as its touches.
* **Withheld evidence is accounted for.** The backend presents the nearest bands per side; the chart
  applies its own readability cap on top. Both the function status line and the line under the chart
  state the real total: drawn, not drawn (split by which cap withheld them), and merged for display.
* **Higher timeframe bands are separate evidence**, drawn dashed and labelled with their own
  timeframe, positioned against the viewed chart's close and marked as coming from another timeframe.
  If no higher timeframe is supported, the UI says so instead of implying one.
* **Read-only links to Breakouts & Retests**: a band states which stored breakout / failed breakout /
  sweep / retest used *that zone as its own reference level*, with the instant BRAIN recorded it and
  where price sat relative to the band at that instant. A link requires exact band identity and a
  reference of type `zone`; a similar price is not a link, and no link is ever claimed when none was
  recorded.

## 2. Detector accuracy — pinned, not assumed

`tests/test_zone_detector_audit.py` (25 tests) pins the detector characteristics the display depends
on, measured against the real detector rather than read from a comment:

* only swings with `confirmed_at <= as_of` are used; a swing confirmed later is excluded **and
  counted** in `swing_count`;
* clustering is chronological against an anchor that is never replaced, so a band can never grow past
  twice the anchor's tolerance by bridging (`tests/test_zone_detector_audit.py`:
  `test_the_anchor_never_drifts_so_a_band_cannot_be_widened_by_bridging`);
* a swing exactly on the tolerance boundary joins the band (inclusive comparison);
* the role is the centre-vs-close rule, so a band *containing* price can still be classified as
  resistance; the presentation layer reports that disagreement instead of hiding it;
* `band_width_pct` is `(high − low) / high × 100`, and distance is measured from the centre;
* invalid tolerance and empty inputs are refused or reported, never guessed at.

`tests/test_zone_presentation.py` (34 tests) and `tests/test_zone_evidence.py` (15 tests) cover the
display band contract: band-edge positions, inside-overrides-role with the raw roles kept, merge sums
with `merged_zone_count`, the nearest-band cap plus `hidden_count`, fade after 100 viewed candles,
per-band `latest_close`, exact-bounds identity for event links, similar-price ≠ link, swing/range
references ≠ links, `known_at` causality, malformed rows skipped, and "unchecked on this view" being
distinct from "no event referenced it".

## 3. No look-ahead

Every zone, band and link is produced at one `as_of` and carries it. Swings are filtered by
`confirmed_at` before clustering; a swing that confirms after `as_of` cannot enter a band; an event
recorded after `as_of` cannot be linked (`known_at <= as_of` is tested). The chart-evidence window
drops candles that are not closed at `as_of` and reports `excluded_future_count`. Historical bands are
not presented as freshly tested: each band carries `last_tested`, `first_observed` and an
`age_candles` fade state, and the label reads the stored instant count rather than the age.

## 4. Multi-timeframe behaviour (measured payloads)

Read-only probe: `.venv/bin/python scripts/sr_payload_probe.py 15m 1h 4h ...` against disposable
synthetic demo databases. Example (10-day synthetic demo, 15m/1h/4h stored):

| timeframe | stored | presented | drawn | hidden | merged | notes |
|---|---|---|---|---|---|---|
| 15m | 5 | 5 | 5 | 0 | 0 | distinct instants == evidences on this series; two bands faded |
| 1h | 5 | 5 | 5 | 0 | 0 | bands on both sides of price plus one containing it |
| 4h | 4 | 4 | 4 | 0 | 0 | same detector, that timeframe's own swings |

On the 60-day demo database the 1h view presents three zones and the chart's readability cap withholds
two of them, which is exactly what the status line and the `S/R bands:` line report (verified at four
viewport sizes, see §6). `market_state.higher_timeframes` reports `requested: []` for the engine
snapshot — the ladder is built by the chart from `meta.supported_timeframes`, and with this
deployment's configuration (`1m, 5m, 15m, 1h, 4h`) there is no frame above 4H, so no 1D band is
invented. Enabling `1d` requires the deployment's own `TRADING_ASSISTANT_SUPPORTED_TIMEFRAMES` and
genuinely stored 1D candles; the switcher then adds 1D and the ladder offers 1D above 4H.

## 5. Defects found and their disposition

| # | Defect (evidence) | Disposition |
|---|---|---|
| 1 | A band's not-drawn total disagreed between the function status line (`1 further band(s) not drawn`) and the panel/meta line, because the backend cap and the chart's cap were counted separately. | Fixed: both lines read one accounting object (`bandAccounting`); status, panel and the under-chart line agree, tested in `chart-functions.test.mjs`, `function-panel.test.mjs`, `dashboard.test.mjs`. |
| 2 | A label reported `24×` for a band whose 24 swing evidences came from 22 candles — the summed number was shown as if every count were a separate test. | Fixed: `distinct_test_timestamps` is the label figure, the evidence count and the shared-candle count are on the label's title and in the explanation; regression tests added. |
| 3 | The line under the chart could still read `S/R bands: 0 drawn, 0 stored band(s) not drawn` after bands had been painted (the overlays arrive with a later request than the evidence), putting two different totals on one screen. | Fixed: the line is recomposed whenever the model *or* the painted bands change (`updateEvidenceMeta`), with a regression test in `dashboard.test.mjs`. Found in the real browser, not in a fixture. |
| 4 | A very tall band (a zone containing price, 59 % of the pane) was filled at full strength, covering the candles. | Fixed: a band above 35 % of the pane is drawn as a scaled wash (`scaleFillAlpha`), its edges unmoved; the browser check asserts the alpha formula at every viewport. |
| 5 | Band labels could collide where several bands sit within a few pixels. | Fixed: labels are stacked with a 16 px minimum gap (`ZONE_LABEL_MIN_GAP_PX`), asserted in the browser check. |
| 6 | `zone_evidence` links could be claimed for a similar price. | Refused by construction: exact band identity plus `reference_type == "zone"` plus `known_at <= as_of`; pinned by tests. |
| 7 | The Support & Resistance legend described a blue "Range mid — midpoint of a detected range", while the chart draws no midpoint anywhere: the third band is the amber price-inside zone and the blue lines are the detected range's own high and low. The legend therefore named a level that does not exist. | Fixed: the legend now lists exactly what is drawn — Resistance (red), Price inside (amber), Support (green), Range (blue, the dashed stored boundaries) — and the price-inside tone matches the amber band in the panel's own rows (`function-panel.test.mjs`). |

## 6. Browser verification (real Chromium, 4 sizes)

```
node scripts/browser-audit/support-resistance-check.mjs http://127.0.0.1:8123 ./out --timeframe 1h --switch 4h
```

Result: **passed at 1536×864, 1440×900, 1920×1080 and 390×844** (report:
`docs/visual-signoff/support-resistance/support-resistance-report.json`). At each size the check
proves, against the API's own `/api/market/structure` payload:

* every painted band, converted back through the chart's price scale, lies **inside** a stored band's
  bounds and is **exact** unless the pane clips it — no band is drawn at a price the detector did not
  store;
* no band is painted twice (deduplicated by stored bounds);
* the status line's drawn count equals the painted bands, and its not-drawn total equals the API's
  hidden count **plus** the chart's own cap;
* the `S/R bands:` line under the chart agrees with the status line;
* every colour matches the stored position (teal below price, red above, neutral amber containing);
* fills are ≤ 0.12 alpha, a band above a third of the pane uses the documented scaled wash, and the
  band layer never intercepts pointer input;
* each label states a test figure the API reports, labels are ≥ 16 px apart, stay inside the pane, and
  never claim a zone held, flipped, was retested or was broken;
* clicking a band explains **that** band with its own stored bounds and test figure, naming the
  timeframe it came from, with no such claim;
* after switching to 4H every painted band is a 4H zone and the labels name 4H;
* no horizontal overflow and no uncaught page error.

Screenshots: `support-resistance-1536x864-full.png`, `-stage.png`, `-selected.png`, `-4h.png`,
`support-resistance-1440x900-stage.png`, `support-resistance-1920x1080-stage.png`,
`support-resistance-390-mobile-full.png`, `-stage.png`. All of them are the synthetic demo database
(`scripts/seed_sr_demo.py`), clearly labelled demonstration data — never market history.

Console/page errors observed in the sandbox: a `style-src` inline-style CSP report and failed
`wss://stream.binance.com` connections (no exchange access from this sandbox). Both are environment
artefacts and are recorded in the report JSON; the sandbox Chromium also raises
`Invalid language tag: en-US@posix` on some views. None of these is raised by the Support &
Resistance code path, and none changes what is drawn.

## 7. Decision-preservation evidence (the payload digests)

`tests/test_decision_output_regression.py` pins SHA-256 digests of the whole decision-bearing
dashboard payload and of every frame, qualification snapshot and plan at four fixed historical
boundaries. Adding presentation fields changes the dashboard digest by construction, so the change was
measured rather than assumed:

* A structural diff of the stripped dashboard payload before (`a0f1faf`) and after this branch at all
  four boundaries lists **149 changed leaf paths, every one of them inside
  `meta.supported_timeframes` or `overlays.zone_bands.*`, and every one an addition** — no
  `plan`, `planning`, `qualification`, `scenario`, `market_state` or `chart_evidence` value differs.
* The decision digest fixture (`tests/fixtures/decision_digests/decision_n1000.json`) is
  **byte-identical** after regeneration, so frames, enumerated qualification snapshots and trade plans
  are unchanged.
* `tests/fixtures/decision_digests/dashboard_n500.json` was regenerated deliberately with the
  documented script (`PYTHONPATH=src:tests .venv/bin/python tests/decision_digests.py`); it holds only
  new digests for the same boundaries, and both regression tests pass
  (`6 passed, 1 xfailed`).

No detector parameter, threshold, tolerance, lookback or ordering rule was changed. The one
deliberately untouched xfail
(`tests/test_decision_invariance.py::test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary`)
still xfails.

## 8. Performance and regressions

In-process `DashboardService.dashboard(symbol="BTC/USDT", timeframe="1h")` on the same 60-day demo
database: **13.1 s cold / 3.2 s warm**, identical before and after this branch. The cost is the
existing qualification walk (`setup_qualification.service.build_frames` → `pattern_liquidity.analysis`
→ `market_structure.analysis._analyze_memoized`, 3410 memoized analyses), not the band presentation.
No zone computation was added: the bands are built from `analysis.levels` that the engine had already
computed, and the chart never recomputes a zone in the browser.

Frontend suite: `npm test` → **436 passed / 0 failed** (`node --test tests/frontend/*.test.mjs`:
434). Python: `.venv/bin/python -m pytest -q` → see `STATUS.md` for the run recorded with this branch;
the targeted zone suites are 34 + 15 + 25 = 74 passed. Lint:
`.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests` → **15 findings, byte-identical to the
same command on `a0f1faf`** (pre-existing: 2 × F841 in `ai_explanation/renderer.py`, 12 × F401/E741 in
test fixtures and unrelated test modules). This branch adds none. Breakouts & Retests behaviour is unchanged: its check script and its layout tests still pass,
and the only shared file touched (`chart.js`) gained a band style and label helper behind the
existing zone painter, with regression tests in `zone-bands.test.mjs`.

## 8. Changed files

Backend: `web/zone_presentation.py`, `web/zone_evidence.py` (new), `web/chart_evidence.py`,
`web/dashboard_service.py`.
Frontend: `chart.js`, `chart_functions.js`, `function_panel.js`, `explain.js`, `zone-position.js`,
`views/dashboard.js`, `styles.css`.
Tests: `tests/test_zone_presentation.py`, `tests/test_zone_evidence.py` (new),
`tests/test_zone_detector_audit.py` (new), `tests/frontend/{zone-bands,zone-position,chart-functions,
function-panel,dashboard,explain}.test.mjs`.
Tooling: `scripts/browser-audit/support-resistance-check.mjs` (new), `scripts/seed_sr_demo.py` (new),
`scripts/sr_payload_probe.py` (new).

## 9. Limitations and open items (not hidden)

* **No visual acceptance is claimed.** The owner's approved reference screenshot has not been
  available to this session; the screenshots here are evidence of behaviour, not of matching that
  reference. Review against it is still required.
* The support band on the 60-day demo renders at full class opacity; whether the default support
  wash should be lighter is a design question for review, not a defect this session resolved.
* **Corrected 2026-10-14 (BRAIN-AUDIT-0006).** The original entry here said `linked_events` was
  never observed in the `linked: true` state because "their events use swing and zone references
  that do not coincide". That reason was factually wrong: on the 4h demo frame the detector objects
  contain 5 zone-referenced breakouts, 1 zone-referenced failed breakout and 10 zone-referenced
  retests, and the linkage could not link historical references because it compared bounds for
  exact equality while those bounds are re-derived every analysis frame. Two separate defects were
  confirmed and repaired on the integration branch (BRAIN-AUDIT-0005: the shared projection dropped
  a retest's reference entirely; BRAIN-AUDIT-0006: the identity rule plus an operator-facing
  absolute negative that the stored evidence did not support). A live `linked: true` record has now
  been observed — on the demo series truncated at the band's own breakout instant
  (`as_of=2026-08-26T08:00:00Z`), band `85958.6 – 86543.0` reports `linked=True, count=1,
  counts={'breakout': 1, ...}` in the browser as well as the payload. See
  `docs/brain-repair/REPAIR_LOG.md` and `docs/brain-repair/evidence/0005-0006-browser.txt`.
* The engine's `higher_timeframes` list is empty for the engine snapshot on these demo databases, so
  the HTF ladder was verified through the chart's own supported-timeframe driven path and the payload
  probe, not with a live 1D band drawn on a 1H chart.
* Full `pytest` and `ruff` runs are pending on this branch; only the targeted suites above are
  green at the time of writing.
* Chart Patterns integration (for example an explanation that references a pattern level) is
  deliberately out of scope and untouched.
