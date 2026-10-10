# BRAIN browser audit

Readiness-based screenshots and layout checks for the dashboard. It is a separate Node package with
one dependency (`puppeteer-core`) and does not change the Python project or the frontend test runner.

## Mac: exact steps

1. Get the branch that contains this folder (run in Terminal):

       cd ~/btc-trading-assistant
       git status                      # should be clean; commit or stash anything else first
       git fetch origin
       git checkout arena/3ee2f955-btc-trading-assistant
       git pull --ff-only origin arena/3ee2f955-btc-trading-assistant
       ls scripts/browser-audit        # must list audit.mjs, package.json, retry-check.mjs

2. Start the dashboard (one terminal, leave it running). If port 8040 is already in use, stop the old process
   first, or use another port and change the URLs below to match:

       lsof -nP -iTCP:8040 -sTCP:LISTEN     # shows any process holding 8040
       source .venv/bin/activate
       python -m trading_assistant.web --host 127.0.0.1 --port 8040

   Open http://127.0.0.1:8040/ in a browser to confirm it loads before running the audit.

3. Install the audit package (second terminal):

       cd ~/btc-trading-assistant/scripts/browser-audit
       npm install
       export CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
       ls "$CHROME_PATH"                    # must print the path, not "No such file"

4. Run the checks:

       node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/desktop --width 1440 --height 900 --clip .terminal-chart-card --timeout 300
       node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/tablet --width 834 --height 1112 --timeout 300
       node audit.mjs --url http://127.0.0.1:8040/ --out ~/brain-shots/phone --width 390 --height 844 --timeout 300
       node retry-check.mjs http://127.0.0.1:8040/ ~/brain-shots/retry

The first dashboard load can take about 20 seconds on a large database (longer when cold). The script
waits for the decision card and chart, not a fixed delay.

## Troubleshooting

| Message | Meaning and fix |
|---|---|
| `address already in use` when starting the server | Another process holds the port. Use `lsof` above, stop it, or pick another port. |
| `cd: no such file or directory: .../scripts/browser-audit` | Your checkout is not on the branch with this folder. Run step 1. |
| `Cannot find module '.../audit.mjs'` | You ran `node` from the repo root. `cd` into `scripts/browser-audit` first. |
| `could not open http://...` | The dashboard server is not running on that port. Start it (step 2). |
| `ls "$CHROME_PATH"` fails | Chrome is not installed at that path. Install Google Chrome or fix the path. |

## What the report shows

* `outcome.state`: `ready`, `error` (the dashboard's failure state, with its title and text), or `timeout`.
* `apiCalls`: status and duration of each `/api/` request, and any request the browser failed to complete.
* `layout.overflowPx`: horizontal overflow in pixels (must be 0).
* `pageErrors` and `consoleErrors`.

Exit code is 0 only when the dashboard is ready, there is no overflow, and there are no page or console errors.
