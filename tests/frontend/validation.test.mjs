import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../src/trading_assistant/web/static/", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("historical validation remains read-only and is available from the one-page performance section", async () => {
  const [view, api, main, html, dashboard] = await Promise.all([
    source("js/views/validation.js"),
    source("js/api.js"),
    source("js/main.js"),
    source("index.html"),
    source("js/views/dashboard.js"),
  ]);
  assert.match(view, /HISTORICAL VALIDATION — NOT LIVE PERFORMANCE/);
  assert.match(view, /not realised profit/);
  assert.match(api, /\/api\/validation/);
  assert.match(main, /renderValidation/); // legacy direct route remains available
  assert.match(dashboard, /Historical validation loads from the existing read-only API/);
  assert.match(dashboard, /api\.validation\(/);
  assert.doesNotMatch(html, /data-route="validation"/);
  assert.doesNotMatch(view, /\/orders|balance|leverage|position sizing/i);
});
