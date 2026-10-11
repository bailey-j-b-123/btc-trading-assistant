/** Chart history paging: cursor maths, boundary de-duplication, and view preservation. Synthetic rows only. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  CHART_PAGE_SIZE,
  HISTORY_EDGE_BARS,
  historyNote,
  historyState,
  isoFromMs,
  mergeOlderRows,
  shouldLoadOlder,
  utcLabel,
} from "../../src/trading_assistant/web/static/js/chart_history.js";
import {
  setCandles,
  setCandlesPreservingView,
  setHistoryListener,
} from "../../src/trading_assistant/web/static/js/chart.js";

const HOUR = 3_600_000;
const T0 = Date.parse("2026-01-01T00:00:00Z");
/** SYNTHETIC rows: hour index k -> [T0 + k h, open, high, low, close, volume]. */
const row = (k) => [T0 + k * HOUR, "100", "101", "99", "100.5", "1"];
const span = (from, to) => Array.from({ length: to - from }, (_, i) => row(from + i));

test("page size is the documented chart page, not a whole-series read", () => {
  assert.equal(CHART_PAGE_SIZE, 500);
  assert.ok(HISTORY_EDGE_BARS > 0);
});

test("isoFromMs produces the backend's Z form", () => {
  assert.equal(isoFromMs(T0 + 5 * HOUR), "2026-01-01T05:00:00Z");
});

test("historyState: older history exists when the store is earlier than the oldest loaded row", () => {
  const payload = {
    earliest_stored_open: "2026-01-01T00:00:00Z",
    latest_stored_open: "2026-01-02T00:00:00Z",
    window_start: "2026-01-01T10:00:00Z",
  };
  const state = historyState({ payload, rows: span(10, 30) });
  assert.equal(state.hasOlder, true);
  assert.equal(state.earliestReached, false);
  assert.equal(state.cursorMs, T0 + 10 * HOUR);
});

test("historyState: earliest reached when nothing is stored before the cursor", () => {
  const payload = { earliest_stored_open: "2026-01-01T00:00:00Z", latest_stored_open: "2026-01-02T00:00:00Z", window_start: "2026-01-01T00:00:00Z" };
  const state = historyState({ payload, rows: span(0, 30) });
  assert.equal(state.hasOlder, false);
  assert.equal(state.earliestReached, true);
});

test("historyState: an empty page moves the cursor back to its window start, so paging always progresses", () => {
  // SYNTHETIC: the loaded rows start at hour 300, the page window was hours 100..199 and held no stored candle.
  const payload = { earliest_stored_open: "2026-01-01T00:00:00Z", latest_stored_open: "2026-01-20T00:00:00Z", window_start: new Date(T0 + 100 * HOUR).toISOString() };
  const state = historyState({ payload, rows: span(300, 320) });
  assert.equal(state.cursorMs, T0 + 100 * HOUR);
  assert.equal(state.hasOlder, true);
});

test("historyState: unknown span never claims older history", () => {
  const state = historyState({ payload: {}, rows: span(10, 20) });
  assert.equal(state.hasOlder, false);
  assert.equal(state.earliestReached, false);
});

test("shouldLoadOlder fires only near the left edge, when idle and with history", () => {
  const idle = { hasOlder: true, loading: false };
  assert.equal(shouldLoadOlder(idle, { from: 2, to: 120 }), true);
  assert.equal(shouldLoadOlder(idle, { from: HISTORY_EDGE_BARS + 1, to: 120 }), false);
  assert.equal(shouldLoadOlder({ hasOlder: true, loading: true }, { from: 0, to: 10 }), false);
  assert.equal(shouldLoadOlder({ hasOlder: false, loading: false }, { from: 0, to: 10 }), false);
  assert.equal(shouldLoadOlder(null, { from: 0, to: 10 }), false);
  assert.equal(shouldLoadOlder(idle, null), false);
});

test("mergeOlderRows keeps only strictly older rows and never duplicates the boundary candle", () => {
  const existing = span(100, 110);
  // The page includes the boundary candle (hour 100) and an overlap (hour 99 twice).
  const page = [row(100), row(98), row(99), row(99)];
  const merged = mergeOlderRows(existing, page);
  assert.equal(merged.added, 2);
  assert.deepEqual(merged.rows.map((r) => r[0]), [98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109].map((k) => T0 + k * HOUR));
  const stamps = merged.rows.map((r) => r[0]);
  assert.equal(new Set(stamps).size, stamps.length, "no duplicate open times");
  assert.deepEqual(stamps, [...stamps].sort((a, b) => a - b), "ascending");
});

test("mergeOlderRows drops invalid rows and returns the existing rows unchanged when nothing is older", () => {
  const existing = span(50, 52);
  const merged = mergeOlderRows(existing, [["bad"], null, [T0 + 60 * HOUR, "x"]]);
  assert.equal(merged.added, 0);
  assert.deepEqual(merged.rows, existing);
});

test("mergeOlderRows with no existing rows keeps every valid incoming row", () => {
  const merged = mergeOlderRows([], span(0, 3));
  assert.equal(merged.added, 3);
  assert.equal(merged.rows[0][0], T0);
});

test("utcLabel and historyNote state the stored span in plain English", () => {
  assert.equal(utcLabel(T0), "2026-01-01 00:00 UTC");
  const note = historyNote("1H", {
    earliestMs: T0,
    latestMs: T0 + 24 * HOUR,
    hasOlder: false,
    earliestReached: true,
    loading: false,
    error: null,
  });
  assert.match(note, /earliest stored candle reached/);
  assert.match(note, /2026-01-01 00:00 UTC → 2026-01-02 00:00 UTC/);
  assert.match(historyNote("1H", { hasOlder: true, loading: true, earliestMs: T0, latestMs: T0 }), /loading earlier candles/);
  assert.match(historyNote("1H", { hasOlder: true, loading: false, error: "network down" }), /could not be loaded \(network down\)/);
});

/** Mock chart: a time scale that records the logical range, as the real one exposes it. */
function mockChart(range = { from: 0, to: 119 }) {
  const state = { range, listeners: new Set(), data: [] };
  const timeScale = {
    getVisibleLogicalRange: () => state.range,
    setVisibleLogicalRange: (next) => { state.range = next; },
    subscribeVisibleLogicalRangeChange: (fn) => state.listeners.add(fn),
    unsubscribeVisibleLogicalRangeChange: (fn) => state.listeners.delete(fn),
  };
  return {
    state,
    chart: { timeScale: () => timeScale },
    series: { setData: (candles) => { state.data = candles; } },
    volume: { setData: () => {} },
    destroyed: false,
    initialRangeApplied: true,
    loadedRows: [],
    zoneUnsubscribers: [],
    eventUnsubscribers: [],
    evidenceSeries: [],
    priceLineHandles: [],
  };
}

test("setCandlesPreservingView shifts the visible range by the prepended count, keeping the same candles on screen", () => {
  const handle = mockChart({ from: 10, to: 60 });
  setCandles(handle, span(100, 200));
  // A zoomed view: indexes 10..60 show hours 110..160.
  const shownBefore = handle.state.data.slice(10, 61).map((c) => c.time);
  const older = span(40, 100); // 60 older candles
  const merged = mergeOlderRows(handle.loadedRows, older);
  setCandlesPreservingView(handle, merged.rows, merged.added);
  assert.deepEqual(handle.state.range, { from: 70, to: 120 });
  const shownAfter = handle.state.data.slice(70, 121).map((c) => c.time);
  assert.deepEqual(shownAfter, shownBefore, "the same candles remain in the visible window");
});

test("setCandlesPreservingView with nothing added leaves the view alone", () => {
  const handle = mockChart({ from: 5, to: 50 });
  setCandles(handle, span(0, 100));
  setCandlesPreservingView(handle, handle.loadedRows, 0);
  assert.deepEqual(handle.state.range, { from: 5, to: 50 });
});

test("setHistoryListener registers one listener, replaces it, and releases it", () => {
  const handle = mockChart();
  const calls = [];
  setHistoryListener(handle, (range) => calls.push(["first", range]));
  assert.equal(handle.state.listeners.size, 1);
  setHistoryListener(handle, (range) => calls.push(["second", range]));
  assert.equal(handle.state.listeners.size, 1, "replacing does not stack listeners");
  for (const listener of handle.state.listeners) listener({ from: 1, to: 9 });
  assert.deepEqual(calls, [["second", { from: 1, to: 9 }]]);
  setHistoryListener(handle, null);
  assert.equal(handle.state.listeners.size, 0, "clearing removes the listener");
});
