import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { applyOverlays, setCandles, toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";
import { DEFAULT_PREFS, loadPrefs, savePrefs } from "../../src/trading_assistant/web/static/js/util.js";
import { scenarioBand, lookingForViewModel } from "../../src/trading_assistant/web/static/js/looking-for.js";
import { liveQuoteModel, mountLiveDisplay, LIVE_STALE_MS } from "../../src/trading_assistant/web/static/js/live-display.js";

const fact = Object.freeze({
  available: true, setup_id: "actual-setup", timeframe: "1h", family: "breakout_retest_continuation",
  direction: "bearish", state: "WATCH", seed_event: { kind: "breakout", known_at: "2026-10-06T12:00:00Z" },
  reference: { band_low: "81000", band_high: "81100", type: "swing_low" },
  pending_required: [{ rule_id: "held_retest", reason: "Waiting for a held retest." }], invalidation: null,
});
const chart = () => {
  const lines = new Set();
  let candles = null;
  return {
    lines,
    get candles() { return candles; },
    series: {
      setData: (value) => { candles = value; },
      createPriceLine: (options) => { const line = { options }; lines.add(line); return line; },
      removePriceLine: (line) => lines.delete(line),
    },
    volume: { setData() {} },
  };
};
const names = (handle) => [...handle.lines].map((line) => line.options.title);

test("clean defaults hide historical diagnostics but retain an actual scenario band and plan", () => {
  assert.equal(DEFAULT_PREFS.overlays.zones, false);
  assert.equal(DEFAULT_PREFS.overlays.range, false);
  assert.equal(DEFAULT_PREFS.overlays.equalLevels, false);
  assert.equal(DEFAULT_PREFS.overlays.swings, false);
  assert.equal(DEFAULT_PREFS.overlays.planLevels, true);
  const handle = chart();
  const overlays = { zones: [{ band_low: "10", band_high: "20" }],
    equal_levels: [{ level: "30" }], range: { range_low: "40", range_high: "50" },
    swings: [{ price: "60", kind: "high" }] };
  const plan = { state: "PLANNABLE", entry: { value: "80" }, stop: { value: "70" },
    invalidation: { value: "70" }, targets: [{ level: { value: "90" } }] };
  const snapshot = { overlays, plan, scenarioBand: scenarioBand(fact, "1h"), prefs: DEFAULT_PREFS };
  applyOverlays(handle, snapshot);
  assert.deepEqual(names(handle), ["Zone low", "Zone high", "Entry", "Stop / Invalidation", "T1"]);
  applyOverlays(handle, { ...snapshot, prefs: { overlays: {
    zones: true, range: true, equalLevels: true, swings: true, planLevels: true,
  } } });
  for (const name of ["Zone low", "Range low", "Equal lows", "Swing high", "Entry"]) {
    assert.ok(names(handle).some((value) => value.includes(name)), name);
  }
  assert.equal(handle.lines.size, 11); // diagnostic detail remains available, never deleted
});

test("LOOKING FOR is a compact projection of backend setup and hierarchy facts", () => {
  const dashboard = {
    looking_for: fact,
    qualification: { state: "WATCH" },
    planning: { state: null },
    multi_timeframe: {
      available: true,
      status: "evaluated",
      decision: "awaiting_confirmation",
      overall: "WAITING FOR CONFIRMATION",
      counter_trend: false,
      invalidated_if: ["a closed candle above the setup reference"],
    },
  };
  const before = structuredClone(dashboard);
  const model = lookingForViewModel(dashboard);
  assert.equal(model.title, "Bearish breakout → retest");
  assert.equal(model.watching, "$81,000–$81,100");
  assert.match(model.need, /retest that holds the breakout level/);
  assert.equal(model.invalidation, "a closed candle above the setup reference");
  assert.equal(model.status, "WAITING FOR CONFIRMATION");
  assert.deepEqual(dashboard, before);
});

test("missing or mismatched facts draw no scenario; no future coordinates or probabilities", () => {
  assert.equal(scenarioBand(null, "1h"), null);
  assert.equal(scenarioBand(fact, "5m"), null);
  assert.equal(scenarioBand({ ...fact, reference: null }, "1h"), null);
  assert.equal(scenarioBand({ ...fact, reference: { band_low: "??", band_high: "81100" } }, "1h"), null);
  const missing = lookingForViewModel({ looking_for: { available: false }, qualification: { state: "NO_SETUP" } });
  assert.equal(missing.reference, null);
  assert.match(missing.watching, /No unique setup reference/);
  const band = scenarioBand(fact, "1h");
  assert.deepEqual(band, { low: 81000, high: 81100 });
  const handle = chart();
  applyOverlays(handle, { overlays: {}, scenarioBand: band, prefs: DEFAULT_PREFS });
  assert.deepEqual([...handle.lines].map((line) => line.options.price), [81000, 81100]);
  assert.equal([...handle.lines].every((line) => !Object.hasOwn(line.options, "time") &&
    !Object.hasOwn(line.options, "probability")), true);
  applyOverlays(handle, { overlays: {}, scenarioBand: scenarioBand(fact, "5m"), prefs: DEFAULT_PREFS });
  assert.equal(handle.lines.size, 0);
});

test("public quote is validated and becomes visibly stale; it is never a candle", () => {
  const now = Date.parse("2026-10-06T12:00:00Z");
  const payload = { price: "83512.40", status: "CURRENT", exchange: "binance", fetched_at: new Date(now).toISOString() };
  assert.equal(liveQuoteModel(payload, now).status, "CURRENT");
  assert.equal(liveQuoteModel(payload, now + LIVE_STALE_MS).status, "STALE");
  for (const price of [null, "NaN", "Infinity", "-1", "0"]) {
    assert.equal(liveQuoteModel({ ...payload, price }, now).status, "UNAVAILABLE");
  }
  assert.equal(liveQuoteModel({ ...payload, exchange: "kraken" }, now).status, "UNAVAILABLE");
  assert.equal(liveQuoteModel({ ...payload, exchange: undefined }, now).status, "UNAVAILABLE");
  const handle = chart();
  const stored = [[now - 3600000, "100", "102", "99", "101", "5"]];
  setCandles(handle, stored);
  assert.deepEqual(handle.candles, toChartCandles(stored));
  liveQuoteModel(payload, now); // display never updates the closed series
  assert.deepEqual(handle.candles, toChartCandles(stored));
  const liveSource = readFileSync(new URL("../../src/trading_assistant/web/static/js/live-display.js", import.meta.url), "utf8");
  assert.ok(!liveSource.includes("setCandles") && !liveSource.includes("series.update"));
  assert.ok(!liveSource.includes("/api/dashboard") && !liveSource.includes("/api/market/candles"));
});


test("chart-only preference migration preserves the engine timeframe and later manual toggles", () => {
  const prior = globalThis.localStorage;
  let raw = JSON.stringify({ preferredTimeframe: "4h", preferredSymbol: "BTC/USDT",
    overlays: { zones: true, range: true, equalLevels: true, planLevels: false } });
  globalThis.localStorage = { getItem: () => raw, setItem: (_, value) => { raw = value; } };
  try {
    const migrated = loadPrefs();
    assert.equal(migrated.preferredTimeframe, "4h");
    assert.equal(migrated.preferredSymbol, "BTC/USDT");
    assert.equal(migrated.overlays.zones, false);
    assert.equal(migrated.overlays.range, false);
    assert.equal(migrated.overlays.equalLevels, false);
    assert.equal(migrated.overlays.planLevels, false);
    migrated.overlays.zones = true;
    savePrefs(migrated);
    assert.equal(loadPrefs().overlays.zones, true);
    assert.equal(loadPrefs().preferredTimeframe, "4h");
  } finally {
    if (prior === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = prior;
  }
});


test("browser disconnect immediately labels the last valid quote stale without drawing a candle", async () => {
  const saved = ["document", "fetch", "setInterval", "clearInterval"].map((key) => [key,
    Object.hasOwn(globalThis, key), globalThis[key]]);
  class Node {
    constructor() { this.children = []; this._text = ""; this.dataset = {}; this.style = {}; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
    append(...children) { this.children.push(...children); }
    setAttribute() {}
  }
  const ticks = [];
  let requests = 0;
  globalThis.document = { createElement: () => new Node() };
  globalThis.setInterval = (callback) => { ticks.push(callback); return ticks.length; };
  globalThis.clearInterval = () => {};
  globalThis.fetch = async () => {
    if (++requests > 1) throw new Error("Disconnected");
    return { ok: true, json: async () => ({
      status: "CURRENT", price: "82512.40", exchange: "binance",
      source: "Binance Spot public ticker", fetched_at: new Date().toISOString(),
    }) };
  };
  const display = mountLiveDisplay("BTC/USDT");
  try {
    display.start();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(display.node.dataset.freshness, "CURRENT");
    assert.match(display.node.textContent, /Binance Spot public ticker/);
    ticks[0]();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(display.node.dataset.freshness, "STALE");
    assert.match(display.node.textContent, /LIVE DATA STALE/);
    assert.ok(!display.node.textContent.includes("FORMING CANDLE"));
  } finally {
    display.destroy();
    for (const [key, had, value] of saved) {
      if (had) globalThis[key] = value;
      else delete globalThis[key];
    }
  }
});
