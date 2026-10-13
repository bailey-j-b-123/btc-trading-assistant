import assert from "node:assert/strict";
import test from "node:test";

import {
  CHART_FUNCTIONS,
  CHART_FUNCTION_IDS,
  CHART_MODES,
  DEFAULT_CHART_FUNCTION,
  DEFAULT_CHART_MODE,
  DETAILED_MODE,
  SIMPLE_MODE,
  chartFunctionById,
  eventCatalogue,
  evidenceGroupsFor,
  filterEvidence,
  functionHint,
  functionStatus,
  highlightedPattern,
  layersForSelection,
  levelCatalogue,
  normalizeFunctionId,
  normalizeMode,
  overlayPrefsForSelection,
  patternCatalogue,
  swingCatalogue,
  swingConnectors,
  swingEmphasis,
} from "../../src/trading_assistant/web/static/js/chart_functions.js";
import { LAYER_DEFS, DEFAULT_LAYERS } from "../../src/trading_assistant/web/static/js/evidence.js";

const LAYER_IDS = LAYER_DEFS.map((layer) => layer.id);

const HOUR = 3_600_000;
const T0 = Date.parse("2026-10-06T00:00:00Z");
const ms = (index) => T0 + index * HOUR;
const iso = (index) => new Date(ms(index)).toISOString().replace(".000Z", "Z");

/** One stored swing record as the chart-evidence payload carries it. */
function swingRecord(index, kind, label, price) {
  return {
    id: `swing:${label}:${index}`,
    kind,
    label,
    price: String(price),
    time_ms: ms(index),
    known_at: iso(index),
    confirmed_by: iso(index + 1),
  };
}

const EVIDENCE = {
  available: true,
  timeframe: "1h",
  counts: { swings: 4, patterns: 2, breakouts: 3, failed_breakouts: 1, sweeps: 2, retests: 1 },
  excluded_future_count: 2,
  swings: [
    swingRecord(4, "high", "HH", 62_000),
    swingRecord(9, "low", "HL", 60_100),
    swingRecord(15, "high", "LH", 61_400),
    swingRecord(21, "low", "LL", 58_900),
  ],
  patterns: [
    {
      id: "pattern:db:old",
      pattern_id: "db-1",
      type: "double_bottom",
      label: "DB",
      side: "bottom",
      state: "formed",
      is_latest_state: false,
      known_at: iso(10),
      anchor_time_ms: ms(10),
      neckline: "61200",
      invalidation_level: "60100",
      components: [{ time_ms: ms(9), price: "60100" }, { time_ms: ms(14), price: "60250" }],
    },
    {
      id: "pattern:db:new",
      pattern_id: "db-1",
      type: "double_bottom",
      label: "DB",
      side: "bottom",
      state: "confirmed",
      is_latest_state: true,
      known_at: iso(16),
      anchor_time_ms: ms(16),
      neckline: "61200",
      invalidation_level: "60100",
      components: [{ time_ms: ms(9), price: "60100" }, { time_ms: ms(14), price: "60250" }],
    },
  ],
  breakouts: [
    { id: "bo-1", direction: "bullish", state: "confirmed", time_ms: ms(18), known_at: iso(18), band_low: "61500", band_high: "61600" },
    { id: "bo-0", direction: "bullish", state: "confirmed", time_ms: ms(3), known_at: iso(3), band_low: "60300", band_high: "60400" },
  ],
  failed_breakouts: [
    { id: "fb-1", direction: "bearish", state: "failed", time_ms: ms(12), known_at: iso(12), band_low: "60800", band_high: "60900" },
  ],
  sweeps: [
    { id: "sw-1", direction: "bearish", state: "swept", time_ms: ms(20), known_at: iso(20), band_low: "59000", band_high: "59100" },
  ],
  retests: [
    { id: "rt-1", direction: "bullish", state: "held", time_ms: ms(19), known_at: iso(19), confirmed_at: iso(19), confirmation_candles: 2, band_low: "61500", band_high: "61600" },
  ],
  zones: [
    {
      id: "zone:support:0:60100:60200",
      role: "support",
      band_low: "60100",
      band_high: "60200",
      center: "60150",
      touch_count: 3,
      source_swing_times: [iso(9), iso(14), "2026-10-02T04:00:00Z"],
    },
    {
      id: "zone:resistance:1:62000:62100",
      role: "resistance",
      band_low: "62000",
      band_high: "62100",
      center: "62050",
      touch_count: 1,
      source_swing_times: [iso(4)],
    },
  ],
};

// ---------------------------------------------------------------------------
// The five approved chart functions
// ---------------------------------------------------------------------------

test("the five approved chart functions exist with their own evidence group", () => {
  assert.deepEqual(
    [...CHART_FUNCTION_IDS],
    ["support_resistance", "swings", "patterns", "breakouts", "trade_plan"],
  );
  assert.equal(CHART_FUNCTIONS.length, 5);
  for (const entry of CHART_FUNCTIONS) {
    assert.ok(entry.label && entry.short && entry.hint && entry.empty);
    assert.ok(LAYER_IDS.includes(entry.layer), `${entry.id} owns a real evidence layer`);
  }
  const owned = Object.fromEntries(CHART_FUNCTIONS.map((entry) => [entry.id, entry.layer]));
  assert.deepEqual(owned, {
    support_resistance: "levels",
    swings: "structure",
    patterns: "patterns",
    breakouts: "breakouts",
    trade_plan: "plan",
  });
});

test("every chart function reads only the stored groups it draws", () => {
  assert.deepEqual(evidenceGroupsFor("support_resistance"), [], "bands are drawn as level lines, not markers");
  assert.deepEqual(evidenceGroupsFor("trade_plan"), [], "the plan is drawn as price lines");
  assert.deepEqual(evidenceGroupsFor("swings"), ["swings"]);
  assert.deepEqual(evidenceGroupsFor("patterns"), ["patterns"]);
  assert.deepEqual(evidenceGroupsFor("breakouts"), ["breakouts", "failed_breakouts", "sweeps", "retests"]);
});

test("an unknown or missing function id falls back to the calm default", () => {
  assert.equal(DEFAULT_CHART_FUNCTION, "trade_plan");
  assert.equal(normalizeFunctionId("made_up"), DEFAULT_CHART_FUNCTION);
  assert.equal(normalizeFunctionId(null), DEFAULT_CHART_FUNCTION);
  assert.equal(normalizeFunctionId(undefined), DEFAULT_CHART_FUNCTION);
  assert.equal(normalizeFunctionId("swings"), "swings");
  assert.equal(chartFunctionById("nope"), null);
  assert.equal(DEFAULT_CHART_MODE, SIMPLE_MODE);
  assert.equal(normalizeMode("verbose"), SIMPLE_MODE);
  assert.equal(normalizeMode(DETAILED_MODE), DETAILED_MODE);
  assert.deepEqual([...CHART_MODES].map((mode) => mode.id), ["simple", "detailed"]);
});

// ---------------------------------------------------------------------------
// Simple mode (one function at a time) vs Detailed mode (several)
// ---------------------------------------------------------------------------

test("Simple mode switches every layer off except the selected function's own", () => {
  const layers = layersForSelection({ mode: SIMPLE_MODE, functionId: "swings", layers: DEFAULT_LAYERS });
  for (const layer of LAYER_DEFS) {
    assert.equal(layers[layer.id], layer.id === "structure", `${layer.id} must follow the selection`);
  }
  const patterns = layersForSelection({ mode: SIMPLE_MODE, functionId: "patterns", layers: DEFAULT_LAYERS });
  assert.equal(patterns.patterns, true);
  assert.equal(patterns.structure, false);
});

test("Detailed mode keeps the operator's own switches and forces the selected layer on", () => {
  const stored = { ...DEFAULT_LAYERS, patterns: true, breakouts: true, levels: true, plan: false };
  const layers = layersForSelection({ mode: DETAILED_MODE, functionId: "trade_plan", layers: stored });
  assert.equal(layers.plan, true, "the selected function must be visible in Detailed mode");
  assert.equal(layers.patterns, true, "switches the operator made are kept");
  assert.equal(layers.breakouts, true);
  assert.equal(layers.levels, true);
  assert.equal(layers.htfLevels, false, "a default-off layer the operator never switched on is not added");
  assert.equal(layers.candleSignals, false);
  assert.equal(layers.structure, DEFAULT_LAYERS.structure, "an untouched default keeps its own value");
});

test("liquidity (equal levels) is never derived from a layer and stays off in Simple mode", () => {
  const simple = overlayPrefsForSelection({
    mode: SIMPLE_MODE,
    functionId: "breakouts",
    layers: { ...DEFAULT_LAYERS, liquidity: true },
    overlays: { equalLevels: true },
  });
  assert.equal(simple.equalLevels, false, "one function at a time means no extra overlay in Simple mode");
  const detailed = overlayPrefsForSelection({
    mode: DETAILED_MODE,
    functionId: "breakouts",
    layers: DEFAULT_LAYERS,
    overlays: { equalLevels: true },
  });
  assert.equal(detailed.equalLevels, true, "in Detailed mode it stays the operator's explicit switch");
  const off = overlayPrefsForSelection({ mode: DETAILED_MODE, functionId: "breakouts", layers: DEFAULT_LAYERS, overlays: {} });
  assert.equal(off.equalLevels, false);
});

test("support/resistance draws the zone overlay; the other functions do not", () => {
  const bands = overlayPrefsForSelection({ mode: SIMPLE_MODE, functionId: "support_resistance", layers: DEFAULT_LAYERS });
  assert.equal(bands.zones, true);
  assert.equal(bands.range, true);
  const swings = overlayPrefsForSelection({ mode: SIMPLE_MODE, functionId: "swings", layers: DEFAULT_LAYERS });
  assert.equal(swings.zones, false);
  assert.equal(swings.range, false);
  assert.equal(swings.planLevels, false);
  const plan = overlayPrefsForSelection({ mode: SIMPLE_MODE, functionId: "trade_plan", layers: DEFAULT_LAYERS });
  assert.equal(plan.planLevels, true);
  assert.equal(plan.zones, false, "no plan means no unrelated bands on the chart");
});

// ---------------------------------------------------------------------------
// Simple mode shows only what the function owns
// ---------------------------------------------------------------------------

test("Simple mode empties the groups the selected function does not own", () => {
  const filtered = filterEvidence(EVIDENCE, "swings", { mode: SIMPLE_MODE });
  assert.deepEqual(filtered.patterns, []);
  assert.deepEqual(filtered.breakouts, []);
  assert.deepEqual(filtered.sweeps, []);
  assert.deepEqual(filtered.retests, []);
  assert.deepEqual(filtered.failed_breakouts, []);
  assert.equal(filtered.swings.length, 4, "the function's own records are kept verbatim");
  assert.equal(filtered.swings[0], EVIDENCE.swings[0]);
  assert.equal(filtered.excluded_future_count, 2, "the backend's exclusion count survives");
  assert.equal(EVIDENCE.patterns.length, 2, "the stored payload is never mutated");
});

test("Detailed mode returns the stored payload untouched", () => {
  const filtered = filterEvidence(EVIDENCE, "swings", { mode: DETAILED_MODE });
  assert.equal(filtered, EVIDENCE);
});

test("Chart Patterns draws exactly one detected shape at a time", () => {
  const filtered = filterEvidence(EVIDENCE, "patterns", { mode: SIMPLE_MODE, selectedPatternId: "pattern:db:old" });
  assert.equal(filtered.patterns.length, 1);
  assert.equal(filtered.patterns[0].id, "pattern:db:old");
  assert.equal(filtered.patterns[0].state, "formed", "the backend's own lifecycle state is kept");
  assert.equal(filtered.patterns[0].is_latest_state, true, "the selected record is the one drawn");
  const latest = filterEvidence(EVIDENCE, "patterns", { mode: SIMPLE_MODE });
  assert.equal(latest.patterns.length, 1);
  assert.equal(latest.patterns[0].id, "pattern:db:new", "without a choice the current state is drawn");
});

test("an empty or unavailable selection still draws nothing rather than something invented", () => {
  assert.deepEqual(filterEvidence({ available: false, reason: "no window" }, "swings", { mode: SIMPLE_MODE }).swings, []);
  assert.equal(filterEvidence(null, "swings", { mode: SIMPLE_MODE }), null);
  assert.deepEqual(filterEvidence({ available: true, patterns: [] }, "patterns", { mode: SIMPLE_MODE }).patterns, []);
});

// ---------------------------------------------------------------------------
// Patterns: no invented confidence, backend lifecycle respected
// ---------------------------------------------------------------------------

test("the pattern catalogue carries no confidence figure and keeps the backend's states", () => {
  const catalogue = patternCatalogue(EVIDENCE);
  assert.equal(catalogue.length, 2);
  for (const entry of catalogue) {
    assert.equal(entry.confidence, null, "confidence is never invented");
    assert.equal(entry.label, "DB");
    assert.ok(["formed", "confirmed", "invalidated"].includes(entry.state));
    assert.equal(entry.components, undefined, "the raw component list is not re-exposed as a score");
  }
  assert.equal(catalogue[0].id, "pattern:db:new", "newest known state first");
  assert.equal(catalogue[1].id, "pattern:db:old");
  assert.equal(catalogue[0].componentCount, 2);
});

test("the highlighted pattern is the operator's choice when it exists, else the current state", () => {
  assert.equal(highlightedPattern(EVIDENCE, "pattern:db:old").id, "pattern:db:old");
  assert.equal(highlightedPattern(EVIDENCE, "pattern:gone").id, "pattern:db:new");
  assert.equal(highlightedPattern(EVIDENCE, null).id, "pattern:db:new");
  assert.equal(highlightedPattern({ available: true, patterns: [] }, "pattern:db:new"), null);
  assert.equal(highlightedPattern(null, null), null);
});

test("no chart function hint promises a probability or a percentage", () => {
  for (const entry of CHART_FUNCTIONS) {
    assert.doesNotMatch(functionHint(entry.id), /\d\s?%|probability|confidence|likely|expect/i, entry.id);
  }
});

// ---------------------------------------------------------------------------
// Swings: major/minor from stored references only
// ---------------------------------------------------------------------------

test("a swing referenced by a stored zone is major, everything else is minor context", () => {
  const zone = EVIDENCE.zones[0];
  const referenced = EVIDENCE.swings.find((swing) => swing.time_ms === ms(9));
  const unrelated = EVIDENCE.swings.find((swing) => swing.time_ms === ms(15));
  assert.equal(swingEmphasis(referenced, { zones: [zone] }), "major");
  assert.equal(swingEmphasis(unrelated, { zones: [zone] }), "minor");
  assert.equal(swingEmphasis(unrelated, { zones: [], patterns: [] }), "minor");
  assert.equal(swingEmphasis(null, { zones: [zone] }), "minor");
  assert.equal(swingEmphasis({}, { zones: [zone] }), "minor");
});

test("a swing used as a pattern component is major, and stored times may be seconds or ISO", () => {
  const component = { time_ms: ms(14) };
  const swing = { time_ms: ms(14) };
  assert.equal(swingEmphasis(swing, { patterns: [{ components: [component] }] }), "major");
  assert.equal(swingEmphasis({ time_ms: ms(14) }, { patterns: [{ components: [{ time_ms: Math.floor(ms(14) / 1000) }] }] }), "major");
  assert.equal(swingEmphasis({ time_ms: ms(14) }, { patterns: [{ components: [{ time_ms: iso(14) }] }] }), "major");
  assert.equal(swingEmphasis({ time_ms: ms(14) }, { patterns: [{ components: [{ time_ms: "not a time" }] }] }), "minor");
  assert.equal(swingEmphasis({ time_ms: ms(14) }, { patterns: [{ components: [{ time_ms: ms(15) }] }] }), "minor", "a different candle is not the same reference");
});

test("the swing catalogue labels every stored swing and marks the referenced ones", () => {
  const catalogue = swingCatalogue(EVIDENCE, { zones: EVIDENCE.zones, patterns: EVIDENCE.patterns });
  assert.equal(catalogue.length, 4);
  assert.deepEqual(catalogue.map((entry) => entry.label), ["HH", "HL", "LH", "LL"]);
  // HH is a source swing of the stored resistance zone and HL of the stored support
  // zone (and of both pattern components); the other two are unreferenced context.
  assert.deepEqual(catalogue.map((entry) => entry.emphasis), ["major", "major", "minor", "minor"]);
  assert.equal(catalogue[0].price, "62000", "the stored price is passed through");
  assert.equal(catalogue[0].timeMs, ms(4));
});

test("swing connectors use the stored swing prices, split by kind", () => {
  const connectors = new Map(swingConnectors(EVIDENCE).entries());
  assert.deepEqual(connectors.get("high").map((point) => point.time), [Math.floor(ms(4) / 1000), Math.floor(ms(15) / 1000)]);
  assert.deepEqual(connectors.get("high").map((point) => point.value), [62000, 61400]);
  assert.deepEqual(connectors.get("low").map((point) => point.time), [Math.floor(ms(9) / 1000), Math.floor(ms(21) / 1000)]);
  const majors = new Map(swingConnectors(EVIDENCE, { zones: EVIDENCE.zones }, { majorOnly: true }).entries());
  assert.deepEqual(majors.get("high").map((point) => point.value), [62000], "only the referenced highs in major-only mode");
  assert.deepEqual(majors.get("low").map((point) => point.value), [60100]);
});

// ---------------------------------------------------------------------------
// Breakouts / retests and levels
// ---------------------------------------------------------------------------

test("the event catalogue merges the four stored groups, newest first, with the backend's states", () => {
  const rows = eventCatalogue(EVIDENCE);
  assert.ok(rows.length >= 5);
  const times = rows.map((row) => row.timeMs);
  assert.deepEqual([...times].sort((left, right) => right - left), times, "newest first");
  const sweep = rows.find((row) => row.id === "sw-1");
  assert.equal(sweep.kind, "sweep");
  assert.equal(sweep.state, "swept");
  assert.equal(sweep.direction, "bearish");
  const failed = rows.find((row) => row.id === "fb-1");
  assert.equal(failed.kind, "failed_breakout");
  const retest = rows.find((row) => row.id === "rt-1");
  assert.equal(retest.kind, "retest");
  assert.equal(retest.confirmedAt, iso(19));
  assert.equal(retest.confirmationCandles, 2);
  assert.equal(retest.bandLow, "61500");
  assert.equal(rows.some((row) => row.kind === "continuation"), false, "no continuation is invented");
  assert.deepEqual(eventCatalogue(null), []);
});

test("the level catalogue keeps the detector's role, bounds and touch counts", () => {
  const bands = levelCatalogue([
    { id: "b1", display_role: "support", position: "below_price", band_low: "60100", band_high: "60200", center: "60150", touch_count: 3, faded: false, merged_zone_count: 2 },
    { id: "b2", band_low: "62000", band_high: "62100", touch_count: null },
  ]);
  assert.equal(bands[0].role, "support");
  assert.equal(bands[0].low, "60100");
  assert.equal(bands[0].touchCount, 3);
  assert.equal(bands[0].mergedZoneCount, 2);
  assert.equal(bands[1].role, "price_inside", "a band with no role falls back to the detector's position group");
  assert.equal(bands[1].touchCount, null, "an unknown touch count stays unknown");
  assert.deepEqual(levelCatalogue(null), []);
});

// ---------------------------------------------------------------------------
// Honest status lines
// ---------------------------------------------------------------------------

test("a trade plan is announced only when the engine supplied a plannable one", () => {
  const planned = functionStatus({ functionId: "trade_plan", plan: { state: "PLANNABLE" } });
  assert.equal(planned.available, true);
  assert.match(planned.text, /as calculated by the deterministic planner/);
  for (const state of ["NO_PLAN", "WAITING", null, undefined]) {
    const status = functionStatus({ functionId: "trade_plan", plan: state ? { state } : null, planState: state || null });
    assert.equal(status.available, false, `${state} must not look actionable`);
    assert.match(status.text, /No actionable plan right now/);
  }
});

test("support/resistance reports the same counts the backend presented", () => {
  const status = functionStatus({
    functionId: "support_resistance",
    extra: { bands: levelCatalogue([{ id: "b1", band_low: "1", band_high: "2" }]), hiddenCount: 4, mergedCount: 2 },
  });
  assert.equal(status.available, true);
  assert.match(status.text, /1 band\(s\) shown nearest price first · 4 further band\(s\) hidden · 2 merged for display/);
  const none = functionStatus({ functionId: "support_resistance", extra: { bands: [], reason: "no_zones" } });
  assert.equal(none.available, false);
  assert.match(none.text, /No support or resistance zones are stored/);
});

test("an unavailable evidence payload is reported with the backend's reason", () => {
  const status = functionStatus({
    functionId: "swings",
    evidence: { available: false, reason: "chart evidence older than the window" },
  });
  assert.equal(status.available, false);
  assert.match(status.text, /Chart evidence unavailable — chart evidence older than the window\./);
  assert.match(status.text, /Nothing is drawn in its place\./);
  const noReason = functionStatus({ functionId: "swings", evidence: { available: false } });
  assert.match(noReason.text, /the backend did not return chart evidence for this view/);
});

test("status lines count stored records and never invent a number", () => {
  const model = { counts: { structure: 4, patterns: 1, breakouts: 3 }, hiddenFuture: 0, hiddenOlder: { breakouts: 5 } };
  const swings = functionStatus({ functionId: "swings", evidence: EVIDENCE, model });
  assert.match(swings.text, /4 stored confirmed swing\(s\) · 4 drawn in the loaded window/);
  assert.match(swings.text, /2 later-known item\(s\) excluded at this instant/);
  const patterns = functionStatus({ functionId: "patterns", evidence: EVIDENCE, model });
  assert.match(patterns.text, /2 stored pattern state\(s\) · 1 drawn/);
  const breakouts = functionStatus({ functionId: "breakouts", evidence: EVIDENCE, model });
  assert.match(breakouts.text, /3 breakout\(s\) · 1 failed · 2 sweep\(s\) · 1 retest\(s\)/);
  assert.match(breakouts.text, /5 older item\(s\) outside the recent window/);
});

test("a function with nothing stored says so instead of showing a fabricated figure", () => {
  const empty = functionStatus({
    functionId: "breakouts",
    evidence: { available: true, counts: { breakouts: 0, sweeps: 0, retests: 0, failed_breakouts: 0 } },
    model: { counts: { breakouts: 0 }, hiddenFuture: 0, hiddenOlder: {} },
  });
  assert.equal(empty.available, false);
  assert.match(empty.text, /No breakout, sweep or retest is stored/);
  const unavailable = functionStatus({ functionId: "patterns", evidence: null });
  assert.equal(unavailable.available, false);
  assert.match(unavailable.text, /Chart evidence unavailable/);
});
