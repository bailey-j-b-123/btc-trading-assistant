import assert from "node:assert/strict";
import test from "node:test";

import {
  annotationFullyOnScreen,
  patternAnnotation,
  patternKeyLevels,
  patternTone,
} from "../../src/trading_assistant/web/static/js/pattern_view.js";
import { functionPanelModel } from "../../src/trading_assistant/web/static/js/function_panel.js";
import {
  clearPatternAnnotation,
  monotonePath,
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
  assert.deepEqual(
    annotation.extremes.map((point) => [point.label, point.timeSeconds, point.price]),
    [["Bottom 1", seconds(B1), 82_349.7], ["Bottom 2", seconds(B2), 82_461.0]],
    "the labels sit on the stored troughs, not on a nicer-looking candle",
  );
  assert.equal(annotation.neckline.price, 84_229.9, "the backend neckline price, unchanged");
  assert.deepEqual(annotation.confirmation, { kind: "confirmation", timeSeconds: seconds(CONFIRM), price: 84_229.9 });
  const components = annotation.curve.filter((point) => point.kind === "component");
  assert.deepEqual(components.map((point) => [point.timeSeconds, point.price]), DOUBLE_BOTTOM.components.map((point) => [seconds(point.time_ms), Number(point.price)]));
  assert.equal(annotation.title, "Double Bottom - Confirmed");
  assert.equal(annotation.extremeLine.price, 82_349.7, "the support line is the lower stored bottom");
});

test("a component on a candle that is not loaded draws nothing, never a neighbouring candle", () => {
  const rows = candles().filter((row) => row[0] !== B2);
  assert.equal(patternAnnotation(DOUBLE_BOTTOM, rows), null);
  assert.equal(patternAnnotation({ ...DOUBLE_BOTTOM, components: DOUBLE_BOTTOM.components.slice(0, 1) }, candles()), null);
});

test("the only non-component point is a real loaded candle, capped at the neckline and never labelled", () => {
  const rows = candles();
  const annotation = patternAnnotation(DOUBLE_BOTTOM, rows);
  const wall = annotation.wall;
  assert.equal(wall.kind, "wall");
  const row = rows.find((candle) => seconds(candle[0]) === wall.timeSeconds);
  assert.ok(row, "the wall ends on a stored candle");
  assert.ok(wall.price <= Number(row[2]) && wall.price <= 84_229.9);
  assert.ok(wall.timeSeconds < seconds(B1));
  assert.ok(!annotation.extremes.some((point) => point.timeSeconds === wall.timeSeconds));
});

test("the smooth curve never dips below a stored bottom or rises above the stored peak", () => {
  const points = [{ x: 0, y: 100 }, { x: 40, y: 300 }, { x: 90, y: 120 }, { x: 140, y: 290 }, { x: 200, y: 100 }];
  const numbers = monotonePath(points).match(/-?\d+(\.\d+)?/g).map(Number);
  const ys = numbers.filter((_, index) => index % 2 === 1);
  assert.ok(Math.max(...ys) <= 300 + 1e-6 && Math.min(...ys) >= 100 - 1e-6, "control points stay inside the stored range");
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

test("the chart overlay puts each label over its stored candle and the neckline at the stored price", () => {
  withFakeDocument(() => {
    const handle = fakeHandle();
    const annotation = patternAnnotation(DOUBLE_BOTTOM, candles());
    setPatternAnnotation(handle, annotation);
    const layer = handle.patternLayer;
    assert.ok(layer, "the overlay layer exists");
    const points = all(layer, (node) => node.className === "pattern-anno-point");
    assert.deepEqual(points.map((node) => node.textContent), ["Bottom 1", "Bottom 2"]);
    for (const node of points) {
      const time = Number(node.dataset.time);
      const left = Number.parseFloat(node.style.left);
      assert.ok(Math.abs(left + 35 - handle.xOf(time)) < 0.5, "the label is centred on its stored candle");
      assert.ok(Number.parseFloat(node.style.top) > handle.yOf(Number(node.dataset.price)), "under the bottom");
    }
    const neck = all(layer, (node) => node.attributes.class === "pattern-anno-neckline")[0];
    assert.equal(Number(neck.attributes.y1), handle.yOf(84_229.9));
    assert.equal(Number(neck.attributes.y2), handle.yOf(84_229.9));
    const title = all(layer, (node) => node.className === "pattern-anno-title")[0];
    assert.equal(title.textContent, "Double Bottom - Confirmed");
    assert.equal(title.dataset.tone, "green", "the colour comes from the stylesheet via the tone");
    const connector = all(layer, (node) => node.attributes.class === "pattern-anno-connector")[0];
    assert.equal(Number(connector.attributes.x2), handle.xOf(seconds(CONFIRM)), "the connector ends on the confirmation candle");

    // A resize redraws at the same candles: new coordinates, same stored anchors.
    clearPatternAnnotation(handle);
    assert.equal(handle.patternLayer, null);
    assert.equal(handle.patternAnnotation, null);
    assert.equal(handle.container.children.length, 0, "clearing leaves nothing behind (timeframe switch)");
  });
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
