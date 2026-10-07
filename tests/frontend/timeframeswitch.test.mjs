/** Step 13 UI: view-only chart timeframe switching + compact hierarchy status. */

import assert from "node:assert/strict";
import test from "node:test";

import { toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";
import {
  CHART_TIMEFRAMES,
  compactHierarchyStrip,
  compactHierarchyViewModel,
  disposeDashboard,
  multiTimeframeCard,
  overlaysForViewedTimeframe,
  renderDashboard,
} from "../../src/trading_assistant/web/static/js/views/dashboard.js";

const AS_OF = "2026-10-06T12:00:00Z";
const SYMBOL = "BTC/USDT";

class MockNode {
  constructor(name, textNode = false) {
    this.tagName = textNode ? "#text" : String(name).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.value = "";
    this.open = false;
    this._text = "";
    this.listeners = new Map();
    this.clientWidth = 1200;
  }

  get clientHeight() {
    return this.className.includes("terminal-chart-wrap") ? 530 : 40;
  }

  get firstChild() { return this.children[0] || null; }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent || "").join("");
  }

  set textContent(value) {
    this._text = String(value ?? "");
    for (const child of this.children) child.parentNode = null;
    this.children = [];
  }

  append(...nodes) {
    for (const child of nodes) {
      if (child === null || child === undefined) continue;
      const node = child instanceof MockNode ? child : new MockNode("#text", true);
      if (!(child instanceof MockNode)) node._text = String(child);
      node.parentNode = this;
      this.children.push(node);
    }
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
    if (name === "open") this.open = true;
  }

  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(callback);
    this.listeners.set(name, listeners);
  }
  removeEventListener(name, callback) {
    this.listeners.set(name, (this.listeners.get(name) || []).filter((item) => item !== callback));
  }
  focus() {}
  remove() { this.parentNode?.removeChild(this); }
}

function findNodes(node, predicate, found = []) {
  if (predicate(node)) found.push(node);
  for (const child of node.children || []) findNodes(child, predicate, found);
  return found;
}

function findOne(node, predicate) {
  return findNodes(node, predicate)[0] || null;
}

function click(node) {
  for (const listener of node.listeners.get("click") || []) listener({ preventDefault() {} });
}

async function flush(rounds = 25) {
  for (let round = 0; round < rounds; round += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

const ENGINE_ROWS = [
  [1791284400000, "62000", "62120", "61920", "62080", "12.4"],
  [1791288000000, "62080", "62200", "62000", "62160", "15.2"],
];
const FIVE_MIN_ROWS = [
  [1791284700000, "62100", "62120", "62090", "62110", "3.1"],
  [1791285000000, "62110", "62140", "62100", "62130", "2.7"],
  [1791285300000, "62130", "62150", "62120", "62140", "4.0"],
];
const FIFTEEN_MIN_ROWS = [
  [1791285600000, "62010", "62060", "61990", "62040", "8.8"],
  [1791286500000, "62040", "62090", "62020", "62070", "9.1"],
];
const FOUR_H_ROWS = [
  [1791266400000, "61800", "62000", "61700", "61950", "120.5"],
  [1791280800000, "61950", "62300", "61900", "62160", "140.2"],
];

function structurePayload(timeframe, { zones = [], range = null, candleCount = 48 } = {}) {
  return {
    exchange: "kraken",
    symbol: SYMBOL,
    timeframe,
    as_of: AS_OF,
    candle_count: candleCount,
    trend: { direction: "bullish" },
    zones,
    range,
    swings: [],
    completeness: {},
  };
}

function candlesPayload(timeframe, rows) {
  return {
    exchange: "kraken",
    symbol: SYMBOL,
    timeframe,
    as_of: AS_OF,
    candles: rows,
    gaps: [],
    complete: true,
    returned_count: rows.length,
  };
}

function hierarchyPayload(overrides = {}) {
  return {
    available: true,
    hierarchy: {
      steps: [
        { role: "context", timeframe: "4h" },
        { role: "setup", timeframe: "1h" },
        { role: "confirmation", timeframe: "15m" },
        { role: "execution", timeframe: "5m" },
      ],
      timeframes: ["4h", "1h", "15m", "5m"],
      rules_version: "multi-timeframe-hierarchy-v1",
      fingerprint: "abc123",
    },
    decision_time: AS_OF,
    decision: "awaiting_confirmation",
    decision_label: "Waiting for confirmation",
    overall: "WAITING FOR CONFIRMATION",
    alignment: "aligned",
    alignment_label: "Aligned",
    counter_trend: false,
    status: "evaluated",
    ladder: [
      {
        role: "context", timeframe: "4h", label: "4H CONTEXT", state: "Transition",
        tone: "warn", detail: "4H structure is in transition.", available: true,
        boundary_open: "2026-10-06T08:00:00Z", boundary_close: AS_OF,
      },
      {
        role: "setup", timeframe: "1h", label: "1H SETUP", state: "Breakout / retest — watching",
        tone: "warn", detail: "A 1H breakout / retest setup (bearish) is being watched.", available: true,
        boundary_open: "2026-10-06T11:00:00Z", boundary_close: AS_OF,
      },
      {
        role: "confirmation", timeframe: "15m", label: "15M CONFIRMATION", state: "Confirming",
        tone: "good", detail: "15M confirmation is confirming.", available: true,
        boundary_open: "2026-10-06T11:45:00Z", boundary_close: AS_OF,
      },
      {
        role: "execution", timeframe: "5m", label: "5M EXECUTION", state: "Not armed",
        tone: "neutral", detail: "The execution layer is not armed.", available: true,
        boundary_open: "2026-10-06T11:55:00Z", boundary_close: AS_OF,
      },
    ],
    waiting_for: ["15M acceptance of the setup reference level"],
    waiting_for_text: "Waiting for: 15M acceptance of the setup reference level",
    invalidated_if: ["a closed candle below the reference level"],
    invalidated_if_text: "Invalidated if: a closed candle below the reference level",
    reasons: ["confirmation_waiting"],
    explanation: { headline: "WAITING FOR CONFIRMATION", sentences: [] },
    snapshot: {},
    latest_recorded: null,
    limitations: ["The hierarchy is decision support only."],
    ...overrides,
  };
}

function dashboardFixture(overrides = {}) {
  return {
    meta: { exchange: "kraken", symbol: SYMBOL, timeframe: "1h", as_of: AS_OF },
    market: {
      candles: ENGINE_ROWS,
      latest_closed_candle: {
        timestamp: AS_OF, open: "62080", high: "62200", low: "62000", close: "62160", volume: "15.2",
      },
      complete: true,
      missing_candle_count: 0,
    },
    freshness: { status: "CURRENT", latest_stored: AS_OF, expected_latest_closed: AS_OF, staleness_intervals: 0 },
    qualification: {
      available: true, state: "WATCH", reasons: [], selected_setup_id: null,
      setups: [], snapshot: { setups: [] },
    },
    planning: { state: null, reasons: [], missing_inputs: [], state_detail: null },
    plan: null,
    overlays: {
      zones: [{ role: "support", band_low: "61900", band_high: "62000", center: "61950", touch_count: 3 }],
      range: null,
      equal_levels: [],
      swings: [],
      setup_reference: null,
    },
    market_state: { available: false, reason: "no Step 3/4 frame could be built at this boundary" },
    scenario: { available: false, reason: "no evaluated snapshot at this boundary" },
    explanation: { available: false, error: { code: "no_snapshot", message: "No snapshot to explain." } },
    journal: {
      journal_id: null, latest_decision: null, decision_history_count: 0,
      can_decide: false, disabled_reason: "Evidence is still developing; no qualified proposal to decide.",
    },
    multi_timeframe: hierarchyPayload(),
    ...overrides,
  };
}

function forwardFixture() {
  return {
    disclaimer: "Paper trading and historical performance do not establish future profitability.",
    status: {
      market_data: {
        data_health: "CURRENT",
        latest_stored_candle_open: AS_OF,
        expected_latest_closed_candle_open: AS_OF,
        missing_candle_count: 0,
      },
      runner: { status: "IDLE", recorded_at: AS_OF, latest_cycle_as_of: AS_OF, pending_boundaries: 0, last_error: null },
      sample: { paper_plans: 0, pending_catch_up_boundaries: 0 },
      current_state: { available: true, as_of: AS_OF, setup_state_counts: {}, plan_state_counts: {} },
    },
    observations: { observations: [] },
    report: { combined_metrics_available: true, metrics: { paper_plan_count: 0 }, version_cohorts: [] },
  };
}

function chartLibraryState() {
  const charts = [];
  const observers = [];
  class ResizeObserverMock {
    constructor(callback) { this.callback = callback; this.disconnected = false; observers.push(this); }
    observe() {}
    disconnect() { this.disconnected = true; }
  }
  const library = {
    createChart: () => {
      const record = { removed: false, candleData: [], formingData: [], volumeData: [], lines: new Set() };
      const series = {
        setData: (rows) => { record.candleData = rows; },
        createPriceLine: (options) => {
          const line = { options };
          record.lines.add(line);
          return line;
        },
        removePriceLine: (line) => record.lines.delete(line),
      };
      const forming = { setData: (rows) => { record.formingData = rows; } };
      let candleSeriesCount = 0;
      const volume = { setData: (rows) => { record.volumeData = rows; } };
      charts.push(record);
      return {
        addCandlestickSeries: () => candleSeriesCount++ === 0 ? series : forming,
        addHistogramSeries: () => volume,
        priceScale: () => ({ applyOptions: () => {} }),
        resize: () => {},
        remove: () => { record.removed = true; },
      };
    },
  };
  return { charts, observers, ResizeObserverMock, library };
}

async function withDashboard({ dashboard, forward, market = {}, failCandles = [], failStructure = [], livePrice = null, WebSocketImpl = null }, callback) {
  const keys = ["Node", "document", "window", "ResizeObserver", "getComputedStyle", "localStorage", "fetch"];
  const prior = new Map(keys.map((key) => [
    key,
    { present: Object.hasOwn(globalThis, key), value: globalThis[key] },
  ]));
  const view = new MockNode("main");
  const ids = new Map([["view", view]]);
  for (const id of ["topbar-symbol", "topbar-timeframe", "topbar-candle-time", "topbar-price", "topbar-status"]) {
    ids.set(id, new MockNode("span"));
  }
  const chartState = chartLibraryState();
  const calls = [];
  const prefWrites = [];
  const candlesByTimeframe = {
    "5m": candlesPayload("5m", FIVE_MIN_ROWS),
    "15m": candlesPayload("15m", FIFTEEN_MIN_ROWS),
    "4h": candlesPayload("4h", FOUR_H_ROWS),
    ...(market.candles || {}),
  };
  const structureByTimeframe = {
    "5m": structurePayload("5m", {
      zones: [{ role: "resistance", band_low: "62150", band_high: "62200", center: "62175", touch_count: 2 }],
    }),
    "15m": structurePayload("15m", {
      zones: [{ role: "support", band_low: "61950", band_high: "62000", center: "61975", touch_count: 2 }],
    }),
    "4h": structurePayload("4h", {
      zones: [],
      range: { range_low: "61000", range_high: "63000", active: true },
    }),
    ...(market.structure || {}),
  };
  globalThis.Node = MockNode;
  globalThis.ResizeObserver = chartState.ResizeObserverMock;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "monospace" });
  const storage = new Map();
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) || null,
    setItem: (key, value) => { storage.set(key, value); prefWrites.push([key, value]); },
  };
  globalThis.document = {
    documentElement: {},
    getElementById: (id) => ids.get(id) || null,
    createElement: (name) => new MockNode(name),
    createTextNode: (text) => {
      const node = new MockNode("#text", true);
      node._text = String(text);
      return node;
    },
  };
  globalThis.window = { LightweightCharts: chartState.library, WebSocket: WebSocketImpl };
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path: String(path), method: options.method || "GET" });
    const url = new URL(String(path), "http://test.invalid");
    if (url.pathname === "/api/dashboard") return { ok: true, status: 200, json: async () => dashboard };
    if (url.pathname === "/api/forward") return { ok: true, status: 200, json: async () => forward };
    if (url.pathname === "/api/market/live-price") {
      return { ok: true, status: 200, json: async () => livePrice || { status: "UNAVAILABLE" } };
    }
    if (url.pathname === "/api/market/candles") {
      const timeframe = url.searchParams.get("timeframe");
      if (failCandles.includes(timeframe)) {
        return { ok: false, status: 500, json: async () => ({ error: { code: "boom", message: "candles exploded" } }) };
      }
      const payload = candlesByTimeframe[timeframe];
      if (!payload) return { ok: false, status: 400, json: async () => ({ error: { code: "validation_error", message: "unsupported" } }) };
      return { ok: true, status: 200, json: async () => payload };
    }
    if (url.pathname === "/api/market/structure") {
      const timeframe = url.searchParams.get("timeframe");
      if (failStructure.includes(timeframe)) {
        return { ok: false, status: 500, json: async () => ({ error: { code: "boom", message: "structure exploded" } }) };
      }
      const payload = structureByTimeframe[timeframe];
      if (!payload) return { ok: false, status: 400, json: async () => ({ error: { code: "validation_error", message: "unsupported" } }) };
      return { ok: true, status: 200, json: async () => payload };
    }
    throw new Error(`unexpected request ${path}`);
  };

  try {
    await callback({ view, ids, chartState, calls, prefWrites });
  } finally {
    disposeDashboard();
    for (const [key, value] of prior) {
      if (value.present) globalThis[key] = value.value;
      else delete globalThis[key];
    }
  }
}

function switchButtons(view) {
  return findNodes(view, (node) => node.tagName === "BUTTON" && node.getAttribute("data-timeframe"));
}

function headingTitle(view) {
  return findOne(view, (node) => (node.className || "").split(" ").includes("chart-heading-title"));
}

function viewNote(view) {
  return findOne(view, (node) => (node.className || "").split(" ").includes("chart-view-note"));
}

function stripNode(view) {
  return findOne(view, (node) => node.getAttribute("aria-label") === "Multi-timeframe status");
}

function drawnLines(chartState) {
  return [...chartState.charts[0].lines].map((line) => `${line.options.title}@${line.options.price}`);
}

function marketCalls(calls, endpoint, timeframe) {
  return calls.filter((call) => {
    const url = new URL(call.path, "http://test.invalid");
    return url.pathname === endpoint && url.searchParams.get("timeframe") === timeframe;
  });
}

// ---------------------------------------------------------------------------
// Switcher controls
// ---------------------------------------------------------------------------

test("the chart offers 5m, 15m, 1H and 4H view-only controls", async () => {
  assert.deepEqual(CHART_TIMEFRAMES.map((entry) => entry.id), ["5m", "15m", "1h", "4h"]);
  assert.deepEqual(CHART_TIMEFRAMES.map((entry) => entry.label), ["5M", "15M", "1H", "4H"]);
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view }) => {
    await renderDashboard(view);
    const buttons = switchButtons(view);
    assert.deepEqual(buttons.map((button) => button.getAttribute("data-timeframe")), ["5m", "15m", "1h", "4h"]);
    assert.deepEqual(buttons.map((button) => button.textContent), ["5M", "15M", "1H", "4H"]);
    for (const button of buttons) {
      assert.match(button.getAttribute("aria-label"), /view only/);
    }
    const group = findOne(view, (node) => (node.className || "").split(" ").includes("chart-timeframe-switch"));
    assert.ok(group);
    assert.match(group.getAttribute("aria-label"), /view only/);
    // The engine timeframe (1h) starts selected.
    assert.deepEqual(buttons.map((button) => button.getAttribute("aria-pressed")), ["false", "false", "true", "false"]);
  });
});

test("selecting 5m requests and renders stored 5m candles and 5m structure", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, calls, chartState }) => {
    await renderDashboard(view);
    const before = calls.length;
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
    await flush();
    const candleCalls = marketCalls(calls.slice(before), "/api/market/candles", "5m");
    const structureCalls = marketCalls(calls.slice(before), "/api/market/structure", "5m");
    assert.equal(candleCalls.length, 1);
    assert.equal(structureCalls.length, 1);
    // Both reads are pinned to the dashboard decision instant — the viewed
    // chart can never run ahead of the verdict.
    for (const call of [...candleCalls, ...structureCalls]) {
      const url = new URL(call.path, "http://test.invalid");
      assert.equal(url.searchParams.get("symbol"), SYMBOL);
      assert.equal(url.searchParams.get("end_time") || url.searchParams.get("as_of"), AS_OF);
    }
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(FIVE_MIN_ROWS));
    assert.equal(headingTitle(view).textContent, `${SYMBOL} · 5M chart`);
    assert.match(viewNote(view).textContent, /5M stored closed candles/);
    // Diagnostic zones stay hidden by default even on the viewed timeframe.
    assert.deepEqual(drawnLines(chartState), []);
  });
});

test("selecting 15m requests and renders stored 15m candles and 15m structure", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, calls, chartState }) => {
    await renderDashboard(view);
    const before = calls.length;
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "15m"));
    await flush();
    assert.equal(marketCalls(calls.slice(before), "/api/market/candles", "15m").length, 1);
    assert.equal(marketCalls(calls.slice(before), "/api/market/structure", "15m").length, 1);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(FIFTEEN_MIN_ROWS));
    assert.equal(headingTitle(view).textContent, `${SYMBOL} · 15M chart`);
    assert.deepEqual(drawnLines(chartState), []);
  });
});

test("selecting 4H requests and renders stored 4h candles and 4h structure", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, calls, chartState }) => {
    await renderDashboard(view);
    const before = calls.length;
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "4h"));
    await flush();
    assert.equal(marketCalls(calls.slice(before), "/api/market/candles", "4h").length, 1);
    assert.equal(marketCalls(calls.slice(before), "/api/market/structure", "4h").length, 1);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(FOUR_H_ROWS));
    assert.equal(headingTitle(view).textContent, `${SYMBOL} · 4H chart`);
    assert.deepEqual(drawnLines(chartState), []);
  });
});

test("selecting 1H restores the engine snapshot and its exact overlays", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, chartState }) => {
    await renderDashboard(view);
    const initialCandles = chartState.charts[0].candleData;
    const initialLines = drawnLines(chartState);
    assert.deepEqual(initialLines, []);

    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
    await flush();
    assert.deepEqual(drawnLines(chartState), []);

    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "1h"));
    await flush();
    // The 1h data rendered is the dashboard's own stored snapshot — the same
    // rows the verdict was computed from — with its exact overlays back.
    assert.deepEqual(chartState.charts[0].candleData, initialCandles);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    assert.deepEqual(drawnLines(chartState), initialLines);
    assert.equal(headingTitle(view).textContent, `${SYMBOL} · 1H chart`);
  });
});

test("switching clears the setup scenario before new data arrives", async () => {
  const dashboard = dashboardFixture({ looking_for: {
    available: true, timeframe: "1h", setup_id: "only-watch", family: "breakout_retest_continuation",
    direction: "bullish", state: "WATCH", seed_event: { kind: "breakout" },
    reference: { band_low: "61900", band_high: "62000" }, pending_required: [], invalidation: null,
  } });
  await withDashboard({ dashboard, forward: forwardFixture() }, async ({ view, chartState }) => {
    await renderDashboard(view);
    assert.deepEqual(drawnLines(chartState), [
      "scenario reference low · not prediction@61900",
      "scenario reference high · not prediction@62000",
    ]);
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
    // Synchronously after the click — before any response lands — the
    // previous timeframe's levels are already gone.
    assert.equal(chartState.charts[0].lines.size, 0);
    await flush();
    assert.deepEqual(drawnLines(chartState), []);
  });
});

test("plan levels persist across chart switches, governed by the trade plan", async () => {
  const dashboard = dashboardFixture({
    qualification: {
      available: true, state: "QUALIFIED", reasons: [], selected_setup_id: "setup-1",
      setups: [{ id: "setup-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish" }],
      snapshot: { setups: [{ id: "setup-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish", rules: [] }] },
    },
    planning: { state: "PLANNABLE", reasons: [], missing_inputs: [], state_detail: null },
    plan: {
      state: "PLANNABLE", direction: "bullish", family: "breakout_retest_continuation",
      entry: { value: "62250" }, stop: { value: "62050" }, invalidation: { value: "62050" },
      risk_per_unit: "200", targets: [{ level: { value: "62600" }, r_multiple: "1.75" }],
    },
  });
  await withDashboard({ dashboard, forward: forwardFixture() }, async ({ view, chartState }) => {
    await renderDashboard(view);
    assert.deepEqual(drawnLines(chartState), [
      "entry@62250",
      "protective stop@62050",
      "target 1@62600",
    ]);
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
    await flush();
    // Structure belongs to 5m; the plan lines are unchanged in price and
    // title because they come from the deterministic trade plan.
    assert.deepEqual(drawnLines(chartState), [
      "entry@62250",
      "protective stop@62050",
      "target 1@62600",
    ]);
    assert.match(viewNote(view).textContent, /plan levels from the engine plan/);
  });
});

test("switching chart timeframe cannot alter hierarchy, verdict, plan, or journal state", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, calls, ids, prefWrites }) => {
    await renderDashboard(view);
    const stripBefore = stripNode(view).textContent;
    const verdictBefore = findOne(view, (node) => (node.className || "").split(" ").includes("verdict-card")).textContent;
    const planBefore = findOne(view, (node) => (node.className || "").split(" ").includes("plan-card")).textContent;
    const topbarBefore = [
      ids.get("topbar-symbol").textContent,
      ids.get("topbar-timeframe").textContent,
      ids.get("topbar-candle-time").textContent,
      ids.get("topbar-price").textContent,
      ids.get("topbar-status").textContent,
    ];
    const dashboardCallsBefore = calls.filter((call) => call.path.startsWith("/api/dashboard")).length;

    for (const timeframe of ["5m", "15m", "4h", "1h"]) {
      click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === timeframe));
      await flush();
    }

    assert.equal(stripNode(view).textContent, stripBefore);
    assert.equal(
      findOne(view, (node) => (node.className || "").split(" ").includes("verdict-card")).textContent,
      verdictBefore,
    );
    assert.equal(
      findOne(view, (node) => (node.className || "").split(" ").includes("plan-card")).textContent,
      planBefore,
    );
    assert.deepEqual(
      [
        ids.get("topbar-symbol").textContent,
        ids.get("topbar-timeframe").textContent,
        ids.get("topbar-candle-time").textContent,
        ids.get("topbar-price").textContent,
        ids.get("topbar-status").textContent,
      ],
      topbarBefore,
    );
    // View-only proof: only GETs, no dashboard refetch, no decision POST,
    // and no preference write (the engine timeframe preference is untouched).
    assert.ok(calls.every((call) => call.method === "GET"));
    assert.equal(calls.filter((call) => call.path.startsWith("/api/dashboard")).length, dashboardCallsBefore);
    assert.ok(calls.every((call) => !call.path.includes("/decisions")));
    assert.equal(prefWrites.length, 0);
  });
});

// ---------------------------------------------------------------------------
// Compact hierarchy status
// ---------------------------------------------------------------------------

test("compactHierarchyViewModel projects the backend payload without recomputing", () => {
  const model = compactHierarchyViewModel({ multi_timeframe: hierarchyPayload() });
  assert.equal(model.available, true);
  assert.deepEqual(model.rows.map((row) => row.label), ["4H CONTEXT", "1H SETUP", "15M CONFIRMATION", "5M EXECUTION"]);
  assert.deepEqual(
    model.rows.map((row) => row.state),
    ["Transition", "Breakout / retest — watching", "Confirming", "Not armed"],
  );
  assert.equal(model.overall, "WAITING FOR CONFIRMATION");
  assert.equal(model.decisionLabel, "Waiting for confirmation");
  assert.equal(model.alignmentLabel, "Aligned");
  assert.equal(model.counterTrend, false);
  assert.equal(model.waitingForText, "Waiting for: 15M acceptance of the setup reference level");

  // Pure projection: verbatim backend strings pass through untouched,
  // including unfamiliar ones the frontend has no logic for.
  const custom = compactHierarchyViewModel({
    multi_timeframe: hierarchyPayload({
      overall: "PURPLE OVERALL",
      ladder: hierarchyPayload().ladder.map((row) => ({ ...row, state: `custom ${row.role}` })),
    }),
  });
  assert.equal(custom.overall, "PURPLE OVERALL");
  assert.deepEqual(
    custom.rows.map((row) => row.state),
    ["custom context", "custom setup", "custom confirmation", "custom execution"],
  );

  const unavailable = compactHierarchyViewModel({ multi_timeframe: { available: false } });
  assert.equal(unavailable.available, false);
  assert.ok(unavailable.reason.length > 0);
});

test("the compact strip sits next to the chart and shows every engine role", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view }) => {
    await renderDashboard(view);
    const stack = findOne(view, (node) => (node.className || "").split(" ").includes("chart-stack"));
    assert.ok(stack);
    assert.equal(stack.children[1].getAttribute("aria-label"), "Multi-timeframe status");
    const strip = stripNode(view);
    assert.ok(strip);
    const text = strip.textContent;
    for (const expected of [
      "Multi-timeframe status",
      "engine hierarchy",
      "read-only",
      "4H CONTEXT",
      "Transition",
      "1H SETUP",
      "Breakout / retest — watching",
      "15M CONFIRMATION",
      "Confirming",
      "5M EXECUTION",
      "Not armed",
      "OVERALL",
      "WAITING FOR CONFIRMATION",
      "Waiting for confirmation",
      "Aligned",
      "Waiting for: 15M acceptance of the setup reference level",
    ]) {
      assert.ok(text.includes(expected), `strip should contain ${expected}`);
    }
    const rows = findNodes(strip, (node) => (node.className || "").split(" ").includes("mtf-compact-row"));
    assert.equal(rows.length, 4);
    assert.deepEqual(rows.map((row) => row.dataset.tone), ["warn", "warn", "good", "neutral"]);
  });
});

test("the compact strip reports an unavailable hierarchy honestly", async () => {
  const dashboard = dashboardFixture({
    multi_timeframe: {
      available: false,
      error: { type: "HierarchyNotConfigured", message: "timeframe 5m is not supported" },
      limitations: [],
    },
  });
  await withDashboard({ dashboard, forward: forwardFixture() }, async ({ view }) => {
    await renderDashboard(view);
    const strip = stripNode(view);
    assert.ok(strip);
    assert.ok(strip.textContent.includes("timeframe 5m is not supported"));
    assert.equal(findNodes(strip, (node) => (node.className || "").split(" ").includes("mtf-compact-row")).length, 0);
  });
});

test("the detailed ladder stays available but collapsed", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view }) => {
    await renderDashboard(view);
    const ladder = findOne(view, (node) => node.getAttribute("aria-label") === "Multi-timeframe ladder");
    assert.ok(ladder);
    assert.equal(ladder.tagName, "DETAILS");
    assert.equal(ladder.open, false);
    assert.ok(ladder.textContent.includes("technical details"));
    assert.ok(ladder.textContent.includes("4H CONTEXT"));
    assert.ok(ladder.textContent.includes("OVERALL"));
  });
});

// ---------------------------------------------------------------------------
// Overlay mapping across timeframes
// ---------------------------------------------------------------------------

test("overlaysForViewedTimeframe keeps engine overlays only on the engine view", () => {
  const dashboard = dashboardFixture();
  assert.equal(
    overlaysForViewedTimeframe({ dashboard, viewedTimeframe: "1h" }),
    dashboard.overlays,
  );
  const mapped = overlaysForViewedTimeframe({
    dashboard,
    viewedTimeframe: "5m",
    structure: structurePayload("5m", {
      zones: [{ role: "resistance", band_low: "62150", band_high: "62200" }],
      range: { range_low: "61000", range_high: "63000" },
    }),
  });
  assert.deepEqual(mapped.zones, [{ role: "resistance", band_low: "62150", band_high: "62200" }]);
  assert.deepEqual(mapped.range, { range_low: "61000", range_high: "63000" });
  assert.deepEqual(mapped.equal_levels, []);
  assert.equal(mapped.setup_reference, null);
  // Missing or mismatched structure never falls back to another timeframe.
  assert.deepEqual(
    overlaysForViewedTimeframe({ dashboard, viewedTimeframe: "5m", structure: null }),
    { equal_levels: [], zones: [], range: null, swings: [], setup_reference: null },
  );
  assert.deepEqual(
    overlaysForViewedTimeframe({ dashboard, viewedTimeframe: "5m", structure: structurePayload("1h") }),
    { equal_levels: [], zones: [], range: null, swings: [], setup_reference: null },
  );
});

test("the liquidity toggle is disabled off the engine timeframe with an honest reason", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view }) => {
    await renderDashboard(view);
    const liquidity = () => findOne(view, (node) => node.getAttribute("data-overlay") === "equalLevels");
    assert.equal(Boolean(liquidity().disabled), false);
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
    await flush();
    assert.equal(liquidity().disabled, true);
    assert.match(liquidity().title, /only available on the engine timeframe/);
    click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "1h"));
    await flush();
    assert.equal(Boolean(liquidity().disabled), false);
  });
});

// ---------------------------------------------------------------------------
// Honest unavailable states
// ---------------------------------------------------------------------------

test("a timeframe with no stored candles renders honestly empty, never fabricated", async () => {
  await withDashboard(
    {
      dashboard: dashboardFixture(),
      forward: forwardFixture(),
      market: { candles: { "4h": candlesPayload("4h", []) } },
    },
    async ({ view, chartState }) => {
      await renderDashboard(view);
      assert.equal(chartState.charts[0].candleData.length, 2);
      click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "4h"));
      await flush();
      assert.deepEqual(chartState.charts[0].candleData, []);
      assert.equal(chartState.charts[0].lines.size, 0);
      const empty = findOne(view, (node) => (node.className || "").split(" ").includes("chart-empty"));
      assert.ok(empty);
      assert.ok(empty.textContent.includes("4H data unavailable"));
      assert.ok(empty.textContent.includes("No substitute data is shown"));
      assert.match(viewNote(view).textContent, /honestly empty/);
      assert.equal(headingTitle(view).textContent, `${SYMBOL} · 4H chart`);
    },
  );
});

test("a candles failure leaves the chart empty with the backend message", async () => {
  await withDashboard(
    { dashboard: dashboardFixture(), forward: forwardFixture(), failCandles: ["5m"] },
    async ({ view, chartState }) => {
      await renderDashboard(view);
      click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
      await flush();
      assert.deepEqual(chartState.charts[0].candleData, []);
      const empty = findOne(view, (node) => (node.className || "").split(" ").includes("chart-empty"));
      assert.ok(empty.textContent.includes("5M chart unavailable"));
      assert.ok(empty.textContent.includes("candles exploded"));
    },
  );
});

test("a structure failure still shows candles but hides levels honestly", async () => {
  await withDashboard(
    { dashboard: dashboardFixture(), forward: forwardFixture(), failStructure: ["15m"] },
    async ({ view, chartState }) => {
      await renderDashboard(view);
      click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "15m"));
      await flush();
      assert.deepEqual(chartState.charts[0].candleData, toChartCandles(FIFTEEN_MIN_ROWS));
      assert.equal(chartState.charts[0].lines.size, 0);
      assert.match(viewNote(view).textContent, /structure unavailable/);
      assert.match(viewNote(view).textContent, /levels hidden, candles only/);
    },
  );
});

test("mismatched backend timeframes are never rendered as the requested view", async () => {
  await withDashboard(
    {
      dashboard: dashboardFixture(),
      forward: forwardFixture(),
      market: { candles: { "5m": candlesPayload("1h", ENGINE_ROWS) } },
    },
    async ({ view, chartState }) => {
      await renderDashboard(view);
      click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === "5m"));
      await flush();
      // The 1h rows are refused: the 5m chart must not show another
      // timeframe's candles under a 5m heading.
      assert.deepEqual(chartState.charts[0].candleData, []);
      const empty = findOne(view, (node) => (node.className || "").split(" ").includes("chart-empty"));
      assert.ok(empty.textContent.includes("it is not shown"));
    },
  );
});

// ---------------------------------------------------------------------------
// Existing behaviour preserved
// ---------------------------------------------------------------------------

test("existing dashboard behaviour still works alongside the new UI", async () => {
  await withDashboard({ dashboard: dashboardFixture(), forward: forwardFixture() }, async ({ view, ids, chartState }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /Watching/);
    assert.match(view.textContent, /No qualified trade plan right now\./);
    assert.match(view.textContent, /Evidence/);
    assert.match(view.textContent, /No market frame could be built at this point\./);
    assert.match(view.textContent, /No snapshot to explain\./);
    assert.equal(ids.get("topbar-symbol").textContent, SYMBOL);
    assert.equal(ids.get("topbar-timeframe").textContent, "1H");
    assert.equal(ids.get("topbar-status").textContent, "SYSTEM OK");
    assert.equal(chartState.charts.length, 1);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    // The collapsed ladder still carries the full audit trail.
    const ladder = multiTimeframeCard(dashboardFixture());
    assert.equal(ladder.tagName, "DETAILS");
    assert.ok(ladder.textContent.includes("Invalidated if:"));
    const strip = compactHierarchyStrip(dashboardFixture());
    assert.equal(strip.getAttribute("aria-label"), "Multi-timeframe status");
  });
});


test("stale public quote is visibly stale and never changes stored candles or chart decision", async () => {
  const dashboard = dashboardFixture();
  const original = structuredClone(dashboard);
  const stale = { status: "STALE", price: "90000", fetched_at: new Date(Date.now() - 60000).toISOString() };
  await withDashboard({ dashboard, forward: forwardFixture(), livePrice: stale }, async ({ view, chartState, calls }) => {
    await renderDashboard(view);
    await flush();
    const quote = findOne(view, (node) => (node.className || "").split(" ").includes("live-quote"));
    assert.equal(quote.dataset.freshness, "STALE");
    assert.match(quote.textContent, /LIVE DATA STALE/);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    assert.deepEqual(dashboard, original);
    assert.equal(calls.filter((call) => call.path.startsWith("/api/market/live-price")).length, 1);
    assert.equal(calls.filter((call) => call.method !== "GET").length, 0);
  });
});

test("manual S/R toggle reveals the stored structure without changing setup or hierarchy", async () => {
  const dashboard = dashboardFixture();
  await withDashboard({ dashboard, forward: forwardFixture() }, async ({ view, chartState, prefWrites }) => {
    await renderDashboard(view);
    assert.deepEqual(drawnLines(chartState), []);
    const button = findOne(view, (node) => node.getAttribute("data-overlay") === "zones");
    assert.equal(button.getAttribute("aria-pressed"), "false");
    click(button);
    assert.equal(button.getAttribute("aria-pressed"), "true");
    assert.deepEqual(drawnLines(chartState), ["support low@61900", "support high@62000"]);
    assert.equal(prefWrites.length, 1);
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    assert.equal(dashboard.qualification.state, "WATCH");
    assert.equal(dashboard.multi_timeframe.decision, "awaiting_confirmation");
  });
});


test("public forming OHLC follows 5M/15M/1H/4H chart view only, never the engine or stored series", async () => {
  const originalNow = Date.now;
  const now = Date.parse("2026-10-06T13:03:00Z");
  Date.now = () => now;
  const intervals = { "5m": 5, "15m": 15, "1h": 60, "4h": 240 };
  const buckets = { "5m": "2026-10-06T13:00:00Z", "15m": "2026-10-06T13:00:00Z",
    "1h": "2026-10-06T13:00:00Z", "4h": "2026-10-06T12:00:00Z" };
  class PublicSocket {
    static all = [];
    constructor(url) { this.url = url; this.sent = []; this.closed = false; PublicSocket.all.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { this.closed = true; this.onclose?.(); }
    emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
  }
  const engineRows = [[Date.parse("2026-10-06T11:00:00Z"), "100", "102", "99", "101", "5"],
    [Date.parse("2026-10-06T12:00:00Z"), "101", "104", "100", "103", "6"]];
  const rows = {
    "5m": [[Date.parse("2026-10-06T12:55:00Z"), "101", "103", "100", "102", "1"]],
    "15m": [[Date.parse("2026-10-06T12:45:00Z"), "101", "103", "100", "102", "2"]],
    "4h": [[Date.parse("2026-10-06T08:00:00Z"), "101", "105", "99", "103", "8"]],
  };
  const fixture = dashboardFixture({
    meta: { exchange: "kraken", symbol: SYMBOL, timeframe: "1h", as_of: "2026-10-06T13:00:00Z" },
    market: { candles: engineRows, latest_closed_candle: { close: "103" } },
    qualification: { available: true, state: "QUALIFIED", reasons: [], selected_setup_id: "fixed-setup",
      setups: [{ id: "fixed-setup", state: "QUALIFIED", direction: "bullish" }], snapshot: { setups: [] } },
    planning: { state: "PLANNABLE", reasons: [] },
    plan: { state: "PLANNABLE", setup_id: "fixed-setup", entry: { value: "110" },
      stop: { value: "95" }, invalidation: { value: "95" }, targets: [{ level: { value: "125" } }] },
  });
  const before = structuredClone(fixture);
  try {
    await withDashboard({ dashboard: fixture, forward: forwardFixture(), WebSocketImpl: PublicSocket,
      market: { candles: Object.fromEntries(Object.entries(rows).map(([tf, data]) => [tf, candlesPayload(tf, data)])) },
    }, async ({ view, chartState, calls }) => {
      await renderDashboard(view);
      for (const tf of ["1h", "5m", "15m", "4h"]) {
        if (tf !== "1h") {
          const previous = PublicSocket.all.at(-1);
          click(switchButtons(view).find((button) => button.getAttribute("data-timeframe") === tf));
          assert.equal(previous.closed, true);
          assert.deepEqual(chartState.charts[0].formingData, []);
          await flush();
        }
        const socket = PublicSocket.all.at(-1);
        socket.onopen();
        assert.equal(socket.sent.at(-1).params.interval, intervals[tf]);
        assert.equal(socket.sent.at(-1).params.symbol[0], "BTC/USDT");
        const stored = tf === "1h" ? engineRows : rows[tf];
        const original = structuredClone(toChartCandles(stored));
        assert.deepEqual(chartState.charts[0].candleData, original);
        socket.emit({ channel: "ohlc", type: "update", timestamp: new Date(now).toISOString(), data: [{
          symbol: SYMBOL, interval: intervals[tf], interval_begin: buckets[tf],
          open: 104, high: 108, low: 102, close: 106, volume: 3, trades: 6,
        }] });
        assert.deepEqual(chartState.charts[0].formingData, [{
          time: Date.parse(buckets[tf]) / 1000, open: 104, high: 108, low: 102, close: 106,
        }]);
        assert.deepEqual(chartState.charts[0].candleData, original);
        const badge = findOne(view, (node) => (node.className || "").split(" ").includes("forming-status"));
        assert.match(badge.textContent, /FORMING .*DISPLAY ONLY/);
        assert.equal(badge.dataset.freshness, "CURRENT");
      }
      PublicSocket.all.at(-1).close();
      assert.deepEqual(chartState.charts[0].formingData, []);
      assert.deepEqual(chartState.charts[0].candleData, toChartCandles(rows["4h"]));
      assert.deepEqual(fixture, before);
      assert.deepEqual(drawnLines(chartState), ["entry@110", "protective stop@95", "target 1@125"]);
      assert.equal(calls.filter((c) => c.path.startsWith("/api/dashboard")).length, 1);
      assert.equal(calls.filter((c) => c.method !== "GET").length, 0);
    });
  } finally { Date.now = originalNow; }
});
