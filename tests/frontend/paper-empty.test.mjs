import assert from "node:assert/strict";
import test from "node:test";

// Minimal DOM shim: enough for util.el() and the paper section's text output.
class ShimNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.style = {};
    this._text = "";
  }
  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }
  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }
  append(...nodes) {
    for (const node of nodes) this.children.push(node instanceof ShimNode ? node : Object.assign(new ShimNode("#text"), { _text: String(node) }));
  }
  appendChild(node) { this.append(node); return node; }
  setAttribute(name, value) { this[name] = String(value); }
  addEventListener() {}
}
globalThis.Node = ShimNode;
globalThis.document = { createElement: (tag) => new ShimNode(tag), createTextNode: (text) => Object.assign(new ShimNode("#text"), { _text: String(text) }) };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };

const { paperSection } = await import("../../src/trading_assistant/web/static/js/views/dashboard.js");

const zeroForward = {
  paper_label: "PAPER OBSERVATION — NO REAL ORDER",
  observations: { paper_plans: [], observations: [] },
  report: { metrics: { paper_plan_count: 0 } },
  limitations: ["Paper trading does not establish future profitability."],
};

test("with zero paper plans the ledger is one plain statement, not a grid of zero metrics", () => {
  const section = paperSection({ meta: {} }, zeroForward);
  const text = section.textContent;
  assert.match(text, /No paper plans recorded yet/);
  assert.match(text, /records a paper plan only when its engine builds one from a qualified setup/);
  assert.doesNotMatch(text, /Paper plans0|Sample for R/, "no zero-metric grid is rendered");
  assert.doesNotMatch(text, /SYSTEM OK|SYSTEM WARNING/);
});

test("with recorded plans the three-panel ledger is still rendered", () => {
  const withPlans = {
    ...zeroForward,
    status: { sample: { paper_plans: 2 } },
    observations: { paper_plans: [], observations: [] },
    report: { metrics: { paper_plan_count: 2 } },
  };
  const text = paperSection({ meta: {} }, withPlans).textContent;
  // Recorded (or counted) plans keep the full three-panel ledger; the empty-ledger card is not used.
  assert.match(text, /Paper observations/);
  assert.match(text, /Recent decisions/);
  assert.match(text, /Measured performance/);
  assert.match(text, /Paper plans2/);
});
