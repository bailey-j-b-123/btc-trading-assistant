import assert from "node:assert/strict";
import test from "node:test";

import { captureLogicalView, restoreLogicalView } from "../../src/trading_assistant/web/static/js/chart.js";

/**
 * The chart view has to survive two different events:
 *  * a data refresh on the same timeframe, which puts back the same instants
 *    (captureChartView/restoreChartView, covered elsewhere);
 *  * a timeframe switch or a chart-function switch, which loads different or
 *    fewer candles. The comparable thing there is the visible index window: the
 *    same number of candles on screen, at the same place relative to the newest
 *    one. These helpers do that, clamped to what is actually loaded.
 */

/** Minimal stand-in for the lightest-charts handle the dashboard holds. */
function handle({ bars = 200, range = { from: 100, to: 130 }, destroyed = false, timeScale = true } = {}) {
  const calls = [];
  return {
    destroyed,
    barCount: bars,
    calls,
    chart: timeScale
      ? {
          timeScale: () => ({
            getVisibleLogicalRange: () => range,
            setVisibleLogicalRange: (next) => calls.push(next),
          }),
        }
      : {},
  };
}

test("capturing a view keeps the index window, not the wall clock", () => {
  assert.deepEqual(captureLogicalView(handle()), { from: 100, to: 130 });
  assert.equal(captureLogicalView(handle({ range: null })), null);
  assert.equal(captureLogicalView(handle({ range: { from: 5, to: 5 } })), null, "an empty window is not a view");
  assert.equal(captureLogicalView(handle({ range: { from: NaN, to: 30 } })), null);
  assert.equal(captureLogicalView(handle({ destroyed: true })), null);
  assert.equal(captureLogicalView(handle({ timeScale: false })), null);
  assert.equal(captureLogicalView(null), null);
});

test("a wider series keeps the user's window exactly", () => {
  const chart = handle({ bars: 500, range: { from: 10, to: 40 } });
  const captured = captureLogicalView(chart);
  restoreLogicalView(chart, captured);
  assert.deepEqual(chart.calls, [{ from: 10, to: 40 }]);
});

test("a shorter series shifts the window so no gap is shown past the newest candle", () => {
  // The user followed the live edge on the finer timeframe: bars 480..510 of 512.
  const captured = { from: 480, to: 510 };
  const chart = handle({ bars: 128 });
  restoreLogicalView(chart, captured);
  const [applied] = chart.calls;
  assert.equal(applied.to, 130, "the right edge is clamped to just past the newest candle");
  assert.equal(applied.to - applied.from, 30, "the same number of candles stays on screen");
});

test("the same zoom width is kept and the window never starts before the first candle", () => {
  const chart = handle({ bars: 40 });
  restoreLogicalView(chart, { from: -5, to: 25 });
  assert.deepEqual(chart.calls, [{ from: 0, to: 30 }], "a window starting before the first candle slides in without changing its width");
});

test("a window that ran past the newest candle comes back to the live edge", () => {
  // The user was following the live edge on a finer timeframe with a very wide
  // window; the coarser timeframe has fewer candles than the window started at.
  const chart = handle({ bars: 10 });
  restoreLogicalView(chart, { from: 380, to: 410 });
  const [applied] = chart.calls;
  assert.equal(applied.to, 12, "the right edge lands just past the newest candle");
  assert.equal(applied.to - applied.from, 10, "the window shrinks to the loaded series, never past it");
});

test("restoring is skipped when there is nothing to restore it onto", () => {
  const untouched = handle();
  restoreLogicalView(untouched, null);
  assert.deepEqual(untouched.calls, [], "a null capture leaves the chart untouched");
  const empty = handle({ bars: 0 });
  restoreLogicalView(empty, { from: 1, to: 9 });
  assert.deepEqual(empty.calls, [], "no candles loaded means no view to set");
  const gone = handle({ destroyed: true });
  restoreLogicalView(gone, { from: 1, to: 9 });
  assert.deepEqual(gone.calls, [], "a disposed chart is never touched");
  const noScale = handle({ timeScale: false });
  restoreLogicalView(noScale, { from: 1, to: 9 });
  assert.deepEqual(noScale.calls, []);
});
