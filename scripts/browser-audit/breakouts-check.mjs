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

/**
 * The four sizes the layout is signed off at. 1536x864 is the primary one; the
 * desktop sizes check the two-column proportions and the phone checks that the
 * composition collapses to a single column without clipping anything.
 */
const VIEWPORTS = [
  { label: "1536x864", width: 1536, height: 864 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
  { label: "390-mobile", width: 390, height: 844 },
];

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

const report = { url, generatedAt: new Date().toISOString(), viewports: [] };

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
    /**
     * Scroll the way an operator reads the screen: the chart card's top just
     * below the sticky bars, so the whole composition is on screen and nothing
     * is tucked up under the header.
     */
    const scrollToChart = async () => {
      await page.evaluate(() => {
        const bottom = Math.max(0, ...[".topbar", ".dash-commandbar"].map((selector) => {
          const node = document.querySelector(selector);
          return node ? node.getBoundingClientRect().bottom : 0;
        }));
        const card = document.querySelector(".terminal-chart-card");
        window.scrollTo(0, Math.max(0, card.getBoundingClientRect().top + window.scrollY - bottom - 8));
      });
      await new Promise((resolveWait) => setTimeout(resolveWait, 350));
    };
    const calloutState = () => page.evaluate(() => {
      const nodes = [...document.querySelectorAll(".breakout-anno")];
      const arrows = [...document.querySelectorAll(".breakout-anno-arrow")];
      return {
        badges: nodes.map((node) => {
          const box = node.getBoundingClientRect();
          const pane = document.querySelector(".terminal-chart-wrap").getBoundingClientRect();
          const numberBox = node.querySelector(".breakout-anno-num")?.getBoundingClientRect();
          // elementFromPoint only answers for points inside the viewport, so a
          // callout scrolled out of view reports "unknown" rather than "covered".
          const inView = box.bottom > 0 && box.top < window.innerHeight && box.right > 0 && box.left < window.innerWidth;
          const centre = inView
            ? document.elementFromPoint(Math.round(box.x + box.width / 2), Math.round(box.y + box.height / 2))
            : null;
          return {
            stage: node.dataset.stage,
            recorded: node.dataset.recorded,
            place: node.dataset.place,
            number: node.querySelector(".breakout-anno-num")?.textContent || null,
            title: node.querySelector(".breakout-anno-title")?.textContent || null,
            status: node.querySelector(".breakout-anno-status")?.textContent || null,
            left: Number.parseFloat(node.style.left),
            top: Number.parseFloat(node.style.top),
            width: Math.round(box.width),
            height: Math.round(box.height),
            fontSize: getComputedStyle(node).fontSize,
            numberSize: numberBox ? Math.round(numberBox.width) : 0,
            paneWidth: Math.round(pane.width),
            insidePane: box.left >= pane.left - 1 && box.right <= pane.right + 1 && box.top >= pane.top - 1 && box.bottom <= pane.bottom + 1,
            onTop: inView ? Boolean(centre && (centre === node || node.contains(centre))) : null,
            viewportTop: Math.round(box.top),
          };
        }),
        pendingLine: document.querySelectorAll('.breakout-anno-line[data-pending="true"]').length,
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
    const storedEvents = {
      count: stored.events.length,
      counts: stored.counts,
      breakoutCandle: new Date(breakoutSeconds * 1000).toISOString(),
      retestCandle: new Date(retestSeconds * 1000).toISOString(),
      retestStates,
      band,
    };
    checks.push({ name: "stored events", ...storedEvents });

    // --- 1. Selecting the function must not change the ZOOM. The number of
    // candles on screen is kept exactly. The window is only moved when not one
    // stored step is on screen — otherwise this screen opens on an empty
    // stretch of chart with all three callouts off the side.
    const wide = viewport.width >= 1024;
    const zoomWidth = (range) => (range ? Math.round((range.to - range.from) * 1e6) / 1e6 : null);
    const beforeSwitch = await logicalRange();
    await clickControl('[data-function="breakouts"]');
    await page.waitForFunction(() => document.querySelector(".chart-function-status")?.innerText.startsWith("Breakouts / Retests"));
    await new Promise((resolveWait) => setTimeout(resolveWait, 400));
    const afterSwitch = await logicalRange();
    assert.ok(beforeSwitch && afterSwitch, "the chart's visible range is readable for the check");
    assert.equal(zoomWidth(afterSwitch), zoomWidth(beforeSwitch), "switching chart function must not change the zoom");
    // Whichever way the window moved, every stored step is on it.
    const drawn = await page.evaluate(() => [...document.querySelectorAll(".breakout-anno")].map((node) => node.dataset.stage));
    assert.deepEqual(drawn, ["breakout", "retest", "continuation"], "all three steps are on the chart after the switch");
    checks.push({ name: "zoom preserved across a function switch", before: beforeSwitch, after: afterSwitch, zoomWidth: zoomWidth(beforeSwitch) });

    // --- 2. The status line names the three steps.
    const status = await statusText();
    assert.match(status, /1 Breakout:/, "step 1 is named");
    assert.match(status, /2 Retest:/, "step 2 is named");
    assert.match(status, /3 Continuation: In Progress/, "continuation is not claimed complete");
    assert.doesNotMatch(status, /3 Continuation: Confirmed/);
    checks.push({ name: "status line", text: status });

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
          summary: node.querySelector(".cfp-stage-summary")?.textContent || null,
          recorded: node.dataset.recorded || null,
          statusRight: Math.round(node.querySelector(".cfp-stage-status").getBoundingClientRect().right),
        })),
        metaCount: panel.querySelectorAll(".cfp-stage-meta").length,
        keyIdea: panel.querySelector(".cfp-key-idea-text")?.textContent || null,
        note: panel.querySelector(".cfp-note")?.textContent || null,
        // Smallest font size anywhere in the column: Simple mode carries no
        // microscopic text.
        smallestFont: Math.min(...[...panel.querySelectorAll("*"), ...summary.querySelectorAll("*")]
          .filter((node) => [...node.childNodes].some((child) => child.nodeType === 3 && child.textContent.trim()))
          .map((node) => Number.parseFloat(getComputedStyle(node).fontSize)), Number.POSITIVE_INFINITY),
        panelContentRight: Math.round(panel.getBoundingClientRect().right - Number.parseFloat(getComputedStyle(panel).paddingRight)),
        tops: {
          chart: Math.round(document.querySelector(".terminal-chart-wrap").getBoundingClientRect().top),
          column: Math.round(slot.getBoundingClientRect().top),
        },
        widths: {
          chart: Math.round(document.querySelector(".terminal-chart-wrap").getBoundingClientRect().width),
          column: Math.round(slot.getBoundingClientRect().width),
          row: Math.round(document.querySelector(".chart-stage").getBoundingClientRect().width),
        },
        // The chart pane itself, and how much of the card it takes: the candles
        // are the subject of the screen, so the pane must dominate the card.
        canvas: (() => {
          const pane = document.querySelector(".terminal-chart-wrap canvas");
          const cardBox = document.querySelector(".terminal-chart-card").getBoundingClientRect();
          if (!pane) return null;
          const box = pane.getBoundingClientRect();
          return {
            width: Math.round(box.width),
            height: Math.round(box.height),
            shareOfCard: Math.round(((box.width * box.height) / (cardBox.width * cardBox.height)) * 1000) / 10,
          };
        })(),
      };
    });
    assert.ok(rightSide, "the right-hand column renders the summary card and the panel");
    assert.equal(rightSide.order, "summary-then-panel", "the summary card is above the Breakout Logic panel");
    assert.ok(rightSide.summaryAbove, "and it sits above it, not beside it");
    if (wide) {
      assert.ok(rightSide.rightOfChart, "both are right of the chart");
    } else {
      // A phone stacks the composition: chart first, then the two cards.
      assert.ok(!rightSide.rightOfChart, "on a phone the cards stack under the chart");
    }
    assert.equal(rightSide.symbol, "BTC/USDT");
    assert.equal(rightSide.heading, "Breakout Logic");
    assert.deepEqual(rightSide.steps.map((step) => step.number), ["1", "2", "3"]);
    assert.deepEqual(rightSide.steps.map((step) => step.title), ["Breakout", "Retest", "Continuation"]);
    assert.equal(rightSide.steps[0].status, "Completed", "the stored breakout is completed");
    assert.equal(rightSide.steps[1].status, retestStates.includes("held") ? "Held" : "Awaiting Confirmation", "the retest status is the backend's own state");
    assert.equal(rightSide.steps[2].status, "In Progress", "continuation is in progress, not complete");
    assert.equal(rightSide.steps[2].tone, "amber");
    // A step with no stored record is flagged, so it is never drawn as done.
    assert.deepEqual(rightSide.steps.map((step) => step.recorded), ["true", "true", "false"]);
    assert.match(rightSide.steps[0].summary, /closed through the stored .* band/);
    assert.match(rightSide.steps[1].summary, /retest held|came back to the band/);
    assert.match(rightSide.steps[2].summary, /Nothing recorded yet/);
    assert.equal(rightSide.metaCount, 0, "no raw metadata lines under the steps in Simple mode");
    assert.ok(rightSide.keyIdea, "the card closes with a Key Idea");
    assert.match(rightSide.note, /No step is marked complete without a stored record/);
    assert.ok(rightSide.band.includes("Reference band"), "the broken band is stated");
    assert.ok(rightSide.smallestFont >= 11, `nothing in the right-hand column is smaller than 11px (smallest: ${rightSide.smallestFont})`);
    // Statuses are right-aligned pills.
    for (const step of rightSide.steps) {
      assert.ok(Math.abs(step.statusRight - rightSide.panelContentRight) <= 2, `the ${step.title} pill is right-aligned (${step.statusRight} vs ${rightSide.panelContentRight})`);
    }
    checks.push({ name: "right-hand column", ...rightSide });

    // --- 3b. The composition: ~75 % chart, ~23 % cards, tops on one line.
    const { widths, tops } = rightSide;
    const chartShare = Math.round((widths.chart / widths.row) * 1000) / 10;
    const columnShare = Math.round((widths.column / widths.row) * 1000) / 10;
    if (wide) {
      assert.ok(chartShare >= 73 && chartShare <= 77, `the chart is about 75 % of the row (${chartShare} %)`);
      assert.ok(columnShare >= 22 && columnShare <= 25, `the cards are about 23 % of the row (${columnShare} %)`);
      assert.ok(Math.abs(tops.chart - tops.column) <= 1, `the chart and the cards start on the same line (${tops.chart} vs ${tops.column})`);
      assert.ok(widths.chart > widths.column * 2.5, "the chart is far wider than the cards beside it");
      assert.ok(rightSide.canvas.shareOfCard >= 40, `the chart pane dominates the card (${rightSide.canvas.shareOfCard} %)`);
      assert.ok(rightSide.canvas.height >= 480, `the chart is tall enough to read (${rightSide.canvas.height}px)`);
    } else {
      assert.ok(widths.chart >= widths.column, "on a phone the chart and the cards stack, chart first");
    }
    checks.push({ name: "composition", chartShare, columnShare, tops, widths });

    // --- 3c. The default view of the screen, before any framing.
    await scrollToChart();
    await page.screenshot({ path: resolve(out, `breakouts-${viewport.label}-default.png`) });

    // --- 4. Frame the three steps, then read the callouts.
    await clickControl('[data-frame-stages="true"]');
    // Read it the way an operator would: the chart under the header, not
    // wherever the click helper had to scroll to reach the control.
    await scrollToChart();
    await new Promise((resolveWait) => setTimeout(resolveWait, 400));
    const framed = await logicalRange();
    assert.ok(framed, "the framed range is readable");
    checks.push({ name: "framed window", range: framed });

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
    assert.ok(callouts.arrows[0].y > 0 && callouts.arrows[0].y < viewport.height, "the arrow is inside the chart pane");
    // Each callout sits above or below the candle its step belongs to, stepped
    // apart when two would land in the same place: none may overlap another,
    // and every one must be drawn whole inside the pane with nothing covering it.
    const boxes = callouts.badges.map((badge) => ({
      stage: badge.stage, left: badge.left, top: badge.top, right: badge.left + badge.width, bottom: badge.top + badge.height,
    }));
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        assert.ok(!(a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom), `the ${a.stage} and ${b.stage} callouts do not overlap`);
      }
    }
    for (const badge of callouts.badges) {
      assert.ok(badge.insidePane, `the ${badge.stage} callout is drawn whole inside the chart pane`);
      if (badge.onTop !== null) assert.ok(badge.onTop, `nothing covers the ${badge.stage} callout`);
      assert.ok(Number.parseFloat(badge.fontSize) >= 13, `the ${badge.stage} callout text is large enough to read (${badge.fontSize})`);
      assert.ok(badge.numberSize >= 26, `the ${badge.stage} number circle is prominent (${badge.numberSize}px)`);
      assert.ok(badge.width <= callouts.badges[0].paneWidth, `the ${badge.stage} callout fits the pane width`);
    }
    // The arrangement: 1 above the breakout candle, 2 below and to the right of
    // its candle pointing back at it, 3 above the later price action.
    assert.equal(callouts.badges[0].place, "above", "callout 1 sits above the breakout candle");
    assert.equal(callouts.badges[1].place, "below", "callout 2 sits below the retest candle");
    assert.ok(callouts.badges[1].left > callouts.arrows[1].x, "callout 2 is offset to the right of the candle it points back at");
    assert.equal(callouts.badges[2].place, "above", "callout 3 sits above the later price action");
    // A step with no stored record is drawn differently from a confirmed one.
    assert.equal(callouts.badges[2].recorded, "false", "the unrecorded continuation is flagged");
    assert.equal(callouts.pendingLine, 1, "and its leader line is dashed, not solid");
    checks.push({ name: "callouts", ...callouts, breakoutX, retestX, boxes });

    // --- 5. The shaded reference band across the chart: blue-grey, and thick
    // enough to read as a zone rather than a hairline.
    const bands = await page.evaluate(() => {
      const paneWidth = Math.round(document.querySelector(".terminal-chart-wrap").getBoundingClientRect().width);
      return [...document.querySelectorAll(".zone-band")].map((node) => {
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return {
          top: box.top, height: Math.round(box.height), width: Math.round(box.width), paneWidth,
          fill: style.backgroundColor, edge: style.borderTopColor,
        };
      });
    });
    const labels = await page.evaluate(() => [...document.querySelectorAll(".zone-band-label")].map((node) => node.textContent));
    assert.ok(labels.includes("Resistance Zone"), `the shaded band is labelled (labels: ${JSON.stringify(labels)})`);
    const zone = bands[0];
    assert.ok(zone && zone.width >= zone.paneWidth - 2, `the band spans the full width of the chart (${zone?.width} of ${zone?.paneWidth})`);
    // ~30px at the primary size, and never the thin red strip it was before.
    assert.ok(zone.height >= (wide ? 28 : 20), `the band is thick enough to read as a zone (${zone.height}px)`);
    const rgb = zone.fill.match(/[\d.]+/g)?.map(Number) || [];
    assert.equal(rgb.length >= 3, true, `the band fill is readable: ${zone.fill}`);
    assert.ok(rgb[2] > rgb[0], `the band is blue-grey, not red (blue ${rgb[2]} > red ${rgb[0]})`);
    assert.ok(rgb[0] < 200 && rgb[2] < 230, "and it is translucent, not a solid block");
    checks.push({ name: "shaded band", labels, band: zone });

    // --- 5b. The header must not cover the chart or its callouts. Read the
    // callouts again with the chart pushed up under the sticky bars.
    await scrollToChart();
    const underHeader = await calloutState();
    const header = await page.evaluate(() => {
      const bar = document.querySelector(".topbar");
      const command = document.querySelector(".dash-commandbar");
      const bottom = Math.max(
        bar ? bar.getBoundingClientRect().bottom : 0,
        command ? command.getBoundingClientRect().bottom : 0,
      );
      return { bottom: Math.round(bottom) };
    });
    assert.equal(underHeader.badges.length, 3, "all three callouts are still drawn with the chart under the header");
    for (const badge of underHeader.badges) {
      assert.ok(badge.insidePane, `the ${badge.stage} callout is still whole inside the pane`);
      if (badge.onTop !== null) assert.ok(badge.onTop, `the header does not cover the ${badge.stage} callout`);
      assert.ok(badge.viewportTop >= header.bottom - 1, `the ${badge.stage} callout sits below the sticky header (${badge.viewportTop} vs ${header.bottom})`);
    }
    checks.push({ name: "callouts under the header", header, badges: underHeader.badges });

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
    checks.push({ name: "volume bars", ...volume });

    // --- 7. Screenshots of the finished layout.
    const stage = await page.$(".terminal-chart-card");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: resolve(out, `breakouts-${viewport.label}-full.png`), fullPage: true });
    await stage.screenshot({ path: resolve(out, `breakouts-${viewport.label}-stage.png`) });
    await scrollToChart();
    await page.screenshot({ path: resolve(out, `breakouts-${viewport.label}-framed.png`) });

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
    await page.screenshot({ path: resolve(out, `breakouts-${viewport.label}-after-return.png`) });
    checks.push({ name: "zoom preserved switching away and back", beforeReturn, tradePlan, backAgain });

    viewportReport.pageErrors = pageErrors;
    viewportReport.consoleErrors = consoleErrors;
    assert.deepEqual(pageErrors, [], "no uncaught page errors");
    await page.close();
}
} finally {
  await browser.close();
  writeFileSync(resolve(out, "breakouts-report.json"), `${JSON.stringify(report, null, 2)}\n`);
}

console.log(JSON.stringify(report, null, 2));
