# BRAIN browser audit

Readiness-based screenshots and layout checks for the dashboard. It is a separate Node package and
does not affect the Python project.

## Mac setup (uses your installed Chrome)

    cd ~/btc-trading-assistant/scripts/browser-audit
    npm install
    export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/desktop --width 1440 --height 900 --clip .terminal-chart-card

The first dashboard load can take about 20 seconds on a large database. The script waits for the
decision card and chart, not a fixed delay. Use `--timeout 300` for a slower machine.

## What the report shows

* `outcome.state`: `ready`, `error` (the app's own "Something went wrong" state, with its text), or `timeout`.
* `apiCalls`: status and duration of each `/api/` request, and any request the browser failed to complete.
* `layout.overflowPx`: horizontal overflow in pixels (must be 0).
* `pageErrors` and `consoleErrors`.

Exit code is 0 only when the dashboard is ready, there is no overflow, and there are no page or console errors.
