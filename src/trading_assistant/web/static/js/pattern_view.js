/**
 * Chart Patterns presentation: the annotation drawn over the candles and the
 * three cards beside them, built from the backend's own stored records.
 *
 * Presentation only, DOM-free and unit-tested.
 *
 * Which records exist
 * -------------------
 * Two detectors feed this screen; nothing else does:
 *
 *   * `pattern_liquidity.classical_patterns` stores lifecycle records
 *     (formed → confirmed / invalidated) for double_bottom, double_top,
 *     head_and_shoulders and inverse_head_and_shoulders;
 *   * `market_structure.ranges` reports at most ONE active consolidation range
 *     (`annotations.range`). It has no formed/confirmed lifecycle: the API only
 *     serves a range while it is active, so its state reads "Active".
 *
 * PATTERN_TYPES below also carries names and pictograms for pattern families
 * BRAIN does NOT detect (triangles, flags, wedges, channels, pennant, cup and
 * handle). That is presentation readiness only: a family is drawn when — and
 * only when — a validated backend record of that type arrives. Listing a name
 * here never creates, lists or draws anything.
 *
 * What this module may do
 * -----------------------
 * Read a stored record plus the candles already loaded on the chart and
 * describe how it is drawn:
 *
 *   * the labelled extremes ("Bottom 1", "Head" …) are the record's own
 *     components, at their stored candle times and stored prices;
 *   * each extreme gets a small arc whose turning point IS that stored point;
 *     the arc's two ends sit on real loaded candles a few bars either side,
 *     at that candle's own low (high for a top), capped at the neckline;
 *   * the dashed neckline is drawn at the stored neckline price and, for a
 *     confirmed record, ends on the stored confirmation candle;
 *   * a double pattern's dashed support/resistance line is the stored
 *     invalidation level (the lower bottom / the higher top);
 *   * the title reads "<Pattern> - <State>" with the backend's own state.
 *
 * What it may never do
 * --------------------
 *   * detect, score or re-label a pattern; no confidence figure exists here;
 *   * move a component to a neighbouring candle or to a "nicer" price;
 *   * call an unconfirmed record confirmed;
 *   * derive a target. The backend records no pattern target, so none is shown.
 */

import { patternDefaultRank } from "./chart_functions.js";
import { displayRounded, formatUtc } from "./format.js";

/**
 * Presentation metadata per pattern type.
 *
 *   drawing: "arcs"    — small arcs at the stored extremes + neckline (classical records);
 *            "range"   — the stored range box;
 *            "outline" — a thin line through the record's own components, for
 *                        a family whose records do not exist yet.
 *   labels:  names for the stored extremes, in stored order.
 *   icon:    a pictogram of the TYPE in a 0..100 x 0..60 box (y down).
 */
export const PATTERN_TYPES = Object.freeze({
  double_bottom: { title: "Double Bottom", side: "bottom", drawing: "arcs", labels: ["Bottom 1", "Bottom 2"], icon: [[6, 8], [28, 52], [50, 18], [72, 52], [94, 8]] },
  double_top: { title: "Double Top", side: "top", drawing: "arcs", labels: ["Top 1", "Top 2"], icon: [[6, 52], [28, 8], [50, 42], [72, 8], [94, 52]] },
  head_and_shoulders: { title: "Head and Shoulders", side: "top", drawing: "arcs", labels: ["Left Shoulder", "Head", "Right Shoulder"], icon: [[4, 54], [20, 24], [34, 38], [50, 6], [66, 38], [80, 24], [96, 54]] },
  inverse_head_and_shoulders: { title: "Inverse Head and Shoulders", side: "bottom", drawing: "arcs", labels: ["Left Shoulder", "Head", "Right Shoulder"], icon: [[4, 6], [20, 36], [34, 22], [50, 54], [66, 22], [80, 36], [96, 6]] },
  trading_range: { title: "Trading Range", side: "range", drawing: "range", labels: [], icon: [[4, 30], [16, 12], [30, 48], [44, 12], [58, 48], [72, 12], [86, 48], [96, 30]] },
  // --- Not detected by BRAIN today: names and pictograms only (see header). ---
  ascending_triangle: { title: "Ascending Triangle", side: "bottom", drawing: "outline", labels: [], icon: [[4, 54], [24, 10], [38, 40], [58, 10], [70, 28], [90, 10]] },
  descending_triangle: { title: "Descending Triangle", side: "top", drawing: "outline", labels: [], icon: [[4, 6], [24, 50], [38, 20], [58, 50], [70, 32], [90, 50]] },
  symmetrical_triangle: { title: "Symmetrical Triangle", side: "range", drawing: "outline", labels: [], icon: [[4, 6], [22, 54], [40, 16], [58, 44], [72, 24], [90, 34]] },
  bull_flag: { title: "Bull Flag", side: "bottom", drawing: "outline", labels: [], icon: [[4, 56], [30, 8], [44, 20], [56, 14], [70, 28], [96, 4]] },
  bear_flag: { title: "Bear Flag", side: "top", drawing: "outline", labels: [], icon: [[4, 4], [30, 52], [44, 40], [56, 46], [70, 32], [96, 56]] },
  rising_wedge: { title: "Rising Wedge", side: "top", drawing: "outline", labels: [], icon: [[4, 54], [24, 26], [38, 40], [58, 16], [70, 26], [84, 12], [96, 50]] },
  falling_wedge: { title: "Falling Wedge", side: "bottom", drawing: "outline", labels: [], icon: [[4, 6], [24, 34], [38, 20], [58, 44], [70, 34], [84, 48], [96, 10]] },
  pennant: { title: "Pennant", side: "range", drawing: "outline", labels: [], icon: [[4, 56], [26, 6], [40, 30], [54, 14], [66, 26], [76, 20], [96, 4]] },
  rising_channel: { title: "Rising Channel", side: "range", drawing: "outline", labels: [], icon: [[4, 50], [22, 30], [36, 44], [56, 22], [70, 36], [92, 12]] },
  falling_channel: { title: "Falling Channel", side: "range", drawing: "outline", labels: [], icon: [[4, 10], [22, 30], [36, 16], [56, 38], [70, 24], [92, 48]] },
  cup_and_handle: { title: "Cup and Handle", side: "bottom", drawing: "outline", labels: [], icon: [[4, 10], [18, 40], [34, 52], [52, 40], [64, 10], [74, 22], [84, 18], [96, 4]] },
});

/** Title-case names, exactly as the approved design writes them. */
export const PATTERN_TITLES = Object.freeze(Object.fromEntries(
  Object.entries(PATTERN_TYPES).map(([type, meta]) => [type, meta.title]),
));

/** Pictograms by type (kept as its own export for the card renderer). */
export const PATTERN_ICON_POINTS = Object.freeze(Object.fromEntries(
  Object.entries(PATTERN_TYPES).map(([type, meta]) => [type, meta.icon]),
));

/** A neutral pictogram for a type this screen has no name for. */
export const GENERIC_ICON_POINTS = Object.freeze([[6, 44], [26, 18], [46, 36], [66, 14], [94, 30]]);

/** The backend's lifecycle words, capitalised. Nothing else is renamed. */
const STATE_TITLES = Object.freeze({
  formed: "Formed",
  confirmed: "Confirmed",
  invalidated: "Invalidated",
  active: "Active",
});

/** Share of the gap to the neighbouring stored point an arc may span, per side. */
const ARC_SHARE = 0.3;
/** Arc half-width bounds, in candles. */
const ARC_MIN_BARS = 2;
const ARC_MAX_BARS = 8;

const UNAVAILABLE = "Unavailable";
const RANGE_TYPE = "trading_range";

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function seconds(ms) {
  const number = finite(ms);
  return number === null ? null : Math.floor(number / 1000);
}

function isoSeconds(iso) {
  if (typeof iso !== "string" || !iso) return null;
  const parsed = Date.parse(iso);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

/** "84,229.90" — a stored price, formatted only. */
export function priceText(value) {
  const number = finite(value);
  if (number === null) return null;
  const { display } = displayRounded(value, 2);
  return display ?? null;
}

function typeMeta(type) {
  return PATTERN_TYPES[type] || null;
}

export function patternTitle(type, label = null) {
  return PATTERN_TITLES[type] || label || "Pattern";
}

export function patternStateTitle(state) {
  if (STATE_TITLES[state]) return STATE_TITLES[state];
  if (typeof state === "string" && state.trim()) return state.trim();
  return "Unknown";
}

/** The pictogram for a type: its own when known, a neutral one otherwise. */
export function patternIconPoints(type) {
  return PATTERN_ICON_POINTS[type] || GENERIC_ICON_POINTS;
}

/**
 * Display tone for a record. Only a confirmed record gets the directional
 * colour (green for a bottom, red for a top); a formed one is amber, an active
 * range blue and an invalidated record neutral grey, so nothing unconfirmed
 * looks confirmed.
 */
export function patternTone(pattern) {
  const state = pattern?.state;
  if (state === "confirmed") return pattern?.side === "top" ? "red" : "green";
  if (state === "formed") return "amber";
  if (state === "active") return "blue";
  return "neutral";
}

// ---------------------------------------------------------------------------
// The records on this screen
// ---------------------------------------------------------------------------

/**
 * The range detector's active range as a record of this screen, or null.
 * Every value is the API's own; only the shape is adapted. The API serves the
 * range only while it is active — an inactive or malformed one is not shown.
 */
export function rangeRecord(evidence) {
  const range = evidence?.range;
  if (!range || typeof range !== "object" || range.active !== true) return null;
  const high = finite(range.range_high);
  const low = finite(range.range_low);
  if (high === null || low === null || high <= low || typeof range.id !== "string" || !range.id) return null;
  return {
    id: range.id,
    pattern_id: range.id,
    type: RANGE_TYPE,
    label: "Trading range",
    side: "range",
    state: "active",
    is_latest_state: true,
    source: "range detector",
    known_at: range.known_at || range.current_time || null,
    anchor_time: range.current_time || null,
    range_high: range.range_high,
    range_low: range.range_low,
    start_time: range.start_time || null,
    end_time: range.end_time || null,
    current_time: range.current_time || null,
    touch_count: range.touch_count ?? null,
    width_pct: range.width_pct ?? null,
    components: [],
  };
}

function catalogueEntry(record) {
  return {
    id: record.id,
    state: record.state || null,
    isLatestState: record.is_latest_state === true,
    knownAt: record.known_at || null,
  };
}

/**
 * Every record this screen can show for the viewed window, in the documented
 * default order: the stored pattern records ranked by patternDefaultRank
 * (current state, still standing, most recently known, id), then the active
 * range. The range comes last because its known time is always the newest
 * candle — recency alone would always pick it over every pattern record.
 */
export function chartPatternRecords(evidence) {
  if (!evidence || evidence.available !== true) return [];
  const patterns = (Array.isArray(evidence.patterns) ? evidence.patterns : [])
    .filter((pattern) => pattern && typeof pattern === "object" && typeof pattern.id === "string")
    .map((record) => ({ record, entry: catalogueEntry(record) }))
    .sort((left, right) => patternDefaultRank(left.entry, right.entry))
    .map(({ record }) => record);
  const range = rangeRecord(evidence);
  return range ? [...patterns, range] : patterns;
}

/**
 * The record to draw: the operator's explicit choice when that exact record is
 * present in this window, otherwise the first record in the default order.
 * A choice from another timeframe is simply absent here, so it is never drawn.
 */
export function chooseChartPattern(records, selectedId) {
  const list = Array.isArray(records) ? records : [];
  if (typeof selectedId === "string" && selectedId) {
    const chosen = list.find((record) => record.id === selectedId);
    if (chosen) return chosen;
  }
  return list[0] || null;
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

/** The candles the chart has loaded, as {time (s), open, high, low, close}, ascending. */
export function chartCandleIndex(candles) {
  const rows = (Array.isArray(candles) ? candles : [])
    .map((row) => (Array.isArray(row)
      ? { time: seconds(row[0]), open: finite(row[1]), high: finite(row[2]), low: finite(row[3]), close: finite(row[4]) }
      : { time: finite(row?.time), open: finite(row?.open), high: finite(row?.high), low: finite(row?.low), close: finite(row?.close) }))
    .filter((row) => row.time !== null && row.high !== null && row.low !== null)
    .sort((a, b) => a.time - b.time);
  const byTime = new Map(rows.map((row, index) => [row.time, index]));
  return { rows, byTime };
}

function storedComponents(pattern) {
  return (Array.isArray(pattern?.components) ? pattern.components : [])
    .map((point) => ({
      kind: point?.kind === "high" ? "high" : point?.kind === "low" ? "low" : null,
      timeSeconds: seconds(point?.time_ms) ?? isoSeconds(point?.time),
      price: finite(point?.price),
      time: typeof point?.time === "string" ? point.time : null,
    }))
    .filter((point) => point.kind && point.timeSeconds !== null && point.price !== null)
    .sort((a, b) => a.timeSeconds - b.timeSeconds);
}

/**
 * Small arcs around the stored extremes. One half-width (in candles) for the
 * whole record, so the arcs read as a set: ARC_SHARE of the shortest gap from
 * an extreme to its neighbouring stored point, within ARC_MIN/MAX_BARS.
 * Each end is a real loaded candle; its price is that candle's own low (high
 * for a top), never past the stored extreme and never past the neckline.
 */
function extremeArcs({ extremes, components, index, top, neckline }) {
  const componentIndices = components.map((point) => index.byTime.get(point.timeSeconds));
  let gap = Infinity;
  for (const point of extremes) {
    const at = index.byTime.get(point.timeSeconds);
    const position = componentIndices.indexOf(at);
    for (const neighbour of [componentIndices[position - 1], componentIndices[position + 1]]) {
      if (neighbour !== undefined) gap = Math.min(gap, Math.abs(neighbour - at));
    }
  }
  if (!Number.isFinite(gap)) gap = ARC_MIN_BARS / ARC_SHARE;
  const half = Math.max(ARC_MIN_BARS, Math.min(ARC_MAX_BARS, Math.round(gap * ARC_SHARE)));
  const last = index.rows.length - 1;
  const endAt = (i, extremePrice) => {
    const row = index.rows[i];
    let price = top ? row.high : row.low;
    price = top ? Math.min(price, extremePrice) : Math.max(price, extremePrice);
    if (neckline !== null) price = top ? Math.max(price, neckline) : Math.min(price, neckline);
    return { timeSeconds: row.time, price, source: "loaded candle" };
  };
  return {
    half,
    arcs: extremes.map((point) => {
      const at = index.byTime.get(point.timeSeconds);
      const leftIndex = Math.max(0, at - half);
      const rightIndex = Math.min(last, at + half);
      return {
        center: { timeSeconds: point.timeSeconds, price: point.price },
        left: leftIndex < at ? endAt(leftIndex, point.price) : null,
        right: rightIndex > at ? endAt(rightIndex, point.price) : null,
        opens: top ? "down" : "up",
      };
    }),
  };
}

function rangeAnnotation(pattern, index) {
  const high = finite(pattern.range_high);
  const low = finite(pattern.range_low);
  const from = isoSeconds(pattern.start_time);
  const to = isoSeconds(pattern.current_time) ?? isoSeconds(pattern.end_time);
  if (high === null || low === null || from === null || to === null || to <= from) return null;
  // Both ends must be loaded candles; a box is never stretched to a neighbour.
  if (!index.byTime.has(from) || !index.byTime.has(to)) return null;
  const tone = patternTone(pattern);
  return {
    id: pattern.id ?? null,
    patternId: pattern.pattern_id ?? pattern.id ?? null,
    type: RANGE_TYPE,
    drawing: "range",
    side: "range",
    state: pattern.state || null,
    tone,
    title: `${patternTitle(RANGE_TYPE)} - ${patternStateTitle(pattern.state)}`,
    extremes: [],
    arcs: [],
    outline: [],
    neckline: null,
    confirmation: null,
    extremeLine: null,
    rangeBox: { high, low, fromSeconds: from, toSeconds: to },
    labelAnchorSeconds: index.rows[Math.round((index.byTime.get(from) + index.byTime.get(to)) / 2)].time,
    fromSeconds: from,
    toSeconds: to,
  };
}

/**
 * Everything needed to draw one stored record over the candles, or null when
 * it cannot be drawn honestly (no components, or a component's candle is not
 * loaded — a point is never snapped elsewhere).
 */
export function patternAnnotation(pattern, candles) {
  if (!pattern || typeof pattern !== "object") return null;
  const index = chartCandleIndex(candles);
  if (pattern.type === RANGE_TYPE) return rangeAnnotation(pattern, index);
  const meta = typeMeta(pattern.type);
  const drawing = meta?.drawing === "arcs" ? "arcs" : "outline";
  const top = pattern.side === "top";
  const components = storedComponents(pattern);
  if (components.length < 2) return null;
  // Every component must be a loaded candle; otherwise nothing is drawn.
  if (components.some((point) => !index.byTime.has(point.timeSeconds))) return null;

  const neckline = finite(pattern.neckline);
  const extremeKind = top ? "high" : "low";
  const names = meta?.labels || [];
  const extremes = drawing === "arcs"
    ? components.filter((point) => point.kind === extremeKind).map((point, i) => ({
        ...point,
        label: names[i] || `${top ? "Top" : "Bottom"} ${i + 1}`,
      }))
    : [];
  const middles = components.filter((point) => point.kind !== extremeKind);

  // Confirmation: only a confirmed state has one, at its stored anchor candle.
  const anchorSeconds = seconds(pattern.anchor_time_ms) ?? isoSeconds(pattern.anchor_time);
  const confirmation = pattern.state === "confirmed" && neckline !== null && anchorSeconds !== null
    && index.byTime.has(anchorSeconds) && anchorSeconds > components[components.length - 1].timeSeconds
    ? { kind: "confirmation", timeSeconds: anchorSeconds, price: neckline }
    : null;

  const { arcs } = drawing === "arcs" && extremes.length
    ? extremeArcs({ extremes, components, index, top, neckline })
    : { arcs: [] };
  const firstArc = arcs[0];
  const lastArc = arcs[arcs.length - 1];
  const fromSeconds = Math.min(components[0].timeSeconds, firstArc?.left?.timeSeconds ?? Infinity);
  const shapeEnd = Math.max(components[components.length - 1].timeSeconds, lastArc?.right?.timeSeconds ?? -Infinity);
  const toSeconds = confirmation ? Math.max(confirmation.timeSeconds, shapeEnd) : shapeEnd;
  // A double pattern's dashed line is the stored invalidation level (the lower
  // bottom / the higher top), under both arcs.
  const isDouble = pattern.type === "double_bottom" || pattern.type === "double_top";
  const invalidation = finite(pattern.invalidation_level);
  const extremeLine = isDouble && arcs.length >= 2 && invalidation !== null
    ? {
        price: invalidation,
        fromSeconds: firstArc.left?.timeSeconds ?? firstArc.center.timeSeconds,
        toSeconds: lastArc.right?.timeSeconds ?? lastArc.center.timeSeconds,
      }
    : null;
  const tone = patternTone(pattern);
  return {
    id: pattern.id ?? null,
    patternId: pattern.pattern_id ?? pattern.id ?? null,
    type: pattern.type || null,
    drawing,
    side: top ? "top" : "bottom",
    state: pattern.state || null,
    tone,
    title: `${patternTitle(pattern.type, pattern.label)} - ${patternStateTitle(pattern.state)}`,
    extremes,
    middles,
    arcs,
    // The outline drawing (and the understated secondary style) joins the
    // stored components in order — nothing between them is invented.
    outline: components.map((point) => ({ timeSeconds: point.timeSeconds, price: point.price })),
    confirmation,
    neckline: neckline === null
      ? null
      : {
          price: neckline,
          fromSeconds,
          // Ends on the confirmation candle when there is one: the break is where the line stops.
          toSeconds: confirmation ? confirmation.timeSeconds : shapeEnd,
          label: "Neckline",
        },
    extremeLine,
    rangeBox: null,
    // The title sits over the record's central stored point.
    labelAnchorSeconds: components[Math.floor(components.length / 2)].timeSeconds,
    fromSeconds,
    toSeconds,
  };
}

/**
 * Other records drawn in the understated secondary style, like the approved
 * design's grey shapes: only real records, only where they do not overlap the
 * selected one (or each other), at most `limit` of them. Nearest in time to
 * the selected record first; ties keep the default order.
 */
export function secondaryAnnotations(records, primary, candles, { limit = 2 } = {}) {
  const taken = primary ? [[primary.fromSeconds, primary.toSeconds]] : [];
  const seen = new Set(primary?.patternId ? [primary.patternId] : []);
  // Nearest in time to the selected pattern first (ties keep the default
  // order), so the records drawn are the ones beside it on screen rather than
  // distant ones that would only be clipped.
  const gap = (annotation) => {
    if (!primary) return 0;
    if (annotation.toSeconds < primary.fromSeconds) return primary.fromSeconds - annotation.toSeconds;
    if (annotation.fromSeconds > primary.toSeconds) return annotation.fromSeconds - primary.toSeconds;
    return 0;
  };
  const candidates = (Array.isArray(records) ? records : [])
    .map((record, order) => {
      if (!record || record.id === primary?.id || record.is_latest_state !== true) return null;
      const annotation = patternAnnotation(record, candles);
      return annotation ? { record, annotation, order, gap: gap(annotation) } : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.gap - right.gap || left.order - right.order);
  const result = [];
  for (const { record, annotation } of candidates) {
    if (result.length >= limit) break;
    if (seen.has(annotation.patternId)) continue;
    const overlaps = taken.some(([from, to]) => annotation.fromSeconds <= to && annotation.toSeconds >= from);
    if (overlaps) continue;
    taken.push([annotation.fromSeconds, annotation.toSeconds]);
    seen.add(annotation.patternId);
    result.push({ ...annotation, secondary: true, name: patternTitle(record.type, record.label) });
  }
  return result;
}

/** Bar indices of the drawn span, for bringing it into view. */
export function annotationBarSpan(annotation, candles) {
  if (!annotation) return null;
  const index = chartCandleIndex(candles);
  const from = index.byTime.get(annotation.fromSeconds);
  const to = index.byTime.get(annotation.toSeconds);
  if (from === undefined || to === undefined) return null;
  return { fromIndex: from, toIndex: to };
}

/** True when every candle of the drawn pattern is inside the visible window. */
export function annotationFullyOnScreen(span, range) {
  if (!span || !range || !Number.isFinite(range.from) || !Number.isFinite(range.to)) return false;
  return span.fromIndex >= range.from && span.toIndex <= range.to;
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

function componentTimeText(point) {
  if (point?.time) return formatUtc(point.time);
  return Number.isFinite(point?.timeSeconds) ? formatUtc(new Date(point.timeSeconds * 1000).toISOString()) : null;
}

function joinPrices(prices) {
  if (prices.length <= 1) return prices.join("");
  return `${prices.slice(0, -1).join(", ")} and ${prices[prices.length - 1]}`;
}

/**
 * A short, readable explanation from the record's own facts. It states what
 * the detector recorded; it never predicts what price will do next.
 */
export function patternExplanation(pattern) {
  if (!pattern || typeof pattern !== "object") return null;
  if (pattern.type === RANGE_TYPE) {
    const low = priceText(pattern.range_low);
    const high = priceText(pattern.range_high);
    const since = pattern.start_time ? ` since ${formatUtc(pattern.start_time)}` : "";
    const touches = Number.isFinite(Number(pattern.touch_count)) ? `${Number(pattern.touch_count)} recorded touches` : null;
    // The stored width (a percentage) is left to Detailed mode's facts, so a
    // percentage is never shown next to the "Not calculated" confidence.
    const detail = touches || "";
    return `BRAIN's range detector found price contained between ${low ?? "the recorded low"} and ${high ?? "the recorded high"}${since}${detail ? ` (${detail})` : ""}. The range is active at the latest closed candle.`;
  }
  const top = pattern.side === "top";
  const components = (Array.isArray(pattern.components) ? pattern.components : [])
    .filter((point) => point && finite(point.price) !== null);
  const extremes = components.filter((point) => point.kind === (top ? "high" : "low"));
  const neckline = priceText(pattern.neckline);
  const extremeWord = top ? "highs" : "lows";
  const prices = extremes.map((point) => priceText(point.price)).filter(Boolean);
  let shape;
  if (pattern.type === "double_bottom" || pattern.type === "double_top") {
    shape = prices.length >= 2
      ? `Price formed two distinct ${extremeWord} at a similar level (${prices[0]} and ${prices[1]})`
      : `Price formed two distinct ${extremeWord} at a similar level`;
  } else if (pattern.type === "head_and_shoulders" || pattern.type === "inverse_head_and_shoulders") {
    shape = prices.length === 3
      ? `Price formed a ${top ? "higher high" : "lower low"} (the head, ${prices[1]}) between two shoulders (${prices[0]} and ${prices[2]})`
      : `Price formed a ${top ? "higher" : "lower"} head between two shoulders`;
  } else {
    shape = prices.length ? `The detector recorded points at ${joinPrices(prices)}` : "The detector recorded this pattern";
  }
  const breakWord = top ? "below" : "above";
  if (pattern.state === "confirmed") {
    const when = pattern.confirmed_at ? ` (${formatUtc(pattern.confirmed_at)})` : "";
    return `${shape} and closed ${breakWord} the ${neckline ?? "recorded"} neckline${when}: the detector's confirmation.`;
  }
  if (pattern.state === "invalidated") {
    const level = priceText(pattern.invalidation_level);
    const when = pattern.invalidated_at ? ` (${formatUtc(pattern.invalidated_at)})` : "";
    return `${shape}, then price closed through the ${level ?? "recorded"} invalidation level${when}, so the detector invalidated it.`;
  }
  if (pattern.state === "formed") {
    return `${shape}. Price has not closed ${breakWord} the ${neckline ?? "recorded"} neckline, so it is not confirmed.`;
  }
  return `${shape}.`;
}

function level(key, label, qualifier, value, detail, tone = "neutral") {
  const text = priceText(value);
  return {
    key,
    label,
    qualifier,
    available: text !== null,
    value: text ?? UNAVAILABLE,
    detail: text !== null ? detail : `Not recorded by the backend, so it is shown as ${UNAVAILABLE}.`,
    tone: text !== null ? tone : "neutral",
  };
}

const TARGET_DETAIL = "The backend records no pattern target, so none is shown.";

function target(key, label) {
  return { key, label, qualifier: "(Target)", available: false, value: UNAVAILABLE, detail: TARGET_DETAIL, tone: "neutral" };
}

/**
 * The three Key Levels rows, from the record only, in the approved order
 * (Neckline, Support, Resistance). The side of the stored extremes is the
 * stored invalidation level; the other side would be a target, which the
 * backend does not record, so it reads Unavailable — it is never derived.
 * A range shows its own stored high and low.
 */
export function patternKeyLevels(pattern) {
  if (pattern?.type === RANGE_TYPE) {
    return [
      { key: "neckline", label: "Breakout", qualifier: "(Target)", available: false, value: UNAVAILABLE, detail: "The range detector records no breakout level or target.", tone: "neutral" },
      level("support", "Support", "(Range Low)", pattern.range_low, "Stored range low", "neutral"),
      level("resistance", "Resistance", "(Range High)", pattern.range_high, "Stored range high", "neutral"),
    ];
  }
  const top = pattern?.side === "top";
  const tone = pattern ? patternTone(pattern) : "neutral";
  const neckline = level("neckline", "Neckline", top ? "(Breakdown)" : "(Breakout)", pattern?.neckline, "Stored neckline price", tone);
  const extremes = (Array.isArray(pattern?.components) ? pattern.components : [])
    .filter((point) => point && point.kind === (top ? "high" : "low") && finite(point.price) !== null);
  const numbers = extremes.map((point) => finite(point.price));
  const fallback = numbers.length ? (top ? Math.max(...numbers) : Math.min(...numbers)) : null;
  const stored = finite(pattern?.invalidation_level) ?? fallback;
  const heads = pattern?.type === "head_and_shoulders" || pattern?.type === "inverse_head_and_shoulders";
  const extremeQualifier = heads ? "(Head)" : top ? "(Tops)" : "(Bottoms)";
  const allExtremes = extremes.map((point) => priceText(point.price)).filter(Boolean);
  const extremeRow = level(
    top ? "resistance" : "support",
    top ? "Resistance" : "Support",
    extremeQualifier,
    stored,
    `Stored invalidation level${allExtremes.length ? ` · stored ${top ? "highs" : "lows"}: ${joinPrices(allExtremes)}` : ""}`,
  );
  if (!pattern) {
    return [neckline, level("support", "Support", "(Bottoms)", null, ""), target("resistance", "Resistance")];
  }
  return top
    ? [neckline, target("support", "Support"), extremeRow]
    : [neckline, extremeRow, target("resistance", "Resistance")];
}

/** Stored-record facts for the Detailed view of the selected record. */
export function patternRecordFacts(pattern) {
  if (!pattern) return [];
  if (pattern.type === RANGE_TYPE) {
    return [
      { label: "Record id", value: String(pattern.id || UNAVAILABLE) },
      { label: "Source", value: "Range detector (market structure)" },
      { label: "Range start", value: pattern.start_time ? formatUtc(pattern.start_time) : UNAVAILABLE },
      { label: "Last touch", value: pattern.end_time ? formatUtc(pattern.end_time) : UNAVAILABLE },
      { label: "Range high", value: priceText(pattern.range_high) ?? UNAVAILABLE },
      { label: "Range low", value: priceText(pattern.range_low) ?? UNAVAILABLE },
      { label: "Touches", value: Number.isFinite(Number(pattern.touch_count)) ? String(Number(pattern.touch_count)) : UNAVAILABLE },
      { label: "Width", value: finite(pattern.width_pct) !== null ? `${displayRounded(pattern.width_pct, 2).display}%` : UNAVAILABLE },
    ];
  }
  const points = (Array.isArray(pattern.components) ? pattern.components : []);
  return [
    { label: "Record id", value: String(pattern.id || UNAVAILABLE) },
    { label: "Known at", value: pattern.known_at ? formatUtc(pattern.known_at) : UNAVAILABLE },
    ...points.map((point, i) => ({
      label: `Point ${i + 1} (${point.kind || "?"})`,
      value: `${priceText(point.price) ?? UNAVAILABLE} · ${componentTimeText({ time: point.time, timeSeconds: seconds(point.time_ms) }) ?? UNAVAILABLE}`,
    })),
    { label: "Invalidation level", value: priceText(pattern.invalidation_level) ?? UNAVAILABLE },
  ];
}
