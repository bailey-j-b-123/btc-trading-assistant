// Automatic dashboard update rules and the live price model. Pure logic only: no DOM, no network.
// Timers, the network and the page are injected, so every schedule here is deterministic.
// Candle rows and payloads are SYNTHETIC fixtures; they say nothing about real exchange data.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  boundaryChanged,
  createDashboardUpdater,
  dataStatus,
  LATEST_POLL_MS,
  mergeCandleRows,
  nextLatestDelay,
  pendingStatus,
  refreshCooldown,
} from "../../src/trading_assistant/web/static/js/live-updates.js";
import {
  liveQuoteModel,
  liveQuoteView,
  LIVE_POLL_MS,
  LIVE_STALE_MS,
} from "../../src/trading_assistant/web/static/js/live-display.js";

const JS_DIR = new URL("../../src/trading_assistant/web/static/js/", import.meta.url);
const flush = () => new Promise((resolve) => setImmediate(resolve));
const flushMany = async (count = 6) => { for (let i = 0; i < count; i += 1) await flush(); };

function latestPayload({
  asOf = "2026-10-11T09:00:00Z",
  status = "CURRENT",
  behind = 0,
  stored = "2026-10-11T08:00:00Z",
  checked = "2026-10-11T09:30:00Z",
} = {}) {
  return {
    default_as_of: asOf,
    checked_at: checked,
    latest_stored_open: stored,
    expected_latest_closed_open: stored,
    freshness: { status, reason: "fixture", staleness_intervals: behind, is_current_boundary: true },
  };
}

/** Injected clock, timers and network. Timers are run by hand, so nothing waits on real time. */
function harness({ current = "2026-10-11T08:00:00Z", latest = () => latestPayload(), refresh, blocked = false } = {}) {
  const timers = new Map();
  let nextId = 1;
  let nowMs = Date.parse("2026-10-11T09:30:00Z");
  const statuses = [];
  const calls = { fetch: 0, refresh: [] };
  let currentAsOf = current;
  const state = { blocked };
  const updater = createDashboardUpdater({
    fetchLatest: () => { calls.fetch += 1; return latest(calls.fetch); },
    getCurrentAsOf: () => currentAsOf,
    refreshDashboard: (payload) => {
      calls.refresh.push(payload);
      return refresh ? refresh(calls.refresh.length, payload, (value) => { currentAsOf = value; }) : Promise.resolve();
    },
    onStatus: (model) => statuses.push(model),
    isBlocked: () => state.blocked,
    now: () => nowMs,
    schedule: (fn, ms) => { const id = nextId; nextId += 1; timers.set(id, { fn, ms }); return id; },
    cancel: (id) => { timers.delete(id); },
  });
  return {
    updater,
    statuses,
    calls,
    state,
    timers,
    setNow: (ms) => { nowMs = ms; },
    advance: (ms) => { nowMs += ms; },
    setCurrent: (value) => { currentAsOf = value; },
    runNextTimer() {
      const [id, timer] = [...timers.entries()][0] ?? [];
      assert.ok(id !== undefined, "a check must be scheduled");
      timers.delete(id);
      timer.fn();
      return timer.ms;
    },
    /** Delay of the check that is currently scheduled, without running it. */
    nextDelay() {
      const scheduled = [...timers.values()];
      assert.equal(scheduled.length, 1, "exactly one check is scheduled");
      return scheduled[0].ms;
    },
    last: () => statuses.at(-1),
  };
}

test("boundary check cadence backs off on failure and stays bounded", () => {
  assert.equal(LATEST_POLL_MS, 15_000);
  assert.equal(nextLatestDelay(0), 15_000);
  assert.equal(nextLatestDelay(Number.NaN), 15_000);
  assert.equal(nextLatestDelay(1), 30_000);
  assert.equal(nextLatestDelay(2), 60_000);
  assert.equal(nextLatestDelay(3), 60_000);
  assert.equal(nextLatestDelay(12), 60_000);
});

test("refresh cooldown doubles after each failed analysis refresh and is capped", () => {
  assert.equal(refreshCooldown(0), 0);
  assert.equal(refreshCooldown(1), 30_000);
  assert.equal(refreshCooldown(2), 60_000);
  assert.equal(refreshCooldown(3), 120_000);
  assert.equal(refreshCooldown(5), 300_000);
  assert.equal(refreshCooldown(40), 300_000);
  assert.equal(refreshCooldown(1.5), 0);
});

test("boundaryChanged refreshes only when both instants parse and differ", () => {
  assert.equal(boundaryChanged({ default_as_of: "2026-10-11T09:00:00Z" }, "2026-10-11T08:00:00Z"), true);
  assert.equal(boundaryChanged({ default_as_of: "2026-10-11T09:00:00Z" }, "2026-10-11T09:00:00Z"), false);
  // The same instant written differently is still the same boundary.
  assert.equal(boundaryChanged({ default_as_of: "2026-10-11T09:00:00Z" }, "2026-10-11T09:00:00.000+00:00"), false);
  // Malformed values never trigger the expensive refresh.
  assert.equal(boundaryChanged({ default_as_of: "not a date" }, "2026-10-11T08:00:00Z"), false);
  assert.equal(boundaryChanged({}, "2026-10-11T08:00:00Z"), false);
  assert.equal(boundaryChanged({ default_as_of: "2026-10-11T09:00:00Z" }, null), false);
  assert.equal(boundaryChanged(null, "2026-10-11T08:00:00Z"), false);
});

test("dataStatus labels CURRENT, STALE, UNKNOWN and failed checks honestly", () => {
  const current = dataStatus(latestPayload(), { now: Date.parse("2026-10-11T09:30:00Z") });
  assert.equal(current.label, "CURRENT");
  assert.equal(current.tone, "current");
  assert.match(current.detail, /latest stored close opened 08:00 UTC/);

  const stale = dataStatus(latestPayload({ status: "STALE", behind: 2 }), { now: 0 });
  assert.equal(stale.label, "STALE");
  assert.match(stale.detail, /2 closed candles are not stored yet/);
  assert.match(stale.detail, /Nothing is filled in or invented/);

  const one = dataStatus(latestPayload({ status: "STALE", behind: 1 }), { now: 0 });
  assert.match(one.detail, /1 closed candle is not stored yet/);

  const unknown = dataStatus({ ...latestPayload(), freshness: { status: "WEIRD", reason: "x" } }, { now: 0 });
  assert.equal(unknown.label, "UNKNOWN");

  const failed = dataStatus(null, { failed: true, lastSuccessMs: Date.parse("2026-10-11T09:29:05Z") });
  assert.equal(failed.label, "UNAVAILABLE");
  assert.match(failed.detail, /Retrying automatically/);
  assert.match(failed.detail, /09:29:05 UTC/, "the last successful check time is named, never hidden");

  // A failed check never reports CURRENT, even when an earlier check was CURRENT.
  assert.notEqual(dataStatus(latestPayload(), { failed: true }).label, "CURRENT");
});

test("pendingStatus never claims CURRENT while the page is behind the server", () => {
  assert.equal(pendingStatus({ blocked: true, refreshCooldownActive: false }).label, "PAUSED");
  const cooling = pendingStatus({ blocked: false, refreshCooldownActive: true, refreshError: "timeout" });
  assert.equal(cooling.label, "UNAVAILABLE");
  assert.match(cooling.detail, /timeout/);
  assert.equal(pendingStatus({ blocked: false, refreshCooldownActive: false }).label, "UPDATING");
  for (const model of [
    pendingStatus({ blocked: true, refreshCooldownActive: false }),
    pendingStatus({ blocked: false, refreshCooldownActive: true }),
    pendingStatus({ blocked: false, refreshCooldownActive: false }),
  ]) {
    assert.notEqual(model.label, "CURRENT");
  }
});

test("mergeCandleRows keeps older pages, lets the newer snapshot win, and drops invalid rows", () => {
  const h = (hour, close) => [Date.parse(`2026-10-11T${String(hour).padStart(2, "0")}:00:00Z`), 1, 2, 0, close, 5];
  const existing = [h(6, 100), h(7, 101), h(8, 102)];
  const incoming = [h(7, 150), h(8, 102), h(9, 103)];
  const { rows, addedCount } = mergeCandleRows(existing, incoming);
  assert.deepEqual(rows.map((row) => row[0]), [h(6).at(0), h(7).at(0), h(8).at(0), h(9).at(0)]);
  assert.equal(rows[1][4], 150, "the newer snapshot's row wins on the same open time");
  assert.equal(addedCount, 1, "only open times not already on screen count as added");

  const dirty = mergeCandleRows([h(6, 100), [null, 1, 2, 3, 4, 5], "junk", [1, 2]], [h(7, 101)]);
  assert.equal(dirty.rows.length, 2);
  assert.equal(mergeCandleRows(undefined, undefined).rows.length, 0);
});

test("updater: unchanged boundary never reruns the expensive refresh", async () => {
  const h = harness({ current: "2026-10-11T09:00:00Z", latest: () => latestPayload({ asOf: "2026-10-11T09:00:00Z" }) });
  h.updater.start();
  await flushMany();
  assert.equal(h.calls.fetch, 1);
  assert.equal(h.calls.refresh.length, 0);
  assert.equal(h.last().label, "CURRENT");
  assert.equal(h.nextDelay(), LATEST_POLL_MS);
  h.runNextTimer();
  await flushMany();
  assert.equal(h.calls.refresh.length, 0, "still unchanged after another check");
  h.updater.stop();
});

test("updater: a newer stored boundary triggers one refresh, then the page is current", async () => {
  let refreshed = 0;
  const h = harness({
    current: "2026-10-11T08:00:00Z",
    latest: () => latestPayload({ asOf: "2026-10-11T09:00:00Z" }),
    refresh: (_count, _payload, setCurrent) => { refreshed += 1; setCurrent("2026-10-11T09:00:00Z"); return Promise.resolve(); },
  });
  h.updater.start();
  await flushMany();
  assert.equal(refreshed, 1);
  assert.ok(h.statuses.some((model) => model.label === "UPDATING"), "the page says it is updating");
  h.runNextTimer();
  await flushMany();
  assert.equal(refreshed, 1, "the boundary now matches; no further analysis");
  h.updater.stop();
});

test("updater: a failed refresh shows UNAVAILABLE, keeps the last good page, and waits out the cooldown", async () => {
  let attempts = 0;
  const h = harness({
    current: "2026-10-11T08:00:00Z",
    latest: () => latestPayload({ asOf: "2026-10-11T09:00:00Z" }),
    refresh: () => { attempts += 1; return Promise.reject(new Error("boom")); },
  });
  h.updater.start();
  await flushMany();
  assert.equal(attempts, 1);
  assert.equal(h.last().label, "UNAVAILABLE");
  assert.match(h.last().detail, /boom/);

  // The next check arrives within the cooldown: no second analysis request.
  h.advance(10_000);
  h.runNextTimer();
  await flushMany();
  assert.equal(attempts, 1, "cooldown holds the expensive request back");

  // Past the cooldown the refresh is retried.
  h.advance(30_000);
  h.runNextTimer();
  await flushMany();
  assert.equal(attempts, 2);
  h.updater.stop();
});

test("updater: a dialog or hidden tab pauses the refresh; the next check resumes it", async () => {
  let refreshed = 0;
  const h = harness({
    current: "2026-10-11T08:00:00Z",
    latest: () => latestPayload({ asOf: "2026-10-11T09:00:00Z" }),
    refresh: () => { refreshed += 1; return Promise.resolve(); },
    blocked: true,
  });
  h.updater.start();
  await flushMany();
  assert.equal(refreshed, 0);
  assert.equal(h.last().label, "PAUSED");
  h.state.blocked = false;
  h.runNextTimer();
  await flushMany();
  assert.equal(refreshed, 1);
  h.updater.stop();
});

test("updater: fetch failures back off and report UNAVAILABLE with the last successful check time", async () => {
  let fail = false;
  const h = harness({ latest: () => { if (fail) throw new Error("offline"); return latestPayload(); } });
  h.setNow(Date.parse("2026-10-11T09:29:05Z"));
  h.updater.start();
  await flushMany();
  assert.equal(h.nextDelay(), 15_000, "the successful check scheduled the normal cadence");
  fail = true;
  h.runNextTimer();
  await flushMany();
  assert.equal(h.last().label, "UNAVAILABLE");
  assert.match(h.last().detail, /09:29:05 UTC/);
  assert.equal(h.nextDelay(), 30_000, "first failure doubles the delay");
  h.runNextTimer();
  await flushMany();
  assert.equal(h.nextDelay(), 60_000, "second failure");
  h.runNextTimer();
  await flushMany();
  assert.equal(h.nextDelay(), 60_000, "delay is capped");
  h.updater.stop();
  h.updater.stop();
});

test("updater: checkNow never duplicates a check already in flight", async () => {
  let release;
  const h = harness({ latest: () => new Promise((resolve) => { release = resolve; }) });
  h.updater.start();
  await flush();
  h.updater.checkNow();
  h.updater.checkNow();
  assert.equal(h.calls.fetch, 1);
  release(latestPayload());
  await flushMany();
  h.updater.stop();
});

test("updater: stop ends the schedule and discards an in-flight result", async () => {
  let release;
  let refreshed = 0;
  const h = harness({
    current: "2026-10-11T08:00:00Z",
    latest: () => new Promise((resolve) => { release = resolve; }),
    refresh: () => { refreshed += 1; return Promise.resolve(); },
  });
  h.updater.start();
  await flush();
  h.updater.stop();
  release(latestPayload({ asOf: "2026-10-11T09:00:00Z" }));
  await flushMany();
  assert.equal(refreshed, 0, "a result that lands after stop never refreshes the page");
  assert.equal(h.timers.size, 0, "nothing is rescheduled after stop");
  h.updater.checkNow();
  assert.equal(h.calls.fetch, 1, "checkNow after stop does nothing");
});

test("live quote model: CURRENT only for a fresh valid Binance quote; never a fabricated price", () => {
  const now = Date.parse("2026-10-11T09:30:00Z");
  const fresh = { exchange: "binance", price: "60123.45", fetched_at: new Date(now - 2_000).toISOString(), status: "CURRENT" };
  assert.deepEqual(
    { status: liveQuoteModel(fresh, now).status, price: liveQuoteModel(fresh, now).price },
    { status: "CURRENT", price: 60123.45 },
  );
  const aged = { ...fresh, fetched_at: new Date(now - LIVE_STALE_MS).toISOString() };
  assert.equal(liveQuoteModel(aged, now).status, "STALE", "a quote at the stale threshold is STALE");
  assert.equal(liveQuoteModel({ ...fresh, status: "STALE" }, now).status, "STALE");
  assert.equal(liveQuoteModel({ ...fresh, price: "abc" }, now).status, "UNAVAILABLE");
  assert.equal(liveQuoteModel({ ...fresh, price: "0" }, now).price, null);
  assert.equal(liveQuoteModel({ ...fresh, price: "" }, now).status, "UNAVAILABLE");
  assert.equal(liveQuoteModel({ ...fresh, exchange: "kraken" }, now).status, "UNAVAILABLE", "only Binance quotes display");
  assert.equal(liveQuoteModel({ ...fresh, fetched_at: new Date(now + 60_000).toISOString() }, now).status, "UNAVAILABLE", "future timestamps are not trusted");
  assert.equal(liveQuoteModel(null, now).status, "UNAVAILABLE");
  assert.equal(LIVE_POLL_MS, 20_000);
});

test("live quote view: plain labels, and the value is blank whenever the quote is not current", () => {
  const now = Date.parse("2026-10-11T09:30:00Z");
  const fresh = { exchange: "binance", price: "60123.45", fetched_at: new Date(now).toISOString(), status: "CURRENT" };
  const current = liveQuoteView(liveQuoteModel(fresh, now));
  assert.equal(current.status, "CURRENT");
  assert.equal(current.value, "$60,123.45");
  assert.match(current.note, /Binance Spot last trade/);
  assert.match(current.note, /display only/);

  const stale = liveQuoteView(liveQuoteModel({ ...fresh, fetched_at: new Date(now - 120_000).toISOString() }, now));
  assert.equal(stale.status, "STALE");
  assert.equal(stale.value, "$60,123.45", "the last valid price is kept but labelled");
  assert.match(stale.note, /not current/);

  const none = liveQuoteView(liveQuoteModel(null, now));
  assert.equal(none.status, "UNAVAILABLE");
  assert.equal(none.value, "—", "no price is shown when none is valid");
  assert.match(none.note, /confirmed chart and analysis are unchanged/);

  const checking = liveQuoteView(liveQuoteModel(null, now), { checking: true });
  assert.equal(checking.status, "CHECKING");
});

test("contract: the live modules import nothing from forming, engine, analysis, planning or journal code", () => {
  const specifiers = (source) => [...source.matchAll(/(?:import|export)[^"';]*?from\s+["']([^"']+)["']|import\s+["']([^"']+)["']/g)]
    .map((match) => match[1] ?? match[2]);
  const forbidden = /forming|binance|engine|qualif|plan|paper|journal|chart|dashboard|candle|analysis|api\.js|signal|observ/i;
  for (const file of ["live-updates.js", "live-display.js"]) {
    const source = readFileSync(new URL(file, JS_DIR), "utf8");
    for (const specifier of specifiers(source)) {
      assert.doesNotMatch(specifier, forbidden, `${file} must not import ${specifier}`);
    }
  }
  const updates = readFileSync(new URL("live-updates.js", JS_DIR), "utf8");
  assert.deepEqual(specifiers(updates), [], "the boundary rules import nothing at all");
  // Comments may name the endpoint for readers; the code itself must not contain a URL or a request.
  const code = updates.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  assert.doesNotMatch(code, /\/api\//, "the rules take their network access by injection, not by URL");
  assert.doesNotMatch(code, /\bfetch\(/, "no direct network call in the rules module");
});
