# BRAIN browser audit

Readiness-based screenshots and layout checks for the dashboard. It is a separate Node package with
one dependency (`puppeteer-core`) and does not change the Python project or the frontend test runner.

## Mac: exact steps

Paste each code block as-is. Do not copy any text after a `#` on a command line; zsh runs it as a command.

### 1. Get the branch that contains this folder

Explanation: check that the working tree is clean first. If `git status` lists files, commit or stash them before continuing.

```
cd ~/btc-trading-assistant
git status
git fetch origin
git checkout arena/3ee2f955-btc-trading-assistant
git pull --ff-only origin arena/3ee2f955-btc-trading-assistant
ls scripts/browser-audit
```

The last command must list `audit.mjs`, `package.json`, `README.md` and `retry-check.mjs`.

### 2. Make sure port 8040 is free, then start the dashboard

Explanation: this shows any process already listening on port 8040. If it prints a line, note the number in the second column (PID) and stop that process with `kill PID`, replacing `PID` with that number. If it prints nothing, go straight to the start command.

```
lsof -nP -iTCP:8040 -sTCP:LISTEN
```

Start the dashboard in its own terminal and leave it running:

```
cd ~/btc-trading-assistant
source .venv/bin/activate
python -m trading_assistant.web --host 127.0.0.1 --port 8040
```

Open http://127.0.0.1:8040/ in a browser to confirm the dashboard loads before running the audit.

### 3. Install the audit package (second terminal)

```
cd ~/btc-trading-assistant/scripts/browser-audit
npm install
export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ls "$CHROME_PATH"
```

The `ls` must print the path. If it says "No such file", install Google Chrome or correct the path.

### 4. Run the checks

Explanation: the first dashboard load can take about 20 seconds on a large database, longer when cold. The script waits for the decision card and chart, not a fixed delay.

```
node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/desktop --width 1440 --height 900 --clip .terminal-chart-card --timeout 300
node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/tablet --width 834 --height 1112 --timeout 300
node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/phone --width 390 --height 844 --timeout 300
node retry-check.mjs http://127.0.0.1:8040/ ~/brain-shots/retry
```

Each `node audit.mjs` run prints a JSON report and exits 0 only when the dashboard is ready, there is no overflow, and there are no page or console errors.

## Troubleshooting

| Message | Meaning and fix |
|---|---|
| `address already in use` when starting the server | Another process holds port 8040. Run the `lsof` check in step 2, stop that process, and start again. |
| `cd: no such file or directory: .../scripts/browser-audit` | Your checkout is not on the branch with this folder. Run step 1. |
| `Cannot find module '.../audit.mjs'` | You ran `node` from the repo root. `cd ~/btc-trading-assistant/scripts/browser-audit` first. |
| `could not open http://...` | The dashboard server is not running on that port. Start it (step 2). |
| `ls "$CHROME_PATH"` says "No such file" | Chrome is not installed at that path. Install Google Chrome or fix the path. |
| `lsof: status error on ...` | A comment was pasted into the command. Paste only the command lines. |

## What the report shows

* `outcome.state`: `ready`, `error` (the dashboard's failure state, with its title and text), or `timeout`.
* `apiCalls`: status and duration of each `/api/` request, and any request the browser failed to complete.
* `layout.overflowPx`: horizontal overflow in pixels (must be 0).
* `pageErrors` and `consoleErrors`.


## Interactive chart regressions (offline fixtures only)

`chart-check.mjs` tests the real vendored chart library, not a mocked canvas. Use
an offline disposable test server containing stored synthetic candles for **all
four timeframes**, detected patterns, and visible support/resistance zones. Do
not seed an existing/user database to run this check. The browser uses a fresh
profile; the script submits no decisions and writes no ledger rows.

```bash
node scripts/browser-audit/chart-check.mjs http://127.0.0.1:8040/ .cache/chart-review
```

Run from the repository root after installing this package. `CHROME_PATH` works
as for `audit.mjs`. The optional sandbox Chromium package is not an application
dependency; on minimal Linux images its bundled shared libraries may also need
to be on `LD_LIBRARY_PATH`.

Checks include:

* Every layer toggle preserves OHLC, logical time range and price coordinates.
* An extreme 1-to-1,000,000 pattern guide and forty stacked markers cannot squash
  the confirmed candles (deliberately synthetic stress values, not BTC prices).
* Switching 5M → 15M → 4H → 1H shows recent bars on each view.
* Keyboard selection inspects an individual pattern without losing focus or changing the scale.
* Price-axis dragging actually enters manual scale mode; zone bands follow the
  resulting price transform. Reset view restores automatic scale.
* Zone layers exclude both axes and unchanged redraws retain their button nodes.
* A stalled then deliberately failed timeframe request leaves no stale candles or zones.
* Desktop (1440), tablet (834) and phone (390) have no horizontal page overflow or
  collapsed chart, including with every dashboard disclosure expanded. Screenshots go to the specified output folder (keep it ignored).

The existing `audit.mjs` and `retry-check.mjs` remain useful for initial load and
whole-dashboard failure/retry checks. These tests establish rendering behaviour,
not live exchange connectivity, strategy correctness or profitability.
