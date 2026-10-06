/** Display formatting preserves authoritative values; missing stays UNKNOWN. */

import test from "node:test";
import assert from "node:assert/strict";

import {
  displayOrUnknown,
  displayPrice,
  formatDecimalText,
  formatUtc,
  familyLabel,
  isMissing,
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
