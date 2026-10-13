/**
 * Chart Patterns presentation: the annotation drawn over the candles and the
 * three cards beside them, built from ONE stored pattern record.
 *
 * Presentation only, DOM-free and unit-tested.
 *
 * What this module may do
 * -----------------------
 * Read one backend pattern record (type, side, lifecycle state, components,
 * neckline, invalidation level, anchor time) plus the candles already loaded
 * on the chart, and describe how that record is drawn:
 *
 *   * the labelled extremes ("Bottom 1", "Bottom 2", "Head" …) are the record's
 *     own components, at their stored candle times and stored prices;
 *   * the dashed neckline is drawn at the stored neckline price;
 *   * the dashed support/resistance line is drawn at the stored extreme price
 *     (for a double bottom that is the lower stored bottom);
 *   * the confirmation point is the stored anchor candle of a CONFIRMED state,
 *     at the stored neckline price — only a confirmed record has one;
 *   * the title reads "<Pattern> - <State>" with the backend's own state.
 *
 * What it may never do
 * --------------------
 *   * detect, score or re-label a pattern; no confidence figure exists here;
 *   * move a component to a neighbouring candle or to a "nicer" price;
 *   * call an unconfirmed record confirmed;
 *   * derive a target. The backend records no pattern target, so none is shown.
 *
 * The one visual extent that is not a pattern component
 * -----------------------------------------------------
 * The approved design draws each bottom as a "U": the first bottom has a left
 * wall rising towards the neckline. The record stores no point there, so the
 * wall ends on a REAL loaded candle: the candle with the highest high (lowest
 * low for a top pattern) in the stretch immediately before the first component,
 * half as long as the first component-to-component stretch (the approved
 * drawing's wall is about half the width of the first leg). Its price is that
 * candle's own high, capped at the neckline. It is marked `kind: "wall"`, it is
 * never labelled and it is never presented as pattern evidence. When no such
 * candle is loaded the drawing simply starts at the first component.
 */

import { displayRounded, formatUtc } from "./format.js";

/** Title-case names, exactly as the approved design writes them. */
export const PATTERN_TITLES = Object.freeze({
  double_bottom: "Double Bottom",
  double_top: "Double Top",
  head_and_shoulders: "Head and Shoulders",
  inverse_head_and_shoulders: "Inverse Head and Shoulders",
});

/** The backend's lifecycle words, capitalised. Nothing else is renamed. */
const STATE_TITLES = Object.freeze({
  formed: "Formed",
  confirmed: "Confirmed",
  invalidated: "Invalidated",
});

/** Labels for the record's own extremes, in stored order. */
const EXTREME_LABELS = Object.freeze({
  double_bottom: ["Bottom 1", "Bottom 2"],
  double_top: ["Top 1", "Top 2"],
  head_and_shoulders: ["Left Shoulder", "Head", "Right Shoulder"],
  inverse_head_and_shoulders: ["Left Shoulder", "Head", "Right Shoulder"],
});

/**
 * Small outline drawings for the pattern icons, in a 0..100 x 0..60 box
 * (y down). These are pictograms of the pattern TYPE, not of the record.
 */
export const PATTERN_ICON_POINTS = Object.freeze({
  double_bottom: [[6, 8], [28, 52], [50, 18], [72, 52], [94, 8]],
  double_top: [[6, 52], [28, 8], [50, 42], [72, 8], [94, 52]],
  head_and_shoulders: [[4, 54], [20, 24], [34, 38], [50, 6], [66, 38], [80, 24], [96, 54]],
  inverse_head_and_shoulders: [[4, 6], [20, 36], [34, 22], [50, 54], [66, 22], [80, 36], [96, 6]],
});

const UNAVAILABLE = "Unavailable";

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

export function patternTitle(type, label = null) {
  return PATTERN_TITLES[type] || label || "Pattern";
}

export function patternStateTitle(state) {
  if (STATE_TITLES[state]) return STATE_TITLES[state];
  if (typeof state === "string" && state.trim()) return state.trim();
  return "Unknown";
}

/**
 * Display tone for a record. Only a confirmed record gets the directional
 * colour (green for a bottom, red for a top); a formed one is amber and an
 * invalidated one neutral grey, so nothing unconfirmed looks confirmed.
 */
export function patternTone(pattern) {
  const state = pattern?.state;
  if (state === "confirmed") return pattern?.side === "top" ? "red" : "green";
  if (state === "formed") return "amber";
  return "neutral";
}

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

/**
 * The left wall's end point: a real loaded candle (see the module header).
 * Returns null when nothing suitable is loaded.
 */
function wallPoint({ components, index, top, neckline }) {
  if (components.length < 2 || neckline === null) return null;
  const firstIndex = index.byTime.get(components[0].timeSeconds);
  const secondIndex = index.byTime.get(components[1].timeSeconds);
  if (firstIndex === undefined || secondIndex === undefined || secondIndex <= firstIndex) return null;
  const width = Math.max(1, Math.round((secondIndex - firstIndex) / 2));
  const start = Math.max(0, firstIndex - width);
  let best = null;
  for (let i = start; i < firstIndex; i += 1) {
    const row = index.rows[i];
    if (!best || (top ? row.low < best.low : row.high > best.high)) best = row;
  }
  if (!best) return null;
  const price = top ? Math.max(best.low, neckline) : Math.min(best.high, neckline);
  return { kind: "wall", timeSeconds: best.time, price, source: "loaded candle" };
}

/**
 * Everything needed to draw one stored pattern record over the candles, or
 * null when the record cannot be drawn honestly (no components, or a
 * component's candle is not loaded — a point is never snapped elsewhere).
 */
export function patternAnnotation(pattern, candles) {
  if (!pattern || typeof pattern !== "object") return null;
  const index = chartCandleIndex(candles);
  const top = pattern.side === "top";
  const components = (Array.isArray(pattern.components) ? pattern.components : [])
    .map((point) => ({
      kind: point?.kind === "high" ? "high" : point?.kind === "low" ? "low" : null,
      timeSeconds: seconds(point?.time_ms) ?? isoSeconds(point?.time),
      price: finite(point?.price),
      time: typeof point?.time === "string" ? point.time : null,
    }))
    .filter((point) => point.kind && point.timeSeconds !== null && point.price !== null)
    .sort((a, b) => a.timeSeconds - b.timeSeconds);
  if (components.length < 2) return null;
  // Every component must be a loaded candle; otherwise nothing is drawn.
  if (components.some((point) => !index.byTime.has(point.timeSeconds))) return null;

  const neckline = finite(pattern.neckline);
  const extremeKind = top ? "high" : "low";
  const names = EXTREME_LABELS[pattern.type] || [];
  const extremes = components.filter((point) => point.kind === extremeKind);
  const labelled = extremes.map((point, i) => ({
    ...point,
    label: names[i] || `${top ? "Top" : "Bottom"} ${i + 1}`,
  }));
  const middles = components.filter((point) => point.kind !== extremeKind);

  // Confirmation: only a confirmed state has one, at its stored anchor candle.
  const anchorSeconds = seconds(pattern.anchor_time_ms) ?? isoSeconds(pattern.anchor_time);
  const confirmed = pattern.state === "confirmed";
  const confirmation = confirmed && neckline !== null && anchorSeconds !== null && index.byTime.has(anchorSeconds)
    && anchorSeconds > components[components.length - 1].timeSeconds
    ? { kind: "confirmation", timeSeconds: anchorSeconds, price: neckline }
    : null;

  const wall = wallPoint({ components, index, top, neckline });
  const curve = [
    ...(wall ? [wall] : []),
    ...components.map((point) => ({ kind: "component", timeSeconds: point.timeSeconds, price: point.price })),
    ...(confirmation ? [confirmation] : []),
  ];
  const fromSeconds = curve[0].timeSeconds;
  const toSeconds = curve[curve.length - 1].timeSeconds;
  const extremePrice = extremes.length
    ? (top ? Math.max(...extremes.map((point) => point.price)) : Math.min(...extremes.map((point) => point.price)))
    : null;
  const tone = patternTone(pattern);
  return {
    id: pattern.id ?? null,
    patternId: pattern.pattern_id ?? pattern.id ?? null,
    type: pattern.type || null,
    side: top ? "top" : "bottom",
    state: pattern.state || null,
    tone,
    title: `${patternTitle(pattern.type, pattern.label)} - ${patternStateTitle(pattern.state)}`,
    extremes: labelled,
    middles,
    curve,
    wall,
    confirmation,
    neckline: neckline === null ? null : { price: neckline, fromSeconds, toSeconds, label: "Neckline" },
    extremeLine: extremePrice === null
      ? null
      : {
          price: extremePrice,
          fromSeconds: extremes[0].timeSeconds,
          toSeconds: extremes[extremes.length - 1].timeSeconds,
        },
    // The title box sits over the middle of the record (its central component).
    labelAnchorSeconds: middles.length ? middles[Math.floor((middles.length - 1) / 2)].timeSeconds
      : components[Math.floor(components.length / 2)].timeSeconds,
    fromSeconds,
    toSeconds,
  };
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

function componentTimeText(point) {
  if (point?.time) return formatUtc(point.time);
  return Number.isFinite(point?.timeSeconds) ? formatUtc(new Date(point.timeSeconds * 1000).toISOString()) : null;
}

/**
 * A short, readable explanation from the record's own facts. It states what
 * the detector recorded; it never predicts what price will do next.
 */
export function patternExplanation(pattern) {
  if (!pattern || typeof pattern !== "object") return null;
  const top = pattern.side === "top";
  const components = (Array.isArray(pattern.components) ? pattern.components : [])
    .filter((point) => point && finite(point.price) !== null);
  const extremes = components.filter((point) => point.kind === (top ? "high" : "low"));
  const neckline = priceText(pattern.neckline);
  const name = patternTitle(pattern.type, pattern.label);
  const extremeWord = top ? "highs" : "lows";
  const prices = extremes.map((point) => priceText(point.price)).filter(Boolean);
  let shape;
  if (pattern.type === "double_bottom" || pattern.type === "double_top") {
    shape = prices.length >= 2
      ? `Price formed two distinct ${extremeWord} at a similar level (${prices[0]} and ${prices[1]})`
      : `Price formed two distinct ${extremeWord} at a similar level`;
  } else {
    shape = `Price formed a ${top ? "higher" : "lower"} head between two shoulders`;
  }
  const breakWord = top ? "below" : "above";
  if (pattern.state === "confirmed") {
    const when = pattern.confirmed_at ? ` (${formatUtc(pattern.confirmed_at)})` : "";
    return `${shape} and closed ${breakWord} the ${neckline ?? "recorded"} neckline${when}, which is the detector's confirmation of this ${name.toLowerCase()}.`;
  }
  if (pattern.state === "invalidated") {
    const level = priceText(pattern.invalidation_level);
    const when = pattern.invalidated_at ? ` (${formatUtc(pattern.invalidated_at)})` : "";
    return `${shape}, then price moved through the ${level ?? "recorded"} invalidation level${when}, so the detector invalidated it.`;
  }
  if (pattern.state === "formed") {
    return `${shape}. Price has not closed ${breakWord} the ${neckline ?? "recorded"} neckline, so it is not confirmed.`;
  }
  return `${shape}.`;
}

/**
 * The three Key Levels rows, from the record only.
 *
 * Rows keep the approved order (Neckline, Support, Resistance). The side the
 * extremes sit on is filled from the record's own components; the other side
 * would be a target, which the backend does not record, so it reads
 * Unavailable — it is never derived.
 */
export function patternKeyLevels(pattern) {
  const neckline = pattern ? priceText(pattern.neckline) : null;
  const top = pattern?.side === "top";
  const extremes = (Array.isArray(pattern?.components) ? pattern.components : [])
    .filter((point) => point && point.kind === (top ? "high" : "low") && finite(point.price) !== null);
  const numbers = extremes.map((point) => finite(point.price));
  const extreme = numbers.length ? (top ? Math.max(...numbers) : Math.min(...numbers)) : null;
  const extremeText = extreme === null ? null : priceText(extreme);
  const allExtremes = extremes.map((point) => priceText(point.price)).filter(Boolean).join(" and ");
  const extremeRow = {
    available: extremeText !== null,
    value: extremeText ?? UNAVAILABLE,
    detail: extremeText !== null ? `Stored ${top ? "tops" : "bottoms"}: ${allExtremes}` : `No stored ${top ? "tops" : "bottoms"}`,
  };
  const targetRow = {
    available: false,
    value: UNAVAILABLE,
    detail: "The backend records no pattern target, so none is shown.",
  };
  return [
    {
      key: "neckline",
      label: "Neckline",
      qualifier: top ? "(Breakdown)" : "(Breakout)",
      available: neckline !== null,
      value: neckline ?? UNAVAILABLE,
      detail: neckline !== null ? "Stored neckline price" : "No neckline stored",
      tone: neckline !== null ? patternTone(pattern) : "neutral",
    },
    {
      key: "support",
      label: "Support",
      qualifier: top ? "(Target)" : "(Bottoms)",
      tone: "neutral",
      ...(top ? targetRow : extremeRow),
    },
    {
      key: "resistance",
      label: "Resistance",
      qualifier: top ? "(Tops)" : "(Target)",
      tone: "neutral",
      ...(top ? extremeRow : targetRow),
    },
  ];
}

/** Stored-record facts for the Detailed view of the selected pattern. */
export function patternRecordFacts(pattern) {
  if (!pattern) return [];
  const extremes = (Array.isArray(pattern.components) ? pattern.components : []);
  return [
    { label: "Record id", value: String(pattern.id || UNAVAILABLE) },
    { label: "Known at", value: pattern.known_at ? formatUtc(pattern.known_at) : UNAVAILABLE },
    ...extremes.map((point, i) => ({
      label: `Point ${i + 1} (${point.kind || "?"})`,
      value: `${priceText(point.price) ?? UNAVAILABLE} · ${componentTimeText({ time: point.time, timeSeconds: seconds(point.time_ms) }) ?? UNAVAILABLE}`,
    })),
    { label: "Invalidation level", value: priceText(pattern.invalidation_level) ?? UNAVAILABLE },
  ];
}
