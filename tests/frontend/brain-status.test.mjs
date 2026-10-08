import assert from "node:assert/strict";
import test from "node:test";

import { brainStatusViewModel } from "../../src/trading_assistant/web/static/js/brain-status.js";

const AS_OF = "2026-10-06T12:00:00Z";
const SYMBOL = "BTC/USDT";
const TIMEFRAME = "1h";
const CYCLE_ID = "cycle-recorded-1";

function dashboardFixture({
  state = "WATCH",
  setupId = "setup-recorded-1",
  asOf = AS_OF,
  freshness = "CURRENT",
  marketComplete = true,
  plan = null,
} = {}) {
  const active = state === "WATCH" || state === "QUALIFIED";
  return {
    meta: { symbol: SYMBOL, timeframe: TIMEFRAME, as_of: asOf },
    freshness: { status: freshness },
    market: { complete: marketComplete },
    qualification: {
      available: true,
      state,
      selected_setup_id: state === "QUALIFIED" ? setupId : null,
    },
    looking_for: active ? { available: true, setup_id: setupId } : { available: false },
    plan,
  };
}

function observationFixture({
  setupId = "setup-recorded-1",
  state = "WATCH",
  plan = null,
} = {}) {
  const setup = {
    id: setupId,
    family: "breakout_retest_continuation",
    direction: "bullish",
    state,
    rules: [{ rule_id: "held_retest", outcome: "pending", required: true, reason: "Waiting for a held retest." }],
  };
  return {
    observation_id: "observation-recorded-1",
    cycle_id: CYCLE_ID,
    setup_id: setupId,
    setup_family: setup.family,
    setup_direction: setup.direction,
    setup_state: state,
    setup_json: JSON.stringify(setup),
    pending_rules: state === "WATCH" ? ["held_retest"] : [],
    plan_id: plan?.id ?? null,
    plan_state: plan?.state ?? null,
    plan_json: plan ? JSON.stringify(plan) : null,
    no_trade_reason: null,
    data_health: "CURRENT",
    as_of: AS_OF,
  };
}

function forwardFixture({
  runnerStatus = "IDLE",
  statusAsOf = AS_OF,
  cycleAsOf = AS_OF,
  currentStateAsOf = cycleAsOf,
  cycleStatus = "COMPLETE",
  snapshotState = "WATCH",
  dataHealth = "CURRENT",
  missingCandleCount = 0,
  pending = 0,
  observation = observationFixture({ state: snapshotState }),
} = {}) {
  const observations = observation ? [{ ...observation, cycle_id: CYCLE_ID }] : [];
  const cycle = {
    cycle_id: CYCLE_ID,
    as_of: cycleAsOf,
    status: cycleStatus,
    snapshot_state: snapshotState,
    snapshot_json: JSON.stringify({ state: snapshotState }),
    observation_count: observations.length,
    setup_state_counts: [[snapshotState, observations.length]],
    plan_state_counts: [],
    explanation_headline: "Persisted explanation at this closed candle.",
    notes: [],
  };
  return {
    symbol: SYMBOL,
    timeframe: TIMEFRAME,
    observations: { observations },
    status: {
      symbol: SYMBOL,
      timeframe: TIMEFRAME,
      as_of: statusAsOf,
      market_data: { data_health: dataHealth, missing_candle_count: missingCandleCount },
      runner: { status: runnerStatus, recorded_at: AS_OF, last_error: null, pending_boundaries: pending },
      latest_cycle: cycle,
      current_state: {
        available: true,
        as_of: currentStateAsOf,
        cycle_status: cycleStatus,
        setup_state: snapshotState,
        setup_state_counts: { [snapshotState]: observations.length },
        plan_state_counts: {},
        explanation_headline: "Persisted explanation at this closed candle.",
        notes: [],
      },
      sample: { pending_catch_up_boundaries: pending },
    },
  };
}

test("a matching completed cycle exposes only its recorded watch and explanation", () => {
  const view = brainStatusViewModel(dashboardFixture(), forwardFixture());
  assert.equal(view.statusLabel, "CURRENT");
  assert.equal(view.cycleCurrent, true);
  assert.equal(view.currentDecisionAvailable, true);
  assert.equal(view.explanation, "Persisted explanation at this closed candle.");
  assert.equal(view.observations.length, 1);
  assert.equal(view.activeSetups[0].id, "setup-recorded-1");
  assert.equal(view.activeSetups[0].state, "WATCH");
  assert.deepEqual(view.activeSetups[0].pendingRules, ["held_retest"]);
});

test("a persisted NO_SETUP cycle is distinguishable from an empty observation response", () => {
  const forward = forwardFixture({ snapshotState: "NO_SETUP", observation: null });
  const view = brainStatusViewModel(dashboardFixture({ state: "NO_SETUP" }), forward);
  assert.equal(view.statusLabel, "CURRENT");
  assert.equal(view.currentDecisionAvailable, true);
  assert.equal(view.snapshotState, "NO_SETUP");
  assert.deepEqual(view.observations, []);
});

test("a stopped runner shows its last persisted cycle as historical, never current", () => {
  const view = brainStatusViewModel(
    dashboardFixture(),
    forwardFixture({ runnerStatus: "STOPPED" }),
  );
  assert.equal(view.statusLabel, "STOPPED");
  assert.equal(view.cycleCurrent, false);
  assert.equal(view.currentDecisionAvailable, false);
  assert.equal(view.observations.length, 1);
  assert.match(view.reason, /stopped/);
});

test("missing heartbeat and missing cycle remain explicit non-decisions", () => {
  const noStatus = brainStatusViewModel(dashboardFixture(), null);
  assert.equal(noStatus.statusLabel, "UNAVAILABLE");
  assert.equal(noStatus.currentDecisionAvailable, false);

  const noHeartbeatForward = forwardFixture();
  noHeartbeatForward.status.runner = null;
  const noHeartbeat = brainStatusViewModel(dashboardFixture(), noHeartbeatForward);
  assert.equal(noHeartbeat.statusLabel, "NOT RUN");
  assert.equal(noHeartbeat.currentDecisionAvailable, false);

  const noCycleForward = forwardFixture();
  noCycleForward.status.latest_cycle = null;
  noCycleForward.status.current_state.available = false;
  noCycleForward.status.current_state.cycle_status = null;
  noCycleForward.observations.observations = [];
  const noCycle = brainStatusViewModel(dashboardFixture(), noCycleForward);
  assert.equal(noCycle.statusLabel, "WAITING");
  assert.equal(noCycle.currentDecisionAvailable, false);
});

test("incomplete, stale, gapped, and catching-up cycles cannot produce a current verdict", () => {
  const incomplete = brainStatusViewModel(
    dashboardFixture(),
    forwardFixture({ cycleStatus: "MISSING_FRAME" }),
  );
  assert.equal(incomplete.currentDecisionAvailable, false);
  assert.equal(incomplete.statusLabel, "WAITING");

  const staleBoundary = brainStatusViewModel(
    dashboardFixture({ asOf: "2026-10-06T13:00:00Z" }),
    forwardFixture({ statusAsOf: "2026-10-06T13:00:00Z", cycleAsOf: AS_OF, currentStateAsOf: AS_OF }),
  );
  assert.equal(staleBoundary.currentDecisionAvailable, false);
  assert.equal(staleBoundary.statusLabel, "STALE");

  const gapped = brainStatusViewModel(
    dashboardFixture(),
    forwardFixture({ dataHealth: "INCOMPLETE", missingCandleCount: 1 }),
  );
  assert.equal(gapped.currentDecisionAvailable, false);
  assert.equal(gapped.statusLabel, "STALE");

  const catchingUp = brainStatusViewModel(
    dashboardFixture(),
    forwardFixture({ pending: 2 }),
  );
  assert.equal(catchingUp.currentDecisionAvailable, false);
  assert.equal(catchingUp.statusLabel, "CATCHING UP");
});

test("dashboard setup, plan, or closed-boundary mismatches withhold recomputed decisions", () => {
  const forward = forwardFixture({
    snapshotState: "QUALIFIED",
    observation: observationFixture({ state: "QUALIFIED", plan: { id: "persisted-plan-1", state: "PLANNABLE" } }),
  });
  forward.status.latest_cycle.plan_state_counts = [["PLANNABLE", 1]];
  forward.status.current_state.plan_state_counts = { PLANNABLE: 1 };

  const wrongSetup = brainStatusViewModel(
    dashboardFixture({ state: "QUALIFIED", setupId: "different-setup", plan: { id: "persisted-plan-1", state: "PLANNABLE" } }),
    forward,
  );
  assert.equal(wrongSetup.statusLabel, "CURRENT");
  assert.equal(wrongSetup.cycleCurrent, true);
  assert.equal(wrongSetup.currentDecisionAvailable, false);
  assert.match(wrongSetup.reason, /does not match its recorded observation/);

  const wrongPlan = brainStatusViewModel(
    dashboardFixture({
      state: "QUALIFIED",
      setupId: "setup-recorded-1",
      plan: { id: "recomputed-plan", state: "PLANNABLE" },
    }),
    forward,
  );
  assert.equal(wrongPlan.currentDecisionAvailable, false);

  const missingPlan = brainStatusViewModel(
    dashboardFixture({ state: "QUALIFIED", setupId: "setup-recorded-1" }),
    forward,
  );
  assert.equal(missingPlan.currentDecisionAvailable, false,
    "a persisted PLANNABLE record cannot match a dashboard with no recorded plan");

  const wrongSetupState = brainStatusViewModel(
    dashboardFixture({ state: "QUALIFIED", setupId: "setup-recorded-1" }),
    forwardFixture({
      snapshotState: "QUALIFIED",
      observation: observationFixture({ state: "WATCH" }),
    }),
  );
  assert.equal(wrongSetupState.currentDecisionAvailable, false,
    "the selected setup's persisted state must match the dashboard state");

  const gappedDashboard = brainStatusViewModel(
    dashboardFixture({ marketComplete: false }),
    forward,
  );
  assert.equal(gappedDashboard.cycleCurrent, true);
  assert.equal(gappedDashboard.currentDecisionAvailable, false);
});
