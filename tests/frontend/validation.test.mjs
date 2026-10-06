import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../src/trading_assistant/web/static/", import.meta.url);

async function source(path) {
  return readFile(new URL(path, root), "utf8");
}

test("validation UI is explicitly historical and has no execution/account surface", async () => {
  const [view, api, main, html] = await Promise.all([
    source("js/views/validation.js"),
    source("js/api.js"),
    source("js/main.js"),
    source("index.html"),
  ]);
  assert.match(view, /HISTORICAL VALIDATION — NOT LIVE PERFORMANCE/);
  assert.match(view, /not realised profit/);
  assert.match(api, /\/api\/validation/);
  assert.match(main, /renderValidation/);
  assert.match(html, /data-route="validation"/);
  assert.doesNotMatch(view, /\/orders|balance|leverage|position sizing/i);
});
