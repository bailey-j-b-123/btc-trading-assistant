/**
 * The panel beside the chart that explains the selected chart function.
 *
 * The approved designs (one per chart function) all pair the chart with a short
 * column of facts: a colour legend, the nearest stored levels, the detected
 * geometry, the ledger of events, or the engine's own plan. This module builds
 * exactly that column, from stored records only.
 *
 * Rules it never breaks
 * ---------------------
 *  * every price, bound, count, state and timestamp is a stored value passed
 *    through unchanged (formatting only);
 *  * no confidence score, probability or "strength" score is ever shown — the
 *    detector stores geometry, and geometry is what is shown;
 *  * a step that the backend did not record (for example a continuation) is
 *    reported as not recorded, never drawn as if it happened;
 *  * the risk/reward figures are the plan's own levels, and the only arithmetic
 *    is the ratio between them, which the approved design asks for explicitly;
 *  * when a function has nothing stored, its own empty sentence is shown
 *    instead of a blank panel or an invented example.
 */

import {
  chartFunctionById,
  eventCatalogue,
  levelCatalogue,
  normalizeFunctionId,
  swingCatalogue,
} from "./chart_functions.js";
import { directionArrow, directionLabel, displayRounded, formatUtc, isMissing } from "./format.js";
import {
  chartPatternRecords,
  chooseChartPattern,
  patternExplanation,
  patternIconPoints,
  patternKeyLevels,
  patternRecordFacts,
  patternStateTitle,
  patternTitle,
  patternTone,
} from "./pattern_view.js";
import { trendReasonText } from "./plain.js";
import { el } from "./util.js";

const UNKNOWN = "UNKNOWN";
const MAX_LEVELS = 4;
const MAX_SWINGS = 6;
const MAX_EVENTS = 6;

/** Sentence-case names for the legacy panel fields (title, list), unchanged. */
const PATTERN_NAMES = Object.freeze({
  double_top: "Double top",
  double_bottom: "Double bottom",
  head_and_shoulders: "Head and shoulders",
  inverse_head_and_shoulders: "Inverse head and shoulders",
  trading_range: "Trading range",
});

const EVENT_KINDS = Object.freeze({
  breakout: "Breakout",
  failed_breakout: "Failed breakout",
  sweep: "Liquidity sweep",
  retest: "Retest",
});

/**
 * A finite number, or null when the value is absent or unparseable.
 *
 * This exists specifically to stop `Number(null)` — which is `0`, not `NaN` — from
 * turning a level the backend never supplied into a real zero. Every plan figure that
 * is compared, subtracted or divided goes through it first.
 */
function numericOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** A price, formatted; a missing or unformattable value stays an explicit UNKNOWN. */
function price(value) {
  if (isMissing(value)) return UNKNOWN;
  const { display } = displayRounded(value, 2);
  return display === null || display === undefined ? UNKNOWN : display;
}

/** A price distance, formatted; an unformattable amount stays an explicit UNKNOWN. */
function displayAmount(value) {
  const { display } = displayRounded(value, 2);
  return display === null || display === undefined ? UNKNOWN : display;
}

/** An instant, formatted; a missing instant stays an explicit UNKNOWN. */
function when(iso) {
  return iso ? `${formatUtc(iso)}` : UNKNOWN;
}

/**
 * The ratio line's text: the plan's own levels divided, the plan's stated R multiple, or
 * UNKNOWN. A stated R multiple is marked "(plan)" so it is never mistaken for the ratio of
 * the two distances shown beside it.
 */
function ratioText(rr) {
  if (rr.ratioBasis === "levels") return `R:R ${rr.ratio}`;
  if (rr.ratioBasis === "stated") return `R:R ${rr.ratio} (plan)`;
  return `R:R ${UNKNOWN}`;
}

function roleLabel(role) {
  if (role === "support") return "Support";
  if (role === "resistance") return "Resistance";
  return "Price inside";
}

function roleTone(role) {
  if (role === "support") return "green";
  if (role === "resistance") return "red";
  // A zone containing price is the amber band on the chart, and its legend row is amber.
  return "amber";
}

function stateTone(state) {
  if (state === "confirmed" || state === "held" || state === "passed") return "green";
  if (state === "invalidated" || state === "failed") return "red";
  if (state === "formed" || state === "swept" || state === "pending") return "amber";
  return "neutral";
}

/** The bands nearest the last confirmed close, split by the side they sit on. */
function nearestBands(bands) {
  const rows = levelCatalogue(bands);
  return {
    all: rows,
    nearest: rows.slice(0, MAX_LEVELS),
    support: rows.find((band) => band.role === "support") || null,
    resistance: rows.find((band) => band.role === "resistance") || null,
  };
}

/**
 * The test figure for a band, stated so a summed evidence count is never read as distinct tests.
 *
 * `24 tests` is only said when 24 separate instants tested the level. When a candle contributed
 * both a swing high and a swing low (which the detector's own source list shows as a repeated
 * instant), the row says `22 tests · 24 swing evidences` instead.
 */
function bandTestText(band) {
  const distinct = band.distinctTestTimestamps;
  const evidences = band.touchCount;
  if (distinct === null && evidences === null) return "tests unknown";
  const tests = distinct === null ? evidences : distinct;
  const plural = tests === 1 ? "" : "s";
  if (evidences !== null && distinct !== null && evidences > distinct) {
    return `${tests} test${plural} · ${evidences} swing evidences`;
  }
  return `${tests} test${plural}`;
}

/** One factual line about the stored events that used this zone as their own reference.
 *
 * An exact match (the event's own recorded band is this band) and a contained reference (a
 * smaller, earlier recorded band inside this band's price area) are stated differently: only the
 * first is identity, and the second keeps the line from reading as "nothing happened here".
 */
function linkedEventText(linkage) {
  if (!linkage || typeof linkage !== "object") return null;
  if (linkage.event_count === null || linkage.counts === null) return null;
  const contained = Number.isFinite(linkage.contained_count) ? linkage.contained_count : 0;
  if (!linkage.linked) {
    return contained > 0
      ? `${contained} stored event(s) inside it, with smaller recorded bounds`
      : "no recorded event used this zone";
  }
  const counts = linkage.counts || {};
  const parts = [
    counts.breakout ? `${counts.breakout} breakout${counts.breakout === 1 ? "" : "s"}` : null,
    counts.failed_breakout ? `${counts.failed_breakout} failed breakout${counts.failed_breakout === 1 ? "" : "s"}` : null,
    counts.sweep ? `${counts.sweep} sweep${counts.sweep === 1 ? "" : "s"}` : null,
    counts.retest ? `${counts.retest} retest${counts.retest === 1 ? "" : "s"}` : null,
  ].filter(Boolean);
  return parts.length ? `referenced by ${parts.join(" · ")}` : "referenced by a stored event";
}

/** Support / Resistance: colour legend, nearest stored bands, honest hidden and test counts. */
function supportResistanceModel({ bands, hidden = null, merged = null, reason = null, accounting = null }) {
  const levels = nearestBands(bands);
  if (reason === "no_zones" || !levels.all.length) {
    const stored = Number.isFinite(accounting?.totalStored) ? Number(accounting.totalStored) : null;
    return {
      heading: "Support & Resistance",
      chip: null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      note: reason === "no_zones"
        ? stored === 0
          ? "No support or resistance zones are stored for this timeframe at this instant."
          : `No zone band is drawable for this timeframe: ${stored ?? "some"} stored zone(s) were unavailable. Nothing is drawn in their place.`
        : "No zone bands are available for this view.",
    };
  }
  // Total not drawn = the backend's own nearest-per-side selection + this chart's readability cap.
  const backendHidden = Number.isFinite(hidden) ? hidden : null;
  const capHidden = Number.isFinite(accounting?.capHidden) ? Number(accounting.capHidden) : 0;
  const totalHidden = backendHidden === null ? null : backendHidden + capHidden;
  return {
    heading: "Support & Resistance",
    chip: null,
    tone: "blue",
    // One row per thing this screen actually draws. The amber band is a stored zone
    // containing the close, and the blue dashed lines are the detected range's own high
    // and low — no midpoint is drawn anywhere, so none is claimed here.
    legend: [
      { label: "Resistance", tone: "red", meaning: "Stored bands above the current close, where selling pressure has historically stopped price." },
      { label: "Price inside", tone: "amber", meaning: "A stored zone that contains the current close: the level price is testing now, not a prediction." },
      { label: "Support", tone: "green", meaning: "Stored bands below the current close, where buying pressure has historically stopped price." },
      { label: "Range", tone: "blue", meaning: "The detected range's own high and low, dashed: the boundaries the detector stored, not a forecast." },
    ],
    facts: [
      { label: "Bands drawn", value: String(levels.nearest.length), tone: "blue" },
      { label: "Further bands not drawn", value: totalHidden === null ? UNKNOWN : String(totalHidden) },
      { label: "Merged for display", value: merged === null ? UNKNOWN : String(merged) },
    ],
    list: levels.nearest.map((band) => ({
      title: roleLabel(band.role),
      value: `${price(band.low)} – ${price(band.high)}`,
      meta: [
        band.timeframe ? `${String(band.timeframe).toUpperCase()} zone` : null,
        bandTestText(band),
        band.faded ? "older, faded" : null,
        band.mergedZoneCount && band.mergedZoneCount > 1 ? `${band.mergedZoneCount} merged` : null,
        linkedEventText(band.linkedEvents),
      ].filter(Boolean).join(" · "),
      tone: roleTone(band.role),
    })),
    note: "Only the nearest active zones are drawn; the count above says how many were not. Bounds and test counts are the detector's own, and a test count counts separate instants, never summed swing evidences.",
  };
}

/** Swing Highs / Lows: structure legend, last swing, bias and the levels around price. */
function swingsModel({ evidence, bands, marketState }) {
  const rows = swingCatalogue(evidence, {
    zones: Array.isArray(evidence?.zones) ? evidence.zones : [],
    patterns: Array.isArray(evidence?.patterns) ? evidence.patterns : [],
  });
  const levels = nearestBands(bands);
  const validTrend = (value) => value && typeof value === "object"
    && ["bullish", "bearish", "neutral"].includes(value.direction);
  // Legacy same-frame fallback requires explicit identity and available evidence.
  // An unavailable chart must never inherit the engine's bias under another label.
  const sameFrame = typeof evidence?.timeframe === "string" && evidence.timeframe.length > 0
    && evidence.timeframe === marketState?.timeframe;
  const trend = evidence?.available !== true ? null
    : validTrend(evidence.trend) ? evidence.trend
      : sameFrame && marketState?.available === true && validTrend(marketState.trend)
        ? marketState.trend : null;
  const latest = rows.reduce((newest, row) => (
    Number.isFinite(row.timeMs) && (!newest || row.timeMs > Number(newest.timeMs)) ? row : newest
  ), null);
  const facts = [
    {
      label: "Last stored swing",
      value: latest ? `${latest.label || UNKNOWN} · ${price(latest.price)}` : UNKNOWN,
      tone: latest && String(latest.label || "").startsWith("H") ? "green" : "red",
    },
    {
      label: "Current bias",
      value: trend ? `${directionLabel(trend.direction)} · ${trendReasonText(trend.reason)}` : UNKNOWN,
      tone: trend?.direction === "bullish" ? "green" : trend?.direction === "bearish" ? "red" : "neutral",
    },
    {
      label: "Key level (support)",
      value: levels.support ? `${price(levels.support.low)} – ${price(levels.support.high)}` : "none stored below price",
      tone: "green",
    },
    {
      label: "Next resistance",
      value: levels.resistance ? `${price(levels.resistance.low)} – ${price(levels.resistance.high)}` : "none stored above price",
      tone: "red",
    },
  ];
  return {
    heading: "Market Structure",
    chip: trend ? directionLabel(trend.direction) : null,
    tone: trend?.direction === "bullish" ? "green" : trend?.direction === "bearish" ? "red" : "neutral",
    legend: [
      { label: "HH", tone: "green", meaning: "Higher high (stronger high)" },
      { label: "HL", tone: "green", meaning: "Higher low (stronger low)" },
      { label: "LH", tone: "red", meaning: "Lower high (weaker high)" },
      { label: "LL", tone: "red", meaning: "Lower low (weaker low)" },
    ],
    facts,
    list: [...rows]
      .sort((left, right) => (Number(right.timeMs) || 0) - (Number(left.timeMs) || 0))
      .slice(0, MAX_SWINGS)
      .map((row) => ({
        title: `${row.label || UNKNOWN} · ${price(row.price)}`,
        value: when(new Date(Number(row.timeMs)).toISOString().replace(".000Z", "Z")),
        meta: row.emphasis === "major"
          ? "referenced by a stored zone or pattern"
          : "independent swing",
        tone: row.kind === "high" ? "green" : "red",
      })),
    note: "Only confirmed swings are marked, at the candle that confirmed them. A swing counts as referenced when a stored zone or pattern is built on it — that is emphasis, not a strength score.",
  };
}

/**
 * Chart Patterns: the selected record, its stored geometry and the other
 * records. The records are the stored pattern records plus the range
 * detector's active range (pattern_view.chartPatternRecords), in the
 * documented default order; nothing else is listed.
 */
function patternsModel({ evidence, selectedPatternId, bands }) {
  const records = chartPatternRecords(evidence);
  const raw = chooseChartPattern(records, selectedPatternId);
  const levels = nearestBands(bands);
  if (!raw) {
    return {
      heading: "Chart Patterns",
      chip: null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      // The approved three-card column keeps its shape when nothing is stored:
      // each card states plainly that there is nothing to show.
      detection: null,
      others: [],
      keyLevels: patternKeyLevels(null),
      recordFacts: [],
      note: "No chart pattern is stored for this window at this instant.",
    };
  }
  const isRange = raw.type === "trading_range";
  const geometry = raw.geometry && typeof raw.geometry === "object" ? raw.geometry : null;
  const componentCount = Array.isArray(raw.components) ? raw.components.length : 0;
  const facts = isRange
    ? [
        { label: "State", value: raw.state || UNKNOWN, tone: "blue" },
        { label: "Known at", value: when(raw.known_at), tone: "neutral" },
      ]
    : [
        { label: "State", value: raw.state || UNKNOWN, tone: stateTone(raw.state) },
        { label: "Known at", value: when(raw.known_at), tone: "neutral" },
        { label: "Neckline", value: isMissing(raw.neckline) ? "not stored" : price(raw.neckline), tone: "blue" },
        { label: "Invalidation level", value: isMissing(raw.invalidation_level) ? "not stored" : price(raw.invalidation_level), tone: "red" },
        { label: "Detected points", value: String(componentCount), tone: "neutral" },
      ];
  const geometryRows = geometry ? [
    { label: "Matching extremes differ by", value: percentText(geometry.matching_extremes_difference_pct) },
    { label: "Pattern depth", value: percentText(geometry.depth_pct) },
    { label: "Head prominence", value: percentText(geometry.head_prominence_pct) },
    { label: "Span", value: Number.isFinite(Number(geometry.span_candles)) ? `${Number(geometry.span_candles)} candles` : UNKNOWN },
  ].filter((row) => row.value !== null) : [];
  const others = records.filter((record) => record.id !== raw.id);
  const name = (record) => patternTitle(record.type, record.label);
  const legacyName = (record) => PATTERN_NAMES[record.type] || record.label || "Pattern";
  return {
    heading: "Chart Patterns",
    chip: raw.state || null,
    tone: stateTone(raw.state),
    title: {
      label: legacyName(raw),
      state: raw.state || UNKNOWN,
      tone: stateTone(raw.state),
    },
    legend: [],
    facts,
    geometry: geometryRows,
    list: others.slice(0, MAX_LEVELS).map((record) => ({
      title: legacyName(record),
      value: record.state || UNKNOWN,
      meta: `${record.is_latest_state === true ? "current state" : "earlier state"} · known ${when(record.known_at)}`,
      tone: stateTone(record.state),
    })),
    levels: [
      isRange ? null : { label: "Neckline", value: isMissing(raw.neckline) ? "not stored" : price(raw.neckline), tone: "blue" },
      levels.resistance ? { label: "Stored level above price", value: `${price(levels.resistance.low)} – ${price(levels.resistance.high)}`, tone: "red" } : null,
      levels.support ? { label: "Stored level below price", value: `${price(levels.support.low)} – ${price(levels.support.high)}`, tone: "green" } : null,
    ].filter(Boolean),
    // --- The approved three-card column (Pattern Detection, Other Detected
    // Patterns, Key Levels). Every value below is the record's own; the score
    // slot states that nothing is calculated, because the backend calculates
    // nothing of the kind.
    detection: {
      id: raw.id,
      type: raw.type || null,
      name: name(raw),
      state: raw.state || null,
      stateTitle: patternStateTitle(raw.state),
      tone: patternTone(raw),
      knownAt: raw.known_at || null,
      source: isRange ? "range detector" : "pattern detector",
      score: { calculated: false, text: "Not calculated" },
      explanation: patternExplanation(raw),
    },
    others: others.map((record) => ({
      id: record.id,
      type: record.type || null,
      name: name(record),
      state: record.state || null,
      stateTitle: patternStateTitle(record.state),
      tone: patternTone(record),
      current: record.is_latest_state === true,
      knownAt: record.known_at || null,
    })),
    keyLevels: patternKeyLevels(raw),
    recordFacts: patternRecordFacts(raw),
    note: "The shape drawn is the detector's own geometry. The detector stores geometry, not a confidence score, so no confidence percentage is shown.",
  };
}

function percentText(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return `${displayRounded(numeric, 2).display}%`;
}

/**
 * Breakout Logic: the three steps of the sequence, in order, each with the
 * status the backend recorded for it.
 *
 * The three steps are always shown — that is the layout — whatever the data
 * says. A step with no stored record keeps its place and reads "Not recorded"
 * or "In Progress"; it is never filled in from price movement.
 */
// One sentence, written for a person: what the whole three-step layout is
// saying about this sequence. It never asserts an outcome the backend has not
// stored, so a continuation that has not been recorded reads as a watch.
const KEY_IDEA_BY_DIRECTION = Object.freeze({
  bullish: "The broken band is now support. The move on from the retest is what is being watched.",
  bearish: "The broken band is now resistance. The move on from the retest is what is being watched.",
});

function breakoutsModel({ evidence, stages }) {
  const sequence = stages && typeof stages === "object" ? stages : null;
  const list = Array.isArray(sequence?.stages) ? sequence.stages : [];
  const direction = sequence?.direction || null;
  const note = sequence?.available === false
    ? `The layout is unchanged: all three steps stay in place. ${sequence.reason ? `Nothing is shown for them because ${sequence.reason}.` : ""}`.trim()
    : "No step is marked complete without a stored record.";
  if (!list.length) {
    return {
      heading: "Breakout Logic",
      chip: null,
      tone: "amber",
      band: null,
      stages: [],
      frameAction: null,
      note: "Chart evidence is unavailable for this view, so no step is recorded.",
    };
  }
  return {
    heading: "Breakout Logic",
    chip: direction ? `${direction === "bearish" ? "Bearish" : "Bullish"} sequence` : null,
    tone: "blue",
    band: sequence.bandText
      ? { label: direction === "bearish" ? "Broken support band" : "Broken resistance band", value: sequence.bandText, tone: direction === "bearish" ? "green" : "red" }
      : null,
    keyIdea: KEY_IDEA_BY_DIRECTION[direction] || KEY_IDEA_BY_DIRECTION.bullish,
    stages: list.map((stage) => ({
      key: stage.key,
      number: stage.number,
      title: stage.title,
      status: stage.status,
      tone: stage.tone,
      detail: stage.detail,
      summary: stage.summary || stage.title,
      place: stage.place,
      nudge: stage.nudge,
      recorded: stage.recorded === true,
      barIndex: Number.isFinite(stage.barIndex) ? stage.barIndex : -1,
      timeText: stage.timeText,
    })),
    frameAction: list.some((stage) => Number.isFinite(stage.barIndex) && stage.barIndex >= 0)
      ? "Frame all three steps"
      : null,
    note,
  };
}

/** Trade Plan: the engine's own levels, and the ratio between them. */
function tradePlanModel({ plan, planState, reason }) {
  const plannable = plan && plan.state === "PLANNABLE";
  if (!plannable) {
    return {
      heading: "Trade Plan",
      chip: planState || null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      note: reason || "No actionable plan right now: the engine has not built one from a qualified setup.",
    };
  }
  const entry = plan.entry?.value ?? null;
  const stop = plan.stop?.value ?? null;
  // The plan carries either a bare value or a {value} object per level.
  const unwrap = (level) => (level && typeof level === "object" ? (level.value ?? null) : (level ?? null));
  const invalidationValue = unwrap(plan.invalidation);
  const targets = Array.isArray(plan.targets) ? plan.targets : [];
  const facts = [
    { label: "Direction", value: `${directionLabel(plan.direction)} ${directionArrow(plan.direction)}`, tone: plan.direction === "bullish" ? "green" : plan.direction === "bearish" ? "red" : "neutral" },
    { label: "Entry", value: price(entry), tone: "blue" },
    { label: "Stop", value: price(stop), tone: "red" },
    // A plan whose invalidation value is absent shows UNKNOWN here, exactly as
    // the plan record beside it does; "not stored" is only used where the
    // backend may legitimately omit a field entirely (a pattern's neckline).
    { label: "Invalidation", value: price(invalidationValue), tone: "amber" },
    ...targets.map((target, index) => ({
      label: `Target ${index + 1}`,
      value: price(target?.level?.value),
      tone: "green",
      meta: isMissing(target?.r_multiple) ? null : `${displayRounded(target.r_multiple, 2).display}R from entry`,
    })),
  ];
  // Risk and reward are DISTANCES in price, measured from entry: the amount the
  // plan risks is how far entry sits from the stop, and the amount it targets is
  // how far entry sits from the final target. The absolute entry, stop and target
  // prices keep their own fields above; these two figures are the plan's own
  // levels, subtracted — no other arithmetic happens here.
  //
  // Every one of these reads goes through numericOrNull, because Number(null) is 0:
  // a level or an r_multiple the backend never supplied would otherwise be measured
  // as a real zero — a distance to price 0, or an R:R of "0.00 : 1". An absent value
  // stays absent here.
  const finalTarget = targets.length ? targets[targets.length - 1] : null;
  const entryNumber = numericOrNull(entry);
  const stopNumber = numericOrNull(stop);
  const lastTargetValue = numericOrNull(finalTarget?.level?.value);
  const risk = entryNumber !== null && stopNumber !== null ? Math.abs(entryNumber - stopNumber) : null;
  const reward = entryNumber !== null && lastTargetValue !== null ? Math.abs(lastTargetValue - entryNumber) : null;
  // The ratio of the two distances above: the plan's own levels, divided. Preferred
  // whenever both distances are measurable, so the ratio always agrees with the two
  // figures shown beside it.
  const levelRatio = risk !== null && reward !== null && risk > 0 ? reward / risk : null;
  // Otherwise the plan's own R multiple for the FINAL target — the same target whose
  // distance appears above, so the two can never describe different trades. A missing
  // r_multiple is never read as zero, and a non-positive one is not a ratio.
  const statedRatioRaw = numericOrNull(finalTarget?.r_multiple);
  const statedRatio = statedRatioRaw !== null && statedRatioRaw > 0 ? statedRatioRaw : null;
  // Neither available: the ratio is UNKNOWN. It is never defaulted to 0.
  const ratio = levelRatio ?? statedRatio;
  const targetLabel = `T${targets.length}`;
  return {
    heading: "Trade Plan",
    chip: plan.state,
    tone: "blue",
    legend: [],
    facts,
    list: [],
    // Shown whenever a plan is displayed, so a figure that cannot be computed is
    // stated as UNKNOWN rather than silently dropped.
    rr: {
      risk: risk === null ? UNKNOWN : displayAmount(risk),
      reward: reward === null ? UNKNOWN : displayAmount(reward),
      targetLabel,
      ratio: ratio === null ? UNKNOWN : `${displayRounded(ratio, 2).display} : 1`,
      ratioBasis: levelRatio !== null ? "levels" : statedRatio !== null ? "stated" : null,
      // No bar without both distances: a proportion needs two numbers.
      riskShare: risk !== null && reward !== null && risk + reward > 0 ? risk / (risk + reward) : null,
    },
    note: "Hypothetical paper levels from the engine's own plan. Risk and reward are entry-to-stop and entry-to-target distances in price. The ratio is the reward distance over the risk distance, or the plan's own R multiple when the distances cannot be measured; where neither is available it reads UNKNOWN. Nothing here is an order, and levels appear only while the engine supplies a valid plan.",
  };
}

/**
 * Build the panel model for one chart function. Pure: no DOM, no fetching, and
 * no value that is not already in the arguments.
 */
export function functionPanelModel({
  functionId,
  evidence = null,
  bands = [],
  hidden = null,
  merged = null,
  reason = null,
  accounting = null,
  plan = null,
  planState = null,
  planReason = null,
  marketState = null,
  selectedPatternId = null,
  stages = null,
} = {}) {
  const fn = chartFunctionById(normalizeFunctionId(functionId)) || null;
  if (!fn) return null;
  if (fn.id === "support_resistance") {
    return { id: fn.id, ...supportResistanceModel({ bands, hidden, merged, reason, accounting }) };
  }
  if (fn.id === "swings") return { id: fn.id, ...swingsModel({ evidence, bands, marketState }) };
  if (fn.id === "patterns") return { id: fn.id, ...patternsModel({ evidence, selectedPatternId, bands }) };
  if (fn.id === "breakouts") return { id: fn.id, ...breakoutsModel({ evidence, stages }) };
  return { id: fn.id, ...tradePlanModel({ plan, planState, reason: planReason }) };
}

/**
 * One step of the sequence. With `onSelect` and a candle of its own it becomes a
 * button that brings that candle into view; without one it stays plain text.
 */
function stageNode(stage, onSelect = null) {
  const inner = [
    el("span", { class: "cfp-stage-num", text: String(stage.number) }),
    el("span", { class: "cfp-stage-body" }, [
      el("span", { class: "cfp-stage-title", text: stage.title }),
      // Simple mode shows one short sentence. The stored numbers, prices and
      // times stay on the row's tooltip and in Detailed mode / Diagnostics.
      el("span", { class: "cfp-stage-summary", text: stage.summary || stage.title }),
    ]),
    // Statuses are right-aligned pills, exactly as the layout shows them.
    el("span", { class: "cfp-stage-status", dataset: { tone: stage.tone || "neutral" }, text: stage.status }),
  ];
  const attrs = {
    class: "cfp-stage",
    dataset: { stage: stage.key, tone: stage.tone || "neutral", recorded: String(stage.recorded === true) },
  };
  const jumpable = typeof onSelect === "function" && Number.isFinite(stage.barIndex) && stage.barIndex >= 0;
  if (!jumpable) return el("li", attrs, inner);
  return el("li", attrs, [
    el("button", {
      type: "button",
      class: "cfp-stage-button",
      "data-stage": stage.key,
      title: `${stage.detail} — bring this candle into view (your zoom is kept)`,
      onclick: () => onSelect(stage),
    }, inner),
  ]);
}

function rowNode(row, className) {
  return el("li", { class: className, dataset: row.tone ? { tone: row.tone } : {} }, [
    el("span", { class: `${className}-title`, text: row.title }),
    row.value ? el("span", { class: `${className}-value`, text: row.value }) : null,
    row.meta ? el("span", { class: `${className}-meta`, text: row.meta }) : null,
  ]);
}

/** The panel node. Plain DOM built from the model above. */
export function functionPanel(model, {
  onSelectStage = null,
  onFrameStages = null,
  onSelectPattern = null,
  onFitPattern = null,
  mode = "simple",
} = {}) {
  if (!model) return el("aside", { class: "chart-function-panel", hidden: true });
  if (model.id === "patterns" && "detection" in model) {
    return patternPanel(model, { onSelectPattern, onFitPattern, mode });
  }
  const body = [];
  if (model.title) {
    body.push(el("div", { class: "cfp-title" }, [
      el("span", { class: "cfp-title-name", text: model.title.label }),
      el("span", { class: "cfp-state", dataset: { tone: model.title.tone }, text: model.title.state }),
    ]));
  }
  if (model.legend?.length) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Legend" }),
      el("ul", { class: "cfp-legend" }, model.legend.map((item) => el("li", { class: "cfp-legend-row", dataset: { tone: item.tone } }, [
        el("span", { class: "cfp-swatch", "aria-hidden": "true" }),
        el("span", { class: "cfp-legend-label", text: item.label }),
        el("span", { class: "cfp-legend-meaning", text: item.meaning }),
      ]))),
    ]));
  }
  if (model.facts?.length) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Facts" }),
      el("dl", { class: "cfp-facts" }, model.facts.flatMap((fact) => [
        el("dt", { class: "cfp-fact-label", text: fact.label }),
        el("dd", { class: "cfp-fact-value", dataset: fact.tone ? { tone: fact.tone } : {} }, [
          el("span", { text: fact.value }),
          fact.meta ? el("span", { class: "cfp-fact-meta", text: fact.meta }) : null,
        ]),
      ])),
    ]));
  }
  if (model.geometry?.length) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Detected geometry" }),
      el("dl", { class: "cfp-facts" }, model.geometry.flatMap((fact) => [
        el("dt", { class: "cfp-fact-label", text: fact.label }),
        el("dd", { class: "cfp-fact-value" }, [el("span", { text: fact.value })]),
      ])),
    ]));
  }
  if (model.rr) {
    // The bar states a proportion, so it is drawn only when both distances are known.
    // Without them the block still appears and says UNKNOWN, which is more honest than
    // a bar at an arbitrary fill or a missing block that implies nothing to report.
    const bar = model.rr.riskShare === null
      ? null
      : el("div", {
          class: "cfp-rr-bar",
          role: "img",
          "aria-label": `Entry-to-stop risk ${model.rr.risk}, entry-to-target reward ${model.rr.reward}, ratio ${model.rr.ratio}`,
        }, [
          el("span", { class: "cfp-rr-risk", style: { width: `${Math.round(Math.min(Math.max(model.rr.riskShare, 0), 1) * 100)}%` } }),
          el("span", { class: "cfp-rr-reward", style: { width: `${100 - Math.round(Math.min(Math.max(model.rr.riskShare, 0), 1) * 100)}%` } }),
        ]);
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Risk / Reward" }),
      bar,
      el("div", { class: "cfp-rr-legend" }, [
        el("span", { class: "cfp-rr-risk-label", text: `Risk ${model.rr.risk}` }),
        el("span", { class: "cfp-rr-reward-label", text: `Reward (${model.rr.targetLabel}) ${model.rr.reward}` }),
      ]),
      el("div", { class: "cfp-rr-caption", text: "Distances in price, measured from entry" }),
      // The ratio says where it came from when it is the plan's own R multiple rather
      // than the two distances above, so the two can never be read as contradicting.
      el("div", {
        class: "cfp-rr-ratio",
        dataset: model.rr.ratioBasis ? { basis: model.rr.ratioBasis } : {},
        text: ratioText(model.rr),
      }),
    ]));
  }
  if (model.band) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Reference band" }),
      el("dl", { class: "cfp-facts" }, [
        el("dt", { class: "cfp-fact-label", text: model.band.label }),
        el("dd", { class: "cfp-fact-value", dataset: { tone: model.band.tone }, text: model.band.value }),
      ]),
    ]));
  }
  if (model.stages?.length) {
    // No nested heading: "Breakout Logic" already heads the card, and the
    // numbered circles joined by a line read as steps on their own.
    body.push(el("ol", { class: "cfp-stages" }, model.stages.map((stage) => stageNode(stage, onSelectStage))));
  }
  if (model.keyIdea) {
    body.push(el("div", { class: "cfp-block cfp-key-idea" }, [
      el("div", { class: "cfp-block-title", text: "Key Idea" }),
      el("p", { class: "cfp-key-idea-text", text: model.keyIdea }),
    ]));
  }
  if (model.levels?.length) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Key levels" }),
      el("dl", { class: "cfp-facts" }, model.levels.flatMap((fact) => [
        el("dt", { class: "cfp-fact-label", text: fact.label }),
        el("dd", { class: "cfp-fact-value", dataset: { tone: fact.tone }, text: fact.value }),
      ])),
    ]));
  }
  if (model.list?.length) {
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Stored records" }),
      el("ul", { class: "cfp-list" }, model.list.map((row) => rowNode(row, "cfp-row"))),
    ]));
  }
  if (model.frameAction && typeof onFrameStages === "function") {
    body.push(el("div", { class: "cfp-block" }, [
      el("button", {
        type: "button",
        class: "cfp-action",
        "data-frame-stages": "true",
        text: model.frameAction,
        onclick: () => onFrameStages(),
      }),
    ]));
  }
  if (model.note) body.push(el("p", { class: "cfp-note", text: model.note }));
  return el("aside", {
    class: "chart-function-panel",
    "aria-label": "Chart function details",
    dataset: { tone: model.tone || "neutral", function: model.id },
  }, [
    el("div", { class: "cfp-head" }, [
      el("h3", { class: "cfp-heading", text: model.heading }),
      model.chip ? el("span", { class: "cfp-chip", text: model.chip }) : null,
    ]),
    ...body,
  ]);
}

// ---------------------------------------------------------------------------
// Chart Patterns: the approved three-card column
//
//   1. Pattern Detection   — icon, name, lifecycle badge, the score slot
//                            ("Not calculated"), a short explanation;
//   2. Other Detected Patterns — one selectable row per other stored record;
//   3. Key Levels          — Neckline, Support, Resistance from the record.
//
// Plain DOM built from patternsModel above; nothing here computes a value.
// ---------------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";

/** An SVG node when the document can make one; a plain span otherwise (tests). */
function svgNode(tag, attrs = {}, children = []) {
  if (typeof document === "undefined" || typeof document.createElementNS !== "function") {
    return el("span", { class: `svg-fallback svg-${tag}`, "aria-hidden": "true" });
  }
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  for (const child of children) if (child) node.appendChild(child);
  return node;
}

/** A pictogram of the pattern TYPE (not of the record). */
function patternIcon(type, className) {
  const points = patternIconPoints(type);
  return svgNode("svg", {
    class: className,
    viewBox: "0 0 100 60",
    "aria-hidden": "true",
    focusable: "false",
  }, [
    svgNode("polyline", {
      points: points.map(([x, y]) => `${x},${y}`).join(" "),
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 5,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    }),
  ]);
}

const CARD_ICONS = Object.freeze({
  pulse: () => svgNode("svg", { class: "pd-head-icon", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" }, [
    svgNode("polyline", {
      points: "1,12 6,12 8.5,5 11.5,19 14.5,8 16.5,15 18,12 23,12",
      fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round",
    }),
  ]),
  radar: () => svgNode("svg", { class: "pd-head-icon", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" }, [
    svgNode("circle", { cx: 12, cy: 12, r: 8.5, fill: "none", stroke: "currentColor", "stroke-width": 1.8 }),
    svgNode("circle", { cx: 12, cy: 12, r: 3.2, fill: "currentColor" }),
  ]),
  target: () => svgNode("svg", { class: "pd-head-icon", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" }, [
    svgNode("circle", { cx: 12, cy: 12, r: 9, fill: "none", stroke: "currentColor", "stroke-width": 1.7 }),
    svgNode("circle", { cx: 12, cy: 12, r: 5, fill: "none", stroke: "currentColor", "stroke-width": 1.7 }),
    svgNode("circle", { cx: 12, cy: 12, r: 1.6, fill: "currentColor" }),
  ]),
});

function cardHead(icon, title, extra = null) {
  return el("div", { class: "pd-card-head" }, [
    CARD_ICONS[icon](),
    el("h3", { class: "pd-card-title", text: title }),
    extra,
  ]);
}

function detectionCard(model, { onFitPattern, mode }) {
  const detection = model.detection;
  const fit = detection && typeof onFitPattern === "function"
    ? el("button", {
        type: "button",
        class: "pd-fit",
        "data-fit-pattern": "true",
        title: "Fit the whole selected pattern into the chart. This is the one control here that changes the zoom.",
        text: "Fit to chart",
        onclick: () => onFitPattern(),
      })
    : null;
  if (!detection) {
    return el("section", { class: "pd-card pd-card--detection", "aria-label": "Pattern detection", dataset: { empty: "true" } }, [
      cardHead("pulse", "Pattern Detection"),
      el("div", { class: "pd-body" }, [
        el("p", { class: "pd-empty", text: model.note || "No chart pattern is stored for this window at this instant." }),
      ]),
    ]);
  }
  const body = [
    el("div", { class: "pd-selected" }, [
      el("div", { class: "pd-icon-tile", dataset: { tone: detection.tone } }, [patternIcon(detection.type, "pd-icon")]),
      el("div", { class: "pd-selected-text" }, [
        el("div", { class: "pd-name", text: detection.name }),
        el("span", {
          class: "pd-badge",
          dataset: { tone: detection.tone, state: detection.state || "unknown" },
          title: detection.knownAt ? `Lifecycle state recorded by the backend · known ${formatUtc(detection.knownAt)}` : "Lifecycle state recorded by the backend",
          text: detection.stateTitle,
        }),
      ]),
    ]),
    // The score slot stays where the approved design puts it. The backend does
    // not calculate a score, so the bar is empty and the value says so.
    el("div", { class: "pd-score", dataset: { calculated: String(detection.score.calculated === true) } }, [
      el("div", { class: "pd-score-row" }, [
        el("span", { class: "pd-score-label", text: "Confidence" }),
        el("span", { class: "pd-score-value", text: detection.score.text }),
      ]),
      el("div", {
        class: "pd-score-track",
        role: "img",
        "aria-label": "Not calculated: the backend does not calculate a pattern score",
        title: "The backend stores the pattern's geometry and lifecycle state only; it does not calculate a score.",
      }),
    ]),
    el("div", { class: "pd-explain" }, [
      el("div", { class: "pd-explain-title", text: "Explanation" }),
      el("p", { class: "pd-explain-text", text: detection.explanation || "" }),
    ]),
  ];
  if (mode === "detailed") {
    // Detailed keeps the existing stored-record access: state, times, levels,
    // the detector's own geometry and every stored point.
    body.push(el("div", { class: "pd-record" }, [
      el("div", { class: "pd-explain-title", text: "Stored record" }),
      el("dl", { class: "pd-record-facts" }, [...(model.facts || []), ...(model.geometry || []), ...(model.recordFacts || [])]
        .flatMap((fact) => [
          el("dt", { text: fact.label }),
          el("dd", { dataset: fact.tone ? { tone: fact.tone } : {}, text: fact.value }),
        ])),
    ]));
  }
  return el("section", { class: "pd-card pd-card--detection", "aria-label": "Pattern detection", dataset: { tone: detection.tone } }, [
    cardHead("pulse", "Pattern Detection", fit),
    el("div", { class: "pd-body" }, body),
  ]);
}

function othersCard(model, { onSelectPattern, mode }) {
  // Simple: one row per other pattern's CURRENT state. Detailed: every other
  // stored state as well, each marked as the earlier state it is.
  const rows = (model.others || []).filter((row) => mode === "detailed" || row.current);
  const list = rows.length
    ? el("ul", { class: "pd-others" }, rows.map((row) => {
        const inner = [
          patternIcon(row.type, "pd-row-icon"),
          el("span", { class: "pd-row-name" }, [
            el("span", { class: "pd-row-title", text: row.name }),
            row.current ? null : el("span", { class: "pd-row-meta", text: "earlier state" }),
          ]),
          el("span", { class: "pd-badge pd-badge--small", dataset: { tone: row.tone, state: row.state || "unknown" }, text: row.stateTitle }),
          el("span", { class: "pd-row-chevron", "aria-hidden": "true", text: "\u203a" }),
        ];
        const title = `${row.name} · ${row.stateTitle}${row.current ? "" : " (earlier state)"} · known ${row.knownAt ? formatUtc(row.knownAt) : UNKNOWN}. Select to draw this stored record.`;
        return el("li", { class: "pd-other", dataset: { tone: row.tone } }, [
          typeof onSelectPattern === "function"
            ? el("button", {
                type: "button",
                class: "pd-other-button",
                "data-select-pattern": row.id,
                title,
                "aria-label": `Show ${row.name}, ${row.stateTitle}`,
                onclick: () => onSelectPattern(row.id),
              }, inner)
            : el("div", { class: "pd-other-button", title }, inner),
        ]);
      }))
    : el("p", { class: "pd-empty", text: "No other patterns recorded" });
  return el("section", { class: "pd-card pd-card--others", "aria-label": "Other detected patterns" }, [
    cardHead("radar", "Other Detected Patterns"),
    list,
  ]);
}

function keyLevelsCard(model) {
  return el("section", { class: "pd-card pd-card--levels", "aria-label": "Key levels" }, [
    cardHead("target", "Key Levels"),
    el("dl", { class: "pd-levels" }, (model.keyLevels || []).map((row) => el("div", {
      class: "pd-level",
      dataset: { level: row.key, available: String(row.available === true), tone: row.tone || "neutral" },
      title: row.detail || "",
    }, [
      el("dt", { class: "pd-level-label" }, [
        el("span", { text: row.label }),
        " ",
        el("span", { class: "pd-level-qualifier", text: row.qualifier }),
      ]),
      el("dd", { class: "pd-level-value", text: row.value }),
    ]))),
  ]);
}

/** The approved Chart Patterns column: three stacked cards. */
export function patternPanel(model, { onSelectPattern = null, onFitPattern = null, mode = "simple" } = {}) {
  return el("aside", {
    class: "chart-function-panel pattern-panel",
    "aria-label": "Chart pattern details",
    dataset: { tone: model.tone || "neutral", function: "patterns", mode },
  }, [
    detectionCard(model, { onFitPattern, mode }),
    othersCard(model, { onSelectPattern, mode }),
    keyLevelsCard(model),
  ]);
}
