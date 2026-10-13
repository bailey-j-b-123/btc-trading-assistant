/**
 * The breakout / retest / continuation sequence, read from stored records only.
 *
 * The Breakouts & Retests screen is one annotated chart: a shaded reference band
 * and three numbered callouts — Breakout, Retest, Continuation — each with an
 * arrow to the candle it belongs to. This module decides what those three
 * callouts say and where they point. It is pure: given the backend payload and
 * the loaded candles it returns the three stages, and it never guesses.
 *
 * Rules it never breaks
 * ---------------------
 *  * a stage is "recorded" only when the backend stored a record for it; the
 *    callout is still drawn — the layout is the same whatever the data says —
 *    but it then reads "Not recorded" or "In Progress";
 *  * continuation is the step the backend stores no event of its own for, so it
 *    is reported as "In Progress" until one is recorded. It is never marked
 *    complete because price happens to have moved;
 *  * every price, band, timestamp, count and state in a stage is the backend's
 *    own value, passed through formatting only. Nothing is interpolated,
 *    averaged or re-derived;
 *  * a callout points at an exact loaded candle. A stage whose candle is not
 *    loaded is never snapped to a neighbouring bar — it is reported as not on
 *    the loaded candles instead.
 */

import { toSeconds } from "./evidence.js";
import { displayRounded, formatUtc, isMissing } from "./format.js";

/**
 * The three steps of the sequence, in the order they happen.
 *
 * `place` is where the numbered callout sits relative to the candle it points
 * at: the breakout and the continuation above their price action, the retest
 * below and to the right of its candle, pointing back up at it. That is the
 * arrangement of the approved layout, and it is also what keeps the three
 * callouts from stacking on top of each other when the candles are close.
 */
export const STAGE_DEFS = Object.freeze([
  Object.freeze({ key: "breakout", number: 1, title: "Breakout", place: "above", nudge: "none" }),
  Object.freeze({ key: "retest", number: 2, title: "Retest", place: "below", nudge: "right" }),
  Object.freeze({ key: "continuation", number: 3, title: "Continuation", place: "above", nudge: "none" }),
]);

/**
 * What a stage's status may say. Every one of these is a sentence about a
 * stored record, or an explicit statement that no record exists.
 */
export const STAGE_STATUS = Object.freeze({
  completed: Object.freeze({ label: "Completed", tone: "green" }),
  held: Object.freeze({ label: "Held", tone: "green" }),
  failed: Object.freeze({ label: "Failed", tone: "red" }),
  inProgress: Object.freeze({ label: "In Progress", tone: "amber" }),
  awaitingConfirmation: Object.freeze({ label: "Awaiting Confirmation", tone: "amber" }),
  notRecorded: Object.freeze({ label: "Not recorded", tone: "neutral" }),
});

const UNKNOWN = "UNKNOWN";

/** One short sentence per step, for the panel. The numbers stay in the tooltip. */
const STAGE_SUMMARY = Object.freeze({
  breakout: "Price closed through the stored resistance band.",
  retest: "Price came back to the broken band.",
  continuation: "The move on from the retest.",
});

function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function msText(ms) {
  if (!Number.isFinite(ms)) return UNKNOWN;
  return formatUtc(new Date(ms).toISOString());
}

/** Prices are shown the way the rest of the panel shows them: rounded to two places. */
function priceText(value) {
  if (isMissing(value)) return UNKNOWN;
  return displayRounded(value, 2).display;
}

function percentText(value) {
  if (isMissing(value)) return null;
  return `${displayRounded(value, 2).display}%`;
}

/** Stored records of one kind, oldest first, deterministically ordered. */
function sorted(list) {
  if (!Array.isArray(list)) return [];
  return [...list]
    .filter((item) => item && typeof item === "object")
    .sort((left, right) => {
      const leftMs = num(left.time_ms) ?? 0;
      const rightMs = num(right.time_ms) ?? 0;
      if (leftMs !== rightMs) return leftMs - rightMs;
      const leftKnown = Date.parse(left.known_at || "") || 0;
      const rightKnown = Date.parse(right.known_at || "") || 0;
      if (leftKnown !== rightKnown) return leftKnown - rightKnown;
      return String(left.id || "").localeCompare(String(right.id || ""));
    });
}

/** timeSeconds -> { index, row } for the loaded candles only. */
function loadedIndex(candles) {
  const index = new Map();
  if (!Array.isArray(candles)) return index;
  for (let position = 0; position < candles.length; position += 1) {
    const row = Array.isArray(candles[position]) ? candles[position] : null;
    const seconds = toSeconds(num(row?.[0]));
    if (seconds === null) continue;
    index.set(seconds, { index: position, row });
  }
  return index;
}

/**
 * Where the sequence stands while continuation is awaited.
 *
 * The backend stores no continuation event of its own, so the callout points at
 * the first candle after the retest that traded past the breakout candle's
 * extreme in the breakout's direction — where the move went on from — and, when
 * price has not gone past it, at the furthest candle it has reached. Either way
 * the step says plainly that this is candle data, not a recorded continuation.
 */
function continuationAnchor(candles, { breakoutIndex, fromIndex, bullish }) {
  const breakoutRow = Array.isArray(candles[breakoutIndex]) ? candles[breakoutIndex] : null;
  const level = breakoutRow ? num(bullish ? breakoutRow[2] : breakoutRow[3]) : null;
  if (level !== null) {
    for (let position = fromIndex + 1; position < candles.length; position += 1) {
      const row = Array.isArray(candles[position]) ? candles[position] : null;
      if (!row) continue;
      const value = num(bullish ? row[2] : row[3]);
      if (value === null) continue;
      if (bullish ? value > level : value < level) {
        return { index: position, value, time: num(row[0]), level, basis: "past" };
      }
    }
  }
  const extreme = extremeAfter(candles, fromIndex, bullish);
  return extreme ? { ...extreme, level, basis: "reached" } : null;
}

/** The extreme reached after `fromIndex`, in the direction the sequence ran. */
function extremeAfter(candles, fromIndex, bullish) {
  let best = null;
  for (let position = fromIndex + 1; position < candles.length; position += 1) {
    const row = Array.isArray(candles[position]) ? candles[position] : null;
    if (!row) continue;
    const time = num(row[0]);
    const value = bullish ? num(row[2]) : num(row[3]);
    if (time === null || value === null) continue;
    if (best === null || (bullish ? value > best.value : value < best.value)) {
      best = { index: position, value, time };
    }
  }
  return best;
}

/**
 * The three stages of the sequence, oldest first.
 *
 * @param {object|null} args.evidence the chart-evidence payload (or null)
 * @param {Array} args.candles stored candle rows [ms,o,h,l,c,v] on screen
 * @returns {object} `{ available, reason, direction, band, bandText, stages }`
 */
export function breakoutStages({ evidence, candles } = {}) {
  const rows = Array.isArray(candles) ? candles : [];
  const loaded = loadedIndex(rows);
  const available = Boolean(evidence) && evidence.available === true;

  const breakouts = available ? sorted(evidence.breakouts) : [];
  const retests = available ? sorted(evidence.retests) : [];
  const failures = available ? sorted(evidence.failed_breakouts) : [];
  // The backend stores no continuation event of its own. The list is read in
  // case it ever does, so the step can then be confirmed from a record instead
  // of staying "In Progress" forever.
  const continuations = available ? sorted(evidence.continuations) : [];

  // The sequence is told from one breakout: the newest one the backend has
  // already linked to a stored retest, because that is the sequence an operator
  // is looking at. With no linked retest, the newest stored breakout.
  const linked = new Set(retests.map((item) => item.breakout_id).filter(Boolean));
  const anchor = [...breakouts].reverse().find((item) => linked.has(item.id)) || breakouts.at(-1) || null;

  const shell = (def, status, detail, meta = null) => ({
    key: def.key,
    number: def.number,
    title: def.title,
    status: status.label,
    tone: status.tone,
    place: def.place,
    nudge: def.nudge,
    summary: STAGE_SUMMARY[def.key] || def.title,
    recorded: false,
    timeSeconds: null,
    timeText: UNKNOWN,
    price: null,
    barIndex: -1,
    onLoadedCandles: false,
    detail,
    meta,
  });

  if (!available) {
    return {
      available: false,
      reason: typeof evidence?.reason === "string" && evidence.reason.trim()
        ? evidence.reason.trim()
        : "the backend did not return chart evidence for this view",
      direction: null,
      band: null,
      bandText: null,
      stages: STAGE_DEFS.map((def) => shell(def, STAGE_STATUS.notRecorded, "Chart evidence unavailable, so no step is recorded.")),
    };
  }

  if (!anchor) {
    return {
      available: false,
      reason: "no breakout is stored for this window at this instant",
      direction: null,
      band: null,
      bandText: null,
      stages: STAGE_DEFS.map((def) => shell(
        def,
        def.key === "breakout" ? STAGE_STATUS.notRecorded : STAGE_STATUS.awaitingConfirmation,
        def.key === "breakout"
          ? "No breakout is stored for this window at this instant."
          : "This step follows a breakout, and none is stored yet.",
      )),
    };
  }

  const bullish = anchor.direction !== "bearish";
  const bandLow = num(anchor.band_low);
  const bandHigh = num(anchor.band_high);
  const band = bandLow !== null && bandHigh !== null ? { low: bandLow, high: bandHigh } : null;
  const bandText = band
    ? (band.low === band.high ? priceText(band.low) : `${priceText(band.low)} – ${priceText(band.high)}`)
    : null;
  const anchorSeconds = toSeconds(num(anchor.time_ms));
  const anchorEntry = anchorSeconds === null ? null : loaded.get(anchorSeconds) || null;
  const anchorClose = num(anchor.breakout_close);
  const breakoutPrice = anchorClose ?? (anchorEntry ? num(anchorEntry.row[4]) : null);

  // `recorded` means the backend stored a record for this step. A step without
  // one is drawn as an outline — on the chart and in the panel — so a step that
  // is only being watched never looks like a confirmed event.
  const finish = (def, status, detail, extra = {}) => ({
    ...shell(def, status, detail, extra.meta ?? null),
    recorded: true,
    ...extra,
  });

  // --- Step 1: the breakout itself ------------------------------------------
  const failure = failures.find((item) => item.breakout_id === anchor.id) || null;
  const confirming = num(anchor.confirmation_candles);
  const penetration = percentText(anchor.penetration_pct);
  const breakoutStage = failure
    ? finish(STAGE_DEFS[0], STAGE_STATUS.failed,
      `Price closed back through the ${bandText ?? "reference"} band; the backend recorded the failure.`,
      {
        summary: "Price broke the band, then closed back through it.",
        timeSeconds: anchorSeconds,
        timeText: msText(num(failure.time_ms) ?? num(anchor.time_ms)),
        price: num(failure.reentry_distance) !== null && band ? band.low : breakoutPrice,
        barIndex: anchorEntry ? anchorEntry.index : -1,
        onLoadedCandles: Boolean(anchorEntry),
        meta: anchorSeconds === null ? null : `${msText(num(anchor.time_ms))} · failed ${num(failure.elapsed_candles) ?? UNKNOWN} candle(s) later`,
      })
    : finish(STAGE_DEFS[0], STAGE_STATUS.completed,
      [
        breakoutPrice !== null ? `Close ${priceText(breakoutPrice)}` : "A stored close",
        `broke the ${bandText ?? "reference"} band`,
        penetration ? `by ${penetration}` : null,
      ].filter(Boolean).join(" "),
      {
        summary: `Price closed through the stored ${bullish ? "resistance" : "support"} band.`,
        timeSeconds: anchorSeconds,
        timeText: msText(num(anchor.time_ms)),
        price: breakoutPrice,
        barIndex: anchorEntry ? anchorEntry.index : -1,
        onLoadedCandles: Boolean(anchorEntry),
        meta: [
          msText(num(anchor.time_ms)),
          confirming === null ? null : `${confirming} confirming candle${confirming === 1 ? "" : "s"}`,
          anchor.reference_type ? `${anchor.reference_type} band` : null,
        ].filter(Boolean).join(" · "),
      });

  // --- Step 2: the retest of that breakout ----------------------------------
  // Linked by the backend's own breakout_id. Where a record carries no link, the
  // retests at or after the breakout on the same band are used instead; nothing
  // outside that is ever pulled in.
  const mine = retests.filter((item) => (
    item.breakout_id
      ? item.breakout_id === anchor.id
      : num(item.time_ms) !== null && num(item.time_ms) >= (num(anchor.time_ms) ?? Infinity)
  ));
  const firstRetest = mine[0] || null;
  const retestSeconds = firstRetest ? toSeconds(num(firstRetest.time_ms)) : null;
  const retestEntry = retestSeconds === null ? null : loaded.get(retestSeconds) || null;
  const retestPrice = retestEntry ? num(bullish ? retestEntry.row[3] : retestEntry.row[2]) : (bullish ? bandLow : bandHigh);
  const heldCount = mine.filter((item) => item.state === "held").length;
  const failedCount = mine.filter((item) => item.state === "failed").length;
  const observedCount = mine.filter((item) => item.state !== "held" && item.state !== "failed").length;

  const retestStage = firstRetest
    ? finish(STAGE_DEFS[1],
      heldCount > 0 ? STAGE_STATUS.held : failedCount > 0 ? STAGE_STATUS.failed : STAGE_STATUS.awaitingConfirmation,
      [
        mine.length === 1
          ? "The stored retest of this breakout"
          : `${mine.length} retest records are stored for this breakout`,
        heldCount > 0 ? `and ${heldCount === mine.length ? "all" : `${heldCount} of ${mine.length}`} held the band` : null,
        failedCount > 0 && heldCount === 0 ? `and ${failedCount} failed it` : null,
        observedCount > 0 && heldCount === 0 && failedCount === 0 ? "and none is confirmed yet" : null,
      ].filter(Boolean).join(" "),
      {
        summary: heldCount > 0
          ? "Price came back to the band and the retest held."
          : failedCount > 0
            ? "Price came back to the band and the retest failed."
            : "Price came back to the band; the backend has not confirmed it.",
        recorded: true,
        timeSeconds: retestSeconds,
        timeText: msText(num(firstRetest.time_ms)),
        price: retestPrice,
        barIndex: retestEntry ? retestEntry.index : -1,
        onLoadedCandles: Boolean(retestEntry),
        meta: [
          msText(num(firstRetest.time_ms)),
          num(firstRetest.distance_from_band) !== null ? `${priceText(firstRetest.distance_from_band)} from the band` : null,
          num(firstRetest.elapsed_candles) !== null ? `${num(firstRetest.elapsed_candles)} candle(s) after the breakout` : null,
        ].filter(Boolean).join(" · "),
      })
    : finish(STAGE_DEFS[1], STAGE_STATUS.awaitingConfirmation,
      "No retest of this breakout is stored yet.",
      {
        summary: "No return to the band has been recorded yet.",
        recorded: false,
        timeSeconds: anchorSeconds,
        timeText: msText(num(anchor.time_ms)),
        price: breakoutPrice,
        barIndex: anchorEntry ? anchorEntry.index : -1,
        onLoadedCandles: Boolean(anchorEntry),
      });

  // --- Step 3: continuation --------------------------------------------------
  // The backend stores no continuation event of its own, so this step stays
  // "In Progress" until one is recorded. Where price has traded since the
  // retest, that is reported as candle data — never as a confirmation.
  const stored = continuations.filter((item) => (item.breakout_id ? item.breakout_id === anchor.id : true));
  const sinceIndex = retestEntry ? retestEntry.index : (anchorEntry ? anchorEntry.index : -1);
  const progress = sinceIndex >= 0
    ? continuationAnchor(rows, { breakoutIndex: anchorEntry ? anchorEntry.index : sinceIndex, fromIndex: sinceIndex, bullish })
    : null;
  const continuationStage = stored.length
    ? finish(STAGE_DEFS[2], STAGE_STATUS.completed,
      "The backend recorded continuation of this breakout.",
      {
        summary: "The move went on after the retest.",
        recorded: true,
        timeSeconds: toSeconds(num(stored[0].time_ms)),
        timeText: msText(num(stored[0].time_ms)),
        price: num(stored[0].price ?? stored[0].close) ?? (progress ? progress.value : retestPrice),
        barIndex: (() => {
          const seconds = toSeconds(num(stored[0].time_ms));
          const entry = seconds === null ? null : loaded.get(seconds);
          return entry ? entry.index : -1;
        })(),
        onLoadedCandles: (() => {
          const seconds = toSeconds(num(stored[0].time_ms));
          return seconds !== null && loaded.has(seconds);
        })(),
        meta: msText(num(stored[0].time_ms)),
      })
    : finish(STAGE_DEFS[2], STAGE_STATUS.inProgress,
      "The backend has stored no continuation for this breakout, so this step is not marked complete.",
      {
        summary: "Nothing recorded yet — the move on from the retest is still being watched.",
        recorded: false,
        timeSeconds: progress ? toSeconds(progress.time) : retestSeconds,
        timeText: progress ? msText(progress.time) : (retestSeconds === null ? UNKNOWN : msText(num(firstRetest.time_ms))),
        price: progress ? progress.value : retestPrice,
        barIndex: progress ? progress.index : (retestEntry ? retestEntry.index : -1),
        onLoadedCandles: progress ? true : Boolean(retestEntry),
        meta: !progress
          ? null
          : progress.basis === "past" && progress.level !== null
            ? `Price first traded past the breakout candle's ${bullish ? "high" : "low"} (${priceText(progress.level)}) at ${priceText(progress.value)} (${msText(progress.time)}) — candle data, not a recorded continuation`
            : `Since the retest, the ${bullish ? "highest high" : "lowest low"} is ${priceText(progress.value)} (${msText(progress.time)}) — candle data, not a recorded continuation`,
      });

  return {
    available: true,
    reason: null,
    direction: bullish ? "bullish" : "bearish",
    band,
    bandText,
    breakoutId: anchor.id,
    stages: [breakoutStage, retestStage, continuationStage],
  };
}

/**
 * The span of bars the three callouts sit on, as the first and last loaded bar
 * that carries one. Null when no callout is on a loaded candle.
 */
export function stageFrame(stages) {
  const bars = (Array.isArray(stages) ? stages : [])
    .map((stage) => (Number.isFinite(stage?.barIndex) ? stage.barIndex : null))
    .filter((index) => index !== null && index >= 0);
  if (!bars.length) return null;
  return { fromIndex: Math.min(...bars), toIndex: Math.max(...bars), count: bars.length };
}

/** The callouts that can be drawn: a stage is drawn only on its own loaded candle. */
export function drawableStages(stages) {
  return (Array.isArray(stages) ? stages : []).filter((stage) => stage && Number.isFinite(stage.barIndex) && stage.barIndex >= 0);
}
