/**
 * Runner heartbeat liveness in the global header (docs/runner-health.md).
 *
 * The dashboard used to report a healthy "RUNNER STARTED/PROCESSED/IDLE" from
 * the newest heartbeat's status alone: heartbeat age was displayed and never
 * decisive, so a runner that had been silently dead for days still read green
 * while its stored candles stayed current and no catch-up was pending.
 *
 * These tests pin the repaired contract:
 *
 * 1. liveness is SERVER-OWNED — the header reads `status.runner_liveness` and
 *    never computes an age from the browser clock;
 * 2. a stale, missing, future, stopped or failed heartbeat is never healthy;
 * 3. market freshness stays a separate dimension — a stale heartbeat means
 *    "not confirmed alive", never "the process died" and never "data is stale";
 * 4. the operational tolerance (including a custom runner cadence or a long
 *    processing pass) comes from the server, verbatim.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  runnerDetailsViewModel,
  runnerLivenessViewModel,
  systemHealthViewModel,
} from "../../src/trading_assistant/web/static/js/topbar.js";

const TOPBAR_PATH = fileURLToPath(
  new URL("../../src/trading_assistant/web/static/js/topbar.js", import.meta.url),
);

const RECORDED_AT = "2026-10-06T12:00:00Z";
const ASSESSED_AT = "2026-10-06T12:30:00Z";

/** A dashboard payload whose market data is perfectly current and complete. */
function currentDashboard() {
  return {
    meta: { exchange: "binance", symbol: "BTC/USDT", timeframe: "1h", as_of: RECORDED_AT },
    market: {
      latest_closed_candle: { timestamp: RECORDED_AT, close: "62160" },
      complete: true,
    },
    freshness: { status: "CURRENT" },
  };
}

/**
 * The server-owned liveness block exactly as `ForwardTestService.status()`
 * renders it (see `trading_assistant.forward_testing.liveness`).
 */
function liveness(overrides = {}) {
  return {
    policy_version: "runner-liveness-v1",
    state: "CONFIRMED_ALIVE",
    alive_confirmed: true,
    heartbeat_present: true,
    heartbeat_status: "IDLE",
    recorded_at: RECORDED_AT,
    assessed_at: ASSESSED_AT,
    age_seconds: 30,
    tolerance_seconds: 360,
    tolerance_source: "policy_floor",
    cadence_seconds: null,
    cadence_source: "unconfirmed",
    cadence_gap_count: 0,
    heartbeat_rows_examined: 1,
    clock_skew_diagnostic_seconds: 5,
    configured_poll_interval_seconds: "60",
    last_error: null,
    detail:
      "The newest heartbeat (IDLE) was recorded 30s ago, inside the 360s tolerance " +
      "(policy floor), so the runner process is confirmed alive.",
    ...overrides,
  };
}

/** A forward payload with current data, no backlog, and the given runner facts. */
function forwardWith(runner, runnerLiveness) {
  const status = {
    market_data: { data_health: "CURRENT" },
    sample: { pending_catch_up_boundaries: 0 },
    runner,
  };
  if (runnerLiveness !== undefined) status.runner_liveness = runnerLiveness;
  return { status };
}

function idleRunner(overrides = {}) {
  return {
    status: "IDLE",
    recorded_at: RECORDED_AT,
    heartbeat_age_seconds: 30,
    latest_cycle_as_of: RECORDED_AT,
    pending_boundaries: 0,
    last_error: null,
    detail: "no new closed candle to process",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// 1. The reported defect: an old heartbeat must not read healthy
// ---------------------------------------------------------------------------

test("REGRESSION: a stale heartbeat with current candles and no backlog is not healthy", () => {
  // The exact reported false-healthy case: stored candles are CURRENT, nothing
  // is pending, the newest heartbeat says IDLE — and it is 30 minutes old.
  const legacyPayload = forwardWith(
    idleRunner({ heartbeat_age_seconds: 1800, recorded_at: "2026-10-06T12:00:00Z" }),
    liveness({
      state: "STALE_UNCONFIRMED",
      alive_confirmed: false,
      age_seconds: 1800,
      detail:
        "The newest heartbeat (IDLE) was recorded 1800s ago, beyond the 360s tolerance " +
        "(policy floor), so the runner is not confirmed alive. Stale is not proof that " +
        "the process stopped: no heartbeat has been recorded since.",
    }),
  );
  const health = systemHealthViewModel(currentDashboard(), legacyPayload);
  assert.equal(health.healthy, false);
  assert.equal(health.tone, "amber");
  assert.equal(health.label, "FORWARD RUNNER · HEARTBEAT STALE");
  assert.match(health.primary.detail, /1800s ago/);
  assert.match(health.primary.detail, /360s tolerance/);
  // Stale is unconfirmed, never a claim that the process died.
  assert.doesNotMatch(health.primary.detail, /\b(dead|died|crashed)\b/i);
  // Market data is still current: the runner issue must not be reported as a
  // market-data problem.
  assert.equal(health.primary.component, "Forward runner");
  assert.equal(
    health.issues.filter((issue) => issue.component === "Market data").length,
    0,
  );
});

test("REGRESSION: without a server verdict an old heartbeat is still not healthy", () => {
  // A payload from a backend that does not send `runner_liveness` must never be
  // promoted to healthy: the browser has no business deciding liveness itself.
  const payload = forwardWith(idleRunner({ heartbeat_age_seconds: 99999 }));
  const health = systemHealthViewModel(currentDashboard(), payload);
  assert.equal(health.healthy, false);
  assert.equal(health.label, "FORWARD RUNNER · LIVENESS UNKNOWN");
  assert.match(health.primary.detail, /server/i);
});

test("REGRESSION: a missing heartbeat is never healthy", () => {
  const payload = forwardWith(null, liveness({
    state: "NEVER_RUN",
    alive_confirmed: false,
    heartbeat_present: false,
    heartbeat_status: null,
    recorded_at: null,
    age_seconds: null,
    cadence_gap_count: 0,
    heartbeat_rows_examined: 0,
    detail:
      "No heartbeat row exists for BTC/USDT 1h: the forward runner has never recorded a pass.",
  }));
  const health = systemHealthViewModel(currentDashboard(), payload);
  assert.equal(health.healthy, false);
  assert.equal(health.label, "FORWARD RUNNER · NEVER REPORTED");
});

// ---------------------------------------------------------------------------
// 2. Every liveness state the server can report
// ---------------------------------------------------------------------------

test("a recent, clock-consistent heartbeat confirms the runner and reads healthy", () => {
  const health = systemHealthViewModel(currentDashboard(), forwardWith(idleRunner(), liveness()));
  assert.equal(health.healthy, true);
  assert.equal(health.tone, "green");
  assert.equal(health.label, "DATA CURRENT · RUNNER IDLE");
});

test("a failed heartbeat is red and names the recorded error", () => {
  const payload = forwardWith(
    idleRunner({ status: "ERROR", last_error: "ConnectionError: DNS failure" }),
    liveness({
      state: "FAILED",
      alive_confirmed: false,
      heartbeat_status: "ERROR",
      age_seconds: 12,
      last_error: "ConnectionError: DNS failure",
      detail:
        "The newest heartbeat (ERROR) was recorded 12s ago and reports a failure: " +
        "ConnectionError: DNS failure.",
    }),
  );
  const health = systemHealthViewModel(currentDashboard(), payload);
  assert.equal(health.healthy, false);
  assert.equal(health.tone, "red");
  assert.equal(health.label, "FORWARD RUNNER · PROCESSING FAILURE");
  assert.match(health.primary.detail, /DNS failure/);
});

test("a future heartbeat is never healthy: the clocks disagree", () => {
  const payload = forwardWith(
    idleRunner({ recorded_at: "2026-10-06T14:00:00Z", heartbeat_age_seconds: -7200 }),
    liveness({
      state: "CLOCK_INCONSISTENT",
      alive_confirmed: false,
      recorded_at: "2026-10-06T14:00:00Z",
      age_seconds: -7200,
      detail:
        "The newest heartbeat (IDLE) claims recorded_at 2026-10-06T14:00:00Z, 7200s " +
        "ahead of this server's clock (clock-drift diagnostic band 5s), so no " +
        "time-based verdict is trusted and the runner is not confirmed alive.",
    }),
  );
  const health = systemHealthViewModel(currentDashboard(), payload);
  assert.equal(health.healthy, false);
  assert.equal(health.tone, "red");
  assert.equal(health.label, "FORWARD RUNNER · HEARTBEAT IN THE FUTURE");
  assert.match(health.primary.detail, /clock/i);
});

test("REGRESSION: a future heartbeat is never healthy, however small the gap", () => {
  // A negative age is never a liveness certificate: +1s and +3s are as
  // unconfirmed as a large future instant. The diagnostic band only explains
  // the wording; it can never buy an all-clear.
  for (const age of [-1, -3, -3_600, -7_200]) {
    const payload = forwardWith(
      idleRunner({ recorded_at: "2026-10-06T14:00:00Z", heartbeat_age_seconds: age }),
      liveness({
        state: "CLOCK_INCONSISTENT",
        alive_confirmed: false,
        recorded_at: "2026-10-06T14:00:00Z",
        age_seconds: age,
        detail:
          `The newest heartbeat (IDLE) claims recorded_at 2026-10-06T14:00:00Z, ` +
          `${-age}s ahead of this server's clock, so liveness is not confirmed.`,
      }),
    );
    const health = systemHealthViewModel(currentDashboard(), payload);
    assert.equal(health.healthy, false, `age ${age}`);
    assert.equal(health.tone, "red", `age ${age}`);
    assert.equal(health.primary.condition, "Heartbeat in the future", `age ${age}`);

    const model = runnerLivenessViewModel(payload);
    assert.equal(model.aliveConfirmed, false, `age ${age}`);
    assert.equal(model.ageSeconds, age, `age ${age}`);
  }
});

test("a stopped runner is reported as stopped, not as running", () => {
  const payload = forwardWith(
    idleRunner({ status: "STOPPED", detail: "forward runner stopped cleanly" }),
    liveness({
      state: "STOPPED",
      alive_confirmed: false,
      heartbeat_status: "STOPPED",
      age_seconds: 300,
      detail:
        "The newest heartbeat (STOPPED) records a clean stop 300s ago: the runner is not " +
        "running. Recorded history is intact.",
    }),
  );
  const health = systemHealthViewModel(currentDashboard(), payload);
  assert.equal(health.healthy, false);
  assert.equal(health.tone, "amber");
  assert.equal(health.label, "FORWARD RUNNER · RUNNER STOPPED");
});

test("recovery clears: a fresh heartbeat after a failure reads healthy again", () => {
  const failed = systemHealthViewModel(
    currentDashboard(),
    forwardWith(
      idleRunner({ status: "ERROR", last_error: "boom" }),
      liveness({ state: "FAILED", alive_confirmed: false, heartbeat_status: "ERROR", last_error: "boom" }),
    ),
  );
  assert.equal(failed.healthy, false);

  const recovered = systemHealthViewModel(
    currentDashboard(),
    forwardWith(
      idleRunner({ status: "PROCESSED", recorded_at: ASSESSED_AT, heartbeat_age_seconds: 4 }),
      liveness({
        heartbeat_status: "PROCESSED",
        recorded_at: ASSESSED_AT,
        age_seconds: 4,
        detail:
          "The newest heartbeat (PROCESSED) was recorded 4s ago, inside the 360s tolerance " +
          "(policy floor), so the runner process is confirmed alive.",
      }),
    ),
  );
  assert.equal(recovered.healthy, true);
  assert.equal(recovered.label, "DATA CURRENT · RUNNER PROCESSED");
  assert.equal(recovered.issues.length, 0);
});

test("an unrecognised server state is unknown, never healthy", () => {
  const health = systemHealthViewModel(
    currentDashboard(),
    forwardWith(idleRunner(), liveness({ state: "SOMETHING_NEW", alive_confirmed: false })),
  );
  assert.equal(health.healthy, false);
  assert.equal(health.label, "FORWARD RUNNER · LIVENESS UNKNOWN");
});

// ---------------------------------------------------------------------------
// 3. Liveness and market freshness stay separate dimensions
// ---------------------------------------------------------------------------

test("a stale runner does not make current market data look stale", () => {
  const health = systemHealthViewModel(
    currentDashboard(),
    forwardWith(
      idleRunner({ heartbeat_age_seconds: 5400 }),
      liveness({ state: "STALE_UNCONFIRMED", alive_confirmed: false, age_seconds: 5400 }),
    ),
  );
  assert.deepEqual(
    health.issues.map((issue) => issue.component),
    ["Forward runner"],
  );
  assert.equal(health.issues[0].condition, "Heartbeat stale");
});

test("stale market data with a live runner names market data only", () => {
  const dashboard = currentDashboard();
  dashboard.freshness = {
    status: "STALE",
    reason: "stored_candles_stop_before_expected_boundary",
    latest_stored: "2026-10-06T11:00:00Z",
    expected_latest_closed: "2026-10-06T12:00:00Z",
    staleness_intervals: 1,
  };
  const health = systemHealthViewModel(dashboard, forwardWith(idleRunner(), liveness()));
  assert.equal(health.label, "MARKET DATA · FRESHNESS STALE");
  assert.equal(
    health.issues.some((issue) => issue.component === "Forward runner"),
    false,
  );
});

test("a live runner with a NO_DATA heartbeat keeps the market verdict separate", () => {
  // NO_DATA is a statement about the market, not about the process: liveness is
  // confirmed, and the existing status condition still surfaces on its own.
  const health = systemHealthViewModel(
    currentDashboard(),
    forwardWith(
      idleRunner({ status: "NO_DATA" }),
      liveness({ heartbeat_status: "NO_DATA", state: "CONFIRMED_ALIVE", alive_confirmed: true }),
    ),
  );
  assert.equal(health.healthy, false);
  assert.equal(health.label, "FORWARD RUNNER · STATUS NO_DATA");
});

// ---------------------------------------------------------------------------
// 4. The tolerance and cadence are the server's, quoted verbatim
// ---------------------------------------------------------------------------

test("a custom cadence tolerance is quoted from the server, not recomputed", () => {
  const model = runnerLivenessViewModel(
    forwardWith(
      idleRunner({ heartbeat_age_seconds: 1500 }),
      liveness({
        age_seconds: 1500,
        tolerance_seconds: 1800,
        tolerance_source: "observed_cadence",
        cadence_seconds: 600,
        cadence_source: "observed_heartbeats",
        cadence_gap_count: 11,
        heartbeat_rows_examined: 12,
        detail:
          "The newest heartbeat (IDLE) was recorded 1500s ago, inside the 1800s tolerance " +
          "(3x the 600s cadence observed from 11 gaps between the 12 newest heartbeat rows).",
      }),
    ),
  );
  assert.equal(model.state, "CONFIRMED_ALIVE");
  assert.equal(model.aliveConfirmed, true);
  assert.equal(model.toleranceSeconds, 1800);
  assert.equal(model.toleranceSource, "observed_cadence");
  assert.equal(model.cadenceSeconds, 600);
  assert.match(model.detail, /1800s tolerance/);
  assert.match(model.detail, /600s cadence/);
});

test("an operator tolerance override is reported as an operator setting", () => {
  const model = runnerLivenessViewModel(
    forwardWith(
      idleRunner({ heartbeat_age_seconds: 3600 }),
      liveness({
        age_seconds: 3600,
        tolerance_seconds: 7200,
        tolerance_source: "operator_setting",
        detail:
          "The newest heartbeat (IDLE) was recorded 3600s ago, inside the 7200s tolerance " +
          "(operator setting).",
      }),
    ),
  );
  assert.equal(model.state, "CONFIRMED_ALIVE");
  assert.equal(model.toleranceSource, "operator_setting");
  assert.equal(
    systemHealthViewModel(
      currentDashboard(),
      forwardWith(
        idleRunner({ heartbeat_age_seconds: 3600 }),
        liveness({ age_seconds: 3600, tolerance_seconds: 7200, tolerance_source: "operator_setting" }),
      ),
    ).healthy,
    true,
  );
});

test("a missing verdict is reported as unreported, with an honest label", () => {
  const model = runnerLivenessViewModel(forwardWith(idleRunner()));
  assert.equal(model.reported, false);
  assert.equal(model.state, "UNREPORTED");
  assert.equal(model.aliveConfirmed, false);
  assert.match(model.detail, /server/i);

  const unavailable = runnerLivenessViewModel(null);
  assert.equal(unavailable.reported, false);
  assert.equal(unavailable.aliveConfirmed, false);
});

test("a null runner with no verdict is never reported as alive", () => {
  const model = runnerLivenessViewModel(forwardWith(null));
  assert.equal(model.aliveConfirmed, false);
  assert.equal(model.state, "UNREPORTED");
});

// ---------------------------------------------------------------------------
// 5. Server-owned means the browser clock is never consulted
// ---------------------------------------------------------------------------

test("topbar.js never reads the browser clock and never invents an age", () => {
  const source = readFileSync(TOPBAR_PATH, "utf8");
  assert.doesNotMatch(source, /Date\.now\s*\(/);
  assert.doesNotMatch(source, /new Date\s*\(/);
  assert.doesNotMatch(source, /performance\.now\s*\(/);
  // The verdict is read from the server-owned field.
  assert.match(source, /runner_liveness/);
});

test("the runner-details heartbeat row states the server verdict, not just an age", () => {
  const stale = runnerDetailsViewModel(
    forwardWith(
      idleRunner({ heartbeat_age_seconds: 1800 }),
      liveness({
        state: "STALE_UNCONFIRMED",
        alive_confirmed: false,
        age_seconds: 1800,
        detail: "The newest heartbeat (IDLE) was recorded 1800s ago, beyond the 360s tolerance.",
      }),
    ),
  );
  assert.equal(stale.presence, "reported");
  assert.equal(stale.livenessState, "STALE_UNCONFIRMED");
  assert.match(stale.heartbeat, /1800s ago/);
  assert.match(stale.heartbeat, /not confirmed alive/i);

  const live = runnerDetailsViewModel(forwardWith(idleRunner(), liveness()));
  assert.equal(live.livenessState, "CONFIRMED_ALIVE");
  assert.match(live.heartbeat, /confirmed alive/i);

  const unreported = runnerDetailsViewModel(forwardWith(idleRunner()));
  assert.equal(unreported.livenessState, "UNREPORTED");
  assert.match(unreported.heartbeat, /liveness unknown/i);
});
