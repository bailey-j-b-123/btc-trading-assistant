#!/usr/bin/env node
/** Desktop-only bottom-layout regression against an isolated demo server.
 * No API mocking or invented performance figures. Also checks responsive switching.
 * Usage: node desktop-bottom-check.mjs http://127.0.0.1:8123 /path/to/screenshots
 */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import puppeteer from "puppeteer-core";

const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error("URL and screenshot directory are required");
mkdirSync(out, { recursive: true });
const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || await chromium.executablePath(),
  args: chromium?.args || ["--no-sandbox"],
});
const reports = [];
try {
  for (const width of [1440, 1920]) {
    const page = await browser.newPage();
    const errors = [];
    const consoleErrors = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
    await page.setViewport({ width, height: 1080, deviceScaleFactor: 1 });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    const ready = async () => page.waitForFunction(() => document.querySelector(".hero-card") &&
      document.querySelector(".terminal-chart-card canvas") && !document.querySelector('[aria-busy="true"]'));
    await ready();
    await page.evaluate(() => document.fonts.ready);
    const check = async left => {
      const layout = await page.evaluate(left => {
        const rect = selector => {
          const node = document.querySelector(selector);
          const r = node.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, hidden: node.hidden };
        };
        return {
          overflowPx: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          tabs: rect(".dash-tabs"),
          left: rect(`#dash-panel-${left}`),
          performance: rect("#dash-panel-performance"),
          performanceTabHidden: document.querySelector("#dash-tab-performance").hidden,
          unavailable: [...document.querySelectorAll('.metric-tile--headline')].map(n => n.innerText),
        };
      }, left);
      assert.equal(layout.overflowPx, 0);
      assert.equal(layout.left.hidden, false);
      assert.equal(layout.performance.hidden, false);
      assert.equal(layout.performanceTabHidden, true);
      assert.ok(layout.performance.left >= layout.left.right, "Performance is to the right of the left panel");
      assert.ok(Math.abs(layout.performance.top - layout.tabs.top) < 1, "Performance aligns with left tab strip");
      assert.ok(layout.left.top >= layout.tabs.bottom, "left content is below its tabs");
      assert.ok(layout.performance.right <= width, "Performance fits the viewport");
      assert.equal(layout.unavailable.length, 2);
      assert.ok(layout.unavailable.every(text => text.includes("Not available yet")), "demo metrics stay honestly unavailable");
      return layout;
    };
    const paper = await check("paper");
    await page.screenshot({ path: resolve(out, `desktop-${width}.png`), fullPage: true });
    await page.$eval(".dash-bottom", node => window.scrollTo(0, window.scrollY + node.getBoundingClientRect().top - 210));
    await page.screenshot({ path: resolve(out, `desktop-${width}-bottom.png`) });
    await page.click("#dash-tab-diagnostics");
    await page.waitForFunction(() => !document.querySelector("#dash-panel-diagnostics").hidden);
    const diagnostics = await check("diagnostics");
    await page.screenshot({ path: resolve(out, `desktop-${width}-diagnostics.png`) });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.click("#refresh-dashboard");
    await page.waitForFunction(() => !document.querySelector("#refresh-dashboard").disabled);
    await ready();
    await check("paper"); // Manual refresh remounts the dashboard on its existing default tab.
    await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1 });
    await page.waitForFunction(() => !document.querySelector("#dash-tab-performance").hidden && document.querySelector("#dash-panel-performance").hidden);
    await page.$eval(".dash-tabs", node => window.scrollTo(0, window.scrollY + node.getBoundingClientRect().top - 400));
    await page.click("#dash-tab-performance");
    await page.waitForFunction(() => !document.querySelector("#dash-panel-performance").hidden);
    await page.setViewport({ width, height: 1080, deviceScaleFactor: 1 });
    await page.waitForFunction(() => document.querySelector("#dash-tab-performance").hidden && !document.querySelector("#dash-panel-paper").hidden);
    await check("paper");
    assert.deepEqual(errors, [], `browser errors at ${width}px`);
    reports.push({ width, paper, diagnostics, refreshAndResponsiveSwitch: "passed", errors, consoleErrors });
    await page.close();
  }
  writeFileSync(resolve(out, "desktop-bottom-report.json"), JSON.stringify(reports, null, 2) + "\n");
  console.log(JSON.stringify(reports, null, 2));
} finally {
  await browser.close();
}
