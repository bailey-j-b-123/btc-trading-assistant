import assert from "node:assert/strict";
import test from "node:test";

import {
  annotationFullyOnScreen,
  chartPatternRecords,
  chooseChartPattern,
  patternAnnotation,
  patternExplanation,
  patternIconPoints,
  patternKeyLevels,
  patternTone,
  rangeRecord,
  secondaryAnnotations,
} from "../../src/trading_assistant/web/static/js/pattern_view.js";
import { functionPanelModel } from "../../src/trading_assistant/web/static/js/function_panel.js";
import {
  arcPath,
  clearPatternAnnotation,
  setPatternAnnotation,
} from "../../src/trading_assistant/web/static/js/chart.js";

/**
 * The approved Chart Patterns screen (BRAIN_Approved_Chart_Patterns): what it
 * draws must be the stored record, exactly. These tests pin the honesty rules
 * of that presentation: anchors come from the backend, the lifecycle word is
 * the backend's own, the confidence stays "Not calculated", nothing that was
 * not recorded is drawn or listed, and no target is derived.
 */

const HOUR_MS = 3_600_000;
const T0 = Date.parse("2026-09-22T00:00:00Z");
const at = (iso) => Date.parse(iso);
const isoOf = (ms) => new Date(ms).toISOString().replace(".000Z", "Z");

// The synthetic demo database's double bottom, as /api/dashboard returns it.
const B1 = at("2026-09-23T20:00:00Z");
const PEAK = at("2026-09-25T14:00:00Z");
const B2 = at("2026-09-26T21:00:00Z");
const CONFIRM = at("2026-09-28T08:00:00Z");
const TOP2 = at("2026-09-28T10:00:00Z");

function candles(count = 160) {
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    const time = T0 + i * HOUR_MS;
    let low = 82_700;
    let high = 83_300;
    if (time === B1) low = 82_349.7;
    if (time === B2) low = 82_461.0;
    if (time === PEAK) high = 84_229.9;
    if (time === TOP2) high = 84_484.4;
    if (time < B1 && time >= B1 - 30 * HOUR_MS) high = 83_300 + (B1 - time) / HOUR_MS * 10;
    rows.push([time, "83000", String(high), String(low), "83100", "12.5"]);
  }
  return rows;
}

const DOUBLE_BOTTOM = {
  id: "pattern:double_bottom:047223525f08",
  pattern_id: "187318724a00",
  type: "double_bottom",
  label: "DB",
  side: "bottom",
  state: "confirmed",
  is_latest_state: true,
  known_at: isoOf(CONFIRM),
  anchor_time: isoOf(CONFIRM),
  anchor_time_ms: CONFIRM,
  formed_at: "2026-09-27T00:00:00Z",
  confirmed_at: isoOf(CONFIRM),
  neckline: "84229.90000000",
  invalidation_level: "82349.7",
  components: [
    { kind: "low", time: isoOf(B1), time_ms: B1, price: "82349.7" },
    { kind: "high", time: isoOf(PEAK), time_ms: PEAK, price: "84229.9" },
    { kind: "low", time: isoOf(B2), time_ms: B2, price: "82461.0" },
  ],
  geometry: { difference_pct: "0.135", depth_pct: "2.10", span_candles: 73 },
};

const DOUBLE_TOP = {
  id: "pattern:double_top:67aefd670e3d",
  pattern_id: "e5dea7f53ab4",
  type: "double_top",
  label: "DT",
  side: "top",
  state: "invalidated",
  is_latest_state: true,
  known_at: "2026-09-28T18:00:00Z",
  anchor_time: "2026-09-28T18:00:00Z",
  anchor_time_ms: at("2026-09-28T18:00:00Z"),
  neckline: "82461.0",
  invalidation_level: "84484.4",
  components: [
    { kind: "high", time_ms: PEAK, price: "84229.9" },
    { kind: "low", time_ms: B2, price: "82461.0" },
    { kind: "high", time_ms: TOP2, price: "84484.4" },
  ],
};

const FORMED_BOTTOM = { ...DOUBLE_BOTTOM, id: "pattern:double_bottom:f2d1891286b6", state: "formed", is_latest_state: false, known_at: "2026-09-27T00:00:00Z", anchor_time_ms: at("2026-09-27T00:00:00Z") };

const EVIDENCE = {
  available: true,
  timeframe: "1h",
  counts: { patterns: 3 },
  patterns: [FORMED_BOTTOM, DOUBLE_BOTTOM, DOUBLE_TOP],
};

const seconds = (ms) => Math.floor(ms / 1000);

// ---------------------------------------------------------------------------
// Anchors come from the backend
// ---------------------------------------------------------------------------

test("the drawn bottoms, neckline and confirmation are the stored record's own times and prices", () => {
  const annotation = patternAnnotation(DOUBLE_BOTTOM, candles());
  assert.ok(annotation);
  assert.equal(annotation.drawing, "arcs");
  assert.deepEqual(
    annotation.extremes.map((point) => [point.label, point.timeSeconds, point.price]),
    [["Bottom 1", seconds(B1), 82_349.7], ["Bottom 2", seconds(B2), 82_461.0]],
    "the labels sit on the stored troughs, not on a nicer-looking candle",
  );
  assert.deepEqual(
    annotation.arcs.map((arc) => [arc.center.timeSeconds, arc.center.price]),
    [[seconds(B1), 82_349.7], [seconds(B2), 82_461.0]],
    "each arc turns exactly at a stored bottom",
  );
  assert.equal(annotation.neckline.price, 84_229.9, "the backend neckline price, unchanged");
  assert.deepEqual(annotation.confirmation, { kind: "confirmation", timeSeconds: seconds(CONFIRM), price: 84_229.9 });
  assert.equal(annotation.neckline.toSeconds, seconds(CONFIRM), "the neckline ends on the stored confirmation candle");
  assert.deepEqual(annotation.outline.map((point) => [point.timeSeconds, point.price]), DOUBLE_BOTTOM.components.map((point) => [seconds(point.time_ms), Number(point.price)]));
  assert.equal(annotation.title, "Double Bottom - Confirmed");
  assert.equal(annotation.extremeLine.price, 82_349.7, "the support line is the stored invalidation level (the lower bottom)");
});

test("a component on a candle that is not loaded draws nothing, never a neighbouring candle", () => {
  const rows = candles().filter((row) => row[0] !== B2);
  assert.equal(patternAnnotation(DOUBLE_BOTTOM, rows), null);
  assert.equal(patternAnnotation({ ...DOUBLE_BOTTOM, components: DOUBLE_BOTTOM.components.slice(0, 1) }, candles()), null);
});

test("the arcs are small: their ends are real loaded candles, never past the stored bottom or the neckline, never labelled", () => {
  const rows = candles();
  const annotation = patternAnnotation(DOUBLE_BOTTOM, rows);
  const gap = Math.min((PEAK - B1) / HOUR_MS, (B2 - PEAK) / HOUR_MS);
  for (const arc of annotation.arcs) {
    for (const end of [arc.left, arc.right]) {
      assert.ok(end, "both ends exist when the candles are loaded");
      const row = rows.find((candle) => seconds(candle[0]) === end.timeSeconds);
      assert.ok(row, "the end is a stored candle");
      assert.equal(end.price, Math.min(Math.max(Number(row[3]), arc.center.price), 84_229.9), "that candle's own low, held between the bottom and the neckline");
      assert.ok(!annotation.extremes.some((point) => point.timeSeconds === end.timeSeconds), "an arc end is never labelled");
    }
    const halfBars = (arc.right.timeSeconds - arc.center.timeSeconds) / 3600;
    assert.ok(arc.left.timeSeconds < arc.center.timeSeconds && arc.center.timeSeconds < arc.right.timeSeconds);
    assert.ok(halfBars <= 8 && halfBars < gap / 2, `a small arc around the trough (${halfBars} bars), not a curve across the pattern`);
  }
});

test("an arc never passes beyond the stored extreme it marks", () => {
  const center = { x: 100, y: 300 };
  const d = arcPath({ left: { x: 40, y: 180 }, center, right: { x: 170, y: 200 } });
  const numbers = d.match(/-?\d+(\.\d+)?/g).map(Number);
  const xs = numbers.filter((_, index) => index % 2 === 0);
  const ys = numbers.filter((_, index) => index % 2 === 1);
  assert.ok(Math.max(...ys) <= center.y + 1e-6, "nothing below the stored bottom");
  assert.ok(Math.min(...xs) >= 40 - 1e-6 && Math.max(...xs) <= 170 + 1e-6, "nothing outside its two ends");
  assert.equal(arcPath({ center: { x: Number.NaN, y: 1 } }), "");
});

/** A tiny DOM, enough for the chart overlay to lay itself out. */
class FakeEl {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.dataset = {};
    this.className = "";
    this.parentNode = null;
    this._text = "";
  }
  get offsetWidth() { return this.className.includes("title") ? 260 : 70; }
  get offsetHeight() { return this.className.includes("title") ? 36 : 18; }
  get clientWidth() { return 1000; }
  get clientHeight() { return 600; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
}

function all(node, predicate, found = []) {
  if (predicate(node)) found.push(node);
  for (const child of node.children) all(child, predicate, found);
  return found;
}

function withFakeDocument(callback) {
  const prior = { document: globalThis.document, getComputedStyle: globalThis.getComputedStyle };
  globalThis.document = { createElement: (tag) => new FakeEl(tag), createElementNS: (_ns, tag) => new FakeEl(tag) };
  globalThis.getComputedStyle = () => ({ position: "relative" });
  try {
    callback();
  } finally {
    globalThis.document = prior.document;
    globalThis.getComputedStyle = prior.getComputedStyle;
  }
}

/** A chart whose coordinates are a plain function of the stored time and price. */
function fakeHandle() {
  const xOf = (time) => (time - seconds(T0)) / 3600 * 6;
  const yOf = (price) => (85_000 - price) / 5;
  return {
    container: new FakeEl("div"),
    chart: {
      timeScale: () => ({ timeToCoordinate: xOf, height: () => 0 }),
      priceScale: () => ({ width: () => 0 }),
    },
    series: { priceToCoordinate: yOf },
    xOf,
    yOf,
  };
}

const byClass = (layer, name) => all(layer, (node) => node.attributes.class === name || node.className === name);

test("the chart overlay puts each label over its stored candle, the neckline at the stored price, and a short connector to it", () => {
  withFakeDocument(() => {
    const handle = fakeHandle();
    const annotation = patternAnnotation(DOUBLE_BOTTOM, candles());
    setPatternAnnotation(handle, annotation);
    const layer = handle.patternLayer;
    assert.ok(layer, "the overlay layer exists");
    const points = byClass(layer, "pattern-anno-point");
    assert.deepEqual(points.map((node) => node.textContent), ["Bottom 1", "Bottom 2"]);
    for (const node of points) {
      const time = Number(node.dataset.time);
      const left = Number.parseFloat(node.style.left);
      assert.ok(Math.abs(left + 35 - handle.xOf(time)) < 0.5, "the label is centred on its stored candle");
      assert.ok(Number.parseFloat(node.style.top) > handle.yOf(Number(node.dataset.price)), "under the bottom");
    }
    const arcs = byClass(layer, "pattern-anno-arc");
    assert.deepEqual(arcs.map((node) => Number(node.attributes["data-time"])), [seconds(B1), seconds(B2)], "one arc per stored bottom");
    assert.equal(byClass(layer, "pattern-anno-fill").length, 0, "no wash over the whole pattern");
    const neck = byClass(layer, "pattern-anno-neckline")[0];
    assert.equal(Number(neck.attributes.y1), handle.yOf(84_229.9));
    assert.equal(Number(neck.attributes.y2), handle.yOf(84_229.9));
    assert.equal(Number(neck.attributes.x2), handle.xOf(seconds(CONFIRM)), "the neckline ends on the confirmation candle");
    const title = byClass(layer, "pattern-anno-title")[0];
    assert.equal(title.textContent, "Double Bottom - Confirmed");
    assert.equal(title.dataset.tone, "green", "the colour comes from the stylesheet via the tone");
    const connector = byClass(layer, "pattern-anno-connector")[0];
    const length = Math.hypot(connector.attributes.x2 - connector.attributes.x1, connector.attributes.y2 - connector.attributes.y1);
    assert.ok(length > 4 && length < 80, `a short connector (${length.toFixed(1)}px)`);
    assert.ok(Math.abs(Number(connector.attributes.y2) - handle.yOf(84_229.9)) <= 3, "it ends on the neckline");
    assert.ok(Number(connector.attributes.x2) >= Number(neck.attributes.x1) && Number(connector.attributes.x2) <= Number(neck.attributes.x2));

    // A redraw (pan, zoom, resize) replaces the drawing: nothing is duplicated.
    setPatternAnnotation(handle, annotation);
    setPatternAnnotation(handle, annotation);
    assert.equal(byClass(handle.patternLayer, "pattern-anno-svg").length, 1);
    assert.equal(byClass(handle.patternLayer, "pattern-anno-title").length, 1);
    assert.equal(byClass(handle.patternLayer, "pattern-anno-arc").length, 2);
    assert.equal(handle.container.children.length, 1, "one overlay layer");

    clearPatternAnnotation(handle);
    assert.equal(handle.patternLayer, null);
    assert.equal(handle.patternAnnotation, null);
    assert.equal(handle.container.children.length, 0, "clearing leaves nothing behind (timeframe switch)");
  });
});

// ---------------------------------------------------------------------------
// Every type BRAIN records: its own geometry
// ---------------------------------------------------------------------------

// The multi-pattern synthetic demo's head and shoulders / inverse, as the API returns them.
const LS = T0 + 20 * HOUR_MS;
const N1 = T0 + 39 * HOUR_MS;
const HEAD = T0 + 60 * HOUR_MS;
const N2 = T0 + 81 * HOUR_MS;
const RS = T0 + 100 * HOUR_MS;
const HS_CONFIRM = T0 + 118 * HOUR_MS;

function shoulderCandles(top) {
  return candles(160).map((row) => {
    const time = row[0];
    const marks = top
      ? { [LS]: ["87471.6", "high"], [N1]: ["86043.1", "low"], [HEAD]: ["88481.3", "high"], [N2]: ["86105.1", "low"], [RS]: ["87474.0", "high"] }
      : { [LS]: ["83992.5", "low"], [N1]: ["85280.5", "high"], [HEAD]: ["82911.9", "low"], [N2]: ["85325.5", "high"], [RS]: ["83986.5", "low"] };
    const mark = marks[time];
    const base = top ? [time, "86900", "87000", "86800", "86950", "10"] : [time, "84500", "84600", "84400", "84550", "10"];
    if (!mark) return base;
    return mark[1] === "high" ? [time, base[1], mark[0], base[3], base[4], "10"] : [time, base[1], base[2], mark[0], base[4], "10"];
  });
}

const comp = (kind, time, price) => ({ kind, time: isoOf(time), time_ms: time, price });
const HEAD_AND_SHOULDERS = {
  id: "pattern:hs:confirmed", pattern_id: "hs-1", type: "head_and_shoulders", label: "HS", side: "top",
  state: "confirmed", is_latest_state: true, known_at: isoOf(HS_CONFIRM + HOUR_MS), anchor_time_ms: HS_CONFIRM,
  confirmed_at: isoOf(HS_CONFIRM + HOUR_MS), neckline: "86074.10000000", invalidation_level: "88481.3",
  components: [comp("high", LS, "87471.6"), comp("low", N1, "86043.1"), comp("high", HEAD, "88481.3"), comp("low", N2, "86105.1"), comp("high", RS, "87474.0")],
};
const INVERSE = {
  id: "pattern:ihs:confirmed", pattern_id: "ihs-1", type: "inverse_head_and_shoulders", label: "IHS", side: "bottom",
  state: "confirmed", is_latest_state: true, known_at: isoOf(HS_CONFIRM + HOUR_MS), anchor_time_ms: HS_CONFIRM,
  confirmed_at: isoOf(HS_CONFIRM + HOUR_MS), neckline: "85303.00000000", invalidation_level: "82911.9",
  components: [comp("low", LS, "83992.5"), comp("high", N1, "85280.5"), comp("low", HEAD, "82911.9"), comp("high", N2, "85325.5"), comp("low", RS, "83986.5")],
};

test("head and shoulders: three caps at the stored shoulders and head, its own neckline, no invented support line", () => {
  const annotation = patternAnnotation(HEAD_AND_SHOULDERS, shoulderCandles(true));
  assert.ok(annotation);
  assert.deepEqual(
    annotation.extremes.map((point) => [point.label, point.timeSeconds, point.price]),
    [["Left Shoulder", seconds(LS), 87_471.6], ["Head", seconds(HEAD), 88_481.3], ["Right Shoulder", seconds(RS), 87_474.0]],
  );
  assert.equal(annotation.arcs.length, 3);
  assert.ok(annotation.arcs.every((arc) => arc.opens === "down"), "caps over the highs, not troughs");
  for (const arc of annotation.arcs) {
    for (const end of [arc.left, arc.right]) assert.ok(end.price <= arc.center.price && end.price >= 86_074.1, "never above the stored high, never below the neckline");
  }
  assert.equal(annotation.neckline.price, 86_074.1);
  assert.equal(annotation.neckline.toSeconds, seconds(HS_CONFIRM));
  assert.equal(annotation.extremeLine, null, "only a double pattern has a stored-extreme line");
  assert.equal(annotation.title, "Head and Shoulders - Confirmed");
  assert.equal(annotation.tone, "red", "a confirmed top is red");
  assert.equal(annotation.labelAnchorSeconds, seconds(HEAD), "the title sits over the head");
  assert.deepEqual(patternKeyLevels(HEAD_AND_SHOULDERS).map((row) => [row.label, row.qualifier, row.value]), [
    ["Neckline", "(Breakdown)", "86,074.10"],
    ["Support", "(Target)", "Unavailable"],
    ["Resistance", "(Head)", "88,481.30"],
  ]);
  assert.match(patternExplanation(HEAD_AND_SHOULDERS), /higher high \(the head, 88,481\.30\) between two shoulders \(87,471\.60 and 87,474\.00\).*below the 86,074\.10 neckline/);
});

test("inverse head and shoulders: three troughs at the stored lows, the head's level as support, no target", () => {
  const annotation = patternAnnotation(INVERSE, shoulderCandles(false));
  assert.deepEqual(annotation.extremes.map((point) => point.label), ["Left Shoulder", "Head", "Right Shoulder"]);
  assert.deepEqual(annotation.arcs.map((arc) => arc.center.price), [83_992.5, 82_911.9, 83_986.5]);
  assert.ok(annotation.arcs.every((arc) => arc.opens === "up"));
  assert.equal(annotation.tone, "green");
  assert.deepEqual(patternKeyLevels(INVERSE).map((row) => [row.label, row.qualifier, row.value]), [
    ["Neckline", "(Breakout)", "85,303.00"],
    ["Support", "(Head)", "82,911.90"],
    ["Resistance", "(Target)", "Unavailable"],
  ]);
  // Negative: a missing head candle draws nothing at all.
  assert.equal(patternAnnotation(INVERSE, shoulderCandles(false).filter((row) => row[0] !== HEAD)), null);
});

const RANGE_START = T0 + 120 * HOUR_MS;
const RANGE_NOW = T0 + 159 * HOUR_MS;
const ACTIVE_RANGE = {
  id: `range:${isoOf(RANGE_START)}:${isoOf(RANGE_NOW)}`, evidence_kind: "range", kind: "range",
  range_high: "88640.3", range_low: "87141.6", start_time: isoOf(RANGE_START), end_time: isoOf(RANGE_NOW - 2 * HOUR_MS),
  current_time: isoOf(RANGE_NOW), known_at: isoOf(RANGE_NOW), touch_count: 15, active: true, width_pct: "1.69076594",
};

test("the trading range is the range detector's own active range, drawn as its stored box", () => {
  const record = rangeRecord({ range: ACTIVE_RANGE });
  assert.equal(record.type, "trading_range");
  assert.equal(record.state, "active", "the API serves only an active range; nothing else is claimed");
  assert.equal(record.range_high, "88640.3");
  assert.equal(rangeRecord({ range: { ...ACTIVE_RANGE, active: false } }), null, "an inactive range is not shown");
  assert.equal(rangeRecord({ range: { ...ACTIVE_RANGE, range_low: "90000" } }), null, "a malformed range is not shown");
  assert.equal(rangeRecord({ range: null }), null);
  const annotation = patternAnnotation(record, candles());
  assert.deepEqual(annotation.rangeBox, { high: 88_640.3, low: 87_141.6, fromSeconds: seconds(RANGE_START), toSeconds: seconds(RANGE_NOW) });
  assert.equal(annotation.title, "Trading Range - Active");
  assert.equal(annotation.tone, "blue");
  assert.equal(patternAnnotation(record, candles().filter((row) => row[0] !== RANGE_START)), null, "never stretched to a neighbouring candle");
  assert.deepEqual(patternKeyLevels(record).map((row) => [row.label, row.qualifier, row.value]), [
    ["Breakout", "(Target)", "Unavailable"],
    ["Support", "(Range Low)", "87,141.60"],
    ["Resistance", "(Range High)", "88,640.30"],
  ]);
  assert.match(patternExplanation(record), /between 87,141\.60 and 88,640\.30.*15 recorded touches/);
  assert.doesNotMatch(patternExplanation(record), /%/, "no percentage beside the uncalculated confidence");
  withFakeDocument(() => {
    const handle = fakeHandle();
    setPatternAnnotation(handle, annotation);
    const high = byClass(handle.patternLayer, "pattern-anno-range-high")[0];
    const low = byClass(handle.patternLayer, "pattern-anno-range-low")[0];
    assert.equal(Number(high.attributes.y1), handle.yOf(88_640.3));
    assert.equal(Number(low.attributes.y1), handle.yOf(87_141.6));
    assert.equal(Number(high.attributes.x1), handle.xOf(seconds(RANGE_START)));
    assert.equal(Number(high.attributes.x2), handle.xOf(seconds(RANGE_NOW)));
  });
});

// ---------------------------------------------------------------------------
// Default selection: deterministic, documented, stored evidence only
// ---------------------------------------------------------------------------

test("the default record is the newest current state still standing; the operator's choice wins when present", () => {
  const evidence = { available: true, patterns: [FORMED_BOTTOM, DOUBLE_BOTTOM, DOUBLE_TOP], range: ACTIVE_RANGE };
  const records = chartPatternRecords(evidence);
  assert.deepEqual(records.map((record) => record.id), [DOUBLE_BOTTOM.id, DOUBLE_TOP.id, FORMED_BOTTOM.id, ACTIVE_RANGE.id],
    "current + standing, current + invalidated, earlier state, then the range");
  assert.equal(chooseChartPattern(records, null).id, DOUBLE_BOTTOM.id, "the confirmed double bottom, though the invalidated double top is newer");
  assert.equal(chooseChartPattern(records, DOUBLE_TOP.id).id, DOUBLE_TOP.id, "an explicit choice is kept");
  assert.equal(chooseChartPattern(records, ACTIVE_RANGE.id).id, ACTIVE_RANGE.id, "the range can be chosen");
  assert.equal(chooseChartPattern(records, "pattern:from-another-timeframe").id, DOUBLE_BOTTOM.id, "a choice not in this window is never drawn");
  // Every record invalidated: the newest current state.
  const allInvalid = chartPatternRecords({ available: true, patterns: [DOUBLE_TOP, { ...DOUBLE_BOTTOM, state: "invalidated", known_at: "2026-09-28T09:00:00Z" }] });
  assert.equal(allInvalid[0].id, DOUBLE_TOP.id);
  // Ties on time are broken by id, so the order never flickers.
  const tieA = { ...DOUBLE_BOTTOM, id: "a", known_at: "2026-09-28T08:00:00Z" };
  const tieB = { ...DOUBLE_BOTTOM, id: "b", known_at: "2026-09-28T08:00:00Z" };
  assert.deepEqual(chartPatternRecords({ available: true, patterns: [tieA, tieB] }).map((r) => r.id), ["b", "a"]);
  assert.deepEqual(chartPatternRecords({ available: true, patterns: [tieB, tieA] }).map((r) => r.id), ["b", "a"]);
  // The range is the default only when no pattern record exists.
  assert.equal(chooseChartPattern(chartPatternRecords({ available: true, patterns: [], range: ACTIVE_RANGE }), null).type, "trading_range");
  assert.deepEqual(chartPatternRecords({ available: false, patterns: [DOUBLE_BOTTOM] }), [], "an unavailable payload shows nothing");
});

test("secondary drawings are other current records only, never overlapping the selected one", () => {
  const rows = shoulderCandles(true);
  const primary = patternAnnotation(HEAD_AND_SHOULDERS, rows);
  const records = [
    HEAD_AND_SHOULDERS,
    { ...HEAD_AND_SHOULDERS, id: "pattern:hs:formed", state: "formed", is_latest_state: false },
    rangeRecord({ range: ACTIVE_RANGE }),
  ];
  const secondary = secondaryAnnotations(records, primary, rows);
  assert.deepEqual(secondary.map((item) => item.id), [ACTIVE_RANGE.id], "the earlier state of the selected pattern is not drawn twice");
  assert.equal(secondary[0].secondary, true);
  assert.equal(secondary[0].name, "Trading Range");
  const overlapping = { ...ACTIVE_RANGE, id: "range:overlap", start_time: isoOf(LS) };
  assert.deepEqual(secondaryAnnotations([rangeRecord({ range: overlapping })], primary, rows), [], "an overlapping record is not drawn");
  assert.deepEqual(secondaryAnnotations([], primary, rows), []);
  withFakeDocument(() => {
    const handle = fakeHandle();
    setPatternAnnotation(handle, primary, { secondary });
    assert.deepEqual(byClass(handle.patternLayer, "pattern-anno-secondary-label").map((node) => node.textContent), ["Trading Range"]);
    assert.equal(byClass(handle.patternLayer, "pattern-anno-title").length, 1, "only the selected record has a title");
  });
});

test("a pattern family BRAIN does not detect is drawn only from a real record of it, through its own points", () => {
  assert.deepEqual(chartPatternRecords({ available: true, patterns: [] }), [], "a name in the registry creates nothing");
  const model = functionPanelModel({ functionId: "patterns", evidence: { available: true, patterns: [] } });
  assert.equal(model.detection, null);
  assert.doesNotMatch(JSON.stringify(model), /triangle|flag|wedge|pennant|channel|cup/i);
  // If a validated record of a new family ever arrives, it is drawn from its own points only.
  const future = {
    id: "pattern:asc:1", pattern_id: "asc-1", type: "ascending_triangle", side: "bottom", state: "formed", is_latest_state: true,
    known_at: isoOf(B2), neckline: "84229.9", invalidation_level: "82349.7",
    components: DOUBLE_BOTTOM.components,
  };
  const annotation = patternAnnotation(future, candles());
  assert.equal(annotation.drawing, "outline");
  assert.deepEqual(annotation.arcs, []);
  assert.deepEqual(annotation.extremes, [], "no labels are invented for it");
  assert.deepEqual(annotation.outline.map((p) => p.price), [82_349.7, 84_229.9, 82_461.0]);
  assert.equal(annotation.title, "Ascending Triangle - Formed");
  assert.equal(patternKeyLevels(future).find((row) => row.qualifier === "(Target)").value, "Unavailable");
  const unknown = patternAnnotation({ ...future, type: "mystery_shape", label: "Mystery" }, candles());
  assert.equal(unknown.title, "Mystery - Formed", "an unknown type keeps the backend's own label");
  assert.notEqual(patternIconPoints("mystery_shape"), patternIconPoints("double_bottom"), "and a neutral pictogram, not a borrowed one");
});

// ---------------------------------------------------------------------------
// Lifecycle unchanged
// ---------------------------------------------------------------------------

test("the lifecycle word and colour are the backend's own state", () => {
  const formed = patternAnnotation(FORMED_BOTTOM, candles());
  assert.equal(formed.title, "Double Bottom - Formed");
  assert.equal(formed.confirmation, null, "only a confirmed record has a confirmation point");
  assert.equal(formed.tone, "amber");
  const invalidated = patternAnnotation(DOUBLE_TOP, candles());
  assert.equal(invalidated.title, "Double Top - Invalidated");
  assert.equal(invalidated.tone, "neutral");
  assert.equal(invalidated.confirmation, null);
  assert.deepEqual(invalidated.extremes.map((point) => point.label), ["Top 1", "Top 2"]);
  assert.equal(patternTone({ side: "top", state: "confirmed" }), "red");
  assert.equal(patternTone({ side: "bottom", state: "confirmed" }), "green");
  // A record that says confirmed but whose anchor is not after its last
  // component is still called confirmed — nothing is re-labelled — but no
  // confirmation point is invented for it.
  const early = patternAnnotation({ ...DOUBLE_BOTTOM, anchor_time_ms: B2 }, candles());
  assert.equal(early.title, "Double Bottom - Confirmed");
  assert.equal(early.confirmation, null);
});

test("the Pattern Detection card carries the stored state for every record", () => {
  for (const record of EVIDENCE.patterns) {
    const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, selectedPatternId: record.id });
    assert.equal(model.detection.id, record.id);
    assert.equal(model.detection.state, record.state);
    assert.equal(model.title.state, record.state, "the existing panel fields are unchanged");
  }
});

// ---------------------------------------------------------------------------
// Confidence stays unavailable
// ---------------------------------------------------------------------------

test("confidence is never calculated: no number, no percentage, no fill", () => {
  for (const record of EVIDENCE.patterns) {
    const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, selectedPatternId: record.id });
    assert.deepEqual(model.detection.score, { calculated: false, text: "Not calculated" });
    const { geometry, note, ...rest } = model;
    const text = JSON.stringify(rest);
    assert.doesNotMatch(text, /confidence|probability|win rate|accuracy/i);
    assert.doesNotMatch(text, /"\d+(\.\d+)?%"/);
    assert.doesNotMatch(text, /87/);
  }
  // Even if a record carried a confidence-like field, the card does not show it.
  const tagged = { ...DOUBLE_BOTTOM, confidence: "0.87", score: 87 };
  const model = functionPanelModel({ functionId: "patterns", evidence: { available: true, patterns: [tagged] } });
  assert.deepEqual(model.detection.score, { calculated: false, text: "Not calculated" });
});

// ---------------------------------------------------------------------------
// No invented patterns, levels or targets
// ---------------------------------------------------------------------------

test("Other Detected Patterns lists only stored records, never a Bull Flag or Descending Triangle", () => {
  const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, selectedPatternId: DOUBLE_BOTTOM.id });
  const stored = new Set(EVIDENCE.patterns.map((record) => record.id));
  assert.ok(model.others.every((row) => stored.has(row.id)));
  assert.ok(!model.others.some((row) => row.id === DOUBLE_BOTTOM.id), "the selected record is not repeated");
  assert.doesNotMatch(JSON.stringify(model), /bull flag|descending triangle/i);
  const empty = functionPanelModel({ functionId: "patterns", evidence: { available: true, patterns: [] } });
  assert.equal(empty.detection, null);
  assert.deepEqual(empty.others, []);
  assert.ok(empty.keyLevels.every((row) => row.available === false && row.value === "Unavailable"));
});

test("Key Levels show the stored neckline and bottoms; the target is always unavailable", () => {
  const bottom = patternKeyLevels(DOUBLE_BOTTOM);
  assert.deepEqual(bottom.map((row) => [row.label, row.qualifier, row.value]), [
    ["Neckline", "(Breakout)", "84,229.90"],
    ["Support", "(Bottoms)", "82,349.70"],
    ["Resistance", "(Target)", "Unavailable"],
  ]);
  const top = patternKeyLevels(DOUBLE_TOP);
  assert.deepEqual(top.map((row) => [row.label, row.qualifier, row.value]), [
    ["Neckline", "(Breakdown)", "82,461.00"],
    ["Support", "(Target)", "Unavailable"],
    ["Resistance", "(Tops)", "84,484.40"],
  ]);
  const noNeck = patternKeyLevels({ ...DOUBLE_BOTTOM, neckline: null });
  assert.equal(noNeck[0].value, "Unavailable");
});

// ---------------------------------------------------------------------------
// Zoom survives
// ---------------------------------------------------------------------------

test("the window moves only when the pattern is not wholly on screen", () => {
  assert.equal(annotationFullyOnScreen({ fromIndex: 140, toIndex: 200 }, { from: 100, to: 222 }), true, "wholly visible: leave the window alone");
  assert.equal(annotationFullyOnScreen({ fromIndex: 40, toIndex: 120 }, { from: 100, to: 222 }), false, "partly visible: centre it at the same zoom");
  assert.equal(annotationFullyOnScreen({ fromIndex: 40, toIndex: 120 }, { from: 300, to: 422 }), false);
  assert.equal(annotationFullyOnScreen(null, { from: 0, to: 10 }), false);
});
