// Plan risk and reward areas: the shaded region between entry and stop (what the plan
// risks) and between entry and the final target (what it targets).
//
// Two things are checked here. First, the regions come from the plan's own levels and
// nothing else: a plan missing a level shades only what it really states, and no area is
// invented. Second, the approved design's colours — blue entry, red stop / risk, green
// targets / reward — which are tied to the stylesheet's own palette tokens rather than to
// hard-coded hexes in the test.
//
// The Node test runner has no DOM, so a minimal element stub records what the module creates.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  applyOverlays,
  clearOverlays,
  OVERLAY_COLORS,
  planAreasFor,
} from "../../src/trading_assistant/web/static/js/chart.js";

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.className = "";
    this.style = { setProperty(key, value) { this[key] = value; } };
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this._text = "";
    this.removed = false;
  }
  get textContent() { return this._text; }
  set textContent(value) {
    this._text = String(value);
    if (value === "" || value === null) this.children = [];
  }
  appendChild(child) { this.children.push(child); return child; }
  remove() { this.removed = true; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

globalThis.document = { createElement: (tag) => new FakeElement(tag) };

/** Price→pixel mapping: 10px per price unit, top of the pane is 110. */
const PX_PER_PRICE = 10;
const TOP_PRICE = 110;
function makeHandle(height = 400) {
  const container = new FakeElement("div");
  container.clientHeight = height;
  container.clientWidth = 800;
  const lines = new Set();
  return {
    destroyed: false,
    container,
    // applyOverlays only runs for a series that can carry price lines, so the stub
    // mirrors the two methods the vendored library actually exposes.
    series: {
      priceToCoordinate: (price) => (TOP_PRICE - price) * PX_PER_PRICE,
      createPriceLine: (options) => { const line = { options }; lines.add(line); return line; },
      removePriceLine: (line) => { lines.delete(line); },
    },
    lines,
    chart: {
      timeScale: () => ({
        subscribeVisibleLogicalChange: () => {},
        subscribeVisibleLogicalRangeChange: () => {},
        unsubscribeVisibleLogicalRangeChange: () => {},
      }),
      subscribeCrosshairMove: () => {},
      unsubscribeCrosshairMove: () => {},
    },
    zoneUnsubscribers: [],
    zoneBandsState: null,
    zoneLayer: null,
  };
}

/** A PLANNABLE plan whose levels all sit inside the stub's visible price range. */
const PLAN = {
  state: "PLANNABLE",
  direction: "bullish",
  entry: { value: "100" },
  stop: { value: "96" },
  targets: [{ level: { value: "105" } }, { level: { value: "108" } }],
};

const areasIn = (handle) => handle.container.children
  .flatMap((child) => child.children)
  .filter((node) => String(node.className).startsWith("plan-area"));

// --- The regions themselves -------------------------------------------------------------

test("risk and reward come from the plan's own entry, stop and final target", () => {
  assert.deepEqual(planAreasFor(PLAN), [
    { kind: "risk", low: 96, high: 100 },
    { kind: "reward", low: 100, high: 108 },
  ]);
});

test("reward uses the LAST target, not an earlier one", () => {
  const [, reward] = planAreasFor(PLAN);
  assert.equal(reward.high, 108);
});

test("a plan with no stop shades only the reward region", () => {
  const areas = planAreasFor({ entry: { value: "100" }, targets: [{ level: { value: "108" } }] });
  assert.deepEqual(areas, [{ kind: "reward", low: 100, high: 108 }]);
});

test("a plan with no targets shades only the risk region", () => {
  const areas = planAreasFor({ entry: { value: "100" }, stop: { value: "96" }, targets: [] });
  assert.deepEqual(areas, [{ kind: "risk", low: 96, high: 100 }]);
});

test("no entry means no regions at all — nothing is measured from a missing level", () => {
  assert.deepEqual(planAreasFor({ stop: { value: "96" }, targets: [{ level: { value: "108" } }] }), []);
  assert.deepEqual(planAreasFor({}), []);
  assert.deepEqual(planAreasFor(null), []);
  assert.deepEqual(planAreasFor(undefined), []);
});

test("a level equal to entry is skipped rather than drawn as a zero-height region", () => {
  assert.deepEqual(planAreasFor({ entry: { value: "100" }, stop: { value: "100" }, targets: [] }), []);
});

test("null and blank levels are never coerced to zero", () => {
  // Number(null) is 0, which would draw a region down to price 0. Every absent shape
  // must stay absent instead.
  assert.deepEqual(planAreasFor({ entry: { value: "100" }, stop: { value: null }, targets: [{ level: { value: null } }] }), []);
  assert.deepEqual(planAreasFor({ entry: { value: null }, stop: { value: "96" } }), []);
  assert.deepEqual(planAreasFor({ entry: { value: "" }, stop: { value: "96" } }), []);
  assert.deepEqual(planAreasFor({ entry: { value: "not a price" }, stop: { value: "96" } }), []);
});

test("a short (bearish) plan still yields an ordered region", () => {
  const areas = planAreasFor({
    entry: { value: "100" },
    stop: { value: "105" },
    targets: [{ level: { value: "90" } }],
  });
  assert.deepEqual(areas, [
    { kind: "risk", low: 100, high: 105 },
    { kind: "reward", low: 90, high: 100 },
  ]);
});

// --- Wired into the overlay pass --------------------------------------------------------

test("applyOverlays puts the plan's regions on the handle and draws them", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  assert.deepEqual(handle.planAreas, [
    { kind: "risk", low: 96, high: 100 },
    { kind: "reward", low: 100, high: 108 },
  ]);
  assert.deepEqual(areasIn(handle).map((node) => node.className), ["plan-area plan-area--risk", "plan-area plan-area--reward"]);
});

test("each region is positioned at the coordinates of its two levels", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  const [risk, reward] = areasIn(handle);
  // Risk spans entry ($100 → 100px) down to the stop ($96 → 140px).
  assert.equal(risk.style.top, "100px");
  assert.equal(risk.style.height, "40px");
  // Reward spans entry up to the final target ($108 → 20px).
  assert.equal(reward.style.top, "20px");
  assert.equal(reward.style.height, "80px");
});

test("regions are decorative: hidden from assistive tech and click-through", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  for (const area of areasIn(handle)) assert.equal(area.attributes["aria-hidden"], "true");
  const css = readFileSync(new URL("../../src/trading_assistant/web/static/styles.css", import.meta.url), "utf8");
  const rule = css.match(/\.plan-area\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(rule, /pointer-events:\s*none/);
});

test("the plan-levels switch turns the regions off with the lines", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: { planLevels: false } } });
  assert.deepEqual(handle.planAreas, []);
  assert.equal(areasIn(handle).length, 0);
});

test("no plan means no regions", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: null, prefs: { overlays: {} } });
  assert.deepEqual(handle.planAreas, []);
  assert.equal(areasIn(handle).length, 0);
});

test("a plan the engine does not consider PLANNABLE is not drawn", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: { ...PLAN, state: "WAITING" }, prefs: { overlays: {} } });
  assert.equal(handle.lines.size, 0);
  assert.deepEqual(handle.planAreas, []);
});

test("clearing overlays removes the regions", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  assert.equal(areasIn(handle).length, 2);
  clearOverlays(handle);
  assert.deepEqual(handle.planAreas, []);
  assert.equal(areasIn(handle).length, 0);
});

test("re-applying overlays never accumulates regions", () => {
  const handle = makeHandle();
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  applyOverlays(handle, { overlays: {}, plan: PLAN, prefs: { overlays: {} } });
  assert.equal(areasIn(handle).length, 2);
});

// --- The approved design's colours ------------------------------------------------------

/** A custom property from the stylesheet's :root block, so the test tracks the real palette. */
function token(name) {
  const css = readFileSync(new URL("../../src/trading_assistant/web/static/styles.css", import.meta.url), "utf8");
  const root = css.match(/:root\s*\{[^}]*\}/)?.[0] ?? "";
  return root.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1].trim();
}

test("approved design colours: blue entry, red stop, green targets", () => {
  assert.equal(OVERLAY_COLORS.entry, token("blue"));
  assert.equal(OVERLAY_COLORS.stop, token("red"));
  assert.equal(OVERLAY_COLORS.targets, token("green"));
});

test("the three plan colours stay distinct from each other", () => {
  const seen = new Set([OVERLAY_COLORS.entry, OVERLAY_COLORS.stop, OVERLAY_COLORS.targets]);
  assert.equal(seen.size, 3);
});
