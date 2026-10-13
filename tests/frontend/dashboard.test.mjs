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
import { TRACKER_REFRESH_INTERVAL_MS } from "../../src/trading_assistant/web/static/js/paper-tracker.js";

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

  replaceWith(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const index = parent.children.indexOf(this);
    if (index === -1) return;
    parent.removeChild(this);
    nodes.forEach((node, offset) => {
      parent.children.splice(index + offset, 0, node);
      node.parentNode = parent;
    });
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

/** Text of a card with every collapsed <details> disclosure excluded. */
function normalViewText(node) {
  if (node.tagName === "DETAILS") return "";
  const own = node.tagName === "#TEXT" ? node.textContent : node._text || "";
  return own + (node.children || []).map((child) => normalViewText(child)).join("");
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
    meta: { exchange: "binance", symbol: "BTC/USDT", timeframe: "1h", as_of: timestamp },
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
    // The backend's own single-subject resolution for one WATCH setup.
    looking_for: {
      available: true,
      setup_id: "setup-watch-1",
      timeframe: "1h",
      family: "range_rejection_reversal",
      direction: "bearish",
      state: "WATCH",
      seed_event: { kind: "retest", known_at: "2026-10-06T10:00:00Z" },
      reference: null,
      pending_required: [{ rule_id: "confirmation_event", reason: "A confirming close has not been recorded." }],
      invalidation: null,
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
    paper_tracker: {
      label: "PAPER OBSERVATION — NO REAL ORDER",
      disclaimer: "The tracker lists recorded paper plans and their recorded outcome versions only.",
      exchange: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
      as_of: timestamp,
      active_paper_plan_id: "plan-tracker-1",
      unresolved_paper_plan_count: 1,
      recorded_paper_plan_count: 1,
      latest_assessment: {
        available: true,
        unavailable_reason: null,
        as_of: timestamp,
        recorded_at: timestamp,
        cycle_status: "COMPLETE",
        setup_state: "WATCH",
        explanation_headline: "Watching",
      },
      tracked_plans: [
        {
          paper_plan_id: "plan-tracker-1",
          setup_id: "setup-1",
          family: "breakout_retest_continuation",
          direction: "bullish",
          timeframe: "1h",
          plan_as_of: timestamp,
          recorded_at: timestamp,
          entry: "83916.01",
          stop: "83606.01",
          invalidation: "83606.01",
          risk_per_unit: "310.00",
          targets: ["84379.31"],
          target_r_multiples: ["1.5"],
          observation_horizon_candles: 20,
          entry_reached: true,
          entry_timestamp: timestamp,
          settled: false,
          occupies_slot: true,
          outcome_version_count: 3,
          outcome: {
            outcome_id: "outcome-3",
            sequence: 3,
            status: "OPEN_AT_CUTOFF",
            entry_reached: true,
            entry_timestamp: timestamp,
            stop_reached: false,
            targets_reached: [],
            observed_through: timestamp,
            recorded_at: timestamp,
            ambiguous: false,
            incomplete: false,
            missing_candle_count: 0,
          },
        },
      ],
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
  const documentListeners = new Map();
  const modalRoot = new MockNode("div");
  modalRoot.childElementCount = 0;
  ids.set("modal-root", modalRoot);
  globalThis.document = {
    documentElement: {},
    hidden: false,
    // The dashboard listens for tab visibility to resume automatic updates.
    addEventListener(name, callback) {
      documentListeners.set(name, [...(documentListeners.get(name) || []), callback]);
    },
    removeEventListener(name, callback) {
      documentListeners.set(name, (documentListeners.get(name) || []).filter((item) => item !== callback));
    },
    getElementById: (id) => ids.get(id) || null,
    createElement: (name) => new MockNode(name),
    createTextNode: (text) => {
      const node = new MockNode("#text", true);
      node._text = String(text);
      return node;
    },
  };
  globalThis.window = { LightweightCharts: chartState.library };
  // The tracker poll endpoint serves a mutable override so a test can record a
  // new outcome version between the dashboard render and a tracker poll.
  let trackerOverride = forward?.paper_tracker ?? null;
  // Forward reads serve a mutable payload, or a handler that can hold or fail one read.
  let forwardOverride = forward ?? null;
  let forwardHandler = null;
  const fetchCounts = new Map();
  globalThis.fetch = async (path) => {
    const key = String(path);
    fetchCounts.set(key, (fetchCounts.get(key) || 0) + 1);
    if (key.startsWith("/api/forward/paper-tracker")) {
      if (trackerOverride === null || trackerOverride === undefined) throw new Error("missing tracker fixture payload");
      return { ok: true, json: async () => trackerOverride };
    }
    if (key.startsWith("/api/forward?") || key === "/api/forward") {
      if (forwardHandler) return forwardHandler(key);
      if (!forwardOverride) throw new Error("missing forward fixture payload");
      return { ok: true, json: async () => forwardOverride };
    }
    const payload = key.startsWith("/api/dashboard") ? dashboard : forward;
    if (!payload) throw new Error("missing fixture payload");
    return { ok: true, json: async () => payload };
  };

  try {
    await callback({
      view,
      ids,
      chartState,
      fetchCounts,
      documentListeners,
      modalRoot,
      setTrackerPayload(tracker) {
        trackerOverride = tracker;
        // The tracker now arrives inside the scoped forward read as well.
        if (forwardOverride) forwardOverride = { ...forwardOverride, paper_tracker: tracker };
      },
      setForwardPayload(payload) { forwardOverride = payload; },
      setForwardHandler(handler) { forwardHandler = handler; },
    });
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
    assert.match(view.textContent, /Watching/);
    assert.match(view.textContent, /Range rejection reversal/);
    assert.match(view.textContent, /A sell-side sweep was recorded\./);
    assert.match(view.textContent, /A confirming close has not been recorded\./);
    assert.match(ids.get("topbar-status").textContent, /^DATA CURRENT · RUNNER /);
    // Fix #2: the one WATCH setup the backend itself resolved is the primary
    // focus of the Bot-is-watching card (Why facts + one Next requirement).
    const card = findNodes(view, (node) => (node.className || "").split(" ").includes("bot-watching-card"))[0];
    assert.ok(card);
    assert.match(card.textContent, /Bot is watching/);
    assert.match(card.textContent, /Bearish range rejection/);
    assert.match(card.textContent, /A confirming close has not been recorded\./);
    assert.match(card.textContent, /Invalid if/);
    assert.match(card.textContent, /Scenario — not prediction/);
  });
});

test("compact LOOKING FOR keeps its complete backend facts behind collapsed technical details", async () => {
  const dashboard = backendDashboard({
    looking_for: {
      available: true,
      setup_id: "setup-watch-1",
      timeframe: "1h",
      family: "breakout_retest_continuation",
      direction: "bearish",
      state: "WATCH",
      seed_event: { kind: "breakout", known_at: "2026-10-06T10:00:00Z" },
      reference: { type: "swing_low", band_low: "81000", band_high: "81100" },
      pending_required: [{ rule_id: "held_retest", reason: "Waiting for a held retest." }],
      invalidation: "82000",
    },
    multi_timeframe: {
      available: true,
      status: "evaluated",
      decision: "awaiting_confirmation",
      overall: "WAITING FOR CONFIRMATION",
      counter_trend: false,
      waiting_for_text: "Waiting for: 15M acceptance of the setup reference level",
      invalidated_if: ["a closed candle above the setup reference"],
      invalidated_if_text: "Invalidated if: a closed candle above the setup reference",
    },
  });
  const original = structuredClone(dashboard);
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    const card = findNodes(view, (node) => (node.className || "").split(" ").includes("looking-for"))[0];
    assert.ok(card);
    assert.match(card.textContent, /LOOKING FOR/);
    assert.match(card.textContent, /Bearish breakout → retest/);
    assert.match(card.textContent, /Watching:\$81,000–\$81,100/);
    assert.match(card.textContent, /Need:retest that holds the breakout level/);
    assert.match(card.textContent, /Invalid if:\$82,000/);
    assert.match(card.textContent, /Status:WAITING FOR CONFIRMATION/);
    assert.match(card.textContent, /Scenario — not prediction/);
    const disclosure = findNodes(card, (node) => node.tagName === "DETAILS")[0];
    assert.ok(disclosure);
    assert.equal(disclosure.open, false);
    assert.match(disclosure.textContent, /pending required held_retest/);
    assert.match(disclosure.textContent, /hierarchy waiting_for_text/);
    assert.deepEqual(dashboard, original);
  });
});

test("NO TRADE is rendered from NO_SETUP and its backend reason", async () => {
  const dashboard = backendDashboard({
    looking_for: { available: false, reason: "No active setup at this close." },
    qualification: {
      available: true,
      state: "NO_SETUP",
      reasons: ["No candidate met deterministic setup conditions."],
      setups: [],
      selected_setup_id: null,
      snapshot: { setups: [] },
    },
    scenario: {
      available: true,
      doing_now: "Trend is UNKNOWN. No active range. Qualification state: NO_SETUP.",
      bot_seeing: { state: "NO_SETUP", status: "evaluated", live_count: 0, live_setups: [] },
      strengthen_bullish: {
        direction: "bullish",
        developing_setups: [],
        none_developing: true,
        to_start_a_setup: { breakout_retest_continuation: "a fresh breakout of a structural band" },
      },
      strengthen_bearish: {
        direction: "bearish",
        developing_setups: [],
        none_developing: true,
        to_start_a_setup: {},
      },
      waiting_for: {
        pending: [],
        note: "No live setups: waiting for a fresh seed event (breakout, failed breakout, or sweep).",
      },
      invalidate: { cases: [] },
    },
  });
  await withDashboard(dashboard, forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /No trade/);
    assert.match(view.textContent, /No candidate met deterministic setup conditions\./);
    assert.match(view.textContent, /No qualified trade plan right now\./);
    // The setup-focus card stays honest with zero live candidates.
    const card = findNodes(view, (node) => (node.className || "").split(" ").includes("bot-watching-card"))[0];
    assert.ok(card);
    assert.match(card.textContent, /No setups being monitored at this close\./);
    assert.match(card.textContent, /waiting for a fresh seed event/);
    assert.match(card.textContent, /Nothing else is being monitored\./);
  });
});

test("unavailable qualification data never becomes a zero count or a NO TRADE verdict", () => {
  const model = verdictViewModel({
    qualification: { available: false, state: "NO_SETUP", setups: null },
    planning: { state: "NO_PLAN" },
    plan: null,
  });
  assert.equal(model.state, "Unknown");
  assert.equal(model.watchCount, null);
  assert.equal(model.qualifiedCount, null);
  assert.equal(model.plannedCount, null);
});

test("PLANNABLE requires the backend plan state and renders its supplied levels", async () => {
  const dashboard = backendDashboard({
    looking_for: {
      available: true,
      setup_id: "setup-qualified-1",
      timeframe: "1h",
      family: "breakout_retest_continuation",
      direction: "bullish",
      state: "QUALIFIED",
      seed_event: { kind: "breakout", known_at: "2026-10-06T10:00:00Z" },
      reference: { type: "swing_high", band_low: "117", band_high: "118" },
      pending_required: [],
      invalidation: "117",
    },
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
        rules: [
          {
            rule_id: "seed_event",
            required: true,
            outcome: "passed",
            reason: "confirmed Breakout",
            evidence: [{ status: "supportive", category: "event", timeframe: "1h", reason: "confirmed Breakout" }],
          },
          {
            rule_id: "held_retest",
            required: true,
            outcome: "passed",
            reason: "requires Step 4 held retest of the seed breakout",
            evidence: [{ status: "supportive", category: "confirmation", timeframe: "1h", reason: "requires Step 4 held retest of the seed breakout" }],
          },
        ],
      }] },
    },
    scenario: {
      available: true,
      doing_now: "Trend is BULLISH. Qualification state: QUALIFIED.",
      bot_seeing: {
        state: "QUALIFIED",
        status: "evaluated",
        live_count: 1,
        live_setups: [
          {
            setup_id: "setup-qualified-1",
            family: "breakout_retest_continuation",
            direction: "bullish",
            state: "QUALIFIED",
            created_at: "2026-10-06T10:00:00Z",
            age_bars: 2,
            max_bars: 10,
            bars_remaining: 8,
            vetoed: false,
            vetoed_by: [],
            passed_rules: ["seed_event", "held_retest"],
            failed_rules: [],
            pending_required: [],
            invalidation_evidence: [],
          },
        ],
      },
      strengthen_bullish: {
        direction: "bullish",
        developing_setups: [{ setup_id: "setup-qualified-1", state: "QUALIFIED", pending_required: [] }],
        none_developing: false,
        to_start_a_setup: {},
      },
      strengthen_bearish: { direction: "bearish", developing_setups: [], none_developing: true, to_start_a_setup: {} },
      waiting_for: { pending: [], note: null },
      invalidate: {
        cases: [
          {
            setup_id: "setup-qualified-1",
            state: "QUALIFIED",
            bars_remaining: 8,
            max_bars: 10,
            invalidation_evidence: [],
            vetoed: false,
            vetoed_by: [],
          },
        ],
      },
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
    multi_timeframe: {
      available: true,
      evaluation_source: "live_read_only",
      decision_kind: "live_hierarchy_evaluation",
      stored_decision_for_paper_confirmation: false,
      status: "evaluated",
      decision: "awaiting_confirmation",
      decision_label: "Trade ready",
      overall: "TRADE READY",
      counter_trend: false,
      waiting_for: ["15M acceptance of the setup reference level"],
      waiting_for_text: "Waiting for: 15M acceptance of the setup reference level",
      invalidated_if: ["a closed lower-timeframe candle closing below the setup reference level 117–118"],
      invalidated_if_text: "Invalidated if: a closed lower-timeframe candle closing below the setup reference level 117–118",
    },
  });
  const original = structuredClone(dashboard);
  const forward = forwardPayload();
  forward.status.current_state.as_of = "2026-10-06T11:00:00Z";
  await withDashboard(dashboard, forward, async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /1H PAPER CANDIDATE/);
    assert.match(view.textContent, /CONFIRMATION UNAVAILABLE/);
    assert.match(view.textContent, /LIVE EVALUATION/);
    assert.doesNotMatch(view.textContent, /TRADE READY|PLAN READY|Plan ready/);
    assert.match(view.textContent, /124/);
    assert.match(view.textContent, /117/);
    assert.match(view.textContent, /138/);
    assert.match(view.textContent, /2\.00 R/);
    assert.equal(verdictViewModel(dashboard).state, "1H PAPER CANDIDATE");
    // Fix #2: the engine-selected setup is the primary focus; its Next line is
    // the hierarchy's own waiting label, and no state is upgraded anywhere.
    const card = findNodes(view, (node) => (node.className || "").split(" ").includes("bot-watching-card"))[0];
    assert.ok(card);
    assert.match(card.textContent, /Bullish breakout → retest/);
    assert.match(card.textContent, /Qualified/);
    assert.match(card.textContent, /NextLive evaluation: Waiting for lower-timeframe confirmation/);
    assert.match(card.textContent, /Invalid ifa closed lower-timeframe candle closing below the setup reference level 117–118/);
    assert.doesNotMatch(card.textContent, /Trade ready|TRADE READY|PLAN READY/);
    const liveReady = {
      ...dashboard,
      multi_timeframe: {
        available: true,
        evaluation_source: "live_read_only",
        status: "evaluated",
        decision: "plannable",
        overall: "LIVE EVALUATION COMPLETE — NOT STORED CONFIRMATION",
        counter_trend: false,
      },
    };
    assert.equal(verdictViewModel(liveReady).state, "1H PAPER CANDIDATE");
    assert.equal(verdictViewModel(liveReady).hierarchyReady, false);

    const storedProof = {
      paper_candidate: {
        label: "1H PAPER CANDIDATE",
        timeframe: "1h",
        plan_as_of: "2026-10-06T12:00:00Z",
        recorded_at: "2026-10-06T12:01:00Z",
        paper_plan_id: "paper-plan-1",
        is_recorded_paper_plan: true,
      },
      four_timeframe_confirmation: {
        status: "confirmed",
        confirmed: true,
        label: "FOUR-TIMEFRAME CONFIRMED",
        reason: "A matching stored hierarchy decision was available by the paper plan.",
        decision_time: "2026-10-06T12:00:00Z",
        recorded_at: "2026-10-06T12:00:30Z",
        decision: "plannable",
        matching_boundary: true,
        available_by_plan: true,
        evidence_complete: true,
        observation_id: "hierarchy-observation-1",
        hierarchy_fingerprint: "fingerprint-1",
        records: [{
          observation_id: "hierarchy-observation-1",
          hierarchy_fingerprint: "fingerprint-1",
          decision_time: "2026-10-06T12:00:00Z",
          recorded_at: "2026-10-06T12:00:30Z",
          decision: "plannable",
          status: "evaluated",
        }],
      },
    };
    const confirmed = verdictViewModel({ ...liveReady, signal_provenance: storedProof });
    assert.equal(confirmed.state, "1H PAPER CANDIDATE");
    assert.equal(confirmed.hierarchyReady, true);
    assert.equal(confirmed.hierarchyStatus, "FOUR-TIMEFRAME CONFIRMED");

    const liveReadyWithoutStoredRecord = {
      ...liveReady,
      signal_provenance: {
        ...storedProof,
        paper_candidate: { ...storedProof.paper_candidate, is_recorded_paper_plan: false, paper_plan_id: null },
      },
    };
    assert.equal(verdictViewModel(liveReadyWithoutStoredRecord).hierarchyReady, false);
    assert.equal(verdictViewModel({ ...liveReady,
      multi_timeframe: { ...liveReady.multi_timeframe, status: "incomplete" },
    }).state, "1H PAPER CANDIDATE");
    assert.deepEqual(dashboard, original);
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
    // Clean default: only the engine plan, never the full S/R catalog.
    assert.equal(chartState.charts[0].lines.size, 3);
    const titles = [...chartState.charts[0].lines].map((line) => line.options.title);
    assert.ok(titles.includes("Stop / Invalidation"));
    assert.ok(!titles.some((title) => title.includes("support")));
    await renderDashboard(view);
    assert.equal(chartState.charts.length, 2);
    assert.equal(chartState.charts[0].removed, true);
    assert.equal(chartState.charts[0].lines.size, 0);
    assert.equal(chartState.charts[1].lines.size, 3);
    assert.equal(chartState.charts.filter((chart) => !chart.removed).length, 1);
  });
});

test("healthy system details collapse to one compact DATA CURRENT indicator", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view, ids }) => {
    await renderDashboard(view);
    assert.match(ids.get("topbar-status").textContent, /^DATA CURRENT · RUNNER /);
    const details = findNodes(view, (node) => node.tagName === "DETAILS" && node.className.includes("system-details"))[0];
    assert.ok(details);
    assert.equal(details.open, false);
    assert.match(details.textContent, /Pending catch-up/);
    assert.match(systemHealthViewModel(backendDashboard(), forwardPayload()).label, /^DATA CURRENT · RUNNER /);
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
    assert.match(view.textContent, /0\/1 · not enough data/);
    assert.match(view.textContent, /Still open at cutoff: 1/);
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

test("market now and scenario render backend facts in plain English", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    assert.match(view.textContent, /Bullish · trend unchanged · steady/);
    assert.match(view.textContent, /2\.75% of price · contracting/);
    assert.match(view.textContent, /Unknown \(insufficient volume history\)/);
    assert.match(view.textContent, /No active range \(no range\)/);
    assert.match(view.textContent, /support 61,900\.00–62,000\.00 \(3 touches\)/);
    assert.match(view.textContent, /resistance none in range/);
    assert.match(view.textContent, /equal below 61,850\.00 \(equal lows ×2\)/);
    assert.match(view.textContent, /Fresh at this close: 1 attempt\(s\) · 0 acceptance\(s\)/);
    assert.match(view.textContent, /breakout Bullish @ 62,160\.00/);
    assert.match(view.textContent, /Latest breakout: Bullish of price zone at 2026-10-06 12:00 UTC/);
    assert.doesNotMatch(view.textContent, /no higher timeframes requested/);
    // Fix #2: the composed doing-now paragraph is gone from the normal view
    // (the Market now card above carries the same structured facts); the
    // exact backend string stays in the collapsed audit disclosure.
    const card = findNodes(view, (node) => (node.className || "").split(" ").includes("bot-watching-card"))[0];
    assert.ok(card);
    const disclosure = findNodes(card, (node) => node.tagName === "DETAILS")[0];
    assert.ok(disclosure);
    assert.equal(disclosure.open, false);
    assert.match(disclosure.textContent, /doing_now: Trend is BULLISH\. Volatility contracting\./);
    assert.match(disclosure.textContent, /Watching 1 developing setup · fully evaluated/);
    assert.match(disclosure.textContent, /age 2\/10 bars · 8 left/);
    assert.match(disclosure.textContent, /Still required: waiting for the confirming close/);
    assert.match(disclosure.textContent, /No developing Bullish setups\./);
    assert.match(disclosure.textContent, /still required: waiting/);
    assert.match(disclosure.textContent, /starts with: a fresh breakout of a structural band/);
    assert.match(disclosure.textContent, /waiting for the confirming close \(1 setup\(s\)\)/);
    assert.match(disclosure.textContent, /No invalidation\/lifecycle evidence yet\./);
    // The normal view shows only the primary summary plus grouped others.
    const normalText = normalViewText(card);
    assert.match(normalText, /Bot is watching/);
    assert.match(normalText, /Bearish range rejection/);
    assert.match(normalText, /A sell-side sweep was recorded\./);
    assert.match(normalText, /A confirming close has not been recorded\./);
    assert.match(normalText, /No other setups being watched\./);
    assert.match(normalText, /Scenario — not prediction/);
    assert.doesNotMatch(normalText, /doing_now|Passed:|Failed:|Still required|strengthen|Waiting for/);
    assert.match(view.textContent, /Technical record · BTC\/USDT · 1H · Watching/);
    assert.match(view.textContent, /Backend headline: Step 9 grounded explanation/);
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
    assert.match(view.textContent, /No market frame could be built at this point\./);
    assert.match(view.textContent, /No completed analysis at this point\./);
    assert.match(view.textContent, /No snapshot to explain\./);
  });
});

test("the paper trade tracker is prominent, persistent, and ledger-driven", async () => {
  await withDashboard(backendDashboard(), forwardPayload(), async ({ view }) => {
    await renderDashboard(view);
    const tracker = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0];
    assert.ok(tracker, "the tracker card renders on the dashboard");
    // Prominent placement: directly after the decision hero, before the chart.
    const hero = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("hero-card"))[0];
    const chart = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("dash-main"))[0];
    const order = view.children.indexOf(hero) < view.children.indexOf(tracker)
      && tracker ? view.children.indexOf(tracker) < view.children.indexOf(chart) : false;
    assert.ok(order, "tracker sits between the decision hero and the chart");
    // The recorded plan shows every ledger fact, unresolved included.
    assert.match(tracker.textContent, /Paper trade tracker/);
    assert.match(tracker.textContent, /83,916\.01/);
    assert.match(tracker.textContent, /83,606\.01/);
    assert.match(tracker.textContent, /84,379\.31/);
    assert.match(tracker.textContent, /YES/);
    assert.match(tracker.textContent, /Still open at cutoff/);
    assert.match(tracker.textContent, /Unresolved — still observing/);
    assert.match(tracker.textContent, /1 UNRESOLVED/);
    assert.equal(tracker.dataset.trackerState, "active");
    // Persistence: a later assessment that says WATCH/No trade does not remove the plan.
    assert.match(tracker.textContent, /stays on the tracker until its outcome settles/);
  });
});

test("with no tracker payload the dashboard keeps an explicit unavailable state", async () => {
  const forward = forwardPayload();
  delete forward.paper_tracker;
  await withDashboard(backendDashboard(), forward, async ({ view }) => {
    await renderDashboard(view);
    const tracker = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0];
    assert.ok(tracker, "the tracker card still renders its slot");
    assert.match(tracker.textContent, /Paper trade tracker unavailable/);
  });
});

// ---------------------------------------------------------------------------
// Tracker refresh race: the runner records an outcome AFTER the dashboard
// refreshed on a new stored candle. The tracker must catch up on its own
// read-only poll — without a new candle, without re-running the dashboard
// analysis, and without touching the chart.
// ---------------------------------------------------------------------------

async function drainMicrotasks(times = 20) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

function stoppedTrackerFrom(payload) {
  const tracker = structuredClone(payload);
  tracker.unresolved_paper_plan_count = 0;
  tracker.active_paper_plan_id = null;
  const plan = tracker.tracked_plans[0];
  plan.settled = true;
  plan.occupies_slot = false;
  plan.outcome_version_count = (plan.outcome_version_count || 0) + 1;
  plan.outcome = {
    ...plan.outcome,
    outcome_id: "outcome-4-stopped",
    sequence: (plan.outcome?.sequence || 0) + 1,
    status: "STOPPED",
    stop_reached: true,
    observed_through: "2026-10-06T13:00:00Z",
    recorded_at: "2026-10-06T13:30:00Z",
  };
  return tracker;
}

/** The scoped, limit-8 stored forward read used by the dashboard poll (symbol/timeframe from the page). */
const SCOPED_FORWARD_POLL = "/api/forward?symbol=BTC%2FUSDT&timeframe=1h&limit=8";

function fetchTotal(fetchCounts, prefix) {
  return [...fetchCounts.entries()]
    .filter(([key]) => key.startsWith(prefix))
    .reduce((sum, [, count]) => sum + count, 0);
}

test("tracker updates when the runner records STOPPED after the dashboard refreshed — no new candle, no re-analysis", async () => {
  const forward = forwardPayload();
  const openTracker = forward.paper_tracker;
  assert.equal(openTracker.tracked_plans[0].outcome.status, "OPEN_AT_CUTOFF");

  await withDashboard(backendDashboard(), forward, async ({ view, chartState, fetchCounts, setTrackerPayload }) => {
    // Hold the scheduled timers so the test fires the tracker poll itself.
    const timers = [];
    const realSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
    let rendered;
    try {
      rendered = await renderDashboard(view);
    } finally {
      globalThis.setTimeout = realSetTimeout;
    }
    assert.equal(rendered, true);

    const cardBefore = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0];
    const heroBefore = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("hero-card"))[0];
    const chartWrapBefore = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("dash-chart"))[0];
    assert.ok(cardBefore && heroBefore && chartWrapBefore);
    // The dashboard refreshed on the new candle while the plan was still open.
    assert.match(cardBefore.textContent, /Still open at cutoff/);
    assert.match(cardBefore.textContent, /Unresolved — still observing/);
    assert.equal(cardBefore.dataset.trackerState, "active");
    const childCount = view.children.length;

    // The forward runner records the STOPPED outcome afterwards. No candle appears.
    setTrackerPayload(stoppedTrackerFrom(openTracker));
    // Fire the scheduled 15s poll timers (the tracker refresher; the boundary
    // check is a harmless no-op). Stale one-shot guards (e.g. the live
    // display's abort timer) are never re-fired.
    for (const timer of timers.filter((item) => item.ms === TRACKER_REFRESH_INTERVAL_MS)) timer.fn();
    await drainMicrotasks();

    const cardAfter = findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0];
    assert.ok(cardAfter, "the tracker card is still mounted");
    assert.notEqual(cardAfter, cardBefore, "the tracker card was replaced in place");
    assert.equal(cardAfter.parentNode, view);
    assert.equal(cardBefore.parentNode, null);
    assert.match(cardAfter.textContent, /Latest outcome\s*Stopped/);
    assert.match(cardAfter.textContent, /Plan state\s*Settled — final/);
    assert.match(cardAfter.textContent, /SETTLED/);
    assert.match(cardAfter.textContent, /ALL SETTLED/);
    assert.equal(cardAfter.dataset.trackerState, "settled");
    // The frozen entry/stop/targets are unchanged on the refreshed card.
    assert.match(cardAfter.textContent, /83,916\.01/);
    assert.match(cardAfter.textContent, /83,606\.01/);
    assert.match(cardAfter.textContent, /84,379\.31/);

    // No re-analysis, no page rebuild, no new chart: only the card moved.
    assert.equal(view.children.length, childCount, "the page layout is not rebuilt");
    assert.equal(findNodes(view, (node) => (node.className || "").split(/\s+/).includes("hero-card"))[0], heroBefore);
    assert.equal(findNodes(view, (node) => (node.className || "").split(/\s+/).includes("dash-chart"))[0], chartWrapBefore);
    assert.equal(chartState.charts.length, 1, "no new chart instance is created");
    assert.equal(fetchTotal(fetchCounts, "/api/dashboard"), 1, "the expensive dashboard analysis is NOT re-requested");
    // Polling re-reads only the stored forward ledger, scoped to the displayed symbol/timeframe.
    // The initial page load is the only unscoped read; the dashboard analysis is still not repeated.
    assert.equal(fetchTotal(fetchCounts, "/api/forward?limit=8"), 1, "the initial forward read is not repeated");
    assert.ok(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL) >= 1, "the scoped stored forward poll served the update");

    // A repeat poll with unchanged ledger data re-renders nothing.
    const stableCard = cardAfter;
    for (const timer of timers.filter((item) => item.ms === TRACKER_REFRESH_INTERVAL_MS)) timer.fn();
    await drainMicrotasks();
    assert.equal(findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0], stableCard, "unchanged ledger data never re-renders the card");

    // Navigation: dispose stops the refresher; a late poll neither fetches nor throws.
    disposeDashboard();
    const trackerPolls = fetchTotal(fetchCounts, SCOPED_FORWARD_POLL);
    for (const timer of timers.filter((item) => item.ms === TRACKER_REFRESH_INTERVAL_MS)) timer.fn();
    await drainMicrotasks();
    assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), trackerPolls, "no poll runs after navigation");
    assert.equal(findNodes(view, (node) => (node.className || "").split(/\s+/).includes("paper-tracker"))[0], stableCard);
  });
});

// ---------------------------------------------------------------------------
// Scoped stored forward polling. The runner heartbeat, the paper tracker and the
// paper/performance results must follow the stored forward ledger even when the
// closed candle is unchanged. Polls never re-run the dashboard analysis and never
// touch the decision hero, the chart, its zoom, or the scroll position.
// ---------------------------------------------------------------------------

/** Let every pending promise chain (fetch -> json -> apply) run to completion. */
function settle() {
  return new Promise((resolve) => setImmediate(resolve));
}

/**
 * Controllable timers for the whole test body. Only timers scheduled while the
 * clock is installed are captured; `fire()` runs each pending one exactly once.
 */
function fakeClock() {
  const timers = [];
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;
  globalThis.setTimeout = (fn, ms) => {
    const timer = { fn, ms, cancelled: false, fired: false };
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = (timer) => {
    if (timer && typeof timer === "object") timer.cancelled = true;
  };
  return {
    pending() {
      return timers.filter((timer) => !timer.cancelled && !timer.fired);
    },
    fire() {
      for (const timer of this.pending()) {
        if (timer.cancelled || timer.fired) continue;
        timer.fired = true;
        timer.fn();
      }
    },
    restore() {
      globalThis.setTimeout = realSetTimeout;
      globalThis.clearTimeout = realClearTimeout;
    },
  };
}

function okResponse(payload) {
  return { ok: true, json: async () => payload };
}

/** The open-plan forward read, with the report counts the performance card reads. */
function openForward() {
  const forward = forwardPayload();
  forward.symbol = "BTC/USDT";
  forward.timeframe = "1h";
  forward.report.metrics.completed_count = 0;
  forward.report.metrics.unresolved_count = 1;
  forward.report.metrics.outcome_status_counts = [{ value: "OPEN_AT_CUTOFF", count: 1 }];
  return forward;
}

/** The same ledger after the runner stopped and the open plan settled as STOPPED. */
function stoppedForward(base) {
  const forward = structuredClone(base);
  forward.status.runner = {
    ...forward.status.runner,
    status: "STOPPED",
    recorded_at: "2026-10-06T13:30:00Z",
    detail: "Runner stopped after the 13:00 cycle.",
  };
  forward.paper_tracker = stoppedTrackerFrom(base.paper_tracker);
  forward.report.metrics.completed_count = 1;
  forward.report.metrics.unresolved_count = 0;
  forward.report.metrics.outcome_status_counts = [{ value: "STOPPED", count: 1 }];
  return forward;
}

const hasClass = (name) => (node) => (node.className || "").split(/\s+/).includes(name);
const hasLabel = (label) => (node) => node.attributes?.["aria-label"] === label;

function metricValue(root, label) {
  const metric = findNodes(root, hasClass("performance-metric"))
    .find((node) => node.children[0]?.textContent === label);
  return metric ? metric.children[1].textContent : null;
}

function headerStatus(ids) {
  return ids.get("topbar-status");
}

async function renderOpen(view, forward) {
  const rendered = await renderDashboard(view);
  assert.equal(rendered, true);
  return forward;
}

test("unchanged candle: a STOPPED runner and a settled outcome reach the header, tracker and paper results on the scoped poll", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, ids, chartState, fetchCounts, setForwardPayload }) => {
    const clock = fakeClock();
    try {
      await renderOpen(view, base);
      const header = headerStatus(ids);
      assert.match(header.textContent, /DATA CURRENT · RUNNER IDLE/);
      assert.equal(header.dataset.tone, "green");
      assert.equal(metricValue(view, "Settled outcomes"), "0");
      assert.equal(metricValue(view, "Unresolved"), "1");
      const heroBefore = findNodes(view, hasClass("hero-card"))[0];
      const chartBefore = findNodes(view, hasClass("dash-chart"))[0];
      const trackerBefore = findNodes(view, hasClass("paper-tracker"))[0];
      const childCount = view.children.length;

      // An unchanged closed candle, and the forward runner now STOPPED with the plan settled.
      setForwardPayload(stoppedForward(base));
      clock.fire();
      await settle();

      assert.doesNotMatch(header.textContent, /DATA CURRENT/, "the green verdict is removed");
      assert.notEqual(header.dataset.tone, "green");
      assert.match(header.textContent, /FORWARD RUNNER · STATUS STOPPED/);
      assert.equal(header.dataset.tone, "amber");

      assert.equal(metricValue(view, "Settled outcomes"), "1", "the settled count follows the ledger");
      assert.equal(metricValue(view, "Unresolved"), "0");
      const trackerAfter = findNodes(view, hasClass("paper-tracker"))[0];
      assert.notEqual(trackerAfter, trackerBefore, "the tracker card was replaced");
      assert.match(trackerAfter.textContent, /Latest outcome\s*Stopped/);
      assert.match(trackerAfter.textContent, /ALL SETTLED/);

      // The decision hero, chart, zoom and page layout are untouched by the poll.
      assert.equal(findNodes(view, hasClass("hero-card"))[0], heroBefore);
      assert.equal(findNodes(view, hasClass("dash-chart"))[0], chartBefore);
      assert.equal(view.children.length, childCount, "the page layout is not rebuilt");
      assert.equal(chartState.charts.length, 1, "no new chart instance is created");
      assert.equal(fetchTotal(fetchCounts, "/api/dashboard"), 1, "the dashboard analysis is NOT re-requested");
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 1, "exactly one scoped forward read per tick");
      assert.equal(fetchTotal(fetchCounts, "/api/dashboard/decisions"), 0, "no decision is recorded");
    } finally {
      clock.restore();
    }
  });
});

test("health follows the server verdict even when the tracker ledger is unchanged; unchanged report facts are not rebuilt", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, ids, setForwardPayload }) => {
    const clock = fakeClock();
    try {
      await renderOpen(view, base);
      const trackerBefore = findNodes(view, hasClass("paper-tracker"))[0];
      const performanceBefore = findNodes(view, hasLabel("Paper and historical performance"))[0];
      assert.ok(trackerBefore && performanceBefore);

      // Only the runner heartbeat changes; the tracker and report are identical.
      const runnerOnly = structuredClone(base);
      runnerOnly.status.runner.status = "STOPPED";
      runnerOnly.status.runner.detail = "Runner stopped.";
      setForwardPayload(runnerOnly);
      clock.fire();
      await settle();

      assert.match(headerStatus(ids).textContent, /FORWARD RUNNER · STATUS STOPPED/);
      assert.equal(findNodes(view, hasClass("paper-tracker"))[0], trackerBefore, "unchanged tracker is not re-rendered");
      assert.equal(findNodes(view, hasLabel("Paper and historical performance"))[0], performanceBefore, "unchanged report facts are not re-rendered");

      // Repeat poll with no change at all: the health line is still applied, nothing else moves.
      clock.fire();
      await settle();
      assert.match(headerStatus(ids).textContent, /FORWARD RUNNER · STATUS STOPPED/);
      assert.equal(findNodes(view, hasClass("paper-tracker"))[0], trackerBefore);
      assert.equal(findNodes(view, hasLabel("Paper and historical performance"))[0], performanceBefore);
    } finally {
      clock.restore();
    }
  });
});

test("a failed forward read removes the green header, keeps the last good paper facts marked unavailable, and a retry recovers", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, ids, setForwardHandler, setForwardPayload }) => {
    const clock = fakeClock();
    try {
      await renderOpen(view, base);
      const header = headerStatus(ids);
      assert.equal(header.dataset.tone, "green");

      setForwardHandler(async () => { throw new Error("network down"); });
      clock.fire();
      await settle();

      assert.notEqual(header.dataset.tone, "green", "a failed read never leaves the green verdict");
      assert.match(header.textContent, /FORWARD STATUS UNAVAILABLE/);
      assert.match(header.title, /last good|Retrying|retry/i);
      const status = findNodes(view, hasClass("dash-forward-status"))[0];
      assert.ok(status, "the forward read status line is mounted");
      assert.match(status.textContent, /unavailable/i);
      // Last good paper facts remain on screen with the card still present.
      assert.equal(metricValue(view, "Settled outcomes"), "0");
      assert.match(findNodes(view, hasClass("paper-tracker"))[0].textContent, /Still open at cutoff/);

      // Retry after the backoff recovers with the current ledger.
      setForwardHandler(null);
      setForwardPayload(stoppedForward(base));
      clock.fire();
      await settle();
      assert.match(header.textContent, /FORWARD RUNNER · STATUS STOPPED/);
      assert.equal(header.dataset.tone, "amber");
      assert.equal(metricValue(view, "Settled outcomes"), "1");
      assert.doesNotMatch(findNodes(view, hasClass("dash-forward-status"))[0].textContent, /unavailable/i);
    } finally {
      clock.restore();
    }
  });
});

test("a hidden tab or an open dialog pauses forward polling without a request; returning resumes it", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, fetchCounts, documentListeners, modalRoot }) => {
    const clock = fakeClock();
    try {
      await renderOpen(view, base);
      globalThis.document.hidden = true;
      clock.fire();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 0, "no read while the tab is hidden");

      globalThis.document.hidden = false;
      for (const listener of documentListeners.get("visibilitychange") || []) listener();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 1, "returning to the tab resumes polling at once");

      modalRoot.childElementCount = 1;
      clock.fire();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 1, "no read while a dialog is open");
      modalRoot.childElementCount = 0;
      clock.fire();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 2, "polling resumes when the dialog closes");
    } finally {
      clock.restore();
    }
  });
});

test("only one forward read is ever in flight, and a full refresh does not add a second poller", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, fetchCounts, setForwardHandler }) => {
    const clock = fakeClock();
    const held = [];
    try {
      await renderOpen(view, base);
      setForwardHandler((key) => {
        if (key.startsWith(SCOPED_FORWARD_POLL)) return new Promise((resolve) => held.push(() => resolve(okResponse(base))));
        return Promise.resolve(okResponse(base));
      });
      clock.fire();
      clock.fire();
      clock.fire();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL), 1, "a slow read is never duplicated");
      held.shift()();
      await settle();

      // A second full render must reuse the single poller: one tick, one read.
      assert.equal(await renderDashboard(view), true);
      const before = fetchTotal(fetchCounts, SCOPED_FORWARD_POLL);
      clock.fire();
      await settle();
      assert.equal(fetchTotal(fetchCounts, SCOPED_FORWARD_POLL) - before, 1, "one poller after a full refresh");
    } finally {
      clock.restore();
    }
  });
});

test("a forward read that returns after a full refresh is discarded, and a read for another scope is rejected", async () => {
  const base = openForward();
  const fresh = stoppedForward(base);
  await withDashboard(backendDashboard(), base, async ({ view, ids, setForwardHandler }) => {
    const clock = fakeClock();
    const held = [];
    try {
      await renderOpen(view, base);
      let scopedPolls = 0;
      setForwardHandler((key) => {
        if (key.startsWith(SCOPED_FORWARD_POLL)) {
          scopedPolls += 1;
          if (scopedPolls === 1) return new Promise((resolve) => held.push(() => resolve(okResponse(base))));
          return Promise.resolve(okResponse({ ...base, symbol: "ETH/USDT" }));
        }
        return Promise.resolve(okResponse(fresh));
      });
      clock.fire();
      await settle();
      // The full refresh (new generation) lands first with the settled ledger.
      assert.equal(await renderDashboard(view), true);
      assert.match(headerStatus(ids).textContent, /FORWARD RUNNER · STATUS STOPPED/);
      // The stale read from before the refresh now returns the old open ledger: discarded.
      held.shift()();
      await settle();
      assert.match(headerStatus(ids).textContent, /FORWARD RUNNER · STATUS STOPPED/, "a stale generation never overwrites the newer render");
      assert.match(findNodes(view, hasClass("paper-tracker"))[0].textContent, /Latest outcome\s*Stopped/);

      // A response for a different symbol/timeframe is rejected, never displayed.
      clock.fire();
      await settle();
      assert.equal(scopedPolls, 2);
      assert.match(findNodes(view, hasClass("paper-tracker"))[0].textContent, /Latest outcome\s*Stopped/, "another scope never replaces this one");
    } finally {
      clock.restore();
    }
  });
});

test("navigation away discards a late forward read and leaves the page untouched", async () => {
  const base = openForward();
  await withDashboard(backendDashboard(), base, async ({ view, ids, setForwardHandler }) => {
    const clock = fakeClock();
    const held = [];
    try {
      await renderOpen(view, base);
      const trackerBefore = findNodes(view, hasClass("paper-tracker"))[0];
      const headerBefore = headerStatus(ids).textContent;
      setForwardHandler((key) => {
        if (key.startsWith(SCOPED_FORWARD_POLL)) return new Promise((resolve) => held.push(() => resolve(okResponse(stoppedForward(base)))));
        return Promise.resolve(okResponse(base));
      });
      clock.fire();
      await settle();
      disposeDashboard();
      held.shift()();
      await settle();
      assert.equal(findNodes(view, hasClass("paper-tracker"))[0], trackerBefore);
      assert.equal(headerStatus(ids).textContent, headerBefore, "no header write after navigation");
      clock.fire();
      await settle();
      assert.equal(held.length, 0, "no new read after navigation");
    } finally {
      clock.restore();
    }
  });
});
