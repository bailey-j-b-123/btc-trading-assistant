import assert from "node:assert/strict";
import test from "node:test";

import { createForwardRefresher } from "../../src/trading_assistant/web/static/js/forward-refresh.js";
import { TRACKER_REFRESH_INTERVAL_MS, TRACKER_REFRESH_MAX_INTERVAL_MS } from "../../src/trading_assistant/web/static/js/paper-tracker.js";

/** Manual scheduler: timers only run when the test fires them. */
function scheduler() {
  const timers = [];
  return {
    timers,
    schedule(fn, ms) {
      const timer = { fn, ms, cancelled: false, fired: false };
      timers.push(timer);
      return timer;
    },
    cancel(timer) { if (timer) timer.cancelled = true; },
    pending() { return timers.filter((timer) => !timer.cancelled && !timer.fired); },
    fire() {
      for (const timer of this.pending()) {
        if (timer.cancelled || timer.fired) continue;
        timer.fired = true;
        timer.fn();
      }
    },
  };
}

/** A fetch whose responses the test resolves explicitly. */
function controlledFetch() {
  const calls = [];
  return {
    calls,
    fetchForward() {
      return new Promise((resolve, reject) => calls.push({ resolve, reject }));
    },
  };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

test("the cadence is the existing 15s tracker interval, backing off on failures up to a bounded maximum", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const applied = [];
  const failures = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward: (payload) => applied.push(payload),
    applyFailure: (info) => failures.push(info),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  assert.equal(clock.pending()[0].ms, TRACKER_REFRESH_INTERVAL_MS);

  clock.fire();
  net.calls.shift().reject(new Error("down"));
  await flush();
  assert.equal(failures.length, 1);
  assert.equal(failures[0].failures, 1);
  assert.equal(clock.pending()[0].ms, TRACKER_REFRESH_INTERVAL_MS * 2, "one failure doubles the delay");

  clock.fire();
  net.calls.shift().reject(new Error("down"));
  await flush();
  assert.equal(clock.pending()[0].ms, TRACKER_REFRESH_INTERVAL_MS * 4);

  for (let i = 0; i < 4; i += 1) {
    clock.fire();
    net.calls.shift().reject(new Error("down"));
    await flush();
  }
  assert.equal(clock.pending()[0].ms, TRACKER_REFRESH_MAX_INTERVAL_MS, "the delay never exceeds the bound");

  clock.fire();
  net.calls.shift().resolve({ status: "ok" });
  await flush();
  assert.deepEqual(applied, [{ status: "ok" }]);
  assert.equal(clock.pending()[0].ms, TRACKER_REFRESH_INTERVAL_MS, "a success resets the cadence");
  refresher.stop();
});

test("one read is in flight at a time and a slow read is never duplicated", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward() {},
    applyFailure() {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  assert.equal(refresher.checkNow() instanceof Promise, true);
  assert.equal(await refresher.checkNow(), false, "a second request while one is in flight is skipped");
  assert.equal(net.calls.length, 1);
  assert.equal(refresher.state().inFlight, true);
  net.calls.shift().resolve({});
  await flush();
  assert.equal(refresher.state().inFlight, false);
  refresher.stop();
});

test("blocked polls pause without any read, keep the loop alive, and resume when unblocked", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  let blocked = true;
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward() {},
    applyFailure() {},
    isBlocked: () => blocked,
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  clock.fire();
  await flush();
  assert.equal(net.calls.length, 0, "no read while blocked");
  assert.equal(clock.pending().length, 1, "the loop stays scheduled");
  blocked = false;
  clock.fire();
  assert.equal(net.calls.length, 1, "resumes once unblocked");
  net.calls.shift().resolve({});
  await flush();
  refresher.stop();
});

test("a read that returns after rebase() is discarded and does not count as a failure", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const applied = [];
  const failures = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward: (payload) => applied.push(payload),
    applyFailure: (info) => failures.push(info),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  refresher.rebase();
  net.calls.shift().resolve({ stale: true });
  await flush();
  assert.deepEqual(applied, []);
  assert.deepEqual(failures, []);
  assert.equal(refresher.state().failures, 0);
  // The discarded read still counts as in flight until it settles, so no duplicate starts.
  refresher.stop();
});

test("a failed read after rebase() is also discarded", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const failures = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward() {},
    applyFailure: (info) => failures.push(info),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  refresher.rebase();
  net.calls.shift().reject(new Error("late"));
  await flush();
  assert.deepEqual(failures, []);
  refresher.stop();
});

test("stop() cancels the pending timer, discards a late response, and nothing runs afterwards", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const applied = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward: (payload) => applied.push(payload),
    applyFailure() {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  refresher.stop();
  net.calls.shift().resolve({ late: true });
  await flush();
  assert.deepEqual(applied, []);
  assert.equal(clock.pending().length, 0, "no timer remains");
  assert.equal(await refresher.checkNow(), false);
  assert.equal(net.calls.length, 0, "no read after stop");
});

test("an apply failure is reported conservatively as a failed read, never silently dropped", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const failures = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward: () => { throw new Error("render bug"); },
    applyFailure: (info) => failures.push(info),
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  net.calls.shift().resolve({});
  await flush();
  assert.equal(failures.length, 1);
  assert.match(String(failures[0].error.message), /render bug/);
  assert.equal(refresher.state().inFlight, false);
  refresher.stop();
});

test("restarting after stop begins a fresh single loop", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward() {},
    applyFailure() {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  refresher.stop();
  refresher.start();
  assert.equal(clock.pending().length, 1, "exactly one timer after restart");
  refresher.stop();
});

test("missing callbacks are rejected up front", () => {
  assert.throws(() => createForwardRefresher({ applyForward() {}, applyFailure() {} }), /fetchForward/);
});

test("the accepted scoped payload reaches applyForward as the same object, with server fields untouched", async () => {
  const clock = scheduler();
  const net = controlledFetch();
  const received = [];
  const refresher = createForwardRefresher({
    fetchForward: net.fetchForward,
    applyForward: (payload) => received.push(payload),
    applyFailure() {},
    schedule: clock.schedule,
    cancel: clock.cancel,
  });
  refresher.start();
  clock.fire();
  // Server-side verdict fields are passed through verbatim; the poller never derives or rewrites them.
  const payload = { symbol: "BTC/USDT", timeframe: "1h", status: { runner: { status: "IDLE", heartbeat_age_seconds: 361, liveness_tolerance_seconds: 360 } } };
  net.calls.shift().resolve(payload);
  await flush();
  assert.equal(received.length, 1);
  assert.equal(received[0], payload, "same object, not a copy or derived view");
  refresher.stop();
});
