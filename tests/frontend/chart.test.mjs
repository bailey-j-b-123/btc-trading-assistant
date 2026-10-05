/** Chart data mapping: exact backend rows in, no invented overlays out. */

import test from "node:test";
import assert from "node:assert/strict";

import { applyOverlays, toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";

const ROWS = [
  [1704067200000, "100", "102", "99", "101", "10"],
  [1704070800000, "101", "105", "100.5", "104", "12"],
];

test("toChartCandles maps backend rows without recomputing values", () => {
  const candles = toChartCandles(ROWS);
  assert.equal(candles.length, 2);
  assert.deepEqual(candles[0], { time: 1704067200, open: 100, high: 102, low: 99, close: 101 });
  assert.equal(candles[1].close, 104);
});

test("empty payload stays empty — no demo candles are invented", () => {
  assert.deepEqual(toChartCandles([]), []);
  assert.deepEqual(toChartCandles(null), []);
});

function mockHandle() {
  const lines = [];
  return {
    lines,
    series: {
      priceLines: () => lines,
      removePriceLine: (line) => lines.splice(lines.indexOf(line), 1),
      createPriceLine: (line) => lines.push(line),
    },
  };
}

test("applyOverlays draws only levels present in the payload", () => {
  const handle = mockHandle();
  applyOverlays(handle, {
    overlays: {
      zones: [{ band_low: "99", band_high: "101", role: "support" }],
      range: { range_low: "98", range_high: "103" },
      equal_levels: [{ level: "102.5", type: "equal_high" }],
      swings: [],
    },
    plan: {
      entry: { value: "124" },
      stop: { value: "117" },
      invalidation: { value: "117" }, // identical to stop -> not duplicated
      targets: [{ level: { value: "138" } }, { level: { value: null } }],
    },
    prefs: { overlays: { zones: true, range: true, equalLevels: true, planLevels: true, swings: false } },
  });
  const titles = handle.lines.map((line) => line.title);
  assert.ok(titles.includes("entry"));
  assert.ok(titles.includes("protective stop"));
  assert.ok(!titles.includes("invalidation")); // same level as stop: no duplicate line
  assert.ok(titles.includes("target 1"));
  assert.ok(!titles.includes("target 2")); // null target level -> never drawn
  assert.equal(handle.lines.filter((line) => line.title === "support low").length, 1);
  assert.equal(handle.lines.filter((line) => line.title === "range high").length, 1);
});

test("missing plan or overlays create zero lines", () => {
  const handle = mockHandle();
  applyOverlays(handle, { overlays: {}, plan: null, prefs: { overlays: {} } });
  assert.equal(handle.lines.length, 0);
});

test("disabled overlay groups stay hidden", () => {
  const handle = mockHandle();
  applyOverlays(handle, {
    overlays: { zones: [{ band_low: "99", band_high: "101", role: "support" }] },
    plan: null,
    prefs: { overlays: { zones: false } },
  });
  assert.equal(handle.lines.length, 0);
});
