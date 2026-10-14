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

const {
  paperTrackerCard,
  paperTrackerViewModel,
  signalProvenanceViewModel,
} = await import("../../src/trading_assistant/web/static/js/paper-tracker.js");

function findNode(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}

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

function validStoredConfirmationProvenance(plan = {}) {
  const decisionTime = plan.plan_as_of || "2026-10-10T14:00:00Z";
  const planRecordedAt = new Date(Date.parse(decisionTime) + 30_000).toISOString();
  const hierarchyRecordedAt = new Date(Date.parse(decisionTime) + 20_000).toISOString();
  const observationId = "hierarchy-observation-1";
  return {
    paper_candidate: {
      label: "1H PAPER CANDIDATE",
      timeframe: "1h",
      plan_as_of: decisionTime,
      recorded_at: planRecordedAt,
      paper_plan_id: plan.paper_plan_id || "plan-stopped-1",
      is_recorded_paper_plan: true,
    },
    four_timeframe_confirmation: {
      status: "confirmed",
      confirmed: true,
      label: "FOUR-TIMEFRAME CONFIRMED",
      reason: "A complete stored hierarchy decision matched this candidate before it was recorded.",
      decision_time: decisionTime,
      recorded_at: hierarchyRecordedAt,
      decision: "plannable",
      matching_boundary: true,
      available_by_plan: true,
      evidence_complete: true,
      observation_id: observationId,
      hierarchy_fingerprint: "fingerprint-1",
      records: [{
        observation_id: observationId,
        hierarchy_fingerprint: "fingerprint-1",
        decision_time: decisionTime,
        recorded_at: hierarchyRecordedAt,
        decision: "plannable",
        status: "evaluated",
      }],
    },
  };
}

test("stored confirmation presentation requires matching recorded proof", () => {
  const valid = signalProvenanceViewModel(validStoredConfirmationProvenance());
  assert.equal(valid.candidateLabel, "1H PAPER CANDIDATE");
  assert.equal(valid.confirmationStatus, "confirmed");
  assert.equal(valid.confirmationLabel, "FOUR-TIMEFRAME CONFIRMED");
  assert.equal(valid.confirmed, true);

  const mismatched = validStoredConfirmationProvenance();
  mismatched.four_timeframe_confirmation.decision_time = "2026-10-10T13:00:00Z";
  assert.equal(signalProvenanceViewModel(mismatched).confirmationStatus, "conflicting");
  assert.equal(signalProvenanceViewModel(mismatched).confirmed, false);

  const recordedLate = validStoredConfirmationProvenance();
  recordedLate.four_timeframe_confirmation.recorded_at = "2026-10-10T14:00:40Z";
  recordedLate.four_timeframe_confirmation.records[0].recorded_at = "2026-10-10T14:00:40Z";
  assert.equal(signalProvenanceViewModel(recordedLate).confirmed, false);
  assert.equal(signalProvenanceViewModel(recordedLate).confirmationStatus, "conflicting");

  const incomplete = {
    paper_candidate: { label: "1H PAPER CANDIDATE", timeframe: "1h" },
    four_timeframe_confirmation: { status: "incomplete", confirmed: false },
  };
  assert.equal(signalProvenanceViewModel(incomplete).confirmationStatus, "incomplete");
  assert.equal(signalProvenanceViewModel(incomplete).confirmed, false);
});

test("trade-history provenance column follows Direction and labels every older row", () => {
  const recent = {
    ...trackerPayload().tracked_plans[0],
    paper_plan_id: "plan-recent",
    plan_as_of: "2026-10-10T16:00:00Z",
    recorded_at: "2026-10-10T16:00:30Z",
    signal_provenance: validStoredConfirmationProvenance({
      paper_plan_id: "plan-recent",
      plan_as_of: "2026-10-10T16:00:00Z",
    }),
  };
  const historical = {
    ...trackerPayload().tracked_plans[0],
    paper_plan_id: "plan-historical",
    plan_as_of: "2026-10-10T14:00:00Z",
    recorded_at: "2026-10-10T14:00:30Z",
    signal_provenance: {
      paper_candidate: {
        label: "1H PAPER CANDIDATE",
        timeframe: "1h",
        plan_as_of: "2026-10-10T14:00:00Z",
        paper_plan_id: "plan-historical",
        is_recorded_paper_plan: true,
      },
      four_timeframe_confirmation: {
        status: "incomplete",
        confirmed: false,
        decision_time: "2026-10-10T14:00:00Z",
        matching_boundary: true,
        available_by_plan: true,
        evidence_complete: false,
        reason: "The stored hierarchy evidence was incomplete.",
      },
    },
  };
  const tracker = trackerPayload({ tracked_plans: [recent, historical] });
  const card = paperTrackerCard(forward(tracker));
  const table = findNode(card, (node) => node.tagName === "TABLE");
  assert.ok(table, "history table remains present for recorded plans");
  const headers = table.children[0].children[0].children.map((cell) => cell.textContent);
  assert.deepEqual(headers.slice(0, 4), ["Planned", "Direction", "Signal provenance", "Entry"]);
  const historicalRow = table.children[1].children[0];
  assert.equal(
    historicalRow.children[2].textContent,
    "1H PAPER CANDIDATE · HIERARCHY EVIDENCE INCOMPLETE",
  );
  assert.match(card.textContent, /FOUR-TIMEFRAME CONFIRMED/);
  assert.match(card.textContent, /HIERARCHY EVIDENCE INCOMPLETE/);
  assert.equal(historicalRow.children[7].textContent, "Stopped", "existing outcome is unchanged");
});

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
  assert.match(text, /NO PAPER TRADES/);
  assert.doesNotMatch(text, /ALL SETTLED/);
  assert.equal(card.dataset.trackerState, "empty");
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

// ---------------------------------------------------------------------------
// Tracker refresh race: the read-only refresher
// ---------------------------------------------------------------------------

const { createTrackerRefresher, nextTrackerDelay, trackerLedgerSignature, TRACKER_REFRESH_INTERVAL_MS } = await import("../../src/trading_assistant/web/static/js/paper-tracker.js");

function openTrackerPayload() {
  return trackerPayload({
    active_paper_plan_id: "plan-open-1",
    unresolved_paper_plan_count: 1,
    tracked_plans: [
      {
        ...trackerPayload().tracked_plans[0],
        paper_plan_id: "plan-open-1",
        entry_reached: true,
        settled: false,
        occupies_slot: true,
        outcome_version_count: 3,
        outcome: {
          outcome_id: "outcome-3",
          sequence: 3,
          status: "OPEN_AT_CUTOFF",
          entry_reached: true,
          entry_timestamp: "2026-10-10T15:00:00Z",
          stop_reached: false,
          targets_reached: [],
          observed_through: "2026-10-10T16:00:00Z",
          recorded_at: "2026-10-10T16:00:30Z",
          ambiguous: false,
          incomplete: false,
          missing_candle_count: 0,
        },
      },
    ],
  });
}

function stoppedTrackerPayload() {
  const payload = openTrackerPayload();
  payload.unresolved_paper_plan_count = 0;
  payload.active_paper_plan_id = null;
  const plan = payload.tracked_plans[0];
  plan.settled = true;
  plan.occupies_slot = false;
  plan.outcome_version_count = 4;
  plan.outcome = {
    outcome_id: "outcome-4",
    sequence: 4,
    status: "STOPPED",
    entry_reached: true,
    entry_timestamp: "2026-10-10T15:00:00Z",
    stop_reached: true,
    targets_reached: [],
    observed_through: "2026-10-10T17:00:00Z",
    recorded_at: "2026-10-10T17:30:00Z",
    ambiguous: false,
    incomplete: false,
    missing_candle_count: 0,
  };
  return payload;
}

test("tracker ledger signature changes only when ledger facts change", () => {
  const open = openTrackerPayload();
  assert.equal(trackerLedgerSignature(open), trackerLedgerSignature(openTrackerPayload()));
  assert.notEqual(trackerLedgerSignature(open), trackerLedgerSignature(stoppedTrackerPayload()));
  // A live-only field outside the ledger (here: an extra payload key) never changes the signature.
  assert.equal(trackerLedgerSignature(open), trackerLedgerSignature({ ...open, latest_assessment: null }));
  assert.equal(trackerLedgerSignature(null), null);
  assert.equal(trackerLedgerSignature(undefined), null);
});

test("nextTrackerDelay backs off exponentially and caps", () => {
  assert.equal(nextTrackerDelay(0), TRACKER_REFRESH_INTERVAL_MS);
  assert.equal(nextTrackerDelay(1), TRACKER_REFRESH_INTERVAL_MS * 2);
  assert.equal(nextTrackerDelay(2), TRACKER_REFRESH_INTERVAL_MS * 4);
  assert.equal(nextTrackerDelay(9), 60_000);
});

function manualScheduler() {
  const timers = [];
  return {
    timers,
    schedule: (fn, ms) => {
      timers.push({ fn, ms, cleared: false });
      return timers.length;
    },
    cancel: (handle) => {
      const timer = timers[handle - 1];
      if (timer) timer.cleared = true;
    },
    fire(index) {
      const timer = timers[index - 1];
      if (timer && !timer.cleared) timer.fn();
    },
  };
}

async function drainMicrotasks(times = 16) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

test("refresher seeds its signature, applies a change once, and ignores repeats", async () => {
  const scheduler = manualScheduler();
  let fetched = 0;
  let applied = [];
  const refresher = createTrackerRefresher({
    fetchTracker: () => {
      fetched += 1;
      return Promise.resolve(stoppedTrackerPayload());
    },
    applyTracker: (tracker) => applied.push(tracker),
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  // The dashboard just rendered with the OPEN_AT_CUTOFF tracker.
  refresher.start(openTrackerPayload());
  assert.equal(scheduler.timers.length, 1);
  assert.equal(scheduler.timers[0].ms, TRACKER_REFRESH_INTERVAL_MS);

  // The runner has since recorded STOPPED: one poll applies exactly one update.
  scheduler.fire(1);
  await drainMicrotasks();
  assert.equal(fetched, 1);
  assert.equal(applied.length, 1);
  assert.equal(applied[0].tracked_plans[0].outcome.status, "STOPPED");

  // The next poll sees unchanged ledger facts and applies nothing.
  scheduler.fire(2);
  await drainMicrotasks();
  assert.equal(fetched, 2);
  assert.equal(applied.length, 1, "no duplicate re-render for unchanged ledger data");
  refresher.stop();
});

test("refresher keeps the last recorded state on failure and backs off without inventing data", async () => {
  const scheduler = manualScheduler();
  let attempts = 0;
  let applied = 0;
  const refresher = createTrackerRefresher({
    fetchTracker: () => {
      attempts += 1;
      if (attempts <= 2) return Promise.reject(new Error("tracker endpoint unavailable"));
      return Promise.resolve(stoppedTrackerPayload());
    },
    applyTracker: () => {
      applied += 1;
    },
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  refresher.start(openTrackerPayload());

  scheduler.fire(1);
  await drainMicrotasks();
  assert.equal(applied, 0, "a failed poll never applies or invents state");
  assert.equal(refresher.state().failures, 1);
  assert.equal(scheduler.timers[1].ms, TRACKER_REFRESH_INTERVAL_MS * 2, "backed-off next poll");

  scheduler.fire(2);
  await drainMicrotasks();
  assert.equal(refresher.state().failures, 2);
  assert.equal(scheduler.timers[2].ms, TRACKER_REFRESH_INTERVAL_MS * 4);

  scheduler.fire(3);
  await drainMicrotasks();
  assert.equal(applied, 1, "recovery applies the fresh ledger read");
  assert.equal(refresher.state().failures, 0);
  assert.equal(scheduler.timers[3].ms, TRACKER_REFRESH_INTERVAL_MS, "delay resets after success");
  refresher.stop();
});

test("a slow in-flight poll is never duplicated and a late response after stop is discarded", async () => {
  const scheduler = manualScheduler();
  let fetched = 0;
  let applied = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const refresher = createTrackerRefresher({
    fetchTracker: () => {
      fetched += 1;
      return gate.then(() => stoppedTrackerPayload());
    },
    applyTracker: () => {
      applied += 1;
    },
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  refresher.start(openTrackerPayload());

  const first = scheduler.timers[0];
  first.fn(); // poll starts and hangs
  const second = refresher.checkNow();
  const secondResult = await second;
  assert.equal(secondResult, false, "in-flight poll is not duplicated");
  assert.equal(fetched, 1);
  assert.equal(refresher.state().inFlight, true);

  refresher.stop();
  release(); // the slow response arrives after navigation stopped the refresher
  await drainMicrotasks();
  assert.equal(applied, 0, "a late response after stop is discarded, never applied");
  assert.equal(refresher.state().hasPendingTimer, false);
});

test("blocked polls pause without fetching and stop() cancels the pending timer", () => {
  const scheduler = manualScheduler();
  let fetched = 0;
  const refresher = createTrackerRefresher({
    fetchTracker: () => {
      fetched += 1;
      return Promise.resolve(openTrackerPayload());
    },
    applyTracker: () => {},
    isBlocked: () => true,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  refresher.start(openTrackerPayload());
  const first = scheduler.timers[0];
  first.fn();
  return drainMicrotasks().then(() => {
    assert.equal(fetched, 0, "a blocked tab never fetches");
    assert.equal(scheduler.timers.length, 2, "the loop stays alive and resumes later");
    refresher.stop();
    assert.equal(scheduler.timers[1].cleared, true, "the pending poll timer is cancelled");
  });
});


test("an incomplete tracker cannot claim all settled or that no trades exist", () => {
  const card = paperTrackerCard(forward(trackerPayload({
    recorded_paper_plan_count: null, unresolved_paper_plan_count: null, tracked_plans: [],
  })));
  assert.equal(card.dataset.trackerState, "unknown");
  assert.match(card.textContent, /Paper trade details unavailable/);
  assert.doesNotMatch(card.textContent, /ALL SETTLED|No paper trade recorded yet/);
});
