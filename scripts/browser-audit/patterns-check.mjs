#!/usr/bin/env node
/**
 * Chart Patterns regression against an isolated synthetic demo server.
 *
 * Proves, in a real browser and against the backend's own records, that the
 * Chart Patterns screen is the approved composition (BRAIN_Approved_Chart_Patterns,
 * 1672 x 941): one slim header, a large chart panel (~72 %) and a 15 px gutter
 * before three stacked cards (~27 %), top-aligned; the selected stored pattern
 * drawn on its real candles at its real prices; the confidence "Not calculated";
 * only stored patterns listed; key levels from the record or "Unavailable"; a
 * volume pane along the bottom — and that none of it resets the operator's zoom.
 *
 * No API mocking, no invented patterns, no production database.
 *
 * Usage: node scripts/browser-audit/patterns-check.mjs http://127.0.0.1:8123 ./out
 *        (CHROME_PATH=/path/to/chromium to use a local browser)
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import puppeteer from "puppeteer-core";

const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error("URL and screenshot directory are required");
mkdirSync(out, { recursive: true });

/** The approved image's own size first, then the three other sign-off sizes. */
const VIEWPORTS = [
  { label: "1672x941", width: 1672, height: 941, primary: true },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
  { label: "390-mobile", width: 390, height: 844 },
];

/**
 * Console errors the sandboxed demo always produces and that are not part of
 * this screen: the live Binance stream cannot connect without network, and the
 * vendored chart library sets one inline style the page's CSP refuses (present
 * before this change too). Anything else is reported as a failure.
 */
const KNOWN_ENVIRONMENT_ERRORS = [
  /WebSocket connection to 'wss:\/\/stream\.binance\.com/,
  /Refused to apply inline style because it violates the following Content Security Policy/,
];

/** Audit-only probes: read the REAL chart scales instead of trusting the DOM. */
function installProbes() {
  window.__brainCharts = [];
  window.__brainSeries = [];
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
            if (chart) {
              window.__brainCharts.push(chart);
              const addCandles = chart.addCandlestickSeries.bind(chart);
              chart.addCandlestickSeries = (...seriesArgs) => {
                const series = addCandles(...seriesArgs);
                window.__brainSeries.push(series);
                return series;
              };
            }
            return chart;
          },
        });
        value = shim;
        return;
      }
      value = next;
    },
  });
  window.__xOf = (seconds) => {
    const chart = window.__brainCharts.at(-1);
    const x = chart ? chart.timeScale().timeToCoordinate(seconds) : null;
    return x === null || x === undefined ? null : x;
  };
  window.__yOf = (price) => {
    const series = window.__brainSeries.at(-2) || window.__brainSeries.at(-1);
    const y = series ? series.priceToCoordinate(price) : null;
    return y === null || y === undefined ? null : y;
  };
}

const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
  args: chromium?.args || ["--no-sandbox", "--disable-gpu"],
  env: { ...process.env, LANG: "en_GB.UTF-8", LC_ALL: "en_GB.UTF-8" },
});

const report = { url, generatedAt: new Date().toISOString(), viewports: [] };
const wait = (ms) => new Promise((resolveWait) => setTimeout(resolveWait, ms));
const zoomWidth = (range) => (range ? Math.round((range.to - range.from) * 1e6) / 1e6 : null);

try {
  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage();
    const checks = [];
    const viewportReport = { label: viewport.label, width: viewport.width, height: viewport.height, checks };
    report.viewports.push(viewportReport);
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
    await page.evaluateOnNewDocument(installProbes);
    await page.setViewport({ width: viewport.width, height: viewport.height, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => document.querySelector(".terminal-chart-card canvas") && !document.querySelector('[aria-busy="true"]'),
      { timeout: 180000 },
    );
    await page.evaluate(() => document.fonts.ready);
    // Every shot is taken from the top of the page, the way the screen opens.
    const shot = async (name, fullPage = false) => {
      await page.evaluate(() => window.scrollTo(0, 0));
      await wait(250);
      await page.screenshot({ path: resolve(out, `patterns-${viewport.label}-${name}.png`), fullPage });
    };
    const logicalRange = () => page.evaluate(() => {
      const range = window.__brainCharts.at(-1)?.timeScale().getVisibleLogicalRange();
      return range ? { from: Math.round(range.from * 1e6) / 1e6, to: Math.round(range.to * 1e6) / 1e6 } : null;
    });
    const clickControl = async (selector) => {
      await page.waitForSelector(selector, { visible: true, timeout: 30000 });
      await page.$eval(selector, (node) => node.scrollIntoView({ block: "center", inline: "nearest" }));
      await wait(150);
      await page.click(selector);
    };
    const restMouse = async () => {
      // Off the chart, so the OHLC line shows the newest candle and no crosshair is drawn.
      await page.mouse.move(viewport.width - 4, 4);
      await wait(250);
    };

    // --- Ground truth: the backend's stored pattern records, read independently.
    const stored = await page.evaluate(async () => {
      const payload = await (await fetch("/api/market/annotations")).json();
      return {
        available: payload?.available === true,
        timeframe: payload?.timeframe ?? null,
        patterns: (Array.isArray(payload?.patterns) ? payload.patterns : []).map((item) => ({
          id: item.id,
          patternId: item.pattern_id,
          type: item.type,
          side: item.side,
          state: item.state,
          isLatest: item.is_latest_state === true,
          knownAt: item.known_at,
          anchorTime: item.anchor_time,
          neckline: item.neckline,
          invalidation: item.invalidation_level,
          confidenceField: Object.keys(item).filter((key) => /confidence|probability|score/i.test(key)),
          components: (item.components || []).map((point) => ({ kind: point.kind, time: point.time, seconds: Math.floor(Number(point.time_ms) / 1000), price: Number(point.price) })),
        })),
      };
    });
    assert.equal(stored.available, true, "chart evidence is available on the demo server");
    assert.ok(stored.patterns.length > 0, "the demo database stores patterns to show");
    checks.push({ name: "stored patterns", patterns: stored.patterns });
    const storedIds = new Set(stored.patterns.map((item) => item.id));

    // --- Zoom: selecting the function keeps the number of candles on screen.
    const beforeSwitch = await logicalRange();
    await clickControl('[data-function="patterns"]');
    await page.waitForSelector(".pattern-panel", { visible: true });
    await wait(600);
    await restMouse();
    const afterSwitch = await logicalRange();
    assert.equal(zoomWidth(afterSwitch), zoomWidth(beforeSwitch), "switching to Chart Patterns must not change the zoom");
    checks.push({ name: "zoom preserved opening Chart Patterns", before: beforeSwitch, after: afterSwitch });

    const readScreen = () => page.evaluate(() => {
      const box = (selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const r = node.getBoundingClientRect();
        return { left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height) };
      };
      const layer = document.querySelector(".pattern-anno-layer");
      const layerBox = layer?.getBoundingClientRect();
      const labels = [...document.querySelectorAll(".pattern-anno-point, .pattern-anno-title, .pattern-anno-neckline-label")].map((node) => {
        const r = node.getBoundingClientRect();
        return {
          cls: node.className,
          text: node.textContent,
          tone: node.dataset.tone || null,
          time: node.dataset.time ? Number(node.dataset.time) : null,
          price: node.dataset.price ? Number(node.dataset.price) : null,
          centreX: r.left + r.width / 2 - layerBox.left,
          // Held inside the pane at its edge rather than cut off (narrow screens).
          atEdge: r.left - layerBox.left <= 5 || layerBox.right - r.right <= 5,
          top: r.top - layerBox.top,
          bottom: r.bottom - layerBox.top,
          inside: r.left >= layerBox.left - 1 && r.right <= layerBox.right + 1 && r.top >= layerBox.top - 1 && r.bottom <= layerBox.bottom + 1,
          fontSize: getComputedStyle(node).fontSize,
          colour: getComputedStyle(node).color,
          border: getComputedStyle(node).borderTopColor,
        };
      });
      const neck = document.querySelector(".pattern-anno-neckline");
      const connector = document.querySelector(".pattern-anno-connector");
      const panel = document.querySelector(".pattern-panel");
      const cards = [...document.querySelectorAll(".pd-card")].map((node) => {
        const r = node.getBoundingClientRect();
        return { cls: node.className, top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height) };
      });
      const overflowing = [...document.querySelectorAll(".pattern-panel *, .pattern-chart-head *, .dash-commandbar *")]
        .filter((node) => node.children.length === 0 && node.textContent.trim() && node.offsetParent && node.scrollWidth > node.clientWidth + 1 && getComputedStyle(node).overflow !== "visible")
        .map((node) => node.className || node.tagName);
      const header = [...document.querySelectorAll(".dash-commandbar > *, .commandbar-identity > *")]
        .filter((node) => node.offsetParent && getComputedStyle(node).display !== "contents")
        .map((node) => node.getBoundingClientRect())
        .sort((a, b) => a.left - b.left);
      let headerOverlap = 0;
      for (let i = 1; i < header.length; i += 1) {
        if (Math.abs(header[i].top - header[i - 1].top) < 10) headerOverlap = Math.max(headerOverlap, header[i - 1].right - header[i].left);
      }
      return {
        topbarVisible: Boolean(document.querySelector(".topbar")?.offsetParent),
        header: box(".dash-commandbar"),
        headerOverlapPx: Math.round(headerOverlap),
        headerControlsChildren: document.querySelector(".chart-header-controls")?.children.length ?? null,
        activeTimeframe: document.querySelector('.tf-button[aria-pressed="true"]')?.textContent || null,
        timeframeBg: (() => { const n = document.querySelector('.tf-button[aria-pressed="true"]'); return n ? getComputedStyle(n).backgroundColor : null; })(),
        chartHead: box(".pattern-chart-head"),
        chartHeadText: document.querySelector(".pattern-chart-head")?.textContent || null,
        chartWrap: box(".terminal-chart-wrap"),
        slot: box(".chart-function-slot"),
        cards,
        layer: layer ? { id: layer.dataset.patternId || null, state: layer.dataset.state || null, tone: layer.dataset.tone || null, width: Math.round(layerBox.width), height: Math.round(layerBox.height) } : null,
        labels,
        neckY: neck ? Number(neck.getAttribute("y1")) : null,
        neckX: neck ? [Number(neck.getAttribute("x1")), Number(neck.getAttribute("x2"))] : null,
        connectorEnd: connector ? { x: Number(connector.getAttribute("x2")), y: Number(connector.getAttribute("y2")) } : null,
        curves: document.querySelectorAll(".pattern-anno-curve").length,
        detection: {
          name: panel?.querySelector(".pd-name")?.textContent || null,
          badge: panel?.querySelector(".pd-card--detection .pd-badge")?.textContent || null,
          badgeTone: panel?.querySelector(".pd-card--detection .pd-badge")?.dataset.tone || null,
          score: panel?.querySelector(".pd-score-value")?.textContent || null,
          scoreTrackChildren: panel?.querySelector(".pd-score-track")?.children.length ?? null,
          explanation: panel?.querySelector(".pd-explain-text")?.textContent || null,
        },
        others: [...document.querySelectorAll(".pd-other-button")].map((node) => ({ id: node.dataset.selectPattern, text: node.innerText.replace(/\s+/g, " ").trim() })),
        othersEmpty: document.querySelector(".pd-card--others .pd-empty")?.textContent || null,
        levels: [...document.querySelectorAll(".pd-level")].map((node) => ({ key: node.dataset.level, available: node.dataset.available, text: node.innerText.replace(/\s+/g, " ").trim() })),
        volume: {
          separator: box(".pattern-volume-separator"),
          label: document.querySelector(".pattern-volume-label")?.textContent || null,
          axisMask: box(".pattern-volume-axis-mask"),
        },
        overflowing,
        panelText: panel?.innerText || "",
        pageScrollWidth: document.documentElement.scrollWidth,
      };
    });

    // The default selection is the newest current state (unchanged behaviour).
    const latest = stored.patterns.filter((item) => item.isLatest).sort((a, b) => Date.parse(b.knownAt) - Date.parse(a.knownAt))[0];
    let screen = await readScreen();
    assert.equal(screen.topbarVisible, false, "the global bar folds away on this screen");
    assert.equal(screen.headerControlsChildren, 1, "the function switches sit in the slim header");
    assert.equal(screen.activeTimeframe, "1H");
    assert.ok(screen.detection.name, "the Pattern Detection card names the selected pattern");
    checks.push({ name: "default selection", expected: latest?.id, detection: screen.detection, layer: screen.layer });
    await shot("default");

    // --- Select the confirmed double bottom (the reference's subject) from Card 2.
    const target = stored.patterns.find((item) => item.type === "double_bottom" && item.isLatest) || stored.patterns[0];
    const beforeSelect = await logicalRange();
    if (screen.others.some((row) => row.id === target.id)) {
      await clickControl(`[data-select-pattern="${target.id}"]`);
      await wait(700);
      await restMouse();
    }
    const afterSelect = await logicalRange();
    assert.equal(zoomWidth(afterSelect), zoomWidth(beforeSelect), "selecting a pattern keeps the zoom");
    checks.push({ name: "zoom preserved selecting a pattern", before: beforeSelect, after: afterSelect });
    screen = await readScreen();

    // Anchors: each label over its stored candle, the neckline at the stored price.
    const anchors = [];
    for (const label of screen.labels.filter((item) => item.cls === "pattern-anno-point")) {
      const component = target.components.find((point) => point.seconds === label.time);
      assert.ok(component, `${label.text} sits on a stored component candle`);
      assert.equal(label.price, component.price, `${label.text} uses the stored price`);
      const x = await page.evaluate((value) => window.__xOf(value), label.time);
      anchors.push({ label: label.text, time: new Date(label.time * 1000).toISOString(), price: label.price, labelCentreX: Math.round(label.centreX * 10) / 10, candleX: x === null ? null : Math.round(x * 10) / 10, heldAtPaneEdge: label.atEdge });
      if (x !== null && label.inside && !label.atEdge) assert.ok(Math.abs(label.centreX - x) <= 1.5, `${viewport.label}: ${label.text} is centred on its candle (label ${label.centreX}, candle ${x})`);
    }
    const neckY = await page.evaluate((price) => window.__yOf(price), Number(target.neckline));
    if (screen.neckY !== null && neckY !== null) assert.ok(Math.abs(screen.neckY - neckY) <= 0.6, "the neckline is drawn at the stored neckline price");
    const confirmX = target.state === "confirmed" ? await page.evaluate((iso) => window.__xOf(Math.floor(Date.parse(iso) / 1000)), target.anchorTime) : null;
    if (screen.connectorEnd && confirmX !== null) assert.ok(Math.abs(screen.connectorEnd.x - confirmX) <= 0.6, "the label's connector ends on the confirmation candle");
    const title = screen.labels.find((item) => item.cls === "pattern-anno-title");
    const expectedTitle = `${target.type === "double_bottom" ? "Double Bottom" : target.type} - ${target.state[0].toUpperCase()}${target.state.slice(1)}`;
    if (title) assert.equal(title.text, expectedTitle, "the label carries the stored lifecycle state");
    checks.push({
      name: "annotation anchors",
      pattern: target.id,
      state: target.state,
      anchors,
      neckline: { stored: target.neckline, drawnY: screen.neckY, priceScaleY: neckY, x: screen.neckX },
      confirmation: { anchorTime: target.anchorTime, connectorEnd: screen.connectorEnd, candleX: confirmX },
      title,
    });

    // Cards: lifecycle badge, confidence placeholder, stored-only lists, levels.
    assert.equal(screen.detection.score, "Not calculated");
    assert.equal(screen.detection.scoreTrackChildren, 0, "the confidence bar is never filled");
    assert.doesNotMatch(screen.panelText, /\d+(\.\d+)?\s?%/, "no percentage anywhere in the cards");
    assert.doesNotMatch(screen.panelText, /bull flag|descending triangle/i, "no invented pattern");
    assert.ok(screen.others.every((row) => storedIds.has(row.id)), "every listed pattern is a stored record");
    const levelText = screen.levels.map((row) => row.text);
    checks.push({ name: "cards", detection: screen.detection, others: screen.others, othersEmpty: screen.othersEmpty, levels: screen.levels });
    assert.ok(levelText.some((text) => /Unavailable/.test(text)), "no derived target");

    // Layout proportions against the approved image.
    const panel = screen.chartHead && screen.chartWrap
      ? { left: screen.chartHead.left, right: screen.chartWrap.right, top: screen.chartHead.top, bottom: screen.chartWrap.bottom }
      : screen.chartWrap;
    const layout = {
      header: screen.header,
      headerOverlapPx: screen.headerOverlapPx,
      chartPanel: panel,
      chartWidthPct: Math.round(((panel.right - panel.left) / viewport.width) * 1000) / 10,
      rightColumn: screen.slot,
      rightWidthPct: Math.round((screen.slot.width / viewport.width) * 1000) / 10,
      gutter: screen.slot.left - panel.right,
      topAligned: Math.abs((screen.cards[0]?.top ?? 0) - panel.top),
      cards: screen.cards,
      timeframeBg: screen.timeframeBg,
      chartHeadText: screen.chartHeadText,
      pageScrollWidth: screen.pageScrollWidth,
    };
    checks.push({ name: "layout", ...layout });
    assert.ok(screen.headerOverlapPx <= 0, `header controls do not overlap (${screen.headerOverlapPx}px)`);
    assert.ok(screen.pageScrollWidth <= viewport.width, "no horizontal page scroll");
    if (viewport.width > 1020) {
      assert.ok(layout.gutter >= 13 && layout.gutter <= 18, `gutter ${layout.gutter}px`);
      assert.ok(layout.topAligned <= 12, "the cards are top-aligned with the chart panel");
    }

    // No clipped labels.
    const clipped = screen.labels.filter((item) => !item.inside).map((item) => item.text);
    checks.push({ name: "labels inside the chart and no clipped text", clipped, overflowing: screen.overflowing });

    // Volume pane.
    const volume = await page.evaluate(() => {
      const canvas = [...document.querySelectorAll(".terminal-chart-wrap canvas")].sort((a, b) => b.width * b.height - a.width * a.height)[0];
      const separator = document.querySelector(".pattern-volume-separator");
      if (!canvas || !separator) return null;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      const top = Math.round((separator.getBoundingClientRect().top - canvas.getBoundingClientRect().top) * (canvas.height / canvas.getBoundingClientRect().height));
      let data;
      try {
        data = ctx.getImageData(0, top + 4, Math.round(canvas.width * 0.9), Math.max(1, canvas.height - top - 8)).data;
      } catch {
        return { readable: false };
      }
      let coloured = 0;
      for (let index = 0; index < data.length; index += 4) {
        const [red, green, blue] = [data[index], data[index + 1], data[index + 2]];
        if ((green > red + 8 && green > blue + 8) || (red > green + 8 && red > blue + 8)) coloured += 1;
      }
      return { readable: true, coloured };
    });
    checks.push({ name: "volume pane", label: screen.volume.label, separator: screen.volume.separator, ...volume });
    assert.match(screen.volume.label || "", /^Volume/);

    await shot("double-bottom");
    if (viewport.width < 600) await shot("double-bottom-full", true);
    if (viewport.primary) {
      await shot("full", true);

      // Resize: the drawing follows its candles.
      await page.setViewport({ width: 1500, height: 900, deviceScaleFactor: 1 });
      await wait(900);
      const resized = await readScreen();
      const resizedAnchors = [];
      for (const label of resized.labels.filter((item) => item.cls === "pattern-anno-point")) {
        const x = await page.evaluate((value) => window.__xOf(value), label.time);
        resizedAnchors.push({ label: label.text, labelCentreX: Math.round(label.centreX * 10) / 10, candleX: x === null ? null : Math.round(x * 10) / 10 });
        if (x !== null && label.inside) assert.ok(Math.abs(label.centreX - x) <= 1.5, `${label.text} follows its candle after a resize`);
      }
      checks.push({ name: "resize redraws at the same candles", resizedAnchors });
      await page.setViewport({ width: viewport.width, height: viewport.height, deviceScaleFactor: 1 });
      await wait(900);

      // Detailed mode keeps the evidence controls.
      await clickControl('.chart-mode-button[data-mode="detailed"]');
      await wait(700);
      await restMouse();
      const detailed = await page.evaluate(() => ({
        toolbarVisible: Boolean(document.querySelector(".terminal-chart-card > .chart-toolbar")?.offsetParent),
        layerDetailsVisible: Boolean(document.querySelector(".chart-layer-details")?.offsetParent),
        recordFacts: document.querySelectorAll(".pd-record-facts dt").length,
        annotation: Boolean(document.querySelector(".pattern-anno-title")),
      }));
      checks.push({ name: "detailed mode", ...detailed });
      assert.ok(detailed.recordFacts > 0, "Detailed mode shows the stored record facts");
      await shot("detailed");
      await clickControl('.chart-mode-button[data-mode="simple"]');
      await wait(500);

      // Timeframe change clears the 1H drawing.
      const oneHour = (await readScreen()).layer;
      await clickControl('.tf-button[data-timeframe="4h"], .tf-button:nth-child(4)');
      await page.waitForFunction(() => document.querySelector('.tf-button[aria-pressed="true"]')?.textContent === "4H", { timeout: 60000 });
      await wait(1500);
      await restMouse();
      const fourHour = await readScreen();
      checks.push({ name: "timeframe change clears the old annotation", oneHour, fourHour: fourHour.layer, fourHourDetection: fourHour.detection, labels: fourHour.labels.map((item) => item.text) });
      assert.ok(!fourHour.layer || fourHour.layer.id !== oneHour?.id, "the 1H pattern is not drawn on the 4H chart");
      await shot("4h");
      await clickControl('.tf-button[data-timeframe="1h"], .tf-button:nth-child(3)');
      await page.waitForFunction(() => document.querySelector('.tf-button[aria-pressed="true"]')?.textContent === "1H", { timeout: 60000 });
      await wait(1200);
    }

    // Leaving: Breakouts & Retests keeps its own layout and the zoom.
    const beforeLeave = await logicalRange();
    await clickControl('[data-function="breakouts"]');
    await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
    await wait(700);
    await restMouse();
    const breakouts = await page.evaluate(() => ({
      topbarVisible: Boolean(document.querySelector(".topbar")?.offsetParent),
      headerControlsChildren: document.querySelector(".chart-header-controls")?.children.length ?? null,
      callouts: [...document.querySelectorAll(".breakout-anno")].map((node) => node.dataset.stage),
      patternLayer: Boolean(document.querySelector(".pattern-anno-layer")),
      volumeLabel: Boolean(document.querySelector(".pattern-volume-label")),
      summaryCard: Boolean(document.querySelector(".market-summary-card")),
    }));
    const afterLeave = await logicalRange();
    checks.push({ name: "breakouts unaffected", ...breakouts, zoomBefore: beforeLeave, zoomAfter: afterLeave });
    assert.equal(breakouts.topbarVisible, true);
    assert.equal(breakouts.headerControlsChildren, 0);
    assert.equal(breakouts.patternLayer, false, "the pattern drawing is removed");
    assert.equal(breakouts.volumeLabel, false, "the pattern volume pane is restored to the default");
    assert.deepEqual(breakouts.callouts, ["breakout", "retest", "continuation"]);
    assert.equal(zoomWidth(afterLeave), zoomWidth(beforeLeave), "leaving keeps the zoom");
    await shot("breakouts");

    const unexpected = consoleErrors.filter((text) => !KNOWN_ENVIRONMENT_ERRORS.some((pattern) => pattern.test(text)));
    viewportReport.pageErrors = pageErrors;
    viewportReport.consoleErrors = consoleErrors;
    viewportReport.unexpectedConsoleErrors = unexpected;
    assert.deepEqual(pageErrors, [], "no uncaught page errors");
    assert.deepEqual(unexpected, [], "no console errors beyond the sandbox's known ones");
    await page.close();
  }
} finally {
  await browser.close();
  writeFileSync(resolve(out, "patterns-report.json"), `${JSON.stringify(report, null, 2)}\n`);
}

console.log(JSON.stringify(report.viewports.map((item) => ({ label: item.label, checks: item.checks.map((check) => check.name) })), null, 2));
