/**
 * Freshness badge mapping. The backend computes the deterministic verdict;
 * this module only maps it to display text. "LIVE" is deliberately not part
 * of the vocabulary: CURRENT means the latest expected closed candle is
 * stored, nothing more.
 */

import { formatUtc, isMissing } from "./format.js";

export const FRESHNESS_TONES = {
  CURRENT: "green",
  STALE: "amber",
  HISTORICAL: "blue",
  UNKNOWN: "neutral",
};

export function freshnessBadge(freshness) {
  if (!freshness || isMissing(freshness.status)) {
    return { label: "UNKNOWN", tone: "neutral", detail: "No freshness information is available." };
  }
  const status = freshness.status;
  const tone = FRESHNESS_TONES[status] ?? "neutral";
  if (status === "CURRENT") {
    return {
      label: "CURRENT",
      tone,
      detail: "The latest expected closed candle is stored. Closed-candle data — not a live tick feed.",
    };
  }
  if (status === "STALE") {
    const n = freshness.staleness_intervals;
    const gap = Number.isInteger(n) && n > 0 ? ` (${n} candle${n === 1 ? "" : "s"} behind)` : "";
    return {
      label: `STALE${gap}`,
      tone,
      detail: "Stored candles stop before the expected latest closed candle.",
    };
  }
  if (status === "HISTORICAL") {
    const n = freshness.staleness_intervals;
    const gap = Number.isInteger(n) && n > 0 ? ` (${n} interval${n === 1 ? "" : "s"} behind now)` : "";
    return {
      label: `HISTORICAL${gap}`,
      tone,
      detail: "This screen evaluates a past instant; it is never live data.",
    };
  }
  return {
    label: "UNKNOWN",
    tone,
    detail: "No stored candles (or an unusable series) for this instrument/timeframe.",
  };
}

export function asOfLine(meta, freshness) {
  const asOf = meta && meta.as_of ? formatUtc(meta.as_of) : "UNKNOWN";
  const latest = freshness && freshness.latest_stored ? formatUtc(freshness.latest_stored) : "no stored candle";
  return `As of ${asOf} · latest stored candle ${latest}`;
}

/** True only when the backend explicitly says the data is current. */
export function isCurrentData(freshness) {
  return Boolean(freshness) && freshness.status === "CURRENT";
}
