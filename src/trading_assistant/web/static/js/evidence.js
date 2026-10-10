/**
 * Chart evidence projection (pure, DOM-free, unit-tested).
 *
 * Input: the backend's chart-evidence payload (GET /api/market/annotations or
 * dashboard.chart_evidence) plus the exact stored candles on screen. Output:
 * chart markers, pattern line segments, and a time index used for click
 * resolution. Nothing here detects anything. It only decides how to DRAW what
 * the backend already recorded, and it enforces three rules:
 *
 *  1. Time: an item is drawn only when its known_at is at or before the instant
 *     the chart is showing (as_of). Future-known items are never drawn.
 *  2. Coordinates: an item is drawn only at a candle that exists in the stored
 *     series on screen. Nothing is interpolated, extended, or invented.
 *  3. Honesty: every visual label is one of the backend's own labels or states.
 */

/**
 * Density control. Breakout/sweep/retest and candle-shape markers are drawn only
 * for the most recent candles on screen, and at most one marker per candle.
 * Older items stay in the data and in the click-to-explain panel; the legend
 * states how many were not drawn. Swings and patterns are never trimmed.
 */
export const RECENT_EVENT_CANDLES = 40;

/** Layer model: what a user can switch on. Defaults keep the chart calm. */
export const LAYER_DEFS = Object.freeze([
  {
    id: "structure",
    label: "Structure",
    hint: "Confirmed swing highs and lows, labelled HH, HL, LH or LL against the previous swing of the same kind.",
    defaultOn: true,
  },
  {
    id: "patterns",
    label: "Patterns",
    hint: "Double tops and bottoms, head and shoulders, inverse head and shoulders. Shows formed, confirmed or invalidated state.",
    defaultOn: true,
  },
  {
    id: "breakouts",
    label: "Breakouts & sweeps",
    hint: "Breakouts, failed breakouts, liquidity sweeps and retests that BRAIN detected.",
    defaultOn: true,
  },
  {
    id: "levels",
    label: "Support / resistance",
    hint: "Clustered support and resistance zones, the active range and equal highs or lows. Off by default: the chart stays calm until you ask for them.",
    defaultOn: false,
  },
  {
    id: "plan",
    label: "Trade plan",
    hint: "Entry, stop, invalidation and targets from the engine's paper plan. Never an order.",
    defaultOn: true,
  },
  {
    id: "htfLevels",
    label: "Higher-TF levels",
    hint: "Support and resistance zones from the next higher timeframe, drawn as thin lines.",
    defaultOn: false,
  },
  {
    id: "candleSignals",
    label: "Candle shapes",
    hint: "Descriptive candle shapes: engulfing, long wicks, strong bodies, indecision. Not trade signals.",
    defaultOn: false,
  },
]);

export const DEFAULT_LAYERS = Object.freeze(
  Object.fromEntries(LAYER_DEFS.map((layer) => [layer.id, layer.defaultOn])),
);

/** Normalise any stored layer object: unknown keys dropped, missing keys defaulted. */
export function normalizeLayers(stored) {
  const source = stored && typeof stored === "object" ? stored : {};
  return Object.fromEntries(
    LAYER_DEFS.map((layer) => [layer.id, typeof source[layer.id] === "boolean" ? source[layer.id] : layer.defaultOn]),
  );
}

/**
 * Map layer switches onto the legacy overlay keys chart.applyOverlays already
 * draws. Liquidity (equalLevels) is deliberately NOT derived from a layer: it
 * stays its own persisted switch, so turning Support/resistance on or off never
 * silently changes it.
 */
export function overlayPrefsFromLayers(layers) {
  const normal = normalizeLayers(layers);
  return {
    zones: normal.levels,
    range: normal.levels,
    planLevels: normal.plan,
    swings: false,
  };
}

const STRUCTURE_BULL = "#2fbf7f";
const STRUCTURE_BEAR = "#e0565b";
const STRUCTURE_NEUTRAL = "#8ea0bd";
const PATTERN_TOP = "#e0565b";
const PATTERN_BOTTOM = "#2fbf7f";
const NECKLINE = "#e8a33d";
const INVALIDATION = "#8ea0bd";
const BREAKOUT_UP = "#2fbf7f";
const BREAKOUT_DOWN = "#e0565b";
const SHAPE_BULL = "#7fd6ae";
const SHAPE_BEAR = "#f08a8e";
const SHAPE_NEUTRAL = "#a7b0c2";

const STRUCTURE_TONE = { HH: "bull", HL: "bull", LH: "bear", LL: "bear", SH: "neutral", SL: "neutral", EH: "neutral", EL: "neutral" };

const BREAKOUT_PRIORITY = ["sweep", "failed", "breakout", "retest"];

const SHAPE_PRIORITY = [
  "bullish_engulfing",
  "bearish_engulfing",
  "lower_wick_rejection",
  "upper_wick_rejection",
  "strong_bullish_body",
  "strong_bearish_body",
  "indecision",
];

const SHAPE_TEXT = {
  bullish_engulfing: "Bull engulf",
  bearish_engulfing: "Bear engulf",
  lower_wick_rejection: "Long lower wick",
  upper_wick_rejection: "Long upper wick",
  strong_bullish_body: "Strong bull body",
  strong_bearish_body: "Strong bear body",
  indecision: "Indecision",
};

const PATTERN_STATE_TEXT = { formed: "formed", confirmed: "confirmed", invalidated: "invalidated" };

/** Seconds (lightweight-charts time) from a backend ms timestamp; NaN-safe. */
export function toSeconds(ms) {
  const value = Number(ms);
  return Number.isFinite(value) && value > 0 ? Math.floor(value / 1000) : null;
}

/** Parse a backend ISO timestamp to epoch ms, or null. */
export function isoMs(value) {
  if (typeof value !== "string" || !value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The candle series on screen, indexed by open time in seconds. Only rows the
 * stored-candle payload actually returned are present.
 */
export function candleTimes(candles) {
  const times = new Set();
  for (const candle of Array.isArray(candles) ? candles : []) {
    const seconds = Array.isArray(candle) ? toSeconds(candle[0]) : toSeconds(candle?.time_ms ?? candle?.timeMs);
    if (seconds !== null) times.add(seconds);
  }
  return times;
}

/** True only when the item's own known_at is at or before the displayed instant. */
export function knownBy(item, asOfMs) {
  const known = isoMs(item?.known_at);
  if (known === null) return false;
  if (asOfMs === null || asOfMs === undefined) return false;
  return known <= asOfMs;
}

/** Minimum candle spacing between two text labels on the same side of the chart. Closer labels keep their dot. */
export const LABEL_GAP_CANDLES = 5;

/** Short, unambiguous pattern names for chart labels. The full name is in the explanation and the item list. */
export const PATTERN_SHORT_NAME = Object.freeze({
  double_top: "DT",
  double_bottom: "DB",
  head_and_shoulders: "H&S",
  inverse_head_and_shoulders: "iH&S",
});

/** Chart label for a pattern marker: short name plus lifecycle state, e.g. "DT · confirmed". */
export function patternMarkerText(pattern) {
  const name = PATTERN_SHORT_NAME[pattern.type] || pattern.label || "Pattern";
  return `${name} · ${PATTERN_STATE_TEXT[pattern.state] || pattern.state}`;
}

/**
 * Decide which markers keep their text label. Candidates are {marker, priority, index}, where index is the
 * candle index. Higher-priority labels are placed first; a label is removed (dot kept) when another kept label
 * on the same side is closer than LABEL_GAP_CANDLES. Returns the number of labels removed. Deterministic.
 */
export function gateMarkerLabels(candidates) {
  const ordered = candidates
    .filter((c) => c.marker.text)
    .sort((a, b) => a.priority - b.priority || a.index - b.index);
  const kept = { aboveBar: [], belowBar: [] };
  let removed = 0;
  for (const candidate of ordered) {
    const side = kept[candidate.marker.position] || kept.aboveBar;
    const clash = side.some((index) => Math.abs(index - candidate.index) < LABEL_GAP_CANDLES);
    if (clash) {
      candidate.marker.text = "";
      removed += 1;
    } else {
      side.push(candidate.index);
    }
  }
  return removed;
}

function makeMarker(time, { position, shape, color, text, size = 1 }) {
  return { time, position, shape, color, text, size };
}

/**
 * The candle a pattern event is drawn on, in seconds. Uses the backend's anchor_time,
 * which is always a candle open. Falls back only for older payloads that lack it.
 * known_at / confirmation_time are close boundaries and are never used to place a marker.
 */
export function patternAnchorSeconds(pattern) {
  if (pattern && pattern.anchor_time) return toSeconds(isoMs(pattern.anchor_time));
  if (pattern?.state === "formed") return toSeconds(isoMs(pattern.formed_at));
  return null;
}

function patternLines(pattern, { lastTime, candleSet }) {
  const points = (pattern.components || [])
    .map((point) => ({ time: toSeconds(point.time_ms), value: Number(point.price) }))
    .filter((point) => point.time !== null && candleSet.has(point.time) && Number.isFinite(point.value));
  const lines = [];
  const top = pattern.side === "top";
  const zigzagStyle = pattern.state === "formed" ? 2 : pattern.state === "invalidated" ? 1 : 0;
  if (points.length >= 2) {
    lines.push({
      id: `${pattern.id}:zigzag`,
      role: "pattern-zigzag",
      patternId: pattern.id,
      color: top ? PATTERN_TOP : PATTERN_BOTTOM,
      lineStyle: zigzagStyle,
      lineWidth: 2,
      points,
    });
  }
  const start = points.length ? points[0].time : null;
  const neckline = Number(pattern.neckline);
  const anchor = patternAnchorSeconds(pattern);
  const anchorOnChart = anchor !== null && candleSet.has(anchor) ? anchor : null;
  if (start !== null && Number.isFinite(neckline)) {
    // A confirmed neckline stops at the candle that broke it; otherwise it runs to the latest candle.
    const end = pattern.state === "confirmed" && anchorOnChart !== null ? anchorOnChart : lastTime;
    if (end !== null && end > start) {
      lines.push({
        id: `${pattern.id}:neckline`,
        role: "pattern-neckline",
        patternId: pattern.id,
        color: NECKLINE,
        lineStyle: 2,
        lineWidth: 1,
        points: [{ time: start, value: neckline }, { time: end, value: neckline }],
      });
    }
  }
  const invalidation = Number(pattern.invalidation_level);
  // An invalidated pattern's level stops at the candle that invalidated it; before that it runs to the latest candle.
  const invalidationEnd = pattern.state === "invalidated" && anchorOnChart !== null ? anchorOnChart : lastTime;
  if (start !== null && Number.isFinite(invalidation) && invalidationEnd !== null && invalidationEnd > start) {
    lines.push({
      id: `${pattern.id}:invalidation`,
      role: "pattern-invalidation",
      patternId: pattern.id,
      color: INVALIDATION,
      lineStyle: 1,
      lineWidth: 1,
      points: [{ time: start, value: invalidation }, { time: invalidationEnd, value: invalidation }],
    });
  }
  return lines;
}

/**
 * Build everything the chart draws for the evidence layers.
 *
 * @param {object} args
 * @param {object} args.evidence chart-evidence payload (may be unavailable)
 * @param {Array} args.candles stored candle rows [ms,o,h,l,c,v] on screen
 * @param {number|null} args.asOfMs displayed decision instant (epoch ms)
 * @param {object} args.layers normalised layer switches
 */
export function buildEvidenceModel({ evidence, candles, asOfMs, layers }) {
  const model = {
    markers: [],
    patternLines: [],
    items: [],
    byTime: new Map(),
    counts: { structure: 0, patterns: 0, breakouts: 0, candleSignals: 0 },
    hiddenFuture: 0,
    hiddenOlder: { breakouts: 0, candleSignals: 0 },
  };
  const normal = normalizeLayers(layers);
  if (!evidence || evidence.available !== true) return model;
  const candleSet = candleTimes(candles);
  if (candleSet.size === 0) return model;
  const sortedTimes = [...candleSet].sort((a, b) => a - b);
  const candleIndex = new Map(sortedTimes.map((time, index) => [time, index]));
  const labelCandidates = [];
  const lastTime = sortedTimes[sortedTimes.length - 1];
  const recentFrom = sortedTimes[Math.max(0, sortedTimes.length - RECENT_EVENT_CANDLES)];
  const inRecentWindow = (time) => time >= recentFrom;

  const register = (item, time, group) => {
    if (!model.byTime.has(time)) model.byTime.set(time, []);
    model.byTime.get(time).push({ ...item, group, timeSeconds: time });
    model.items.push({ ...item, group, timeSeconds: time });
  };
  const eligible = (item) => {
    if (!knownBy(item, asOfMs)) {
      model.hiddenFuture += 1;
      return false;
    }
    return true;
  };

  if (normal.structure) {
    for (const swing of Array.isArray(evidence.swings) ? evidence.swings : []) {
      const time = toSeconds(swing.time_ms);
      if (time === null || !candleSet.has(time) || !eligible(swing)) continue;
      const tone = STRUCTURE_TONE[swing.label] || "neutral";
      const color = tone === "bull" ? STRUCTURE_BULL : tone === "bear" ? STRUCTURE_BEAR : STRUCTURE_NEUTRAL;
      const high = swing.kind === "high";
      const marker = makeMarker(time, {
        position: high ? "aboveBar" : "belowBar",
        shape: "circle",
        color,
        text: swing.label,
        size: 1,
      });
      model.markers.push(marker);
      labelCandidates.push({ marker, priority: 2, index: candleIndex.get(time) ?? -1 });
      register(swing, time, "structure");
      model.counts.structure += 1;
    }
  }

  if (normal.patterns) {
    for (const pattern of Array.isArray(evidence.patterns) ? evidence.patterns : []) {
      if (!eligible(pattern)) continue;
      if (pattern.is_latest_state !== true) continue; // history stays in the explanation panel
      const top = pattern.side === "top";
      const lines = patternLines(pattern, { lastTime, candleSet });
      model.patternLines.push(...lines);
      const anchorSource = patternAnchorSeconds(pattern);
      const anchor = anchorSource !== null && candleSet.has(anchorSource) ? anchorSource : null;
      if (anchor !== null) {
        const marker = makeMarker(anchor, {
          position: top ? "aboveBar" : "belowBar",
          shape: "square",
          color: pattern.state === "invalidated" ? INVALIDATION : top ? PATTERN_TOP : PATTERN_BOTTOM,
          text: patternMarkerText(pattern),
          size: 1,
        });
        model.markers.push(marker);
        labelCandidates.push({ marker, priority: 0, index: candleIndex.get(anchor) ?? -1 });
        register(pattern, anchor, "patterns");
        model.counts.patterns += 1;
      }
    }
  }

  if (normal.breakouts) {
    const breakoutItems = [
      ...(Array.isArray(evidence.sweeps) ? evidence.sweeps : []).map((item) => ({ ...item, _kind: "sweep", _text: item.direction === "below" ? "SWP↓" : "SWP↑" })),
      ...(Array.isArray(evidence.failed_breakouts) ? evidence.failed_breakouts : []).map((item) => ({ ...item, _kind: "failed", _text: "FAIL" })),
      ...(Array.isArray(evidence.breakouts) ? evidence.breakouts : []).map((item) => ({ ...item, _kind: "breakout", _text: item.direction === "bullish" ? "BRK↑" : "BRK↓" })),
      ...(Array.isArray(evidence.retests) ? evidence.retests : []).map((item) => ({ ...item, _kind: "retest", _text: item.state === "held" ? "RT held" : item.state === "failed" ? "RT failed" : "RT" })),
    ];
    const byCandle = new Map();
    for (const item of breakoutItems) {
      const time = toSeconds(item.time_ms);
      if (time === null || !candleSet.has(time)) continue;
      if (!inRecentWindow(time)) {
        model.hiddenOlder.breakouts += 1;
        continue;
      }
      if (!eligible(item)) continue;
      if (!byCandle.has(time)) byCandle.set(time, []);
      byCandle.get(time).push(item);
    }
    for (const [time, items] of [...byCandle.entries()].sort((a, b) => a[0] - b[0])) {
      items.sort((a, b) => BREAKOUT_PRIORITY.indexOf(a._kind) - BREAKOUT_PRIORITY.indexOf(b._kind));
      const primary = items[0];
      const down = primary.direction === "bearish" || primary.direction === "below";
      const failed = primary.kind === "failed_breakout" || primary.state === "failed";
      // The arrow points the way the breakout or sweep went (up = bullish, down = bearish).
      // Retests have no direction arrow, so they use a plain circle.
      const retest = primary._kind === "retest";
      model.markers.push(makeMarker(time, {
        position: down ? "aboveBar" : "belowBar",
        shape: retest ? "circle" : down ? "arrowDown" : "arrowUp",
        color: failed ? SHAPE_NEUTRAL : down ? BREAKOUT_DOWN : BREAKOUT_UP,
        // Arrows and circles only: the label text would collide on a dense chart.
        // The click-to-explain panel names the event and its state exactly.
        text: "",
        size: 1,
      }));
      for (const item of items) {
        const { _text, _kind, ...clean } = item;
        void _text;
        void _kind;
        register(clean, time, "breakouts");
        model.counts.breakouts += 1;
      }
    }
  }

  if (normal.candleSignals) {
    const byCandle = new Map();
    for (const shape of Array.isArray(evidence.candle_shapes) ? evidence.candle_shapes : []) {
      const time = toSeconds(shape.time_ms);
      if (time === null || !candleSet.has(time)) continue;
      if (!inRecentWindow(time)) {
        model.hiddenOlder.candleSignals += 1;
        continue;
      }
      if (!eligible(shape)) continue;
      if (!byCandle.has(time)) byCandle.set(time, []);
      byCandle.get(time).push(shape);
    }
    for (const [time, shapes] of [...byCandle.entries()].sort((a, b) => a[0] - b[0])) {
      shapes.sort((a, b) => SHAPE_PRIORITY.indexOf(a.kind) - SHAPE_PRIORITY.indexOf(b.kind));
      const primary = shapes[0];
      const color = primary.direction === "bullish" ? SHAPE_BULL : primary.direction === "bearish" ? SHAPE_BEAR : SHAPE_NEUTRAL;
      const marker = makeMarker(time, {
        position: primary.direction === "bearish" ? "aboveBar" : "belowBar",
        shape: "circle",
        color,
        text: SHAPE_TEXT[primary.kind] || primary.kind,
        size: 0.8,
      });
      model.markers.push(marker);
      labelCandidates.push({ marker, priority: 1, index: candleIndex.get(time) ?? -1 });
      for (const shape of shapes) register(shape, time, "candleSignals");
      model.counts.candleSignals += 1;
    }
  }

  model.labelsRemoved = gateMarkerLabels(labelCandidates);
  model.markers.sort((a, b) => a.time - b.time);
  model.patternLines.sort((a, b) => a.id.localeCompare(b.id));
  return model;
}

/** The evidence items at a clicked candle, highest-priority group first. */
export function itemsAtTime(model, timeSeconds) {
  const items = model?.byTime?.get(timeSeconds) || [];
  const order = { patterns: 0, breakouts: 1, structure: 2, candleSignals: 3 };
  return [...items].sort((a, b) => (order[a.group] ?? 9) - (order[b.group] ?? 9));
}

/** Lookup by backend id, used by level chips and explanation links. */
export function findItem(evidence, id) {
  if (!evidence || typeof id !== "string") return null;
  const groups = ["swings", "patterns", "breakouts", "failed_breakouts", "sweeps", "retests", "equal_levels", "zones", "candle_shapes"];
  for (const group of groups) {
    for (const item of Array.isArray(evidence[group]) ? evidence[group] : []) {
      if (item && item.id === id) return { ...item, group };
    }
  }
  if (evidence.range && evidence.range.id === id) return { ...evidence.range, group: "range" };
  return null;
}

/** Stable, human-readable count line for the chart legend. */
export function evidenceCountText(model) {
  const parts = [];
  if (model.counts.structure) parts.push(`${model.counts.structure} swing label${model.counts.structure === 1 ? "" : "s"}`);
  if (model.counts.patterns) parts.push(`${model.counts.patterns} pattern${model.counts.patterns === 1 ? "" : "s"}`);
  if (model.counts.breakouts) parts.push(`${model.counts.breakouts} breakout/sweep marker${model.counts.breakouts === 1 ? "" : "s"}`);
  if (model.counts.candleSignals) parts.push(`${model.counts.candleSignals} candle shape${model.counts.candleSignals === 1 ? "" : "s"}`);
  const older = (model.hiddenOlder?.breakouts || 0) + (model.hiddenOlder?.candleSignals || 0);
  if (older) parts.push(`${older} older event${older === 1 ? "" : "s"} not drawn (outside the last ${RECENT_EVENT_CANDLES} candles)`);
  return parts.length ? parts.join(" · ") : "No evidence drawn for the enabled layers at this candle window.";
}

const PATTERN_FULL_NAME = Object.freeze({
  double_top: "Double top",
  double_bottom: "Double bottom",
  head_and_shoulders: "Head and shoulders",
  inverse_head_and_shoulders: "Inverse head and shoulders",
});

const GROUP_NAME = Object.freeze({
  structure: "Structure",
  patterns: "Pattern",
  breakouts: "Breakout, sweep or retest",
  candleSignals: "Candle shape",
});

/**
 * Full, plain-language name for one drawn evidence item. Used by the accessible item list so
 * every event stays available even when its chart label is hidden to avoid covering candles.
 */
export function describeEvidenceItem(item) {
  if (!item || typeof item !== "object") return "Evidence";
  if (item.group === "structure") {
    const side = item.kind === "high" ? "high" : item.kind === "low" ? "low" : "";
    return `Swing ${item.label || ""}${side ? ` (${side})` : ""}`.trim();
  }
  if (item.group === "patterns") {
    const name = PATTERN_FULL_NAME[item.type] || item.label || "Pattern";
    return `${name} · ${PATTERN_STATE_TEXT[item.state] || item.state || "state unknown"}`;
  }
  if (item.group === "candleSignals") return SHAPE_TEXT[item.kind] || String(item.kind || "Candle shape");
  const kind = String(item.kind || item.type || GROUP_NAME[item.group] || "event").replaceAll("_", " ");
  const state = item.state ? ` · ${item.state}` : "";
  const direction = item.direction ? ` (${item.direction})` : "";
  return `${kind.charAt(0).toUpperCase()}${kind.slice(1)}${direction}${state}`;
}

/** The items in view, oldest first, with time and a plain name. Empty when no evidence model exists. */
export function evidenceItemRows(model) {
  if (!model || !Array.isArray(model.items)) return [];
  return [...model.items]
    .sort((a, b) => a.timeSeconds - b.timeSeconds)
    .map((item) => ({
      timeSeconds: item.timeSeconds,
      group: item.group,
      name: describeEvidenceItem(item),
    }));
}
