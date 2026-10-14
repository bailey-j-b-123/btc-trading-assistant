/**
 * Position of a support/resistance band relative to the price of the chart being VIEWED.
 *
 * Backend zone bands carry a position computed against their OWN timeframe's latest close
 * (`latest_close`, the source close). A higher-timeframe band drawn on a lower-timeframe chart
 * can therefore read "resistance" while the viewed chart's last confirmed close is already
 * above it. This module relocates the band against the viewed close for display, and keeps the
 * source evidence: `source_position`, `source_close` and the band's own timestamps are unchanged.
 *
 * It also accounts for every band the chart does not paint. The backend presents the nearest
 * bands per side; the chart further caps that for readability. Both numbers are reported, so a
 * hidden band is never silently dropped: `capBandsForDisplay` returns the bands to draw AND how
 * many were left out, by side.
 *
 * Display only. It never changes stored zones, detector output, qualification or plans.
 */

export const POSITION_ABOVE = "above_price";
export const POSITION_BELOW = "below_price";
export const POSITION_INSIDE = "price_inside";

const ROLE_FOR_POSITION = Object.freeze({
  [POSITION_ABOVE]: "resistance",
  [POSITION_BELOW]: "support",
  [POSITION_INSIDE]: "price_inside",
});

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** Position of [low, high] against a price: inside when low <= price <= high. Null when unusable. */
export function positionAgainstPrice(low, high, price) {
  const lo = finiteNumber(low);
  const hi = finiteNumber(high);
  const p = finiteNumber(price);
  if (lo === null || hi === null || p === null) return null;
  const top = Math.max(lo, hi);
  const bottom = Math.min(lo, hi);
  if (p < bottom) return POSITION_ABOVE;
  if (p > top) return POSITION_BELOW;
  return POSITION_INSIDE;
}

/** Last confirmed close of a candle array in the API shape [openMs, open, high, low, close, volume]. */
export function lastConfirmedClose(candleRows) {
  if (!Array.isArray(candleRows) || candleRows.length === 0) return null;
  const last = candleRows[candleRows.length - 1];
  return Array.isArray(last) ? finiteNumber(last[4]) : null;
}

/**
 * Distance from a price to the nearest edge of [low, high]: 0 while price is inside.
 *
 * Returned as a positive number for a band above price and a negative one for a band below,
 * which is the sign convention the backend's own `distance_from_close` uses. Null when the
 * band or the price is unusable — a missing distance is never reported as zero.
 */
export function distanceToBandEdge(low, high, price) {
  const lo = finiteNumber(low);
  const hi = finiteNumber(high);
  const p = finiteNumber(price);
  if (lo === null || hi === null || p === null) return null;
  const bottom = Math.min(lo, hi);
  const top = Math.max(lo, hi);
  if (p > top) return p - top;
  if (p < bottom) return p - top;
  return 0;
}

/**
 * Return a copy of `band` positioned against `viewedClose`.
 *
 * Source evidence is kept: `source_position` is the position the backend computed against the band's
 * own close (`latest_close`), and `source_close` is that close. When the two positions differ, the
 * copy is marked `position_changed: true` so the explanation can say so. With no usable viewed close
 * the band keeps its source position and is marked `position_basis: "source"` so it is never shown
 * as verified against the viewed chart.
 *
 * The distance to the nearest band edge is recomputed against the close this chart is showing
 * (`viewed_distance` / `viewed_distance_pct`); the backend's own distance against the source
 * close stays on `distance_from_close` and is never overwritten.
 */
export function relocateBandToViewedPrice(band, viewedClose) {
  if (!band || typeof band !== "object") return band;
  const sourcePosition = band.source_position || band.position || null;
  const sourceClose = band.source_close ?? band.latest_close ?? null;
  const viewed = finiteNumber(viewedClose);
  if (viewed === null) {
    return {
      ...band,
      source_position: sourcePosition,
      source_close: sourceClose,
      position_basis: "source",
      position_changed: false,
      viewed_close: null,
      viewed_distance: null,
      viewed_distance_pct: null,
    };
  }
  const position = positionAgainstPrice(band.band_low, band.band_high, viewed) || sourcePosition;
  const distance = distanceToBandEdge(band.band_low, band.band_high, viewed);
  return {
    ...band,
    position,
    display_role: ROLE_FOR_POSITION[position] || band.display_role || "price_inside",
    source_position: sourcePosition,
    source_close: sourceClose,
    viewed_close: String(viewedClose),
    viewed_distance: distance,
    viewed_distance_pct: distance === null || viewed === 0 ? null : (distance / viewed) * 100,
    position_basis: "viewed",
    position_changed: Boolean(sourcePosition && sourcePosition !== position),
  };
}

/** Relocate every band in a list against the viewed close. Non-array input yields an empty list. */
export function relocateBands(bands, viewedClose) {
  if (!Array.isArray(bands)) return [];
  return bands.map((band) => relocateBandToViewedPrice(band, viewedClose));
}

const EMPTY_SIDE_COUNTS = Object.freeze({ above_price: 0, below_price: 0, price_inside: 0 });

const EMPTY_CAP = Object.freeze({
  shown: Object.freeze([]),
  hidden: 0,
  hiddenBySide: EMPTY_SIDE_COUNTS,
  countsBySide: EMPTY_SIDE_COUNTS,
  nearest: Object.freeze({ above: null, below: null, inside: null }),
});

/**
 * Chart display cap (presentation only; the stored zones are unchanged).
 *
 * Keeps the bands nearest to the viewed close: `perSide` above, `perSide` below and `inside`
 * containing price. Everything else is counted in `hidden`, broken down by side, so the caller can
 * state exactly how many bands were not drawn and where they sat. Nearest is measured from the
 * viewed close, because relocation can change the backend's order.
 */
export function capBandsForDisplay(bands, { perSide = 1, inside = 1 } = {}) {
  if (!Array.isArray(bands)) return { ...EMPTY_CAP };
  const above = [];
  const below = [];
  const within = [];
  for (const band of bands) {
    if (band?.position === POSITION_ABOVE) above.push(band);
    else if (band?.position === POSITION_BELOW) below.push(band);
    else within.push(band);
  }
  // Nearest first, measured from the price this chart is showing. When a band
  // carries no viewed close (a caller that skipped relocation), the band's own
  // price bounds order it instead: the lowest band above price, the highest
  // band below it. Neither is ever treated as "distance zero".
  const proximityOf = (band, fallbackLow, fallbackHigh) => {
    const viewedDistance = finiteNumber(band?.viewed_distance);
    if (viewedDistance !== null) return Math.abs(viewedDistance);
    const viewedClose = finiteNumber(band?.viewed_close);
    if (viewedClose !== null) {
      const edge = distanceToBandEdge(band.band_low, band.band_high, viewedClose);
      if (edge !== null) return Math.abs(edge);
    }
    const low = finiteNumber(band?.band_low);
    const high = finiteNumber(band?.band_high);
    if (low === null || high === null) return Number.POSITIVE_INFINITY;
    return fallbackLow ? low : -high;
  };
  above.sort((a, b) => proximityOf(a, true, false) - proximityOf(b, true, false));
  below.sort((a, b) => proximityOf(a, false, true) - proximityOf(b, false, true));
  within.sort((a, b) => (finiteNumber(b?.touch_count) || 0) - (finiteNumber(a?.touch_count) || 0));
  const shown = [...above.slice(0, perSide), ...below.slice(0, perSide), ...within.slice(0, inside)];
  const shownSet = new Set(shown);
  return {
    shown,
    hidden: bands.length - shown.length,
    hiddenBySide: {
      above_price: above.filter((band) => !shownSet.has(band)).length,
      below_price: below.filter((band) => !shownSet.has(band)).length,
      price_inside: within.filter((band) => !shownSet.has(band)).length,
    },
    countsBySide: { above_price: above.length, below_price: below.length, price_inside: within.length },
    nearest: { above: above[0] || null, below: below[0] || null, inside: within[0] || null },
  };
}
