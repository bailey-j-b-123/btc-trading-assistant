/** Freshness badge mapping: deterministic verdicts only, never LIVE. */

import test from "node:test";
import assert from "node:assert/strict";

import { freshnessBadge, isCurrentData } from "../../src/trading_assistant/web/static/js/freshness.js";

test("CURRENT badge only when the backend says CURRENT", () => {
  const badge = freshnessBadge({ status: "CURRENT", staleness_intervals: 0 });
  assert.equal(badge.label, "CURRENT");
  assert.equal(badge.tone, "green");
  assert.ok(isCurrentData({ status: "CURRENT" }));
});

test("STALE badge carries the exact staleness count", () => {
  const badge = freshnessBadge({ status: "STALE", staleness_intervals: 5 });
  assert.equal(badge.label, "STALE (5 candles behind)");
  assert.equal(badge.tone, "amber");
  assert.ok(!isCurrentData({ status: "STALE" }));
});

test("HISTORICAL data is never labelled live/current", () => {
  const badge = freshnessBadge({ status: "HISTORICAL" });
  assert.equal(badge.label, "HISTORICAL");
  assert.ok(badge.detail.includes("never live"));
  assert.ok(!isCurrentData({ status: "HISTORICAL" }));
});

test("UNKNOWN badge for missing data", () => {
  const badge = freshnessBadge({ status: "UNKNOWN" });
  assert.equal(badge.label, "UNKNOWN");
  assert.equal(badge.tone, "neutral");
  const none = freshnessBadge(null);
  assert.equal(none.label, "UNKNOWN");
});

test("the word LIVE is never used anywhere in badge output", () => {
  for (const status of ["CURRENT", "STALE", "HISTORICAL", "UNKNOWN"]) {
    const badge = freshnessBadge({ status, staleness_intervals: 1 });
    assert.ok(!badge.label.includes("LIVE"));
  }
});
