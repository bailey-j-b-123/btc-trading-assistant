import assert from "node:assert/strict";
import test from "node:test";

import {
  RECENT_EVENT_CANDLES,
  buildEvidenceModel,
  candleTimes,
  DEFAULT_LAYERS,
  evidenceCountText,
  findItem,
  itemsAtTime,
  knownBy,
  LAYER_DEFS,
  normalizeLayers,
  overlayPrefsFromLayers,
  toSeconds,
} from "../../src/trading_assistant/web/static/js/evidence.js";

const HOUR = 3_600_000;
const T0 = Date.parse("2026-10-06T00:00:00Z");
const ms = (index) => T0 + index * HOUR;
const iso = (index) => new Date(ms(index)).toISOString().replace(".000Z", "Z");

/** Stored candle rows as the backend returns them: [ms, open, high, low, close, volume]. */
function rows(count) {
  return Array.from({ length: count }, (_, index) => [ms(index), "100", "101", "99", "100.5", "10"]);
}

function swing(index, kind, label, price, confirmedIndex) {
  return {
    id: `swing:${kind}:${iso(index)}`,
    kind,
    label,
    time_ms: ms(index),
    time: iso(index),
    price,
    known_at: iso(confirmedIndex),
    confirmed_by: iso(confirmedIndex - 1),
    left_window: 2,
    right_window: 2,
    tie_policy: "strict",
  };
}

function evidence(overrides = {}) {
  return {
    available: true,
    swings: [
      swing(3, "high", "SH", "111.0", 6),
      swing(5, "low", "SL", "99.0", 8),
      swing(9, "high", "HH", "112.0", 12),
      swing(11, "low", "HL", "100.0", 14),
    ],
    patterns: [],
    breakouts: [],
    failed_breakouts: [],
    sweeps: [],
    retests: [],
    zones: [],
    range: null,
    equal_levels: [],
    candle_shapes: [],
    ...overrides,
  };
}

const LAYERS_ON = { structure: true, patterns: true, breakouts: true, levels: true, plan: true, htfLevels: true, candleSignals: true };

test("layer defaults keep the chart calm and expose every requested layer", () => {
  const ids = LAYER_DEFS.map((layer) => layer.id);
  assert.deepEqual(ids, ["structure", "patterns", "breakouts", "levels", "plan", "htfLevels", "candleSignals"]);
  assert.equal(DEFAULT_LAYERS.structure, true);
  assert.equal(DEFAULT_LAYERS.patterns, true);
  assert.equal(DEFAULT_LAYERS.candleSignals, false, "candle shapes are opt-in");
  assert.equal(DEFAULT_LAYERS.htfLevels, false, "higher-timeframe levels are opt-in");
  for (const layer of LAYER_DEFS) assert.match(layer.hint, /\S/);
});

test("normalizeLayers drops unknown keys and fills missing switches with defaults", () => {
  const normal = normalizeLayers({ structure: false, bogus: true, candleSignals: "yes" });
  assert.equal(normal.structure, false);
  assert.equal(normal.candleSignals, DEFAULT_LAYERS.candleSignals, "non-boolean is ignored");
  assert.equal(normal.patterns, true);
  assert.equal(Object.hasOwn(normal, "bogus"), false);
});

test("layer switches map onto the legacy overlay keys; liquidity and price-line swings are not layer-driven", () => {
  const prefs = overlayPrefsFromLayers({ levels: false, plan: true });
  assert.deepEqual(prefs, { zones: false, range: false, planLevels: true, swings: false });
  assert.deepEqual(overlayPrefsFromLayers({ levels: true, plan: false }), { zones: true, range: true, planLevels: false, swings: false });
  assert.equal(Object.hasOwn(overlayPrefsFromLayers(DEFAULT_LAYERS), "equalLevels"), false);
});

test("swing labels are drawn at their candle and only once known", () => {
  const candles = rows(20);
  const model = buildEvidenceModel({ evidence: evidence(), candles, asOfMs: ms(12), layers: LAYERS_ON });
  const labels = model.markers.map((marker) => marker.text);
  // HL (known at index 14) is after the as-of instant, so it is hidden.
  assert.deepEqual(labels, ["SH", "SL", "HH"]);
  assert.equal(model.hiddenFuture, 1);
  const shMarker = model.markers.find((marker) => marker.text === "SH");
  assert.equal(shMarker.time, toSeconds(ms(3)));
  assert.equal(shMarker.position, "aboveBar");
});

test("a swing is never drawn before its known_at instant", () => {
  const candles = rows(20);
  for (let index = 0; index < 20; index += 1) {
    const model = buildEvidenceModel({ evidence: evidence(), candles, asOfMs: ms(index), layers: LAYERS_ON });
    for (const marker of model.markers) {
      assert.ok(marker.time * 1000 <= ms(index), `marker at ${marker.time} drawn at as-of ${index}`);
    }
  }
  assert.equal(knownBy({ known_at: iso(6) }, ms(6)), true);
  assert.equal(knownBy({ known_at: iso(7) }, ms(6)), false);
  assert.equal(knownBy({}, ms(6)), false, "missing known_at is never assumed known");
});

test("evidence never draws at a candle time that is not in the stored series", () => {
  // Candles 0..4 only: swing at index 9 has no candle on screen and must not be drawn.
  const model = buildEvidenceModel({ evidence: evidence(), candles: rows(5), asOfMs: ms(20), layers: LAYERS_ON });
  assert.deepEqual(model.markers.map((marker) => marker.text), ["SH"]);
});

test("unavailable or empty evidence produces an empty model, never invented markers", () => {
  const empty = buildEvidenceModel({ evidence: { available: false, reason: "no candles" }, candles: rows(10), asOfMs: ms(9), layers: LAYERS_ON });
  assert.equal(empty.markers.length, 0);
  assert.equal(empty.patternLines.length, 0);
  const noCandles = buildEvidenceModel({ evidence: evidence(), candles: [], asOfMs: ms(9), layers: LAYERS_ON });
  assert.equal(noCandles.markers.length, 0);
  assert.equal(evidenceCountText(noCandles), "No evidence drawn for the enabled layers at this candle window.");
});

test("switching the structure layer off removes its labels entirely", () => {
  const model = buildEvidenceModel({ evidence: evidence(), candles: rows(20), asOfMs: ms(19), layers: { ...LAYERS_ON, structure: false } });
  assert.equal(model.markers.filter((marker) => ["SH", "SL", "HH", "HL"].includes(marker.text)).length, 0);
});

function doubleTopPattern(state, extra = {}) {
  const components = [
    { time_ms: ms(2), time: iso(2), kind: "high", price: "111.0", known_at: iso(5) },
    { time_ms: ms(4), time: iso(4), kind: "low", price: "99.0", known_at: iso(7) },
    { time_ms: ms(6), time: iso(6), kind: "high", price: "111.2", known_at: iso(9) },
  ];
  return {
    id: `pattern-record-${state}`,
    pattern_id: "pattern-root",
    type: "double_top",
    label: "Double top",
    side: "top",
    state,
    is_latest_state: true,
    known_at: iso(state === "formed" ? 9 : state === "confirmed" ? 12 : 13),
    formed_at: iso(9),
    confirmation_time: state === "confirmed" ? iso(12) : null,
    // Backend anchors (candle opens): formed -> last swing candle; confirmed/invalidated -> the
    // candle whose close is the known_at boundary (known_at - 1 interval).
    anchor_time: state === "formed" ? iso(6) : state === "confirmed" ? iso(11) : iso(12),
    confirmed_at: state === "confirmed" ? iso(12) : null,
    invalidated_at: state === "invalidated" ? iso(13) : null,
    neckline: "99.0",
    invalidation_level: "111.2",
    components,
    ...extra,
  };
}

test("a formed pattern draws a dashed zigzag, neckline and invalidation at backend levels", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("formed")] }),
    candles: rows(20),
    asOfMs: ms(10),
    layers: LAYERS_ON,
  });
  const zigzag = model.patternLines.find((line) => line.role === "pattern-zigzag");
  assert.equal(zigzag.lineStyle, 2, "formed patterns are dashed");
  assert.deepEqual(zigzag.points.map((point) => point.value), [111, 99, 111.2]);
  const neckline = model.patternLines.find((line) => line.role === "pattern-neckline");
  assert.equal(neckline.points[0].value, 99);
  assert.equal(neckline.points[1].time, toSeconds(ms(19)), "unbroken neckline runs to the last candle on screen, never beyond it");
  const invalidation = model.patternLines.find((line) => line.role === "pattern-invalidation");
  assert.equal(invalidation.points[0].value, 111.2);
  const marker = model.markers.find((item) => item.text.startsWith("DT"));
  assert.equal(marker.text, "DT · formed");
});

test("a confirmed pattern ends its neckline at the confirmation candle and labels the state", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("confirmed")] }),
    candles: rows(20),
    asOfMs: ms(13),
    layers: LAYERS_ON,
  });
  const neckline = model.patternLines.find((line) => line.role === "pattern-neckline");
  // The neckline stops at the candle that closed through it (open ms(11)), not at its close boundary (ms(12)).
  assert.equal(neckline.points[1].time, toSeconds(ms(11)));
  assert.equal(model.markers.find((item) => item.text.startsWith("DT")).text, "DT · confirmed");
});

test("only the latest state of a pattern is drawn; history stays in the explanation", () => {
  const formed = doubleTopPattern("formed", { is_latest_state: false });
  const confirmed = doubleTopPattern("confirmed");
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [formed, confirmed] }),
    candles: rows(20),
    asOfMs: ms(13),
    layers: LAYERS_ON,
  });
  const labels = model.markers.filter((item) => item.text.startsWith("DT"));
  assert.equal(labels.length, 1);
  assert.equal(labels[0].text, "DT · confirmed");
});

test("invalidated patterns use the muted dotted zigzag and never claim confirmation", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("invalidated", { confirmation_time: null })] }),
    candles: rows(20),
    asOfMs: ms(14),
    layers: LAYERS_ON,
  });
  const zigzag = model.patternLines.find((line) => line.role === "pattern-zigzag");
  assert.equal(zigzag.lineStyle, 1);
  assert.equal(model.markers.find((item) => item.text.startsWith("DT")).text, "DT · invalidated");
});

test("pattern components unknown to the candle series are dropped rather than bridged", () => {
  const pattern = doubleTopPattern("formed");
  pattern.components[1].time_ms = ms(50); // not on screen
  const model = buildEvidenceModel({ evidence: evidence({ patterns: [pattern] }), candles: rows(20), asOfMs: ms(10), layers: LAYERS_ON });
  const zigzag = model.patternLines.find((line) => line.role === "pattern-zigzag");
  assert.equal(zigzag.points.length, 2);
  assert.ok(zigzag.points.every((point) => point.time !== toSeconds(ms(50))));
});

test("breakouts, failed breakouts, sweeps and retests get their own labelled markers", () => {
  const breakout = {
    id: "bo-1", kind: "breakout", direction: "bullish", reference_type: "swing_high", band_low: "110", band_high: "111",
    time_ms: ms(5), time: iso(5), known_at: iso(6), breakout_close: "112", penetration_pct: "0.5", confirmation_candles: 1,
  };
  const sweep = { id: "sw-1", kind: "sweep", direction: "below", reference_type: "swing_low", band_low: "99", band_high: "100", time_ms: ms(8), time: iso(8), known_at: iso(9), extreme: "98", reclaim_close: "99.5" };
  const retest = { id: "rt-1", kind: "retest", state: "held", direction: "bullish", band_low: "110", band_high: "111", time_ms: ms(9), time: iso(9), known_at: iso(10), distance_from_band: "0.1", elapsed_candles: 3 };
  const model = buildEvidenceModel({
    evidence: evidence({ breakouts: [breakout], sweeps: [sweep], retests: [retest] }),
    candles: rows(20),
    asOfMs: ms(19),
    layers: LAYERS_ON,
  });
  // Markers are shapes only (no label text, so dense charts stay readable); the click panel names each event.
  const shapes = model.markers.map((marker) => marker.shape);
  assert.ok(shapes.includes("arrowUp"), "bullish breakout arrow");
  assert.ok(shapes.includes("arrowDown"), "sweep arrow");
  assert.ok(shapes.includes("circle"), "retest circle");
  const arrows = model.markers.filter((marker) => marker.shape === "arrowUp" || marker.shape === "arrowDown");
  assert.ok(arrows.length > 0 && arrows.every((marker) => marker.text === ""), "no label text on breakout and sweep arrows");
  assert.equal(model.counts.breakouts, 3);
  const off = buildEvidenceModel({ evidence: evidence({ breakouts: [breakout] }), candles: rows(20), asOfMs: ms(19), layers: { ...LAYERS_ON, breakouts: false } });
  assert.equal(off.counts.breakouts, 0);
});

test("candle shapes are opt-in and show one primary marker per candle by priority", () => {
  const shapes = [
    { id: "s1", kind: "strong_bullish_body", direction: "bullish", time_ms: ms(7), time: iso(7), known_at: iso(8), open: "100", high: "110", low: "100", close: "109" },
    { id: "s2", kind: "bullish_engulfing", direction: "bullish", time_ms: ms(7), time: iso(7), known_at: iso(8), open: "100", high: "110", low: "99", close: "109" },
    { id: "s3", kind: "indecision", direction: "neutral", time_ms: ms(9), time: iso(9), known_at: iso(10), open: "100", high: "105", low: "95", close: "100.2" },
  ];
  const offModel = buildEvidenceModel({ evidence: evidence({ candle_shapes: shapes }), candles: rows(20), asOfMs: ms(19), layers: { ...LAYERS_ON, candleSignals: false } });
  assert.equal(offModel.markers.filter((m) => m.shape === "circle" && m.size === 0.8).length, 0);
  const onModel = buildEvidenceModel({ evidence: evidence({ candle_shapes: shapes }), candles: rows(20), asOfMs: ms(19), layers: { ...LAYERS_ON, candleSignals: true } });
  const shapeMarkers = onModel.markers.filter((m) => m.size === 0.8);
  assert.equal(shapeMarkers.length, 2, "one marker per candle, not per shape");
  assert.equal(shapeMarkers[0].text, "Bull engulf", "engulfing outranks a strong body on the same candle");
  // Two candles apart is closer than the label spacing: the second keeps its dot but loses its text.
  assert.equal(shapeMarkers[1].text, "", "a label closer than LABEL_GAP_CANDLES is hidden, not overlapped");
  assert.ok(onModel.labelsRemoved >= 1, "at least the close shape label is removed (fixtures may add other labelled items)");
});

test("clicking a candle returns its evidence in priority order", () => {
  const model = buildEvidenceModel({
    evidence: evidence({
      breakouts: [{ id: "bo-1", kind: "breakout", direction: "bullish", reference_type: "swing_high", band_low: "1", band_high: "2", time_ms: ms(3), time: iso(3), known_at: iso(4), breakout_close: "3", penetration_pct: "1", confirmation_candles: 1 }],
    }),
    candles: rows(20),
    asOfMs: ms(19),
    layers: LAYERS_ON,
  });
  const items = itemsAtTime(model, toSeconds(ms(3)));
  assert.deepEqual(items.map((item) => item.group), ["breakouts", "structure"], "breakouts outrank structure at one candle");
  assert.equal(itemsAtTime(model, toSeconds(ms(15))).length, 0);
});

test("findItem resolves backend ids across groups and never invents a match", () => {
  const payload = evidence({ zones: [{ id: "zone-a", kind: "zone", band_low: "1", band_high: "2" }] });
  assert.equal(findItem(payload, "zone-a").group, "zones");
  assert.equal(findItem(payload, "missing"), null);
  assert.equal(findItem(null, "zone-a"), null);
});

test("candleTimes accepts only rows with a usable timestamp", () => {
  const times = candleTimes([[ms(1), "1", "1", "1", "1", "1"], [null, "1"], ["bad"]]);
  assert.deepEqual([...times], [toSeconds(ms(1))]);
});


test("breakout and sweep markers are limited to the recent window and one marker per candle", () => {
  const count = 200;
  const candles = rows(count);
  const sweepAt = (index, id) => ({
    id,
    kind: "sweep",
    time_ms: ms(index),
    known_at: iso(index + 1),
    direction: "below",
    state: "observed",
  });
  const breakoutAt = (index, id) => ({
    id,
    kind: "breakout",
    time_ms: ms(index),
    known_at: iso(index + 1),
    direction: "bullish",
  });
  const evidence = {
    available: true,
    swings: [],
    patterns: [],
    breakouts: [breakoutAt(10, "b-old"), breakoutAt(190, "b-recent-1"), breakoutAt(190, "b-recent-2")],
    failed_breakouts: [],
    sweeps: [sweepAt(190, "s-recent")],
    retests: [],
    candle_shapes: [],
  };
  const model = buildEvidenceModel({
    evidence,
    candles,
    asOfMs: ms(count),
    layers: { ...DEFAULT_LAYERS, breakouts: true },
  });
  // Old breakout (index 10) is outside the last RECENT_EVENT_CANDLES candles: not drawn, counted.
  assert.equal(RECENT_EVENT_CANDLES, 40);
  assert.equal(model.hiddenOlder.breakouts, 1);
  const eventMarkers = model.markers.filter((marker) => marker.shape === "arrowDown");
  assert.equal(eventMarkers.length, 1, "one marker for the candle holding three events");
  assert.equal(eventMarkers[0].text, "", "event markers carry no label text");
  // Every event on the candle stays registered and clickable.
  assert.equal(itemsAtTime(model, toSeconds(ms(190))).length, 3);
  assert.match(evidenceCountText(model), /1 older event not drawn/);
});

test("candle-shape markers outside the recent window are not drawn but are counted", () => {
  const count = 120;
  const shape = (index, kind) => ({
    id: `shape:${index}`,
    kind,
    time_ms: ms(index),
    known_at: iso(index + 1),
    direction: "bullish",
  });
  const evidence = {
    available: true,
    swings: [],
    patterns: [],
    candle_shapes: [shape(5, "strong_bullish_body"), shape(100, "bullish_engulfing")],
  };
  const model = buildEvidenceModel({
    evidence,
    candles: rows(count),
    asOfMs: ms(count),
    layers: { ...DEFAULT_LAYERS, candleSignals: true },
  });
  assert.equal(model.hiddenOlder.candleSignals, 1);
  assert.equal(model.counts.candleSignals, 1);
  assert.equal(model.markers.filter((marker) => marker.text === "Bull engulf").length, 1);
  assert.equal(model.markers.some((marker) => marker.text === "Strong bull body"), false);
});

test("breakout arrows point the way their label says; retests use a circle", () => {
  const candles = rows(20);
  const evidence = {
    available: true,
    swings: [],
    patterns: [],
    breakouts: [{ id: "bull", kind: "breakout", time_ms: ms(15), known_at: iso(16), direction: "bullish" }],
    failed_breakouts: [],
    sweeps: [],
    retests: [{ id: "re", kind: "retest", time_ms: ms(17), known_at: iso(18), state: "held", direction: "bearish" }],
    candle_shapes: [],
  };
  const model = buildEvidenceModel({ evidence, candles, asOfMs: ms(20), layers: { ...DEFAULT_LAYERS, breakouts: true } });
  const up = model.markers.find((marker) => marker.shape === "arrowUp");
  assert.ok(up, "bullish breakout is drawn as an up arrow");
  assert.equal(up.position, "belowBar");
  const retest = model.markers.find((marker) => marker.shape === "circle");
  assert.ok(retest, "retest is drawn as a circle");
});

// --- Pattern timing edge cases (audit P2): the latest candle and the lifecycle -------------

test("a confirmation on the latest stored candle keeps its marker even though known_at is the close boundary", () => {
  // Stored candles 0..12; the breaking candle is 12, the latest one. known_at = ms(13) = as_of
  // lies beyond candle 12's open. Before the fix the anchor was the boundary and the marker vanished.
  const pattern = doubleTopPattern("confirmed");
  pattern.known_at = iso(13);
  pattern.confirmation_time = iso(13);
  pattern.confirmed_at = iso(13);
  pattern.anchor_time = iso(12);
  const model = buildEvidenceModel({ evidence: evidence({ patterns: [pattern] }), candles: rows(13), asOfMs: ms(13), layers: LAYERS_ON });
  const marker = model.markers.find((item) => item.text === "DT · confirmed");
  assert.ok(marker, "marker must still be drawn on the latest candle");
  assert.equal(marker.time, toSeconds(ms(12)));
  assert.equal(model.counts.patterns, 1);
});

test("a newly formed pattern on the latest candle keeps its marker, anchored on its last swing", () => {
  const pattern = doubleTopPattern("formed");
  pattern.known_at = iso(13);
  pattern.formed_at = iso(13);
  pattern.anchor_time = iso(6); // last swing candle, not the close boundary
  const model = buildEvidenceModel({ evidence: evidence({ patterns: [pattern] }), candles: rows(13), asOfMs: ms(13), layers: LAYERS_ON });
  const marker = model.markers.find((item) => item.text.startsWith("DT"));
  assert.ok(marker, "formed marker must not be dropped for a boundary timestamp");
  assert.equal(marker.time, toSeconds(ms(6)));
  assert.equal(model.counts.patterns, 1);
});

test("an invalidation label is placed on the invalidating candle, not at formation", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("invalidated", { confirmation_time: null })] }),
    candles: rows(20),
    asOfMs: ms(14),
    layers: LAYERS_ON,
  });
  const marker = model.markers.find((item) => item.text === "DT · invalidated");
  assert.ok(marker);
  assert.equal(marker.time, toSeconds(ms(12)), "the invalidating candle");
  assert.notEqual(marker.time, toSeconds(ms(9)), "must not look like it invalidated at formation");
});

test("an invalidated pattern's level stops at the invalidating candle", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("invalidated", { confirmation_time: null })] }),
    candles: rows(20),
    asOfMs: ms(14),
    layers: LAYERS_ON,
  });
  const level = model.patternLines.find((line) => line.role === "pattern-invalidation");
  assert.equal(level.points[1].time, toSeconds(ms(12)));
});

test("a formed pattern's level still runs to the latest candle", () => {
  const model = buildEvidenceModel({
    evidence: evidence({ patterns: [doubleTopPattern("formed")] }),
    candles: rows(20),
    asOfMs: ms(13),
    layers: LAYERS_ON,
  });
  const level = model.patternLines.find((line) => line.role === "pattern-invalidation");
  assert.equal(level.points[1].time, toSeconds(ms(19)));
});

test("legacy payloads without anchor_time still anchor formed patterns, and never guess a confirmed anchor from a boundary", () => {
  const legacyFormed = doubleTopPattern("formed");
  delete legacyFormed.anchor_time;
  const formedModel = buildEvidenceModel({ evidence: evidence({ patterns: [legacyFormed] }), candles: rows(20), asOfMs: ms(13), layers: LAYERS_ON });
  assert.equal(formedModel.markers.find((item) => item.text.startsWith("DT")).time, toSeconds(ms(9)));

  const legacyConfirmed = doubleTopPattern("confirmed");
  delete legacyConfirmed.anchor_time;
  const confirmedModel = buildEvidenceModel({ evidence: evidence({ patterns: [legacyConfirmed] }), candles: rows(20), asOfMs: ms(13), layers: LAYERS_ON });
  assert.equal(confirmedModel.markers.filter((item) => item.text.startsWith("DT")).length, 0);
});

// --- Chart label collisions (audit P4) -----------------------------------------------------

import { describeEvidenceItem, evidenceItemRows, gateMarkerLabels, LABEL_GAP_CANDLES, patternMarkerText } from "../../src/trading_assistant/web/static/js/evidence.js";

test("pattern labels use short names with the lifecycle state", () => {
  assert.equal(patternMarkerText({ type: "double_top", state: "confirmed", label: "Double top" }), "DT · confirmed");
  assert.equal(patternMarkerText({ type: "double_bottom", state: "invalidated" }), "DB · invalidated");
  assert.equal(patternMarkerText({ type: "head_and_shoulders", state: "formed" }), "H&S · formed");
  assert.equal(patternMarkerText({ type: "inverse_head_and_shoulders", state: "confirmed" }), "iH&S · confirmed");
});

test("gateMarkerLabels keeps the highest-priority label and removes only conflicting neighbours on the same side", () => {
  const marker = (position, text) => ({ position, text, time: 0 });
  const pattern = marker("aboveBar", "DT · confirmed");
  const swingNear = marker("aboveBar", "HH");
  const swingFar = marker("aboveBar", "LH");
  const swingOtherSide = marker("belowBar", "HL");
  const removed = gateMarkerLabels([
    { marker: swingNear, priority: 2, index: 10 },
    { marker: pattern, priority: 0, index: 12 },
    { marker: swingFar, priority: 2, index: 12 + LABEL_GAP_CANDLES },
    { marker: swingOtherSide, priority: 2, index: 11 },
  ]);
  assert.equal(pattern.text, "DT · confirmed", "patterns are placed first and always keep their label");
  assert.equal(swingNear.text, "", "a swing within the gap of the pattern on the same side loses its text");
  assert.equal(swingFar.text, "LH", "exactly the gap away is allowed");
  assert.equal(swingOtherSide.text, "HL", "the opposite side is independent");
  assert.equal(removed, 1);
});

test("gateMarkerLabels is deterministic: the same input gives the same output", () => {
  const build = () => Array.from({ length: 12 }, (_, i) => ({ marker: { position: "aboveBar", text: `L${i}` }, priority: i % 3, index: i * 2 }));
  const first = build();
  const second = build();
  gateMarkerLabels(first);
  gateMarkerLabels(second);
  assert.deepEqual(first.map((c) => c.marker.text), second.map((c) => c.marker.text));
});

test("dense swings keep a bounded number of readable labels on the chart", () => {
  const swings = Array.from({ length: 40 }, (_, i) => ({
    id: `sw${i}`, kind: i % 2 ? "low" : "high", label: i % 2 ? "HL" : "HH",
    time_ms: ms(i), time: iso(i), known_at: iso(i + 1), price: String(100 + i),
  }));
  const model = buildEvidenceModel({ evidence: evidence({ swings }), candles: rows(60), asOfMs: ms(59), layers: LAYERS_ON });
  const labelled = model.markers.filter((m) => m.text);
  assert.ok(labelled.length <= Math.ceil(40 / LABEL_GAP_CANDLES) * 2, `labelled=${labelled.length}`);
  assert.ok(model.markers.length === 40, "every swing keeps its dot");
  assert.ok(model.labelsRemoved > 0);
});

test("the item list names every drawn item, including those whose chart label was hidden", () => {
  const swings = [
    { id: "a", kind: "high", label: "HH", time_ms: ms(4), time: iso(4), known_at: iso(5), price: "104" },
    { id: "b", kind: "low", label: "HL", time_ms: ms(5), time: iso(5), known_at: iso(6), price: "101" },
  ];
  const model = buildEvidenceModel({ evidence: evidence({ swings }), candles: rows(12), asOfMs: ms(11), layers: LAYERS_ON });
  const rows2 = evidenceItemRows(model);
  assert.equal(rows2.length, 2);
  assert.equal(rows2[0].name, "Swing HH (high)");
  assert.equal(rows2[1].name, "Swing HL (low)");
  assert.equal(describeEvidenceItem({ group: "patterns", type: "double_bottom", state: "formed" }), "Double bottom · formed");
});

// Regression (PR #38 P4): every drawn chart item must be reachable as text, not only by clicking the canvas.
test("the dashboard renders the evidence item list and the marker key names the pattern abbreviations", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(
    new URL("../../src/trading_assistant/web/static/js/views/dashboard.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /evidenceItemRows\(model\)/);
  assert.match(source, /class: "chart-items"/);
  assert.match(source, /onclick: \(\) => showExplanationAt\(row\.timeSeconds\)/);
  assert.match(source, /DT double top, DB double bottom, H&S head and shoulders, iH&S inverse H&S/);
});
