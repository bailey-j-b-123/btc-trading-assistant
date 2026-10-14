# Desktop bottom-section follow-up to PR #60

Status: **draft, awaiting human review; not merged or deployed.**

Base: PR #60, `arena/13e9921c-btc-trading-assistant`, commit
`092389b155f0f5201eb67c7fdf1d7e42e5c9e6cb`.

## Scope

Only the bottom dashboard composition changes:

- At ≥1181px, Paper Trades / Diagnostics are the left tabs. Performance is
  always displayed on the right, aligned with the existing 360px Current Setup
  rail and the top of the left tab strip. Switching left tabs never hides it.
- At ≤1180px, the existing three-tab behaviour is retained, including the
  mobile header/trading-bar correction from PR #60. Resizing restores the
  narrow selection without re-fetching data or rebuilding the chart.
- Desktop Performance is a labelled region, not a panel controlled by an
  invisible tab. Its tab and tabpanel semantics return on narrow screens.
- All performance renderers and data sources are unchanged. Missing Win rate
  and Total P&L remain **Not available yet**; no illustrative figures, P&L,
  win/loss ratios, or new metrics are introduced.

No chart, Current Setup, engine, API, trading rule, fingerprint, threshold,
production database, or existing trading record was changed.

## Real Chromium evidence

The screenshots are generated from the actual app and API using Chromium 131,
not HTML mocks or mocked API responses. The disposable SQLite database
`/tmp/brain-desktop-demo.sqlite3` contains only the deterministic synthetic
candles from `scripts/seed_demo_dashboard.py`: **demonstration data, not genuine
market history or performance**. Its backend returned no recorded paper plans;
the screenshots therefore show backend-reported zero counts and honest missing
performance metrics. No outcome or profit figure was seeded.

| Width | Full page | Bottom viewport | Diagnostics viewport |
| --- | --- | --- | --- |
| 1440 × 1080 | [Full](visual-signoff/desktop-bottom/desktop-1440.png) | [Paper Trades + Performance](visual-signoff/desktop-bottom/desktop-1440-bottom.png) | [Diagnostics + Performance](visual-signoff/desktop-bottom/desktop-1440-diagnostics.png) |
| 1920 × 1080 | [Full](visual-signoff/desktop-bottom/desktop-1920.png) | [Paper Trades + Performance](visual-signoff/desktop-bottom/desktop-1920-bottom.png) | [Diagnostics + Performance](visual-signoff/desktop-bottom/desktop-1920-diagnostics.png) |

Both widths: **0px horizontal overflow**, Performance to the right and
aligned with the left tab strip, visible on both left tabs and after manual
refresh, responsive switching passed, **no uncaught page errors**.
Manual refresh retains the existing remount-to-Paper-Trades behaviour.

The audit records rather than conceals console errors: Binance WebSocket
connections are blocked in this sandbox, and the chart emits an inline-style
CSP warning. The identical CSP warning was reproduced on unmodified PR #60
served from a temporary archive against the same isolated demo database.
Neither chart nor CSP was changed to suppress it. See
[layout/console report](visual-signoff/desktop-bottom/desktop-bottom-report.json)
and [PR #60 baseline console](visual-signoff/desktop-bottom/pr60-baseline-console.json).
Visual approval remains a human decision.

## Commands and results

- `.venv/bin/python -m pytest` → **1182 passed, 1 xfailed, 1 warning**, 299.35s.
  The expected shallow-history xfail is untouched. The warning concerns
  Starlette's deprecated httpx test-client integration.
- `npm test` → **410 passed, 0 failed**.
- `node --test tests/frontend/dashboard.test.mjs` → **25 passed, 0 failed**.
- `LD_LIBRARY_PATH=/tmp/brain-chromium-libs/lib LANG=en_GB.UTF-8 LC_ALL=en_GB.UTF-8 node scripts/browser-audit/desktop-bottom-check.mjs http://127.0.0.1:8123 docs/visual-signoff/desktop-bottom`
  → **passed at 1440px and 1920px**; screenshots/report above.
- `LD_LIBRARY_PATH=/tmp/brain-chromium-libs/lib LANG=en_GB.UTF-8 LC_ALL=en_GB.UTF-8 node scripts/browser-audit/mobile-layout-check.mjs http://127.0.0.1:8123 /tmp/brain-mobile-regression`
  → **passed at 390px and 768px**, 0px overflow, no containment/overlap
  issues or page errors; all five timeframe controls, More and refresh passed.
- `git diff --check` → passed.

The sandbox used optional `@sparticuz/chromium@131`, installed without changing
the package manifest or generating a lockfile. Its bundled `al2023.tar.br`
libraries were extracted to `/tmp/brain-chromium-libs`; with local Chrome, set
`CHROME_PATH` instead (see the browser-audit README). No dependencies were
added to the application or frontend test runner.
