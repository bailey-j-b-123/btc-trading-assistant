/**
 * Plain-English translator for deterministic backend facts (pure logic, no DOM).
 *
 * Policy (presentation only — every fact still comes from the backend):
 * - Level 1 (one-glance): short labels, never enums or steps.
 * - Level 2 (this module): short plain sentences. All enums, rule ids,
 *   `key=value` diagnostics, step numbers, and None fragments are translated
 *   or dropped; numbers arrive pre-rounded by the caller.
 * - Level 3 (collapsible technical details): the caller keeps showing the raw
 *   backend strings verbatim, so nothing is ever lost or reinterpreted.
 *
 * Every function degrades to a humanized fallback for unknown inputs: the UI
 * must never render raw `snake_case`, `UPPER_CASE`, `Step N`, or `None`.
 * Reason-string parsing below mirrors the exact f-string formats in
 * `setup_qualification/rules.py`; if a format drifts, the parser falls back
 * to a generic sentence (never the raw string).
 */

import { DECISION_LABELS } from "./decision.js";
import { displayRounded, isMissing } from "./format.js";

export const UNKNOWN_TEXT = "UNKNOWN";

/** "maximum_bars_elapsed" -> "Maximum bars elapsed"; strips Step N prefixes. */
export function humanizeToken(value) {
  if (isMissing(value)) return UNKNOWN_TEXT;
  let text = String(value).trim();
  if (!text) return UNKNOWN_TEXT;
  text = text.replace(/^step_?\d+_?/i, "");
  const [head, tail] = text.split(":");
  const words = head.replace(/_/g, " ").trim().toLowerCase();
  const titled = words.charAt(0).toUpperCase() + words.slice(1);
  if (tail === undefined || tail === "") return titled || UNKNOWN_TEXT;
  return `${titled} ${tail.trim().toUpperCase()}`;
}

/** Short chip title for a rule id ("higher_timeframe:4h" -> "Higher timeframe 4H"). */
export function shortRuleTitle(ruleId) {
  if (isMissing(ruleId)) return UNKNOWN_TEXT;
  const text = String(ruleId);
  if (text.startsWith("classical_pattern:")) return "Classical pattern";
  if (text.startsWith("higher_timeframe:")) {
    const tail = text.slice("higher_timeframe:".length);
    if (tail === "not_requested") return "Higher timeframes";
    return `Higher timeframe ${tail.toUpperCase()}`;
  }
  return humanizeToken(text);
}

function directionWord(direction) {
  if (direction === "bullish") return "bullish";
  if (direction === "bearish") return "bearish";
  return null;
}

function oppositeWord(direction) {
  if (direction === "bullish") return "bearish";
  if (direction === "bearish") return "bullish";
  return null;
}

/** "WATCH" -> "Watching"; unknown states humanize, never raw. */
export function setupStateLabel(state) {
  if (state === "WATCH") return "Watching";
  if (state === "QUALIFIED") return "Qualified";
  if (state === "NO_SETUP") return "No setup";
  return humanizeToken(state);
}

/** Big verdict chip words (Level 1), without implying hierarchy confirmation. */
export function verdictStateLabel(state) {
  if (state === "PLANNABLE") return "Plan calculated";
  if (state === "WATCH") return "Watching";
  if (state === "NO TRADE") return "No trade";
  return humanizeToken(state);
}

const HIERARCHY_STATUS_LABELS = {
  no_setup: "NO TRADE YET",
  watch: "WATCHING A SETUP",
  awaiting_confirmation: "WAITING FOR CONFIRMATION",
  awaiting_execution: "WAITING FOR ENTRY TIMING",
  plannable: "PLAN READY — HIERARCHY COMPLETE",
  invalidated: "SETUP INVALIDATED",
};

const HIERARCHY_DECISION_LABELS = {
  no_setup: "No trade yet",
  watch: "Watching a setup",
  awaiting_confirmation: "Waiting for lower-timeframe confirmation",
  awaiting_execution: "Waiting for entry timing",
  plannable: "Plan ready — hierarchy complete",
  invalidated: "Setup invalidated",
};

function containsReadinessWording(value) {
  return typeof value === "string" &&
    /\b(?:plan|trade|execution|entry|setup)(?:\s+|-)(?:is\s+)?(?:ready|confirmed|approved|cleared|permitted|actionable)\b|\b(?:ready|confirmed|approved|cleared|permitted|actionable)\s+to\s+trade\b/i.test(value);
}

/**
 * Presentation-only permission for readiness wording. The full, available
 * Step 13 snapshot must explicitly be evaluated as PLANNABLE; Step 6's
 * PLANNABLE plan state alone only means its levels were calculated.
 */
export function hierarchyAllowsReadyWording(payload) {
  return payload?.available === true && payload?.status === "evaluated" &&
    payload?.decision === "plannable" && payload?.counter_trend !== true;
}

/** Short display translation of existing Step 13 payload states. */
export function hierarchyStatusLabel(payload) {
  if (payload?.available !== true) return "HIERARCHY UNAVAILABLE";
  if (payload.status === "incomplete") return "WAITING FOR COMPLETE MARKET DATA";
  if (payload.counter_trend === true) return "COUNTER-TREND BLOCKED BELOW PLANNABLE";
  if (payload.decision === "plannable") {
    return hierarchyAllowsReadyWording(payload)
      ? HIERARCHY_STATUS_LABELS.plannable
      : "HIERARCHY NOT CLEARED";
  }
  const overall = typeof payload.overall === "string" ? payload.overall.trim() : "";
  if (containsReadinessWording(overall) || Object.values(HIERARCHY_STATUS_LABELS).includes(overall)) {
    return HIERARCHY_STATUS_LABELS[payload.decision] || "HIERARCHY STATUS UNKNOWN";
  }
  if (overall) return overall;
  return HIERARCHY_STATUS_LABELS[payload.decision] || "HIERARCHY STATUS UNKNOWN";
}

/** Keep backend wording except when it conflicts with the authoritative decision state. */
export function hierarchyDecisionLabel(payload) {
  if (payload?.available !== true) return "Hierarchy unavailable";
  if (payload.status === "incomplete") return "Waiting for complete market data";
  const backendLabel = typeof payload.decision_label === "string" ? payload.decision_label.trim() : "";
  const knownLabels = Object.values(HIERARCHY_DECISION_LABELS);
  if (payload.decision === "plannable") {
    if (!hierarchyAllowsReadyWording(payload)) return "Hierarchy not cleared";
    return containsReadinessWording(backendLabel) || knownLabels.includes(backendLabel)
      ? HIERARCHY_DECISION_LABELS.plannable
      : backendLabel || HIERARCHY_DECISION_LABELS.plannable;
  }
  if (containsReadinessWording(backendLabel) || knownLabels.includes(backendLabel)) {
    return HIERARCHY_DECISION_LABELS[payload.decision] || "Hierarchy status unknown";
  }
  if (backendLabel) return backendLabel;
  return HIERARCHY_DECISION_LABELS[payload.decision] || "Hierarchy status unknown";
}

/** Snapshot-level reasons from QualificationSnapshot.reasons. */
const SNAPSHOT_REASONS = {
  no_seed_confirmed_in_replayed_frames:
    "No breakout, failed breakout, or sweep has been confirmed in the candles checked.",
  all_candidates_invalidated_or_expired:
    "Every candidate setup has either expired or been invalidated.",
  candidates_have_failed_or_pending_rules_or_vetoes:
    "At least one developing setup still has failed or waiting checks.",
  at_least_one_candidate_satisfies_all_required_rules_without_veto:
    "At least one setup currently passes every required check.",
  missing_replay_frames:
    "Some candle closes were skipped while replaying, so affected setups were invalidated rather than guessed.",
  missing_current_candle:
    "The latest expected candle is not stored, so this boundary cannot be evaluated.",
  source_history_incomplete:
    "The stored candle history has gaps, so this evaluation is incomplete.",
};

export function snapshotReasonText(reason) {
  if (isMissing(reason)) return null;
  const text = String(reason).trim();
  return SNAPSHOT_REASONS[text] || humanizeToken(text);
}

/** Terminal reasons: short phrases plus a full sentence with age context. */
const TERMINAL_REASONS = {
  missing_replay_frames: "frames were skipped while replaying",
  missing_current_candle: "the latest candle is missing",
  source_candle_gap: "a gap in the stored candles",
  maximum_bars_elapsed: "its time limit ran out",
  failed_breakout: "the breakout failed",
  failed_retest: "the retest failed",
  opposite_close_through_reference: "price closed back through the level",
  close_outside_frozen_range: "price closed outside the frozen range",
};

export function terminalReasonText(reason) {
  if (isMissing(reason)) return "an unknown reason";
  const text = String(reason).trim();
  return TERMINAL_REASONS[text] || humanizeToken(text).toLowerCase();
}

export function terminalSentence(reason, { ageBars = null, maxBars = null } = {}) {
  const phrase = terminalReasonText(reason);
  const age =
    Number.isInteger(ageBars) && Number.isInteger(maxBars)
      ? ` after ${ageBars} of ${maxBars} allowed closes`
      : "";
  const text = String(reason || "").trim();
  if (text === "maximum_bars_elapsed") {
    return Number.isInteger(ageBars) && Number.isInteger(maxBars)
      ? `Time ran out: the setup lived ${ageBars} closes (limit ${maxBars}).`
      : "Time ran out: the setup passed its allowed lifetime.";
  }
  return `This setup ended because ${phrase}${age}.`;
}

/** TrendReason enum values from market_structure/trend.py. */
const TREND_REASONS = {
  higher_highs_and_higher_lows: "higher highs and higher lows",
  lower_highs_and_lower_lows: "lower highs and lower lows",
  insufficient_swings: "not enough confirmed swings",
  equal_extremes: "equal highs and lows",
  conflicting_structure: "swings pointing both ways",
};

export function trendReasonText(reason) {
  if (isMissing(reason)) return "an unknown reason";
  const text = String(reason).trim();
  return TREND_REASONS[text] || humanizeToken(text).toLowerCase();
}

const TREND_TRANSITIONS = {
  unknown: "trend change unknown",
  unchanged: "trend unchanged",
  to_neutral: "trend turned neutral",
  from_neutral: "trend moved out of neutral",
  reversed: "trend reversed",
};

export function trendTransitionText(value) {
  if (isMissing(value)) return "trend change unknown";
  const text = String(value).trim();
  return TREND_TRANSITIONS[text] || humanizeToken(text).toLowerCase();
}

const TREND_MOMENTUM = {
  unknown: "momentum unknown",
  strengthening: "strengthening",
  weakening: "weakening",
  steady: "steady",
  mixed: "mixed",
};

export function trendMomentumText(value) {
  if (isMissing(value)) return "momentum unknown";
  const text = String(value).trim();
  return TREND_MOMENTUM[text] || humanizeToken(text).toLowerCase();
}

const METRIC_LABELS = {
  unknown: "trend unknown",
  expanding: "expanding",
  contracting: "contracting",
  strengthening: "strengthening",
  weakening: "weakening",
  unchanged: "unchanged",
};

export function metricLabelText(value) {
  if (isMissing(value)) return "trend unknown";
  const text = String(value).trim();
  return METRIC_LABELS[text] || humanizeToken(text).toLowerCase();
}

const RANGE_TRANSITIONS = {
  absent: "no range",
  formed: "range formed",
  broken: "range broken",
  held: "range held",
  redefined: "range redefined",
};

export function rangeTransitionText(value) {
  if (isMissing(value)) return "range state unknown";
  const text = String(value).trim();
  return RANGE_TRANSITIONS[text] || humanizeToken(text).toLowerCase();
}

/** Volume/volatility/HTF unavailability reasons ("insufficient_candles", None, ...). */
export function unavailableReasonText(reason) {
  if (isMissing(reason)) return "not enough stored candles yet";
  const text = String(reason).trim().toLowerCase();
  if (text === "none") return "not enough stored candles yet";
  if (text === "insufficient_candles") return "not enough stored candles yet";
  if (text === "insufficient_swings") return "not enough confirmed swings yet";
  if (text === "missing_context") return "no data for that timeframe";
  if (text === "synthesized_context_not_usable") return "resampled data, not trusted";
  if (text === "incomplete_context") return "incomplete data for that timeframe";
  if (text === "missing_analysis") return "no analysis for that timeframe";
  if (text === "unavailable_context") return "unavailable for that timeframe";
  return humanizeToken(text).toLowerCase();
}

const REFERENCE_TYPES = {
  swing_high: "swing high",
  swing_low: "swing low",
  zone: "price zone",
  range_high: "range top",
  range_low: "range bottom",
  equal_high: "equal highs level",
  equal_low: "equal lows level",
};

export function referenceTypeText(type) {
  if (isMissing(type)) return "unknown reference";
  const text = String(type).trim();
  return REFERENCE_TYPES[text] || humanizeToken(text).toLowerCase();
}

export function rejectionKindText(kind) {
  if (kind === "failed_breakout") return "failed breakout";
  if (kind === "failed_retest") return "failed retest";
  return humanizeToken(kind).toLowerCase();
}

export function snapshotStatusText(status) {
  if (status === "evaluated") return "fully evaluated";
  if (status === "incomplete") return "incomplete data";
  return humanizeToken(status).toLowerCase();
}

export function healthLabel(status) {
  if (status === "CURRENT") return "Current";
  if (status === "STALE") return "Stale";
  if (status === "HISTORICAL") return "Historical";
  if (status === "INCOMPLETE") return "Incomplete";
  return humanizeToken(status);
}

const FRESHNESS_REASONS = {
  no_stored_candles: "No stored candles for this instrument and timeframe.",
  stored_candle_after_expected_boundary: "A stored candle is newer than the expected boundary.",
  as_of_is_not_the_current_boundary: "Evaluating a past instant, not the current boundary.",
  latest_expected_closed_candle_is_stored: "The latest expected closed candle is stored.",
  stored_candles_stop_before_expected_boundary: "Stored candles stop before the expected boundary.",
};

/**
 * Data-health detail: dashboard freshness codes map to sentences; forward-side
 * details are already plain sentences and pass through.
 */
export function healthDetailText(value) {
  if (isMissing(value)) return UNKNOWN_TEXT;
  const text = String(value).trim();
  if (FRESHNESS_REASONS[text]) return FRESHNESS_REASONS[text];
  return scrubStepRefs(text);
}

/**
 * Backend notice sentences are plain English except two templates that name
 * pipeline steps. Those map to fixed sentences; anything else passes through
 * with step references scrubbed, never raw.
 */
export function scrubStepRefs(text) {
  return String(text || "")
    .replace(/Step \d+(\/\d+)?/g, "the analysis")
    .replace(/step_?\d+_?/gi, "")
    .trim();
}

export function backendNoticeText(reason, fallback) {
  if (typeof reason !== "string" || !reason.trim()) return fallback;
  const text = reason.trim();
  if (/no Step 3\/4 frame could be built/i.test(text)) {
    return "No market frame could be built at this point.";
  }
  if (/no evaluated snapshot at this boundary/i.test(text)) {
    return "No completed analysis at this point.";
  }
  return scrubStepRefs(text) || fallback;
}

const EVIDENCE_CATEGORIES = {
  event: "Seed event",
  timing: "Timing",
  confirmation: "Confirmation",
  invalidation: "Invalidation",
  structure: "Structure",
  volume: "Volume",
  volatility: "Volatility",
  location: "Location",
  higher_timeframe: "Higher timeframe",
  classical_pattern: "Classical pattern",
  lifecycle: "Lifetime",
  context: "Context",
};

export function evidenceCategoryLabel(category) {
  if (isMissing(category)) return "Evidence";
  const text = String(category).trim();
  return EVIDENCE_CATEGORIES[text] || humanizeToken(text);
}

// ---------------------------------------------------------------------------
// Rule translation: { rule_id, outcome, reason, required, veto } -> sentences
// ---------------------------------------------------------------------------

function parseAssignments(reason) {
  const fields = {};
  const text = String(reason || "");
  for (const part of text.split(";")) {
    const match = part.trim().match(/^([A-Za-z_]+)\s*=\s*(.+)$/);
    if (match) fields[match[1]] = match[2].trim();
  }
  return fields;
}

function decimalOrNull(text) {
  if (isMissing(text)) return null;
  const cleaned = String(text).trim();
  if (cleaned.toLowerCase() === "none") return null;
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(cleaned)) return null;
  return cleaned;
}

function roundedOrUnknown(text, decimals) {
  const numeric = decimalOrNull(text);
  if (numeric === null) return UNKNOWN_TEXT;
  return displayRounded(numeric, decimals).display;
}

function seedTypeText(reason) {
  const match = String(reason || "").match(/confirmed\s+([A-Za-z]+)/);
  const type = match ? match[1] : "";
  if (type === "Breakout") return "breakout";
  if (type === "FailedBreakout") return "failed breakout";
  if (type === "Sweep") return "liquidity sweep";
  return "seed event";
}

function structureSentence(outcome, reason, direction) {
  const fields = parseAssignments(reason);
  const trend = (fields.trend || "").toLowerCase();
  const trendReason = trendReasonText(fields.reason);
  const side = directionWord(direction);
  const sideClause = side ? `this ${side}` : "this";
  if (outcome === "passed") {
    if (trend === "neutral") {
      return "Market structure is neutral — allowed for a reversal setup, but it adds no real support.";
    }
    return "Market structure supports this direction.";
  }
  if (outcome === "pending") {
    return `Market structure is unclear (${trendReason}) — waiting for clarity.`;
  }
  if (trend === "neutral") {
    const need =
      side === "bullish"
        ? "clearly bullish"
        : side === "bearish"
          ? "clearly bearish"
          : "clearly directional";
    return `Market structure is neutral (${trendReason}), but ${sideClause} continuation needs ${need} structure.`;
  }
  const opposite = oppositeWord(direction);
  if (opposite && trend === opposite) {
    return `Market structure points ${trend} (${trendReason}) — against ${sideClause} setup.`;
  }
  return `Market structure does not support ${sideClause} setup (${trendReason}).`;
}

function volumeSentence(outcome, reason) {
  const fields = parseAssignments(reason);
  const actual = roundedOrUnknown(fields.relative_volume, 2);
  const minimum = roundedOrUnknown(fields.minimum, 2);
  if (outcome === "pending" || decimalOrNull(fields.relative_volume) === null) {
    const why = unavailableReasonText(fields.source_reason);
    return `Volume data is not available yet (${why}).`;
  }
  if (outcome === "passed") {
    return `Volume is healthy at ${actual}× its average.`;
  }
  return `Volume is weak at ${actual}× its average (needs ${minimum}×).`;
}

function volatilitySentence(outcome, reason) {
  const fields = parseAssignments(reason);
  const actual = decimalOrNull(fields.ATR_percent);
  if (outcome === "pending" || actual === null) {
    const why = unavailableReasonText(fields.source_reason);
    return `Volatility data is not available yet (${why}).`;
  }
  const shown = roundedOrUnknown(fields.ATR_percent, 2);
  const limitMatch = String(reason || "").match(/<=\s*([\d.]+)/);
  const limit = limitMatch ? roundedOrUnknown(limitMatch[1], 2) : UNKNOWN_TEXT;
  if (outcome === "passed") {
    return `Volatility is within limits (ATR ${shown}% of price).`;
  }
  return `Volatility is too high: ATR is ${shown}% of price (limit ${limit}%).`;
}

function locationSentence(outcome, reason, direction) {
  const fields = parseAssignments(reason);
  const close = roundedOrUnknown(fields.close, 2);
  const bandMatch = String(reason || "").match(/\[([^\]]+)\]/);
  const side = directionWord(direction);
  const sideClause = side === "bullish" ? "above" : side === "bearish" ? "below" : "on the trade side of";
  if (outcome === "pending" || decimalOrNull(fields.close) === null) {
    return "The latest close is not available yet — waiting to place it against the frozen band.";
  }
  if (outcome === "passed") {
    return "The latest close sits on the trade side of the frozen band.";
  }
  const band = bandMatch ? ` ${roundedOrUnknown(bandMatch[1].split(",")[0], 2)}–${roundedOrUnknown(bandMatch[1].split(",")[1], 2)}` : "";
  return `The latest close (${close}) is on the wrong side — it must hold ${sideClause} the frozen band${band}.`;
}

function activeRangeSentence(outcome) {
  if (outcome === "passed") return "The active range still matches the setup's frozen range.";
  if (outcome === "pending") return "Waiting to compare the active range with the setup's frozen range.";
  return "The active range no longer matches the range this setup started from.";
}

function followthroughSentence(outcome) {
  if (outcome === "passed") return "Price followed through inside the frozen range.";
  if (outcome === "pending") return "Waiting for a later close inside the frozen range, further inward.";
  return "Price has not followed through inside the frozen range.";
}

function htfSentence(ruleId, outcome, reason) {
  const tail = String(ruleId).slice("higher_timeframe:".length);
  if (tail === "not_requested") {
    return "Higher timeframes are not used on this screen — nothing is inferred and nothing is blocked.";
  }
  const label = `Higher timeframe ${tail.toUpperCase()}`;
  const fields = parseAssignments(reason);
  const trend = (fields.trend || "").toLowerCase();
  if (outcome === "passed") {
    if (trend === "neutral") return `${label} is neutral — allowed, but it adds no real support.`;
    return `${label} trend is aligned.`;
  }
  if (outcome === "pending") {
    return `${label} trend is not clearly aligned yet (optional supporting evidence).`;
  }
  return `${label} trend opposes this setup — this vetoes the setup.`;
}

function classicalSentence(ruleId, outcome, reason) {
  if (ruleId === "classical_pattern") {
    return "No confirmed chart pattern adds support right now (optional).";
  }
  const first = String(reason || "").split(";")[0].trim();
  const pattern = humanizeToken(first || "chart pattern");
  if (outcome === "passed") return `A confirmed ${pattern.toLowerCase()} supports this direction (optional).`;
  if (outcome === "pending") return `A ${pattern.toLowerCase()} is forming but not confirmed (optional).`;
  return `A confirmed ${pattern.toLowerCase()} points the other way (optional — it never blocks).`;
}

function lifecycleSentence(outcome, reason) {
  if (outcome !== "failed") {
    return "This setup is still live — within its time limit and not invalidated.";
  }
  const first = String(reason || "").split(";")[0].trim();
  const fields = parseAssignments(reason);
  const age = /^\d+$/.test(fields.age_bars || "") ? Number(fields.age_bars) : null;
  const max = /^\d+$/.test(fields.max_bars || "") ? Number(fields.max_bars) : null;
  return terminalSentence(first, { ageBars: age, maxBars: max });
}

/**
 * A reason is already display-safe prose when it carries no diagnostics: no
 * key=value fragments, step references, snake_case tokens, or None literals.
 * Unknown future rules with clean prose keep their sentence; anything else
 * falls back to a generic sentence, never the raw string.
 */
export function isCleanSentence(reason) {
  if (typeof reason !== "string") return false;
  const text = reason.trim();
  if (!text) return false;
  if (text.includes("=")) return false;
  if (/\bStep \d/i.test(text)) return false;
  if (/\bstep_?\d/i.test(text)) return false;
  if (/\b[a-z]+_[a-z_]+\b/.test(text)) return false;
  if (/\bNone\b/.test(text)) return false;
  return true;
}

/**
 * Translate one qualification rule into a Level-2 title + sentence.
 * context.direction ("bullish"/"bearish"/null) disambiguates structure/location.
 * Returns { title, sentence, blocking, optional }.
 */
export function translateRule(rule, context = {}) {
  const ruleId = rule?.rule_id ?? rule?.rule ?? null;
  const outcome = rule?.outcome ?? null;
  const reason = typeof rule?.reason === "string" ? rule.reason : "";
  const required = rule?.required !== false;
  const id = isMissing(ruleId) ? "" : String(ruleId);
  const title = shortRuleTitle(id || null);
  const direction = context.direction ?? rule?.direction ?? null;

  const fail = outcome === "failed";
  const pending = outcome === "pending";
  let sentence;
  if (id === "seed_event") {
    sentence = `A ${seedTypeText(reason)} was confirmed.`;
  } else if (id === "later_evaluation") {
    sentence = pending
      ? "Confirmed on this very close — the first assessment comes on the next close."
      : "Assessment is running on closes after the seed.";
  } else if (id === "held_retest") {
    sentence = pending
      ? "Still needs a retest that holds the breakout level."
      : "A retest held the breakout level.";
  } else if (id === "no_failed_breakout") {
    sentence = fail
      ? "The seed breakout has failed — this setup is invalidated."
      : "The seed breakout has not failed.";
  } else if (id === "no_failed_retest") {
    sentence = fail
      ? "A retest of the seed breakout failed — this setup is invalidated."
      : "No retest of the seed breakout has failed.";
  } else if (id === "reversal_breakout") {
    sentence = pending
      ? "Still needs a directional breakout at a different level."
      : "A directional breakout confirmed at a different level.";
  } else if (id === "active_range") {
    sentence = activeRangeSentence(outcome);
  } else if (id === "range_followthrough") {
    sentence = followthroughSentence(outcome);
  } else if (id === "structure") {
    sentence = structureSentence(outcome, reason, direction);
  } else if (id === "volume") {
    sentence = volumeSentence(outcome, reason);
  } else if (id === "volatility") {
    sentence = volatilitySentence(outcome, reason);
  } else if (id === "location") {
    sentence = locationSentence(outcome, reason, direction);
  } else if (id === "lifecycle") {
    sentence = lifecycleSentence(outcome, reason);
  } else if (id.startsWith("higher_timeframe:")) {
    sentence = htfSentence(id, outcome, reason);
  } else if (id.startsWith("classical_pattern")) {
    sentence = classicalSentence(id, outcome, reason);
  } else if (!id) {
    sentence = isCleanSentence(reason)
      ? reason.trim()
      : "A backend check needs attention (no rule id was supplied).";
  } else {
    const verb = fail ? "is failing" : pending ? "is still waiting" : "was assessed";
    sentence = isCleanSentence(reason) ? reason.trim() : `${title} ${verb}.`;
  }
  if (rule?.veto === true && fail && !/veto/i.test(sentence)) {
    sentence += " This vetoes the setup.";
  }
  return {
    title,
    sentence,
    blocking: required && (fail || pending),
    optional: !required,
  };
}

// ---------------------------------------------------------------------------
// Plan states, plan reason codes, runner states, outcomes
// ---------------------------------------------------------------------------

export function planStateLabel(state) {
  if (state === "PLANNABLE") return "Plannable";
  if (state === "NO_PLAN") return "No plan";
  if (state === "INVALID") return "Invalid";
  return humanizeToken(state);
}

const PLAN_REASONS = {
  active_range_mismatch: "the live range no longer matches the frozen one",
  confirmation_event_missing: "the confirmation event is missing",
  current_close_unavailable: "the current close is unavailable",
  direction_mismatch: "the plan direction disagrees with the setup",
  entry_level_not_usable: "no usable entry level exists",
  entry_not_on_trade_side: "the entry would sit on the wrong side",
  family_mismatch: "the setup family changed mid-plan",
  frame_snapshot_asof_mismatch: "the frame and snapshot instants disagree",
  frozen_range_evidence_missing: "the frozen range evidence is missing",
  future_evidence_used: "evidence from the future would be needed",
  instrument_mismatch: "the instrument changed mid-plan",
  invalid_reference_band: "the reference band is invalid",
  minimum_r_multiple_not_met: "no target reaches the minimum reward-to-risk",
  missing_atr_for_stop_buffer: "the ATR buffer input is missing",
  missing_confirmation_level: "the confirmation level is missing",
  missing_plan_close: "the plan close is missing",
  missing_reference_band: "the reference band is missing",
  no_valid_target_available: "no valid target level exists",
  non_positive_risk: "the risk would not be positive",
  range_entry_outside_frozen_bounds: "the entry would fall outside the frozen range",
  range_followthrough_failed: "range follow-through failed",
  reference_id_mismatch: "the reference changed mid-plan",
  seed_event_missing: "the seed event is missing",
  setup_identity_mismatch: "the setup changed mid-plan",
  setup_not_in_snapshot: "the setup is not in this snapshot",
  setup_not_qualified: "the setup is not qualified",
  stale_qualification: "the qualification is stale",
  stop_non_positive: "the stop would not be positive",
  stop_not_beyond_entry: "the stop would not sit beyond the entry",
  terminal_source_setup: "the source setup already ended",
};

export function planReasonText(code) {
  if (isMissing(code)) return null;
  const text = String(code).trim();
  return PLAN_REASONS[text] || humanizeToken(text).toLowerCase();
}

/** "NO_PLAN: a, b" planner refusals become one plain sentence. */
export function planRefusalSentence(codes) {
  const list = (Array.isArray(codes) ? codes : [])
    .map(planReasonText)
    .filter((text) => typeof text === "string" && text);
  if (!list.length) return "The planner could not build a plan from the available inputs.";
  if (list.length === 1) return `The planner could not build a plan: ${list[0]}.`;
  return `The planner could not build a plan: ${list.slice(0, -1).join("; ")}; and ${list[list.length - 1]}.`;
}

const RUNNER_STATUSES = {
  STARTED: "Started",
  PROCESSED: "Processed",
  IDLE: "Idle",
  NO_DATA: "No data",
  ERROR: "Error",
  STOPPED: "Stopped",
};

export function runnerStatusLabel(status) {
  if (isMissing(status)) return UNKNOWN_TEXT;
  const text = String(status).trim();
  return RUNNER_STATUSES[text] || humanizeToken(text);
}

const OUTCOME_STATUSES = {
  ENTRY_NOT_REACHED: "Entry never reached",
  INVALIDATED_BEFORE_ENTRY: "Invalidated before entry",
  STOPPED: "Stopped",
  STOPPED_AFTER_TARGETS: "Stopped after targets",
  TARGETS_REACHED: "Targets reached",
  OPEN_AT_CUTOFF: "Still open at cutoff",
  AMBIGUOUS: "Ambiguous candle",
  INCOMPLETE_DATA: "Incomplete data",
};

export function outcomeStatusText(status) {
  if (isMissing(status)) return UNKNOWN_TEXT;
  const text = String(status).trim();
  return OUTCOME_STATUSES[text] || humanizeToken(text);
}

export function metricStatusText(status) {
  if (status === "SUFFICIENT_DATA") return "enough data";
  if (status === "INSUFFICIENT_DATA") return "not enough data";
  return humanizeToken(status).toLowerCase();
}

export function decisionText(decision) {
  if (isMissing(decision)) return UNKNOWN_TEXT;
  const text = String(decision).trim();
  return DECISION_LABELS[text] || humanizeToken(text);
}

const LEVEL_SOURCES = {
  unknown: "unknown source",
  step3_volatility_latest_close: "latest close",
  step4_retest_close: "held-retest close",
  step4_breakout_close: "confirmation breakout close",
  step4_sweep_reclaim_close: "sweep reclaim close",
  step4_failure_reentry_close: "failed-breakout re-entry close",
  step4_equal_level_cluster: "equal highs/lows cluster",
  planner_stop_buffer_none: "no buffer (equals invalidation)",
  planner_stop_buffer_atr: "ATR buffer",
  planner_stop_buffer_percentage: "percentage buffer",
  r_multiple_fallback: "fallback projection",
};

/** Plan-level source_type values become short source phrases. */
export function levelSourceText(sourceType) {
  if (isMissing(sourceType)) return "unknown source";
  const text = String(sourceType).trim();
  if (LEVEL_SOURCES[text]) return LEVEL_SOURCES[text];
  const reference = text.match(/^step4_reference_(.+)$/);
  if (reference) return `${referenceTypeText(reference[1])} band`;
  const buffered = text.match(/^planner_stop_buffer_(.+)$/);
  if (buffered) return `${humanizeToken(buffered[1]).toLowerCase()} buffer`;
  return humanizeToken(text).toLowerCase();
}

/**
 * Journal disabled reasons are plain sentences already, except the two Step 6
 * refusal templates ("Step 6 refused a plan (NO_PLAN: a, b); ..."). Those are
 * rebuilt from translated plan codes; anything unparseable falls back to a
 * humanized variant, never raw.
 */
export function disabledReasonText(reason) {
  if (typeof reason !== "string" || !reason.trim()) {
    return "This proposal cannot currently receive a decision.";
  }
  const text = reason.trim();
  const refused = text.match(/^Step 6 refused a plan \((?:NO_PLAN|INVALID):\s*(.*?)\);\s*(.*)$/);
  if (refused) {
    const codes = refused[1].split(",").map((part) => part.trim()).filter(Boolean);
    const tail = (refused[2] || "").trim();
    const tailSentence = tail ? ` ${tail.charAt(0).toUpperCase()}${tail.slice(1)}` : "";
    return `${planRefusalSentence(codes)}${tailSentence}`;
  }
  const invalid = text.match(/^Step 6 produced an INVALID plan \(invariant violation:\s*(.*?)\);\s*(.*)$/);
  if (invalid) {
    const codes = invalid[1].split(",").map((part) => part.trim()).filter(Boolean);
    const what = codes.length ? codes.map(planReasonText).filter(Boolean).join("; ") : "unknown";
    return `The planner produced an invalid plan (invariant violation: ${what}); it is reported, never corrected, and cannot be decided.`;
  }
  return text
    .replace(/Step 6/g, "the planner")
    .replace(/NO_PLAN/g, "no plan")
    .replace(/INVALID/g, "invalid");
}

// ---------------------------------------------------------------------------
// Composed Level-2 paragraphs from structured backend facts
// ---------------------------------------------------------------------------

function trendWord(direction) {
  if (direction === "bullish") return "Bullish";
  if (direction === "bearish") return "Bearish";
  if (direction === "neutral") return "Neutral";
  return null;
}

/**
 * The "doing now" paragraph, composed from the same structured market_state
 * facts the backend's doing_now string uses — so numbers and wording stay
 * identical to the Market-now card. The backend string remains available for
 * the Level-3 technical details.
 */
export function doingNowParagraph(marketState, snapshotState) {
  if (!marketState || typeof marketState !== "object") {
    return "Market-state facts are unavailable from the backend.";
  }
  const parts = [];
  const trend = marketState.trend || {};
  if (trend.sufficient === true) {
    const word = trendWord(trend.direction);
    if (word) {
      parts.push(`Trend is ${word.toLowerCase()} (${trendReasonText(trend.reason)}).`);
    } else {
      parts.push(`Trend direction is unknown (${trendReasonText(trend.reason)}).`);
    }
  } else {
    parts.push(
      `Trend is unknown (${trendReasonText(trend.reason)}); structure is insufficient to classify direction.`,
    );
  }
  const volatility = marketState.volatility || {};
  if (volatility.available === true) {
    const value = roundedOrUnknown(volatility.atr_percent_of_price, 2);
    const label = metricLabelText(volatility.direction?.label);
    if (label === "trend unknown") {
      parts.push(`Volatility is at ATR ${value}% of price (trend unknown: previous close insufficient).`);
    } else {
      parts.push(`Volatility ${label} (ATR ${value}% of price).`);
    }
  } else {
    parts.push(`Volatility is unknown (${unavailableReasonText(volatility.reason)}).`);
  }
  const volume = marketState.volume || {};
  if (volume.sufficient === true) {
    const value = roundedOrUnknown(volume.relative_volume, 2);
    const label = metricLabelText(volume.direction?.label);
    if (label === "trend unknown") {
      parts.push(`Volume is ${value}x its average (trend unknown: previous close insufficient).`);
    } else {
      parts.push(`Volume is ${label} at ${value}x its average.`);
    }
  } else {
    parts.push(`Volume is unknown (${unavailableReasonText(volume.reason)}).`);
  }
  const range = marketState.range || {};
  const detected = range.detected && typeof range.detected === "object" ? range.detected : null;
  if (range.active === true && detected) {
    parts.push(
      `Price is inside an active range ${roundedOrUnknown(detected.range_low, 2)}–${roundedOrUnknown(detected.range_high, 2)}.`,
    );
  } else {
    parts.push("No active range.");
  }
  const breakout = marketState.breakout_state || {};
  const fresh = [];
  if (Array.isArray(breakout.attempts) && breakout.attempts.length) {
    fresh.push(`${breakout.attempts.length} breakout attempt(s)`);
  }
  if (Array.isArray(breakout.acceptances) && breakout.acceptances.length) {
    fresh.push(`${breakout.acceptances.length} acceptance(s)`);
  }
  if (Array.isArray(breakout.rejections) && breakout.rejections.length) {
    fresh.push(`${breakout.rejections.length} rejection(s)`);
  }
  if (Array.isArray(breakout.sweeps) && breakout.sweeps.length) {
    fresh.push(`${breakout.sweeps.length} sweep(s)`);
  }
  if (fresh.length) parts.push(`Fresh at this close: ${fresh.join(", ")}.`);
  parts.push(`Qualification state: ${setupStateLabel(snapshotState).toLowerCase()}.`);
  return parts.join(" ");
}

/** Aggregate bot-seeing line: "Watching 2 developing setup(s) · fully evaluated". */
export function aggregateSeeingLine(seeing) {
  const state = seeing?.state ?? null;
  const status = snapshotStatusText(seeing?.status);
  const count = Number.isInteger(seeing?.live_count) ? seeing.live_count : null;
  const noun = count === 1 ? "developing setup" : "developing setups";
  if (state === "WATCH") {
    return count === null ? `Watching · ${status}` : `Watching ${count} ${noun} · ${status}`;
  }
  if (state === "QUALIFIED") {
    return count === null ? `Qualified · ${status}` : `Qualified: ${count} ${noun} · ${status}`;
  }
  if (state === "NO_SETUP") {
    return `No setup · ${status}`;
  }
  return `${setupStateLabel(state)} · ${status}`;
}

/** Live-setup meta line for the scenario card (all values translated). */
export function liveSetupMetaLine(setup) {
  const bits = [
    setupStateLabel(setup?.state),
    `age ${setup?.age_bars ?? "?"}/${setup?.max_bars ?? "?"} bars`,
    `${setup?.bars_remaining ?? "?"} left`,
  ];
  if (setup?.vetoed === true) {
    const by = Array.isArray(setup.vetoed_by) && setup.vetoed_by.length
      ? setup.vetoed_by.map(shortRuleTitle).join(", ")
      : "unknown check";
    bits.push(`vetoed by ${by}`);
  }
  return bits.join(" · ");
}

/** Invalidate-case meta line (mirrors liveSetupMetaLine minus family/direction). */
export function invalidateMetaLine(item) {
  const bits = [
    setupStateLabel(item?.state),
    `${item?.bars_remaining ?? "?"}/${item?.max_bars ?? "?"} bars left`,
  ];
  if (item?.vetoed === true) {
    const by = Array.isArray(item.vetoed_by) && item.vetoed_by.length
      ? item.vetoed_by.map(shortRuleTitle).join(", ")
      : "unknown check";
    bits.push(`vetoed by ${by}`);
  }
  return bits.join(" · ");
}
