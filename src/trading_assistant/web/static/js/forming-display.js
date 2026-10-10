/**
 * Shared UTC-bucket and stored-history adjacency rules for forming display data.
 * This module deliberately has no exchange socket, HTTP, storage, or engine path.
 */

export const FORMING_INTERVALS = Object.freeze({ "5m": 5, "15m": 15, "1h": 60, "4h": 240 });
export const FORMING_STALE_MS = 45_000;

/** Epoch-anchored UTC open time for the currently forming fixed-duration bucket. */
export function currentBucketMs(timeframe, nowMs) {
  const minutes = FORMING_INTERVALS[timeframe];
  if (!minutes || !Number.isSafeInteger(nowMs) || nowMs <= 0) return null;
  const duration = minutes * 60_000;
  return Math.floor(nowMs / duration) * duration;
}

/** A forming value is displayable only beside recent, aligned stored history. */
export function canShowForming(timeframe, confirmedOpenMs, nowMs) {
  const minutes = FORMING_INTERVALS[timeframe];
  const bucket = currentBucketMs(timeframe, nowMs);
  if (!minutes || bucket === null || !Number.isSafeInteger(confirmedOpenMs)) return false;
  const duration = minutes * 60_000;
  return confirmedOpenMs % duration === 0 &&
    confirmedOpenMs < bucket &&
    confirmedOpenMs >= bucket - duration * 2;
}
