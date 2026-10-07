/**
 * Display formatting for authoritative backend values.
 *
 * Rules:
 *  - backend values arrive as exact decimal strings or null; raw values are
 *    never altered, recomputed, or invented;
 *  - missing/null/undefined always renders as UNKNOWN, never as zero;
 *  - formatDecimalText only adds thousands separators and trims display noise;
 *  - displayRounded shows a short rounded figure for one-glance reading while
 *    keeping the exact backend spelling in `raw` (callers surface it on
 *    hover), so rounding is presentation only and never a data change.
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

/**
 * Round an exact decimal string to `decimals` places (half away from zero),
 * operating on the string so binary floats never touch backend values.
 * Returns null for missing/non-numeric input.
 */
export function roundDecimalText(value, decimals = 2) {
  if (isMissing(value)) return null;
  const text = String(value).trim();
  const match = text.match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match || (match[2] === "" && !match[3])) return null;
  const places = Math.max(0, Math.min(12, Math.trunc(decimals)));
  const intPart = match[2] === "" ? "0" : match[2];
  const digits = intPart + (match[3] || "");
  const integerLength = intPart.length;
  // One extra digit decides the rounding; padEnd only extends short inputs.
  const padded = digits.padEnd(integerLength + places + 1, "0");
  const kept = padded.slice(0, integerLength + places).split("").map(Number);
  const deciding = Number(padded[integerLength + places]);
  if (deciding >= 5) {
    let index = kept.length - 1;
    while (index >= 0) {
      kept[index] += 1;
      if (kept[index] <= 9) break;
      kept[index] = 0;
      index -= 1;
    }
    // Carry out of "9.9..." grows the integer part by one digit.
    if (index < 0) kept.unshift(1);
  }
  const totalInteger = kept.length - places;
  const integerPart =
    (kept.slice(0, totalInteger).join("") || "0").replace(/^0+(?=\d)/, "") || "0";
  const fractionPart = kept.slice(totalInteger).join("");
  // A value that rounds to zero is "0.00", never "-0.00".
  const sign = match[1] === "-" && /[1-9]/.test(integerPart + fractionPart) ? "-" : "";
  return places === 0 ? `${sign}${integerPart}` : `${sign}${integerPart}.${fractionPart}`;
}

/**
 * One-glance rounded display plus the exact backend spelling.
 * `display` is grouped + rounded; `raw` is the untouched backend string (or
 * null when missing) for hover/title and technical details.
 */
export function displayRounded(value, decimals = 2) {
  const raw = isMissing(value) ? null : String(value);
  const rounded = roundDecimalText(value, decimals);
  if (rounded === null) return { display: UNKNOWN_TEXT, raw };
  const grouped = formatDecimalText(rounded);
  return { display: grouped === null ? UNKNOWN_TEXT : grouped, raw };
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
