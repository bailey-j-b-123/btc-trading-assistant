/**
 * Chart history paging, as pure logic (no DOM, no chart library).
 *
 * The chart loads the newest page of closed candles first. Scrolling to the left edge asks the
 * backend for the page strictly older than the oldest loaded candle (`before`). Rows are
 * [timestamp_ms, open, high, low, close, volume] exactly as the backend returns them.
 */

/** Candles requested per page. Matches the first load so every page has the same shape. */
export const CHART_PAGE_SIZE = 500;

/** Logical-index distance from the left edge that triggers an older-page request. */
export const HISTORY_EDGE_BARS = 40;

/** Empty pages (windows of stored holes) followed automatically, so a user is never stuck on one. */
export const MAX_EMPTY_PAGE_CHAIN = 8;

/** ISO-8601 UTC string for a millisecond timestamp, in the backend's `Z` form. */
export function isoFromMs(ms) {
  return new Date(ms).toISOString().replace(".000Z", "Z");
}

function finiteMs(value) {
  if (typeof value === "number") return Number.isFinite(value) && value > 0 ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) && ms > 0 ? ms : null;
}

/** Oldest and newest stored open times as milliseconds, or null when unknown. */
export function storedSpanMs(payload) {
  return {
    earliestMs: finiteMs(payload?.earliest_stored_open),
    latestMs: finiteMs(payload?.latest_stored_open),
  };
}

/**
 * History state after a page or first load.
 *
 * `cursorMs` is the oldest instant the chart has checked: the oldest loaded row, or the page's
 * `window_start` when that page was empty (for example a window of stored holes). Using the
 * window start means paging always moves backwards, even through a stretch with no candles.
 * `hasOlder` is true only when the stored earliest candle is older than the cursor.
 */
export function historyState({ payload, rows }) {
  const { earliestMs, latestMs } = storedSpanMs(payload);
  const firstLoadedMs = Array.isArray(rows) && rows.length && Array.isArray(rows[0]) ? finiteMs(rows[0][0]) : null;
  const windowStartMs = finiteMs(payload?.window_start);
  const candidates = [firstLoadedMs, windowStartMs].filter((value) => value !== null);
  const cursorMs = candidates.length ? Math.min(...candidates) : null;
  const hasOlder = earliestMs !== null && cursorMs !== null && earliestMs < cursorMs;
  return {
    earliestMs,
    latestMs,
    firstLoadedMs,
    cursorMs,
    hasOlder,
    earliestReached: earliestMs !== null && cursorMs !== null && !hasOlder,
    loading: false,
    error: null,
  };
}

/** Whether a visible-range change should request an older page. */
export function shouldLoadOlder(state, range) {
  if (!state || state.loading || !state.hasOlder) return false;
  if (!range || !Number.isFinite(range.from)) return false;
  return range.from < HISTORY_EDGE_BARS;
}

/**
 * Merge an older page into the loaded rows.
 *
 * Only rows strictly older than the oldest loaded row are kept, so boundary candles are never
 * duplicated. Invalid rows are dropped. Returns the combined ascending rows and how many were added.
 */
export function mergeOlderRows(existingRows, olderRows) {
  const existing = Array.isArray(existingRows) ? existingRows : [];
  const incoming = Array.isArray(olderRows) ? olderRows : [];
  const firstMs = existing.length && Array.isArray(existing[0]) ? finiteMs(existing[0][0]) : null;
  const ceiling = firstMs === null ? Infinity : firstMs;
  const seen = new Set();
  const fresh = [];
  for (const row of incoming) {
    if (!Array.isArray(row) || row.length < 5) continue;
    const ms = finiteMs(row[0]);
    if (ms === null || ms >= ceiling || seen.has(ms)) continue;
    seen.add(ms);
    fresh.push(row);
  }
  fresh.sort((a, b) => a[0] - b[0]);
  return { rows: [...fresh, ...existing], added: fresh.length };
}

/** "2026-01-01 00:00 UTC" for a millisecond timestamp. */
export function utcLabel(ms) {
  return `${isoFromMs(ms).slice(0, 16).replace("T", " ")} UTC`;
}

/** Plain-English status for the chart note: stored span, and whether older history exists. */
export function historyNote(label, state) {
  if (!state) return `${label} stored closed candles.`;
  if (state.error) return `${label}: older candles could not be loaded (${state.error}). The newer candles shown are unchanged.`;
  const span = state.earliestMs !== null && state.latestMs !== null
    ? ` · stored ${utcLabel(state.earliestMs)} → ${utcLabel(state.latestMs)}`
    : "";
  if (state.loading) return `${label}: loading earlier candles…${span}`;
  if (state.earliestReached) return `${label}: earliest stored candle reached${span}.`;
  if (state.hasOlder) return `${label}: scroll left to load earlier candles${span}.`;
  return `${label} stored closed candles${span}.`;
}
