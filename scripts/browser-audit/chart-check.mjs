#!/usr/bin/env node
/** Read-only browser regressions. Run against an OFFLINE synthetic/test dashboard.
 * node scripts/browser-audit/chart-check.mjs http://127.0.0.1:8040/ .cache/chart-review
 * No decision/ledger requests are submitted. Browser preferences are disposable.
 */
import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import puppeteer from "puppeteer-core";

const url = process.argv[2];
const out = process.argv[3];
if (!url || !out) throw new Error("Usage: chart-check.mjs URL OUTPUT_DIRECTORY (offline test server)");
await mkdir(out, { recursive: true });
const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({ headless: true,
  env: { ...process.env, LANG: "en_GB.UTF-8", LC_ALL: "en_GB.UTF-8" },
  executablePath: process.env.CHROME_PATH || await chromium.executablePath(),
  args: chromium?.args || ["--no-sandbox"],
  defaultViewport: { width: 1440, height: 1000 },
});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let blockedTimeframe = null;
let blockedRequest = null;
let onBlocked = null;
const vendor = await readFile(new URL("../../src/trading_assistant/web/static/vendor/lightweight-charts.standalone.production.js", import.meta.url), "utf8");
// Instrument the REAL chart library in this isolated browser; no application debug API.
await page.setRequestInterception(true);
page.on("request", (request) => {
  if (request.url().endsWith("lightweight-charts.standalone.production.js")) {
    request.respond({ status: 200, contentType: "application/javascript", body: vendor + `
      window.__reviewCharts = [];
      const realLibrary = window.LightweightCharts;
      window.LightweightCharts = { ...realLibrary, createChart(...args) {
        const chart = realLibrary.createChart(...args);
        const add = chart.addCandlestickSeries.bind(chart);
        const entry = { chart, series: [] };
        chart.addCandlestickSeries = (...options) => {
          const series = add(...options); entry.series.push(series); return series;
        };
        window.__reviewCharts.push(entry);
        return chart;
      } };
    ` });
  } else if (blockedTimeframe && new URL(request.url()).pathname === "/api/market/candles" &&
      new URL(request.url()).searchParams.get("timeframe") === blockedTimeframe) {
    blockedRequest = request;
    onBlocked?.();
  } else request.continue();
});
const frame = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
const chartMetrics = () => page.evaluate(() => {
  const { chart, series } = window.__reviewCharts[0];
  const data = series[0].data();
  const last = data.at(-1);
  return { low: series[0].priceToCoordinate(last.low), high: series[0].priceToCoordinate(last.high),
    range: chart.timeScale().getVisibleLogicalRange(), data };
});
try {
  await page.goto(url, { waitUntil: "networkidle0", timeout: 120000 });
  await page.waitForSelector(".terminal-chart-card canvas", { timeout: 120000 });
  await frame();
  const before = await chartMetrics();
  for (const layer of ["patterns", "structure", "breakouts", "candleSignals", "levels", "htfLevels", "plan"]) {
    await page.click(`[data-layer="${layer}"]`);
    await frame();
    const after = await chartMetrics();
    assert.deepEqual(after.data, before.data, `${layer} changed OHLC`);
    assert.deepEqual(after.range, before.range, `${layer} changed time range`);
    assert.ok(Math.abs(after.low - before.low) < 0.1 && Math.abs(after.high - before.high) < 0.1, `${layer} rescaled price`);
    await page.click(`[data-layer="${layer}"]`);
    await frame();
  }

  const component = await page.evaluate(async () => {
    const api = await import("/static/js/chart.js");
    const host = document.createElement("div");
    host.style.cssText = "width:800px;height:400px;position:fixed;top:0;left:0";
    document.body.append(host);
    const handle = api.createPriceChart(host);
    const rows = Array.from({ length: 150 }, (_, i) => [1700000000000 + i * 3600000, 100, 102, 99, 101, 10]);
    api.setCandles(handle, rows);
    const settle = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    await settle();
    const before = handle.series.priceToCoordinate(100);
    const range = handle.chart.timeScale().getVisibleLogicalRange();
    api.setEvidence(handle, { patternLines: [{ points: [
      { time: rows[40][0] / 1000, value: 1 }, { time: rows[120][0] / 1000, value: 1000000 },
    ] }], markers: Array.from({ length: 40 }, () => ({ time: rows[120][0] / 1000,
      position: "aboveBar", shape: "square", color: "red", text: "pattern" })) });
    await settle();
    const after = handle.series.priceToCoordinate(100);
    const afterRange = handle.chart.timeScale().getVisibleLogicalRange();
    api.setZoneBands(handle, [{ id: "test", band_low: 100, band_high: 101, position: "below_price", source_timeframe: "1h" }]);
    await settle();
    const layer = host.querySelector(".zone-band-layer").getBoundingClientRect();
    const pane = { width: handle.chart.timeScale().width(), height: host.clientHeight - handle.chart.timeScale().height() };
    const firstLabel = host.querySelector(".zone-band-label");
    api.renderZoneBands(handle);
    const stableLabel = firstLabel === host.querySelector(".zone-band-label");
    api.destroyPriceChart(handle);
    host.remove();
    return { before, after, range, afterRange, layer: { width: layer.width, height: layer.height }, pane, stableLabel };
  });
  assert.ok(Math.abs(component.before - component.after) < 0.1, "extreme guides/stacked markers rescaled candles");
  assert.deepEqual(component.range, component.afterRange);
  assert.deepEqual(component.layer, component.pane, "zones cover price/time axes");
  assert.equal(component.stableLabel, true, "unchanged redraw destroys focused zone label");

  // Every supported timeframe, including switch-back, gets recent bars rather than stale indices.
  for (const timeframe of ["5m", "15m", "4h", "1h"]) {
    await page.click(`[data-timeframe="${timeframe}"]`);
    await page.waitForFunction((tf) => document.querySelector(".chart-view-note")?.textContent.includes("stored") &&
      !document.querySelector(".chart-view-note")?.textContent.includes("Loading") &&
      document.querySelector(`.tf-button[data-timeframe="${tf}"]`)?.getAttribute("aria-pressed") === "true",
      {}, timeframe);
    await frame();
    const current = await chartMetrics();
    assert.ok(current.data.length > 0, `offline fixture needs ${timeframe} candles`);
    assert.ok(current.range.to >= current.data.length - 1, `${timeframe} stranded in history`);
  }

  // Keyboard-accessible item list selects one pattern; it never changes scale or candle data.
  await page.click(".chart-items summary");
  const pattern = await page.$(".chart-item-button:has(.chart-item-name)");
  assert.ok(pattern);
  const beforeSelection = await chartMetrics();
  const selected = await page.evaluate(() => {
    const button = [...document.querySelectorAll(".chart-item-button")].find((node) => /Double top|Double bottom|head and shoulders/i.test(node.textContent));
    button?.focus(); return Boolean(button);
  });
  assert.ok(selected, "offline fixture must contain detected patterns");
  await page.keyboard.press("Enter");
  await frame();
  assert.deepEqual(await chartMetrics(), beforeSelection, "inspecting a pattern changed the market view");
  assert.equal(await page.evaluate(() => document.activeElement.classList.contains("chart-item-button")), true, "keyboard selection lost focus");
  assert.match(await page.$eval(".chart-selection-note", (node) => node.textContent), /exact recorded neckline/);
  await page.click(".chart-selection button");
  await page.click(".chart-items summary");

  // Price-axis drag and explicit reset; band coordinates track the final chart transform.
  await page.click('[data-layer="levels"]');
  await page.$eval(".terminal-chart-wrap", (node) => node.scrollIntoView({ block: "center" }));
  const bounds = await page.$eval(".terminal-chart-wrap", (node) => {
    const r = node.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
  await page.mouse.move(bounds.x + bounds.w - 25, bounds.y + 160);
  await page.mouse.down(); await page.mouse.move(bounds.x + bounds.w - 25, bounds.y + 220, { steps: 8 }); await page.mouse.up();
  await frame();
  assert.equal(await page.evaluate(() => window.__reviewCharts[0].chart.priceScale("right").options().autoScale), false,
    "price-axis drag must actually exercise manual scaling");
  const bands = await page.evaluate(() => {
    const series = window.__reviewCharts[0].series[0];
    const expected = [...document.querySelectorAll(".zone-band-label")].map((label) => {
      const match = label.getAttribute("aria-label").match(/zone ([\d.]+) to ([\d.]+)/);
      return match ? Math.max(0, Math.min(series.priceToCoordinate(Number(match[1])), series.priceToCoordinate(Number(match[2])))) : null;
    }).sort((a, b) => a - b);
    const actual = [...document.querySelectorAll(".zone-band:not(.zone-band--setup)")]
      .map((node) => parseFloat(node.style.top)).sort((a, b) => a - b);
    return { expected, actual };
  });
  assert.ok(bands.expected.length > 0, "fixture must include visible zones");
  assert.equal(bands.actual.length, bands.expected.length);
  assert.ok(bands.actual.every((y, i) => Math.abs(y - bands.expected[i]) < 0.1), "bands did not follow price-axis drag");
  await page.click(".chart-reset"); await frame();
  assert.equal(await page.evaluate(() => window.__reviewCharts[0].chart.priceScale("right").options().autoScale), true);

  const responsive = [];
  for (const [width, height, name] of [[1440, 1000, "desktop"], [834, 1112, "tablet"], [390, 844, "phone"]]) {
    await page.setViewport({ width, height }); await frame();
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      chartWidth: document.querySelector(".terminal-chart-wrap").clientWidth,
    }));
    assert.equal(layout.overflow, 0, `${name} horizontal overflow`);
    assert.ok(layout.chartWidth > 200, `${name} collapsed chart`);
    await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
    const expandedOverflow = await page.evaluate(() => {
      const disclosures = [...document.querySelectorAll(".terminal-dashboard details")];
      const opened = disclosures.map((node) => node.open);
      disclosures.forEach((node) => { node.open = true; });
      const overflow = document.documentElement.scrollWidth - document.documentElement.clientWidth;
      disclosures.forEach((node, i) => { node.open = opened[i]; });
      return overflow;
    });
    assert.equal(expandedOverflow, 0, `${name} expanded diagnostics overflow`);
    responsive.push({ name, ...layout, expandedOverflow });
  }
  // A deliberately stalled/failed timeframe request must never leave old bands/candles visible.
  const blockedArrival = new Promise((resolve) => { onBlocked = resolve; });
  blockedTimeframe = "15m";
  await page.click('[data-timeframe="15m"]');
  await page.waitForFunction(() => document.querySelector(".chart-view-note").textContent.includes("Loading"));
  assert.equal(await page.evaluate(() => window.__reviewCharts[0].series[0].data().length), 0);
  assert.equal(await page.$$eval(".zone-band", (nodes) => nodes.length), 0);
  // Wait on the intercepted request itself, not an arbitrary sleep.
  await blockedArrival;
  assert.ok(blockedRequest);
  await blockedRequest.respond({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { code: "test_unavailable", message: "offline regression" } }) });
  await page.waitForFunction(() => document.querySelector(".chart-view-note").textContent.includes("unavailable"));
  assert.equal(await page.evaluate(() => window.__reviewCharts[0].series[0].data().length), 0);
  assert.equal(await page.$$eval(".zone-band", (nodes) => nodes.length), 0);
  blockedTimeframe = null;
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ outcome: "passed", layers: "OHLC, time and price scale unchanged", extremePattern: component,
    timeframes: ["5m", "15m", "4h", "1h"], responsive, pageErrors: errors }, null, 2));
} finally { await browser.close(); }
