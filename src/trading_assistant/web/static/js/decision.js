/**
 * Decision-workflow helpers (pure logic, no DOM).
 *
 * Semantics come from Step 7: ACCEPT/REJECT/SKIP only record Bailey's
 * decision in the append-only journal. Nothing here executes a trade, and
 * there is deliberately no default decision.
 */

export const DECISIONS = ["ACCEPTED", "REJECTED", "SKIPPED"];

export const DECISION_LABELS = {
  ACCEPTED: "Accept",
  REJECTED: "Reject",
  SKIPPED: "Skip",
};

export const DECISION_MEANINGS = {
  ACCEPTED: "Record that Bailey accepted this proposed plan for consideration. This appends a journal decision only — no order is placed.",
  REJECTED: "Record that Bailey actively declined this proposed plan. This appends a journal decision only — nothing is executed.",
  SKIPPED: "Record that Bailey let this proposal pass without an accept/reject judgement. This appends a journal decision only.",
};

export function isValidDecision(value) {
  return DECISIONS.includes(value);
}

/**
 * Build the request body for POST /api/dashboard/decisions.
 * Throws instead of submitting anything incomplete: a decision must always be
 * explicit and pinned to the exact snapshot shown to the user.
 */
export function buildDecisionRequest({ decision, dashboard, reason }) {
  if (!isValidDecision(decision)) {
    throw new Error("decision must be explicitly ACCEPTED, REJECTED, or SKIPPED — there is no default");
  }
  if (!dashboard || !dashboard.meta || !dashboard.meta.as_of) {
    throw new Error("the decision must be pinned to a dashboard snapshot with an as_of instant");
  }
  const setupId =
    dashboard.qualification && dashboard.qualification.selected_setup_id;
  if (!setupId) {
    throw new Error("no selected setup exists in the current snapshot");
  }
  const body = {
    decision,
    as_of: dashboard.meta.as_of,
    setup_id: setupId,
    symbol: dashboard.meta.symbol,
    timeframe: dashboard.meta.timeframe,
  };
  if (typeof reason === "string" && reason.trim().length > 0) {
    body.reason = reason.trim();
  }
  return body;
}

/** Snapshot summary shown in the confirmation dialog (display strings only). */
export function confirmationSummary(dashboard) {
  const q = dashboard.qualification || {};
  const plan = dashboard.plan;
  const setup = (q.setups || []).find((s) => s.id === q.selected_setup_id);
  return {
    symbol: dashboard.meta ? dashboard.meta.symbol : null,
    timeframe: dashboard.meta ? dashboard.meta.timeframe : null,
    asOf: dashboard.meta ? dashboard.meta.as_of : null,
    setupState: setup ? setup.state : null,
    family: setup ? setup.family : null,
    direction: setup ? setup.direction : null,
    setupId: q.selected_setup_id || null,
    planState: dashboard.planning ? dashboard.planning.state : null,
    planId: plan ? plan.id : null,
    entry: plan && plan.entry ? plan.entry.value : null,
    stop: plan && plan.stop ? plan.stop.value : null,
    targets: plan ? (plan.targets || []).map((t) => (t.level ? t.level.value : null)) : [],
    riskPerUnit: plan ? plan.risk_per_unit : null,
  };
}

/** A decision button is usable only for a complete PLANNABLE proposal. */
export function decisionAvailability(dashboard) {
  const journal = dashboard && dashboard.journal;
  if (!journal) {
    return { enabled: false, reason: "Dashboard data is unavailable." };
  }
  if (journal.can_decide === true) {
    return { enabled: true, reason: null };
  }
  return {
    enabled: false,
    reason: journal.disabled_reason || "This proposal cannot currently receive a decision.",
  };
}
