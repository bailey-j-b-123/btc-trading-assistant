import assert from "node:assert/strict";
import test from "node:test";

import {
  STAGE_DEFS,
  breakoutStages,
  drawableStages,
  stageFrame,
} from "../../src/trading_assistant/web/static/js/breakout_stages.js";

const HOUR = 3_600_000;
const T0 = Date.parse("2026-09-28T00:00:00Z");
const ms = (index) => T0 + index * HOUR;
const iso = (index) => new Date(ms(index)).toISOString().replace(".000Z", "Z");

/** Stored candle rows as the backend returns them: [ms, open, high, low, close, volume]. */
function candles(count, { high = 84_400, low = 84_100 } = {}) {
  return Array.from({ length: count }, (_, index) => [ms(index), "84200", String(high), String(low), "84250", "10"]);
}

function evidence(overrides = {}) {
  return {
    available: true,
    timeframe: "1h",
    counts: { breakouts: 1, failed_breakouts: 0, sweeps: 0, retests: 1 },
    excluded_future_count: 0,
    breakouts: [
      {
        id: "bo-1",
        direction: "bullish",
        time_ms: ms(10),
        known_at: iso(11),
        band_low: "84229.9",
        band_high: "84229.9",
        breakout_close: "84328.0",
        penetration_pct: "0.11646696",
        confirmation_candles: 1,
      },
    ],
    failed_breakouts: [],
    sweeps: [],
    retests: [
      {
        id: "rt-1",
        state: "held",
        breakout_id: "bo-1",
        direction: "bullish",
        time_ms: ms(11),
        known_at: iso(12),
        band_low: "84229.9",
        band_high: "84229.9",
        distance_from_band: "89.5",
        elapsed_candles: 1,
      },
    ],
    ...overrides,
  };
}

const titles = (model) => model.stages.map((stage) => stage.title);
const statuses = (model) => model.stages.map((stage) => stage.status);

test("the three steps are always returned, in the order they happen", () => {
  const model = breakoutStages({ evidence: evidence(), candles: candles(30) });
  assert.equal(model.available, true);
  assert.deepEqual(titles(model), ["Breakout", "Retest", "Continuation"]);
  assert.deepEqual(model.stages.map((stage) => stage.number), [1, 2, 3]);
  assert.deepEqual(STAGE_DEFS.map((stage) => stage.key), ["breakout", "retest", "continuation"]);
});

test("each step is anchored to the candle the backend recorded it on", () => {
  const model = breakoutStages({ evidence: evidence(), candles: candles(30) });
  const [breakout, retest] = model.stages;
  assert.equal(breakout.barIndex, 10);
  assert.equal(breakout.timeSeconds, Math.floor(ms(10) / 1000));
  assert.equal(breakout.price, 84_328, "the stored breakout close");
  assert.equal(retest.barIndex, 11);
  assert.equal(retest.timeSeconds, Math.floor(ms(11) / 1000));
  assert.equal(retest.price, 84_100, "the low of the retest candle, for a bullish sequence");
  assert.equal(model.band.low, 84_229.9);
  assert.equal(model.band.high, 84_229.9);
  assert.equal(model.bandText, "84,229.90");
});

test("a step whose candle is not loaded is never snapped to a neighbour", () => {
  const model = breakoutStages({ evidence: evidence(), candles: candles(11) });
  // Only candles 0..10 are loaded: the retest at 11 has no candle.
  assert.equal(model.stages[0].barIndex, 10, "the breakout's own candle is loaded");
  assert.equal(model.stages[1].barIndex, -1, "the retest is not moved to the nearest bar");
  assert.equal(model.stages[1].onLoadedCandles, false);
  assert.deepEqual(drawableStages(model.stages).map((stage) => stage.key), ["breakout"]);
});

test("the breakout is reported as failed when the backend recorded the failure", () => {
  const model = breakoutStages({
    evidence: evidence({
      failed_breakouts: [{ id: "fb-1", breakout_id: "bo-1", time_ms: ms(14), known_at: iso(15), elapsed_candles: 4 }],
    }),
    candles: candles(30),
  });
  assert.equal(model.stages[0].status, "Failed");
  assert.equal(model.stages[0].tone, "red");
  assert.match(model.stages[0].detail, /closed back through/);
});

test("the retest status is the backend's own state, and Awaiting Confirmation when none is stored", () => {
  const held = breakoutStages({ evidence: evidence(), candles: candles(30) });
  assert.equal(held.stages[1].status, "Held");

  const failed = breakoutStages({
    evidence: evidence({ retests: [{ id: "rt-1", state: "failed", breakout_id: "bo-1", time_ms: ms(11), known_at: iso(12), band_low: "84229.9", band_high: "84229.9" }] }),
    candles: candles(30),
  });
  assert.equal(failed.stages[1].status, "Failed");

  const observed = breakoutStages({
    evidence: evidence({ retests: [{ id: "rt-1", state: "observed", breakout_id: "bo-1", time_ms: ms(11), known_at: iso(12), band_low: "84229.9", band_high: "84229.9" }] }),
    candles: candles(30),
  });
  assert.equal(observed.stages[1].status, "Awaiting Confirmation");

  const none = breakoutStages({ evidence: evidence({ retests: [] }), candles: candles(30) });
  assert.equal(none.stages[1].status, "Awaiting Confirmation");
  assert.match(none.stages[1].detail, /No retest of this breakout is stored yet/);
});

test("continuation stays In Progress until the backend records one", () => {
  // Price has run far past the breakout and the band had clearly been left
  // behind — but the backend stores no continuation, so it is not claimed.
  const rows = candles(30);
  rows[20][2] = "88999";
  const model = breakoutStages({ evidence: evidence(), candles: rows });
  const continuation = model.stages[2];
  assert.equal(continuation.status, "In Progress");
  assert.equal(continuation.tone, "amber");
  assert.match(continuation.detail, /The backend has stored no continuation/);
  assert.match(continuation.meta, /traded past the breakout candle's high \(84,400\.00\) at 88,999\.00/, "the candle fact is still reported");
  assert.match(continuation.meta, /not a recorded continuation/);
  assert.equal(continuation.barIndex, 20, "pointed at where price went on from, while it waits");
});

test("a continuation the backend did record is confirmed", () => {
  const model = breakoutStages({
    evidence: evidence({
      continuations: [{ id: "cont-1", breakout_id: "bo-1", time_ms: ms(18), known_at: iso(19), price: "85200" }],
    }),
    candles: candles(30),
  });
  const continuation = model.stages[2];
  assert.equal(continuation.status, "Completed");
  assert.equal(continuation.tone, "green");
  assert.equal(continuation.barIndex, 18);
});

test("a step with no stored record is flagged unrecorded, so it is never drawn as confirmed", () => {
  const model = breakoutStages({ evidence: evidence({ retests: [{ id: "rt-1", time_ms: ms(13), state: "held" }] }), candles: candles(20) });
  // The stored breakout and retest are records; the continuation is not.
  assert.deepEqual(model.stages.map((stage) => stage.recorded), [true, true, false]);
  // The continuation still has a candle to point at, so it stays on the chart —
  // drawn as an outline rather than a filled "done" marker.
  assert.ok(Number.isFinite(model.stages[2].barIndex) && model.stages[2].barIndex >= 0);
  // The callout arrangement: breakout above, retest below and to the right,
  // continuation above.
  assert.deepEqual(model.stages.map((stage) => `${stage.place}/${stage.nudge}`), ["above/none", "below/right", "above/none"]);
  // Each step carries one short sentence for the panel.
  for (const stage of model.stages) assert.match(stage.summary, /\S/);
});

test("the layout keeps its three steps when nothing is stored", () => {
  const empty = breakoutStages({
    evidence: { available: true, breakouts: [], failed_breakouts: [], sweeps: [], retests: [] },
    candles: candles(30),
  });
  assert.deepEqual(titles(empty), ["Breakout", "Retest", "Continuation"], "the layout is the same");
  assert.deepEqual(statuses(empty), ["Not recorded", "Awaiting Confirmation", "Awaiting Confirmation"]);
  assert.equal(empty.available, false);
  assert.equal(empty.band, null);

  const unavailable = breakoutStages({ evidence: { available: false, reason: "no window" }, candles: candles(30) });
  assert.deepEqual(statuses(unavailable), ["Not recorded", "Not recorded", "Not recorded"]);
  assert.equal(unavailable.reason, "no window");

  assert.deepEqual(statuses(breakoutStages({ evidence: null, candles: candles(30) })), ["Not recorded", "Not recorded", "Not recorded"]);
  assert.deepEqual(titles(breakoutStages({})), ["Breakout", "Retest", "Continuation"], "and with no arguments at all");
});

test("the newest breakout with a stored retest is the one the sequence is told from", () => {
  const older = { id: "bo-old", direction: "bullish", time_ms: ms(4), known_at: iso(5), band_low: "84000", band_high: "84000", breakout_close: "84050" };
  const newer = { id: "bo-new", direction: "bullish", time_ms: ms(10), known_at: iso(11), band_low: "84229.9", band_high: "84229.9", breakout_close: "84328.0" };
  const model = breakoutStages({
    evidence: evidence({
      breakouts: [older, newer],
      retests: [{ id: "rt-1", state: "held", breakout_id: "bo-old", time_ms: ms(6), known_at: iso(7), band_low: "84000", band_high: "84000" }],
    }),
    candles: candles(30),
  });
  assert.equal(model.breakoutId, "bo-old", "the sequence with a stored retest, not simply the newest");
  assert.equal(model.band.low, 84_000);
  assert.equal(model.stages[1].barIndex, 6);
});

test("the frame spans the first and last loaded bar that carries a step", () => {
  // A flat series: the extreme after the retest is the first candle after it.
  const model = breakoutStages({ evidence: evidence(), candles: candles(30) });
  assert.deepEqual(stageFrame(model.stages), { fromIndex: 10, toIndex: 12, count: 3 });
  // A rising series: the frame follows price as far as it has reached.
  const rows = candles(30);
  rows[20][2] = "84900";
  const rising = breakoutStages({ evidence: evidence(), candles: rows });
  assert.deepEqual(stageFrame(rising.stages), { fromIndex: 10, toIndex: 20, count: 3 });
  assert.equal(stageFrame([]), null);
  assert.equal(stageFrame(null), null);
  const unloaded = breakoutStages({ evidence: evidence(), candles: candles(5) });
  assert.equal(stageFrame(unloaded.stages), null, "nothing to frame when no step is on a loaded candle");
});

test("a step that is still in progress points at how far price has reached", () => {
  // Price never gets past the breakout candle's high: the step points at the
  // furthest candle it did reach, and says so.
  const rows = candles(30);
  for (let index = 12; index < 30; index += 1) rows[index][2] = "84250";
  const model = breakoutStages({ evidence: evidence(), candles: rows });
  const continuation = model.stages[2];
  assert.equal(continuation.status, "In Progress");
  assert.equal(continuation.barIndex, 12);
  assert.match(continuation.meta, /highest high is 84,250\.00/);
});

// BRAIN-AUDIT-0004: lifecycle confirmation belongs to its actual outcome candle.
for (const state of ["held", "failed"]) {
  for (const delayed of [true, false]) {
    test(`retest ${state} uses ${delayed ? "later" : "same-candle"} outcome evidence, not the first touch`, () => {
      const observed = { id: "observed", breakout_id: "bo-1", state: "observed", time_ms: ms(11), known_at: iso(12), distance_from_band: "0", elapsed_candles: 1 };
      const outcome = { ...observed, id: state, state, time_ms: ms(delayed ? 12 : 11), known_at: iso(delayed ? 13 : 12), distance_from_band: "1", elapsed_candles: delayed ? 2 : 1 };
      const stored = evidence({ retests: [observed, outcome] });
      const before = structuredClone(stored);
      const rows = candles(30);
      rows[12][3] = "84110";
      const stage = breakoutStages({ evidence: stored, candles: rows }).stages[1];
      assert.equal(stage.status, state === "held" ? "Held" : "Failed");
      assert.equal(stage.timeSeconds, outcome.time_ms / 1000);
      assert.equal(stage.barIndex, delayed ? 12 : 11);
      assert.equal(stage.price, delayed ? 84110 : 84100);
      assert.equal(stage.knownAt, outcome.known_at);
      assert.equal(stage.observedKnownAt, observed.known_at);
      assert.match(stage.meta, /1\.00 from the band/);
      assert.match(stage.meta, /first observed/);
      assert.match(stage.meta, /outcome known/);
      assert.doesNotMatch(stage.detail, /1 of 2 held/);
      assert.deepEqual(stored, before, "source records unchanged");
      // Before the confirming record exists, do not infer an outcome from later candles.
      const pending = breakoutStages({ evidence: evidence({ retests: [observed] }), candles: rows }).stages[1];
      assert.equal(pending.status, "Awaiting Confirmation");
      assert.equal(pending.timeSeconds, observed.time_ms / 1000);
    });
  }
}
