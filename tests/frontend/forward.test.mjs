import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../src/trading_assistant/web/static/", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("live/paper UI labels forward observations as paper, not performance", async () => {
  const [view, api, main, html, dashboard] = await Promise.all([
    source("js/views/live.js"),
    source("js/api.js"),
    source("js/main.js"),
    source("index.html"),
    source("js/views/dashboard.js"),
  ]);
  assert.match(view, /LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE/);
  assert.match(view, /PAPER OBSERVATION — NO REAL ORDER/);
  assert.match(view, /LIVE MARKET DATA/);
  assert.match(view, /Paper trading and historical performance do not establish future profitability\./);
  assert.match(view, /not realised profit/);
  assert.match(dashboard, /LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE/);
  assert.match(dashboard, /PAPER OBSERVATION — NO REAL ORDER/);
  assert.match(api, /\/api\/forward/);
  assert.match(main, /renderLive/);
  assert.match(html, /data-route="live"/);
});

test("live/paper UI keeps both comparison sides labelled and denominated", async () => {
  const view = await source("js/views/live.js");
  assert.match(view, /comparison\.historical\.label/);
  assert.match(view, /comparison\.forward\.label/);
  assert.match(view, /denominators/);
  assert.match(view, /comparability UNKNOWN|NOT COMPARABLE|comparable rule versions/);
  assert.match(view, /AMBIGUOUS/);
  assert.match(view, /INCOMPLETE/);
  assert.match(view, /unresolved/);
});

test("live/paper UI exposes no order, account, or sizing surface", async () => {
  const [view, dashboard, api] = await Promise.all([
    source("js/views/live.js"),
    source("js/views/dashboard.js"),
    source("js/api.js"),
  ]);
  for (const text of [view, dashboard, api]) {
    assert.doesNotMatch(text, /createOrder|placeOrder|submitOrder|fetchBalance|positions?\(|setLeverage|withdraw/i);
    assert.doesNotMatch(text, /api\/orders|api\/account|api\/balance|api\/positions/i);
  }
  assert.doesNotMatch(view, /innerHTML|document\.write|eval\(/);
});
