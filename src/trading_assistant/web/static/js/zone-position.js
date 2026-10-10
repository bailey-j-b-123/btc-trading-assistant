/**
 * Position of a support/resistance band relative to the price of the chart being VIEWED.
 *
 * Backend zone bands carry a position computed against their OWN timeframe's latest close
 * (`latest_close`, the source close). A higher-timeframe band drawn on a lower-timeframe chart
 * can therefore read "resistance" while the viewed chart's last confirmed close is already
 * above it. This module relocates the band against the viewed close for display, and keeps the
 * source evidence: `source_position`, `source_close` and the band's own timestamps are unchanged.
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
 * Return a copy of `band` positioned against `viewedClose`.
 *
 * Source evidence is kept: `source_position` is the position the backend computed against the band's
 * own close (`latest_close`), and `source_close` is that close. When the two positions differ, the
 * copy is marked `position_changed: true` so the explanation can say so. With no usable viewed close
 * the band keeps its source position and is marked `position_basis: "source"` so it is never shown
 * as verified against the viewed chart.
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
    };
  }
  const position = positionAgainstPrice(band.band_low, band.band_high, viewed) || sourcePosition;
  return {
    ...band,
    position,
    display_role: ROLE_FOR_POSITION[position] || band.display_role || "price_inside",
    source_position: sourcePosition,
    source_close: sourceClose,
    viewed_close: String(viewedClose),
    position_basis: "viewed",
    position_changed: Boolean(sourcePosition && sourcePosition !== position),
  };
}

/** Relocate every band in a list against the viewed close. Non-array input yields an empty list. */
export function relocateBands(bands, viewedClose) {
  if (!Array.isArray(bands)) return [];
  return bands.map((band) => relocateBandToViewedPrice(band, viewedClose));
}
