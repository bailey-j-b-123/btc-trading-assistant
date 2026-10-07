/** Chart data mapping: exact backend rows in, no invented overlays out. */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  applyOverlays,
  clearOverlays,
  createPriceChart,
  destroyPriceChart,
  FALLBACK_CHART_HEIGHT,
  FALLBACK_CHART_WIDTH,
  setCandles,
  toChartCandles,
} from "../../src/trading_assistant/web/static/js/chart.js";

const CHART_MODULE_PATH = fileURLToPath(
  new URL("../../src/trading_assistant/web/static/js/chart.js", import.meta.url),
);
const VENDOR_PATH = fileURLToPath(
  new URL("../../src/trading_assistant/web/static/vendor/lightweight-charts.standalone.production.js", import.meta.url),
);

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

/**
 * Faithful stand-in for the vendored Lightweight Charts v4.2.0 series object.
 *
 * The real series exposes exactly two price-line methods:
 *   createPriceLine(options) -> opaque PriceLine handle
 *   removePriceLine(handle)  -> removes that handle
 * It has no priceLines() enumeration method, so the dashboard must remember
 * every handle it created. This mock deliberately mirrors that surface, which
 * is what makes the regression below reproducible.
 */
function mockHandle() {
  const lines = new Set();
  return {
    series: {
      createPriceLine: (options) => {
        const line = { options };
        lines.add(line);
        return line;
      },
      removePriceLine: (line) => {
        lines.delete(line);
      },
    },
    chart: {},
    volume: {},
    lines,
  };
}

const titles = (handle) => [...handle.lines].map((line) => line.options.title);
const drawn = (handle) => [...handle.lines].map((line) => `${line.options.title}@${line.options.price}`);

const PAYLOAD = {
  overlays: {
    zones: [{ band_low: "99", band_high: "101", role: "support" }],
    range: { range_low: "98", range_high: "103" },
    equal_levels: [{ level: "102.5", type: "equal_high" }],
    swings: [],
  },
  plan: {
    state: "PLANNABLE",
    entry: { value: "124" },
    stop: { value: "117" },
    invalidation: { value: "117" }, // identical to stop -> not duplicated
    targets: [{ level: { value: "138" } }, { level: { value: null } }],
  },
  prefs: { overlays: { zones: true, range: true, equalLevels: true, planLevels: true, swings: false } },
};

// 2 zones + 2 range bounds + 1 equal level + entry/stop/target 1 = 8 deterministic lines.
const EXPECTED_LINES = 8;

test("the chart module never calls the nonexistent series.priceLines() API", () => {
  // The vendored bundle keeps public API names (createPriceLine, removePriceLine)
  // in minified code, so the absent identifier proves there is no enumeration
  // method to call — the exact reason tracking must be explicit.
  const vendor = readFileSync(VENDOR_PATH, "utf8");
  assert.ok(
    !vendor.includes("priceLines"),
    "vendored Lightweight Charts v4.2.0 exposes createPriceLine/removePriceLine only",
  );
  const source = readFileSync(CHART_MODULE_PATH, "utf8");
  assert.ok(
    !/\.priceLines\s*\(/.test(source),
    "the chart module must not call the nonexistent series.priceLines() method",
  );
});

test("applyOverlays draws only levels present in the payload", () => {
  const handle = mockHandle();
  applyOverlays(handle, PAYLOAD);
  const labels = titles(handle);
  assert.ok(labels.includes("entry"));
  assert.ok(labels.includes("protective stop"));
  assert.ok(!labels.includes("invalidation")); // same level as stop: no duplicate line
  assert.ok(labels.includes("target 1"));
  assert.ok(!labels.includes("target 2")); // null target level -> never drawn
  assert.equal(labels.filter((title) => title === "support low").length, 1);
  assert.equal(labels.filter((title) => title === "range high").length, 1);
  assert.equal(handle.lines.size, EXPECTED_LINES);
  // Deterministic values: the exact payload numbers reach the library untouched.
  assert.deepEqual(drawn(handle), [
    "support low@99",
    "support high@101",
    "range low@98",
    "range high@103",
    "equal highs@102.5",
    "entry@124",
    "protective stop@117",
    "target 1@138",
  ]);
});

test("overlay apply and clear work without a priceLines() enumeration method", () => {
  const handle = mockHandle();
  assert.equal(typeof handle.series.priceLines, "undefined");

  applyOverlays(handle, PAYLOAD);
  assert.equal(handle.lines.size, EXPECTED_LINES);

  clearOverlays(handle);
  assert.equal(handle.lines.size, 0);

  // Clearing an empty overlay set is a no-op, not an error.
  clearOverlays(handle);
  clearOverlays(null);
  assert.equal(handle.lines.size, 0);
});

test("clearOverlays removes only lines this dashboard created", () => {
  const handle = mockHandle();
  const foreign = handle.series.createPriceLine({ price: 1, title: "foreign" });
  applyOverlays(handle, PAYLOAD);
  clearOverlays(handle);
  assert.equal(handle.lines.has(foreign), true);
  assert.equal(handle.lines.size, 1);
});

test("repeated dashboard renders never duplicate or leak overlay lines", () => {
  const handle = mockHandle();
  applyOverlays(handle, PAYLOAD);
  const expected = drawn(handle);

  for (let cycle = 0; cycle < 5; cycle += 1) {
    clearOverlays(handle);
    assert.equal(handle.lines.size, 0, `cycle ${cycle}: clear must remove every tracked line`);
    applyOverlays(handle, PAYLOAD);
    assert.deepEqual(drawn(handle), expected, `cycle ${cycle}: overlays must be identical`);
  }

  // A redraw refreshes in place: applying again must replace, never stack.
  applyOverlays(handle, PAYLOAD);
  assert.equal(handle.lines.size, EXPECTED_LINES);
  assert.deepEqual(drawn(handle), expected);
});

test("missing plan or overlays create zero lines", () => {
  const handle = mockHandle();
  applyOverlays(handle, { overlays: {}, plan: null, prefs: { overlays: {} } });
  assert.equal(handle.lines.size, 0);
});

test("disabled overlay groups stay hidden", () => {
  const handle = mockHandle();
  applyOverlays(handle, {
    overlays: { zones: [{ band_low: "99", band_high: "101", role: "support" }] },
    plan: null,
    prefs: { overlays: { zones: false } },
  });
  assert.equal(handle.lines.size, 0);
});

test("confirmed swing overlays remain optional and use only returned levels", () => {
  const handle = mockHandle();
  applyOverlays(handle, {
    overlays: { swings: [{ kind: "high", price: "105" }] },
    plan: null,
    prefs: { overlays: { swings: true } },
  });
  assert.deepEqual(drawn(handle), ["swing high@105"]);
});

test("the chart accepts the real market-candles API envelope", () => {
  const payload = {
    exchange: "kraken",
    symbol: "BTC/USDT",
    timeframe: "1h",
    candles: ROWS,
    returned_count: ROWS.length,
  };
  assert.deepEqual(toChartCandles(payload), toChartCandles(ROWS));
});

test("malformed or missing candle payloads stay empty", () => {
  assert.deepEqual(toChartCandles(undefined), []);
  assert.deepEqual(toChartCandles({ candles: null }), []);
  assert.deepEqual(toChartCandles({ candles: [[1704067200000, "bad", null, "", "NaN", "1"]] }), []);
});

test("chart data refreshes replace overlay handles and chart disposal releases resources", () => {
  const saved = new Map();
  for (const key of ["window", "document", "ResizeObserver", "getComputedStyle"]) {
    saved.set(key, { present: Object.hasOwn(globalThis, key), value: globalThis[key] });
  }

  const charts = [];
  const observers = [];
  class MockResizeObserver {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
  }
  globalThis.document = { documentElement: {} };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "monospace" });
  globalThis.ResizeObserver = MockResizeObserver;
  globalThis.window = {
    LightweightCharts: {
      createChart: (_container, _options) => {
        const record = { removed: false, candles: [], volume: [], lines: new Set(), resizeCount: 0 };
        const series = {
          setData: (data) => { record.candles = data; },
          createPriceLine: (options) => {
            const line = { options };
            record.lines.add(line);
            return line;
          },
          removePriceLine: (line) => record.lines.delete(line),
        };
        const volume = { setData: (data) => { record.volume = data; } };
        const chart = {
          addCandlestickSeries: () => series,
          addHistogramSeries: () => volume,
          priceScale: () => ({ applyOptions: () => {} }),
          resize: () => { record.resizeCount += 1; },
          remove: () => { record.removed = true; },
        };
        charts.push(record);
        return chart;
      },
    },
  };

  try {
    const handle = createPriceChart({ clientWidth: 920, clientHeight: 480 });
    assert.ok(handle);
    setCandles(handle, {
      candles: [
        [1704067200000, "100", "102", "99", "101", "10"],
        [1704070800000, "101", "105", "100.5", "104", "12"],
      ],
    });
    applyOverlays(handle, PAYLOAD);
    const firstDraw = drawn({ lines: charts[0].lines });
    for (let refresh = 0; refresh < 5; refresh += 1) {
      applyOverlays(handle, PAYLOAD);
      assert.deepEqual(drawn({ lines: charts[0].lines }), firstDraw);
      assert.equal(charts[0].lines.size, EXPECTED_LINES);
    }
    assert.equal(charts[0].candles.length, 2);
    assert.equal(charts[0].candles[1].close, 104);

    destroyPriceChart(handle);
    destroyPriceChart(handle);
    assert.equal(charts[0].removed, true);
    assert.equal(charts[0].lines.size, 0);
    assert.equal(observers[0].disconnected, true);
  } finally {
    for (const [key, prior] of saved) {
      if (prior.present) globalThis[key] = prior.value;
      else delete globalThis[key];
    }
  }
});

function withChartGlobals(library, observerClass, run) {
  const saved = new Map();
  for (const key of ["window", "document", "ResizeObserver", "getComputedStyle", "requestAnimationFrame"]) {
    saved.set(key, { present: Object.hasOwn(globalThis, key), value: globalThis[key] });
  }
  globalThis.document = { documentElement: {} };
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "monospace" });
  if (observerClass) globalThis.ResizeObserver = observerClass;
  else delete globalThis.ResizeObserver;
  globalThis.window = { LightweightCharts: library, addEventListener() {}, removeEventListener() {} };
  try {
    return run();
  } finally {
    for (const [key, prior] of saved) {
      if (prior.present) globalThis[key] = prior.value;
      else delete globalThis[key];
    }
  }
}

function stubLibrary(records) {
  return {
    createChart: (_container, options) => {
      const record = { options, candles: [], volume: [], resizedTo: [], removed: false };
      const series = {
        setData: (data) => { record.candles = data; },
        createPriceLine: (lineOptions) => ({ lineOptions }),
        removePriceLine: () => {},
      };
      records.push(record);
      return {
        addCandlestickSeries: () => series,
        addHistogramSeries: () => ({ setData: (data) => { record.volume = data; } }),
        priceScale: () => ({ applyOptions: () => {} }),
        resize: (width, height) => { record.resizedTo.push([width, height]); },
        remove: () => { record.removed = true; },
      };
    },
  };
}

test("a zero-size container at mount no longer kills the chart: fallback now, real layout on resize", () => {
  const records = [];
  const observers = [];
  class CapturingObserver {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe() {}
    disconnect() {}
  }
  withChartGlobals(stubLibrary(records), CapturingObserver, () => {
    const container = { clientWidth: 0, clientHeight: 0 };
    const handle = createPriceChart(container);
    assert.ok(handle, "chart is created even before layout exists");
    assert.equal(records[0].options.width, FALLBACK_CHART_WIDTH);
    assert.equal(records[0].options.height, FALLBACK_CHART_HEIGHT);
    // The stylesheet lands: the container gains its real size and the
    // observer reports it; the chart snaps to the real layout.
    container.clientWidth = 900;
    container.clientHeight = 530;
    observers[0].callback();
    assert.deepEqual(records[0].resizedTo.at(-1), [900, 530]);
    // Valid stored rows still reach the library after the late layout.
    setCandles(handle, ROWS);
    assert.equal(records[0].candles.length, 2);
    assert.equal(records[0].volume.length, 2);
    destroyPriceChart(handle);
  });
});

test("createPriceChart still returns null only when recovery is impossible", () => {
  withChartGlobals(stubLibrary([]), null, () => {
    assert.equal(createPriceChart(null), null);
  });
  withChartGlobals(null, null, () => {
    assert.equal(createPriceChart({ clientWidth: 900, clientHeight: 530 }), null);
  });
});

test("real backend row bytes convert and reach setData in order", () => {
  // Shape pinned to the live /api/dashboard payload: millisecond opens,
  // exact decimal strings, ascending hourly rows.
  const rows = [];
  for (let index = 0; index < 21; index += 1) {
    const open = 100 + index;
    rows.push([1704067200000 + index * 3600000, String(open), String(open + 1), String(open - 1), String(open), "10"]);
  }
  const records = [];
  withChartGlobals(stubLibrary(records), null, () => {
    const handle = createPriceChart({ clientWidth: 900, clientHeight: 530 });
    setCandles(handle, { candles: rows });
    assert.equal(records[0].candles.length, 21);
    assert.equal(records[0].volume.length, 21);
    const times = records[0].candles.map((candle) => candle.time);
    assert.deepEqual(times, [...times].sort((a, b) => a - b));
    assert.equal(new Set(times).size, 21);
    assert.deepEqual(records[0].candles[0], { time: 1704067200, open: 100, high: 101, low: 99, close: 100 });
    destroyPriceChart(handle);
  });
});
