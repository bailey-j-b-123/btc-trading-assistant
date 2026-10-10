import assert from "node:assert/strict";
import test from "node:test";

import {
  lastConfirmedClose,
  positionAgainstPrice,
  relocateBandToViewedPrice,
  relocateBands,
} from "../../src/trading_assistant/web/static/js/zone-position.js";
import { explainZoneBand } from "../../src/trading_assistant/web/static/js/explain.js";

// A 4H support band whose 4H close (62,685) sat ABOVE it, so the backend labelled it support.
const htfBand = {
  id: "4h:62000:62400",
  position: "below_price",
  display_role: "support",
  band_low: "62000",
  band_high: "62400",
  latest_close: "62685.24",
  source_timeframe: "4h",
  touch_count: 2,
  swing_high_count: 1,
  swing_low_count: 1,
  isolated: false,
  first_seen: "2026-10-03T04:00:00Z",
  last_tested: "2026-10-09T12:00:00Z",
  age_candles: 9,
  faded: false,
  source_swing_timestamps: ["2026-10-03T04:00:00Z", "2026-10-09T12:00:00Z"],
  merged_zone_count: 1,
  raw_roles: ["support"],
  htf: true,
};

test("positionAgainstPrice classifies above, below, inside (edges inclusive) and rejects unusable input", () => {
  assert.equal(positionAgainstPrice("62000", "62400", 61900), "above_price");
  assert.equal(positionAgainstPrice("62000", "62400", 62500), "below_price");
  assert.equal(positionAgainstPrice("62000", "62400", 62200), "price_inside");
  assert.equal(positionAgainstPrice("62000", "62400", 62000), "price_inside");
  assert.equal(positionAgainstPrice("62400", "62000", 62100), "price_inside", "reversed edges are normalised");
  assert.equal(positionAgainstPrice("62000", "62400", null), null);
  assert.equal(positionAgainstPrice("nope", "62400", 62100), null);
});

test("a 4H band that is support on the 4H close is relocated to resistance on a 1H chart priced below it", () => {
  const relocated = relocateBandToViewedPrice(htfBand, "61900");
  assert.equal(relocated.position, "above_price", "viewed 1H close 61,900 is below the band, so it is resistance here");
  assert.equal(relocated.display_role, "resistance");
  assert.equal(relocated.position_changed, true);
  assert.equal(relocated.position_basis, "viewed");
});

test("the source evidence (position, close, timestamps, touches) is preserved on the relocated band", () => {
  const relocated = relocateBandToViewedPrice(htfBand, "61900");
  assert.equal(relocated.source_position, "below_price");
  assert.equal(relocated.source_close, "62685.24");
  assert.equal(relocated.latest_close, "62685.24", "the detector's own close is not overwritten");
  assert.equal(relocated.viewed_close, "61900");
  assert.equal(relocated.first_seen, htfBand.first_seen);
  assert.equal(relocated.last_tested, htfBand.last_tested);
  assert.deepEqual(relocated.source_swing_timestamps, htfBand.source_swing_timestamps);
  assert.equal(relocated.touch_count, 2);
  assert.equal(relocated.id, htfBand.id);
});

test("a band the viewed price is inside is labelled inside, even though its source close put it on one side", () => {
  const relocated = relocateBandToViewedPrice(htfBand, "62100");
  assert.equal(relocated.position, "price_inside");
  assert.equal(relocated.display_role, "price_inside");
  assert.equal(relocated.position_changed, true);
});

test("when the source and viewed positions agree, nothing is marked as changed", () => {
  const relocated = relocateBandToViewedPrice(htfBand, "63000");
  assert.equal(relocated.position, "below_price");
  assert.equal(relocated.display_role, "support");
  assert.equal(relocated.position_changed, false);
});

test("without a usable viewed close the band keeps its source position and is never presented as verified", () => {
  for (const missing of [null, undefined, "", "not-a-number"]) {
    const relocated = relocateBandToViewedPrice(htfBand, missing);
    assert.equal(relocated.position, "below_price");
    assert.equal(relocated.display_role, "support");
    assert.equal(relocated.position_basis, "source");
    assert.equal(relocated.position_changed, false);
  }
});

test("relocateBands handles lists, a non-array, and a band-less input without throwing", () => {
  assert.deepEqual(relocateBands(null, "61900"), []);
  assert.deepEqual(relocateBands(undefined, "61900"), []);
  const list = relocateBands([htfBand, { ...htfBand, id: "x", band_low: "63000", band_high: "63200" }], "61900");
  assert.equal(list[0].position, "above_price");
  assert.equal(list[1].position, "above_price");
  assert.equal(list[1].position_changed, true, "inherits the source position below the 4H close, so it changed too");
});

test("lastConfirmedClose reads the close of the last API candle row and rejects malformed rows", () => {
  const rows = [
    [1791644400000, "62938.88", "62939.68", "62669.87", "62685.24", "113.32"],
    [1791648000000, "62685.24", "62813.88", "62457.48", "62612.02", "96.64"],
  ];
  assert.equal(lastConfirmedClose(rows), 62612.02);
  assert.equal(lastConfirmedClose([]), null);
  assert.equal(lastConfirmedClose(null), null);
  assert.equal(lastConfirmedClose([{ close: "1" }]), null);
});

test("the explanation states the viewed-chart position and keeps the source-timeframe close as evidence", () => {
  const relocated = relocateBandToViewedPrice(htfBand, "61900");
  const explanation = explainZoneBand(relocated, { timeframe: "1h" });
  const what = explanation.sections[0].body;
  assert.match(what, /Resistance band 62000 – 62400 on the 4H chart/);
  assert.match(what, /The band is above the latest close 61,900/);
  assert.match(what, /below the 4H close 62,685/);
  assert.match(what, /shown against the viewed close 61,900/);
  assert.doesNotMatch(explanation.title, /^Support/);
});

test("an unverified band explanation says the position is the source timeframe's own", () => {
  const unverified = relocateBandToViewedPrice(htfBand, null);
  const explanation = explainZoneBand(unverified, { timeframe: "1h" });
  assert.match(explanation.sections[0].body, /not verified against this chart/);
});

// Regression: the dashboard's zone explanation must use the close of the chart being viewed
// (`viewed_close`), not the source timeframe's `latest_close`. Otherwise an HTF band explained on
// a 1H chart reports a position that contradicts the relocated band the user is looking at.
test("selecting a zone band explains it against the viewed close, not the source close", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(
    new URL("../../src/trading_assistant/web/static/js/views/dashboard.js", import.meta.url),
    "utf8",
  );
  const call = source.match(/explainZoneBand\(band, \{[^}]*\}\)/);
  assert.ok(call, "selectZoneBand must call explainZoneBand with an explicit close");
  assert.match(call[0], /close: band\.viewed_close \?\? band\.latest_close/);
  assert.doesNotMatch(call[0], /close: band\.latest_close \?\? null/);
});
