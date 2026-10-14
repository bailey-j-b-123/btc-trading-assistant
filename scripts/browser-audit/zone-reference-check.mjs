#!/usr/bin/env node
/**
 * Zone-reference check: the operator-visible statements about a band's stored event references.
 *
 * Regression for BRAIN-AUDIT-0005 (a retest could never be linked, because the shared projection
 * dropped its reference) and BRAIN-AUDIT-0006 (a band whose level *was* referenced read as "no
 * stored breakout, sweep or retest used this zone as its own reference level" whenever the
 * historical reference's bounds differed at one edge — the usual case, because zone bounds are
 * re-derived per analysis frame while an event keeps the bounds it was detected with).
 *
 * What it proves, in a real browser, against the backend's own payload:
 *
 *  * every retest in the served chart evidence carries its reference type and id (0005);
 *  * zone-referenced retests reach the linkage: the bands' own `overlapping_counts.retest` is > 0
 *    somewhere on the view, so the retests are counted as recorded facts rather than dropped;
 *  * a band whose stored evidence OVERLAPS it (and is not identical to it) says so — in the band
 *    explanation, in the Support & Resistance panel row and in the nearby-zones card row — and the
 *    absolute negative is NOT emitted for it (0006);
 *  * a band whose stored evidence is a true link still states the link, and a band with no stored
 *    reference at all still gets the absolute negative: the overlap branch never swallows them;
 *  * the figures on screen are the figures the API reports (no invented counts).
 *
 * Setup (both disposable and synthetic; see scripts/browser-audit/README.md):
 *
 *   .venv/bin/python scripts/browser-audit/offline_server.py --db .cache/brain-repair/sr.sqlite3 --port 8126
 *
 * Run it with the engine timeframe set to the timeframe the demo series actually stores reference
 * events on, exactly as an operator whose backend default is that timeframe would see the screen:
 *
 *   TRADING_ASSISTANT_DEFAULT_TIMEFRAME=4h .venv/bin/python scripts/browser-audit/offline_server.py \
 *       --db .cache/brain-repair/sr.sqlite3 --port 8126
 *   LD_LIBRARY_PATH=$PWD/.cache/chromium-libs/lib node scripts/browser-audit/zone-reference-check.mjs \
 *       http://127.0.0.1:8126/ .cache/verify/browser/zone-reference --timeframe 4h
 *
 * The check fails (never passes vacuously) when the view offers no overlapping band and no
 * genuinely unreferenced band to compare against.
 *
 * Usage:
 *   node scripts/browser-audit/zone-reference-check.mjs <url> <out> [--timeframe 4h]
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
mkdirSync(out, { recursive: true });

/** The sizes the Support & Resistance layout is signed off at. */
const VIEWPORTS = [
  { label: "1536x864", width: 1536, height: 864 },
  { label: "1440x900", width: 1440, height: 900 },
  { label: "1920x1080", width: 1920, height: 1080 },
  { label: "390-mobile", width: 390, height: 844 },
];

/** The absolute negative a band with no stored reference at all may state — and only then. */
const ABSOLUTE_NEGATIVE = /No stored breakout, sweep or retest used this zone as its own reference level\./;
/** The absolute negative in the panel's own wording. */
const PANEL_NEGATIVE = /no recorded event used this zone/;
const OVERLAP_SENTENCE = /stored event[s]? \([^)]*\) reference a level whose stored bounds OVERLAP this band/;
const OVERLAP_FACT = /overlapping, non-identical reference/;
const PANEL_OVERLAP = /recorded event[s]? overlap this level at different bounds \(not counted as the same level\)/;
const CARD_OVERLAP = /event\(s\) overlap it at different bounds/;
const LINK_SENTENCE = /Used as the reference level of /;

const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || (await chromium.executablePath()),
  args: chromium?.args || ["--no-sandbox"],
  env: { ...process.env, LANG: "en_GB.UTF-8", LC_ALL: "en_GB.UTF-8" },
});

const report = { url, generatedAt: new Date().toISOString(), requestedTimeframe, viewports: [] };
let failures = 0;
const global = {
  // Non-vacuity counters. `overlapWordingShown` can only be earned where the view actually offers a
  // band whose stored evidence overlaps it without being identical; `offeredOverlapping` and
  // `offeredLinked` record what the payload offered, so a view with nothing to prove is reported as
  // such rather than read as a pass.
  overlapWordingShown: 0,
  overlapFactsChecked: 0,
  linksSeen: 0,
  negativesSeen: 0,
  offeredOverlapping: 0,
  offeredLinked: 0,
  retests: null,
};

/** One labelled assertion; records the outcome instead of throwing. */
function check(checks, label, condition, detail = null) {
  const ok = Boolean(condition);
  checks.push({ label, ok, detail });
  if (!ok) failures += 1;
  return ok;
}

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
    await page.setViewport({ width: viewport.width, height: viewport.height, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => document.querySelector(".hero-card") &&
        document.querySelector(".terminal-chart-card canvas") &&
        !document.querySelector('[aria-busy="true"]'),
      { timeout: 180000, polling: 500 },
    );
    await page.evaluate(() => document.fonts.ready);
    if (requestedTimeframe) {
      const switched = await page.evaluate((tf) => {
        const button = document.querySelector(`[data-timeframe="${tf}"]`);
        if (!button) return false;
        button.click();
        return true;
      }, requestedTimeframe);
      if (switched) {
        await page.waitForFunction(
          (label) => document.querySelector(`[data-timeframe="${label}"][aria-pressed="true"]`),
          { timeout: 60000, polling: 200 },
          requestedTimeframe,
        );
      }
    }
    await page.waitForSelector('[data-function="support_resistance"]', { visible: true, timeout: 60000 });
    await page.evaluate(() => document.querySelector('[data-function="support_resistance"]').click());
    // The application's own completion facts: the panel names the function and the card rows exist.
    await page.waitForFunction(
      () => (document.querySelector(".chart-function-status")?.textContent || "").startsWith("Support / Resistance") &&
        document.querySelectorAll(".zone-row").length > 0,
      { timeout: 180000, polling: 300 },
    );

    /** The backend's own linkage facts, read from the payload the screen is built from. */
    const api = await page.evaluate(async () => {
      const payload = await (await fetch("/api/dashboard?symbol=BTC%2FUSDT")).json();
      const bands = payload?.overlays?.zone_bands?.bands || [];
      const retests = payload?.chart_evidence?.retests || [];
      const evidence = payload?.chart_evidence || {};
      // The events the SAME payload serves, so the overlap fact can be re-derived here instead of
      // being taken on trust from `linked_events`.
      const kinds = ["breakouts", "failed_breakouts", "sweeps", "retests"];
      const served = kinds.flatMap((key) => (evidence[key] || []).map((item) => ({
        kind: item.kind || key,
        low: String(item.band_low ?? ""),
        high: String(item.band_high ?? ""),
        referenceType: item.reference_type ?? null,
        referenceId: item.reference_id ?? null,
      })));
      return {
        engineTimeframe: payload?.meta?.timeframe || null,
        asOf: payload?.meta?.as_of || null,
        servedEvents: served,
        bands: bands.map((band) => ({
          low: String(band.band_low),
          high: String(band.band_high),
          linked: band.linked_events?.linked === true,
          count: band.linked_events?.event_count ?? null,
          counts: band.linked_events?.counts ?? null,
          overlapping: band.linked_events?.overlapping === true,
          overlappingCount: band.linked_events?.overlapping_count ?? null,
          overlappingCounts: band.linked_events?.overlapping_counts ?? null,
        })),
        retests: {
          total: retests.length,
          withReference: retests.filter((item) => item.reference_type && item.reference_id).length,
          zoneReferences: retests.filter((item) => item.reference_type === "zone").length,
        },
      };
    });

    const viewportReport = {
      label: viewport.label,
      width: viewport.width,
      height: viewport.height,
      api,
      checks,
      pageErrors,
      consoleErrors,
      explanations: [],
      panelRows: [],
      zoneRows: [],
    };
    report.viewports.push(viewportReport);

    check(
      checks,
      "the served view is the engine timeframe the check was pointed at",
      !requestedTimeframe || api.engineTimeframe === requestedTimeframe,
      `engine=${api.engineTimeframe} requested=${requestedTimeframe}`,
    );
    // 0005: the shared projection must carry the retest's own reference, or no retest can ever be
    // linked and every downstream count is structurally zero.
    check(
      checks,
      "every retest in the served evidence carries its reference type and id (BRAIN-AUDIT-0005)",
      api.retests.total === 0 || api.retests.withReference === api.retests.total,
      `retests=${api.retests.total} withReference=${api.retests.withReference}`,
    );
    const retestOverlaps = api.bands.reduce(
      (total, band) => total + Number(band.overlappingCounts?.retest || 0), 0);
    check(
      checks,
      "zone-referenced retests reach the linkage as counted facts (BRAIN-AUDIT-0005)",
      retestOverlaps > 0,
      `overlapping retests counted across bands: ${retestOverlaps}; zone-referenced retests served: ${api.retests.zoneReferences}`,
    );
    global.retests = api.retests;
    global.offeredOverlapping += api.bands.filter((band) => band.overlappingCount > 0 && !band.linked).length;
    global.offeredLinked += api.bands.filter((band) => band.linked).length;

    /** The API band whose stored bounds the displayed figures are (display rounds to 2 dp). */
    const number = (text) => Number(String(text).replace(/[,\s]/g, ""));

    // Independent expectation: a zone-referenced event that touches a band's bounds without being
    // identical to them is exactly what the band must report as an overlapping, non-identical
    // reference. If the payload stopped reporting them while still serving the events, the numbers
    // below would disagree and the check would fail — it does not take the payload's own count as
    // its authority.
    const zoneEvents = api.servedEvents.filter((event) =>
      event.referenceType === "zone" && event.low && event.high);
    const expectedOverlap = (band) => {
      const low = number(band.low);
      const high = number(band.high);
      return zoneEvents.filter((event) => {
        const eventLow = number(event.low);
        const eventHigh = number(event.high);
        const identical = Math.abs(eventLow - low) <= 0.011 && Math.abs(eventHigh - high) <= 0.011;
        return !identical && eventLow < high && eventHigh > low;
      });
    };
    for (const band of api.bands) {
      const expected = expectedOverlap(band);
      check(
        checks,
        `the linkage of ${band.low}-${band.high} accounts for every zone-referenced event the payload serves`,
        Number(band.overlappingCount || 0) === expected.length,
        `reported=${band.overlappingCount} re-derived from the served events=${expected.length} ` +
          `[${expected.map((event) => `${event.kind}:${event.low}-${event.high}`).join(", ")}]`,
      );
    }

    const bandFor = (low, high) => {
      const displayedLow = number(low);
      const displayedHigh = number(high);
      if (!Number.isFinite(displayedLow) || !Number.isFinite(displayedHigh)) return null;
      return api.bands.find((band) =>
        Math.abs(displayedLow - number(band.low)) <= 0.011 &&
        Math.abs(displayedHigh - number(band.high)) <= 0.011) || null;
    };

    // --- the band explanation: click each painted label, expand its details, read the text -----
    const labels = await page.$$(".zone-band-label");
    for (let index = 0; index < labels.length; index += 1) {
      const label = (await page.$$(".zone-band-label"))[index];
      const labelText = await label.evaluate((node) => node.textContent || "");
      await label.click();
      await page.waitForFunction(
        () => document.querySelector(".evidence-explain-body")?.textContent?.length > 0,
        { timeout: 30000, polling: 100 },
      );
      // The explanation's stored facts sit inside a collapsed "Why, timing and influence" block;
      // an operator opens it, so the check opens it too instead of reading hidden text.
      await page.evaluate(() => {
        for (const details of document.querySelectorAll(".evidence-details")) details.open = true;
      });
      const body = await page.evaluate(() =>
        document.querySelector(".evidence-explain-body")?.textContent || "");
      const bounds = /([\d,.]+)\s*[–-]\s*([\d,.]+)/.exec(body);
      const band = bounds ? bandFor(bounds[1], bounds[2]) : null;
      viewportReport.explanations.push({
        label: labelText,
        linked: band?.linked ?? null,
        overlappingCount: band?.overlappingCount ?? null,
        hasOverlapSentence: OVERLAP_SENTENCE.test(body),
        hasAbsoluteNegative: ABSOLUTE_NEGATIVE.test(body),
        hasLinkSentence: LINK_SENTENCE.test(body),
        text: body.replace(/\s+/g, " ").trim().slice(0, 900),
      });
      if (!band) continue;
      if (band.overlappingCount > 0) {
        global.overlapFactsChecked += 1;
        // The unsupported absolute negative is the defect itself, whatever else the band has.
        check(
          checks,
          `the explanation of ${band.low}-${band.high} does not state an absolute negative`,
          !ABSOLUTE_NEGATIVE.test(body),
          body.replace(/\s+/g, " ").slice(0, 300),
        );
      }
      if (band.linked) {
        global.linksSeen += 1;
        check(
          checks,
          `the explanation of ${band.low}-${band.high} states the link it actually has`,
          LINK_SENTENCE.test(body),
          `linked=${band.linked} counts=${JSON.stringify(band.counts)}`,
        );
        check(
          checks,
          `the explanation of ${band.low}-${band.high} does not call an identical reference an overlap`,
          !OVERLAP_SENTENCE.test(body),
          null,
        );
      } else if (band.overlappingCount > 0) {
        const stated = OVERLAP_SENTENCE.test(body);
        if (stated) global.overlapWordingShown += 1;
        check(
          checks,
          `the explanation of ${band.low}-${band.high} states the overlapping, non-identical reference`,
          stated,
          `overlapping_count=${band.overlappingCount}`,
        );
      }
      if (!band.linked && Number(band.overlappingCount || 0) === 0 && band.count === 0) {
        global.negativesSeen += 1;
        check(
          checks,
          `the explanation of ${band.low}-${band.high} keeps the absolute negative it can prove`,
          ABSOLUTE_NEGATIVE.test(body),
          null,
        );
      }
    }

    // --- the Support & Resistance panel rows ---------------------------------------------------
    viewportReport.panelRows = await page.evaluate(() =>
      [...document.querySelectorAll(".cfp-row")].map((row) => row.innerText.replace(/\s+/g, " ").trim()));
    for (const row of viewportReport.panelRows) {
      const bounds = /([\d,.]+)\s*[–-]\s*([\d,.]+)/.exec(row);
      const band = bounds ? bandFor(bounds[1], bounds[2]) : null;
      if (!band) continue;
      if (band.overlappingCount > 0) {
        check(
          checks,
          `the panel row for ${band.low}-${band.high} does not state the absolute negative`,
          !PANEL_NEGATIVE.test(row),
          row,
        );
      }
      if (band.linked) {
        check(
          checks,
          `the panel row for ${band.low}-${band.high} states the link`,
          /referenced by /.test(row),
          row,
        );
      } else if (band.overlappingCount > 0) {
        check(
          checks,
          `the panel row for ${band.low}-${band.high} states ${band.overlappingCount} overlapping reference(s)`,
          PANEL_OVERLAP.test(row) && row.includes(String(band.overlappingCount)),
          row,
        );
      } else if (band.count === 0) {
        check(
          checks,
          `the panel row for ${band.low}-${band.high} keeps the absolute negative it can prove`,
          PANEL_NEGATIVE.test(row),
          row,
        );
      }
    }

    // --- the nearby-zones card rows (the engine timeframe's own card) --------------------------
    viewportReport.zoneRows = await page.evaluate(() =>
      [...document.querySelectorAll(".zone-row")].map((row) => row.innerText.replace(/\s+/g, " ").trim()));
    for (const row of viewportReport.zoneRows) {
      const bounds = /([\d,.]+)\s*[–-]\s*([\d,.]+)/.exec(row);
      const band = bounds ? bandFor(bounds[1], bounds[2]) : null;
      if (!band) continue;
      if (band.linked) {
        check(
          checks,
          `the zone row for ${band.low}-${band.high} states the link it actually has`,
          /stored event\(s\) referenced it/.test(row) && row.includes(String(band.count)),
          row,
        );
      } else if (band.overlappingCount > 0) {
        check(
          checks,
          `the zone row for ${band.low}-${band.high} states ${band.overlappingCount} overlapping reference(s)`,
          CARD_OVERLAP.test(row) && row.includes(String(band.overlappingCount)),
          row,
        );
        check(
          checks,
          `the zone row for ${band.low}-${band.high} does not claim a stored event referenced it`,
          !/stored event\(s\) referenced it/.test(row),
          row,
        );
      } else if (band.count === 0) {
        // The overlap clause must be a stored fact, not a banner printed on every row: a band
        // nothing references and nothing overlaps must not carry it.
        global.negativesSeen += 1;
        check(
          checks,
          `the zone row for ${band.low}-${band.high} makes no reference claim at all`,
          !CARD_OVERLAP.test(row) && !/stored event\(s\) referenced it/.test(row),
          row,
        );
      }
    }

    // The overlap wording is a stored fact only when the on-screen figure is the served one: a
    // card row must never carry a count the payload does not have.
    const servedOverlapping = api.bands.filter((band) => band.overlappingCount > 0).map((band) => band.overlappingCount);
    const statedCounts = [...viewportReport.zoneRows, ...viewportReport.panelRows]
      .flatMap((row) => [...row.matchAll(/(\d+) (?:event\(s\) overlap it|recorded events? overlap)/g)].map((m) => Number(m[1])));
    check(
      checks,
      "every on-screen overlapping count is a count the API serves",
      statedCounts.every((count) => servedOverlapping.includes(count)),
      `stated=${JSON.stringify(statedCounts)} served=${JSON.stringify(servedOverlapping)}`,
    );

    check(checks, "no uncaught page error", pageErrors.length === 0, pageErrors.join(" | ").slice(0, 300));
    check(checks, "no console error", consoleErrors.length === 0, consoleErrors.join(" | ").slice(0, 300));
    check(
      checks,
      "the page does not overflow horizontally",
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
      null,
    );
    await page.close();
  }
} finally {
  await browser.close();
}

// Non-vacuity. A feature that renders nothing at all, or that prints the same sentence on every
// row, must not pass: the view has to offer a genuinely unreferenced band, that band must be
// checked, and every store the view offers has to be shown in the right wording.
const nonVacuity = {
  overlappingFactsChecked: global.overlapFactsChecked,
  overlapWordingShown: global.overlapWordingShown,
  linkedBandsChecked: global.linksSeen,
  unreferencedBandsChecked: global.negativesSeen,
  overlappingBandsOffered: global.offeredOverlapping,
  linkedBandsOffered: global.offeredLinked,
  retests: global.retests,
  requirement:
    "a genuinely unreferenced band must be checked; if the view offers an overlapping non-identical " +
    "reference the overlap wording must be shown, and if it offers an identical one the link wording " +
    "must be shown",
};
const missing = [];
if (global.negativesSeen === 0) missing.push("no genuinely unreferenced band was checked");
if (global.offeredOverlapping > 0 && global.overlapWordingShown === 0) {
  missing.push("the view offers an overlapping non-identical reference but no surface stated it");
}
if (global.offeredLinked > 0 && global.linksSeen === 0) missing.push("the view offers a link but none was checked");
failures += missing.length ? 1 : 0;
report.nonVacuity = { ...nonVacuity, satisfied: missing.length === 0, unmet: missing };

report.failures = failures;
writeFileSync(`${out}/zone-reference-report.json`, JSON.stringify(report, null, 2));
const lines = [];
for (const viewport of report.viewports) {
  lines.push(`${viewport.label}: failures=${viewport.checks.filter((c) => !c.ok).length}`);
  for (const item of viewport.checks.filter((entry) => !entry.ok)) {
    lines.push(`    FAIL ${item.label} :: ${item.detail ?? ""}`);
  }
  lines.push(`    overlapping=${viewport.api.bands.filter((b) => b.overlappingCount > 0).length} linked=${viewport.api.bands.filter((b) => b.linked).length} retests=${viewport.api.retests.total}/${viewport.api.retests.zoneReferences} zone-referenced`);
}
lines.push(`non-vacuity: ${JSON.stringify(report.nonVacuity)}`);
console.log(lines.join("\n"));
if (failures > 0) {
  console.error(`zone-reference check FAILED (${failures} assertion(s)); report: ${out}/zone-reference-report.json`);
  process.exit(1);
}
console.log(`zone-reference check passed at ${VIEWPORTS.length} viewports; report: ${out}/zone-reference-report.json`);
