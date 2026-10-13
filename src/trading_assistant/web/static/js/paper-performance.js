/**
 * Paper-trading performance tiles: Win rate and Total P&L.
 *
 * The approved dashboard design carries these two tiles, and the figures in the reference
 * pictures were illustrative only. They must eventually be real numbers computed from BRAIN's
 * recorded paper trades, so this module does exactly one thing: it reads a metric when the
 * backend supplies one, and otherwise shows `Not available yet`. It never derives a figure
 * from anything else on the page.
 *
 * What it deliberately does NOT do, because doing so would misreport a paper observation as a
 * trading result:
 *
 *  * It never counts "Targets reached" against "Stopped" to make a win rate. The ledger's
 *    outcome states describe proposed levels that price touched on stored candles. They are
 *    not fills, and no win/loss definition has been agreed yet.
 *  * It never converts proposed-level R (Step 8's `HYPOTHETICAL_PROPOSED_LEVEL_R`, or Step 11's
 *    slippage-and-fees variant) into money. Those are explicit hypothetical observations, not
 *    realised profit.
 *  * It never multiplies a price distance by a size, because the ledger stores no size, no
 *    fees and no slippage.
 *
 * The backend contract it reads is documented in `docs/paper-performance-reporting.md`. The
 * shape is intentionally narrow and exact: a `paper_performance` object on the dashboard
 * payload, with `win_rate` as a fraction in [0, 1] and `total_pnl` as `{ amount, currency }`.
 * Anything absent, unparseable or out of range reads as unavailable rather than as a guessed
 * number, and an explicit block-level `reason` is shown verbatim.
 */

import { formatUtc, isMissing } from "./format.js";

/** The one sentence shown wherever BRAIN cannot yet compute a real figure. */
export const NOT_AVAILABLE = "Not available yet";

/** The payload key a future backend supplies. Exact match; no aliases, no guessing. */
export const PAPER_PERFORMANCE_SOURCE = "paper_performance";

const PERCENT_DECIMALS = 2;
const MONEY_DECIMALS = 2;

function finiteNumberOrNull(value) {
  if (isMissing(value)) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/**
 * A win rate the backend supplied, as a fraction in [0, 1].
 *
 * A value outside that range is not a win rate, so it reads as unavailable: showing it would
 * misreport a broken figure as a real result. `null` (the backend's way of saying "not
 * computable") is preserved rather than becoming zero.
 */
function winRateOrNull(value) {
  const number = finiteNumberOrNull(value);
  if (number === null) return null;
  return number >= 0 && number <= 1 ? number : null;
}

/** `{ amount, currency }` with a finite amount, or null. */
function moneyOrNull(value) {
  if (!value || typeof value !== "object") return null;
  const amount = finiteNumberOrNull(value.amount);
  if (amount === null) return null;
  const currency = isMissing(value.currency) ? null : String(value.currency).trim();
  return { amount, currency: currency || null };
}

/** Group the integer part, keep the sign explicit, and always show two decimals. */
function formatMoney(amount, currency) {
  const negative = amount < 0;
  const fixed = Math.abs(amount).toFixed(MONEY_DECIMALS);
  const [whole, fraction] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const sign = negative ? "-" : "+";
  return `${sign}${grouped}.${fraction}${currency ? ` ${currency}` : ""}`;
}

/** A fit-for-display percentage: 0.4762 -> "47.62%". Formatting only; the value is untouched. */
function formatWinRate(fraction) {
  return `${(fraction * 100).toFixed(PERCENT_DECIMALS)}%`;
}

/**
 * Build the two tiles from the dashboard payload.
 *
 * Returns `{ tiles, note, sampleLabel, asOfLabel }`. Each tile carries `available`,
 * `value` (the display string, already formatted) and `detail`. Nothing here reads
 * outcome counts, price levels or R multiples.
 */
export function paperPerformanceModel(dashboard) {
  const block = dashboard?.[PAPER_PERFORMANCE_SOURCE];
  const usable = block && typeof block === "object" ? block : null;
  const backendReason = usable && !isMissing(usable.reason) ? String(usable.reason) : null;
  const winRate = usable ? winRateOrNull(usable.win_rate) : null;
  const money = usable ? moneyOrNull(usable.total_pnl) : null;
  const sampleSize = usable ? finiteNumberOrNull(usable.sample_size) : null;
  const asOf = usable && !isMissing(usable.as_of) ? String(usable.as_of) : null;

  const tiles = [
    {
      id: "win_rate",
      label: "Win rate",
      available: winRate !== null,
      value: winRate !== null ? formatWinRate(winRate) : NOT_AVAILABLE,
      tone: "neutral",
      detail: winRate !== null
        ? `recorded paper trades${sampleSize === null ? "" : ` (${sampleSize} settled)`}`
        : backendReason || "No settled paper trades with an agreed win and loss definition yet.",
    },
    {
      id: "total_pnl",
      label: "Total P&L",
      available: money !== null,
      value: money !== null ? formatMoney(money.amount, money.currency) : NOT_AVAILABLE,
      tone: money === null ? "neutral" : money.amount > 0 ? "green" : money.amount < 0 ? "red" : "neutral",
      detail: money !== null
        ? `${asOf ? `as of ${formatUtc(asOf)} · ` : ""}net of recorded fees and slippage`
        : backendReason || "The ledger stores proposed levels and outcome states, not fills, sizes, fees or slippage.",
    },
  ];

  return {
    tiles,
    sampleLabel: sampleSize === null ? null : `${sampleSize} settled paper trade${sampleSize === 1 ? "" : "s"}`,
    asOfLabel: asOf === null ? null : formatUtc(asOf),
    note: "Both tiles stay empty until BRAIN can compute them from recorded paper trades. "
      + "No example figure is shown: proposed-level observations are not realised profit, and no "
      + "win or loss definition has been agreed yet. The follow-up plan covers the definitions, "
      + "trade sizing, fees, slippage and denominators these two figures require.",
  };
}

/** True when neither metric is available, so a caller can say so once instead of twice. */
export function paperPerformanceUnavailable(model) {
  return Boolean(model) && model.tiles.every((tile) => tile.available !== true);
}
