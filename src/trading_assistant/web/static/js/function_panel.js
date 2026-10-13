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
  highlightedPattern,
  levelCatalogue,
  normalizeFunctionId,
  patternCatalogue,
  swingCatalogue,
} from "./chart_functions.js";
import { directionArrow, directionLabel, displayRounded, formatUtc, isMissing } from "./format.js";
import { trendReasonText } from "./plain.js";
import { el } from "./util.js";

const UNKNOWN = "UNKNOWN";
const MAX_LEVELS = 4;
const MAX_SWINGS = 6;
const MAX_EVENTS = 6;

const PATTERN_NAMES = Object.freeze({
  double_top: "Double top",
  double_bottom: "Double bottom",
  head_and_shoulders: "Head and shoulders",
  inverse_head_and_shoulders: "Inverse head and shoulders",
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
  return "blue";
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

/** Support / Resistance: colour legend, nearest stored bands, hidden counts. */
function supportResistanceModel({ bands, hidden = null, merged = null, reason = null }) {
  const levels = nearestBands(bands);
  if (reason === "no_zones" || !levels.all.length) {
    return {
      heading: "Support & Resistance",
      chip: null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      note: reason === "no_zones"
        ? "No support or resistance zones are stored for this timeframe at this instant."
        : "No zone bands are available for this view.",
    };
  }
  return {
    heading: "Support & Resistance",
    chip: null,
    tone: "blue",
    legend: [
      { label: "Resistance", tone: "red", meaning: "Stored bands above the current close, where selling pressure has historically stopped price." },
      { label: "Range mid", tone: "blue", meaning: "Midpoint of a detected range: a decision zone, not a prediction." },
      { label: "Support", tone: "green", meaning: "Stored bands below the current close, where buying pressure has historically stopped price." },
    ],
    facts: [
      { label: "Bands drawn", value: String(levels.nearest.length), tone: "blue" },
      { label: "Further bands hidden", value: hidden === null ? UNKNOWN : String(hidden) },
      { label: "Merged for display", value: merged === null ? UNKNOWN : String(merged) },
    ],
    list: levels.nearest.map((band) => ({
      title: roleLabel(band.role),
      value: `${price(band.low)} – ${price(band.high)}`,
      meta: [
        band.timeframe ? `${String(band.timeframe).toUpperCase()} zone` : null,
        band.touchCount === null ? "touches unknown" : `${band.touchCount} touch${band.touchCount === 1 ? "" : "es"}`,
        band.faded ? "older, faded" : null,
        band.mergedZoneCount && band.mergedZoneCount > 1 ? `${band.mergedZoneCount} merged` : null,
      ].filter(Boolean).join(" · "),
      tone: roleTone(band.role),
    })),
    note: "Only the nearest active zones are drawn. Price bounds and touch counts are the detector's own; nothing is invented, moved or re-labelled.",
  };
}

/** Swing Highs / Lows: structure legend, last swing, bias and the levels around price. */
function swingsModel({ evidence, bands, marketState }) {
  const rows = swingCatalogue(evidence, {
    zones: Array.isArray(evidence?.zones) ? evidence.zones : [],
    patterns: Array.isArray(evidence?.patterns) ? evidence.patterns : [],
  });
  const levels = nearestBands(bands);
  const trend = marketState?.available === true ? marketState.trend || null : null;
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

/** Chart Patterns: the selected shape, its stored geometry and the other states. */
function patternsModel({ evidence, selectedPatternId, bands }) {
  const catalogue = patternCatalogue(evidence?.available === true ? evidence : null);
  const chosen = highlightedPattern(evidence, selectedPatternId);
  const levels = nearestBands(bands);
  if (!chosen) {
    return {
      heading: "Chart Patterns",
      chip: null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      note: "No chart pattern is stored for this window at this instant.",
    };
  }
  const raw = (Array.isArray(evidence?.patterns) ? evidence.patterns : [])
    .find((pattern) => pattern?.id === chosen.id) || {};
  const geometry = raw.geometry && typeof raw.geometry === "object" ? raw.geometry : null;
  const facts = [
    { label: "State", value: chosen.state || UNKNOWN, tone: stateTone(chosen.state) },
    { label: "Known at", value: when(chosen.knownAt), tone: "neutral" },
    { label: "Neckline", value: isMissing(chosen.neckline) ? "not stored" : price(chosen.neckline), tone: "blue" },
    { label: "Invalidation level", value: isMissing(chosen.invalidation) ? "not stored" : price(chosen.invalidation), tone: "red" },
    { label: "Detected points", value: String(chosen.componentCount), tone: "neutral" },
  ];
  const geometryRows = geometry ? [
    { label: "Matching extremes differ by", value: percentText(geometry.matching_extremes_difference_pct) },
    { label: "Pattern depth", value: percentText(geometry.depth_pct) },
    { label: "Span", value: Number.isFinite(Number(geometry.span_candles)) ? `${Number(geometry.span_candles)} candles` : UNKNOWN },
  ].filter((row) => row.value !== null) : [];
  return {
    heading: "Chart Patterns",
    chip: chosen.state || null,
    tone: stateTone(chosen.state),
    title: {
      label: PATTERN_NAMES[chosen.type] || chosen.label || "Pattern",
      state: chosen.state || UNKNOWN,
      tone: stateTone(chosen.state),
    },
    legend: [],
    facts,
    geometry: geometryRows,
    list: catalogue
      .filter((entry) => entry.id !== chosen.id)
      .slice(0, MAX_LEVELS)
      .map((entry) => ({
        title: PATTERN_NAMES[entry.type] || entry.label || "Pattern",
        value: entry.state || UNKNOWN,
        meta: `${entry.isLatestState ? "current state" : "earlier state"} · known ${when(entry.knownAt)}`,
        tone: stateTone(entry.state),
      })),
    levels: [
      { label: "Neckline", value: isMissing(chosen.neckline) ? "not stored" : price(chosen.neckline), tone: "blue" },
      levels.resistance ? { label: "Stored level above price", value: `${price(levels.resistance.low)} – ${price(levels.resistance.high)}`, tone: "red" } : null,
      levels.support ? { label: "Stored level below price", value: `${price(levels.support.low)} – ${price(levels.support.high)}`, tone: "green" } : null,
    ].filter(Boolean),
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
function breakoutsModel({ evidence, stages }) {
  const sequence = stages && typeof stages === "object" ? stages : null;
  const list = Array.isArray(sequence?.stages) ? sequence.stages : [];
  const direction = sequence?.direction || null;
  const note = sequence?.available === false
    ? `The layout is unchanged: all three steps stay in place. ${sequence.reason ? `Nothing is shown for them because ${sequence.reason}.` : ""}`.trim()
    : "Every status above is a stored record. Continuation stays In Progress until the backend records one — it is never marked complete because price moved.";
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
    stages: list.map((stage) => ({
      key: stage.key,
      number: stage.number,
      title: stage.title,
      status: stage.status,
      tone: stage.tone,
      detail: stage.detail,
      meta: stage.meta,
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
    return { id: fn.id, ...supportResistanceModel({ bands, hidden, merged, reason }) };
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
      el("span", { class: "cfp-stage-detail", text: stage.detail }),
      stage.meta ? el("span", { class: "cfp-stage-meta", text: stage.meta }) : null,
    ]),
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
      title: `Bring ${stage.title}'s candle into view (your zoom is kept)`,
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
export function functionPanel(model, { onSelectStage = null, onFrameStages = null } = {}) {
  if (!model) return el("aside", { class: "chart-function-panel", hidden: true });
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
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Steps" }),
      el("ol", { class: "cfp-stages" }, model.stages.map((stage) => stageNode(stage, onSelectStage))),
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
