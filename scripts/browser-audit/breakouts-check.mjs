#!/usr/bin/env node
/**
 * Breakouts & Retests regression against an isolated synthetic demo server.
 *
 * Proves, in a real browser and against the backend's own records, that the
 * function draws every stored breakout / failed breakout / sweep / retest at the
 * candle the backend detected it on, that the operator can reach them, and that
 * switching chart functions never moves the view.
 *
 * No API mocking, no invented events, no production database. Marker pixels are
 * measured as a DIFFERENCE against the same candles with the breakout layer off,
 * so candle pixels can never be mistaken for markers.
 *
 * Usage: node scripts/browser-audit/breakouts-check.mjs http://127.0.0.1:8123 ./out
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import puppeteer from "puppeteer-core";

const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error("URL and screenshot directory are required");
mkdirSync(out, { recursive: true });

const WIDTH = 1440;
const HEIGHT = 1080;

/** Marker colours evidence.js uses for the breakout family (breakout/sweep, retest, failed). */
const MARKER_COLOURS = ["2fbf7f", "e0565b", "a7b0c2"];

/**
 * Installed before any page script runs. Audit-only: nothing here touches the
 * application's own files. It exists so the check can read the REAL chart view
 * instead of trusting the DOM, and it never changes what the app draws.
 *  * records every chart instance the dashboard creates (the vendored export
 *    object is frozen, so the wrapper is installed as an own property on a
 *    prototype-linked shim rather than by mutating the library itself);
 *  * exposes __columnProbe(seconds), which counts marker-coloured pixels in the
 *    seven-pixel column at the exact x of a stored candle.
 */
function installProbes() {
  const colours = new Set(window.__markerColours);
  window.__brainCharts = [];
  let value;
  Object.defineProperty(window, "LightweightCharts", {
    configurable: true,
    get: () => value,
    set(next) {
      if (next && typeof next.createChart === "function") {
        const original = next.createChart;
        const shim = Object.create(next);
        Object.defineProperty(shim, "createChart", {
          enumerable: true,
          configurable: true,
          value: function instrumented(...args) {
            const chart = original.apply(next, args);
            if (chart) window.__brainCharts.push(chart);
            return chart;
          },
        });
        value = shim;
        return;
      }
      value = next;
    },
  });
  const paneCanvas = () => [...document.querySelectorAll(".terminal-chart-card canvas")]
    .sort((left, right) => right.width * right.height - left.width * left.height)[0];
  window.__paneWidth = () => {
    const canvas = paneCanvas();
    return canvas ? canvas.width : 0;
  };
  window.__columnProbe = (seconds) => {
    const chart = window.__brainCharts[0];
    const canvas = paneCanvas();
    if (!chart || !canvas) return { x: null, pixels: 0, minY: null, maxY: null };
    const x = chart.timeScale().timeToCoordinate(seconds);
    if (x === null || x === undefined) return { x: null, pixels: 0, minY: null, maxY: null };
    const left = Math.max(0, Math.round(x) - 3);
    const width = Math.min(canvas.width - left, 7);
    if (width <= 0) return { x, pixels: 0, minY: null, maxY: null };
    const data = canvas.getContext("2d").getImageData(left, 0, width, canvas.height).data;
    let pixels = 0;
    let minY = null;
    let maxY = null;
    for (let row = 0; row < canvas.height; row += 1) {
      for (let col = 0; col < width; col += 1) {
        const i = (row * width + col) * 4;
        if (data[i + 3] < 200) continue;
        const hex = [data[i], data[i + 1], data[i + 2]]
          .map((part) => part.toString(16).padStart(2, "0"))
          .join("");
        if (!colours.has(hex)) continue;
        pixels += 1;
        if (minY === null) minY = row;
        maxY = row;
      }
    }
    return { x, pixels, minY, maxY };
  };
}

const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
  args: chromium?.args || ["--no-sandbox"],
  env: { ...process.env, LANG: "en_GB.UTF-8", LC_ALL: "en_GB.UTF-8" },
});

const report = { url, generatedAt: new Date().toISOString(), checks: [] };

try {
  const page = await browser.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.evaluateOnNewDocument(`window.__markerColours = ${JSON.stringify(MARKER_COLOURS)};`);
  await page.evaluateOnNewDocument(installProbes);
  await page.setViewport({ width: WIDTH, height: HEIGHT, deviceScaleFactor: 1 });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(
    () => document.querySelector(".hero-card") &&
      document.querySelector(".terminal-chart-card canvas") &&
      !document.querySelector('[aria-busy="true"]'),
    { timeout: 180000 },
  );
  await page.evaluate(() => document.fonts.ready);

  const statusText = () => page.$eval(".chart-function-status", (node) => node.innerText);
  const logicalRange = () => page.evaluate(() => {
    const range = window.__brainCharts?.[0]?.timeScale?.().getVisibleLogicalRange?.();
    return range ? { from: Math.round(range.from * 1e6) / 1e6, to: Math.round(range.to * 1e6) / 1e6 } : null;
  });
  const probe = (seconds) => page.evaluate((value) => window.__columnProbe(value), seconds);
  /**
   * A real mouse click on a chart control. The dashboard's trading bar is sticky,
   * so the control is centred first: a click that lands under the sticky bar
   * would hit the bar instead of the button.
   */
  const clickControl = async (selector) => {
    await page.$eval(selector, (node) => node.scrollIntoView({ block: "center", inline: "nearest" }));
    await new Promise((resolveWait) => setTimeout(resolveWait, 120));
    await page.click(selector);
  };

  // --- Ground truth: the backend's own stored events, read independently of the UI.
  const stored = await page.evaluate(async () => {
    const payload = await (await fetch("/api/market/annotations")).json();
    const rows = [];
    const groups = [["breakouts", "breakout"], ["failed_breakouts", "failed_breakout"], ["sweeps", "sweep"], ["retests", "retest"]];
    for (const [group, kind] of groups) {
      for (const item of Array.isArray(payload?.[group]) ? payload[group] : []) {
        rows.push({
          id: item.id,
          kind,
          state: item.state ?? null,
          direction: item.direction ?? null,
          timeMs: Number(item.time_ms),
          seconds: Math.floor(Number(item.time_ms) / 1000),
        });
      }
    }
    return { available: payload?.available === true, counts: payload?.counts || null, events: rows.sort((a, b) => a.seconds - b.seconds) };
  });
  assert.equal(stored.available, true, "chart evidence is available on the demo server");
  assert.ok(stored.events.length > 0, "the demo database stores breakout/retest events to draw");
  const seconds = stored.events.map((event) => event.seconds);
  report.storedEvents = {
    count: stored.events.length,
    counts: stored.counts,
    distinctCandles: new Set(seconds).size,
    first: new Date(stored.events[0].timeMs).toISOString(),
    last: new Date(stored.events[stored.events.length - 1].timeMs).toISOString(),
  };

  // --- 1. Baseline: the same candles with the breakout layer off (Trade Plan default).
  const baseline = [];
  for (const value of seconds) baseline.push(await probe(value));

  // --- 2. Select Breakouts & Retests: every stored event must be drawn, view unmoved.
  const beforeSwitch = await logicalRange();
  await clickControl('[data-function="breakouts"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
  const afterSwitch = await logicalRange();
  assert.ok(beforeSwitch && afterSwitch, "the chart's visible range is readable for the check");
  assert.deepEqual(afterSwitch, beforeSwitch, "switching chart function must not move or re-zoom the view");
  report.checks.push({ name: "zoom preserved across a function switch", before: beforeSwitch, after: afterSwitch });

  assert.equal(await page.$eval(".chart-event-nav", (node) => !node.hidden), true, "the stored-event navigator appears with its function");
  const navNote = await page.$eval(".chart-event-nav-note", (node) => node.innerText);
  assert.match(navNote, new RegExp(`${stored.events.length} stored event`), "the navigator states how many events are stored");
  const status = await statusText();
  assert.match(status, new RegExp(`${stored.events.length} drawn on the loaded candles`), "every stored event is drawn");
  assert.doesNotMatch(status, /outside the recent window/, "the selected function never trims its own events");
  report.checks.push({ name: "status line", text: status, navNote });

  const itemRows = await page.$$eval(".chart-item-button", (nodes) => nodes.map((node) => node.innerText));
  assert.equal(itemRows.length, stored.events.length, "every stored event is listed and reachable");

  // --- 3. Frame events: every stored event's candle must land inside the window.
  await clickControl('[data-event-frame="true"]');
  await new Promise((resolveWait) => setTimeout(resolveWait, 400));
  const framed = await logicalRange();
  assert.ok(framed && framed.from < afterSwitch.from, "framing moves the window to the stored events");
  assert.ok(framed.from >= 0, "the framed window stays inside the loaded candles");
  const paneWidth = await page.evaluate(() => window.__paneWidth());
  const drawn = [];
  for (const value of seconds) drawn.push(await probe(value));
  for (const [index, probe_] of drawn.entries()) {
    assert.ok(probe_.x !== null, `event ${index} (${new Date(stored.events[index].timeMs).toISOString()}) is inside the visible window`);
    assert.ok(probe_.x >= 0 && probe_.x <= paneWidth, `event ${index} sits inside the chart pane (x=${probe_.x})`);
  }
  report.checks.push({ name: "framed window", range: framed, paneWidth });

  // --- 4. Marker pixels: the difference against the baseline proves they are drawn.
  const perEvent = stored.events.map((event, index) => ({
    id: event.id,
    kind: event.kind,
    state: event.state,
    direction: event.direction,
    time: new Date(event.timeMs).toISOString(),
    x: drawn[index].x,
    markerPixelsAdded: drawn[index].pixels - baseline[index].pixels,
    markerBandY: drawn[index].minY === null ? null : [drawn[index].minY, drawn[index].maxY],
  }));
  const byColumn = new Map();
  for (const row of perEvent) {
    const key = String(row.x);
    if (!byColumn.has(key)) byColumn.set(key, []);
    byColumn.get(key).push(row);
  }
  const columns = [...byColumn.entries()].map(([x, rows]) => ({
    x: Number(x),
    candle: rows[0].time,
    events: rows.length,
    markerPixelsAdded: Math.max(...rows.map((row) => row.markerPixelsAdded)),
    markerBandY: rows[0].markerBandY,
  }));
  for (const column of columns) {
    assert.ok(column.markerPixelsAdded > 0, `a marker is drawn at x=${column.x} (candle ${column.candle})`);
  }
  report.checks.push({ name: "marker pixels at the recorded candles", columns, perEvent });

  const scrollToChart = () => page.$eval(".chart-stage", (node) => window.scrollTo(0, window.scrollY + node.getBoundingClientRect().top - 120));
  await scrollToChart();
  // --- 6. The jump control reaches a stored event and keeps the zoom.
  const zoomBeforeJump = await logicalRange();
  const zoomWidth = (range) => range.to - range.from;
  // Step through the whole stored sequence: several events share a candle, so a
  // single press is not guaranteed to change candle.
  const steps = [];
  for (let index = 0; index < stored.events.length; index += 1) {
    await clickControl('[data-event-step="1"]');
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
    steps.push(await logicalRange());
  }
  for (const step of steps) {
    assert.ok(Math.abs(zoomWidth(step) - zoomWidth(zoomBeforeJump)) <= 0.001, "every jump keeps the zoom");
  }
  const distinctWindows = new Set(steps.map((step) => `${step.from}:${step.to}`));
  assert.ok(
    distinctWindows.size >= Math.min(report.storedEvents.distinctCandles, steps.length),
    `every candle holding a stored event is reachable (${distinctWindows.size} window(s))`,
  );
  const jumpedCoordinates = [];
  for (const value of seconds) jumpedCoordinates.push((await probe(value)).x);
  assert.ok(jumpedCoordinates.some((x) => x !== null), "a stored event's candle is inside the window after a jump");
  report.checks.push({ name: "jump keeps the zoom", zoomBeforeJump, steps, jumpedCoordinates });

  // --- 6b. Screenshots: the events visibly drawn at their candle locations.
  await clickControl('[data-event-frame="true"]');
  await new Promise((resolveWait) => setTimeout(resolveWait, 400));
  await scrollToChart();
  await page.screenshot({ path: resolve(out, "breakouts-1440-framed.png") });
  await scrollToChart();
  const framedStage = await page.$(".chart-stage");
  await framedStage.screenshot({ path: resolve(out, "breakouts-1440-stage.png") });
  await page.screenshot({ path: resolve(out, "breakouts-1440-full.png"), fullPage: true });

  // --- 7. Back to Trade Plan: the view must not move either.
  const beforeReturn = await logicalRange();
  await clickControl('[data-function="trade_plan"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Trade Plan"));
  assert.deepEqual(await logicalRange(), beforeReturn, "switching back does not move the view");
  assert.equal(await page.$eval(".chart-event-nav", (node) => node.hidden), true, "the navigator belongs to its function only");
  report.checks.push({ name: "zoom preserved switching back", beforeReturn, afterReturn: await logicalRange() });

  await clickControl('[data-function="breakouts"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
  await scrollToChart();
  await page.screenshot({ path: resolve(out, "breakouts-1440-after-return.png") });

  report.pageErrors = pageErrors;
  report.consoleErrors = consoleErrors;
  assert.deepEqual(pageErrors, [], "no uncaught page errors");
  writeFileSync(resolve(out, "breakouts-report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
} finally {
  await browser.close();
}
