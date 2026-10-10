// Shaded support/resistance bands: positioning, classes, label collisions, selection, cleanup.
// The Node test runner has no DOM, so a minimal element stub records what the module creates.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { clearZoneBands, renderZoneBands, setZoneBands } from "../../src/trading_assistant/web/static/js/chart.js";

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
  click() { const event = { stopPropagation() { this.stopped = true; } }; for (const fn of this.listeners.click || []) fn(event); return event; }
}

globalThis.document = { createElement: (tag) => new FakeElement(tag) };

/** Price→pixel mapping: 10px per price unit, top of the pane is 110. */
const PX_PER_PRICE = 10;
const TOP_PRICE = 110;
function makeHandle(height = 400) {
  const container = new FakeElement("div");
  container.clientHeight = height;
  container.clientWidth = 800;
  const subs = { visible: [], cross: [] };
  return {
    destroyed: false,
    container,
    series: { priceToCoordinate: (price) => (TOP_PRICE - price) * PX_PER_PRICE },
    chart: {
      timeScale: () => ({
        subscribeVisibleLogicalRangeChange: (fn) => subs.visible.push(fn),
        unsubscribeVisibleLogicalRangeChange: (fn) => { subs.visible = subs.visible.filter((f) => f !== fn); },
      }),
      subscribeCrosshairMove: (fn) => subs.cross.push(fn),
      unsubscribeCrosshairMove: (fn) => { subs.cross = subs.cross.filter((f) => f !== fn); },
    },
    subs,
    zoneUnsubscribers: [],
    zoneBandsState: null,
    zoneLayer: null,
  };
}

function band(overrides = {}) {
  return {
    id: "1h:100:101",
    position: "below_price",
    display_role: "support",
    band_low: "100",
    band_high: "101",
    touch_count: 2,
    source_timeframe: "1h",
    faded: false,
    isolated: false,
    ...overrides,
  };
}

const bandNodes = (handle) => handle.zoneLayer.children.filter((node) => node.className.startsWith("zone-band "));
const labelNodes = (handle) => handle.zoneLayer.children.filter((node) => node.className === "zone-band-label");

test("each display band is a shaded element at the coordinates of its edges", () => {
  const handle = makeHandle();
  setZoneBands(handle, [band()]);
  const [node] = bandNodes(handle);
  // high 101 -> y 90 ; low 100 -> y 100 : top 90px, height 10px.
  assert.equal(node.style.top, "90px");
  assert.equal(node.style.height, "10px");
  assert.match(node.className, /zone-band--below_price/);
  assert.equal(node.style["--zone-edge"], "#2ec4b6", "support uses the teal edge");
});

test("bands above price, containing price and htf bands get distinct classes", () => {
  const handle = makeHandle();
  setZoneBands(handle, [
    band({ id: "a", position: "above_price", display_role: "resistance", band_low: "105", band_high: "107" }), // visible: 110 is the top of this fake pane
    band({ id: "b", position: "price_inside", display_role: "price_inside", band_low: "104", band_high: "106" }),
    band({ id: "c", position: "below_price", display_role: "support", band_low: "90", band_high: "92", htf: true, faded: true }),
  ]);
  const classes = bandNodes(handle).map((node) => node.className);
  assert.ok(classes.some((c) => c.includes("zone-band--above_price")));
  assert.ok(classes.some((c) => c.includes("zone-band--price_inside")));
  const htf = classes.find((c) => c.includes("zone-band--below_price"));
  assert.ok(htf.includes("zone-band--htf") && htf.includes("zone-band--faded"));
});

test("labels that would overlap are stacked at least 16px apart", () => {
  const handle = makeHandle();
  setZoneBands(handle, [
    band({ id: "x", band_low: "100.0", band_high: "100.2" }),
    band({ id: "y", band_low: "100.3", band_high: "100.5", display_role: "resistance", position: "above_price" }),
    band({ id: "z", band_low: "100.6", band_high: "100.8" }),
  ]);
  const ys = labelNodes(handle).map((node) => Number.parseFloat(node.style.top)).sort((a, b) => a - b);
  assert.equal(ys.length, 3);
  for (let index = 1; index < ys.length; index += 1) {
    assert.ok(ys[index] - ys[index - 1] >= 16, `labels ${ys[index - 1]} and ${ys[index]} collide`);
  }
});

test("clicking a band's label reports that band and does not propagate to the chart", () => {
  const handle = makeHandle();
  const picked = [];
  setZoneBands(handle, [band({ id: "pick-me" })], { onSelect: (chosen) => picked.push(chosen.id) });
  const [label] = labelNodes(handle);
  const event = label.click();
  assert.deepEqual(picked, ["pick-me"]);
  assert.equal(event.stopped, true, "candle clicks under the label must not be swallowed twice");
  assert.match(label.attributes["aria-label"] ?? label.getAttribute?.("aria-label") ?? "", /Open explanation/);
});

test("bands entirely outside the visible range are counted, not drawn", () => {
  const handle = makeHandle(400);
  setZoneBands(handle, [
    band({ id: "far-above", band_low: "200", band_high: "210" }),
    band({ id: "visible", band_low: "100", band_high: "101" }),
  ]);
  assert.equal(bandNodes(handle).length, 1);
  const note = handle.zoneLayer.children.find((node) => node.className === "zone-band-offview");
  assert.ok(note && /1 zone outside the visible price range/.test(note.textContent));
});

test("re-rendering replaces the previous bands instead of accumulating them", () => {
  const handle = makeHandle();
  setZoneBands(handle, [band({ id: "one" }), band({ id: "two", band_low: "95", band_high: "96" })]);
  setZoneBands(handle, [band({ id: "one" })]);
  assert.equal(bandNodes(handle).length, 1);
  assert.equal(labelNodes(handle).length, 1);
});

test("invalid or non-object entries are ignored rather than drawn", () => {
  const handle = makeHandle();
  setZoneBands(handle, [null, "x", band({ band_low: "not-a-number" })]);
  assert.equal(bandNodes(handle).length, 0);
});

test("clearing removes the layer, releases the chart subscriptions and is safe to repeat", () => {
  const handle = makeHandle();
  setZoneBands(handle, [band()]);
  assert.equal(handle.subs.visible.length, 1);
  assert.equal(handle.subs.cross.length, 1);
  const layer = handle.zoneLayer;
  clearZoneBands(handle);
  assert.equal(layer.removed, true);
  assert.equal(handle.zoneLayer, null);
  assert.equal(handle.subs.visible.length, 0, "range-change listener released");
  assert.equal(handle.subs.cross.length, 0, "crosshair listener released");
  clearZoneBands(handle);
  assert.equal(handle.zoneLayer, null);
});

test("a destroyed chart never receives bands", () => {
  const handle = makeHandle();
  handle.destroyed = true;
  setZoneBands(handle, [band()]);
  assert.equal(handle.zoneLayer, null);
});

test("renderZoneBands is a pure read: it does not change the stored band state", () => {
  const handle = makeHandle();
  setZoneBands(handle, [band()]);
  const before = JSON.stringify(handle.zoneBandsState.bands);
  renderZoneBands(handle);
  assert.equal(JSON.stringify(handle.zoneBandsState.bands), before);
});

test("CSS keeps candles clickable under the bands: only labels take pointer events", () => {
  const css = readFileSync(new URL("../../src/trading_assistant/web/static/styles.css", import.meta.url), "utf8");
  const bandRule = css.match(/\.zone-band\s*\{[^}]*\}/)?.[0] ?? "";
  const layerRule = css.match(/\.zone-band-layer\s*\{[^}]*\}/)?.[0] ?? "";
  const labelRule = css.match(/\.zone-band-label\s*\{[^}]*\}/)?.[0] ?? "";
  assert.match(layerRule, /pointer-events:\s*none/);
  assert.match(bandRule, /pointer-events:\s*none/);
  assert.match(labelRule, /pointer-events:\s*auto/);
});
