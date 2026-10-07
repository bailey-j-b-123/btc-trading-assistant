/** Display formatting preserves authoritative values; missing stays UNKNOWN. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  displayOrUnknown,
  displayPrice,
  displayRounded,
  formatDecimalText,
  formatUtc,
  familyLabel,
  isMissing,
  roundDecimalText,
} from "../../src/trading_assistant/web/static/js/format.js";

test("formatDecimalText groups thousands without altering digits", () => {
  assert.equal(formatDecimalText("1234567.89"), "1,234,567.89");
  assert.equal(formatDecimalText("-42.5"), "-42.5");
  assert.equal(formatDecimalText("0.00000001"), "0.00000001");
  assert.equal(formatDecimalText("124"), "124");
});

test("formatDecimalText refuses to invent numbers", () => {
  assert.equal(formatDecimalText(null), null);
  assert.equal(formatDecimalText(""), null);
  assert.equal(formatDecimalText("abc"), null);
  assert.equal(formatDecimalText("1.2.3"), null);
});

test("displayOrUnknown never substitutes zero for missing values", () => {
  assert.equal(displayOrUnknown(null), "UNKNOWN");
  assert.equal(displayOrUnknown(undefined), "UNKNOWN");
  assert.equal(displayOrUnknown(""), "UNKNOWN");
  assert.equal(displayOrUnknown("0"), "0"); // a real zero stays a real zero
});

test("displayPrice keeps the raw authoritative spelling", () => {
  const { display, raw } = displayPrice("124");
  assert.equal(display, "124");
  assert.equal(raw, "124");
  const missing = displayPrice(null);
  assert.equal(missing.display, "UNKNOWN");
  assert.equal(missing.raw, null);
});

test("formatUtc renders stored timestamps or UNKNOWN", () => {
  assert.equal(formatUtc("2024-01-01T21:00:00+00:00"), "2024-01-01 21:00 UTC");
  assert.equal(formatUtc(null), "UNKNOWN");
  assert.equal(formatUtc("not-a-date"), "UNKNOWN");
});

test("familyLabel falls back to the raw value, never blank", () => {
  assert.equal(familyLabel("breakout_retest_continuation"), "Breakout · retest continuation");
  assert.equal(familyLabel("future_family_x"), "future_family_x");
  assert.equal(familyLabel(null), "UNKNOWN");
});

test("isMissing treats only null/undefined/empty as missing", () => {
  assert.ok(isMissing(null));
  assert.ok(isMissing(undefined));
  assert.ok(isMissing(""));
  assert.ok(!isMissing(0));
  assert.ok(!isMissing("0"));
  assert.ok(!isMissing(false));
});

test("roundDecimalText rounds decimal strings without float error", () => {
  assert.equal(roundDecimalText("110980.123456", 2), "110980.12");
  assert.equal(roundDecimalText("2.345", 2), "2.35");
  assert.equal(roundDecimalText("2.335", 2), "2.34");
  assert.equal(roundDecimalText("0.005", 2), "0.01");
  assert.equal(roundDecimalText("0.001", 2), "0.00");
  assert.equal(roundDecimalText("9.99", 0), "10");
  assert.equal(roundDecimalText("-2.345", 2), "-2.35");
  assert.equal(roundDecimalText("-0.001", 2), "0.00");
  assert.equal(roundDecimalText("124", 2), "124.00");
  assert.equal(roundDecimalText(".5", 2), "0.50");
  assert.equal(roundDecimalText("0.29", 2), "0.29");
  assert.equal(roundDecimalText(null, 2), null);
  assert.equal(roundDecimalText("", 2), null);
  assert.equal(roundDecimalText("abc", 2), null);
  assert.equal(roundDecimalText("1.2.3", 2), null);
});

test("displayRounded groups the rounded figure and keeps the exact raw", () => {
  assert.deepEqual(displayRounded("110980.123456", 2), { display: "110,980.12", raw: "110980.123456" });
  assert.deepEqual(displayRounded("0", 2), { display: "0.00", raw: "0" });
  assert.deepEqual(displayRounded(null, 2), { display: "UNKNOWN", raw: null });
  assert.deepEqual(displayRounded("abc", 2), { display: "UNKNOWN", raw: "abc" });
});
