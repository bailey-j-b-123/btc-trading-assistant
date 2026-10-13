import assert from "node:assert/strict";
import test from "node:test";
import { api, ApiError, DASHBOARD_TIMEOUT_MS } from "../../src/trading_assistant/web/static/js/api.js";

async function withReadFixture(run) {
  const previous = { fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const timers = [];
  globalThis.setTimeout = (fn, ms) => {
    const timer = { fn, ms, cancelled: false };
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = (timer) => { if (timer) timer.cancelled = true; };
  try {
    await run({
      timers,
      expire() {
        const timer = timers.find((item) => !item.cancelled);
        assert.ok(timer, "a pending whole-read deadline must exist");
        assert.equal(timer.ms, DASHBOARD_TIMEOUT_MS, "reuse the existing operational load limit");
        timer.cancelled = true;
        timer.fn();
      },
    });
  } finally {
    Object.assign(globalThis, previous);
  }
}

async function flush() { for (let i = 0; i < 15; i++) await Promise.resolve(); }

for (const method of ["dashboard", "forward"]) {
  for (const phase of ["headers", "body"]) {
    test(`${method} load aborts stalled ${phase}, rejects late data and permits a valid retry`, async () => {
      await withReadFixture(async ({ timers, expire }) => {
        let signal;
        let release;
        const abandoned = new Promise((resolve) => { release = resolve; });
        globalThis.fetch = (_path, options) => {
          signal = options.signal;
          // Deliberately ignore abort: even a late resolver must not win after the deadline.
          return phase === "headers" ? abandoned : Promise.resolve({ ok: true, json: () => abandoned });
        };
        let outcome = null;
        const loading = api[method]({ symbol: "BTC/USDT", timeframe: "5m", limit: 8 })
          .then((value) => { outcome = { value }; }, (error) => { outcome = { error }; });
        await flush();
        try {
          expire();
          await flush();
          assert.ok(signal?.aborted, "the stalled request must receive an abort");
          assert.ok(outcome?.error instanceof ApiError, "loading must finish with a typed failure");
          assert.equal(outcome.error.code, "timeout");
          const failed = outcome;
          release(phase === "headers" ? { ok: true, json: async () => ({ stale: true }) } : { stale: true });
          await loading;
          await flush();
          assert.equal(outcome, failed, "late data cannot replace the timeout");
          const current = { current: true };
          const calls = [];
          globalThis.fetch = async (path, options) => {
            calls.push({ path, options });
            return { ok: true, json: async () => current };
          };
          assert.equal(await api[method]({ symbol: "BTC/USDT", timeframe: "5m", limit: 8 }), current);
          const url = new URL(calls[0].path, "http://fixture.invalid");
          assert.equal(url.pathname, `/api/${method}`);
          assert.equal(url.searchParams.get("symbol"), "BTC/USDT");
          assert.equal(url.searchParams.get("timeframe"), "5m");
          assert.equal(url.searchParams.get("limit"), "8");
          assert.ok(timers.every((timer) => timer.cancelled), "successful retry clears its deadline");
        } finally {
          release(phase === "headers" ? { ok: true, json: async () => null } : null);
          await flush();
        }
      });
    });
  }
}

test("bounded load preserves the server error mapping", async () => {
  await withReadFixture(async ({ timers }) => {
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ error: { code: "fixture_busy", message: "Try later" } }) });
    await assert.rejects(api.forward(), (error) => error instanceof ApiError && error.status === 503 && error.code === "fixture_busy");
    assert.ok(timers.every((timer) => timer.cancelled));
  });
});
