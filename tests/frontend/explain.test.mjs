import assert from "node:assert/strict";
import test from "node:test";

import { explainEvidence, explainPlanLevel, influenceFor } from "../../src/trading_assistant/web/static/js/explain.js";

const HOUR = 3_600_000;
const T0 = Date.parse("2026-10-06T00:00:00Z");
const ms = (index) => T0 + index * HOUR;
const iso = (index) => new Date(ms(index)).toISOString().replace(".000Z", "Z");

function section(explanation, heading) {
  const found = explanation.sections.find((entry) => entry.heading === heading);
  assert.ok(found, `missing section ${heading}`);
  return found.body;
}

function qualification(setups, selected = null) {
  return {
    available: true,
    state: selected ? "QUALIFIED" : "NO_SETUP",
    selected_setup_id: selected,
    snapshot: { setups },
  };
}

test("a swing explanation states exact price, candle time, confirmation instant and label meaning", () => {
  // Shape exactly as chart_evidence._swing_item emits it.
  const item = {
    id: "swing:high:x",
    evidence_kind: "swing",
    kind: "high",
    label: "HH",
    time: iso(9),
    price: "112.50",
    known_at: iso(12),
    confirmed_by: iso(11),
    left_window: 2,
    right_window: 2,
    tie_policy: "strict",
  };
  const explanation = explainEvidence(item, { timeframe: "1h", qualification: qualification([]) });
  assert.equal(explanation.title, "HH · swing high at 112.50");
  assert.equal(explanation.status, "Confirmed");
  assert.match(section(explanation, "What BRAIN detected"), /swing high at 112\.50 on the candle that opened at 2026-10-06 09:00 UTC/);
  assert.match(section(explanation, "What BRAIN detected"), /higher than the previous confirmed swing high/);
  assert.match(section(explanation, "When the evidence became available"), /2026-10-06 11:00 UTC/);
  assert.match(section(explanation, "When the evidence became available"), /first knew it at 2026-10-06 12:00 UTC/);
  assert.match(section(explanation, "When the evidence became available"), /Before that instant it was not structure/);
  assert.match(explanation.timeframe, /1H timeframe/);
});

test("influence is 'No' when no setup is selected, and says why", () => {
  const explanation = explainEvidence(
    { id: "bo-1", kind: "breakout", direction: "bullish", reference_type: "swing_high", band_low: "110", band_high: "111", breakout_close: "112", time: iso(5), known_at: iso(6), penetration_pct: "0.2", confirmation_candles: 1 },
    { timeframe: "1h", qualification: qualification([]) },
  );
  assert.match(section(explanation, "Did it influence the current assessment?"), /^No\. No setup is selected/);
  assert.equal(explanation.influenced, false);
});

test("influence is 'Yes' only when the selected setup's rule cites the same id", () => {
  const selected = {
    id: "setup-1",
    family: "breakout_retest",
    state: "QUALIFIED",
    seed_event_id: "bo-1",
    reference_id: "ref-1",
    rules: [
      { rule_id: "breakout_seed", outcome: "pass", evidence: [{ source_reference: "bo-1", category: "seed" }] },
    ],
  };
  const q = qualification([selected], "setup-1");
  const linked = influenceFor("bo-1", q);
  assert.equal(linked.used, true);
  assert.match(linked.detail, /seed event/);
  const unrelated = influenceFor("sweep-9", q);
  assert.equal(unrelated.used, false);
  assert.match(unrelated.detail, /does not cite this item/);
});

test("rule evidence ids link precisely: a rule citing a different event does not link", () => {
  const selected = {
    id: "setup-2",
    family: "liquidity_reversal",
    state: "WATCH",
    seed_event_id: "other",
    reference_id: "other-ref",
    rules: [{ rule_id: "retest", outcome: "pending", evidence: [{ source_reference: "rt-7" }] }],
  };
  assert.equal(influenceFor("rt-7", qualification([selected], "setup-2")).used, true);
  assert.equal(influenceFor("rt-8", qualification([selected], "setup-2")).used, false);
});

test("unavailable qualification never claims influence", () => {
  assert.equal(influenceFor("bo-1", { available: false }).used, false);
  assert.match(influenceFor("bo-1", null).detail, /unavailable/);
});

test("pattern explanations show each lifecycle state with its own wording and times", () => {
  // Shape exactly as chart_evidence._pattern_item emits it (no kind field).
  const base = {
    id: "p-rec-1",
    evidence_kind: "pattern",
    pattern_id: "p-root",
    type: "double_top",
    label: "Double top",
    side: "top",
    neckline: "99.0",
    invalidation_level: "111.2",
    formed_at: iso(9),
    known_at: iso(12),
    confirmation_time: iso(12),
    components: [
      { time: iso(2), kind: "high", price: "111.0", known_at: iso(5) },
      { time: iso(4), kind: "low", price: "99.0", known_at: iso(7) },
      { time: iso(6), kind: "high", price: "111.2", known_at: iso(9) },
    ],
    geometry: { matching_extremes_difference_pct: "0.18", depth_pct: "12.1", span_candles: 4 },
    state: "confirmed",
  };
  const confirmed = explainEvidence(base, { timeframe: "1h", qualification: qualification([]) });
  assert.equal(confirmed.status, "Confirmed");
  assert.match(section(confirmed, "What BRAIN detected"), /high 111\.0 \(2026-10-06 02:00 UTC\) → low 99\.0/);
  assert.match(section(confirmed, "When the evidence became available"), /neckline closed through at 2026-10-06 12:00 UTC/);
  assert.match(section(confirmed, "Confirmed or invalidated"), /closed beyond the neckline by more than the neckline tolerance/);
  const invalidated = explainEvidence({ ...base, state: "invalidated", confirmation_time: null }, { timeframe: "1h", qualification: qualification([]) });
  assert.equal(invalidated.status, "Invalidated");
  assert.match(section(invalidated, "Confirmed or invalidated"), /closed beyond the invalidation level/);
  const formed = explainEvidence({ ...base, state: "formed", confirmation_time: null }, { timeframe: "1h", qualification: qualification([]) });
  assert.equal(formed.status, "Formed");
  assert.match(section(formed, "Confirmed or invalidated"), /neckline has not been closed through/);
});

test("candle shape explanations are explicitly descriptive and never call themselves signals", () => {
  // Shape exactly as chart_evidence._shape_item emits it.
  const explanation = explainEvidence(
    { id: "c1", evidence_kind: "candle_shape", kind: "strong_bullish_body", direction: "bullish", time: iso(4), known_at: iso(5), open: "100", high: "110", low: "99", close: "109", body_ratio: "0.8", upper_wick_ratio: "0.1", lower_wick_ratio: "0.1" },
    { timeframe: "1h", qualification: qualification([]) },
  );
  assert.equal(explanation.kind, "candle_shape");
  assert.equal(explanation.title, "Strong bullish body");
  assert.match(explanation.note, /not a BRAIN trading signal/);
  assert.match(explanation.note, /not an input to setup qualification/);
  assert.equal(explanation.influenced, false);
});

test("candle shape explanation picks its wording from the backend shape kind", () => {
  const base = { evidence_kind: "candle_shape", time: iso(4), known_at: iso(5), open: "100", high: "110", low: "90", close: "101", body_ratio: "0.1" };
  const engulf = explainEvidence({ ...base, id: "e1", kind: "bearish_engulfing", direction: "bearish" }, {});
  assert.equal(engulf.title, "Bearish engulfing (two candles)");
  const rejection = explainEvidence({ ...base, id: "w1", kind: "lower_wick_rejection", direction: "bullish" }, {});
  assert.equal(rejection.title, "Long lower wick");
  const indecision = explainEvidence({ ...base, id: "i1", kind: "indecision", direction: "neutral" }, {});
  assert.equal(indecision.tone, "neutral");
  assert.equal(explainEvidence({ ...base, id: "u1", kind: "made_up_shape", direction: "bullish" }, {}).title, "made_up_shape", "unknown shape falls back to its own name");
});

test("swing dispatch works from the backend's high/low kind even without evidence_kind", () => {
  const swingLow = explainEvidence({ id: "s-low", kind: "low", label: "HL", time: iso(3), price: "100", known_at: iso(5), confirmed_by: iso(4) }, {});
  assert.equal(swingLow.kind, "swing");
  assert.equal(swingLow.title, "HL · swing low at 100");
});

test("missing backend fields are called UNKNOWN, never filled in", () => {
  const explanation = explainEvidence({ id: "z", kind: "zone", role: "support", band_low: "100", band_high: "101", center: "100.5", touch_count: null }, { timeframe: "1h" });
  assert.match(section(explanation, "Why it was detected"), /UNKNOWN touch/);
  const swing = explainEvidence({ id: "s", kind: "swing", label: "HL", time: null, price: "100", known_at: iso(3) }, {});
  assert.match(section(swing, "What BRAIN detected"), /UNKNOWN/);
});

test("unknown evidence kinds return null rather than a guessed explanation", () => {
  assert.equal(explainEvidence({ kind: "mystery" }, {}), null);
  assert.equal(explainEvidence(null, {}), null);
});

test("plan-level explanations state that no order is placed", () => {
  const explanation = explainPlanLevel({ label: "Entry", value: "124.00", plan: { direction: "bullish" }, timeframe: "1h", planState: "PLANNABLE" });
  assert.match(section(explanation, "What BRAIN detected"), /Entry 124\.00 is part of the engine's PLANNABLE paper plan \(bullish\)/);
  assert.match(section(explanation, "Confirmed or invalidated"), /no order is placed/);
  const target = explainPlanLevel({ label: "T2", value: "138.00", plan: null, timeframe: "1h", planState: "PLANNABLE" });
  assert.match(section(target, "Why it was detected"), /observation levels, not promises/);
});

test("plan levels claim influence only when a backend plan supplied them", () => {
  const withPlan = explainPlanLevel({ label: "Stop", value: "117.00", plan: { direction: "bullish" }, timeframe: "1h", planState: "PLANNABLE" });
  assert.match(section(withPlan, "Did it influence the current assessment?"), /^Yes\./);
  const withoutPlan = explainPlanLevel({ label: "Stop", value: "117.00", plan: null, timeframe: "1h", planState: "NO_PLAN" });
  assert.match(section(withoutPlan, "Did it influence the current assessment?"), /^No\./);
});

// --- Support / resistance band explanations (audit P1) ------------------------------------

import { explainZoneBand } from "../../src/trading_assistant/web/static/js/explain.js";

/** Section body by position: 0 what, 1 why, 2 when, 3 confirmed/invalidated, 4 influence. */
const body = (explanation, index) => explanation.sections[index].body;

const baseBand = {
  id: "1h:61500:61900",
  position: "price_inside",
  display_role: "price_inside",
  band_low: "61500",
  band_high: "61900",
  band_width_pct: "0.65",
  touch_count: 3,
  swing_high_count: 2,
  swing_low_count: 1,
  isolated: false,
  first_seen: "2026-10-06T04:00:00Z",
  last_tested: "2026-10-10T15:00:00Z",
  age_candles: 2,
  faded: false,
  source_timeframe: "1h",
  source_swing_timestamps: ["2026-10-06T04:00:00Z", "2026-10-08T10:00:00Z", "2026-10-10T15:00:00Z"],
  merged_zone_count: 1,
  raw_roles: ["resistance"],
  latest_close: "61700",
};

test("a band that contains the close is explained as inside, never as resistance, and says why the detector differed", () => {
  const explanation = explainZoneBand(baseBand, { timeframe: "1h" });
  assert.match(explanation.title, /^Price inside zone · 1H · 61,500\.00 – 61,900\.00$/);
  assert.match(body(explanation, 0), /The latest close 61,700 is inside this band/);
  assert.doesNotMatch(explanation.title, /resistance/i);
  assert.match(explanation.note, /labelled this zone "resistance" because its centre sits on that side of the close/);
});

test("the explanation states touches, origin, last test and that influence is not claimed", () => {
  const explanation = explainZoneBand(baseBand, { timeframe: "1h" });
  assert.match(body(explanation, 3), /3 swing touches \(2 highs, 1 lows\)/);
  assert.match(body(explanation, 1), /Built by clustering 3 confirmed swing extremes/);
  assert.match(body(explanation, 2), /Last tested 2026-10-10 15:00 UTC \(2 candles ago\)/);
  assert.equal(explanation.influenced, false);
  assert.match(body(explanation, 4), /^Not established\. /);
  assert.match(body(explanation, 4), /candidate structural target when a trade plan is produced/);
});

test("a single-swing band says it has not been tested by a second swing", () => {
  const explanation = explainZoneBand({ ...baseBand, touch_count: 1, isolated: true, swing_high_count: 1, swing_low_count: 0 }, { timeframe: "1h" });
  assert.equal(explanation.status, "Single swing");
  assert.match(body(explanation, 3), /Only 1 swing touch: it has not yet been tested by a second swing/);
});

test("a merged display band reports how many detector zones it combines", () => {
  const explanation = explainZoneBand({ ...baseBand, merged_zone_count: 3, source_timeframe: "4h" }, { timeframe: "4h" });
  assert.match(body(explanation, 3), /Merged from 3 overlapping detector zones for display; touches are summed/);
  assert.match(explanation.timeframe, /4H/);
});

test("a support band below price reads as support with the close above it", () => {
  const explanation = explainZoneBand({
    ...baseBand, position: "below_price", display_role: "support", band_low: "58000", band_high: "58200",
    raw_roles: ["support"], latest_close: "60000",
  }, { timeframe: "1h" });
  assert.match(explanation.title, /^Support · 1H · 58,000\.00 – 58,200\.00$/);
  assert.match(body(explanation, 0), /The band is below the latest close/);
  assert.equal(explanation.note, "Display only. Showing this band does not change any zone, setup, plan or paper observation.");
});

test("a faded band is described as last tested long ago", () => {
  const explanation = explainZoneBand({ ...baseBand, faded: true, age_candles: 180 }, { timeframe: "1h" });
  assert.match(body(explanation, 3), /Last tested long ago, so it is drawn faded/);
});

test("missing or non-object bands produce no explanation rather than a guessed one", () => {
  assert.equal(explainZoneBand(null), null);
  assert.equal(explainZoneBand("zone"), null);
});

test("evidence zone items with a display position use the band wording, not the raw detector label", () => {
  const explanation = explainEvidence(
    { ...baseBand, kind: "zone", role: "resistance", center: "61700" },
    { timeframe: "1h" },
  );
  assert.match(explanation.title, /Price inside zone/);
  assert.doesNotMatch(explanation.title, /^Resistance/);
});

test("head and shoulders wording matches the detector geometry (five alternating swings)", async () => {
  const { explainEvidence: explain } = await import("../../src/trading_assistant/web/static/js/explain.js");
  const item = {
    id: "hs-1", evidence_kind: "pattern", type: "head_and_shoulders", pattern_id: "hs-1",
    state: "confirmed", anchor_time: "2026-10-10T10:00:00Z", confirmed_at: "2026-10-10T12:00:00Z",
  };
  const text = JSON.stringify(explain(item, { qualification: null }));
  assert.match(text, /five alternating swings/);
  assert.match(text, /three swing highs/);
  assert.match(text, /two swing lows/);
  assert.doesNotMatch(text, /three swing highs where the middle/);
  assert.doesNotMatch(text, /confirmation rule is satisfied/);
  assert.doesNotMatch(text, /no longer treats this geometry as live/);
});
