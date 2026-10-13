#!/usr/bin/env node
/**
 * Breakouts & Retests regression against an isolated synthetic demo server.
 *
 * Proves, in a real browser and against the backend's own records, that the
 * screen is the annotated layout: a large candlestick chart with a shaded
 * reference band, exactly three numbered callouts (1 Breakout, 2 Retest,
 * 3 Continuation) each with an arrow to its own candle, volume along the bottom,
 * a BTC/USDT summary card and a Breakout Logic panel with the three steps and
 * their recorded statuses — and that reaching them never resets the zoom.
 *
 * No API mocking, no invented events, no production database.
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

/**
 * Installed before any page script runs. Audit-only: nothing here touches the
 * application's own files, and nothing here changes what the app draws. It
 * exists so the check can read the REAL chart view instead of trusting the DOM.
 *
 * The vendored library export is frozen, so the wrapper is installed as an own
 * property on a prototype-linked shim rather than by mutating the library.
 */
function installProbes() {
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
  /** x of one candle, straight from the chart's own time scale. */
  window.__xOf = (seconds) => {
    const chart = window.__brainCharts?.[0];
    if (!chart) return null;
    const x = chart.timeScale().timeToCoordinate(seconds);
    return x === null || x === undefined ? null : x;
  };
  /** y of one price, straight from the candlestick series. */
  window.__yOfPrice = (price) => {
    const series = window.__brainCharts?.[0] && window.__brainCharts[0].__series;
    if (!series) return null;
    return series.priceToCoordinate(price);
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
  const xOf = (seconds) => page.evaluate((value) => window.__xOf(value), seconds);
  /**
   * A real mouse click on a chart control. The dashboard's trading bar is sticky,
   * so the control is centred first: a click that lands under the sticky bar
   * would hit the bar instead of the button.
   */
  const clickControl = async (selector) => {
    await page.waitForSelector(selector, { visible: true, timeout: 30000 });
    await page.$eval(selector, (node) => node.scrollIntoView({ block: "center", inline: "nearest" }));
    await new Promise((resolveWait) => setTimeout(resolveWait, 150));
    await page.click(selector);
  };
  const calloutState = () => page.evaluate(() => {
    const nodes = [...document.querySelectorAll(".breakout-anno")];
    const arrows = [...document.querySelectorAll(".breakout-anno-arrow")];
    return {
      badges: nodes.map((node) => ({
        stage: node.dataset.stage,
        number: node.querySelector(".breakout-anno-num")?.textContent || null,
        title: node.querySelector(".breakout-anno-title")?.textContent || null,
        status: node.querySelector(".breakout-anno-status")?.textContent || null,
        left: Number.parseFloat(node.style.left),
        top: Number.parseFloat(node.style.top),
      })),
      arrows: arrows.map((node) => ({ x: Number.parseFloat(node.style.left), y: Number.parseFloat(node.style.top) })),
      lines: document.querySelectorAll(".breakout-anno-line").length,
      offView: document.querySelector(".breakout-anno-offview")?.textContent || null,
    };
  });

  // --- Ground truth: the backend's own stored events, read independently of the UI.
  const stored = await page.evaluate(async () => {
    const payload = await (await fetch("/api/market/annotations")).json();
    const rows = [];
    const groups = [["breakouts", "breakout"], ["failed_breakouts", "failed_breakout"], ["sweeps", "sweep"], ["retests", "retest"]];
    for (const [group, kind] of groups) {
      for (const item of Array.isArray(payload?.[group]) ? payload[group] : []) {
        rows.push({ id: item.id, kind, state: item.state ?? null, direction: item.direction ?? null, timeMs: Number(item.time_ms), seconds: Math.floor(Number(item.time_ms) / 1000), bandLow: item.band_low ?? null, bandHigh: item.band_high ?? null });
      }
    }
    return { available: payload?.available === true, counts: payload?.counts || null, events: rows.sort((a, b) => a.seconds - b.seconds) };
  });
  assert.equal(stored.available, true, "chart evidence is available on the demo server");
  assert.ok(stored.events.length > 0, "the demo database stores breakout/retest events to show");
  const breakoutSeconds = stored.events.filter((event) => event.kind === "breakout").map((event) => event.seconds)[0];
  const retestStates = stored.events.filter((event) => event.kind === "retest").map((event) => event.state);
  const retestSeconds = stored.events.filter((event) => event.kind === "retest").map((event) => event.seconds)[0];
  const band = { low: Number(stored.events.find((event) => event.kind === "breakout")?.bandLow), high: Number(stored.events.find((event) => event.kind === "breakout")?.bandHigh) };
  report.storedEvents = {
    count: stored.events.length,
    counts: stored.counts,
    breakoutCandle: new Date(breakoutSeconds * 1000).toISOString(),
    retestCandle: new Date(retestSeconds * 1000).toISOString(),
    retestStates,
    band,
  };

  // --- 1. Selecting the function must not move the view.
  const beforeSwitch = await logicalRange();
  await clickControl('[data-function="breakouts"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
  const afterSwitch = await logicalRange();
  assert.ok(beforeSwitch && afterSwitch, "the chart's visible range is readable for the check");
  assert.deepEqual(afterSwitch, beforeSwitch, "switching chart function must not move or re-zoom the view");
  report.checks.push({ name: "zoom preserved across a function switch", before: beforeSwitch, after: afterSwitch });

  // --- 2. The status line names the three steps.
  const status = await statusText();
  assert.match(status, /1 Breakout:/, "step 1 is named");
  assert.match(status, /2 Retest:/, "step 2 is named");
  assert.match(status, /3 Continuation: In Progress/, "continuation is not claimed complete");
  assert.doesNotMatch(status, /3 Continuation: Confirmed/);
  report.checks.push({ name: "status line", text: status });

  // --- 3. Right side: BTC/USDT summary card above the Breakout Logic panel.
  const rightSide = await page.evaluate(() => {
    const slot = document.querySelector(".chart-function-slot");
    const summary = document.querySelector(".market-summary-card");
    const panel = document.querySelector(".chart-function-panel");
    if (!slot || !summary || !panel) return null;
    const summaryBox = summary.getBoundingClientRect();
    const panelBox = panel.getBoundingClientRect();
    const chartBox = document.querySelector(".terminal-chart-wrap").getBoundingClientRect();
    return {
      order: summary.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING ? "summary-then-panel" : "panel-then-summary",
      summaryAbove: summaryBox.bottom <= panelBox.top + 1,
      rightOfChart: summaryBox.left >= chartBox.right - 1,
      symbol: summary.querySelector(".msc-symbol")?.textContent || null,
      summaryRows: [...summary.querySelectorAll(".msc-label")].map((node) => node.textContent),
      heading: panel.querySelector(".cfp-heading")?.textContent || null,
      chip: panel.querySelector(".cfp-chip")?.textContent || null,
      band: [...panel.querySelectorAll(".cfp-block")].map((node) => node.querySelector(".cfp-block-title")?.textContent).filter(Boolean),
      steps: [...panel.querySelectorAll(".cfp-stage")].map((node) => ({
        stage: node.dataset.stage,
        number: node.querySelector(".cfp-stage-num")?.textContent || null,
        title: node.querySelector(".cfp-stage-title")?.textContent || null,
        status: node.querySelector(".cfp-stage-status")?.textContent || null,
        tone: node.querySelector(".cfp-stage-status")?.dataset.tone || null,
        detail: node.querySelector(".cfp-stage-detail")?.textContent || null,
        meta: node.querySelector(".cfp-stage-meta")?.textContent || null,
      })),
      note: panel.querySelector(".cfp-note")?.textContent || null,
    };
  });
  assert.ok(rightSide, "the right-hand column renders the summary card and the panel");
  assert.equal(rightSide.order, "summary-then-panel", "the summary card is above the Breakout Logic panel");
  assert.ok(rightSide.summaryAbove, "and it sits above it, not beside it");
  assert.ok(rightSide.rightOfChart, "both are right of the chart");
  assert.equal(rightSide.symbol, "BTC/USDT");
  assert.equal(rightSide.heading, "Breakout Logic");
  assert.deepEqual(rightSide.steps.map((step) => step.number), ["1", "2", "3"]);
  assert.deepEqual(rightSide.steps.map((step) => step.title), ["Breakout", "Retest", "Continuation"]);
  assert.equal(rightSide.steps[0].status, "Confirmed", "the stored breakout is confirmed");
  assert.equal(rightSide.steps[1].status, retestStates.includes("held") ? "Held" : "Observed", "the retest status is the backend's own state");
  assert.equal(rightSide.steps[2].status, "In Progress", "continuation is in progress, not complete");
  assert.equal(rightSide.steps[2].tone, "amber");
  assert.match(rightSide.steps[0].detail, /broke the .* band/);
  assert.match(rightSide.steps[1].detail, /held the band|No retest of this breakout is stored/);
  assert.match(rightSide.steps[2].detail, /The backend has stored no continuation/);
  assert.match(rightSide.note, /Continuation stays In Progress until the backend records one/);
  assert.ok(rightSide.band.includes("Reference band"), "the broken band is stated");
  report.checks.push({ name: "right-hand column", ...rightSide });

  // --- 4. Frame the three steps, then read the callouts.
  await clickControl('[data-frame-stages="true"]');
  await new Promise((resolveWait) => setTimeout(resolveWait, 400));
  const framed = await logicalRange();
  assert.ok(framed, "the framed range is readable");
  report.checks.push({ name: "framed window", range: framed });

  const callouts = await calloutState();
  assert.equal(callouts.badges.length, 3, "exactly three numbered callouts are drawn");
  assert.deepEqual(callouts.badges.map((badge) => badge.number), ["1", "2", "3"]);
  assert.deepEqual(callouts.badges.map((badge) => badge.title), ["Breakout", "Retest", "Continuation"]);
  assert.equal(callouts.lines, 3, "each callout has a leader line");
  assert.equal(callouts.arrows.length, 3, "each callout has an arrow");
  assert.equal(callouts.offView, null, "no callout is left outside the framed window");

  // Each arrow points at the candle its step belongs to.
  const breakoutX = await xOf(breakoutSeconds);
  const retestX = await xOf(retestSeconds);
  assert.ok(breakoutX !== null && retestX !== null, "both event candles are on screen after framing");
  const near = (left, right) => Math.abs(left - right) <= 2;
  assert.ok(near(callouts.arrows[0].x, breakoutX), `callout 1 points at the breakout candle (${callouts.arrows[0].x} vs ${breakoutX})`);
  assert.ok(near(callouts.arrows[1].x, retestX), `callout 2 points at the retest candle (${callouts.arrows[1].x} vs ${retestX})`);
  assert.ok(callouts.arrows[2].x > callouts.arrows[1].x, "callout 3 points at a candle after the retest");
  assert.ok(callouts.arrows[0].y > 0 && callouts.arrows[0].y < HEIGHT, "the arrow is inside the chart pane");
  // The callouts are stacked so they never overlap each other.
  const tops = callouts.badges.map((badge) => badge.top);
  assert.deepEqual(tops, [...tops].sort((left, right) => left - right), "the three callouts are stacked top-down, not overlapping");
  report.checks.push({ name: "callouts", ...callouts, breakoutX, retestX });

  // --- 5. The shaded reference band across the chart.
  const bands = await page.evaluate(() => [...document.querySelectorAll(".zone-band")].map((node) => {
    const box = node.getBoundingClientRect();
    return { top: box.top, height: box.height, width: box.width, label: null };
  }));
  const labels = await page.evaluate(() => [...document.querySelectorAll(".zone-band-label")].map((node) => node.textContent));
  assert.ok(labels.includes("RESISTANCE ZONE"), `the shaded band is labelled (labels: ${JSON.stringify(labels)})`);
  const zone = bands[0];
  assert.ok(zone && zone.width > 400, "the band spans the chart horizontally");
  assert.ok(zone.height >= 12, "and is shaded with enough thickness to read as a zone");
  report.checks.push({ name: "shaded band", labels, band: zone });

  // --- 6. Volume bars along the bottom of the pane.
  const volume = await page.evaluate(() => {
    const canvases = [...document.querySelectorAll(".terminal-chart-card canvas")];
    const pane = canvases.sort((left, right) => right.width * right.height - left.width * left.height)[0];
    if (!pane) return null;
    const bottom = Math.round(pane.height * 0.82);
    const data = pane.getContext("2d").getImageData(0, bottom, pane.width, pane.height - bottom).data;
    let coloured = 0;
    for (let index = 0; index < data.length; index += 4) {
      if (data[index + 3] < 40) continue;
      const [red, green, blue] = [data[index], data[index + 1], data[index + 2]];
      // Volume bars are the app's green / red at 28% over the dark pane.
      if ((green > red + 8 && green > blue + 8) || (red > green + 8 && red > blue + 8)) coloured += 1;
    }
    return { height: pane.height, bottom, coloured };
  });
  assert.ok(volume && volume.coloured > 500, `volume bars are drawn along the bottom (coloured pixels: ${volume?.coloured})`);
  report.checks.push({ name: "volume bars", ...volume });

  // --- 7. Screenshots of the finished layout.
  const stage = await page.$(".terminal-chart-card");
  await page.screenshot({ path: resolve(out, "breakouts-1440-full.png"), fullPage: true });
  await stage.screenshot({ path: resolve(out, "breakouts-1440-stage.png") });
  await page.screenshot({ path: resolve(out, "breakouts-1440-framed.png") });

  // --- 8. Leaving and returning leaves the view where the operator put it.
  const beforeReturn = await logicalRange();
  await clickControl('[data-function="trade_plan"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Trade Plan"));
  const tradePlan = await logicalRange();
  assert.deepEqual(tradePlan, beforeReturn, "switching away must not move the view");
  await clickControl('[data-function="breakouts"]');
  await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
  const backAgain = await logicalRange();
  assert.deepEqual(backAgain, beforeReturn, "switching back must not move the view");
  assert.equal((await calloutState()).badges.length, 3, "the three callouts are drawn again");
  await page.screenshot({ path: resolve(out, "breakouts-1440-after-return.png") });
  report.checks.push({ name: "zoom preserved switching away and back", beforeReturn, tradePlan, backAgain });

  report.pageErrors = pageErrors;
  report.consoleErrors = consoleErrors;
  assert.deepEqual(pageErrors, [], "no uncaught page errors");
} finally {
  await browser.close();
  writeFileSync(resolve(out, "breakouts-report.json"), `${JSON.stringify(report, null, 2)}\n`);
}

console.log(JSON.stringify(report, null, 2));
