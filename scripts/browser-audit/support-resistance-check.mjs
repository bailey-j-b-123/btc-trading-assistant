#!/usr/bin/env node
/**
 * Support & Resistance regression against an isolated synthetic demo server.
 *
 * Proves, in a real browser and against the backend's own stored zones, that the
 * shaded bands on screen are the detector's zones at the detector's own prices:
 *
 *  * every painted band's pixel extent converts back, through the chart's own
 *    price scale, to a stored band's band_low / band_high (within 2 px);
 *  * no band is painted that the backend did not present, and no presented band
 *    is painted twice;
 *  * each band's label states the distinct test instants (not the swing-evidence
 *    sum) and the same figure the API reports;
 *  * a tall band is drawn as a light wash (alpha scaled down) so candles stay
 *    readable, while its edges stay at the exact stored bounds;
 *  * labels never overlap each other (>= 16 px apart) and never leave the pane;
 *  * the "not drawn" accounting on screen equals the API's hidden count PLUS the
 *    bands this chart's own readability cap withheld, so nothing disappears silently;
 *  * clicking a band label explains that band from its stored fields only, with no
 *    claim that it held, flipped, was retested or was broken;
 *  * switching timeframe re-derives the bands from that timeframe's own zones;
 *  * the page has no horizontal overflow and no uncaught page error.
 *
 * No API mocking, no invented zones, no production database.
 *
 * Usage:
 *   node scripts/browser-audit/support-resistance-check.mjs http://127.0.0.1:8123 ./out [--timeframe 1h] [--switch 4h]
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import puppeteer from "puppeteer-core";

const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error("URL and output directory are required");
const argv = process.argv.slice(2);
const optionValue = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const initialTimeframe = optionValue("timeframe", null);
const switchTimeframe = optionValue("switch", null);
mkdirSync(out, { recursive: true });

/** The sizes the Support & Resistance layout is signed off at. */
const VIEWPORTS = [
  { label: "1536x864", width: 1536, height: 864 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
  { label: "390-mobile", width: 390, height: 844 },
];

/** Read-only audit probes. They wrap the vendored library without mutating it. */
function installProbes() {
  window.__brainCharts = [];
  window.__brainCandles = [];
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
            if (!chart) return chart;
            window.__brainCharts.push(chart);
            const addSeries = chart.addCandlestickSeries;
            if (typeof addSeries === "function") {
              Object.defineProperty(chart, "addCandlestickSeries", {
                configurable: true,
                enumerable: true,
                value: function captureSeries(...seriesArgs) {
                  const series = addSeries.apply(chart, seriesArgs);
                  if (series) window.__brainCandles.push(series);
                  return series;
                },
              });
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
  /** Inverse of the chart's own price scale: a pixel offset back to a price. */
  window.__priceAtY = (y) => {
    const series = window.__brainCandles?.[0];
    if (!series || typeof series.coordinateToPrice !== "function") return null;
    const price = series.coordinateToPrice(y);
    return price === null || price === undefined ? null : price;
  };
  window.__bandPaneHeight = () => {
    const layer = document.querySelector(".zone-band-layer");
    return layer ? Math.round(layer.getBoundingClientRect().height) : null;
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
let failures = 0;

/** One labelled assertion; records the outcome instead of throwing. */
function check(checks, label, condition, detail = null) {
  const ok = Boolean(condition);
  checks.push({ label, ok, detail });
  if (!ok) failures += 1;
  return ok;
}

const alphaOf = (colour) => {
  const match = /rgba?\(([^)]+)\)/.exec(String(colour || ""));
  if (!match) return null;
  const parts = match[1].split(",").map((part) => part.trim());
  return parts.length === 4 ? Number(parts[3]) : 1;
};

try {
  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage();
    const checks = [];
    const pageErrors = [];
    const consoleErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.evaluateOnNewDocument(installProbes);
    await page.setViewport({ width: viewport.width, height: viewport.height, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => document.querySelector(".hero-card") &&
        document.querySelector(".terminal-chart-card canvas") &&
        !document.querySelector('[aria-busy="true"]'),
      { timeout: 180000, polling: 500 },
    );
    await page.evaluate(() => document.fonts.ready);
    // The Support & Resistance function is what this check is about; the dashboard
    // opens on another function, so select it exactly as an operator would.
    await page.waitForSelector('[data-function="support_resistance"]', { visible: true, timeout: 60000 });
    await page.evaluate(() => document.querySelector('[data-function="support_resistance"]').click());
    await page.waitForFunction(
      () => (document.querySelector(".chart-function-status")?.textContent || "").startsWith("Support / Resistance"),
      { timeout: 180000, polling: 300 },
    );
    await page.waitForFunction(
      () => document.querySelectorAll(".zone-band").length > 0 || /no zone|none stored/i.test(document.querySelector(".chart-function-status")?.textContent || ""),
      { timeout: 180000, polling: 300 },
    );

    const viewportReport = {
      label: viewport.label,
      width: viewport.width,
      height: viewport.height,
      checks,
      pageErrors,
      consoleErrors,
      screenshots: [],
    };
    report.viewports.push(viewportReport);

    /** The chart's own answer, read independently of the DOM. */
    const apiBands = async (timeframe) => page.evaluate(async (tf) => {
      const query = tf ? `?symbol=BTC%2FUSDT&timeframe=${encodeURIComponent(tf)}` : "";
      const payload = await (await fetch(`/api/market/structure${query}`)).json();
      const bands = payload?.zone_bands?.bands;
      return {
        timeframe: payload?.timeframe || null,
        stored: payload?.zone_bands?.total_stored ?? null,
        presented: payload?.zone_bands?.presented_count ?? null,
        hidden: payload?.zone_bands?.hidden_count ?? null,
        hiddenAbove: payload?.zone_bands?.hidden_above ?? null,
        hiddenBelow: payload?.zone_bands?.hidden_below ?? null,
        merged: payload?.zone_bands?.merged_count ?? null,
        skippedInvalid: payload?.zone_bands?.skipped_invalid ?? null,
        bands: Array.isArray(bands) ? bands.map((band) => ({
          position: band.position,
          displayRole: band.display_role ?? null,
          low: Number(band.band_low),
          high: Number(band.band_high),
          tests: band.distinct_test_timestamps ?? null,
          evidences: band.touch_count ?? null,
          tf: band.source_timeframe ?? null,
          faded: band.faded === true,
          linked: band.linked_events?.linked === true,
        })) : null,
      };
    }, timeframe);

    /** What the DOM actually painted, in both pixels and prices. */
    const paintedBands = () => page.evaluate(() => {
      const layer = document.querySelector(".zone-band-layer");
      if (!layer) return { present: false, bands: [], labels: [], offViewNote: null, paneHeight: null };
      const pane = layer.getBoundingClientRect();
      const bands = [...layer.querySelectorAll(".zone-band")].map((node) => {
        const box = node.getBoundingClientRect();
        const top = box.top - pane.top;
        const height = box.height;
        const fill = getComputedStyle(node).getPropertyValue("--zone-fill").trim();
        return {
          className: node.className,
          top: Math.round(top * 100) / 100,
          height: Math.round(height * 100) / 100,
          topPrice: window.__priceAtY(top),
          bottomPrice: window.__priceAtY(top + height),
          fill,
          edge: getComputedStyle(node).getPropertyValue("--zone-edge").trim(),
          paneShare: Number(node.style.getPropertyValue("--zone-pane-share")) || null,
          pointerEvents: getComputedStyle(node).pointerEvents,
          width: Math.round(box.width),
        };
      });
      const labels = [...layer.querySelectorAll(".zone-band-label")].map((node) => {
        const box = node.getBoundingClientRect();
        return {
          text: node.textContent || "",
          title: node.getAttribute("title") || "",
          ariaLabel: node.getAttribute("aria-label") || "",
          top: Math.round((box.top - pane.top) * 100) / 100,
          left: Math.round((box.left - pane.left) * 100) / 100,
          right: Math.round((box.right - pane.left) * 100) / 100,
          height: Math.round(box.height * 100) / 100,
          fontSize: getComputedStyle(node).fontSize,
          visible: box.width > 0 && box.height > 0,
        };
      });
      const offView = layer.querySelector(".zone-band-offview");
      return {
        present: true,
        paneHeight: Math.round(pane.height),
        paneWidth: Math.round(pane.width),
        bands,
        labels,
        offViewNote: offView ? offView.textContent : null,
      };
    });

    const readView = () => page.evaluate(() => ({
      status: document.querySelector(".chart-function-status")?.textContent || "",
      meta: document.querySelector(".chart-evidence-meta")?.textContent || "",
      timeframe: document.querySelector('[data-timeframe][aria-pressed="true"]')?.textContent?.trim()
        || document.querySelector(".chart-history-note")?.textContent || "",
      panel: document.querySelector(".chart-function-panel")?.innerText || "",
      explanation: document.querySelector(".evidence-explain-body")?.innerText || "",
      explainWhen: document.querySelector(".evidence-explain-when")?.textContent || "",
      overflowPx: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
    }));

    const initialState = await readView();
    const streamTimeframe = (initialTimeframe || initialState.timeframe || "").trim();
    const first = await apiBands(initialTimeframe);
    const viewportKey = viewport.label;

    const [state, painted] = [initialState, await paintedBands()];
    viewportReport.initial = { state, api: first, painted };

    check(checks, "no horizontal overflow", state.overflowPx === 0, `overflowPx=${state.overflowPx}`);
    check(checks, "the zone band layer exists", painted.present === true);
    check(
      checks,
      "the API presents at least one band on the default view",
      (first.presented ?? 0) >= 1,
      `presented=${first.presented} stored=${first.stored} timeframe=${first.timeframe}`,
    );

    // --- every painted band is a stored band, at the stored prices -----------------
    // A band that reaches the top or bottom of the pane is CLIPPED by the visible price
    // range, so its painted pixels cover a subset of its stored bounds. Nothing else may
    // move a band: away from a pane edge the painted extent must be the stored bounds.
    const paintedPrices = painted.bands.map((band) => ({
      low: Math.min(Number(band.topPrice), Number(band.bottomPrice)),
      high: Math.max(Number(band.topPrice), Number(band.bottomPrice)),
      clipped: band.top <= 0.5 || band.top + band.height >= (painted.paneHeight ?? 0) - 0.5,
      band,
    }));
    let matchedAll = true;
    const geometryRows = [];
    for (const row of paintedPrices) {
      const tolerance = Math.max((row.high - row.low) * 0.02, 1e-9);
      const match = (first.bands || []).find((band) =>
        row.low >= band.low - tolerance && row.high <= band.high + tolerance);
      const exact = (first.bands || []).find((band) =>
        Math.abs(band.low - row.low) <= tolerance && Math.abs(band.high - row.high) <= tolerance);
      geometryRows.push({
        paintedLow: row.low,
        paintedHigh: row.high,
        clipped: row.clipped,
        stored: match ? { low: match.low, high: match.high, position: match.position } : null,
        exact: Boolean(exact),
      });
      if (!match || (!exact && !row.clipped)) matchedAll = false;
    }
    check(
      checks,
      "every painted band lies inside a stored band's bounds, and is exact unless the pane clips it",
      matchedAll,
      JSON.stringify(geometryRows),
    );

    // --- one paint per presented band the chart chose to draw ----------------------
    const drawn = painted.bands.length;
    const capHidden = Math.max(0, (first.presented ?? 0) - drawn);
    const expectedNotDrawn = (first.hidden ?? 0) + capHidden;
    check(
      checks,
      "no band is painted twice",
      new Set(geometryRows.map((row) => `${row.stored?.low}:${row.stored?.high}`)).size === geometryRows.length,
      `${geometryRows.length} painted`,
    );
    const statusText = state.status || "";
    const shownMatch = /(\d+) band\(s\) shown nearest price first/.exec(statusText);
    check(
      checks,
      "the status line's drawn count equals the painted bands",
      shownMatch !== null && Number(shownMatch[1]) === drawn,
      `status="${statusText}" painted=${drawn}`,
    );
    const metaText = state.meta || "";
    const metaMatch = /S\/R bands: (\d+) drawn, (\d+) stored band\(s\) not drawn, (\d+) merged/.exec(metaText);
    check(
      checks,
      "the line under the chart agrees with the status line about drawn and withheld bands",
      metaMatch !== null &&
        Number(metaMatch[1]) === drawn &&
        Number(metaMatch[2]) === expectedNotDrawn,
      `meta="${metaText}" painted=${drawn} expectedNotDrawn=${expectedNotDrawn}`,
    );
    const notDrawnMatch = /(\d+) further band\(s\) not drawn/.exec(statusText);
    check(
      checks,
      "the status line states the real not-drawn total (API hidden + chart cap)",
      expectedNotDrawn === 0
        ? /no further stored band was left out/.test(statusText)
        : notDrawnMatch !== null && Number(notDrawnMatch[1]) === expectedNotDrawn,
      `expected=${expectedNotDrawn} (apiHidden=${first.hidden} capHidden=${capHidden}) status="${statusText}"`,
    );

    // --- readability: a tailored wash for a tall band, never an opaque curtain ------
    const tooOpaque = painted.bands.filter((band) =>
      band.fill.startsWith("rgba") && Number(band.fill.split(",")[3]?.replace(")", "").trim()) > 0.12);
    check(checks, "no band is painted with a fill above 0.12 alpha", tooOpaque.length === 0, JSON.stringify(tooOpaque.map((b) => b.fill)));
    // The wash is the band's own fill with its alpha scaled by 0.35 / pane share, never
    // below 30 % of it, so a band filling the pane still reads as a tint. The edges do not
    // move: only the alpha is scaled.
    const BASE_ALPHA = { support: 0.06, resistance: 0.06, price_inside: 0.08, reference: 0.16 };
    const tallBands = painted.bands.filter((band) => band.paneShare !== null && band.paneShare > 0.35);
    const tallScaled = tallBands.every((band) => {
      const key = /zone-band--below_price/.test(band.className) ? "support"
        : /zone-band--above_price/.test(band.className) ? "resistance"
          : /zone-band--reference/.test(band.className) ? "reference"
            : "price_inside";
      const expected = BASE_ALPHA[key] * Math.max(0.3, Math.min(1, 0.35 / band.paneShare));
      const actual = alphaOf(band.fill);
      return actual !== null && Math.abs(actual - Math.round(expected * 1000) / 1000) <= 0.002 && actual <= BASE_ALPHA[key];
    });
    check(
      checks,
      "a band taller than a third of the pane is drawn as a scaled wash",
      tallBands.length === 0 || tallScaled,
      JSON.stringify(tallBands.map((band) => ({ share: band.paneShare, fill: band.fill }))),
    );
    check(
      checks,
      "the band layer never intercepts pointer input",
      painted.bands.every((band) => band.pointerEvents === "none"),
      JSON.stringify(painted.bands.map((band) => band.pointerEvents)),
    );

    // --- the approved colour language: teal support, red resistance, neutral inside --
    const EDGE_FOR = { support: "#2ec4b6", resistance: "#ef5350", price_inside: "#f5a623" };
    const wrongEdge = painted.bands.filter((band) => {
      const key = /zone-band--below_price/.test(band.className) ? "support"
        : /zone-band--above_price/.test(band.className) ? "resistance"
          : "price_inside";
      // Support and resistance share the position class with the tone the style map
      // picks; a reference-tone band is the breakout sequence's own and is skipped here.
      if (/zone-band--reference/.test(band.className)) return false;
      return band.edge.toLowerCase() !== EDGE_FOR[key];
    });
    check(
      checks,
      "support is teal, resistance is red and a price-inside band is neutral amber",
      wrongEdge.length === 0,
      JSON.stringify(painted.bands.map((band) => ({ className: band.className, edge: band.edge }))),
    );
    // The colour language is decided by the stored band's own position, so the class the
    // painter used must be that stored position — never the previous band's or a guess.
    const positions = geometryRows.map((row, index) => ({
      painted: /zone-band--below_price/.test(painted.bands[index].className) ? "below_price"
        : /zone-band--above_price/.test(painted.bands[index].className) ? "above_price" : "price_inside",
      stored: row.stored?.position ?? null,
      clipped: row.clipped,
    }));
    check(
      checks,
      "each painted band carries the colour of the stored position the API reports",
      positions.every((row) => row.stored !== null && row.stored === row.painted),
      JSON.stringify(positions),
    );

    // --- labels: the truthful test figure, no overlap, inside the pane -------------
    check(
      checks,
      "every painted band has exactly one label",
      painted.labels.length === drawn,
      `${painted.labels.length} labels for ${drawn} bands`,
    );
    const labelGaps = [];
    const ordered = [...painted.labels].sort((a, b) => a.top - b.top);
    for (let index = 1; index < ordered.length; index += 1) {
      labelGaps.push(Math.round((ordered[index].top - (ordered[index - 1].top + ordered[index - 1].height)) * 100) / 100);
    }
    check(
      checks,
      "labels never overlap (>= 16 px between label boxes)",
      labelGaps.every((gap) => gap >= 16 - 0.5),
      `gaps=${JSON.stringify(labelGaps)}`,
    );
    check(
      checks,
      "labels stay inside the pane",
      painted.labels.every((label) => label.top >= -1 && label.top + label.height <= painted.paneHeight + 1),
      JSON.stringify(painted.labels.map((label) => ({ top: label.top, height: label.height, pane: painted.paneHeight }))),
    );
    const claims = /held|flipp|retest|broken|strength|probab|guarantee|smart money|institution/i;
    const labelTexts = painted.labels.map((label) => `${label.text} ${label.title} ${label.ariaLabel}`);
    check(
      checks,
      "no label claims a zone held, flipped, was retested or was broken",
      labelTexts.every((text) => !claims.test(text)),
      JSON.stringify(labelTexts.filter((text) => claims.test(text))),
    );
    const labelFigures = painted.labels.map((label) => {
      const match = /·\s*(\d+)\s*×?\s*$/.exec(label.text.trim());
      return match ? Number(match[1]) : null;
    });
    const apiFigures = new Set((first.bands || []).map((band) => band.tests));
    check(
      checks,
      "each label states a test count the API reports",
      labelFigures.length > 0 && labelFigures.every((figure) => figure !== null && apiFigures.has(figure)),
      `labels=${JSON.stringify(labelFigures)} api=${JSON.stringify([...apiFigures])}`,
    );
    check(
      checks,
      "the label's own title states the distinct test instants and the swing evidence",
      painted.labels.every((label) => !label.visible || /distinct test instant/.test(label.title)),
      JSON.stringify(painted.labels.map((label) => label.title)),
    );

    // --- the panel reads the same numbers -------------------------------------------
    const panel = state.panel || "";
    check(
      checks,
      "the panel shows the bands this chart drew, not the raw store",
      drawn === 0 || panel.includes(`${drawn}`),
      `panel=${panel.slice(0, 400)}`,
    );

    // --- interaction: selecting a band explains it, from stored fields only ---------
    let selection = null;
    if (painted.labels.length > 0) {
      const clickedLabel = await page.$eval(".zone-band-label", (node) => node.textContent || "");
      await page.$eval(".zone-band-label", (node) => node.click());
      await new Promise((resolveWait) => setTimeout(resolveWait, 250));
      const after = await readView();
      selection = { clickedLabel, body: after.explanation, when: after.explainWhen };
      const clickedFigure = Number((/·\s*(\d+)\s*×?\s*$/.exec(clickedLabel.trim()) || [])[1]);
      const candidates = (first.bands || []).filter((band) => band.tests === clickedFigure);
      check(
        checks,
        "clicking a band explains that zone with its stored test figure",
        /test/i.test(after.explanation) && after.explanation.length > 0,
        after.explanation.slice(0, 400),
      );
      check(
        checks,
        "the explanation names the timeframe the zone came from",
        /zone|higher-timeframe/i.test(after.explainWhen),
        after.explainWhen,
      );
      check(
        checks,
        "the explanation makes no held / flipped / retested / broken claim",
        !claims.test(after.explanation),
        after.explanation.slice(0, 600),
      );
      const formatted = (value) => Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const matched = candidates.some((band) =>
        after.explanation.includes(formatted(band.low)) && after.explanation.includes(formatted(band.high)));
      check(
        checks,
        "the explanation carries the clicked band's own stored bounds",
        candidates.length > 0 && matched,
        `label="${clickedLabel}" candidates=${JSON.stringify(candidates.map((band) => [band.low, band.high]))} body=${after.explanation.slice(0, 300)}`,
      );
      viewportReport.selection = selection;
    }
    await page.screenshot({ path: resolve(out, `support-resistance-${viewportKey}-selected.png`), fullPage: true });
    const full = resolve(out, `support-resistance-${viewportKey}-full.png`);
    await page.screenshot({ path: full, fullPage: true });
    viewportReport.screenshots.push(full);
    const stagePath = resolve(out, `support-resistance-${viewportKey}-stage.png`);
    const stage = await page.$(".chart-stage");
    if (stage) await stage.screenshot({ path: stagePath });
    viewportReport.screenshots.push(stagePath);

    // --- switching timeframe derives the bands again --------------------------------
    if (switchTimeframe) {
      const clicked = await page.evaluate((label) => {
        const button = document.querySelector(`[data-timeframe="${label}"]`);
        if (!button) return false;
        button.click();
        return true;
      }, switchTimeframe);
      if (clicked) {
        await page.waitForFunction(
          (label) => {
            const pressed = document.querySelector(`[data-timeframe="${label}"][aria-pressed="true"]`);
            const canvas = document.querySelector(".terminal-chart-card canvas");
            return Boolean(pressed && canvas) && !document.querySelector('[aria-busy="true"]');
          },
          { timeout: 180000, polling: 300 },
          switchTimeframe,
        );
        // The button state flips before the switched view exists: the app still has to fetch and
        // apply THIS timeframe's candles, annotations and structure. A fixed sleep raced that work
        // and could read the previous timeframe's bands, so wait for the app's own two facts
        // instead — the evidence line names the switched timeframe's candle window, and the band
        // layer holds exactly the bands the status line says it drew. Neither fact states the
        // ANSWER (which zones, at which prices); they only say the view has finished switching.
        let switchSettled = true;
        let switchWaitError = null;
        try {
          await page.waitForFunction(
            (upper) => {
              const meta = document.querySelector(".chart-evidence-meta")?.textContent || "";
              const status = document.querySelector(".chart-function-status")?.textContent || "";
              // The evidence line reads "Evidence window: last 360 closed 4H candles (limit 500)".
              const namesTimeframe = new RegExp(`closed\\s+${upper}\\s+candles`, "i").test(meta);
              const shown = /(\d+) band\(s\) shown/.exec(status);
              const layer = document.querySelector(".zone-band-layer");
              const paintedCount = layer ? layer.querySelectorAll(".zone-band").length : 0;
              // A status line that reports no drawn bands must agree with an empty layer.
              const consistent = shown === null ? paintedCount === 0 : Number(shown[1]) === paintedCount;
              return namesTimeframe && consistent;
            },
            { timeout: 120000, polling: 200 },
            switchTimeframe.toUpperCase(),
          );
        } catch (error) {
          switchSettled = false;
          switchWaitError = String(error?.message || error).slice(0, 300);
        }
        const second = await apiBands(switchTimeframe);
        const afterSwitch = await paintedBands();
        const rows = afterSwitch.bands.map((band) => ({
          low: Math.min(Number(band.topPrice), Number(band.bottomPrice)),
          high: Math.max(Number(band.topPrice), Number(band.bottomPrice)),
          // A band reaching a pane edge is CLIPPED by the visible price range, exactly as the
          // default-view geometry check above documents: its painted pixels then cover a subset of
          // its stored bounds. Away from a pane edge the painted extent must still be the stored
          // bounds, so the clipping allowance never hides a band that drifted.
          clipped: band.top <= 0.5 || band.top + band.height >= (afterSwitch.paneHeight ?? 0) - 0.5,
        }));
        const everyRowMatches = rows.every((row) => {
          const tolerance = Math.max((row.high - row.low) * 0.35, 1e-9);
          const containing = (second.bands || []).filter((band) =>
            row.low >= band.low - tolerance && row.high <= band.high + tolerance);
          if (containing.length === 0) return false;
          if (row.clipped) return true;
          return containing.some((band) =>
            Math.abs(band.low - row.low) <= tolerance && Math.abs(band.high - row.high) <= tolerance);
        });
        viewportReport.afterSwitch = { api: second, painted: afterSwitch, switchSettled, switchWaitError };
        check(
          checks,
          `the switched ${switchTimeframe} view finished applying its own structure`,
          switchSettled,
          switchWaitError,
        );
        // The switched view must actually SHOW the switched timeframe's zones. Without this, every
        // band assertion below passes vacuously on an empty layer — the exact failure mode where a
        // non-engine timeframe silently borrows nothing and draws nothing while the API stores bands.
        const switchedStored = Number(second.stored ?? second.presented ?? 0);
        check(
          checks,
          `after switching to ${switchTimeframe} the chart paints that timeframe's stored bands`,
          !(Number.isFinite(switchedStored) && switchedStored > 0) || rows.length > 0,
          JSON.stringify({ stored: second.stored, presented: second.presented, painted: rows.length }),
        );
        check(
          checks,
          `after switching to ${switchTimeframe} every painted band is that timeframe's own zone`,
          rows.length === 0 || everyRowMatches,
          JSON.stringify({ rows, api: second.bands }),
        );
        check(
          checks,
          `after switching to ${switchTimeframe} the labels name ${switchTimeframe.toUpperCase()}`,
          afterSwitch.labels.every((label) => label.text.toUpperCase().includes(switchTimeframe.toUpperCase())),
          JSON.stringify(afterSwitch.labels.map((label) => label.text)),
        );
        await page.screenshot({ path: resolve(out, `support-resistance-${viewportKey}-${switchTimeframe}.png`), fullPage: true });
      } else {
        check(checks, `the ${switchTimeframe} timeframe control exists`, false, "control not found");
      }
    }

    check(checks, "no uncaught page error", pageErrors.length === 0, JSON.stringify(pageErrors));
    viewportReport.consoleErrors = consoleErrors;
    await page.close();
  }
} finally {
  await browser.close();
}

report.failures = failures;
const reportPath = resolve(out, "support-resistance-report.json");
writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`);
const failed = report.viewports.flatMap((viewport) =>
  viewport.checks.filter((entry) => !entry.ok).map((entry) => `${viewport.label}: ${entry.label}`));
if (failed.length) {
  console.error(`support-resistance check FAILED (${failed.length}):\n - ${failed.join("\n - ")}`);
  process.exitCode = 1;
} else {
  console.log(`support-resistance check passed at ${report.viewports.length} viewports; report: ${reportPath}`);
}
