/** The global header has one updater and one backend truth on every route. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  refreshTopbar,
  setTopbarWarning,
  systemHealthViewModel,
  updateTopbar,
} from "../../src/trading_assistant/web/static/js/topbar.js";

const TIMESTAMP = "2026-10-06T12:00:00Z";

function headerDashboard() {
  return {
    meta: { exchange: "binance", symbol: "BTC/USDT", timeframe: "1h", as_of: TIMESTAMP },
    market: {
      latest_closed_candle: {
        timestamp: TIMESTAMP,
        open: "62080",
        high: "62200",
        low: "62000",
        close: "62160",
        volume: "15.2",
      },
      complete: true,
    },
    freshness: { status: "CURRENT" },
  };
}

/**
 * The server-owned liveness verdict (``status.runner_liveness``), exactly as
 * ``ForwardTestService.status()`` renders it. The header quotes it verbatim, so
 * every fixture that expects a verdict must ask the server for one.
 */
function headerLiveness(overrides = {}) {
  return {
    policy_version: "runner-liveness-v1",
    state: "CONFIRMED_ALIVE",
    alive_confirmed: true,
    heartbeat_present: true,
    heartbeat_status: "IDLE",
    recorded_at: TIMESTAMP,
    assessed_at: TIMESTAMP,
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

function headerForward(runnerOverrides = {}, livenessOverrides = {}) {
  return {
    status: {
      market_data: { data_health: "CURRENT" },
      runner: {
        status: "IDLE",
        recorded_at: TIMESTAMP,
        pending_boundaries: 0,
        last_error: null,
        ...runnerOverrides,
      },
      runner_liveness: headerLiveness(livenessOverrides),
      sample: { pending_catch_up_boundaries: 0 },
    },
  };
}

/** Replace the verdict on an existing payload (as the server would report it). */
function withLiveness(forward, overrides) {
  forward.status.runner_liveness = headerLiveness(overrides);
  return forward;
}

function headerNodes() {
  const ids = new Map();
  for (const id of ["topbar-symbol", "topbar-timeframe", "topbar-candle-time", "topbar-price", "topbar-status"]) {
    ids.set(id, {
      textContent: "",
      dataset: {},
      attributes: {},
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
    });
  }
  return ids;
}

async function withHeaderGlobals({ fetchImpl, run }) {
  const keys = ["document", "localStorage", "fetch"];
  const saved = new Map(keys.map((key) => [
    key,
    { present: Object.hasOwn(globalThis, key), value: globalThis[key] },
  ]));
  const ids = headerNodes();
  globalThis.document = { getElementById: (id) => ids.get(id) || null };
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  if (fetchImpl) globalThis.fetch = fetchImpl;
  try {
    await run(ids);
  } finally {
    for (const [key, prior] of saved) {
      if (prior.present) globalThis[key] = prior.value;
      else delete globalThis[key];
    }
  }
}

test("updateTopbar populates every header field from backend facts", async () => {
  await withHeaderGlobals({
    run: async (ids) => {
      updateTopbar(headerDashboard(), headerForward());
      assert.equal(ids.get("topbar-symbol").textContent, "BTC/USDT");
      assert.equal(ids.get("topbar-timeframe").textContent, "1H");
      assert.equal(ids.get("topbar-candle-time").textContent, "2026-10-06 12:00 UTC");
      assert.equal(ids.get("topbar-candle-time").attributes.datetime, TIMESTAMP);
      assert.equal(ids.get("topbar-price").textContent, "62,160.00");
      assert.match(ids.get("topbar-status").textContent, /^DATA CURRENT · RUNNER IDLE$/);
    },
  });
});

test("missing backend facts stay UNKNOWN instead of inventing a header", async () => {
  await withHeaderGlobals({
    run: async (ids) => {
      updateTopbar({}, null);
      assert.equal(ids.get("topbar-symbol").textContent, "UNKNOWN");
      assert.equal(ids.get("topbar-timeframe").textContent, "UNKNOWN");
      assert.equal(ids.get("topbar-candle-time").textContent, "UNKNOWN");
      assert.equal(ids.get("topbar-price").textContent, "UNKNOWN");
      // Names the component that is failing, never a generic warning.
      assert.match(ids.get("topbar-status").textContent, /^(MARKET DATA|FORWARD RUNNER) · /);
    },
  });
});

test("refreshTopbar fetches the same truth other routes render without", async () => {
  const seen = [];
  const fetchImpl = async (path) => {
    seen.push(String(path));
    const payload = String(path).startsWith("/api/dashboard") ? headerDashboard() : headerForward();
    return { ok: true, json: async () => payload };
  };
  await withHeaderGlobals({
    fetchImpl,
    run: async (ids) => {
      await refreshTopbar();
      assert.equal(ids.get("topbar-symbol").textContent, "BTC/USDT");
      assert.equal(ids.get("topbar-price").textContent, "62,160.00");
      assert.match(ids.get("topbar-status").textContent, /^DATA CURRENT · RUNNER IDLE$/);
      assert.ok(seen.some((path) => path.startsWith("/api/dashboard")));
      assert.ok(seen.some((path) => path.startsWith("/api/forward")));
    },
  });
});

test("a stale overlapping refresh never overwrites a newer header", async () => {
  const releaseFirst = [];
  let calls = 0;
  const fetchImpl = (path) => {
    calls += 1;
    const mine = calls;
    const payload = String(path).startsWith("/api/dashboard")
      ? { ...headerDashboard(), meta: { symbol: `BTC/USDT#${mine}`, timeframe: "1h" } }
      : headerForward();
    if (mine <= 2) {
      return new Promise((resolve) => {
        releaseFirst.push(() => resolve({ ok: true, json: async () => payload }));
      });
    }
    return Promise.resolve({ ok: true, json: async () => payload });
  };
  await withHeaderGlobals({
    fetchImpl,
    run: async (ids) => {
      const first = refreshTopbar();
      const second = refreshTopbar();
      await second;
      // The second (newer) refresh populated the header; releasing the
      // first must not clobber it.
      assert.match(ids.get("topbar-symbol").textContent, /#3|#4/);
      for (const release of releaseFirst) release();
      await first;
      assert.match(ids.get("topbar-symbol").textContent, /#3|#4/);
    },
  });
});

test("a failed header fetch falls back to the warning header", async () => {
  const fetchImpl = async () => ({ ok: false, status: 500, json: async () => ({}) });
  await withHeaderGlobals({
    fetchImpl,
    run: async (ids) => {
      await refreshTopbar();
      assert.equal(ids.get("topbar-symbol").textContent, "UNKNOWN");
      assert.equal(ids.get("topbar-status").textContent, "DASHBOARD UNAVAILABLE");
      // The exported warning reset is the same state.
      setTopbarWarning();
      assert.equal(ids.get("topbar-price").textContent, "UNKNOWN");
    },
  });
});

test("runner details distinguish unavailable, never-run, and reported", async () => {
  const { runnerDetailsViewModel } = await import(
    "../../src/trading_assistant/web/static/js/topbar.js"
  );
  const unavailable = runnerDetailsViewModel(null);
  assert.equal(unavailable.presence, "unavailable");
  assert.equal(unavailable.state, "UNKNOWN");
  assert.equal(unavailable.pending, "UNKNOWN");

  const malformed = runnerDetailsViewModel({ status: {} });
  assert.equal(malformed.presence, "unavailable");

  const neverRun = runnerDetailsViewModel({
    status: { runner: null, sample: { pending_catch_up_boundaries: 5 } },
  });
  assert.equal(neverRun.presence, "never-run");
  assert.equal(neverRun.state, "never run");
  assert.equal(neverRun.pending, "5 closed candles not yet processed");
  assert.equal(neverRun.lastError, "not applicable (the runner has never run)");
  // Without a server verdict the details row says so; it never claims liveness.
  assert.equal(neverRun.livenessState, "UNREPORTED");
  assert.match(neverRun.heartbeat, /liveness unknown/i);

  const reported = runnerDetailsViewModel({
    status: {
      runner: {
        status: "PROCESSED",
        detail: "cycle complete",
        recorded_at: TIMESTAMP,
        heartbeat_age_seconds: 125,
        latest_cycle_as_of: TIMESTAMP,
        pending_boundaries: 0,
        last_error: null,
      },
      sample: { pending_catch_up_boundaries: 0 },
    },
  });
  assert.equal(reported.presence, "reported");
  assert.equal(reported.state, "Processed");
  assert.equal(reported.pending, "0 closed candles not yet processed");
  // No server verdict in this payload: the age is shown and liveness stays
  // unconfirmed rather than being read as a running runner.
  assert.equal(reported.heartbeat, "2026-10-06 12:00 UTC (125s ago) — liveness unknown");
  assert.equal(reported.livenessState, "UNREPORTED");
  assert.equal(reported.lastError, "No errors reported");

  const stale = runnerDetailsViewModel(withLiveness({
    status: {
      runner: {
        status: "IDLE",
        detail: "no new closed candle to process",
        recorded_at: TIMESTAMP,
        heartbeat_age_seconds: 1800,
        latest_cycle_as_of: TIMESTAMP,
        pending_boundaries: 0,
        last_error: null,
      },
      sample: { pending_catch_up_boundaries: 0 },
    },
  }, {
    state: "STALE_UNCONFIRMED",
    alive_confirmed: false,
    age_seconds: 1800,
    detail: "The newest heartbeat (IDLE) was recorded 1800s ago, beyond the 360s tolerance.",
  }));
  assert.equal(stale.livenessState, "STALE_UNCONFIRMED");
  assert.equal(stale.heartbeat, "2026-10-06 12:00 UTC (1800s ago) — not confirmed alive");

  const errored = runnerDetailsViewModel({
    status: { runner: { status: "ERROR", last_error: "boom", recorded_at: null } },
  });
  assert.equal(errored.state, "Error");
  assert.equal(errored.lastError, "boom");
  assert.equal(errored.heartbeat, "UNKNOWN");
});

test("system verdict needs every health conjunct, and liveness is the server's", () => {
  const ok = systemHealthViewModel(headerDashboard(), headerForward());
  assert.equal(ok.healthy, true);
  assert.equal(ok.label, "DATA CURRENT · RUNNER IDLE");
  assert.equal(ok.tone, "green");

  // Each single failure flips the verdict to WARNING.
  const staleFreshness = headerDashboard();
  staleFreshness.freshness.status = "STALE";
  assert.equal(systemHealthViewModel(staleFreshness, headerForward()).label, "MARKET DATA · FRESHNESS STALE");

  const gappyMarket = headerDashboard();
  gappyMarket.market.complete = false;
  assert.equal(systemHealthViewModel(gappyMarket, headerForward()).label, "MARKET DATA · STORED WINDOW INCOMPLETE");

  const staleForward = headerForward();
  staleForward.status.market_data.data_health = "STALE";
  assert.equal(systemHealthViewModel(headerDashboard(), staleForward).label, "FORWARD DATA HEALTH · DATA HEALTH STALE");

  const pending = headerForward();
  pending.status.sample.pending_catch_up_boundaries = 1;
  assert.equal(systemHealthViewModel(headerDashboard(), pending).label, "FORWARD RUNNER · CATCH-UP PENDING");

  const errored = withLiveness(
    headerForward({ status: "ERROR", last_error: "boom" }),
    { state: "FAILED", alive_confirmed: false, heartbeat_status: "ERROR", last_error: "boom" },
  );
  assert.equal(systemHealthViewModel(headerDashboard(), errored).label, "FORWARD RUNNER · PROCESSING FAILURE");
  assert.equal(systemHealthViewModel(headerDashboard(), errored).tone, "red");

  const neverRun = headerForward();
  neverRun.status.runner = null;
  neverRun.status.runner_liveness = headerLiveness({
    state: "NEVER_RUN",
    alive_confirmed: false,
    heartbeat_present: false,
    heartbeat_status: null,
    recorded_at: null,
    age_seconds: null,
    cadence_gap_count: 0,
    heartbeat_rows_examined: 0,
  });
  assert.equal(systemHealthViewModel(headerDashboard(), neverRun).label, "FORWARD RUNNER · NEVER REPORTED");

  const noData = withLiveness(
    headerForward({ status: "NO_DATA" }),
    { heartbeat_status: "NO_DATA" },
  );
  assert.equal(systemHealthViewModel(headerDashboard(), noData).label, "FORWARD RUNNER · STATUS NO_DATA");

  assert.equal(systemHealthViewModel(null, null).label, "DASHBOARD DATA · NOT LOADED");

  // REPAIR (docs/runner-health.md): the header reads the server verdict, never
  // the raw age. A stale verdict with otherwise perfect health — current
  // candles, no backlog, a running status — is now amber, not green. The
  // backend suite pins that the server emits this verdict for exactly this
  // payload (test_status_reports_a_stale_runner_while_market_data_stays_current).
  const stale = withLiveness(
    headerForward({ heartbeat_age_seconds: 99_999 }, { heartbeat_age_seconds: 99_999 }),
    {
      state: "STALE_UNCONFIRMED",
      alive_confirmed: false,
      heartbeat_status: "IDLE",
      age_seconds: 99_999,
      detail:
        "The newest heartbeat (IDLE) was recorded 99999s ago, beyond the 360s " +
        "tolerance (policy floor), so the runner is not confirmed alive.",
    },
  );
  const staleHealth = systemHealthViewModel(headerDashboard(), stale);
  assert.equal(staleHealth.healthy, false);
  assert.equal(staleHealth.tone, "amber");
  assert.equal(staleHealth.label, "FORWARD RUNNER · HEARTBEAT STALE");
  assert.match(staleHealth.primary.detail, /not confirmed alive/);

  // The browser never re-derives the verdict: the same age with a server
  // verdict inside its own stated tolerance is quoted as the server reported it.
  const insideOperatorTolerance = withLiveness(
    headerForward({ heartbeat_age_seconds: 99_999 }, { heartbeat_age_seconds: 99_999 }),
    {
      age_seconds: 99_999,
      tolerance_seconds: 100_000,
      tolerance_source: "operator_setting",
      detail:
        "The newest heartbeat (IDLE) was recorded 99999s ago, inside the 100000s " +
        "tolerance (operator setting), so the runner process is confirmed alive.",
    },
  );
  assert.equal(systemHealthViewModel(headerDashboard(), insideOperatorTolerance).label,
    "DATA CURRENT · RUNNER IDLE");

  // Without a verdict nothing is promoted to healthy, however current the
  // candles are: the browser has no liveness clock of its own.
  const unreported = headerForward();
  delete unreported.status.runner_liveness;
  const unreportedHealth = systemHealthViewModel(headerDashboard(), unreported);
  assert.equal(unreportedHealth.healthy, false);
  assert.equal(unreportedHealth.tone, "red");
  assert.equal(unreportedHealth.label, "FORWARD RUNNER · LIVENESS UNKNOWN");
  assert.match(unreportedHealth.primary.detail, /server/i);

  // A future heartbeat is never healthy: the clocks disagree, so liveness is
  // unconfirmed (never clamped away by a small allowance).
  const future = withLiveness(
    headerForward({ heartbeat_age_seconds: -3 }, { heartbeat_age_seconds: -3 }),
    {
      state: "CLOCK_INCONSISTENT",
      alive_confirmed: false,
      age_seconds: -3,
      detail: "The newest heartbeat (IDLE) is 3s ahead of this server's clock, so liveness is not confirmed.",
    },
  );
  const futureHealth = systemHealthViewModel(headerDashboard(), future);
  assert.equal(futureHealth.healthy, false);
  assert.equal(futureHealth.tone, "red");
  assert.equal(futureHealth.label, "FORWARD RUNNER · HEARTBEAT IN THE FUTURE");

  // Recovery clears: a fresh heartbeat makes the server confirm liveness again
  // (the backend test pins that the newest row is what the server sends).
  const recovered = withLiveness(
    headerForward({ status: "PROCESSED" }),
    { heartbeat_status: "PROCESSED", age_seconds: 4 },
  );
  assert.equal(systemHealthViewModel(headerDashboard(), recovered).label, "DATA CURRENT · RUNNER PROCESSED");
});

test("health labels are specific: no generic SYSTEM OK or SYSTEM WARNING can be produced", () => {
  const cases = [
    [headerDashboard(), headerForward()],
    [null, null],
    [headerDashboard(), null],
    [{ ...headerDashboard(), freshness: { status: "HISTORICAL", reason: "as_of_is_not_the_current_boundary" } }, headerForward()],
  ];
  for (const [dashboard, forward] of cases) {
    const health = systemHealthViewModel(dashboard, forward);
    assert.doesNotMatch(health.label, /SYSTEM (OK|WARNING)/);
  }
});

test("each failing condition is listed with its component, most severe first", () => {
  const broken = withLiveness(
    headerForward({ status: "ERROR", last_error: "fetch failed" }),
    { state: "FAILED", alive_confirmed: false, heartbeat_status: "ERROR", last_error: "fetch failed",
      detail: "The newest heartbeat (ERROR) was recorded 12s ago and reports a failure: fetch failed." },
  );
  broken.status.sample.pending_catch_up_boundaries = 2;
  const dashboard = headerDashboard();
  dashboard.freshness.status = "STALE";
  dashboard.freshness.reason = "as_of_is_not_the_current_boundary";
  const health = systemHealthViewModel(dashboard, broken);
  assert.equal(health.healthy, false);
  // Processing failure, stale freshness, then catch-up backlog; forward data health is current here.
  assert.deepEqual(health.issues.map((i) => i.severity), [0, 1, 2]);
  assert.equal(health.primary.condition, "Processing failure");
  assert.match(health.primary.detail, /fetch failed/);
  assert.match(health.issues.find((i) => i.component === "Market data").detail, /not the newest closed-candle boundary/);
  assert.equal(health.tone, "red");
});

// Regression (PR #38 follow-up): a lagging default view must name the stored end and the missing
// candle, never "Freshness STALE" with a generic reason.
test("a stale series names the stored end, the candle that should exist, and how far behind", () => {
  const dashboard = {
    ...headerDashboard(),
    freshness: {
      status: "STALE",
      reason: "stored_candles_stop_before_expected_boundary",
      latest_stored: "2026-10-10T17:00:00Z",
      expected_latest_closed: "2026-10-10T18:00:00Z",
      staleness_intervals: 1,
    },
  };
  const issue = systemHealthViewModel(dashboard, headerForward()).issues.find(
    (item) => item.condition === "Freshness STALE",
  );
  assert.ok(issue, "a STALE freshness issue is reported");
  assert.match(issue.detail, /end at 2026-10-10 17:00 UTC/);
  assert.match(issue.detail, /should be stored is 2026-10-10 18:00 UTC/);
  assert.match(issue.detail, /1 candle behind the clock/);
  assert.match(issue.detail, /Binance ingestion process/);
});
