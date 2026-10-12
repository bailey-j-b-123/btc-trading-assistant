import assert from "node:assert/strict";
import test from "node:test";
import { comparisonValueText, comparisonComparabilityText } from "../../src/trading_assistant/web/static/js/views/live.js";

test("a sufficiently sampled R distribution shows its average and eligible population, not a percentage floor", () => {
  const text = comparisonValueText({sample_size: 20, records_considered: 35, average: "1.25", status: "SUFFICIENT_DATA"});
  assert.match(text, /average 1\.25/);
  assert.match(text, /eligible 20\/35/);
  assert.doesNotMatch(text, /below.*floor|%/i);
});

test("missing comparability evidence remains unknown", () => {
  for (const value of [undefined, null, "true", 1]) {
    assert.equal(comparisonComparabilityText(value), "comparability UNKNOWN");
  }
  assert.equal(comparisonComparabilityText(false), "NOT COMPARABLE");
  assert.match(comparisonComparabilityText(true), /comparable/);
});

test("the backend comparison R contract does not invent a considered count", () => {
  const text = comparisonValueText({sample_size: 20, average: "-0.5", median: "-1", status: "SUFFICIENT_DATA"});
  assert.match(text, /average -0\.5.*eligible 20.*SUFFICIENT_DATA/);
  assert.doesNotMatch(text, /20\/|below.*floor|%/i);
});

test("an insufficient R sample stays withheld with its actual eligible count", () => {
  const text = comparisonValueText({sample_size: 2, average: null, status: "INSUFFICIENT_DATA"});
  assert.match(text, /average UNKNOWN.*eligible 2.*below reporting floor/);
});

test("rate comparisons retain zero percent and their own denominator", () => {
  assert.match(comparisonValueText({percentage: "0", numerator: 0, denominator: 25, status: "SUFFICIENT_DATA"}), /0%.*0\/25/);
  assert.match(comparisonValueText({percentage: null, numerator: 1, denominator: 2, status: "INSUFFICIENT_DATA"}), /below reporting floor.*1\/2/);
});

test("missing or invalid counts are unknown instead of zero", () => {
  assert.match(comparisonValueText({percentage: null}), /percentage UNKNOWN.*UNKNOWN\/UNKNOWN/);
  assert.match(comparisonValueText({sample_size: -1, average: null}), /eligible UNKNOWN/);
  assert.equal(comparisonValueText(null), "—");
  assert.equal(comparisonValueText(0), "0");
});
