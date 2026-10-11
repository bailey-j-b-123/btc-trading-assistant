import assert from "node:assert/strict";
import test from "node:test";

// Minimal DOM shim: enough for util.el() and the tracker card's text output.
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

const { paperTrackerCard, paperTrackerViewModel } = await import("../../src/trading_assistant/web/static/js/paper-tracker.js");

/**
 * Representative recorded trade, shaped exactly like the backend tracker
 * payload for the first genuine forward paper trade: bullish breakout/retest,
 * entry 83,916.01 / stop 83,606.01 / target 84,379.31, entry reached, then
 * STOPPED (-1R) — a settled plan that must stay listed.
 */
function trackerPayload(overrides = {}) {
  return {
    label: "PAPER OBSERVATION — NO REAL ORDER",
    disclaimer: "The tracker lists recorded paper plans and their recorded outcome versions only.",
    exchange: "binance",
    symbol: "BTC/USDT",
    timeframe: "1h",
    as_of: "2026-10-11T03:00:00Z",
    active_paper_plan_id: null,
    unresolved_paper_plan_count: 0,
    recorded_paper_plan_count: 1,
    latest_assessment: {
      available: true,
      unavailable_reason: null,
      as_of: "2026-10-11T03:00:00Z",
      recorded_at: "2026-10-11T03:00:30Z",
      cycle_status: "COMPLETE",
      setup_state: "NO_SETUP",
      explanation_headline: "No qualified setup at this close",
    },
    tracked_plans: [
      {
        paper_plan_id: "plan-stopped-1",
        setup_id: "setup-1",
        family: "breakout_retest_continuation",
        direction: "bullish",
        timeframe: "1h",
        plan_as_of: "2026-10-10T14:00:00Z",
        recorded_at: "2026-10-10T14:00:30Z",
        entry: "83916.01",
        stop: "83606.01",
        invalidation: "83606.01",
        risk_per_unit: "310.00",
        targets: ["84379.31"],
        target_r_multiples: ["1.5"],
        observation_horizon_candles: 20,
        entry_reached: true,
        entry_timestamp: "2026-10-10T15:00:00Z",
        settled: true,
        occupies_slot: false,
        outcome_version_count: 12,
        outcome: {
          outcome_id: "outcome-12",
          sequence: 12,
          status: "STOPPED",
          entry_reached: true,
          entry_timestamp: "2026-10-10T15:00:00Z",
          stop_reached: true,
          targets_reached: [],
          observed_through: "2026-10-11T01:00:00Z",
          recorded_at: "2026-10-11T02:00:30Z",
          ambiguous: false,
          incomplete: false,
          missing_candle_count: 0,
        },
      },
    ],
    ...overrides,
  };
}

function forward(tracker, extras = {}) {
  return { paper_label: "PAPER OBSERVATION — NO REAL ORDER", paper_tracker: tracker, ...extras };
}

test("settled STOPPED trade shows levels, entry reached, outcome, timestamp and SETTLED", () => {
  const card = paperTrackerCard(forward(trackerPayload()));
  const text = card.textContent;
  assert.match(text, /Paper trade tracker/);
  assert.match(text, /PAPER OBSERVATION — NO REAL ORDER/);
  assert.match(text, /Bullish/);
  assert.match(text, /Breakout · retest continuation/);
  assert.match(text, /83,916\.01/);
  assert.match(text, /83,606\.01/);
  assert.match(text, /84,379\.31/);
  assert.match(text, /Entry reached\s*YES at 2026-10-10 15:00 UTC/);
  assert.match(text, /Latest outcome\s*Stopped/);
  assert.match(text, /Outcome recorded\s*2026-10-11 02:00 UTC · observed through 2026-10-11 01:00 UTC/);
  assert.match(text, /Plan state\s*Settled — final/);
  assert.match(text, /SETTLED/);
  assert.match(text, /Outcome versions 12/);
  assert.equal(card.dataset.trackerState, "settled");
});

test("a recorded plan stays on the tracker when the latest assessment says No trade", () => {
  const card = paperTrackerCard(forward(trackerPayload()));
  const text = card.textContent;
  // The newest assessment concluded NO_SETUP, yet the recorded plan is still
  // displayed next to that verdict, never hidden by it.
  assert.match(text, /Latest assessment: No setup as of 2026-10-11 03:00 UTC/);
  assert.match(text, /stays on the tracker until its outcome settles/);
  assert.match(text, /83,916\.01/);
  assert.match(text, /Stopped/);
});

test("an unresolved plan without an outcome shows UNKNOWN entry reach and ACTIVE state", () => {
  const payload = trackerPayload({
    active_paper_plan_id: "plan-open-1",
    unresolved_paper_plan_count: 1,
    latest_assessment: {
      available: true,
      unavailable_reason: null,
      as_of: "2026-10-10T16:00:00Z",
      recorded_at: "2026-10-10T16:00:30Z",
      cycle_status: "COMPLETE",
      setup_state: "WATCH",
      explanation_headline: "Watching",
    },
    tracked_plans: [
      {
        paper_plan_id: "plan-open-1",
        setup_id: "setup-2",
        family: "breakout_retest_continuation",
        direction: "bullish",
        timeframe: "1h",
        plan_as_of: "2026-10-10T16:00:00Z",
        recorded_at: "2026-10-10T16:00:30Z",
        entry: "84200.00",
        stop: "83900.00",
        invalidation: "83900.00",
        risk_per_unit: "300.00",
        targets: ["84650.00"],
        target_r_multiples: ["1.5"],
        observation_horizon_candles: 20,
        entry_reached: null,
        entry_timestamp: null,
        settled: false,
        occupies_slot: true,
        outcome_version_count: 0,
        outcome: null,
      },
    ],
  });
  const card = paperTrackerCard(forward(payload));
  const text = card.textContent;
  assert.match(text, /Entry reached\s*UNKNOWN/);
  assert.match(text, /Latest outcome\s*No outcome recorded yet/);
  assert.match(text, /Outcome recorded\s*—/);
  assert.match(text, /Plan state\s*Unresolved — still observing/);
  assert.match(text, /ACTIVE/);
  assert.match(text, /1 UNRESOLVED/);
  assert.equal(card.dataset.trackerState, "active");
});

test("earlier recorded plans stay listed below the featured one", () => {
  const payload = trackerPayload();
  payload.recorded_paper_plan_count = 2;
  payload.tracked_plans.push({
    paper_plan_id: "plan-older-0",
    setup_id: "setup-0",
    family: "range_rejection_reversal",
    direction: "bearish",
    timeframe: "1h",
    plan_as_of: "2026-10-09T08:00:00Z",
    recorded_at: "2026-10-09T08:00:30Z",
    entry: "85100.00",
    stop: "85450.00",
    invalidation: "85450.00",
    risk_per_unit: "350.00",
    targets: ["84275.00"],
    target_r_multiples: ["2"],
    observation_horizon_candles: 20,
    entry_reached: false,
    entry_timestamp: null,
    settled: true,
    occupies_slot: false,
    outcome_version_count: 20,
    outcome: {
      outcome_id: "outcome-0",
      sequence: 20,
      status: "ENTRY_NOT_REACHED",
      entry_reached: false,
      entry_timestamp: null,
      stop_reached: false,
      targets_reached: [],
      observed_through: "2026-10-10T03:00:00Z",
      recorded_at: "2026-10-10T04:00:30Z",
      ambiguous: false,
      incomplete: false,
      missing_candle_count: 0,
    },
  });
  const card = paperTrackerCard(forward(payload));
  const text = card.textContent;
  assert.match(text, /Recent recorded plans \(1 earlier\)/);
  assert.match(text, /Bearish/);
  assert.match(text, /85,100\.00/);
  assert.match(text, /Entry never reached/);
  assert.match(text, /SETTLED/);
});

test("with no recorded plan the tracker says so and invents no levels", () => {
  const payload = trackerPayload({
    active_paper_plan_id: null,
    unresolved_paper_plan_count: 0,
    recorded_paper_plan_count: 0,
    latest_assessment: {
      available: true,
      unavailable_reason: null,
      as_of: "2026-10-11T03:00:00Z",
      recorded_at: "2026-10-11T03:00:30Z",
      cycle_status: "COMPLETE",
      setup_state: "NO_SETUP",
      explanation_headline: "No qualified setup at this close",
    },
    tracked_plans: [],
  });
  const card = paperTrackerCard(forward(payload));
  const text = card.textContent;
  assert.match(text, /No paper trade recorded yet/);
  assert.match(text, /even when later assessments say No trade/);
  assert.doesNotMatch(text, /Entry reached|Latest outcome|83,916/);
});

test("a missing tracker payload stays explicit instead of guessing", () => {
  const card = paperTrackerCard({});
  assert.match(card.textContent, /Paper trade tracker unavailable/);
  assert.equal(card.dataset.trackerState, "unknown");
  const missing = paperTrackerCard(null);
  assert.match(missing.textContent, /Paper trade tracker unavailable/);
});

test("live and display prices never reach the tracker: identical ledger text", () => {
  const marketOnlyA = {
    status: { market_data: { latest_closed_candle_open: "2026-10-11T02:00:00Z" }, sample: { paper_plans: 1 } },
    observations: { paper_plans: [], observations: [] },
  };
  const marketOnlyB = {
    status: { market_data: { latest_closed_candle_open: "2026-10-11T03:00:00Z" }, sample: { paper_plans: 1 } },
    observations: { paper_plans: [{ entry: "999999.99" }], observations: [] },
  };
  const textA = paperTrackerCard(forward(trackerPayload(), marketOnlyA)).textContent;
  const textB = paperTrackerCard(forward(trackerPayload(), marketOnlyB)).textContent;
  assert.equal(textA, textB, "the tracker must render the ledger payload only");
  assert.doesNotMatch(textA, /999999/);
});

test("view model keeps ledger facts without live data", () => {
  const model = paperTrackerViewModel(trackerPayload());
  assert.equal(model.available, true);
  assert.equal(model.unresolvedCount, 0);
  assert.equal(model.activePlanId, null);
  assert.equal(model.plans.length, 1);
  const plan = model.plans[0];
  assert.equal(plan.entryReached, true);
  assert.equal(plan.outcomeStatus, "STOPPED");
  assert.equal(plan.settled, true);
  assert.equal(plan.stateLabel, "SETTLED");
  assert.equal(model.latestAssessment.setupState, "NO_SETUP");
});
