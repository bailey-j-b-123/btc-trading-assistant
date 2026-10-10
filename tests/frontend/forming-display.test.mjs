import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  canShowForming,
  currentBucketMs,
  FORMING_INTERVALS,
  FORMING_STALE_MS,
} from "../../src/trading_assistant/web/static/js/forming-display.js";

const NOW = Date.parse("2026-10-06T13:03:00Z");

test("forming display buckets align to UTC for 5m, 15m, 1h and 4h", () => {
  assert.deepEqual(Object.fromEntries(Object.keys(FORMING_INTERVALS).map((timeframe) =>
    [timeframe, new Date(currentBucketMs(timeframe, NOW)).toISOString()])), {
    "5m": "2026-10-06T13:00:00.000Z",
    "15m": "2026-10-06T13:00:00.000Z",
    "1h": "2026-10-06T13:00:00.000Z",
    "4h": "2026-10-06T12:00:00.000Z",
  });
  assert.equal(FORMING_STALE_MS, 45_000);
  assert.equal(currentBucketMs("1d", NOW), null);
  assert.equal(currentBucketMs("1h", Number.NaN), null);
});

test("forming display requires recent, aligned history strictly before the current bucket", () => {
  for (const [timeframe, minutes] of Object.entries(FORMING_INTERVALS)) {
    const duration = minutes * 60_000;
    const bucket = currentBucketMs(timeframe, NOW);
    assert.equal(canShowForming(timeframe, bucket - duration, NOW), true);
    assert.equal(canShowForming(timeframe, bucket - 2 * duration, NOW), true);
    assert.equal(canShowForming(timeframe, bucket, NOW), false, "no duplicate of stored current bucket");
    assert.equal(canShowForming(timeframe, bucket - 3 * duration, NOW), false, "old history is not live-adjacent");
    assert.equal(canShowForming(timeframe, bucket - duration + 1, NOW), false, "misaligned history is rejected");
  }
  assert.equal(canShowForming("1d", NOW - 1, NOW), false);
  assert.equal(canShowForming("1h", Number.NaN, NOW), false);
});

test("shared forming-display rules have no exchange, network, storage or engine path", () => {
  const source = readFileSync(new URL("../../src/trading_assistant/web/static/js/forming-display.js", import.meta.url), "utf8");
  for (const forbidden of ["WebSocket", "fetch(", "/api/", "localStorage", "indexedDB", "trading_assistant"])
    assert.ok(!source.includes(forbidden), forbidden);
});
