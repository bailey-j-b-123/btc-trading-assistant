import assert from "node:assert/strict";
import test from "node:test";

import { functionPanelModel } from "../../src/trading_assistant/web/static/js/function_panel.js";

/**
 * The panel beside the chart, one per approved chart-function design. These
 * tests pin the honesty rules: stored values pass through, nothing is invented,
 * no confidence score exists, and a step the backend never recorded is never
 * drawn as if it had happened.
 */

const HOUR = 3_600_000;
const T0 = Date.parse("2026-10-06T00:00:00Z");
const ms = (index) => T0 + index * HOUR;
const iso = (index) => new Date(ms(index)).toISOString().replace(".000Z", "Z");

const BANDS = [
  {
    id: "band-support",
    display_role: "support",
    position: "below_price",
    band_low: "61900",
    band_high: "62000",
    center: "61950",
    touch_count: 3,
    source_timeframe: "1h",
    faded: false,
    merged_zone_count: 2,
  },
  {
    id: "band-resistance",
    display_role: "resistance",
    position: "above_price",
    band_low: "62400",
    band_high: "62500",
    touch_count: null,
  },
];

const EVIDENCE = {
  available: true,
  timeframe: "1h",
  counts: { swings: 2, patterns: 2, breakouts: 1, failed_breakouts: 0, sweeps: 1, retests: 1 },
  excluded_future_count: 0,
  swings: [
    { id: "swing-hh", kind: "high", label: "HH", price: "62100", time_ms: ms(4), known_at: iso(4) },
    { id: "swing-hl", kind: "low", label: "HL", price: "61500", time_ms: ms(9), known_at: iso(9) },
  ],
  patterns: [
    {
      id: "pattern-db",
      type: "double_bottom",
      label: "DB",
      side: "bottom",
      state: "confirmed",
      is_latest_state: true,
      known_at: iso(10),
      anchor_time: iso(10),
      anchor_time_ms: ms(10),
      neckline: "62200",
      invalidation_level: "61500",
      components: [{ time_ms: ms(9), price: "61500" }],
      geometry: {
        matching_extremes_difference_pct: "0.42",
        depth_pct: "3.10",
        span_candles: 18,
      },
    },
    {
      id: "pattern-hns",
      type: "head_and_shoulders",
      label: "H&S",
      side: "top",
      state: "formed",
      is_latest_state: true,
      known_at: iso(12),
      anchor_time: iso(12),
      components: [],
    },
  ],
  breakouts: [
    { id: "bo-1", direction: "bullish", state: "confirmed", time_ms: ms(11), known_at: iso(11), band_low: "61900", band_high: "62000", confirmation_candles: 2 },
  ],
  failed_breakouts: [],
  sweeps: [
    { id: "sw-1", direction: "bearish", state: "swept", time_ms: ms(6), known_at: iso(6), band_low: "61000", band_high: "61100" },
  ],
  retests: [
    { id: "rt-1", direction: "bullish", state: "held", time_ms: ms(13), known_at: iso(13), band_low: "61900", band_high: "62000" },
  ],
  zones: [
    { id: "zone-support", source_swing_times: [iso(9)] },
  ],
};

const MARKET_STATE = {
  available: true,
  trend: { direction: "bullish", reason: "higher_highs_and_higher_lows" },
};

const PLAN = {
  state: "PLANNABLE",
  direction: "bullish",
  entry: { value: "62100" },
  stop: { value: "61900" },
  invalidation: { value: "61900" },
  risk_per_unit: "200",
  targets: [
    { level: { value: "62500" }, r_multiple: "2" },
    { level: { value: "62900" }, r_multiple: "4" },
  ],
};

// ---------------------------------------------------------------------------
// Support / Resistance
// ---------------------------------------------------------------------------

test("the support/resistance panel lists the stored bands with their own bounds and touches", () => {
  const model = functionPanelModel({
    functionId: "support_resistance",
    bands: BANDS,
    hidden: 4,
    merged: 2,
  });
  assert.equal(model.heading, "Support & Resistance");
  assert.deepEqual(model.legend.map((row) => row.label), ["Resistance", "Range mid", "Support"]);
  assert.deepEqual(model.facts.map((row) => row.value), ["2", "4", "2"]);
  assert.equal(model.list[0].title, "Support");
  assert.equal(model.list[0].value, "61,900.00 – 62,000.00");
  assert.match(model.list[0].meta, /1H zone · 3 touches · 2 merged/);
  assert.equal(model.list[1].meta, "touches unknown", "an absent touch count is never shown as zero");
  assert.match(model.note, /nothing is invented, moved or re-labelled/);
});

test("the support/resistance panel has an honest empty state", () => {
  const none = functionPanelModel({ functionId: "support_resistance", bands: [], reason: "no_zones" });
  assert.equal(none.list.length, 0);
  assert.match(none.note, /No support or resistance zones are stored/);
  const unknown = functionPanelModel({ functionId: "support_resistance", bands: [] });
  assert.match(unknown.note, /No zone bands are available/);
});

// ---------------------------------------------------------------------------
// Swings
// ---------------------------------------------------------------------------

test("the swings panel reports the last stored swing, the bias and the levels around price", () => {
  const model = functionPanelModel({
    functionId: "swings",
    evidence: EVIDENCE,
    bands: BANDS,
    marketState: MARKET_STATE,
  });
  assert.equal(model.heading, "Market Structure");
  assert.equal(model.chip, "Bullish");
  assert.deepEqual(model.legend.map((row) => row.label), ["HH", "HL", "LH", "LL"]);
  assert.equal(model.facts[0].value, "HL · 61,500.00", "the newest stored swing, with its own label and price");
  assert.match(model.facts[1].value, /^Bullish · /);
  assert.equal(model.facts[2].value, "61,900.00 – 62,000.00", "the nearest stored support band");
  assert.equal(model.facts[3].value, "62,400.00 – 62,500.00");
  assert.match(model.note, /Only confirmed swings are marked/);
});

test("the swings panel marks a referenced swing and never calls it a strength score", () => {
  const model = functionPanelModel({ functionId: "swings", evidence: EVIDENCE, bands: BANDS, marketState: MARKET_STATE });
  const referenced = model.list.find((row) => /HL/.test(row.title));
  const independent = model.list.find((row) => /HH/.test(row.title));
  assert.match(referenced.meta, /referenced by a stored zone or pattern/);
  assert.equal(independent.meta, "independent swing");
  assert.doesNotMatch(model.note, /stronger|strength score is|confidence/i);
  assert.deepEqual(model.list.map((row) => row.title.split(" · ")[0]), ["HL", "HH"], "newest stored swing first");
});

test("the swings panel says nothing rather than guessing when there is no evidence", () => {
  const model = functionPanelModel({ functionId: "swings", evidence: { available: false, reason: "no window" }, bands: [] });
  assert.equal(model.facts[0].value, "UNKNOWN");
  assert.deepEqual(model.list, []);
});

// ---------------------------------------------------------------------------
// Patterns
// ---------------------------------------------------------------------------

test("the patterns panel shows the detected shape, its state and its own geometry", () => {
  const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, bands: BANDS, selectedPatternId: "pattern-db" });
  assert.equal(model.heading, "Chart Patterns");
  assert.equal(model.title.label, "Double bottom", "the full detected name, not just the abbreviation");
  assert.equal(model.title.state, "confirmed");
  assert.deepEqual(model.facts.map((row) => row.label), ["State", "Known at", "Neckline", "Invalidation level", "Detected points"]);
  assert.equal(model.facts[2].value, "62,200.00");
  assert.equal(model.facts[4].value, "1");
  assert.deepEqual(model.geometry.map((row) => row.value), ["0.42%", "3.10%", "18 candles"]);
  assert.match(model.note, /no confidence percentage is shown/);
});

test("without a selection the patterns panel draws the newest current state", () => {
  const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, bands: BANDS });
  assert.equal(model.title.label, "Head and shoulders", "the most recently known current state");
  assert.equal(model.list[0].title, "Double bottom");
});

test("no panel anywhere exposes a confidence, probability or percentage score", () => {
  const panels = [
    functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, bands: BANDS, selectedPatternId: "pattern-db" }),
    functionPanelModel({ functionId: "swings", evidence: EVIDENCE, bands: BANDS, marketState: MARKET_STATE }),
    functionPanelModel({ functionId: "breakouts", evidence: EVIDENCE }),
    functionPanelModel({ functionId: "support_resistance", bands: BANDS }),
    functionPanelModel({ functionId: "trade_plan", plan: PLAN }),
  ];
  for (const panel of panels) {
    // The detector's own geometry percentages are measurements and are allowed,
    // and the explanatory note is where the panel says it shows no score. What
    // must never carry a score is the data itself.
    const { geometry, note, ...rest } = panel;
    const text = JSON.stringify(rest);
    assert.doesNotMatch(text, /confidence/i, panel.heading);
    assert.doesNotMatch(text, /probability|expected|win rate|accuracy/i, panel.heading);
    assert.doesNotMatch(text, /"\d+(\.\d+)?%"/, `${panel.heading} must not show a percentage score`);
  }
});

test("the patterns panel lists the other stored states and never re-labels them", () => {
  const model = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, bands: BANDS, selectedPatternId: "pattern-db" });
  assert.equal(model.list.length, 1);
  assert.equal(model.list[0].title, "Head and shoulders");
  assert.equal(model.list[0].value, "formed", "the backend's own lifecycle word");
  assert.match(model.list[0].meta, /current state/);
  const selected = functionPanelModel({ functionId: "patterns", evidence: EVIDENCE, bands: BANDS, selectedPatternId: "pattern-hns" });
  assert.equal(selected.title.state, "formed");
  assert.equal(selected.list[0].value, "confirmed");
});

test("an unavailable or empty pattern window keeps the empty sentence", () => {
  const model = functionPanelModel({ functionId: "patterns", evidence: { available: true, patterns: [] }, bands: BANDS });
  assert.equal(model.title, undefined);
  assert.match(model.note, /No chart pattern is stored for this window/);
});

// ---------------------------------------------------------------------------
// Breakouts / retests
// ---------------------------------------------------------------------------

test("the breakouts panel lists only recorded events, newest first, with their own states", () => {
  const model = functionPanelModel({ functionId: "breakouts", evidence: EVIDENCE });
  assert.equal(model.heading, "Breakouts & Retests");
  assert.equal(model.chip, "3 stored events");
  assert.deepEqual(model.list.map((row) => row.value), ["held", "confirmed", "swept"]);
  assert.match(model.list[0].title, /^Retest · Bullish/);
  assert.match(model.list[0].meta, /band 61,900\.00 – 62,000\.00/);
  assert.match(model.list[1].meta, /2 confirmation candles/);
  assert.match(model.note, /A continuation is drawn only when the backend records one/);
  assert.equal(model.list.some((row) => /continuation/i.test(row.title)), false, "no continuation is invented");
});

test("the breakouts panel says so when nothing is stored or evidence is missing", () => {
  const empty = functionPanelModel({
    functionId: "breakouts",
    evidence: { available: true, breakouts: [], failed_breakouts: [], sweeps: [], retests: [] },
  });
  assert.deepEqual(empty.list, []);
  assert.match(empty.note, /No breakout, sweep or retest is stored/);
  const unavailable = functionPanelModel({ functionId: "breakouts", evidence: { available: false, reason: "no window" } });
  assert.match(unavailable.note, /nothing is drawn in its place/);
});

// ---------------------------------------------------------------------------
// Trade plan
// ---------------------------------------------------------------------------

test("the trade plan panel keeps the absolute prices in their own fields", () => {
  const model = functionPanelModel({ functionId: "trade_plan", plan: PLAN });
  assert.deepEqual(model.facts.map((row) => row.label), ["Direction", "Entry", "Stop", "Invalidation", "Target 1", "Target 2"]);
  assert.equal(model.facts[1].value, "62,100.00", "entry price");
  assert.equal(model.facts[2].value, "61,900.00", "stop price");
  assert.equal(model.facts[3].value, "61,900.00", "invalidation price");
  assert.equal(model.facts[4].value, "62,500.00", "target price");
  assert.equal(model.facts[5].value, "62,900.00", "final target price");
  assert.equal(model.facts[5].meta, "4.00R from entry", "the plan's own R multiple");
});

test("the risk/reward panel shows entry-to-stop and entry-to-target distances, not prices", () => {
  const model = functionPanelModel({ functionId: "trade_plan", plan: PLAN });
  // Entry 62,100 · stop 61,900 → risk 200 · final target 62,900 → reward 800.
  assert.equal(model.rr.risk, "200.00", "the risk amount is the entry-to-stop distance");
  assert.equal(model.rr.reward, "800.00", "the reward amount is the entry-to-target distance");
  assert.notEqual(model.rr.risk, model.facts[2].value, "the panel never labels a stop price as the risk amount");
  assert.notEqual(model.rr.reward, model.facts[5].value, "nor a target price as the reward amount");
  assert.equal(model.rr.targetLabel, "T2", "the reward is measured to the final target the plan carries");
  assert.equal(model.rr.ratio, "4.00 : 1", "the ratio the plan itself states");
  assert.equal(model.rr.riskShare, 200 / 1000, "the bar splits by the two distances");
  assert.match(model.note, /entry-to-stop and entry-to-target distances/);
});

test("a short-side plan measures its distances from entry as well", () => {
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: {
      state: "PLANNABLE",
      direction: "bearish",
      entry: { value: "100" },
      stop: { value: "110" },
      invalidation: { value: "110" },
      targets: [{ level: { value: "80" } }],
    },
  });
  assert.equal(model.rr.risk, "10.00", "the stop sits 10 above entry, so the risk distance is 10");
  assert.equal(model.rr.reward, "20.00", "the target sits 20 below entry, so the reward distance is 20");
  assert.equal(model.rr.ratio, "2.00 : 1", "the ratio of the two distances when the plan states no multiple");
  assert.equal(model.rr.targetLabel, "T1");
  assert.equal(model.facts[0].value.startsWith("Bearish"), true);
});

test("no plan means no levels and no ratio, only the engine's reason", () => {
  const model = functionPanelModel({ functionId: "trade_plan", plan: null, planState: "PLANNING_REFUSED", planReason: "The confirmation event is missing." });
  assert.deepEqual(model.facts, []);
  assert.equal(Boolean(model.rr), false);
  assert.equal(model.chip, "PLANNING_REFUSED");
  assert.equal(model.note, "The confirmation event is missing.");
  const noReason = functionPanelModel({ functionId: "trade_plan", plan: { state: "WAITING" } });
  assert.match(noReason.note, /No actionable plan right now/);
});

test("missing plan fields stay explicit UNKNOWN values", () => {
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: null, entry: { value: null }, stop: null, invalidation: { value: null }, targets: [] },
  });
  assert.equal(model.facts[1].value, "UNKNOWN");
  assert.equal(model.facts[2].value, "UNKNOWN");
  assert.equal(model.facts[3].value, "UNKNOWN", "an absent invalidation value is UNKNOWN, never a filled-in level");
  assert.equal(Boolean(model.rr), false, "no ratio or distance is shown when the levels are not there to measure one");
  const values = model.facts.map((row) => row.value);
  assert.equal(values.length, 4, "direction, entry, stop and invalidation are still listed");
  for (const value of values) assert.match(value, /^UNKNOWN/, "no field is filled in: every one is explicit UNKNOWN");
  assert.equal(model.chip, "PLANNABLE", "the plan state itself is still reported");
  assert.match(model.note, /Hypothetical paper levels/, "a plannable plan with unreadable levels still says what it is");
});

test("a plan whose targets carry no price shows no reward distance", () => {
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "90" }, targets: [{ level: { value: null } }] },
  });
  assert.equal(Boolean(model.rr), false, "no reward distance can be measured without a target price");
  assert.equal(model.facts[3].value, "UNKNOWN", "the target field itself stays explicit");
  const zeroRisk = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "100" }, targets: [{ level: { value: "120" } }] },
  });
  assert.equal(Boolean(zeroRisk.rr), false, "a zero risk distance cannot produce a ratio");
});

test("an unknown or missing chart function falls back to the calm default panel", () => {
  assert.equal(functionPanelModel({ functionId: "made_up" }).id, "trade_plan");
  assert.equal(functionPanelModel({}).id, "trade_plan");
  assert.equal(functionPanelModel().id, "trade_plan");
});
