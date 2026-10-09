import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../src/trading_assistant/web/static/", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("the one-page dashboard reuses read-only forward data without primary-page navigation", async () => {
  const [view, api, main, html, dashboard] = await Promise.all([
    source("js/views/live.js"),
    source("js/api.js"),
    source("js/main.js"),
    source("index.html"),
    source("js/views/dashboard.js"),
  ]);
  assert.match(view, /LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE/);
  assert.match(view, /PAPER OBSERVATION — NO REAL ORDER/);
  assert.match(view, /Paper trading and historical performance do not establish future profitability\./);
  assert.match(dashboard, /api\.forward\(\{[^}]*limit: 50/);
  assert.match(dashboard, /symbol: prefs\.preferredSymbol/);
  assert.match(dashboard, /timeframe: prefs\.preferredTimeframe/);
  assert.match(dashboard, /Recent decisions/);
  assert.match(dashboard, /performanceViewModel/);
  assert.match(dashboard, /Paper \/ historical records only/);
  assert.match(api, /\/api\/forward/);
  assert.match(main, /renderLive/); // legacy direct routes remain available
  assert.doesNotMatch(html, /data-route="(live|statistics|validation|settings)"/);
  assert.match(html, /PAPER OBSERVATION ONLY/);
  assert.match(html, /href="#\/journal"/);
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

test("the dashboard exposes no order, account, or sizing surface", async () => {
  const [dashboard, api] = await Promise.all([
    source("js/views/dashboard.js"),
    source("js/api.js"),
  ]);
  for (const text of [dashboard, api]) {
    assert.doesNotMatch(text, /createOrder|placeOrder|submitOrder|fetchBalance|positions?\(|setLeverage|withdraw/i);
    assert.doesNotMatch(text, /api\/orders|api\/account|api\/balance|api\/positions/i);
  }
  assert.match(dashboard, /api\.dashboardDecision\(body\)/);
  assert.match(dashboard, /This appends a paper journal decision only/);
  assert.doesNotMatch(dashboard, /innerHTML|document\.write|eval\(/);
});
