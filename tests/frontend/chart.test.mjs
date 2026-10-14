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
  setEvidence,
  resetChartView,
  captureChartView,
  restoreChartView,
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

// 2 range bounds + 1 equal level + entry/stop/target 3 = 6 deterministic lines.
// Zones are NOT price lines any more: they are shaded bands (setZoneBands), tested in zone-bands.test.mjs.
const EXPECTED_LINES = 6;

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
  assert.ok(labels.includes("Entry"));
  assert.ok(labels.includes("Stop / Invalidation")); // exact shared price, one concise axis label
  assert.ok(labels.includes("T1"));
  assert.ok(!labels.includes("T2")); // null target level -> never drawn
  assert.equal(labels.filter((title) => title === "Support low").length, 0, "zones are bands, not boundary lines");
  assert.equal(labels.filter((title) => title === "Range high").length, 1);
  assert.equal(handle.lines.size, EXPECTED_LINES);
  // Deterministic values: the exact payload numbers reach the library untouched.
  assert.deepEqual(drawn(handle), [
    "Range low@98",
    "Range high@103",
    "Equal highs@102.5",
    "Entry@124",
    "Stop / Invalidation@117",
    "T1@138",
  ]);
});

test("short chart labels preserve exact numeric levels and compact collocated labels", () => {
  const handle = mockHandle();
  applyOverlays(handle, {
    overlays: {},
    scenarioBand: { low: 100.25, high: 117 },
    plan: {
      state: "PLANNABLE",
      entry: { value: "124.50" },
      stop: { value: "117" },
      invalidation: { value: "117" },
      targets: [{ level: { value: "130.75" } }, { level: { value: "135" } }],
    },
    prefs: { overlays: { planLevels: true } },
  });
  // The setup reference is a shaded band (handle.setupBand), never a full-width price line.
  assert.deepEqual(handle.setupBand, { low: 100.25, high: 117 });
  assert.deepEqual(drawn(handle), [
    "Entry@124.5",
    "Stop / Invalidation@117",
    "T1@130.75",
    "T2@135",
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
  assert.deepEqual(drawn(handle), ["Swing high@105"]);
});

test("the chart accepts the real market-candles API envelope", () => {
  const payload = {
    exchange: "binance",
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

test("first load shows the recent candles once; refreshes keep the user's zoom", () => {
  const calls = [];
  const handle = {
    chart: {
      timeScale: () => ({ setVisibleLogicalRange: (range) => calls.push(range) }),
    },
    series: { setData: () => {} },
    volume: { setData: () => {} },
  };
  const rows = Array.from({ length: 300 }, (_, i) => [1704067200000 + i * 3600000, "100", "101", "99", "100", "1"]);
  setCandles(handle, rows);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { from: 300 - 120, to: 299 + 3 });
  setCandles(handle, rows);
  assert.equal(calls.length, 1, "a refresh must not reset the view");
});

test("a short series is never given a negative default range", () => {
  const calls = [];
  const handle = {
    chart: { timeScale: () => ({ setVisibleLogicalRange: (range) => calls.push(range) }) },
    series: { setData: () => {} },
    volume: { setData: () => {} },
  };
  setCandles(handle, [[1704067200000, "100", "101", "99", "100", "1"]]);
  assert.equal(calls[0].from, 0);
});

test("price-axis labels use two decimals with thousands separators (display only)", async () => {
  const { formatPriceAxis } = await import("../../src/trading_assistant/web/static/js/chart.js");
  assert.equal(formatPriceAxis(89000), "89,000.00");
  assert.equal(formatPriceAxis(82787.25), "82,787.25");
  assert.equal(formatPriceAxis(0.5), "0.50");
  assert.equal(formatPriceAxis(NaN), "");
});


test("pattern lines share price coordinates but never contribute to autoscale", () => {
  const options = [], removed = [];
  const handle = { evidenceSeries: [], series: { setMarkers() {} }, chart: {
    addLineSeries: (opts) => { options.push(opts); return { setData(data) { this.data = data; } }; },
    removeSeries: (series) => removed.push(series),
  } };
  const patternLines = [{ points: [{ time: 1, value: 1 }, { time: 2, value: 1000000 }] }];
  setEvidence(handle, { patternLines });
  assert.equal(options[0].priceScaleId, "right");
  assert.equal(options[0].autoscaleInfoProvider(() => ({ priceRange: { minValue: 1, maxValue: 1000000 } })), null);
  assert.equal(options[0].lastValueVisible, false);
  setEvidence(handle, { patternLines: [] });
  assert.equal(removed.length, 1);
  assert.equal(handle.evidenceSeries.length, 0);
});

test("explicit reset restores recent candles and automatic price scale, not overlay data", () => {
  const ranges = [], scales = [];
  const handle = { barCount: 300, chart: {
    timeScale: () => ({ setVisibleLogicalRange: (r) => ranges.push(r) }),
    priceScale: (id) => ({ applyOptions: (o) => scales.push([id, o]) }),
  } };
  resetChartView(handle);
  assert.deepEqual(ranges, [{ from: 180, to: 302 }]);
  assert.deepEqual(scales, [["right", { autoScale: true }]]);
});

test("out-of-order and duplicate candle times are normalised without changing OHLC values", () => {
  const replacement = [...ROWS[0]];
  replacement[4] = "100.25";
  const candles = toChartCandles([ROWS[1], ROWS[0], replacement]);
  assert.equal(candles.length, 2);
  assert.equal(candles[0].close, 100.25);
  assert.ok(candles[0].time < candles[1].time);
});


test("refresh retains fractional zoom/right margin and anchors a historical pan by candle time", () => {
  const rows = Array.from({ length: 150 }, (_, i) => [1700000000000 + i * 3600000, 100, 102, 99, 101, 10]);
  let logical = { from: 35.25, to: 152.75 };
  let visible = { from: rows[36][0] / 1000, to: rows.at(-1)[0] / 1000 };
  const handle = { loadedRows: rows, lastBarTime: rows.at(-1)[0] / 1000, barCount: 150, chart: { timeScale: () => ({
    getVisibleRange: () => visible, getVisibleLogicalRange: () => logical,
    setVisibleLogicalRange: (r) => { logical = r; },
    setVisibleRange: () => { throw new Error("must preserve the exact logical view"); },
  }) } };
  const capture = captureChartView(handle);
  handle.loadedRows = [...rows, [rows.at(-1)[0] + 3600000, 101, 103, 100, 102, 11]];
  handle.barCount = 151; handle.lastBarTime += 3600;
  restoreChartView(handle, capture);
  assert.deepEqual(logical, { from: 36.25, to: 153.75 });

  logical = { from: 10.25, to: 50.75 };
  visible = { from: rows[11][0] / 1000, to: rows[50][0] / 1000 };
  const history = captureChartView(handle);
  handle.loadedRows = handle.loadedRows.slice(5); handle.barCount -= 5;
  restoreChartView(handle, history);
  assert.deepEqual(logical, { from: 5.25, to: 45.75 }, "same candles, even if loaded indices move");
});
