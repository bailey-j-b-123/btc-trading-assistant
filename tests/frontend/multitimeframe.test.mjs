import assert from "node:assert/strict";
import test from "node:test";

import {
  ladderViewModel,
  multiTimeframeCard,
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

// Minimal DOM stubs: el() needs document.createElement and a Node class.
function withDom(callback) {
  const keys = ["Node", "document"];
  const prior = new Map(keys.map((key) => [
    key,
    { present: Object.hasOwn(globalThis, key), value: globalThis[key] },
  ]));
  globalThis.Node = MockNode;
  globalThis.document = {
    documentElement: {},
    getElementById: () => null,
    createElement: (name) => new MockNode(name),
    createTextNode: (text) => {
      const node = new MockNode("#text", true);
      node._text = String(text);
      return node;
    },
  };
  try {
    return callback();
  } finally {
    for (const [key, value] of prior) {
      if (value.present) globalThis[key] = value.value;
      else delete globalThis[key];
    }
  }
}

function ladderDashboard(overrides = {}) {
  return {
    meta: { exchange: "binance", symbol: "BTC/USDT", timeframe: "1h", as_of: "2026-10-07T12:00:00Z" },
    multi_timeframe: {
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
      decision_time: "2026-10-07T12:00:00Z",
      decision: "awaiting_confirmation",
      decision_label: "Waiting for confirmation",
      overall: "WAITING FOR CONFIRMATION",
      alignment: "aligned",
      alignment_label: "Aligned",
      counter_trend: false,
      status: "evaluated",
      ladder: [
        {
          role: "context",
          timeframe: "4h",
          label: "4H CONTEXT",
          state: "Bullish structure",
          tone: "neutral",
          detail: "4H structure is bullish structure (trend bullish, 3 confirmed swing(s)).",
          boundary_open: "2026-10-07T08:00:00Z",
          boundary_close: "2026-10-07T12:00:00Z",
          available: true,
        },
        {
          role: "setup",
          timeframe: "1h",
          label: "1H SETUP",
          state: "Breakout / retest — qualified",
          tone: "good",
          detail: "A 1H breakout / retest setup (bullish) is qualified around the reference level 62000–62400.",
          boundary_open: "2026-10-07T11:00:00Z",
          boundary_close: "2026-10-07T12:00:00Z",
          available: true,
        },
        {
          role: "confirmation",
          timeframe: "15m",
          label: "15M CONFIRMATION",
          state: "Waiting",
          tone: "warn",
          detail: "15M has not accepted the setup's reference level yet; the idea is still waiting for confirmation.",
          boundary_open: "2026-10-07T11:45:00Z",
          boundary_close: "2026-10-07T12:00:00Z",
          available: true,
        },
        {
          role: "execution",
          timeframe: "5m",
          label: "5M EXECUTION",
          state: "Not armed",
          tone: "neutral",
          detail: "The execution layer is not armed: the confirmation layer is waiting, not CONFIRMING.",
          boundary_open: "2026-10-07T11:55:00Z",
          boundary_close: "2026-10-07T12:00:00Z",
          available: true,
        },
      ],
      waiting_for: ["15M acceptance of the setup's reference level (a closed candle beyond the band, level held)"],
      waiting_for_text: "Waiting for: 15M acceptance of the setup's reference level (a closed candle beyond the band, level held)",
      invalidated_if: ["a closed lower-timeframe candle closing below the setup reference level 62000–62400"],
      invalidated_if_text: "Invalidated if: a closed lower-timeframe candle closing below the setup reference level 62000–62400",
      reasons: ["confirmation_waiting"],
      explanation: { headline: "WAITING FOR CONFIRMATION", sentences: [] },
      snapshot: {},
      latest_recorded: null,
      limitations: [
        "The hierarchy is decision support only: PLANNABLE means the complete deterministic hierarchy agreed, never that an order exists.",
      ],
      ...overrides,
    },
  };
}

test("ladderViewModel returns null when the hierarchy is unavailable", () => {
  assert.equal(ladderViewModel({}), null);
  assert.equal(ladderViewModel({ multi_timeframe: { available: false } }), null);
  assert.equal(
    ladderViewModel({ multi_timeframe: { available: true, ladder: "nope" } }).rows.length,
    0
  );
});

test("ladderViewModel maps the four ladder rows in hierarchy order", () => {
  const model = ladderViewModel(ladderDashboard());
  assert.equal(model.overall, "WAITING FOR CONFIRMATION");
  assert.equal(model.decisionLabel, "Waiting for confirmation");
  assert.equal(model.alignmentLabel, "Aligned");
  assert.equal(model.counterTrend, false);
  assert.deepEqual(
    model.rows.map((row) => row.label),
    ["4H CONTEXT", "1H SETUP", "15M CONFIRMATION", "5M EXECUTION"]
  );
  assert.deepEqual(
    model.rows.map((row) => row.role),
    ["context", "setup", "confirmation", "execution"]
  );
  assert.deepEqual(
    model.rows.map((row) => row.tone),
    ["neutral", "good", "warn", "neutral"]
  );
  assert.equal(model.rows[1].detail.includes("62000–62400"), true);
  assert.equal(model.waitingForText.startsWith("Waiting for:"), true);
  assert.equal(model.invalidatedIfText.startsWith("Invalidated if:"), true);
  assert.equal(model.limitations.length, 1);
});

test("ladderViewModel flags a counter-trend hierarchy explicitly", () => {
  const model = ladderViewModel(
    ladderDashboard({
      alignment: "counter_trend",
      alignment_label: "Counter-trend",
      counter_trend: true,
    })
  );
  assert.equal(model.counterTrend, true);
  assert.equal(model.alignmentLabel, "Counter-trend");
});

test("multiTimeframeCard renders the ladder with arrows and the overall row", () => {
  const card = withDom(() => multiTimeframeCard(ladderDashboard()));
  // The verbose ladder is de-emphasised into a collapsed technical-details
  // disclosure; the full audit content stays in the DOM.
  assert.equal(card.tagName, "DETAILS");
  assert.equal(card.getAttribute("aria-label"), "Multi-timeframe ladder");
  assert.equal(card.open, false);
  const text = card.textContent;
  const summaries = findNodes(card, (node) => node.tagName === "SUMMARY");
  assert.ok(summaries.length >= 1);
  assert.ok(summaries[0].textContent.includes("Multi-timeframe ladder"));
  assert.ok(summaries[0].textContent.includes("technical details"));
  assert.ok(summaries[0].textContent.includes("Waiting for confirmation"));
  for (const expected of [
    "Multi-timeframe ladder",
    "4H CONTEXT",
    "1H SETUP",
    "15M CONFIRMATION",
    "5M EXECUTION",
    "OVERALL",
    "WAITING FOR CONFIRMATION",
    "Aligned",
    "Waiting for:",
    "Invalidated if:",
  ]) {
    assert.ok(text.includes(expected), `card should contain ${expected}`);
  }
  const steps = findNodes(card, (node) => (node.className || "").includes("mtf-step "));
  assert.equal(steps.length, 4);
  const arrows = findNodes(card, (node) => (node.className || "") === "mtf-arrow");
  assert.equal(arrows.length, 3);
  const overall = findNodes(card, (node) => (node.className || "").split(" ").includes("mtf-overall"));
  assert.equal(overall.length, 1);
  // No internal enum names are shown in the ladder card.
  for (const forbidden of ["awaiting_confirmation", "NOT_APPLICABLE", "COUNTER_TREND", "aligned,"]) {
    assert.equal(text.includes(forbidden), false, `card must not show ${forbidden}`);
  }
});

test("multiTimeframeCard marks a counter-trend overall row", () => {
  const card = withDom(() =>
    multiTimeframeCard(
      ladderDashboard({
        counter_trend: true,
        alignment: "counter_trend",
        alignment_label: "Counter-trend",
      })
    )
  );
  const overall = findNodes(card, (node) => (node.className || "").split(" ").includes("mtf-overall"))[0];
  assert.ok(overall.className.includes("mtf-warn"));
  assert.ok(card.textContent.includes("counter-trend setup flagged"));
});

test("multiTimeframeCard renders an explicit unavailable state", () => {
  const card = withDom(() => multiTimeframeCard({
    multi_timeframe: {
      available: false,
      error: { type: "HierarchyNotConfigured", message: "timeframe 5m is not supported" },
      limitations: ["decision support only"],
    },
  }));
  assert.equal(card.getAttribute("aria-label"), "Multi-timeframe ladder");
  assert.ok(card.textContent.includes("unavailable"));
  assert.ok(card.textContent.includes("timeframe 5m is not supported"));
  // The unavailable card renders no ladder rows.
  assert.equal(findNodes(card, (node) => (node.className || "").includes("mtf-step ")).length, 0);
});

test("multiTimeframeCard tolerates a missing payload entirely", () => {
  const card = withDom(() => multiTimeframeCard({}));
  assert.ok(card.textContent.includes("not available"));
  assert.equal(ladderViewModel({}), null);
});
