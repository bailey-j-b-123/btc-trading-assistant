/** Dashboard integration: the forming-candle ghost follows the configured exchange. */

import assert from "node:assert/strict";
import test from "node:test";

import { toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";
import { disposeDashboard, renderDashboard } from "../../src/trading_assistant/web/static/js/views/dashboard.js";

const AS_OF = "2026-10-06T12:00:00Z";
const SYMBOL = "BTC/USDT";
const NOW = Date.parse("2026-10-06T13:03:00Z");

const ENGINE_ROWS = [
  [Date.parse("2026-10-06T11:00:00Z"), "62000", "62120", "61920", "62080", "12.4"],
  [Date.parse("2026-10-06T12:00:00Z"), "62080", "62200", "62000", "62160", "15.2"],
];
const FIVE_MIN_ROWS = [
  [Date.parse("2026-10-06T12:55:00Z"), "62100", "62120", "62090", "62110", "3.1"],
];

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

function chartLibraryState() {
  const charts = [];
  class ResizeObserverMock {
    observe() {}
    disconnect() {}
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
  return { charts, ResizeObserverMock, library };
}

function hierarchyPayload() {
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
      { role: "context", timeframe: "4h", label: "4H CONTEXT", state: "Transition",
        tone: "warn", detail: "4H structure is in transition.", available: true,
        boundary_open: "2026-10-06T08:00:00Z", boundary_close: AS_OF },
      { role: "setup", timeframe: "1h", label: "1H SETUP", state: "Breakout / retest — watching",
        tone: "warn", detail: "A 1H breakout / retest setup (bearish) is being watched.", available: true,
        boundary_open: "2026-10-06T11:00:00Z", boundary_close: AS_OF },
      { role: "confirmation", timeframe: "15m", label: "15M CONFIRMATION", state: "Confirming",
        tone: "good", detail: "15M confirmation is confirming.", available: true,
        boundary_open: "2026-10-06T11:45:00Z", boundary_close: AS_OF },
      { role: "execution", timeframe: "5m", label: "5M EXECUTION", state: "Not armed",
        tone: "neutral", detail: "The execution layer is not armed.", available: true,
        boundary_open: "2026-10-06T11:55:00Z", boundary_close: AS_OF },
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
  };
}

function dashboardFixture(exchange, overrides = {}) {
  return {
    meta: { exchange, symbol: SYMBOL, timeframe: "1h", as_of: AS_OF },
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

function candlesPayload(timeframe, rows) {
  return {
    exchange: "binance",
    symbol: SYMBOL,
    timeframe,
    as_of: AS_OF,
    candles: rows,
    gaps: [],
    complete: true,
    returned_count: rows.length,
  };
}

function structurePayload(timeframe) {
  return {
    exchange: "binance",
    symbol: SYMBOL,
    timeframe,
    as_of: AS_OF,
    candle_count: 48,
    trend: { direction: "bullish" },
    zones: [],
    range: null,
    swings: [],
    completeness: {},
  };
}

class PublicSocket {
  static all = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; PublicSocket.all.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.closed = true; this.onclose?.(); }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

function binanceKline(timeframe, values = {}, klineValues = {}) {
  const bucket = Math.floor(NOW / (60000 * { "5m": 5, "15m": 15, "1h": 60, "4h": 240 }[timeframe])) *
    (60000 * { "5m": 5, "15m": 15, "1h": 60, "4h": 240 }[timeframe]);
  return {
    e: "kline", E: NOW, s: "BTCUSDT",
    k: {
      t: bucket, T: bucket + 60000 * { "5m": 5, "15m": 15, "1h": 60, "4h": 240 }[timeframe] - 1,
      s: "BTCUSDT", i: timeframe, f: 100, L: 109,
      o: "62200", h: "62400", l: "62100", c: "62300", v: "3", n: 6, x: false,
      q: "186900", V: "1.5", Q: "93450", B: "0",
      ...klineValues,
    },
    ...values,
  };
}

async function withDashboard(exchange, callback) {
  const keys = ["Node", "document", "window", "ResizeObserver", "getComputedStyle", "localStorage", "fetch"];
  const prior = new Map(keys.map((key) => [
    key,
    { present: Object.hasOwn(globalThis, key), value: globalThis[key] },
  ]));
  PublicSocket.all = [];
  const originalNow = Date.now;
  Date.now = () => NOW;
  const view = new MockNode("main");
  const ids = new Map([["view", view]]);
  for (const id of ["topbar-symbol", "topbar-timeframe", "topbar-candle-time", "topbar-price", "topbar-status"]) {
    ids.set(id, new MockNode("span"));
  }
  const chartState = chartLibraryState();
  const storage = new Map();
  globalThis.Node = MockNode;
  globalThis.ResizeObserver = chartState.ResizeObserverMock;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "monospace" });
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) || null,
    setItem: (key, value) => { storage.set(key, value); },
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
  globalThis.window = { LightweightCharts: chartState.library, WebSocket: PublicSocket };
  const dashboard = dashboardFixture(exchange);
  const forward = forwardFixture();
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(String(path), "http://test.invalid");
    if (url.pathname === "/api/dashboard") return { ok: true, status: 200, json: async () => dashboard };
    if (url.pathname === "/api/forward") return { ok: true, status: 200, json: async () => forward };
    if (url.pathname === "/api/market/candles") {
      const timeframe = url.searchParams.get("timeframe");
      const rows = timeframe === "5m" ? FIVE_MIN_ROWS : [];
      return { ok: true, status: 200, json: async () => candlesPayload(timeframe, rows) };
    }
    if (url.pathname === "/api/market/structure") {
      return { ok: true, status: 200, json: async () => structurePayload(url.searchParams.get("timeframe")) };
    }
    if (url.pathname === "/api/market/live-price") {
      return { ok: true, status: 200, json: async () => ({ status: "UNAVAILABLE" }) };
    }
    throw new Error(`unexpected request ${path}`);
  };
  try {
    await callback({ view, chartState, dashboard });
  } finally {
    disposeDashboard();
    Date.now = originalNow;
    for (const [key, value] of prior) {
      if (value.present) globalThis[key] = value.value;
      else delete globalThis[key];
    }
  }
}

function badge(view) {
  return findOne(view, (node) => (node.className || "").split(" ").includes("forming-status"));
}

function viewNote(view) {
  return findOne(view, (node) => (node.className || "").split(" ").includes("chart-view-note"));
}

test("a Binance dashboard draws the forming kline separately from stored closed candles", async () => {
  await withDashboard("binance", async ({ view, chartState }) => {
    await renderDashboard(view);
    const socket = PublicSocket.all.at(-1);
    assert.equal(socket.url, "wss://stream.binance.com:9443/ws/btcusdt@kline_1h");
    assert.deepEqual(socket.sent, [], "raw stream: no subscribe message");
    const stored = structuredClone(chartState.charts[0].candleData);
    assert.deepEqual(stored, toChartCandles(ENGINE_ROWS));
    socket.emit(binanceKline("1h"));
    assert.deepEqual(chartState.charts[0].formingData, [{
      time: Date.parse("2026-10-06T13:00:00Z") / 1000, open: 62200, high: 62400, low: 62100, close: 62300,
    }]);
    assert.deepEqual(chartState.charts[0].candleData, stored, "the ghost never touches confirmed rows");
    assert.match(badge(view).textContent, /FORMING 1H — DISPLAY ONLY/);
    assert.match(badge(view).textContent, /BINANCE KLINE CURRENT/);
    assert.equal(badge(view).dataset.freshness, "CURRENT");
    // A final kline is closed history: the ghost disappears, stored rows stay.
    socket.emit(binanceKline("1h", {}, { x: true }));
    assert.deepEqual(chartState.charts[0].formingData, []);
    assert.deepEqual(chartState.charts[0].candleData, stored);
    assert.match(badge(view).textContent, /BINANCE KLINE STALE/);
    assert.match(viewNote(view).textContent, /public Binance data/);
  });
});

test("a Binance dashboard re-streams per viewed timeframe and closes the previous socket", async () => {
  await withDashboard("binance", async ({ view }) => {
    await renderDashboard(view);
    const first = PublicSocket.all.at(-1);
    assert.equal(first.url, "wss://stream.binance.com:9443/ws/btcusdt@kline_1h");
    const button = findOne(view, (node) => node.getAttribute?.("data-timeframe") === "5m");
    click(button);
    await flush();
    assert.equal(first.closed, true, "the old timeframe socket is closed before switching");
    const second = PublicSocket.all.at(-1);
    assert.equal(second.url, "wss://stream.binance.com:9443/ws/btcusdt@kline_5m");
    second.emit(binanceKline("5m"));
    assert.equal(badge(view).dataset.freshness, "CURRENT");
    assert.match(badge(view).textContent, /FORMING 5M — DISPLAY ONLY · BINANCE KLINE CURRENT/);
  });
});

test("a Kraken dashboard keeps the Kraken socket and identity", async () => {
  await withDashboard("kraken", async ({ view }) => {
    await renderDashboard(view);
    const socket = PublicSocket.all.at(-1);
    assert.equal(socket.url, "wss://ws.kraken.com/v2");
    socket.onopen();
    assert.deepEqual(socket.sent.at(-1), { method: "subscribe", params: {
      channel: "ohlc", symbol: ["BTC/USDT"], interval: 60, snapshot: true,
    } });
    socket.emit({
      channel: "ohlc", type: "update", timestamp: new Date(NOW).toISOString(),
      data: [{
        symbol: SYMBOL, interval: 60, interval_begin: "2026-10-06T13:00:00.000Z",
        open: 62200, high: 62400, low: 62100, close: 62300, volume: 3, trades: 6,
      }],
    });
    assert.match(badge(view).textContent, /KRAKEN OHLC CURRENT/);
    assert.match(viewNote(view).textContent, /public Kraken data/);
  });
});

test("a dashboard missing exchange identity fails closed instead of guessing Kraken", async () => {
  await withDashboard("", async ({ view, chartState }) => {
    await renderDashboard(view);
    assert.equal(PublicSocket.all.length, 0, "unknown exchange identity never opens a venue socket");
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    assert.deepEqual(chartState.charts[0].formingData, []);
    assert.equal(badge(view).dataset.freshness, "UNAVAILABLE");
    assert.match(badge(view).textContent, /PUBLIC MARKET DATA UNAVAILABLE/);
  });
});

test("an unrecognised exchange gets no public stream and the stored chart is unchanged", async () => {
  await withDashboard("coinbase", async ({ view, chartState }) => {
    await renderDashboard(view);
    assert.equal(PublicSocket.all.length, 0, "no socket is opened for an unknown venue");
    assert.deepEqual(chartState.charts[0].candleData, toChartCandles(ENGINE_ROWS));
    assert.deepEqual(chartState.charts[0].formingData, []);
    assert.equal(badge(view).dataset.freshness, "UNAVAILABLE");
    assert.match(badge(view).textContent, /PUBLIC MARKET DATA UNAVAILABLE/);
    assert.match(badge(view).textContent, /stored chart unchanged/);
  });
});
