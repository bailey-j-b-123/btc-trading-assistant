#!/usr/bin/env node
/** Offline-demo browser regression for the Dashboard V2 header/trading bar.
 * Start the isolated demo server first; never point this at a production DB.
 * Usage: node mobile-layout-check.mjs http://127.0.0.1:8123 /path/to/screenshots
 * Uses CHROME_PATH or the optional sandbox @sparticuz/chromium package.
 */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import puppeteer from "puppeteer-core";

const url = process.argv[2];
const out = process.argv[3];
if (!url || !out) throw new Error("URL and screenshot directory are required");
mkdirSync(out, { recursive: true });
const chromium = process.env.CHROME_PATH ? null : (await import("@sparticuz/chromium")).default;
const browser = await puppeteer.launch({
  headless: true,
  executablePath: process.env.CHROME_PATH || await chromium.executablePath(),
  args: chromium?.args || ["--no-sandbox"],
});
try {
  for (const width of [390, 768]) {
    const page = await browser.newPage();
    await page.setViewport({ width, height: width === 390 ? 844 : 1024, deviceScaleFactor: 1 });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => document.querySelector(".hero-card") &&
      document.querySelector(".terminal-chart-card canvas") && !document.querySelector('[aria-busy="true"]'));
    await page.evaluate(() => document.fonts.ready);
    const check = async () => page.evaluate(() => {
      const issues = [];
      const rect = element => element.getBoundingClientRect();
      const visible = element => rect(element).width > 0 && rect(element).height > 0;
      const label = element => element.className || element.tagName;
      const contained = (parent, child) => {
        const a = rect(parent), b = rect(child);
        if (b.left < a.left - 1 || b.right > a.right + 1 || b.top < a.top - 1 || b.bottom > a.bottom + 1)
          issues.push(`${label(child)} escapes ${label(parent)}`);
      };
      const groups = [".topbar", ".topbar-instrument", ".topbar-meta", ".dash-commandbar",
        ".commandbar-identity", ".chart-timeframe-controls", ".chart-timeframe-switch", ".commandbar-health"];
      for (const selector of groups) {
        const parent = document.querySelector(selector);
        const children = [...parent.children].filter(visible);
        for (const child of children) contained(parent, child);
        for (let i = 0; i < children.length; i++) {
          for (let j = i + 1; j < children.length; j++) {
            const a = rect(children[i]), b = rect(children[j]);
            if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 &&
                Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1)
              issues.push(`${label(children[i])} overlaps ${label(children[j])}`);
          }
        }
      }
      const header = rect(document.querySelector(".topbar"));
      const bar = rect(document.querySelector(".dash-commandbar"));
      if (header.bottom > bar.top + 1) issues.push("header overlaps trading bar");
      return { overflowPx: document.documentElement.scrollWidth - document.documentElement.clientWidth, issues };
    });
    const initial = await check();
    console.log(JSON.stringify({ width, initial }));
    assert.equal(initial.overflowPx, 0, `overflow at ${width}px`);
    assert.deepEqual(initial.issues, [], `layout at ${width}px`);
    // The controls must remain usable, not merely fit in a screenshot.
    for (const timeframe of ["5m", "15m", "4h", "1d", "1h"]) {
      const selector = timeframe === "1d" ? '[data-view-timeframe="1d"]' : `[data-timeframe="${timeframe}"]`;
      await page.click(selector);
      await page.waitForFunction(selector => {
        const button = document.querySelector(selector);
        return button?.getAttribute("aria-pressed") === "true" && !button.disabled;
      }, {}, selector);
      assert.deepEqual((await check()).issues, [], `timeframe ${timeframe} at ${width}px`);
    }
    await page.click(".more-menu summary");
    const menuRight = await page.$eval(".more-list", element => element.getBoundingClientRect().right);
    assert.ok(menuRight <= width, `More menu off-screen at ${width}px`);
    await page.click(".more-menu summary");
    await page.click("#refresh-dashboard");
    await page.waitForFunction(() => !document.querySelector("#refresh-dashboard").disabled &&
      document.querySelector(".hero-card") && !document.querySelector('[aria-busy="true"]'));
    assert.deepEqual((await check()).issues, [], `after refresh at ${width}px`);
    assert.deepEqual(errors, [], `browser errors at ${width}px`);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.screenshot({ path: resolve(out, `mobile-${width}.png`), fullPage: true });
    console.log(`${width}px: layout, timeframe controls, More menu and refresh passed; no page errors`);
    await page.close();
  }
} finally {
  await browser.close();
}
