/**
 * Display formatting for authoritative backend values.
 *
 * Rules:
 *  - backend values arrive as exact decimal strings or null; raw values are
 *    never altered, recomputed, or invented;
 *  - missing/null/undefined always renders as UNKNOWN, never as zero;
 *  - formatting only adds thousands separators and trims display noise.
 */

export const UNKNOWN_TEXT = "UNKNOWN";

export function isMissing(value) {
  return value === null || value === undefined || value === "";
}

/** Add thousands separators to the integer part of an exact decimal string. */
export function formatDecimalText(value) {
  if (isMissing(value)) return null;
  const text = String(value).trim();
  if (!/^[+-]?(\d+(\.\d*)?|\.\d+)$/.test(text)) return null;
  const sign = text.startsWith("-") ? "-" : text.startsWith("+") ? "+" : "";
  const unsigned = sign ? text.slice(1) : text;
  const [integerPart, fractionPart] = unsigned.split(".");
  const grouped = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = fractionPart === undefined ? "" : `.${fractionPart}`;
  return `${sign}${grouped}${fraction}`;
}

/** Display a price-like value, preserving the raw authoritative spelling. */
export function displayPrice(value) {
  const formatted = formatDecimalText(value);
  return { display: formatted === null ? UNKNOWN_TEXT : formatted, raw: isMissing(value) ? null : String(value) };
}

/** Display a decimal or fall back to UNKNOWN; never substitutes zero. */
export function displayOrUnknown(value) {
  if (isMissing(value)) return UNKNOWN_TEXT;
  const formatted = formatDecimalText(value);
  return formatted === null ? String(value) : formatted;
}

/** UTC rendering of an ISO-8601 timestamp; missing stays UNKNOWN. */
export function formatUtc(iso, { withDate = true, withSeconds = false } = {}) {
  if (isMissing(iso)) return UNKNOWN_TEXT;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return UNKNOWN_TEXT;
  const date = parsed.toISOString().slice(0, 10);
  const time = parsed.toISOString().slice(11, withSeconds ? 19 : 16);
  return withDate ? `${date} ${time} UTC` : `${time} UTC`;
}

export function formatDate(iso) {
  if (isMissing(iso)) return UNKNOWN_TEXT;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return UNKNOWN_TEXT;
  return parsed.toISOString().slice(0, 10);
}

export function formatTime(iso) {
  if (isMissing(iso)) return UNKNOWN_TEXT;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return UNKNOWN_TEXT;
  return parsed.toISOString().slice(11, 16);
}

/** Human labels for setup families (display only; raw value preserved). */
const FAMILY_LABELS = {
  breakout_retest_continuation: "Breakout · retest continuation",
  failed_breakout_sweep_reversal: "Failed breakout · sweep reversal",
  range_rejection_reversal: "Range rejection reversal",
};

export function familyLabel(family) {
  if (isMissing(family)) return UNKNOWN_TEXT;
  return FAMILY_LABELS[family] ?? family;
}

export function directionLabel(direction) {
  if (isMissing(direction)) return UNKNOWN_TEXT;
  return direction === "bullish" ? "Bullish" : direction === "bearish" ? "Bearish" : direction;
}

export function directionArrow(direction) {
  if (direction === "bullish") return "▲";
  if (direction === "bearish") return "▼";
  return "—";
}

/** Short id for traceability chips. */
export function shortId(id, length = 10) {
  if (isMissing(id)) return UNKNOWN_TEXT;
  const text = String(id);
  return text.length <= length ? text : `${text.slice(0, length)}…`;
}
