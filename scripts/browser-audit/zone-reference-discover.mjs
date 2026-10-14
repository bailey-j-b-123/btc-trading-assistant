#!/usr/bin/env node
/**
 * Discovery pass: dump exactly what the Support & Resistance screen states about stored event
 * references, so a reviewer can read the operator's own text instead of inferring it.
 *
 * This is a read-only companion to `zone-reference-check.mjs` (which asserts). Use it when you need
 * to see the served payload, the painted labels, the explanation of each painted band, the
 * Support & Resistance panel rows, the nearby-zones card rows and the chart-evidence retests for a
 * given timeframe — for example when a demo database has no overlapping reference to show.
 *
 * The engine timeframe matters: the linkage is evaluated on the ENGINE frame, so the card rows and
 * `linked_events` facts belong to whatever timeframe the server's default is, while the panel and
 * the band explanations follow the VIEWED timeframe. Point it at both to see the difference.
 *
 * Usage (disposable synthetic server; see scripts/browser-audit/README.md):
 *
 *   TRADING_ASSISTANT_DEFAULT_TIMEFRAME=4h .venv/bin/python \
 *       scripts/browser-audit/offline_server.py --db .cache/brain-repair/sr.sqlite3 --port 8126
 *   LD_LIBRARY_PATH=$PWD/.cache/chromium-libs/lib node scripts/browser-audit/zone-reference-discover.mjs \
 *       http://127.0.0.1:8126/ .cache/verify/browser/zone-ref --timeframe 4h
 *
 * Writes `zone-reference-discovery.json` into the output directory and prints a readable summary.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import puppeteer from "puppeteer-core";

const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error("URL and output directory are required");
const argv = process.argv.slice(2);
const optionValue = (name, fallback = null) => {
  const index = argv.indexOf(`--${name}`);
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback;
};
const requestedTimeframe = optionValue("timeframe", null);
const viewport = optionValue("width", "1536x864");
const [width, height] = viewport.split("x").map(Number);
mkdirSync(out, { recursive: true });

const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
  args: chromium?.args || ["--no-sandbox"],
  env: { ...process.env, LANG: "en_GB.UTF-8", LC_ALL: "en_GB.UTF-8" },
});

const page = await browser.newPage();
await page.setViewport({ width, height, deviceScaleFactor: 1 });
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForFunction(
  () => document.querySelector(".hero-card") &&
    document.querySelector(".terminal-chart-card canvas") &&
    !document.querySelector('[aria-busy="true"]'),
  { timeout: 180000, polling: 500 },
);
await page.evaluate(() => document.fonts.ready);
if (requestedTimeframe) {
  const found = await page.evaluate((tf) => Boolean(document.querySelector(`[data-timeframe="${tf}"]`)), requestedTimeframe);
  if (!found) throw new Error(`no timeframe control for ${requestedTimeframe}`);
  await page.evaluate((tf) => document.querySelector(`[data-timeframe="${tf}"]`).click(), requestedTimeframe);
  // Deterministic settle: the button flips before the view exists, so wait for the app's own facts.
  await page.waitForFunction(
    (label) => {
      const meta = document.querySelector(".chart-evidence-meta")?.textContent || "";
      const status = document.querySelector(".chart-function-status")?.textContent || "";
      const namesTimeframe = new RegExp(`closed\\s+${label}\\s+candles`, "i").test(meta);
      const shown = /(\d+) band\(s\) shown/.exec(status);
      const layer = document.querySelector(".zone-band-layer");
      const painted = layer ? layer.querySelectorAll(".zone-band").length : 0;
      return namesTimeframe && (shown === null ? painted === 0 : Number(shown[1]) === painted);
    },
    { timeout: 180000, polling: 300 },
    requestedTimeframe.toUpperCase(),
  );
}
await page.waitForSelector('[data-function="support_resistance"]', { visible: true, timeout: 60000 });
await page.evaluate(() => document.querySelector('[data-function="support_resistance"]').click());
await page.waitForFunction(
  () => (document.querySelector(".chart-function-status")?.textContent || "").startsWith("Support / Resistance") &&
    document.querySelectorAll(".zone-row").length > 0,
  { timeout: 180000, polling: 300 },
);

const api = await page.evaluate(async (tf) => {
  const structure = await (await fetch(
    `/api/market/structure?symbol=BTC%2FUSDT${tf ? `&timeframe=${encodeURIComponent(tf)}` : ""}`)).json();
  const dashboard = await (await fetch("/api/dashboard?symbol=BTC%2FUSDT")).json();
  const bands = dashboard?.overlays?.zone_bands?.bands || [];
  const evidence = dashboard?.chart_evidence || {};
  const retests = evidence.retests || [];
  return {
    engineTimeframe: dashboard?.meta?.timeframe || null,
    asOf: dashboard?.meta?.as_of || null,
    structureTimeframe: structure?.timeframe || null,
    structureBands: (structure?.zone_bands?.bands || []).map((band) => `${band.band_low}-${band.band_high}`),
    engineBands: bands.map((band) => ({
      bounds: `${band.band_low}-${band.band_high}`,
      role: band.display_role,
      linked: band.linked_events?.linked,
      count: band.linked_events?.event_count,
      counts: band.linked_events?.counts,
      overlapping: band.linked_events?.overlapping,
      overlappingCount: band.linked_events?.overlapping_count,
      overlappingCounts: band.linked_events?.overlapping_counts,
      note: band.linked_events?.note,
      overlapNote: band.linked_events?.overlap_note,
    })),
    retests: {
      total: retests.length,
      withReference: retests.filter((item) => item.reference_type && item.reference_id).length,
      referenceTypes: retests.reduce((counts, item) => {
        const key = item.reference_type || "(none)";
        counts[key] = (counts[key] || 0) + 1;
        return counts;
      }, {}),
    },
  };
}, requestedTimeframe);

const painted = await page.evaluate(() =>
  [...(document.querySelector(".zone-band-layer")?.querySelectorAll(".zone-band-label") || [])]
    .map((node) => node.textContent || ""));

const explanations = [];
const labels = await page.$$(".zone-band-label");
for (let index = 0; index < labels.length; index += 1) {
  const label = (await page.$$(".zone-band-label"))[index];
  const labelText = await label.evaluate((node) => node.textContent || "");
  await label.click();
  await page.waitForFunction(
    () => document.querySelector(".evidence-explain-body")?.textContent?.length > 0,
    { timeout: 30000, polling: 100 },
  );
  await page.evaluate(() => {
    for (const details of document.querySelectorAll(".evidence-details")) details.open = true;
  });
  explanations.push({
    label: labelText,
    when: await page.evaluate(() => document.querySelector(".evidence-explain-when")?.textContent || ""),
    text: await page.evaluate(() => document.querySelector(".evidence-explain-body")?.textContent || ""),
  });
}

const panelRows = await page.evaluate(() =>
  [...document.querySelectorAll(".cfp-row")].map((row) => row.innerText.replace(/\s+/g, " ").trim()));
const zoneRows = await page.evaluate(() =>
  [...document.querySelectorAll(".zone-row")].map((row) => row.innerText.replace(/\s+/g, " ").trim()));

const dump = { url, viewport, requestedTimeframe, api, painted, explanations, panelRows, zoneRows };
writeFileSync(`${out}/zone-reference-discovery.json`, JSON.stringify(dump, null, 2));

console.log(`engine=${api.engineTimeframe} viewed=${api.structureTimeframe} asOf=${api.asOf}`);
console.log(`structure bands (${api.structureBands.length}): ${JSON.stringify(api.structureBands)}`);
for (const band of api.engineBands) {
  console.log(`  engine band ${band.bounds} linked=${band.linked} n=${band.count} overlapping=${band.overlapping} ` +
    `oCount=${band.overlappingCount} oCounts=${JSON.stringify(band.overlappingCounts)} note=${JSON.stringify(band.overlapNote)}`);
}
console.log(`retests: total=${api.retests.total} withReference=${api.retests.withReference} types=${JSON.stringify(api.retests.referenceTypes)}`);
console.log(`painted labels: ${JSON.stringify(painted)}`);
for (const row of explanations) {
  console.log(`  --- clicked ${JSON.stringify(row.label)} (${row.when})`);
  console.log(`      ${row.text.replace(/\s+/g, " ").slice(0, 700)}`);
}
console.log(`panel rows: ${JSON.stringify(panelRows, null, 1)}`);
console.log(`zone rows: ${JSON.stringify(zoneRows, null, 1)}`);
console.log(`discovery written to ${out}/zone-reference-discovery.json`);
await browser.close();
