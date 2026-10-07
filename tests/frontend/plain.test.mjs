/** Plain-English translator: every backend token maps; nothing leaks raw. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  aggregateSeeingLine,
  backendNoticeText,
  decisionText,
  disabledReasonText,
  doingNowParagraph,
  evidenceCategoryLabel,
  healthDetailText,
  healthLabel,
  hasValidTradePlan,
  hierarchyAllowsReadyWording,
  hierarchyDecisionLabel,
  hierarchyStatusLabel,
  humanizeToken,
  invalidateMetaLine,
  isCleanSentence,
  levelSourceText,
  liveSetupMetaLine,
  metricLabelText,
  metricStatusText,
  outcomeStatusText,
  planReasonText,
  planRefusalSentence,
  planStateLabel,
  rangeTransitionText,
  referenceTypeText,
  rejectionKindText,
  runnerStatusLabel,
  setupStateLabel,
  shortRuleTitle,
  snapshotReasonText,
  snapshotStatusText,
  terminalReasonText,
  terminalSentence,
  translateRule,
  trendMomentumText,
  trendReasonText,
  trendTransitionText,
  unavailableReasonText,
  verdictStateLabel,
} from "../../src/trading_assistant/web/static/js/plain.js";

test("humanizeToken never returns raw snake, steps, or empties", () => {
  assert.equal(humanizeToken("maximum_bars_elapsed"), "Maximum bars elapsed");
  assert.equal(humanizeToken("NO_SETUP"), "No setup");
  assert.equal(humanizeToken("higher_timeframe:4h"), "Higher timeframe 4H");
  assert.equal(humanizeToken("step4_retest(held)"), "Retest(held)");
  assert.equal(humanizeToken(null), "UNKNOWN");
  assert.equal(humanizeToken(""), "UNKNOWN");
});

test("state labels translate every setup and verdict state", () => {
  assert.equal(setupStateLabel("WATCH"), "Watching");
  assert.equal(setupStateLabel("QUALIFIED"), "Qualified");
  assert.equal(setupStateLabel("NO_SETUP"), "No setup");
  assert.equal(verdictStateLabel("PLANNABLE"), "Plan calculated");
  assert.equal(verdictStateLabel("WATCH"), "Watching");
  assert.equal(verdictStateLabel("NO TRADE"), "No trade");
  assert.equal(verdictStateLabel("UNKNOWN"), "Unknown");
});

test("hierarchy readiness wording requires the evaluated backend PLANNABLE state", () => {
  const waiting = { available: true, status: "evaluated", decision: "awaiting_confirmation",
    overall: "WAITING FOR CONFIRMATION" };
  assert.equal(hierarchyAllowsReadyWording(waiting), false);
  assert.equal(hierarchyStatusLabel(waiting), "WAITING FOR CONFIRMATION");
  assert.equal(hierarchyStatusLabel({ ...waiting, overall: "TRADE READY" }), "WAITING FOR CONFIRMATION");
  assert.equal(hierarchyStatusLabel({ ...waiting, overall: "TRADE CLEARED" }), "WAITING FOR CONFIRMATION");
  assert.equal(hierarchyDecisionLabel({ ...waiting, decision_label: "Trade ready" }), "Waiting for lower-timeframe confirmation");
  assert.equal(hierarchyAllowsReadyWording({ ...waiting, decision: "awaiting_execution" }), false);
  assert.equal(hierarchyStatusLabel({ ...waiting, decision: "awaiting_execution" }), "WAITING FOR ENTRY TIMING");
  assert.equal(hierarchyAllowsReadyWording({ ...waiting, status: "incomplete", decision: "plannable" }), false);
  assert.equal(hierarchyStatusLabel({ ...waiting, status: "incomplete", decision: "plannable" }), "WAITING FOR COMPLETE MARKET DATA");
  assert.equal(hierarchyAllowsReadyWording({ ...waiting, counter_trend: true }), false);
  assert.equal(hierarchyStatusLabel({ ...waiting, counter_trend: true }), "COUNTER-TREND BLOCKED BELOW PLANNABLE");
  assert.equal(hierarchyAllowsReadyWording({ ...waiting, decision: "plannable" }), true);
  assert.equal(hierarchyStatusLabel({ ...waiting, decision: "plannable" }), "PLAN READY — HIERARCHY COMPLETE");
  assert.equal(hierarchyAllowsReadyWording({ ...waiting, decision: "plannable", counter_trend: true }), false);
  assert.equal(hierarchyStatusLabel({ ...waiting, decision: "plannable", counter_trend: true }), "COUNTER-TREND BLOCKED BELOW PLANNABLE");
  assert.equal(hierarchyDecisionLabel({ ...waiting, decision: "plannable",
    decision_label: "Plan ready — hierarchy complete" }), "Plan ready — hierarchy complete");
  assert.equal(hierarchyAllowsReadyWording({ available: false, status: "evaluated", decision: "plannable" }), false);
});

test("snapshot and terminal reasons become sentences", () => {
  assert.match(snapshotReasonText("no_seed_confirmed_in_replayed_frames"), /No breakout/);
  assert.match(snapshotReasonText("all_candidates_invalidated_or_expired"), /expired or been invalidated/);
  assert.match(snapshotReasonText("something_future"), /Something future/);
  assert.equal(snapshotReasonText(null), null);
  assert.equal(terminalReasonText("maximum_bars_elapsed"), "its time limit ran out");
  assert.equal(terminalReasonText("opposite_close_through_reference"), "price closed back through the level");
  assert.equal(
    terminalSentence("maximum_bars_elapsed", { ageBars: 11, maxBars: 10 }),
    "Time ran out: the setup lived 11 closes (limit 10).",
  );
  assert.match(terminalSentence("failed_breakout", {}), /the breakout failed/);
});

test("trend, transition, and momentum vocabularies map fully", () => {
  assert.equal(trendReasonText("higher_highs_and_higher_lows"), "higher highs and higher lows");
  assert.equal(trendReasonText("lower_highs_and_lower_lows"), "lower highs and lower lows");
  assert.equal(trendReasonText("insufficient_swings"), "not enough confirmed swings");
  assert.equal(trendReasonText("equal_extremes"), "equal highs and lows");
  assert.equal(trendReasonText("conflicting_structure"), "swings pointing both ways");
  assert.equal(trendTransitionText("to_neutral"), "trend turned neutral");
  assert.equal(trendTransitionText("reversed"), "trend reversed");
  assert.equal(trendMomentumText("strengthening"), "strengthening");
  assert.equal(trendMomentumText("mixed"), "mixed");
  assert.equal(metricLabelText("expanding"), "expanding");
  assert.equal(metricLabelText("unknown"), "trend unknown");
  assert.equal(rangeTransitionText("held"), "range held");
  assert.equal(rangeTransitionText("absent"), "no range");
});

test("unavailability reasons never surface None or snake", () => {
  assert.equal(unavailableReasonText("insufficient_candles"), "not enough stored candles yet");
  assert.equal(unavailableReasonText(null), "not enough stored candles yet");
  assert.equal(unavailableReasonText("None"), "not enough stored candles yet");
  assert.equal(unavailableReasonText("missing_context"), "no data for that timeframe");
  assert.equal(referenceTypeText("zone"), "price zone");
  assert.equal(referenceTypeText("range_high"), "range top");
  assert.equal(rejectionKindText("failed_breakout"), "failed breakout");
  assert.equal(rejectionKindText("failed_retest"), "failed retest");
  assert.equal(snapshotStatusText("evaluated"), "fully evaluated");
  assert.equal(snapshotStatusText("incomplete"), "incomplete data");
});

test("health, evidence, and decision labels map", () => {
  assert.equal(healthLabel("CURRENT"), "Current");
  assert.equal(healthLabel("STALE"), "Stale");
  assert.match(healthDetailText("stored_candles_stop_before_expected_boundary"), /stop before/);
  assert.equal(healthDetailText("stored candles stop 2 interval(s) before X"), "stored candles stop 2 interval(s) before X");
  assert.equal(evidenceCategoryLabel("higher_timeframe"), "Higher timeframe");
  assert.equal(evidenceCategoryLabel("lifecycle"), "Lifetime");
  assert.equal(decisionText("ACCEPTED"), "Accept");
  assert.equal(decisionText("SKIPPED"), "Skip");
});

test("structure rule translation is direction-aware", () => {
  const failed = translateRule(
    {
      rule_id: "structure",
      outcome: "failed",
      reason: "trend=neutral; reason=conflicting_structure; continuation requires alignment, reversal allows sufficient neutral",
      required: true,
    },
    { direction: "bearish" },
  );
  assert.equal(failed.title, "Structure");
  assert.ok(failed.blocking);
  assert.match(failed.sentence, /neutral/);
  assert.match(failed.sentence, /bearish/);
  assert.doesNotMatch(failed.sentence, /=|conflicting_structure|None/);
  const pending = translateRule(
    { rule_id: "structure", outcome: "pending", reason: "trend=neutral; reason=insufficient_swings", required: true },
    {},
  );
  assert.match(pending.sentence, /waiting for clarity/);
  const pass = translateRule(
    { rule_id: "structure", outcome: "passed", reason: "trend=bullish; reason=higher_highs_and_higher_lows", required: true },
    { direction: "bullish" },
  );
  assert.match(pass.sentence, /supports this direction/);
  assert.ok(!pass.blocking);
});

test("volume and volatility rules translate numbers and None fragments", () => {
  const weak = translateRule(
    { rule_id: "volume", outcome: "failed", reason: "relative_volume=0.290123; minimum=1.0; source_reason=None", required: true },
    {},
  );
  assert.equal(weak.sentence, "Volume is weak at 0.29× its average (needs 1.00×).");
  const healthy = translateRule(
    { rule_id: "volume", outcome: "passed", reason: "relative_volume=1.512; minimum=1.0; source_reason=None", required: true },
    {},
  );
  assert.match(healthy.sentence, /healthy at 1.51×/);
  const missing = translateRule(
    { rule_id: "volume", outcome: "pending", reason: "relative_volume=None; minimum=1.0; source_reason=insufficient_candles", required: true },
    {},
  );
  assert.match(missing.sentence, /not available yet/);
  assert.doesNotMatch(missing.sentence, /None/);
  const hot = translateRule(
    { rule_id: "volatility", outcome: "failed", reason: "ATR_percent=12.345; requires 0 < ATR_percent <= 10.0; source_reason=None", required: true },
    {},
  );
  assert.equal(hot.sentence, "Volatility is too high: ATR is 12.35% of price (limit 10.00%).");
});

test("location, range, and confirmation rules translate", () => {
  const wrong = translateRule(
    { rule_id: "location", outcome: "failed", reason: "close=61850.125; requires directional side of frozen band [61900.5, 62000.75]", required: true },
    { direction: "bullish" },
  );
  assert.match(wrong.sentence, /wrong side/);
  assert.match(wrong.sentence, /61,850.13/);
  const held = translateRule({ rule_id: "held_retest", outcome: "pending", reason: "requires Step 4 held retest of the seed breakout", required: true }, {});
  assert.equal(held.sentence, "Still needs a retest that holds the breakout level.");
  assert.doesNotMatch(held.sentence, /Step/);
  const seed = translateRule({ rule_id: "seed_event", outcome: "passed", reason: "confirmed FailedBreakout; reference=abc", required: true }, {});
  assert.match(seed.sentence, /failed breakout/);
  const rangeFail = translateRule({ rule_id: "active_range", outcome: "failed", reason: "x", required: true }, {});
  assert.match(rangeFail.sentence, /no longer matches/);
  const follow = translateRule({ rule_id: "range_followthrough", outcome: "pending", reason: "x", required: true }, {});
  assert.match(follow.sentence, /Waiting for a later close/);
  const invalidated = translateRule({ rule_id: "no_failed_breakout", outcome: "failed", reason: "x", required: true }, {});
  assert.match(invalidated.sentence, /invalidated/);
  const reversal = translateRule({ rule_id: "reversal_breakout", outcome: "pending", reason: "x", required: true }, {});
  assert.match(reversal.sentence, /different level/);
  const later = translateRule({ rule_id: "later_evaluation", outcome: "pending", reason: "x", required: true }, {});
  assert.match(later.sentence, /next close/);
  const life = translateRule(
    { rule_id: "lifecycle", outcome: "failed", reason: "maximum_bars_elapsed; age_bars=11; max_bars=10; window_end=x; gaps=()", required: true },
    {},
  );
  assert.match(life.sentence, /Time ran out/);
  assert.doesNotMatch(life.sentence, /age_bars|window_end/);
});

test("optional rules never block and say so", () => {
  const htf = translateRule(
    { rule_id: "higher_timeframe:not_requested", outcome: "pending", reason: "no higher timeframes requested; no alignment inferred", required: false },
    {},
  );
  assert.ok(!htf.blocking);
  assert.ok(htf.optional);
  assert.match(htf.sentence, /not used on this screen/);
  const htfVeto = translateRule(
    { rule_id: "higher_timeframe:4h", outcome: "failed", reason: "trend=bearish", required: false, veto: true },
    { direction: "bullish" },
  );
  assert.match(htfVeto.sentence, /veto/);
  const classical = translateRule({ rule_id: "classical_pattern", outcome: "pending", reason: "no current confirmed classical pattern since seed", required: false }, {});
  assert.match(classical.sentence, /optional/);
  const pattern = translateRule({ rule_id: "classical_pattern:xyz", outcome: "passed", reason: "double_bottom; optional only; cannot qualify or veto a setup", required: false }, {});
  assert.match(pattern.sentence, /double bottom/);
  assert.match(pattern.sentence, /optional/);
});

test("unknown rules degrade to humanized titles, clean prose, never raw", () => {
  assert.equal(shortRuleTitle("some_future_check"), "Some future check");
  const generic = translateRule({ rule_id: "some_future_check", outcome: "pending", reason: "k=v; messy_snake_bit", required: true }, {});
  assert.equal(generic.sentence, "Some future check is still waiting.");
  const clean = translateRule({ rule_id: "some_future_check", outcome: "pending", reason: "A clean backend sentence.", required: true }, {});
  assert.equal(clean.sentence, "A clean backend sentence.");
  assert.ok(isCleanSentence("A clean backend sentence."));
  assert.ok(!isCleanSentence("trend=neutral; reason=x"));
  assert.ok(!isCleanSentence("requires Step 4 held retest"));
  assert.ok(!isCleanSentence("value with snake_case inside"));
  assert.ok(!isCleanSentence("source_reason=None"));
});

test("plan states and all thirty reason codes translate", () => {
  assert.equal(planStateLabel("PLANNABLE"), "Plannable");
  assert.equal(planStateLabel("NO_PLAN"), "No plan");
  assert.equal(planStateLabel("INVALID"), "Invalid");
  const codes = [
    "active_range_mismatch", "confirmation_event_missing", "current_close_unavailable",
    "direction_mismatch", "entry_level_not_usable", "entry_not_on_trade_side", "family_mismatch",
    "frame_snapshot_asof_mismatch", "frozen_range_evidence_missing", "future_evidence_used",
    "instrument_mismatch", "invalid_reference_band", "minimum_r_multiple_not_met",
    "missing_atr_for_stop_buffer", "missing_confirmation_level", "missing_plan_close",
    "missing_reference_band", "no_valid_target_available", "non_positive_risk",
    "range_entry_outside_frozen_bounds", "range_followthrough_failed", "reference_id_mismatch",
    "seed_event_missing", "setup_identity_mismatch", "setup_not_in_snapshot", "setup_not_qualified",
    "stale_qualification", "stop_non_positive", "stop_not_beyond_entry", "terminal_source_setup",
  ];
  assert.equal(codes.length, 30);
  for (const code of codes) {
    const text = planReasonText(code);
    assert.ok(text && !text.includes("_"), code);
  }
  assert.match(
    planRefusalSentence(["minimum_r_multiple_not_met", "no_valid_target_available"]),
    /The planner could not build a plan/,
  );
  assert.match(planRefusalSentence([]), /could not build a plan/);
});

test("runner, outcome, and metric statuses translate", () => {
  assert.equal(runnerStatusLabel("PROCESSED"), "Processed");
  assert.equal(runnerStatusLabel("NO_DATA"), "No data");
  assert.equal(runnerStatusLabel("ERROR"), "Error");
  assert.equal(outcomeStatusText("ENTRY_NOT_REACHED"), "Entry never reached");
  assert.equal(outcomeStatusText("INVALIDATED_BEFORE_ENTRY"), "Invalidated before entry");
  assert.equal(outcomeStatusText("STOPPED"), "Stopped");
  assert.equal(outcomeStatusText("STOPPED_AFTER_TARGETS"), "Stopped after targets");
  assert.equal(outcomeStatusText("TARGETS_REACHED"), "Targets reached");
  assert.equal(outcomeStatusText("OPEN_AT_CUTOFF"), "Still open at cutoff");
  assert.equal(outcomeStatusText("AMBIGUOUS"), "Ambiguous candle");
  assert.equal(outcomeStatusText("INCOMPLETE_DATA"), "Incomplete data");
  assert.equal(metricStatusText("SUFFICIENT_DATA"), "enough data");
  assert.equal(metricStatusText("INSUFFICIENT_DATA"), "not enough data");
});

test("level sources translate without step numbers", () => {
  assert.equal(levelSourceText("step3_volatility_latest_close"), "latest close");
  assert.equal(levelSourceText("step4_retest_close"), "held-retest close");
  assert.equal(levelSourceText("step4_reference_zone"), "price zone band");
  assert.equal(levelSourceText("step4_equal_level_cluster"), "equal highs/lows cluster");
  assert.equal(levelSourceText("planner_stop_buffer_none"), "no buffer (equals invalidation)");
  assert.equal(levelSourceText("r_multiple_fallback"), "fallback projection");
  assert.doesNotMatch(levelSourceText("step4_reference_range_high"), /step4/i);
});

test("journal disabled reasons rebuild Step 6 refusals in plain words", () => {
  assert.equal(
    disabledReasonText("Step 6 refused a plan (NO_PLAN: minimum_r_multiple_not_met); there is nothing to decide."),
    "The planner could not build a plan: no target reaches the minimum reward-to-risk. There is nothing to decide.",
  );
  assert.match(
    disabledReasonText("Step 6 produced an INVALID plan (invariant violation: non_positive_risk); it is reported, never corrected, and cannot be decided."),
    /The planner produced an invalid plan \(invariant violation: the risk would not be positive\)/,
  );
  assert.equal(
    disabledReasonText("No qualifying setup is currently present."),
    "No qualifying setup is currently present.",
  );
});

test("unavailable backend notices map to fixed plain sentences", () => {
  assert.equal(
    backendNoticeText("no Step 3/4 frame could be built at this boundary", "fallback"),
    "No market frame could be built at this point.",
  );
  assert.equal(
    backendNoticeText("no evaluated snapshot at this boundary", "fallback"),
    "No completed analysis at this point.",
  );
  assert.equal(backendNoticeText(null, "fallback"), "fallback");
});

test("doing-now paragraph composes from structured facts with rounded numbers", () => {
  const text = doingNowParagraph(
    {
      trend: { sufficient: true, direction: "bullish", reason: "higher_highs_and_higher_lows" },
      volatility: { available: true, atr_percent_of_price: "2.75345715", direction: { label: "contracting" } },
      volume: { sufficient: false, reason: "insufficient_candles" },
      range: { active: false, transition: "absent" },
      breakout_state: { attempts: [{}], acceptances: [], rejections: [], sweeps: [] },
    },
    "WATCH",
  );
  assert.match(text, /Trend is bullish \(higher highs and higher lows\)\./);
  assert.match(text, /ATR 2\.75% of price/);
  assert.match(text, /Volume is unknown \(not enough stored candles yet\)/);
  assert.match(text, /No active range\./);
  assert.match(text, /Fresh at this close: 1 breakout attempt\(s\)\./);
  assert.match(text, /Qualification state: watching\./);
  assert.doesNotMatch(text, /2\.75345715|BULLISH|WATCH/);
});

test("aggregate and meta lines translate states and vetoes", () => {
  assert.equal(
    aggregateSeeingLine({ state: "WATCH", status: "evaluated", live_count: 2 }),
    "Watching 2 developing setups · fully evaluated",
  );
  assert.equal(aggregateSeeingLine({ state: "NO_SETUP", status: "incomplete" }), "No setup · incomplete data");
  assert.equal(
    liveSetupMetaLine({ state: "WATCH", age_bars: 2, max_bars: 10, bars_remaining: 8, vetoed: true, vetoed_by: ["higher_timeframe:4h"] }),
    "Watching · age 2/10 bars · 8 left · vetoed by Higher timeframe 4H",
  );
  assert.equal(
    invalidateMetaLine({ state: "QUALIFIED", bars_remaining: 5, max_bars: 10, vetoed: false }),
    "Qualified · 5/10 bars left",
  );
});

test("hasValidTradePlan reads only the backend's own plan states", () => {
  const complete = {
    qualification: { available: true, state: "QUALIFIED" },
    planning: { state: "PLANNABLE" },
    plan: { state: "PLANNABLE" },
  };
  assert.equal(hasValidTradePlan(complete), true);
  // A WATCH setup, a refused plan, or unavailable qualification can never
  // combine into a "valid plan" — no frontend path upgrades them.
  assert.equal(hasValidTradePlan({ ...complete, qualification: { available: true, state: "WATCH" } }), false);
  assert.equal(hasValidTradePlan({ ...complete, planning: { state: "NO_PLAN" } }), false);
  assert.equal(hasValidTradePlan({ ...complete, plan: { state: "NO_PLAN" } }), false);
  assert.equal(hasValidTradePlan({ ...complete, qualification: { available: false } }), false);
  assert.equal(hasValidTradePlan(null), false);
});
