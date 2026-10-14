import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_EDGE_MARGIN,
  FRAME_PAD_CANDLES,
  captureLogicalView,
  focusBarOnChart,
  frameBarsOnChart,
  restoreLogicalView,
} from "../../src/trading_assistant/web/static/js/chart.js";

/**
 * A timeframe switch loads a different series: different instants AND a different candle count.
 * What must survive is the operator's *view* — the zoom (candles on screen) and the place
 * (distance from the live edge). Absolute indices must never be carried across: a longer series
 * would leave the operator staring at old history instead of the live edge.
 */

/** Minimal stand-in for the lightest-charts handle, with a settable logical range. */
function handle({ bars = 200, from = 180, to = 200, destroyed = false, timeScale = true } = {}) {
  const calls = [];
  let range = { from, to };
  const api = {
    destroyed,
    barCount: bars,
    calls,
    chart: timeScale
      ? {
          timeScale: () => ({
            getVisibleLogicalRange: () => range,
            setVisibleLogicalRange: (next) => {
              calls.push(next);
              range = next;
            },
          }),
        }
      : {},
  };
  return api;
}

test("capturing records the zoom and the distance from the live edge, never bare indices", () => {
  assert.deepEqual(captureLogicalView(handle({ bars: 200, from: 180, to: 200 })), { width: 20, edgeOffset: 1 });
  assert.deepEqual(captureLogicalView(handle({ bars: 200, from: 100, to: 150 })), { width: 50, edgeOffset: -49 });
  assert.equal(captureLogicalView(handle({ bars: 0 })), null, "no candle count means no view to carry");
  assert.equal(captureLogicalView(handle({ from: 5, to: 5 })), null, "an empty window is not a view");
  assert.equal(captureLogicalView(handle({ destroyed: true })), null);
  assert.equal(captureLogicalView(handle({ timeScale: false })), null);
  assert.equal(captureLogicalView(null), null);
});

// ---------------------------------------------------------------------------
// The reported defect: 200 candles -> 500 candles while viewing the latest ones
// ---------------------------------------------------------------------------

test("a longer series keeps the operator at the live edge with the same zoom", () => {
  // The operator is on a finer timeframe: 200 candles loaded, following the last few,
  // 20 candles wide, right edge one candle past the newest.
  const finer = handle({ bars: 200, from: 180, to: 200 });
  const captured = captureLogicalView(finer);
  assert.deepEqual(captured, { width: 20, edgeOffset: 1 });

  // The switch loads 500 candles (the coarser timeframe has more history stored).
  const coarser = handle({ bars: 500 });
  restoreLogicalView(coarser, captured);

  const [applied] = coarser.calls;
  assert.equal(applied.to - applied.from, 20, "the same number of candles stays on screen (zoom preserved)");
  assert.equal(applied.to, 500, "the right edge is still one candle past the newest, exactly as before");
  assert.equal(applied.from, 480);
  // The old behaviour kept indices 180..200 on a 500-candle series: 300 candles of history
  // away from price. That is the bug this test pins shut.
  assert.notEqual(applied.from, 180);
});

test("a coarser-to-finer switch with the same edge distance behaves the same way", () => {
  const coarser = handle({ bars: 500, from: 460, to: 500 });
  const captured = captureLogicalView(coarser);
  const finer = handle({ bars: 200 });
  restoreLogicalView(finer, captured);
  assert.deepEqual(finer.calls, [{ from: 160, to: 200 }], "zoom and edge distance are both carried back");
});

test("a window panned into history keeps its distance from the live edge", () => {
  // The operator scrolled back: 49 candles of history behind the visible window.
  const captured = captureLogicalView(handle({ bars: 200, from: 100, to: 150 }));
  assert.equal(captured.edgeOffset, -49);
  const longer = handle({ bars: 500 });
  restoreLogicalView(longer, captured);
  const [applied] = longer.calls;
  assert.equal(applied.to - applied.from, 50, "the zoom is unchanged");
  assert.equal(applied.to, 450, "the same distance from the newest candle (500 - 1 - 49)");
});

test("a shorter series brings the window back to the live edge without changing the zoom", () => {
  const captured = { width: 30, edgeOffset: 1 };
  const shorter = handle({ bars: 128 });
  restoreLogicalView(shorter, captured);
  assert.deepEqual(shorter.calls, [{ from: 98, to: 128 }]);
});

test("the restored window is clamped exactly like the default view", () => {
  // A window wider than the loaded series: it can never show more than the series holds.
  const tiny = handle({ bars: 10 });
  restoreLogicalView(tiny, { width: 400, edgeOffset: 1 });
  assert.deepEqual(tiny.calls, [{ from: 0, to: 10 }]);

  // The edge overshoot is capped at the same small margin the first-load view uses;
  // the width is kept, so the left edge moves with the clamped right edge.
  const capped = handle({ bars: 40 });
  restoreLogicalView(capped, { width: 20, edgeOffset: 900 });
  const cappedTo = 39 + DEFAULT_EDGE_MARGIN;
  assert.deepEqual(capped.calls, [{ from: cappedTo - 20, to: cappedTo }]);

  // A window that would start before the first candle slides right, keeping its width.
  const shifted = handle({ bars: 40 });
  restoreLogicalView(shifted, { width: 30, edgeOffset: -35 });
  assert.deepEqual(shifted.calls, [{ from: 0, to: 30 }]);
});

test("restoring is a no-op when there is nothing to restore onto", () => {
  const untouched = handle();
  restoreLogicalView(untouched, null);
  assert.deepEqual(untouched.calls, [], "a null capture leaves the chart untouched");
  restoreLogicalView(untouched, undefined);
  assert.deepEqual(untouched.calls, []);
  const empty = handle({ bars: 0 });
  restoreLogicalView(empty, { width: 20, edgeOffset: 1 });
  assert.deepEqual(empty.calls, [], "no candles loaded means no window to set");
  const gone = handle({ destroyed: true });
  restoreLogicalView(gone, { width: 20, edgeOffset: 1 });
  assert.deepEqual(gone.calls, [], "a disposed chart is never touched");
  const noScale = handle({ timeScale: false });
  restoreLogicalView(noScale, { width: 20, edgeOffset: 1 });
  assert.deepEqual(noScale.calls, []);
});

test("a degenerate capture still yields a usable window", () => {
  const chart = handle({ bars: 100 });
  restoreLogicalView(chart, { width: 0, edgeOffset: 1 });
  assert.deepEqual(chart.calls, [{ from: 99, to: 100 }], "a zero-width window widens to at least one candle");
  const missing = handle({ bars: 100 });
  restoreLogicalView(missing, { width: 10 });
  assert.deepEqual(missing.calls, [{ from: 90, to: 100 }], "a missing edge offset defaults to the live edge");
});

// ---------------------------------------------------------------------------
// Jumping to a stored event and framing the stored events.
//
// Switching a chart function must never move the operator's view; the
// Breakouts & Retests navigator exists precisely because the stored events are
// usually off-screen. A jump keeps the zoom, framing is the one explicit
// action that changes it.
// ---------------------------------------------------------------------------

test("jumping to a stored event keeps the zoom and centres that event", () => {
  // 200 candles, 20 on screen, right edge one candle past the newest.
  const chart = handle({ bars: 200, from: 180, to: 200 });
  const applied = focusBarOnChart(chart, 40);
  assert.equal(applied.to - applied.from, 20, "the zoom (candles on screen) is unchanged");
  assert.equal((applied.from + applied.to) / 2, 40, "the requested bar sits in the middle");

  // A bar near the start: the window stays inside the series and keeps its width.
  const start = focusBarOnChart(chart, 2);
  assert.equal(start.from, 0, "clamped at the first candle");
  assert.equal(start.to - start.from, 20, "still the same zoom");

  // A bar near the live edge: clamped to the same margin the default view uses.
  const edge = focusBarOnChart(chart, 199);
  assert.equal(edge.to, 199 + DEFAULT_EDGE_MARGIN, "clamped at the newest candle plus the default margin");
  assert.equal(edge.to - edge.from, 20);

  assert.equal(focusBarOnChart(handle({ bars: 0 }), 5), null, "no candles means nothing to move");
  assert.equal(focusBarOnChart(null, 5), null);
  assert.equal(focusBarOnChart(handle({ bars: 200 }), Number.NaN), null, "a non-numeric bar is never applied");
  assert.equal(focusBarOnChart(handle({ bars: 200, timeScale: false }), 5), null);
});

test("framing the stored events fits them with a margin and stays inside the series", () => {
  const chart = handle({ bars: 500, from: 380, to: 502 });
  const applied = frameBarsOnChart(chart, 125, 126);
  assert.equal(applied.from, 125 - FRAME_PAD_CANDLES, "padding before the first event");
  assert.equal(applied.to, 126 + FRAME_PAD_CANDLES, "padding after the last event");

  // A span at the very start: never a negative index.
  const start = frameBarsOnChart(chart, 1, 2);
  assert.equal(start.from, 0);
  assert.equal(start.to, 2 + FRAME_PAD_CANDLES);

  // A span at the very end: clamped like the default view.
  const end = frameBarsOnChart(chart, 495, 499);
  assert.equal(end.to, 499 + DEFAULT_EDGE_MARGIN);
  assert.equal(end.from, 495 - FRAME_PAD_CANDLES);

  assert.equal(frameBarsOnChart(handle({ bars: 0 }), 1, 5), null, "nothing to frame");
  assert.equal(frameBarsOnChart(handle({ bars: 500 }), null, 5), null, "a missing bar is never applied");
});

test("a jump never touches the frame helper's job: framing is the only zoom change", () => {
  const chart = handle({ bars: 500, from: 380, to: 502 });
  const before = chart.chart.timeScale().getVisibleLogicalRange();
  focusBarOnChart(chart, 100);
  const after = chart.chart.timeScale().getVisibleLogicalRange();
  assert.equal(after.to - after.from, before.to - before.from, "jump: same number of candles");
  frameBarsOnChart(chart, 100, 110);
  const framed = chart.chart.timeScale().getVisibleLogicalRange();
  assert.ok(framed.to - framed.from < before.to - before.from, "frame: a tighter window around the events");
});
