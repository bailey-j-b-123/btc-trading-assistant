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

/** A price, formatted; a missing or unformattable value stays an explicit UNKNOWN. */
function price(value) {
  if (isMissing(value)) return UNKNOWN;
  const { display } = displayRounded(value, 2);
  return display === null || display === undefined ? UNKNOWN : display;
}

/** An instant, formatted; a missing instant stays an explicit UNKNOWN. */
function when(iso) {
  return iso ? `${formatUtc(iso)}` : UNKNOWN;
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

/** Breakouts / Retests: the stored event sequence, with the backend's own states. */
function breakoutsModel({ evidence }) {
  const events = eventCatalogue(evidence?.available === true ? evidence : null);
  if (!events.length) {
    return {
      heading: "Breakouts & Retests",
      chip: null,
      tone: "amber",
      legend: [],
      facts: [],
      list: [],
      note: evidence?.available === true
        ? "No breakout, sweep or retest is stored in the recent part of this window."
        : "Chart evidence is unavailable for this view, so nothing is drawn in its place.",
    };
  }
  const kinds = new Set(events.map((event) => event.kind));
  return {
    heading: "Breakouts & Retests",
    chip: `${events.length} stored event${events.length === 1 ? "" : "s"}`,
    tone: "blue",
    legend: [...kinds].map((kind) => ({
      label: EVENT_KINDS[kind] || kind,
      tone: kind === "breakout" ? "green" : kind === "failed_breakout" ? "red" : kind === "sweep" ? "amber" : "blue",
      meaning: kind === "breakout"
        ? "A stored close beyond a stored reference band, with the confirmation the backend recorded."
        : kind === "failed_breakout"
          ? "A stored breakout that price closed back through."
          : kind === "sweep"
            ? "A stored move beyond a band that was reclaimed in the same candle."
            : "A stored return to the broken band; the backend records whether it held.",
    })),
    facts: [],
    list: events.slice(0, MAX_EVENTS).map((event) => ({
      title: `${EVENT_KINDS[event.kind] || event.kind}${event.direction ? ` · ${directionLabel(event.direction)} ${directionArrow(event.direction)}` : ""}`,
      value: event.state || UNKNOWN,
      meta: [
        when(event.knownAt),
        event.bandLow !== null && event.bandHigh !== null ? `band ${price(event.bandLow)} – ${price(event.bandHigh)}` : null,
        event.confirmationCandles === null ? null : `${event.confirmationCandles} confirmation candle${event.confirmationCandles === 1 ? "" : "s"}`,
      ].filter(Boolean).join(" · "),
      tone: event.kind === "failed_breakout" ? "red" : stateTone(event.state),
    })),
    note: "Every step shown was recorded by the backend, at the candle it was detected on. A continuation is drawn only when the backend records one — it never appears on its own.",
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
  const ratios = targets.map((target) => Number(target?.r_multiple)).filter((value) => Number.isFinite(value));
  const planRatio = ratios.length ? ratios[ratios.length - 1] : null;
  // The ratio the plan itself states when it states one; otherwise the exact
  // levels the plan carries. Either way it is the plan's own numbers, never a
  // figure invented here.
  const risk = Number.isFinite(Number(entry)) && Number.isFinite(Number(stop)) ? Math.abs(Number(entry) - Number(stop)) : null;
  const lastTarget = targets.length ? Number(targets[targets.length - 1]?.level?.value) : null;
  const reward = Number.isFinite(Number(entry)) && Number.isFinite(lastTarget) ? Math.abs(lastTarget - Number(entry)) : null;
  const ratio = planRatio ?? (risk && reward ? reward / risk : null);
  return {
    heading: "Trade Plan",
    chip: plan.state,
    tone: "blue",
    legend: [],
    facts,
    list: [],
    rr: risk !== null && reward !== null && ratio !== null
      ? {
          risk: price(stop),
          reward: price(lastTarget),
          ratio: `${displayRounded(ratio, 2).display} : 1`,
          riskShare: risk + reward > 0 ? risk / (risk + reward) : 0.5,
        }
      : null,
    note: "Hypothetical paper levels from the engine's own plan, with the ratio the plan itself carries. Nothing here is an order, and levels appear only while the engine supplies a valid plan.",
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
} = {}) {
  const fn = chartFunctionById(normalizeFunctionId(functionId)) || null;
  if (!fn) return null;
  if (fn.id === "support_resistance") {
    return { id: fn.id, ...supportResistanceModel({ bands, hidden, merged, reason }) };
  }
  if (fn.id === "swings") return { id: fn.id, ...swingsModel({ evidence, bands, marketState }) };
  if (fn.id === "patterns") return { id: fn.id, ...patternsModel({ evidence, selectedPatternId, bands }) };
  if (fn.id === "breakouts") return { id: fn.id, ...breakoutsModel({ evidence }) };
  return { id: fn.id, ...tradePlanModel({ plan, planState, reason: planReason }) };
}

function rowNode(row, className) {
  return el("li", { class: className, dataset: row.tone ? { tone: row.tone } : {} }, [
    el("span", { class: `${className}-title`, text: row.title }),
    row.value ? el("span", { class: `${className}-value`, text: row.value }) : null,
    row.meta ? el("span", { class: `${className}-meta`, text: row.meta }) : null,
  ]);
}

/** The panel node. Plain DOM built from the model above. */
export function functionPanel(model) {
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
    const share = Math.round(Math.min(Math.max(model.rr.riskShare, 0), 1) * 100);
    body.push(el("div", { class: "cfp-block" }, [
      el("div", { class: "cfp-block-title", text: "Risk / Reward" }),
      el("div", { class: "cfp-rr-bar", role: "img", "aria-label": `Risk ${model.rr.risk}, reward ${model.rr.reward}, ratio ${model.rr.ratio}` }, [
        el("span", { class: "cfp-rr-risk", style: { width: `${share}%` } }),
        el("span", { class: "cfp-rr-reward", style: { width: `${100 - share}%` } }),
      ]),
      el("div", { class: "cfp-rr-legend" }, [
        el("span", { class: "cfp-rr-risk-label", text: `Risk ${model.rr.risk}` }),
        el("span", { class: "cfp-rr-reward-label", text: `Reward ${model.rr.reward}` }),
      ]),
      el("div", { class: "cfp-rr-ratio", text: `R:R ${model.rr.ratio}` }),
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
