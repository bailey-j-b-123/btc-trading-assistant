// Win rate and Total P&L tiles.
//
// The approved reference pictures showed example figures (a win rate percentage and a profit
// total). Those were illustrative. The layout keeps both tiles, and this file is the guard
// that they only ever show a figure the backend supplied:
//
//   * with today's payload — which has no paper-performance block — both read
//     "Not available yet";
//   * a supplied `win_rate` or `total_pnl` is shown, formatted, so the tiles fill in on their
//     own the moment the backend can compute them;
//   * nothing else on the page is ever used as a substitute. In particular the ledger's own
//     "Targets reached" versus "Stopped" counts are NOT a win rate, and proposed-level R
//     (Step 8's hypothetical measure) is NOT money.
import assert from "node:assert/strict";
import test from "node:test";

import {
  NOT_AVAILABLE,
  PAPER_PERFORMANCE_SOURCE,
  paperPerformanceModel,
  paperPerformanceUnavailable,
} from "../../src/trading_assistant/web/static/js/paper-performance.js";

/** A payload shaped like the real dashboard, which carries no paper-performance block. */
const RECORDED_ONLY = {
  meta: { symbol: "BTC/USDT", timeframe: "1h", as_of: "2026-10-17T23:00:00Z" },
  outcome_counts: { targets_reached: 1, stopped: 0, stopped_after_targets: 0, ambiguous: 2, incomplete_data: 1 },
  forward: {
    status: { current_state: { available: true, plan_state_counts: { PLANNABLE: 1, WATCH: 0 } } },
  },
};

const tileById = (model, id) => model.tiles.find((tile) => tile.id === id);

/**
 * A complete, valid report. Every supplied-metric test starts here and overrides one field,
 * so a test that passes is passing because of that field and not because the rest of the
 * provenance happened to be present.
 */
const report = (overrides = {}) => ({
  available: true,
  as_of: "2026-10-17T23:00:00Z",
  sample_size: 42,
  gross_or_net: "net",
  win_rate: "0.4762",
  total_pnl: { amount: "1234.5", currency: "USDT" },
  ...overrides,
});

test("with no supplied metrics both tiles say Not available yet", () => {
  const model = paperPerformanceModel(RECORDED_ONLY);
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
  assert.equal(paperPerformanceUnavailable(model), true);
});

test("no dash, no N/A, no invented zero stands in for a missing figure", () => {
  const model = paperPerformanceModel(RECORDED_ONLY);
  for (const tile of model.tiles) {
    assert.equal(tile.available, false);
    assert.doesNotMatch(tile.value, /^[-\d]/, `${tile.id} must not render a numeric value`);
    assert.doesNotMatch(tile.value, /%|USDT|USD|\$/, `${tile.id} must not render a unit it has no value for`);
  }
});

test("the ledger's own outcome counts are never turned into a win rate", () => {
  // Two targets reached, zero stopped. Counting one against the other would give a
  // confident-looking 100%, from OHLC level touches that are not fills.
  const model = paperPerformanceModel(RECORDED_ONLY);
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
});

test("a hypothetical R measure is never shown as P&L", () => {
  // Step 8 reports HYPOTHETICAL_PROPOSED_LEVEL_R, and Step 11 a slippage/fees variant. Both
  // are explicit observations about proposed levels, not realised profit, so neither may
  // reach the P&L tile.
  const model = paperPerformanceModel({
    ...RECORDED_ONLY,
    statistics: {
      hypothetical_proposed_level_r: { mean: "1.8", count: 12 },
      hypothetical_proposed_level_r_after_costs: { mean: "1.1", count: 12 },
      total_r: "21.6",
    },
  });
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
  assert.equal(paperPerformanceUnavailable(model), true);
});

test("the ledger's proposed levels and risk_per_unit are never multiplied into money", () => {
  const model = paperPerformanceModel({
    ...RECORDED_ONLY,
    plan: { state: "PLANNABLE", entry: { value: "84120" }, stop: { value: "83690" }, risk_per_unit: "430" },
    forward: { observations: { paper_plans: [{ entry: "84120", stop: "83690", risk_per_unit: "430" }] } },
  });
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
});

test("a fully provenanced report is shown: formatted rate and a signed total", () => {
  const model = paperPerformanceModel({
    ...RECORDED_ONLY,
    [PAPER_PERFORMANCE_SOURCE]: report({ win_rate: "0.4762", total_pnl: { amount: "1234.5", currency: "USDT" } }),
  });
  const rate = tileById(model, "win_rate");
  assert.equal(rate.available, true);
  assert.equal(rate.value, "47.62%");
  assert.match(rate.detail, /42 settled paper trades/);
  assert.match(rate.detail, /2026-10-17 23:00 UTC/, "the observation time is stated");

  const pnl = tileById(model, "total_pnl");
  assert.equal(pnl.value, "+1,234.50 USDT");
  assert.equal(pnl.tone, "green");
});

// --- the availability gate --------------------------------------------------------------

test("available: false suppresses every number, even numbers carried in the block", () => {
  // The dangerous case: a report that says it is unavailable but still carries a figure —
  // stale, partially rebuilt, or computed over an empty sample. Nothing may be displayed.
  const model = paperPerformanceModel({
    [PAPER_PERFORMANCE_SOURCE]: report({ available: false, win_rate: "0.99", total_pnl: { amount: "9999", currency: "USDT" } }),
  });
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
  assert.equal(tileById(model, "win_rate").available, false);
  assert.equal(tileById(model, "total_pnl").available, false);
  assert.equal(paperPerformanceUnavailable(model), true);
  assert.doesNotMatch(model.tiles.map((tile) => `${tile.value} ${tile.detail}`).join(" "), /99|9,999|%/, "no figure or unit survives the gate");
});

test("available: false keeps the backend's own reason and reports the figures as withdrawn", () => {
  const model = paperPerformanceModel({
    [PAPER_PERFORMANCE_SOURCE]: report({ available: false, reason: "The report window contains no settled outcomes.", win_rate: "0.5" }),
  });
  assert.equal(tileById(model, "win_rate").detail, "The report window contains no settled outcomes.");
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
});

test("an absent or non-true available flag is not an available report", () => {
  for (const flag of [undefined, null, false, "true", 1, 0]) {
    const model = paperPerformanceModel({
      [PAPER_PERFORMANCE_SOURCE]: report({ available: flag, win_rate: "0.5", total_pnl: { amount: "10", currency: "USDT" } }),
    });
    assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE, `available: ${String(flag)} must not display a rate`);
    assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE, `available: ${String(flag)} must not display a total`);
  }
});

// --- report provenance: sample size and observation time ---------------------------------

test("a rate without a sample size is not shown", () => {
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ sample_size: undefined, win_rate: "0.4762" }) });
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
  assert.match(tileById(model, "win_rate").detail, /no sample size/i);
});

test("a figure without an observation time is not shown", () => {
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ as_of: undefined, total_pnl: { amount: "10", currency: "USDT" }, gross_or_net: "net" }) });
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
  assert.match(tileById(model, "total_pnl").detail, /no observation time/i);
});

test("an unparseable observation time is not a boundary", () => {
  for (const asOf of ["", "not a date", "17/10/2026", null]) {
    const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ as_of: asOf, win_rate: "0.5" }) });
    assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE, `as_of ${JSON.stringify(asOf)} must be refused`);
  }
});

test("an impossible sample size is refused", () => {
  for (const size of [-1, 2.5, "", "many", null, NaN]) {
    const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ sample_size: size, win_rate: "0.5" }) });
    assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE, `sample_size ${String(size)} must be refused`);
  }
});

test("a zero sample cannot carry a rate, and says so", () => {
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ sample_size: 0, win_rate: "0" }) });
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
  assert.match(tileById(model, "win_rate").detail, /No settled paper trades to measure a rate over/);
});

// --- figure provenance: the cost basis ---------------------------------------------------

test("a gross total is captioned gross and never described as net", () => {
  const model = paperPerformanceModel({
    [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: "gross", total_pnl: { amount: "41.14", currency: "USDT" } }),
  });
  const tile = tileById(model, "total_pnl");
  assert.equal(tile.available, true);
  assert.equal(tile.value, "+41.14 USDT");
  assert.match(tile.detail, /before fees and slippage/);
  assert.match(tile.detail, /gross/);
  assert.doesNotMatch(tile.detail, /\bnet\b/, "a gross total must never read as net of costs");
  assert.equal(model.basis, "gross");
});

test("only an explicit net basis produces the net-of-costs wording", () => {
  const net = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: "net" }) });
  assert.match(tileById(net, "total_pnl").detail, /net of recorded fees and slippage/);
  assert.doesNotMatch(tileById(net, "total_pnl").detail, /gross/);
});

test("the basis is read case-insensitively and any other word is refused", () => {
  const upper = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: "NET" }) });
  assert.equal(upper.basis, "net");
  for (const basis of ["nett", "after costs", "", null, "true"]) {
    const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: basis, total_pnl: { amount: "10", currency: "USDT" } }) });
    assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE, `basis ${JSON.stringify(basis)} must be refused`);
  }
});

test("a total with no stated basis is withheld and explains why", () => {
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: undefined, total_pnl: { amount: "10", currency: "USDT" } }) });
  const tile = tileById(model, "total_pnl");
  assert.equal(tile.available, false);
  assert.equal(tile.value, NOT_AVAILABLE);
  assert.match(tile.detail, /does not say whether the total is gross or net/);
});

test("the cost basis gate applies to the total only, not to a rate over the same report", () => {
  // A win rate does not depend on fees or slippage, so an unstated basis must not suppress it.
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ gross_or_net: undefined, win_rate: "0.25", total_pnl: { amount: "10", currency: "USDT" } }) });
  assert.equal(tileById(model, "win_rate").value, "25.00%");
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
});

// --- formatting and independence, unchanged ----------------------------------------------

test("a win rate may arrive as a number or a string, and both render the same", () => {
  const asString = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ win_rate: "0.5" }) });
  const asNumber = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ win_rate: 0.5 }) });
  assert.equal(tileById(asString, "win_rate").value, "50.00%");
  assert.equal(tileById(asNumber, "win_rate").value, "50.00%");
});

test("a supplied total P&L is shown with its sign and currency", () => {
  const gained = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ total_pnl: { amount: "1234.5", currency: "USDT" } }) });
  assert.equal(tileById(gained, "total_pnl").value, "+1,234.50 USDT");
  assert.equal(tileById(gained, "total_pnl").tone, "green");
  assert.match(tileById(gained, "total_pnl").detail, /2026-10-17 23:00 UTC/);

  const lost = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ total_pnl: { amount: -41.14, currency: "USDT" } }) });
  assert.equal(tileById(lost, "total_pnl").value, "-41.14 USDT");
  assert.equal(tileById(lost, "total_pnl").tone, "red");
});

test("a zero P&L is a real figure and reads as neither a gain nor a loss", () => {
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ total_pnl: { amount: "0", currency: "USDT" } }) });
  const tile = tileById(model, "total_pnl");
  assert.equal(tile.available, true);
  assert.equal(tile.value, "+0.00 USDT");
  assert.equal(tile.tone, "neutral");
});

test("the two tiles fill in independently", () => {
  const onlyPnl = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ win_rate: null, total_pnl: { amount: "10", currency: "USDT" } }) });
  assert.equal(tileById(onlyPnl, "win_rate").value, NOT_AVAILABLE);
  assert.equal(tileById(onlyPnl, "total_pnl").available, true);
  assert.equal(paperPerformanceUnavailable(onlyPnl), false);
});

test("an explicit null from the backend stays unavailable rather than becoming zero", () => {
  // null is the backend saying "not computable". Number(null) is 0, which would otherwise
  // render a real-looking 0.00% win rate and a 0.00 P&L.
  const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report({ win_rate: null, total_pnl: null }) });
  assert.equal(tileById(model, "win_rate").value, NOT_AVAILABLE);
  assert.equal(tileById(model, "total_pnl").value, NOT_AVAILABLE);
});

test("malformed values are refused rather than rendered", () => {
  const cases = [
    ["win_rate", { win_rate: "" }],
    ["win_rate", { win_rate: "not a rate" }],
    ["win_rate", { win_rate: 1.5 }],   // a win rate above 1 is not a win rate
    ["win_rate", { win_rate: -0.2 }],
    ["total_pnl", { total_pnl: "41.14" }],              // a bare amount has no currency or provenance
    ["total_pnl", { total_pnl: { amount: "abc" } }],
    ["total_pnl", { total_pnl: { amount: null, currency: "USDT" } }],
  ];
  for (const [id, block] of cases) {
    // The other tile keeps its own valid figure: one malformed field must not take the
    // whole report down, and must not be rendered either.
    const model = paperPerformanceModel({ [PAPER_PERFORMANCE_SOURCE]: report(block) });
    const tile = tileById(model, id);
    assert.equal(tile.available, false, `${JSON.stringify(block)} must refuse ${id}`);
    assert.equal(tile.value, NOT_AVAILABLE, `${JSON.stringify(block)} must not render a value`);
  }
});

test("the backend's own reason is shown verbatim when it supplies one", () => {
  const model = paperPerformanceModel({
    [PAPER_PERFORMANCE_SOURCE]: report({ available: false, reason: "No settled outcome yet reached its observation cutoff." }),
  });
  assert.equal(tileById(model, "win_rate").detail, "No settled outcome yet reached its observation cutoff.");
  assert.equal(tileById(model, "total_pnl").detail, "No settled outcome yet reached its observation cutoff.");
});

test("with no backend reason the tile explains why the figure cannot exist yet", () => {
  const model = paperPerformanceModel(RECORDED_ONLY);
  assert.match(tileById(model, "win_rate").detail, /win and loss definition/i);
  assert.match(tileById(model, "total_pnl").detail, /not fills, sizes, fees or slippage/i);
});

test("the note states that no example figure is shown and where the plan lives", () => {
  const model = paperPerformanceModel(RECORDED_ONLY);
  assert.match(model.note, /No example figure/);
  assert.match(model.note, /not realised profit/);
});

test("a payload with no paper_performance key at all is safe", () => {
  for (const payload of [null, undefined, {}, { [PAPER_PERFORMANCE_SOURCE]: null }]) {
    const model = paperPerformanceModel(payload);
    assert.equal(model.tiles.length, 2);
    assert.equal(paperPerformanceUnavailable(model), true);
  }
});

test("the model reads exactly one payload key and invents no aliases", () => {
  // A near-miss key must not be picked up: only the documented source counts.
  const model = paperPerformanceModel({ performance: { win_rate: "0.9" }, paperPerformance: { win_rate: "0.9" } });
  assert.equal(paperPerformanceUnavailable(model), true);
});
