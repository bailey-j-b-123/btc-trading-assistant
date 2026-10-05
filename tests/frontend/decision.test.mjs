/** Decision workflow: explicit choices only, pinned snapshots, no default. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildDecisionRequest,
  confirmationSummary,
  decisionAvailability,
  isValidDecision,
} from "../../src/trading_assistant/web/static/js/decision.js";

const DASHBOARD = {
  meta: { symbol: "BTC/USDT", timeframe: "1h", as_of: "2024-01-01T21:00:00Z" },
  qualification: {
    state: "QUALIFIED",
    selected_setup_id: "setup-123",
    setups: [{ id: "setup-123", state: "QUALIFIED", family: "breakout_retest_continuation", direction: "bullish" }],
  },
  planning: { state: "PLANNABLE" },
  plan: {
    id: "plan-abc",
    entry: { value: "124" },
    stop: { value: "117" },
    invalidation: { value: "117" },
    risk_per_unit: "7",
    targets: [{ level: { value: "138" }, r_multiple: "2" }],
  },
  journal: { can_decide: true, latest_decision: null, disabled_reason: null },
};

test("there is no default decision", () => {
  assert.throws(() => buildDecisionRequest({ decision: undefined, dashboard: DASHBOARD }));
  assert.throws(() => buildDecisionRequest({ decision: "", dashboard: DASHBOARD }));
  assert.throws(() => buildDecisionRequest({ decision: "BUY", dashboard: DASHBOARD }));
  assert.ok(!isValidDecision(undefined));
  assert.ok(!isValidDecision("PENDING")); // never offered as a button
});

test("a valid request pins the decision to the displayed snapshot", () => {
  const body = buildDecisionRequest({ decision: "ACCEPTED", dashboard: DASHBOARD, reason: "  reviewed  " });
  assert.deepEqual(body, {
    decision: "ACCEPTED",
    as_of: "2024-01-01T21:00:00Z",
    setup_id: "setup-123",
    symbol: "BTC/USDT",
    timeframe: "1h",
    reason: "reviewed",
  });
});

test("requests without a snapshot or setup are refused", () => {
  assert.throws(() => buildDecisionRequest({ decision: "ACCEPTED", dashboard: {} }));
  const noSetup = { ...DASHBOARD, qualification: { ...DASHBOARD.qualification, selected_setup_id: null } };
  assert.throws(() => buildDecisionRequest({ decision: "ACCEPTED", dashboard: noSetup }));
});

test("availability mirrors the backend can_decide verdict", () => {
  assert.deepEqual(decisionAvailability(DASHBOARD), { enabled: true, reason: null });
  const disabled = { journal: { can_decide: false, disabled_reason: "Step 6 refused a plan (NO_PLAN)." } };
  const result = decisionAvailability(disabled);
  assert.equal(result.enabled, false);
  assert.match(result.reason, /NO_PLAN/);
  assert.equal(decisionAvailability(null).enabled, false);
});

test("confirmation summary preserves raw level values", () => {
  const summary = confirmationSummary(DASHBOARD);
  assert.equal(summary.entry, "124");
  assert.equal(summary.stop, "117");
  assert.deepEqual(summary.targets, ["138"]);
  assert.equal(summary.setupId, "setup-123");
  assert.equal(summary.asOf, "2024-01-01T21:00:00Z");
});
