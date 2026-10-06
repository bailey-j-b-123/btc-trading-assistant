import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  disposeDashboard,
  hasValidTradePlan,
  performanceViewModel,
  renderDashboard,
  systemHealthViewModel,
  verdictViewModel,
} from "../../src/trading_assistant/web/static/js/views/dashboard.js";

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

function backendDashboard(overrides = {}) {
  const timestamp = "2026-10-06T12:00:00Z";
  const setup = {
    id: "setup-watch-1",
    state: "WATCH",
    family: "range_rejection_reversal",
    direction: "bearish",
    rules: [
      {
        rule_id: "confirmation_event",
        outcome: "pending",
        reason: "A confirming close has not been recorded.",
        evidence: [
          {
            status: "supportive",
            category: "liquidity sweep",
            timeframe: "1h",
            reason: "A sell-side sweep was recorded.",
          },
        ],
      },
    ],
  };
  return {
    meta: { exchange: "kraken", symbol: "BTC/USDT", timeframe: "1h", as_of: timestamp },
    market: {
      candles: [
        [1791284400000, "62000", "62120", "61920", "62080", "12.4"],
        [1791288000000, "62080", "62200", "62000", "62160", "15.2"],
      ],
      latest_closed_candle: {
        timestamp,
        open: "62080",
        high: "62200",
        low: "62000",
        close: "62160",
        volume: "15.2",
      },
      complete: true,
      missing_candle_count: 0,
    },
    freshness: {
      status: "CURRENT",
      latest_stored: timestamp,
      expected_latest_closed: timestamp,
      staleness_intervals: 0,
    },
    qualification: {
      available: true,
      state: "WATCH",
      reasons: [],
      selected_setup_id: null,
      setups: [{ id: setup.id, state: setup.state, family: setup.family, direction: setup.direction }],
      snapshot: { setups: [setup] },
    },
    planning: { state: null, reasons: [], missing_inputs: [], state_detail: null },
    plan: null,
    overlays: {
      zones: [{ role: "support", band_low: "61900", band_high: "62000" }],
      range: null,
      equal_levels: [],
      swings: [],
      setup_reference: null,
    },
    market_state: {
      available: true,
      as_of: timestamp,
      trend: {
        direction: "bullish",
        reason: "higher_highs_and_higher_lows",
        sufficient: true,
        swing_highs: 2,
        swing_lows: 2,
        higher_highs: true,
        higher_lows: true,
        lower_highs: false,
        lower_lows: false,
        transition: "unchanged",
        momentum: "steady",
      },
      volatility: {
        available: true,
        reason: null,
        period: 14,
        atr: "1710.5",
        atr_percent_of_price: "2.75345715",
        direction: { label: "contracting", previous: "3.10000000" },
      },
      volume: {
        sufficient: false,
        reason: "insufficient_volume_history",
        period: 20,
        current: null,
        rolling_average: null,
        relative_volume: null,
        direction: { label: "unknown", previous: null },
      },
      range: { active: false, detected: null, transition: "absent" },
      levels: {
        zone_count: 1,
        nearest_support: { band_low: "61900", band_high: "62000", center: "61950", touch_count: 3 },
        nearest_resistance: null,
        equal_level_count: 1,
        nearest_level_below: { level: "61850", type: "equal_low", member_count: 2 },
        nearest_level_above: null,
      },
      events: {
        breakouts: {
          count: 2,
          latest: { id: "b1", direction: "bullish", known_at: timestamp, reference_type: "zone" },
        },
        failed_breakouts: { count: 0, latest: null },
        sweeps: { count: 0, latest: null },
        retests: { count: 1, held_count: 1, failed_count: 0, latest: { id: "r1", state: "held", known_at: timestamp } },
        chart_patterns: { count: 0, confirmed_count: 0 },
      },
      breakout_state: {
        attempts: [{ id: "b1", direction: "bullish", reference_type: "zone", close: "62160" }],
        acceptances: [],
        rejections: [],
        sweeps: [],
      },
      higher_timeframes: {
        requested: [],
        note: "no higher timeframes requested; no alignment inferred and none required",
      },
      last_close: "62160",
    },
    scenario: {
      available: true,
      doing_now: "Trend is BULLISH. Volatility contracting.",
      bot_seeing: {
        state: "WATCH",
        status: "evaluated",
        live_count: 1,
        live_setups: [
          {
            setup_id: "setup-watch-1",
            family: "range_rejection_reversal",
            direction: "bearish",
            state: "WATCH",
            created_at: timestamp,
            age_bars: 2,
            max_bars: 10,
            bars_remaining: 8,
            vetoed: false,
            vetoed_by: [],
            passed_rules: ["seed_event"],
            failed_rules: [],
            pending_required: [{ rule: "confirmation_event", reason: "waiting for the confirming close" }],
            invalidation_evidence: [],
          },
        ],
      },
      strengthen_bullish: {
        direction: "bullish",
        developing_setups: [],
        none_developing: true,
        to_start_a_setup: { breakout_retest_continuation: "a fresh breakout of a structural band" },
      },
      strengthen_bearish: {
        direction: "bearish",
        developing_setups: [
          { setup_id: "setup-watch-1", state: "WATCH", pending_required: [{ rule: "confirmation_event", reason: "waiting" }] },
        ],
        none_developing: false,
        to_start_a_setup: {},
      },
      waiting_for: {
        pending: [{ rule: "confirmation_event", reason: "waiting for the confirming close", setup_ids: ["setup-watch-1"] }],
        note: null,
      },
      invalidate: {
        cases: [
          {
            setup_id: "setup-watch-1",
            state: "WATCH",
            bars_remaining: 8,
            max_bars: 10,
            invalidation_evidence: [],
            vetoed: false,
            vetoed_by: [],
          },
        ],
      },
    },
    explanation: {
      headline: "Step 9 grounded explanation — BTC/USDT 1h — setup state: WATCH",
      sections: [{ number: 1, title: "WHAT THE ENGINE SEES", text: "The deterministic engine recorded WATCH." }],
      limitations: ["Paper only."],
      renderer_id: "local-template-renderer",
      renderer_version: "ai-explanation-local-v1",
      provenance: "deterministic-local",
    },
    ...overrides,
  };
}

function forwardPayload(timestamp = "2026-10-06T12:00:00Z") {
  return {
    disclaimer: "Paper trading and historical performance do not establish future profitability.",
    status: {
      market_data: {
        data_health: "CURRENT",
        latest_stored_candle_open: timestamp,
        expected_latest_closed_candle_open: timestamp,
        missing_candle_count: 0,
      },
      runner: {
        status: "IDLE",
        recorded_at: timestamp,
        latest_cycle_as_of: timestamp,
        pending_boundaries: 0,
        last_error: null,
      },
      sample: { paper_plans: 1, pending_catch_up_boundaries: 0 },
      current_state: {
        available: true,
        as_of: timestamp,
        setup_state_counts: { WATCH: 1, QUALIFIED: 0 },
        plan_state_counts: { PLANNABLE: 0 },
      },
    },
    observations: { observations: [] },
    report: {
      combined_metrics_available: true,
      metrics: {
        paper_plan_count: 1,
        completed_count: 0,
        unresolved_count: 1,
        outcome_status_counts: [{ value: "OPEN_AT_CUTOFF", count: 1 }],
        entry_reached_rate: {
          metric: "entry_reached_rate",
          numerator: 0,
          denominator: 1,
          percentage: null,
          status: "INSUFFICIENT_DATA",
          denominator_definition: "Recorded paper outcomes with a known entry-touch state.",
        },
        entry_not_reached_rate: {
          metric: "entry_not_reached_rate",
          numerator: 1,
          denominator: 1,
          percentage: null,
          status: "INSUFFICIENT_DATA",
          denominator_definition: "Recorded paper outcomes with a known entry-touch state.",
        },
        stopped_rate: null,
        unresolved_rate: null,
        ambiguous_rate: null,
        incomplete_rate: null,
        target_hit_rates: [],
        raw_observational_r: {
          sample_size: 0,
          records_considered: 1,
          status: "INSUFFICIENT_DATA",
          average: null,
          median: null,
          minimum: null,
          maximum: null,
          definition: "Proposed-level OHLC observation only; not realised P&L.",
          excluded: [],
        },
        friction_adjusted_hypothetical_r: {
          sample_size: 0,
          records_considered: 1,
          status: "INSUFFICIENT_DATA",
          average: null,
          median: null,
          minimum: null,
          maximum: null,
          definition: "Hypothetical results only.",
          excluded: [],
        },
      },
      version_cohorts: [],
    },
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
      const record = { removed: false, candleData: [], volumeData: [], lines: new Set() };
      const series = {
        setData: (rows) => { record.candleData = rows; },
        createPriceLine: (options) => {
          const line = { options };
          record.lines.add(line);
          return line;
        },
        removePriceLine: (line) => record.lines.delete(line),
      };
      const volume = { setData: (rows) => { record.volumeData = rows; } };
      charts.push(record);
      return {
        addCandlestickSeries: () => series,
        addHistogramSeries: () => volume,
        priceScale: () => ({ applyOptions: () => {} }),
        resize: () => {},
        remove: () => { record.removed = true; },
      };
    },
  };
  return { charts, observers, ResizeObserverMock, library };
}

async function withDashboard(dashboard, forward, callback) {
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
  globalThis.Node = MockNode;
  globalThis.ResizeObserver = chartState.ResizeObserverMock;
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "monospace" });
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
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
  globalThis.window = { LightweightCharts: chartState.library };
  globalThis.fetch = async (path) => {
    const payload = String(path).startsWith("/api/dashboard") ? dashboard : forward;
    if (!payload) throw new Error("missing fixture payload");
    return { ok: true, json: async () => payload };
  };

  try {
    await callback({ view, ids, chartState });
  } finally {
    disposeDashboard();
    for (const [key, value] of prior) {
      if (value.present) globalThis[key] = value.value;
      else delete globalThis[key];
    }
  }
}

test("WATCH renders with backend evidence and missing confirmation", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view, ids }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /WATCH/);
    assert.match(view.textContent, /Range rejection reversal/);
    assert.match(view.textContent, /A sell-side sweep was recorded\./);
    assert.match(view.textContent, /A confirming close has not been recorded\./);
    assert.equal(ids.get("topbar-status").textContent, "SYSTEM OK");
  });
});

test("NO TRADE is rendered from NO_SETUP and its backend reason", async () => {
  const dashboard = backendDashboard({
    qualification: {
      available: true,
      state: "NO_SETUP",
      reasons: ["No candidate met deterministic setup conditions."],
      setups: [],
      selected_setup_id: null,
      snapshot: { setups: [] },
    },
  });
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /NO TRADE/);
    assert.match(view.textContent, /No candidate met deterministic setup conditions\./);
    assert.match(view.textContent, /No qualified trade plan right now\./);
  });
});

test("unavailable qualification data never becomes a zero count or a NO TRADE verdict", () => {
  const model = verdictViewModel({
    qualification: { available: false, state: "NO_SETUP", setups: null },
    planning: { state: "NO_PLAN" },
    plan: null,
  });
  assert.equal(model.state, "UNKNOWN");
  assert.equal(model.watchCount, null);
  assert.equal(model.qualifiedCount, null);
  assert.equal(model.plannedCount, null);
});

test("PLANNABLE requires the backend plan state and renders its supplied levels", async () => {
  const dashboard = backendDashboard({
    qualification: {
      available: true,
      state: "QUALIFIED",
      reasons: [],
      selected_setup_id: "setup-qualified-1",
      setups: [{ id: "setup-qualified-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish" }],
      snapshot: { setups: [{
        id: "setup-qualified-1",
        state: "QUALIFIED",
        family: "breakout_retest_continuation",
        direction: "bullish",
        rules: [],
      }] },
    },
    planning: { state: "PLANNABLE", reasons: [], missing_inputs: [], state_detail: null },
    plan: {
      state: "PLANNABLE",
      direction: "bullish",
      family: "breakout_retest_continuation",
      entry: { value: "124" },
      stop: { value: "117" },
      invalidation: { value: "117" },
      risk_per_unit: "7",
      targets: [{ level: { value: "138" }, r_multiple: "2" }],
    },
  });
  const forward = forwardPayload();
  forward.status.current_state.as_of = "2026-10-06T11:00:00Z";
  await withDashboard(dashboard, forward, async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /PLANNABLE/);
    assert.match(view.textContent, /124/);
    assert.match(view.textContent, /117/);
    assert.match(view.textContent, /138/);
    assert.match(view.textContent, /2 R/);
  });
});

test("non-plannable payload values never leak into a guessed plan", async () => {
  const dashboard = backendDashboard({
    planning: { state: "NO_PLAN", reasons: ["confirmation_event_missing"], missing_inputs: [], state_detail: null },
    plan: {
      state: "NO_PLAN",
      entry: { value: "999999" },
      stop: { value: "888888" },
      targets: [{ level: { value: "777777" } }],
    },
  });
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /No qualified trade plan right now\./);
    assert.match(view.textContent, /confirmation_event_missing/);
    assert.doesNotMatch(view.textContent, /999999|888888|777777/);
  });
});

test("missing valid-plan fields remain UNKNOWN rather than being filled in", async () => {
  const dashboard = backendDashboard({
    qualification: {
      available: true,
      state: "QUALIFIED",
      reasons: [],
      selected_setup_id: "setup-qualified-1",
      setups: [{ id: "setup-qualified-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish" }],
      snapshot: { setups: [{ id: "setup-qualified-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish", rules: [] }] },
    },
    planning: { state: "PLANNABLE", reasons: [], missing_inputs: [], state_detail: null },
    plan: {
      state: "PLANNABLE",
      direction: "bullish",
      family: "breakout_retest_continuation",
      entry: { value: null },
      stop: null,
      invalidation: { value: null },
      risk_per_unit: null,
      targets: [{ level: { value: null }, r_multiple: null }],
    },
  });
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    const levels = findNodes(view, (node) => node.className.split(/\s+/).includes("plan-level"));
    assert.equal(levels.length, 5);
    assert.ok(levels.some((node) => /EntryUNKNOWN/.test(node.textContent)));
    assert.ok(levels.some((node) => /StopUNKNOWN/.test(node.textContent)));
    assert.ok(levels.some((node) => /InvalidationUNKNOWN/.test(node.textContent)));
    assert.match(view.textContent, /R\/R UNKNOWN/);
    assert.doesNotMatch(view.textContent, /\$0|Entry\s+0|Stop\s+0/);
  });
});

test("missing candle data renders an unavailable state without creating a chart", async () => {
  const dashboard = backendDashboard({
    market: { candles: null, latest_closed_candle: null, complete: false, missing_candle_count: null },
    freshness: { status: "UNKNOWN", latest_stored: null, expected_latest_closed: null },
  });
  await withDashboard(dashboard, null, async ({ view, chartState, ids }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /Candle data unavailable/);
    assert.match(view.textContent, /No stored closed candles were returned/);
    assert.equal(chartState.charts.length, 0);
    assert.equal(ids.get("topbar-price").textContent, "UNKNOWN");
  });
});

test("repeated dashboard renders dispose old charts and replace, not stack, overlays", async () => {
  const timestamp = "2026-10-06T12:00:00Z";
  const dashboard = backendDashboard({
    qualification: {
      available: true,
      state: "QUALIFIED",
      reasons: [],
      selected_setup_id: "setup-qualified-1",
      setups: [{ id: "setup-qualified-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish" }],
      snapshot: { setups: [{ id: "setup-qualified-1", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish", rules: [] }] },
    },
    planning: { state: "PLANNABLE", reasons: [], missing_inputs: [], state_detail: null },
    plan: {
      state: "PLANNABLE",
      entry: { value: "62100" },
      stop: { value: "61900" },
      invalidation: { value: "61900" },
      targets: [{ level: { value: "62500" }, r_multiple: "2" }],
    },
  });
  const forward = forwardPayload(timestamp);
  forward.status.current_state.as_of = "2026-10-06T11:00:00Z";
  await withDashboard(dashboard, forward, async ({ view, chartState }) => {
    await renderDashboard(view);
    assert.equal(chartState.charts.length, 1);
    assert.equal(chartState.charts[0].lines.size, 5); // S/R bounds plus entry, stop, target.
    await renderDashboard(view);
    assert.equal(chartState.charts.length, 2);
    assert.equal(chartState.charts[0].removed, true);
    assert.equal(chartState.charts[0].lines.size, 0);
    assert.equal(chartState.charts[1].lines.size, 5);
    assert.equal(chartState.charts.filter((chart) => !chart.removed).length, 1);
  });
});

test("healthy system details collapse to one compact SYSTEM OK indicator", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view, ids }) => {
    await renderDashboard(view);
    assert.equal(ids.get("topbar-status").textContent, "SYSTEM OK");
    const details = findNodes(view, (node) => node.tagName === "DETAILS" && node.className.includes("system-details"))[0];
    assert.ok(details);
    assert.equal(details.open, false);
    assert.match(details.textContent, /Pending catch-up/);
    assert.match(systemHealthViewModel(backendDashboard(), forwardPayload()).label, /SYSTEM OK/);
  });
});

test("performance only exposes recorded counts and leaves unavailable metrics unknown", () => {
  const model = performanceViewModel(forwardPayload());
  assert.equal(model.paperPlans, 1);
  assert.equal(model.resolvedOutcomes, 0);
  assert.equal(model.eligibleRSample, 0);
  assert.equal(model.consideredRSample, 1);
  assert.equal(model.rSampleStatus, "INSUFFICIENT_DATA");
  assert.equal(model.averageObservedR, null);
  assert.equal(model.averageFrictionAdjustedR, null);
  assert.equal(model.rates.length, 2);
});

test("version-separated forward reports never expose combined performance totals", () => {
  const forward = forwardPayload();
  forward.status.sample.paper_plans = 99;
  forward.report = {
    combined_metrics_available: false,
    combined_metrics_unavailable_reason: "Recorded strategy versions differ.",
    metrics: { paper_plan_count: 99, completed_count: 50 },
    version_cohorts: [{
      version_fingerprint: "version-a",
      metrics: {
        paper_plan_count: 2,
        completed_count: 1,
        raw_observational_r: { sample_size: 1, records_considered: 2, status: "INSUFFICIENT_DATA", average: "0.5" },
      },
    }],
  };
  const model = performanceViewModel(forward);
  assert.equal(model.versionSeparated, true);
  assert.equal(model.paperPlans, null);
  assert.equal(model.resolvedOutcomes, null);
  assert.equal(model.versionCohorts.length, 1);
  assert.equal(model.combinedUnavailableReason, "Recorded strategy versions differ.");
});

test("measured outcome observations preserve exact denominators and backend definitions", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /Outcome observations · exact denominators, not win rates/);
    assert.match(view.textContent, /0\/1 · INSUFFICIENT_DATA/);
    assert.match(view.textContent, /OPEN_AT_CUTOFF: 1/);
    assert.match(view.textContent, /Proposed-level OHLC observation only; not realised P&L/);
    assert.match(view.textContent, /Win\/loss classification, win rate, expectancy, drawdown, and realised P&L are unavailable/);
  });
});

test("desktop and narrow layouts use the same one-page dashboard without navigation tabs", async () => {
  const root = new URL("../../src/trading_assistant/web/static/", import.meta.url);
  const [html, css] = await Promise.all([
    readFile(new URL("index.html", root), "utf8"),
    readFile(new URL("styles.css", root), "utf8"),
  ]);
  assert.match(html, /id="view"/);
  assert.doesNotMatch(html, /data-route="(live|statistics|validation|settings)"/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /@media \(max-width: 520px\)/);
  assert.match(css, /\.primary-layout \{ grid-template-columns: minmax\(0, 1fr\); \}/);
  assert.match(css, /\.tertiary-grid \{ grid-template-columns: minmax\(0, 1fr\); \}/);
});

test("market now and scenario render backend facts verbatim", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /BULLISH · unchanged · steady/);
    assert.match(view.textContent, /2\.75345715% of price · contracting/);
    assert.match(view.textContent, /UNKNOWN \(insufficient_volume_history\)/);
    assert.match(view.textContent, /no active range \(absent\)/);
    assert.match(view.textContent, /support 61,900–62,000 \(3 touches\)/);
    assert.match(view.textContent, /resistance none in range/);
    assert.match(view.textContent, /equal below 61,850 \(equal_low ×2\)/);
    assert.match(view.textContent, /Fresh at this close: 1 attempt\(s\) · 0 acceptance\(s\)/);
    assert.match(view.textContent, /breakout Bullish @ 62,160/);
    assert.match(view.textContent, /Latest breakout: Bullish of zone at 2026-10-06 12:00 UTC/);
    assert.match(view.textContent, /no higher timeframes requested/);
    assert.match(view.textContent, /Trend is BULLISH\. Volatility contracting\./);
    assert.match(view.textContent, /Aggregate WATCH \(evaluated\) · 1 live setup/);
    assert.match(view.textContent, /age 2\/10 bars · 8 left/);
    assert.match(view.textContent, /Pending required: confirmation_event — waiting for the confirming close/);
    assert.match(view.textContent, /No developing bullish setups\./);
    assert.match(view.textContent, /still required: confirmation_event — waiting/);
    assert.match(view.textContent, /starts with: a fresh breakout of a structural band/);
    assert.match(view.textContent, /waiting for the confirming close \(1 setup\(s\)\)/);
    assert.match(view.textContent, /No invalidation\/lifecycle evidence yet\./);
    assert.match(view.textContent, /Step 9 grounded explanation — BTC\/USDT 1h — setup state: WATCH/);
    assert.match(view.textContent, /WHAT THE ENGINE SEES/);
    assert.match(view.textContent, /provenance deterministic-local/);
  });
});

test("missing market-state and scenario sections stay honestly unavailable", async () => {
  const dashboard = backendDashboard({
    market_state: { available: false, reason: "no Step 3/4 frame could be built at this boundary" },
    scenario: { available: false, reason: "no evaluated snapshot at this boundary" },
    explanation: { available: false, error: { code: "no_snapshot", message: "No snapshot to explain." } },
  });
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /no Step 3\/4 frame could be built at this boundary/);
    assert.match(view.textContent, /no evaluated snapshot at this boundary/);
    assert.match(view.textContent, /No snapshot to explain\./);
  });
});

