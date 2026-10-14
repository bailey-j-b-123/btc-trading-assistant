/**
 * Chart functions: the five approved ways to look at the evidence BRAIN already
 * recorded. Presentation only, DOM-free and unit-tested.
 *
 * What this module is allowed to do
 * ---------------------------------
 * Decide *which* backend records belong to the chart function the operator
 * selected, how the existing layer switches map onto that selection, and what an
 * honest empty state says when a function has nothing stored to show. It never
 * detects anything, never creates a level, price, pattern, event, confidence or
 * timestamp, and never moves one.
 *
 * What it may never do
 * --------------------
 *  * invent a support/resistance level, swing, pattern, breakout or trade plan;
 *  * re-label a backend record (every label it exposes is the backend's own
 *    label or state: HH/HL/LH/LL, formed/confirmed/invalidated, held/failed,
 *    bullish/bearish …);
 *  * show an item the backend did not mark as known at the analysed instant —
 *    the chart-evidence payload already drops those and counts them in
 *    `excluded_future_count`, and this module keeps that field intact;
 *  * upgrade a "forming" or unconfirmed record to a confirmed one.
 *
 * Simple mode (default) draws exactly one primary function at a time and only
 * its own evidence, so candles stay visible and labels stay readable. Detailed
 * mode keeps everything the operator switched on, plus the extra historical
 * records the backend stores (older pattern states, older breakout events),
 * without ever changing what those records mean.
 */

import { LAYER_DEFS, normalizeLayers, overlayPrefsFromLayers, toSeconds } from "./evidence.js";

/** One primary chart function at a time (Simple mode), or several (Detailed). */
export const CHART_MODES = Object.freeze([
  {
    id: "simple",
    label: "Simple",
    hint: "One chart function at a time. Only the most relevant stored evidence is drawn, so candles stay readable.",
  },
  {
    id: "detailed",
    label: "Detailed",
    hint: "Several layers at once, including older stored evidence. Individual events stay clickable.",
  },
]);

export const SIMPLE_MODE = "simple";
export const DETAILED_MODE = "detailed";
export const DEFAULT_CHART_MODE = SIMPLE_MODE;

/**
 * The five approved chart functions.
 *
 * `layer` is the one evidence layer the function owns (see evidence.js
 * LAYER_DEFS); `overlays` are the legacy overlay switches chart.applyOverlays
 * reads. `evidence` lists the chart-evidence payload groups the function draws;
 * support/resistance and trade planning draw levels and plan lines instead of
 * markers, so their evidence list is empty on purpose.
 */
export const CHART_FUNCTIONS = Object.freeze([
  {
    id: "support_resistance",
    label: "Support / Resistance",
    short: "S/R",
    layer: "levels",
    overlays: Object.freeze({ zones: true, range: true, planLevels: false, equalLevels: false }),
    evidence: Object.freeze([]),
    empty: "No support or resistance zones are stored for this timeframe at this instant.",
    hint: "Shaded bands from the stored Step 3 zones. A band below the close reads as support, above it as resistance; the touch count and price boundaries are the detector's own. No level is invented, moved or re-labelled.",
  },
  {
    id: "swings",
    label: "Swing Highs / Lows",
    short: "Swings",
    layer: "structure",
    overlays: Object.freeze({ zones: false, range: false, planLevels: false, equalLevels: false }),
    evidence: Object.freeze(["swings"]),
    empty: "No confirmed swing is stored inside the loaded candle window.",
    hint: "Confirmed swing highs and lows only, labelled HH/HL (green), LH/LL (red) or SH/SL/EH/EL when there is nothing to compare with or the extremes are equal. A swing appears at the candle that confirmed it, never earlier.",
  },
  {
    id: "patterns",
    label: "Chart Patterns",
    short: "Patterns",
    layer: "patterns",
    overlays: Object.freeze({ zones: false, range: false, planLevels: false, equalLevels: false }),
    evidence: Object.freeze(["patterns"]),
    empty: "No chart pattern is stored for this window at this instant.",
    hint: "One detected pattern is drawn at a time, in its own detected shape: double top, double bottom, head and shoulders, or inverse head and shoulders. Its lifecycle state (formed, confirmed, invalidated) is the backend's. Select another detected pattern to switch the example.",
  },
  {
    id: "breakouts",
    label: "Breakouts / Retests",
    short: "Breakouts",
    layer: "breakouts",
    overlays: Object.freeze({ zones: false, range: false, planLevels: false, equalLevels: false }),
    evidence: Object.freeze(["breakouts", "failed_breakouts", "sweeps", "retests"]),
    empty: "No breakout, sweep or retest is stored in the recent part of this window.",
    hint: "Breakouts (▲/▼), liquidity sweeps (▼/▲), failed breakouts (grey) and retests (●), each at the candle BRAIN detected it, with its own confirmation state. Continuation is never drawn unless the backend recorded it.",
  },
  {
    id: "trade_plan",
    label: "Trade Plan",
    short: "Plan",
    layer: "plan",
    overlays: Object.freeze({ zones: false, range: false, planLevels: true, equalLevels: false }),
    evidence: Object.freeze([]),
    empty: "No actionable plan right now — BRAIN has not built one from a qualified setup.",
    hint: "Entry, stop, invalidation and targets from the engine's own paper plan, with the risk/reward the plan itself calculated. Hypothetical paper levels only; nothing here is an order.",
  },
]);

export const CHART_FUNCTION_IDS = Object.freeze(CHART_FUNCTIONS.map((entry) => entry.id));

/** The engine plan is the calm default: it is the only function that draws a decision unit. */
export const DEFAULT_CHART_FUNCTION = "trade_plan";

export function chartFunctionById(id) {
  const wanted = typeof id === "string" ? id : null;
  return CHART_FUNCTIONS.find((entry) => entry.id === wanted) || null;
}

/** Unknown or missing ids fall back to the default; a stale stored choice never breaks the chart. */
export function normalizeFunctionId(id) {
  return chartFunctionById(id) ? id : DEFAULT_CHART_FUNCTION;
}

export function normalizeMode(id) {
  return CHART_MODES.some((entry) => entry.id === id) ? id : DEFAULT_CHART_MODE;
}

/**
 * Layer switches for the current selection.
 *
 * Simple: only the selected function's layer is on — one function at a time.
 * Detailed: everything the operator switched on stays on, and the selected
 * function's own layer is forced on so the selection is always visible.
 */
export function layersForSelection({ mode, functionId, layers }) {
  const fn = chartFunctionById(normalizeFunctionId(functionId)) || CHART_FUNCTIONS[0];
  const stored = normalizeLayers(layers);
  const next = Object.fromEntries(LAYER_DEFS.map((layer) => [layer.id, layer.id === fn.layer]));
  if (normalizeMode(mode) !== DETAILED_MODE) return next;
  return { ...stored, [fn.layer]: true };
}

/**
 * Overlay switches for the current selection (zones, range, equal levels, plan
 * levels). Liquidity (equalLevels) is never derived from a layer, exactly as in
 * evidence.js: it stays the operator's own switch, but Simple mode keeps it off
 * so a single function is shown at a time.
 */
export function overlayPrefsForSelection({ mode, functionId, layers, overlays }) {
  const storedLayers = normalizeLayers(layers);
  const derived = overlayPrefsFromLayers(layersForSelection({ mode, functionId, layers: storedLayers }));
  const storedOverlays = overlays && typeof overlays === "object" ? overlays : {};
  if (normalizeMode(mode) === DETAILED_MODE) {
    return {
      ...derived,
      equalLevels: storedOverlays.equalLevels === true,
    };
  }
  return {
    ...derived,
    equalLevels: false,
  };
}

/** Which chart-evidence groups the function reads. Unknown ids read nothing. */
export function evidenceGroupsFor(functionId) {
  const fn = chartFunctionById(normalizeFunctionId(functionId));
  return fn ? [...fn.evidence] : [];
}

const EVIDENCE_GROUPS = Object.freeze([
  "swings",
  "patterns",
  "breakouts",
  "failed_breakouts",
  "sweeps",
  "retests",
  "equal_levels",
  "candle_shapes",
]);

/**
 * The backend's own pattern catalogue for this window, newest state first.
 * Nothing is added: each entry is one stored pattern record with the label,
 * state and anchor the backend projected. `is_latest_state` marks the record
 * that is that pattern's current state.
 */
export function patternCatalogue(evidence) {
  const patterns = Array.isArray(evidence?.patterns) ? evidence.patterns : [];
  return patterns
    .filter((pattern) => pattern && typeof pattern === "object")
    .map((pattern) => ({
      id: pattern.id,
      patternId: pattern.pattern_id ?? pattern.id,
      label: pattern.label || pattern.type || "Pattern",
      type: pattern.type || null,
      side: pattern.side || null,
      state: pattern.state || null,
      isLatestState: pattern.is_latest_state === true,
      knownAt: pattern.known_at || null,
      formedAt: pattern.formed_at || null,
      confirmedAt: pattern.confirmed_at || null,
      invalidatedAt: pattern.invalidated_at || null,
      anchorMs: Number.isFinite(Number(pattern.anchor_time_ms)) ? Number(pattern.anchor_time_ms) : null,
      neckline: pattern.neckline ?? null,
      invalidation: pattern.invalidation_level ?? null,
      componentCount: Array.isArray(pattern.components) ? pattern.components.length : 0,
      confidence: null, // never invented: the backend stores geometry, not a confidence score
    }))
    .sort((left, right) => {
      const leftTime = Date.parse(left.knownAt || "") || 0;
      const rightTime = Date.parse(right.knownAt || "") || 0;
      if (leftTime !== rightTime) return rightTime - leftTime;
      return String(right.id).localeCompare(String(left.id));
    });
}

/**
 * Deterministic default order for stored pattern records (documented in
 * docs/visual-signoff/patterns/REPORT.md). It ranks by lifecycle evidence and
 * recency only — never by a "strength" or score, which the backend does not
 * calculate:
 *
 *   1. current states (`is_latest_state`) before earlier states;
 *   2. among those, a record that is still standing (formed / confirmed)
 *      before one the detector has invalidated;
 *   3. then the most recently known (`known_at`) first;
 *   4. then the record id, descending, so equal times never flicker.
 */
export function patternDefaultRank(left, right) {
  const latest = (entry) => (entry.isLatestState ? 0 : 1);
  const standing = (entry) => (entry.state === "invalidated" ? 1 : 0);
  if (latest(left) !== latest(right)) return latest(left) - latest(right);
  if (standing(left) !== standing(right)) return standing(left) - standing(right);
  const leftTime = Date.parse(left.knownAt || "") || 0;
  const rightTime = Date.parse(right.knownAt || "") || 0;
  if (leftTime !== rightTime) return rightTime - leftTime;
  return String(right.id).localeCompare(String(left.id));
}

/**
 * The pattern to highlight: the operator's explicit choice when that exact
 * stored record exists in this window, otherwise the first record in the
 * documented default order (patternDefaultRank): the most recent current state
 * that has not been invalidated, else the most recent current state.
 */
export function highlightedPattern(evidence, selectedId) {
  const catalogue = patternCatalogue(evidence);
  if (typeof selectedId === "string" && selectedId) {
    const chosen = catalogue.find((entry) => entry.id === selectedId);
    if (chosen) return chosen;
  }
  return [...catalogue].sort(patternDefaultRank)[0] || null;
}

/**
 * Evidence payload for the selected function.
 *
 * Simple mode returns a copy that carries only the selected function's own
 * groups; for patterns it carries the highlighted pattern only, so exactly one
 * detected shape is drawn. Detailed mode returns the payload unchanged, so
 * every stored record the layers allow is available.
 *
 * `is_latest_state` is a presentation flag ("draw this record as the current
 * state"). A highlighted historical state keeps its own state string; it is
 * never re-labelled as current.
 */
export function filterEvidence(evidence, functionId, { mode = DEFAULT_CHART_MODE, selectedPatternId = null } = {}) {
  if (!evidence || typeof evidence !== "object") return evidence;
  const groups = evidenceGroupsFor(functionId);
  if (normalizeMode(mode) === DETAILED_MODE) return evidence;
  const next = { ...evidence };
  for (const group of EVIDENCE_GROUPS) {
    next[group] = groups.includes(group) && Array.isArray(evidence[group]) ? evidence[group] : [];
  }
  if (groups.includes("patterns")) {
    const chosen = highlightedPattern(evidence, selectedPatternId);
    next.patterns = chosen
      ? (next.patterns || [])
          .filter((pattern) => pattern?.id === chosen.id)
          .map((pattern) => ({ ...pattern, is_latest_state: true }))
      : [];
  }
  return next;
}

/**
 * A count that is genuinely absent stays null, and null is never rendered as
 * zero: "no touches recorded" and "no touches" are different statements.
 */
function optionalNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

/**
 * Does a stored time reference point at the same instant as a swing?
 *
 * Stored references arrive in three shapes: an ISO string from the chart
 * evidence, an epoch number in seconds from the database rows, or epoch
 * milliseconds. The comparison is the same stored instant, so a reference that
 * was only kept to the second still matches the candle it came from. The
 * shortest configured timeframe is one minute, so a one-second window can never
 * merge two different candles.
 */
function matchesStoredTime(value, timeMs) {
  if (typeof value === "number" && Number.isFinite(value)) {
    const normalised = value > 1e11 ? value : value * 1000;
    return Math.abs(normalised - timeMs) < 1000;
  }
  if (typeof value !== "string" || value.trim() === "") return false;
  const parsed = Date.parse(value);
  if (Number.isFinite(parsed)) return Math.abs(parsed - timeMs) < 1000;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return Math.abs((numeric > 1e11 ? numeric : numeric * 1000) - timeMs) < 1000;
  return false;
}

/**
 * Swing emphasis, from stored evidence only.
 *
 * A swing is "major" when a stored record is built on it — it is one of the
 * source swings of a stored support/resistance zone, or one of the components
 * of a stored chart pattern. Everything else is "minor" context. This is a
 * description of how much stored structure references the swing; it is not a
 * strength score, a probability or a prediction, and no swing is added,
 * removed or moved by it.
 */
export function swingEmphasis(swing, { zones = [], patterns = [] } = {}) {
  const time = Number(swing?.time_ms);
  if (!Number.isFinite(time)) return "minor";
  for (const zone of Array.isArray(zones) ? zones : []) {
    for (const source of Array.isArray(zone?.source_swing_times) ? zone.source_swing_times : []) {
      if (matchesStoredTime(source, time)) return "major";
    }
  }
  for (const pattern of Array.isArray(patterns) ? patterns : []) {
    for (const point of Array.isArray(pattern?.components) ? pattern.components : []) {
      if (matchesStoredTime(point?.time_ms, time)) return "major";
    }
  }
  return "minor";
}

/** Every stored swing with its backend label plus the stored-reference emphasis. */
export function swingCatalogue(evidence, { zones = [], patterns = [] } = {}) {
  const swings = Array.isArray(evidence?.swings) ? evidence.swings : [];
  return swings.map((swing) => ({
    id: swing.id,
    kind: swing.kind,
    label: swing.label,
    price: swing.price,
    timeMs: Number(swing.time_ms),
    knownAt: swing.known_at || null,
    confirmedBy: swing.confirmed_by || null,
    emphasis: swingEmphasis(swing, { zones, patterns }),
  }));
}

/**
 * Swing connector points: one descending-by-time series per kind, using the
 * exact stored swing prices. Used only for the optional subtle connector line.
 */
export function swingConnectors(
  evidence,
  { zones = [], patterns = [] } = {},
  { majorOnly = false, format = "channels" } = {},
) {
  if (format === "chronological" || format === "zigzag") {
    return chronologicalSwingConnectors(evidence, { zones, patterns }, { majorOnly });
  }
  const swings = Array.isArray(evidence?.swings) ? evidence.swings : [];
  const points = new Map([["high", []], ["low", []]]);
  for (const swing of swings) {
    // Chart line data is in seconds, like the candle times it is drawn against.
    const time = toSeconds(swing.time_ms);
    const value = Number(swing.price);
    if (!Number.isFinite(time) || !Number.isFinite(value)) continue;
    if (majorOnly && swingEmphasis(swing, { zones, patterns }) !== "major") continue;
    points.get(swing.kind === "high" ? "high" : "low").push({ time, value });
  }
  return points;
}

/**
 * Chronological swing connector points: one continuous sequence connecting
 * confirmed swings in timestamp order (alternating highs and lows), representing
 * structural market progression (zigzag).
 */
export function chronologicalSwingConnectors(
  evidence,
  { zones = [], patterns = [] } = {},
  { majorOnly = false } = {},
) {
  const swings = Array.isArray(evidence?.swings) ? evidence.swings : [];
  const valid = [];
  for (const swing of swings) {
    const time = toSeconds(swing.time_ms);
    const value = Number(swing.price);
    if (!Number.isFinite(time) || !Number.isFinite(value)) continue;
    if (majorOnly && swingEmphasis(swing, { zones, patterns }) !== "major") continue;
    valid.push({
      time,
      value,
      kind: swing.kind,
      label: swing.label || null,
      timeMs: Number(swing.time_ms),
    });
  }
  return valid.sort((left, right) => left.time - right.time);
}

/** Unified breakout/retest catalogue, newest first, with the backend's own state wording. */
export function eventCatalogue(evidence) {
  const rows = [];
  const push = (list, kind) => {
    for (const item of Array.isArray(list) ? list : []) {
      if (!item || typeof item !== "object") continue;
      rows.push({
        id: item.id,
        kind,
        direction: item.direction || null,
        state: item.state || null,
        label: item.label || null,
        timeMs: Number.isFinite(Number(item.time_ms)) ? Number(item.time_ms) : null,
        knownAt: item.known_at || null,
        bandLow: item.band_low ?? null,
        bandHigh: item.band_high ?? null,
        confirmedAt: item.confirmed_at || null,
        confirmationCandles: Number.isFinite(Number(item.confirmation_candles))
          ? Number(item.confirmation_candles)
          : null,
      });
    }
  };
  push(evidence?.breakouts, "breakout");
  push(evidence?.sweeps, "sweep");
  push(evidence?.failed_breakouts, "failed_breakout");
  push(evidence?.retests, "retest");
  return rows.sort((left, right) => (right.timeMs || 0) - (left.timeMs || 0));
}

/**
 * The displayed support/resistance bands for the S/R function and its summary
 * card. `band.position` / `band.display_role` are the backend's presentation
 * of where the band sits against the close the detector used; this function
 * keeps them verbatim.
 */
export function levelCatalogue(bands) {
  return (Array.isArray(bands) ? bands : [])
    .filter((band) => band && typeof band === "object")
    .map((band) => ({
      id: band.id || `${band.band_low}-${band.band_high}`,
      role: band.display_role || band.position || "price_inside",
      position: band.position || null,
      low: band.band_low,
      high: band.band_high,
      center: band.center,
      touchCount: optionalNumber(band.touch_count),
      // Separate instants that tested the level, versus the swing evidences counted above.
      distinctTestTimestamps: optionalNumber(band.distinct_test_timestamps),
      sharedCandleTests: optionalNumber(band.shared_candle_tests),
      // Every contributing detector zone, so a merged band's aggregate basis stays inspectable.
      mergedZoneCount: optionalNumber(band.merged_zone_count),
      memberZones: Array.isArray(band.member_zones)
        ? band.member_zones.filter((member) => member && typeof member === "object")
        : [],
      timeframe: band.source_timeframe || null,
      faded: band.faded === true,
      isolated: band.isolated === true,
      firstSeen: band.first_seen || null,
      lastTested: band.last_tested || null,
      ageCandles: optionalNumber(band.age_candles),
      // Distance to the nearest edge, against this chart's close when it has one.
      distanceFromClose: optionalNumber(band.distance_from_close),
      distancePctFromClose: optionalNumber(band.distance_pct_from_close),
      viewedDistance: optionalNumber(band.viewed_distance),
      viewedClose: band.viewed_close ?? null,
      sourceClose: band.source_close ?? band.latest_close ?? null,
      linkedEvents: band.linked_events && typeof band.linked_events === "object" ? band.linked_events : null,
    }));
}

/**
 * Honest per-function status line.
 *
 * Every number is a count of stored records (or the backend's own exclusion
 * count). When evidence is unavailable the reason is shown verbatim; when a
 * function legitimately has nothing stored, its empty sentence is used instead
 * of a fabricated figure.
 */
export function functionStatus({ functionId, evidence, model = null, plan = null, planState = null, extra = null, stages = null }) {
  const fn = chartFunctionById(normalizeFunctionId(functionId)) || CHART_FUNCTIONS[0];
  if (fn.id === "trade_plan") {
    const plannable = plan && plan.state === "PLANNABLE";
    return {
      available: plannable === true,
      text: plannable
        ? "Engine paper plan: entry, stop, invalidation and targets as calculated by the deterministic planner."
        : fn.empty,
      detail: plannable ? null : planState || null,
    };
  }
  if (fn.id === "support_resistance") {
    const bands = levelCatalogue(extra?.bands);
    if (extra && extra.reason === "no_zones") return { available: false, text: fn.empty, detail: null };
    if (bands.length) {
      const hidden = Number.isFinite(Number(extra?.hiddenCount)) ? Number(extra.hiddenCount) : 0;
      const merged = Number.isFinite(Number(extra?.mergedCount)) ? Number(extra.mergedCount) : 0;
      // Bands the backend presented but this chart left unpainted, so the total is the real one.
      const capHidden = Number.isFinite(Number(extra?.capHidden)) ? Number(extra.capHidden) : 0;
      const higherHidden = Number.isFinite(Number(extra?.higherTimeframeHidden))
        ? Number(extra.higherTimeframeHidden)
        : 0;
      const total = hidden + capHidden;
      const parts = [`${bands.length} band(s) shown nearest price first`];
      parts.push(
        total > 0
          ? `${total} further band(s) not drawn${capHidden > 0 ? ` (${capHidden} beyond this chart's nearest-band cap)` : ""}`
          : "no further stored band was left out",
      );
      if (higherHidden > 0) parts.push(`${higherHidden} higher-timeframe band(s) also not drawn`);
      parts.push(`${merged} merged for display`);
      return {
        available: true,
        text: `${parts.join(" · ")}.`,
        detail: null,
      };
    }
    return { available: false, text: fn.empty, detail: extra?.reason || null };
  }
  if (!evidence || evidence.available !== true) {
    const reason = evidence && typeof evidence.reason === "string" && evidence.reason.trim()
      ? evidence.reason.trim()
      : "the backend did not return chart evidence for this view";
    return { available: false, text: `Chart evidence unavailable — ${reason}. Nothing is drawn in its place.`, detail: reason };
  }
  const counts = evidence.counts && typeof evidence.counts === "object" ? evidence.counts : {};
  const drawn = model?.counts || null;
  const excluded = optionalNumber(evidence.excluded_future_count) ?? 0;
  const storedSwings = optionalNumber(counts.swings);
  const storedPatterns = optionalNumber(counts.patterns);
  const storedBreakouts = optionalNumber(counts.breakouts);
  const storedSweeps = optionalNumber(counts.sweeps);
  const storedRetests = optionalNumber(counts.retests);
  const storedFailed = optionalNumber(counts.failed_breakouts);
  const shown = drawn
    ? (fn.id === "swings" ? drawn.structure : fn.id === "patterns" ? drawn.patterns : drawn.breakouts)
    : null;
  const eventCounts = [storedBreakouts, storedSweeps, storedRetests, storedFailed];
  const storedTotal = fn.id === "swings"
    ? storedSwings
    : fn.id === "patterns"
      ? storedPatterns
      : eventCounts.every((value) => value === null)
        ? null
        : eventCounts.reduce((total, value) => total + (value ?? 0), 0);
  const excludedNote = excluded > 0 ? `${excluded} later-known item(s) excluded at this instant` : null;
  // Nothing stored and nothing drawn: the function's own empty sentence, never a
  // row of invented zeros.
  if (storedTotal === 0 && (shown === null || shown === 0)) {
    return {
      available: false,
      text: excludedNote ? `${fn.empty} ${excludedNote}.` : fn.empty,
      detail: null,
    };
  }
  const parts = [];
  if (fn.id === "swings") {
    parts.push(storedSwings !== null ? `${storedSwings} stored confirmed swing(s)` : "stored swing count unavailable");
    if (shown !== null) parts.push(`${shown} drawn in the loaded window`);
  } else if (fn.id === "patterns") {
    parts.push(storedPatterns !== null ? `${storedPatterns} stored pattern state(s)` : "stored pattern count unavailable");
    if (shown !== null) parts.push(`${shown} drawn`);
  } else if (fn.id === "breakouts") {
    if (storedTotal !== null) {
      parts.push(`${storedBreakouts ?? "?"} breakout(s) · ${storedFailed ?? "?"} failed · ${storedSweeps ?? "?"} sweep(s) · ${storedRetests ?? "?"} retest(s)`);
    }
    // The Breakouts & Retests screen reads as three annotated steps, so the
    // status names those steps and their recorded statuses. `shown` counts the
    // small per-event markers, which this screen does not draw.
    if (stages && Array.isArray(stages.stages) && stages.stages.length) {
      parts.push(stages.stages.map((stage) => `${stage.number} ${stage.title}: ${stage.status}`).join(" · "));
    } else if (shown !== null) {
      parts.push(`${shown} drawn in the recent window`);
    }
    const hiddenOlder = optionalNumber(model?.hiddenOlder?.breakouts);
    if (hiddenOlder !== null && hiddenOlder > 0) parts.push(`${hiddenOlder} older item(s) outside the recent window`);
  }
  if (excludedNote) parts.push(excludedNote);
  if (model?.hiddenFuture > 0) parts.push(`${model.hiddenFuture} not yet known at this instant`);
  return {
    available: true,
    text: parts.length ? `${parts.join(" · ")}.` : fn.empty,
    detail: null,
  };
}

/** Human sentence for one chart function button's tooltip, always the honesty rule first. */
export function functionHint(functionId) {
  const fn = chartFunctionById(normalizeFunctionId(functionId)) || CHART_FUNCTIONS[0];
  return fn.hint;
}
