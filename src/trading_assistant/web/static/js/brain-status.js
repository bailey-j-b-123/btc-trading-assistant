/**
 * Read-only projection of the persisted forward-cycle and runner status.
 * This module never evaluates setups, constructs plans, or fills missing facts.
 */

const ACTIVE_RUNNER_STATES = new Set(["STARTED", "PROCESSED", "IDLE"]);
const LIVE_SETUP_STATES = new Set(["WATCH", "QUALIFIED"]);

function parseJson(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) return value;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function sameInstant(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  const leftMs = Date.parse(left);
  const rightMs = Date.parse(right);
  return Number.isFinite(leftMs) && Number.isFinite(rightMs) && leftMs === rightMs;
}

function nonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function countMap(value) {
  if (Array.isArray(value)) {
    return Object.fromEntries(value.filter((entry) => Array.isArray(entry) &&
      typeof entry[0] === "string" && nonNegativeInteger(entry[1]) !== null));
  }
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function projectObservation(row) {
  const setup = parseJson(row?.setup_json) || {};
  const plan = parseJson(row?.plan_json);
  return {
    id: setup.id ?? row?.setup_id ?? null,
    family: setup.family ?? row?.setup_family ?? null,
    direction: setup.direction ?? row?.setup_direction ?? null,
    state: setup.state ?? row?.setup_state ?? null,
    asOf: row?.as_of ?? null,
    rules: Array.isArray(setup.rules) ? setup.rules : [],
    pendingRules: Array.isArray(row?.pending_rules) ? row.pending_rules : [],
    planState: row?.plan_state ?? plan?.state ?? null,
    plan,
    noTradeReason: row?.no_trade_reason ?? null,
    dataHealth: row?.data_health ?? null,
  };
}

/**
 * The persisted cycle can be CURRENT while a separate dashboard recomputation
 * is withheld. Dashboard decision panels are allowed only when the complete
 * cycle, confirmed data boundary, setup identity/state, and (when present) plan
 * identity all match the corresponding stored observation.
 */
export function brainStatusViewModel(dashboard, forward) {
  const status = forward?.status;
  const runner = status && typeof status === "object" ? status.runner : null;
  const cycle = status && typeof status === "object" ? status.latest_cycle : null;
  const currentState = status && typeof status === "object" ? status.current_state : null;
  const market = status && typeof status === "object" ? status.market_data : null;
  const sample = status && typeof status === "object" ? status.sample : null;
  const runnerStatus = typeof runner?.status === "string" ? runner.status : null;
  const pending = nonNegativeInteger(sample?.pending_catch_up_boundaries) ??
    nonNegativeInteger(runner?.pending_boundaries);
  const targetAsOf = status?.as_of ?? null;
  const cycleAsOf = cycle?.as_of ?? currentState?.as_of ?? null;
  const matchingCycle = Boolean(
    cycle && cycle.status === "COMPLETE" &&
    currentState?.available === true &&
    currentState?.cycle_status === "COMPLETE" &&
    sameInstant(targetAsOf, cycleAsOf) &&
    sameInstant(currentState?.as_of, cycleAsOf),
  );
  const forwardMarketCurrent = market?.data_health === "CURRENT" &&
    market?.missing_candle_count === 0;
  const runnerActive = ACTIVE_RUNNER_STATES.has(runnerStatus) && !runner?.last_error;
  const cycleCurrent = Boolean(
    runnerActive && matchingCycle && forwardMarketCurrent && pending === 0,
  );
  const dashboardMarketCurrent = dashboard?.freshness?.status === "CURRENT" &&
    dashboard?.market?.complete === true;
  const boundaryMatches = Boolean(
    sameInstant(dashboard?.meta?.as_of, cycleAsOf) &&
    dashboard?.meta?.symbol === status?.symbol &&
    dashboard?.meta?.timeframe === status?.timeframe,
  );

  const allObservations = Array.isArray(forward?.observations?.observations)
    ? forward.observations.observations
    : [];
  const cycleId = cycle?.cycle_id ?? null;
  const cycleObservations = cycleId
    ? allObservations.filter((row) => row?.cycle_id === cycleId).map(projectObservation)
    : [];
  const activeSetups = cycleObservations.filter((row) => LIVE_SETUP_STATES.has(row.state));

  const qualification = dashboard?.qualification || {};
  const dashboardFocusId = qualification.selected_setup_id || dashboard?.looking_for?.setup_id || null;
  const stateMatches = Boolean(cycle?.snapshot_state &&
    cycle.snapshot_state === qualification.state);
  const focusedObservation = dashboardFocusId
    ? cycleObservations.find((row) => row.id === dashboardFocusId) || null
    : null;
  const focusMatches = dashboardFocusId
    ? Boolean(focusedObservation && focusedObservation.state === qualification.state)
    : qualification.state === "NO_SETUP" && activeSetups.length === 0;
  const dashboardPlan = dashboard?.plan && typeof dashboard.plan === "object"
    ? dashboard.plan
    : null;
  const recordedPlan = focusedObservation?.plan || null;
  const dashboardPlanState = dashboardPlan?.state ?? null;
  const recordedPlanState = focusedObservation?.planState ?? null;
  let planMatches = true;
  if (dashboardPlan || recordedPlanState !== null) {
    planMatches = Boolean(focusedObservation && dashboardPlanState === recordedPlanState);
    if (planMatches && dashboardPlanState === "PLANNABLE") {
      planMatches = Boolean(dashboardPlan?.id && recordedPlan?.id &&
        dashboardPlan.id === recordedPlan.id);
    } else if (planMatches && (dashboardPlan?.id || recordedPlan?.id)) {
      planMatches = Boolean(dashboardPlan?.id && recordedPlan?.id &&
        dashboardPlan.id === recordedPlan.id);
    }
  }
  const dashboardMatchesCycle = Boolean(
    cycleCurrent && boundaryMatches && dashboardMarketCurrent &&
    stateMatches && focusMatches && planMatches,
  );

  let statusLabel = "UNAVAILABLE";
  let reason = "Forward runner status is unavailable; no current BRAIN decision is shown.";
  if (!status || typeof status !== "object" || !("runner" in status)) {
    statusLabel = "UNAVAILABLE";
  } else if (!runner) {
    statusLabel = "NOT RUN";
    reason = "No runner heartbeat is recorded. Only the last persisted cycle, if any, is shown.";
  } else if (runnerStatus === "STOPPED") {
    statusLabel = "STOPPED";
    reason = "The BRAIN runner is stopped. The last persisted cycle is historical, not a fresh decision.";
  } else if (runner?.last_error || runnerStatus === "ERROR") {
    statusLabel = "ERROR";
    reason = runner?.last_error || "The latest BRAIN runner heartbeat reports an error.";
  } else if (pending !== null && pending > 0) {
    statusLabel = "CATCHING UP";
    reason = `${pending} closed-candle boundary/boundaries remain unprocessed; no current decision is shown.`;
  } else if (!runnerActive) {
    statusLabel = runnerStatus || "UNKNOWN";
    reason = "The runner has no active, error-free heartbeat; no current decision is shown.";
  } else if (market?.data_health !== "CURRENT" || market?.missing_candle_count !== 0) {
    statusLabel = "STALE";
    reason = "Stored market data is stale or gapped; no current decision is shown.";
  } else if (!cycle || cycle.status !== "COMPLETE") {
    statusLabel = "WAITING";
    reason = cycle
      ? "The latest persisted cycle is incomplete and carries no conclusion."
      : "The runner has not persisted a completed closed-candle cycle yet.";
  } else if (!matchingCycle || pending !== 0) {
    statusLabel = "STALE";
    reason = "The latest persisted BRAIN cycle does not match the latest closed-candle boundary.";
  } else {
    statusLabel = "CURRENT";
    reason = dashboardMatchesCycle
      ? "Latest complete BRAIN cycle and dashboard match this confirmed closed-candle boundary."
      : "The persisted BRAIN cycle is current, but the dashboard setup/plan projection does not match its recorded observation; recomputed decision panels are withheld.";
  }

  const notes = Array.isArray(cycle?.notes)
    ? cycle.notes
    : Array.isArray(currentState?.notes)
      ? currentState.notes
      : [];

  return {
    cycleCurrent,
    currentDecisionAvailable: dashboardMatchesCycle,
    statusLabel,
    runnerStatus: runnerStatus || (status && "runner" in status ? "NOT RUN" : "UNKNOWN"),
    reason,
    runner,
    cycle,
    cycleAsOf,
    cycleStatus: cycle?.status ?? currentState?.cycle_status ?? null,
    snapshotState: cycle?.snapshot_state ?? currentState?.setup_state ?? null,
    setupStateCounts: countMap(currentState?.setup_state_counts ?? cycle?.setup_state_counts),
    planStateCounts: countMap(currentState?.plan_state_counts ?? cycle?.plan_state_counts),
    explanation: cycle?.explanation_headline ?? currentState?.explanation_headline ?? null,
    notes,
    observations: cycleObservations,
    activeSetups,
    snapshot: parseJson(cycle?.snapshot_json),
  };
}
