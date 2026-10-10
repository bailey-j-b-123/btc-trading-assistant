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

function headerForward() {
  return {
    status: {
      market_data: { data_health: "CURRENT" },
      runner: { status: "IDLE", recorded_at: TIMESTAMP, pending_boundaries: 0, last_error: null },
      sample: { pending_catch_up_boundaries: 0 },
    },
  };
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
      assert.equal(ids.get("topbar-status").textContent, "SYSTEM OK");
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
      assert.equal(ids.get("topbar-status").textContent, "SYSTEM WARNING");
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
      assert.equal(ids.get("topbar-status").textContent, "SYSTEM OK");
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
      assert.equal(ids.get("topbar-status").textContent, "SYSTEM WARNING");
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
  assert.equal(reported.heartbeat, "2026-10-06 12:00 UTC (125s ago)");
  assert.equal(reported.lastError, "No errors reported");

  const errored = runnerDetailsViewModel({
    status: { runner: { status: "ERROR", last_error: "boom", recorded_at: null } },
  });
  assert.equal(errored.state, "Error");
  assert.equal(errored.lastError, "boom");
  assert.equal(errored.heartbeat, "UNKNOWN");
});

test("system verdict needs every health conjunct; age alone never warns", () => {
  const ok = systemHealthViewModel(headerDashboard(), headerForward());
  assert.equal(ok.healthy, true);
  assert.equal(ok.label, "SYSTEM OK");
  assert.equal(ok.tone, "green");

  // Each single failure flips the verdict to WARNING.
  const staleFreshness = headerDashboard();
  staleFreshness.freshness.status = "STALE";
  assert.equal(systemHealthViewModel(staleFreshness, headerForward()).label, "SYSTEM WARNING");

  const gappyMarket = headerDashboard();
  gappyMarket.market.complete = false;
  assert.equal(systemHealthViewModel(gappyMarket, headerForward()).label, "SYSTEM WARNING");

  const staleForward = headerForward();
  staleForward.status.market_data.data_health = "STALE";
  assert.equal(systemHealthViewModel(headerDashboard(), staleForward).label, "SYSTEM WARNING");

  const pending = headerForward();
  pending.status.sample.pending_catch_up_boundaries = 1;
  assert.equal(systemHealthViewModel(headerDashboard(), pending).label, "SYSTEM WARNING");

  const errored = headerForward();
  errored.status.runner.status = "ERROR";
  errored.status.runner.last_error = "boom";
  assert.equal(systemHealthViewModel(headerDashboard(), errored).label, "SYSTEM WARNING");

  const neverRun = headerForward();
  neverRun.status.runner = null;
  assert.equal(systemHealthViewModel(headerDashboard(), neverRun).label, "SYSTEM WARNING");

  const noData = headerForward();
  noData.status.runner.status = "NO_DATA";
  assert.equal(systemHealthViewModel(headerDashboard(), noData).label, "SYSTEM WARNING");

  assert.equal(systemHealthViewModel(null, null).label, "SYSTEM WARNING");

  // Heartbeat age is displayed, never decisive: an ancient heartbeat with
  // otherwise perfect health still reads OK.
  const ancient = headerForward();
  ancient.status.runner.heartbeat_age_seconds = 99999;
  assert.equal(systemHealthViewModel(headerDashboard(), ancient).label, "SYSTEM OK");

  // Recovery clears: a healthy newest row reads OK whatever came before
  // (the backend test pins that the newest row is what the backend sends).
  const recovered = headerForward();
  recovered.status.runner.status = "PROCESSED";
  recovered.status.runner.last_error = null;
  assert.equal(systemHealthViewModel(headerDashboard(), recovered).label, "SYSTEM OK");
});
