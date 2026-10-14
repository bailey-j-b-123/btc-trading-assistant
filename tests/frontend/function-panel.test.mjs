import assert from "node:assert/strict";
import test from "node:test";

import { breakoutStages } from "../../src/trading_assistant/web/static/js/breakout_stages.js";
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

test("the swings panel prefers viewed timeframe trend from evidence over engine marketState", () => {
  const evidenceWith15mTrend = {
    ...EVIDENCE,
    trend: {
      direction: "bearish",
      reason: "lower_highs_and_lower_lows",
      sufficient: true,
      higher_highs: false,
      higher_lows: false,
      lower_highs: true,
      lower_lows: true,
    },
  };
  // marketState has 1h bullish trend, but evidence has 15m bearish trend
  const model = functionPanelModel({
    functionId: "swings",
    evidence: evidenceWith15mTrend,
    bands: BANDS,
    marketState: MARKET_STATE,
  });
  assert.equal(model.chip, "Bearish", "panel chip must reflect viewed timeframe trend");
  assert.match(model.facts[1].value, /^Bearish · lower highs and lower lows/);
  assert.equal(model.facts[1].tone, "red");
});

test("the swings panel falls back to marketState trend when evidence has no trend", () => {
  const evidenceWithoutTrend = { ...EVIDENCE, trend: null };
  const model = functionPanelModel({
    functionId: "swings",
    evidence: evidenceWithoutTrend,
    bands: BANDS,
    marketState: MARKET_STATE,
  });
  assert.equal(model.chip, "Bullish", "falls back to marketState when evidence has no trend");
  assert.match(model.facts[1].value, /^Bullish · /);
});

test("the swings panel displays UNKNOWN bias when neither evidence nor marketState has a trend", () => {
  const model = functionPanelModel({
    functionId: "swings",
    evidence: { ...EVIDENCE, trend: null },
    bands: BANDS,
    marketState: null,
  });
  assert.equal(model.chip, null);
  assert.equal(model.facts[1].value, "UNKNOWN");
  assert.equal(model.facts[1].tone, "neutral");
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

test("the Breakout Logic panel shows the three steps with the status the backend recorded", () => {
  const candles = Array.from({ length: 20 }, (_, index) => [ms(index), "62000", "62100", "61900", "62050", "10"]);
  const model = functionPanelModel({
    functionId: "breakouts",
    evidence: EVIDENCE,
    stages: breakoutStages({ evidence: EVIDENCE, candles }),
  });
  assert.equal(model.heading, "Breakout Logic");
  assert.equal(model.chip, "Bullish sequence");
  assert.deepEqual(model.stages.map((stage) => [stage.number, stage.title]), [[1, "Breakout"], [2, "Retest"], [3, "Continuation"]]);
  assert.deepEqual(model.stages.map((stage) => stage.status), ["Completed", "Held", "In Progress"]);
  // The band is the stored breakout's own reference band, passed through.
  assert.equal(model.band.value, "61,900.00 – 62,000.00");
  // Step 2 is the stored retest of that breakout, at its own candle.
  assert.equal(model.stages[1].barIndex, 13);
  // Continuation is named but never claimed complete: the backend stores none.
  assert.equal(model.stages[2].status, "In Progress");
  assert.equal(model.stages[2].tone, "amber");
  // No continuation is stored, so the step is flagged unrecorded: the panel
  // draws it as an outline circle and the chart draws a dashed leader line, so
  // a step that is only being watched never reads as a confirmed event.
  assert.equal(model.stages[2].recorded, false, "no continuation record exists");
  assert.equal(model.stages[0].recorded, true, "the breakout is stored");
  // One short sentence per step, and a Key Idea under them.
  assert.deepEqual(model.stages.map((stage) => typeof stage.summary === "string" && stage.summary.length > 0), [true, true, true]);
  assert.match(model.keyIdea, /now support\. The move on from the retest is what is being watched/);
  // Each step carries where its callout sits relative to its candle.
  assert.deepEqual(model.stages.map((stage) => `${stage.place}/${stage.nudge}`), ["above/none", "below/right", "above/none"]);
  assert.match(model.note, /No step is marked complete without a stored record/);
});

test("the Breakout Logic panel keeps its three steps when nothing is stored", () => {
  const emptyEvidence = { available: true, breakouts: [], failed_breakouts: [], sweeps: [], retests: [] };
  const empty = functionPanelModel({
    functionId: "breakouts",
    evidence: emptyEvidence,
    stages: breakoutStages({ evidence: emptyEvidence, candles: [] }),
  });
  // Same layout, same three steps — only the statuses change.
  assert.deepEqual(empty.stages.map((stage) => stage.title), ["Breakout", "Retest", "Continuation"]);
  assert.deepEqual(empty.stages.map((stage) => stage.status), ["Not recorded", "Awaiting Confirmation", "Awaiting Confirmation"]);
  assert.equal(empty.band, null, "no band is shown when nothing is stored");
  assert.match(empty.note, /no breakout is stored/);

  const unavailableEvidence = { available: false, reason: "no window" };
  const unavailable = functionPanelModel({
    functionId: "breakouts",
    evidence: unavailableEvidence,
    stages: breakoutStages({ evidence: unavailableEvidence, candles: [] }),
  });
  assert.deepEqual(unavailable.stages.map((stage) => stage.status), ["Not recorded", "Not recorded", "Not recorded"]);
  assert.match(unavailable.note, /no window/);
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
  // No distance can be measured, so each one says UNKNOWN rather than disappearing or
  // defaulting to zero. The block still appears, which is how a reader can tell that the
  // plan is being shown but its risk cannot be measured.
  assert.equal(model.rr.risk, "UNKNOWN");
  assert.equal(model.rr.reward, "UNKNOWN");
  assert.equal(model.rr.ratio, "UNKNOWN");
  assert.equal(model.rr.ratioBasis, null);
  assert.equal(model.rr.riskShare, null, "no bar is drawn without two distances");
  const values = model.facts.map((row) => row.value);
  assert.equal(values.length, 4, "direction, entry, stop and invalidation are still listed");
  for (const value of values) assert.match(value, /^UNKNOWN/, "no field is filled in: every one is explicit UNKNOWN");
  assert.equal(model.chip, "PLANNABLE", "the plan state itself is still reported");
  assert.match(model.note, /Hypothetical paper levels/, "a plannable plan with unreadable levels still says what it is");
});

test("a plan whose targets carry no price shows the risk distance and an UNKNOWN reward", () => {
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "90" }, targets: [{ level: { value: null } }] },
  });
  assert.equal(model.facts[3].value, "UNKNOWN", "the target field itself stays explicit");
  assert.equal(model.rr.risk, "10.00", "the risk distance is still measurable from entry and stop");
  assert.equal(model.rr.reward, "UNKNOWN", "no reward distance can be measured without a target price");
  assert.equal(model.rr.ratio, "UNKNOWN");
  assert.equal(model.rr.riskShare, null);

  const zeroRisk = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "100" }, targets: [{ level: { value: "120" } }] },
  });
  assert.equal(zeroRisk.rr.risk, "0.00", "an entry equal to the stop is a real zero distance");
  assert.equal(zeroRisk.rr.ratio, "UNKNOWN", "a zero risk distance cannot produce a ratio");
  assert.equal(zeroRisk.rr.reward, "20.00");
});

// --- r_multiple: null must never become zero --------------------------------------------

test("a null r_multiple never becomes a 0.00 ratio", () => {
  // Number(null) is 0, so the old code read an absent r_multiple as a stated ratio of zero
  // and printed "R:R 0.00 : 1". A ratio of zero risk to reward is not what the plan says.
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: {
      state: "PLANNABLE",
      direction: "bullish",
      entry: { value: "100" },
      stop: { value: "100" },
      targets: [{ level: { value: "120" }, r_multiple: null }],
    },
  });
  assert.notEqual(model.rr.ratio, "0.00 : 1", "a missing r_multiple is not a zero ratio");
  assert.equal(model.rr.ratio, "UNKNOWN");
  assert.equal(model.rr.ratioBasis, null);
});

test("the ratio comes from the plan's levels when both distances are measurable", () => {
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "90" }, targets: [{ level: { value: "130" } }] },
  });
  assert.equal(model.rr.risk, "10.00");
  assert.equal(model.rr.reward, "30.00");
  assert.equal(model.rr.ratio, "3.00 : 1");
  assert.equal(model.rr.ratioBasis, "levels", "the ratio is the reward distance over the risk distance");
  assert.equal(model.rr.riskShare, 0.25);
});

test("the plan's own R multiple is used, and marked, when the distances cannot be measured", () => {
  // No stop, so no risk distance: the plan's stated R multiple is all that is left.
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: null, targets: [{ level: { value: "130" }, r_multiple: "2.5" }] },
  });
  assert.equal(model.rr.risk, "UNKNOWN");
  assert.equal(model.rr.ratio, "2.50 : 1");
  assert.equal(model.rr.ratioBasis, "stated");
});

test("the levels win over a stated R multiple, so the ratio always matches the distances", () => {
  // A plan whose stated R multiple disagrees with its own levels. The displayed ratio must
  // agree with the two distances beside it, or it reads as a contradiction on one row.
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: { value: "90" }, targets: [{ level: { value: "130" }, r_multiple: "9.9" }] },
  });
  assert.equal(model.rr.ratio, "3.00 : 1");
  assert.equal(model.rr.ratioBasis, "levels");
});

test("a missing r_multiple on the final target is not replaced by an earlier target's", () => {
  // The reward distance shown is the final target's, so its ratio must be the final
  // target's too. Borrowing T1's R multiple would describe a different reward.
  const model = functionPanelModel({
    functionId: "trade_plan",
    plan: {
      state: "PLANNABLE",
      direction: "bullish",
      entry: { value: "100" },
      stop: null,
      targets: [{ level: { value: "120" }, r_multiple: "2" }, { level: { value: "130" }, r_multiple: null }],
    },
  });
  assert.equal(model.rr.ratio, "UNKNOWN");
  assert.equal(model.rr.ratioBasis, null);
  // facts: direction, entry, stop, invalidation, then one per target.
  assert.equal(model.facts[4].meta, "2.00R from entry", "each target still reports its own R when it has one");
  assert.equal(model.facts[5].meta, null, "a target without an R multiple reports none, not 0.00R");
});

test("a non-positive or unparseable r_multiple is not a ratio", () => {
  for (const rMultiple of [0, "-1", "abc", "", null, undefined, NaN]) {
    const model = functionPanelModel({
      functionId: "trade_plan",
      plan: { state: "PLANNABLE", direction: "bullish", entry: { value: "100" }, stop: null, targets: [{ level: { value: "NULL" }, r_multiple: rMultiple }] },
    });
    assert.equal(model.rr.ratio, "UNKNOWN", `r_multiple ${String(rMultiple)} must not render as a ratio`);
  }
});

test("an unknown or missing chart function falls back to the calm default panel", () => {
  assert.equal(functionPanelModel({ functionId: "made_up" }).id, "trade_plan");
  assert.equal(functionPanelModel({}).id, "trade_plan");
  assert.equal(functionPanelModel().id, "trade_plan");
});
