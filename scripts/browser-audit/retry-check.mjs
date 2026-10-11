#!/usr/bin/env node
/**
 * Retry check: the first /api/dashboard request is aborted, the page must show the specific failure
 * with a Retry button, and clicking Retry must load the dashboard. Usage:
 *   node retry-check.mjs http://127.0.0.1:8041/ /path/to/out-prefix
 * Honours CHROME_PATH like audit.mjs. Exit 0 only when the retry recovers the dashboard.
 */
import puppeteer from "puppeteer-core";

const [url, outPrefix = "retry-check"] = process.argv.slice(2);
if (!url) {
  console.error("usage: node retry-check.mjs <url> [out-prefix]");
  process.exit(1);
}
const options = {
  headless: true,
  defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
  env: { ...process.env, LANG: process.env.LANG || "en_GB.UTF-8", LC_ALL: process.env.LC_ALL || "en_GB.UTF-8" },
};
if (process.env.CHROME_PATH) {
  options.executablePath = process.env.CHROME_PATH;
  options.args = ["--no-sandbox"];
} else {
  const chromium = (await import("@sparticuz/chromium")).default;
  options.executablePath = await chromium.executablePath();
  options.args = chromium.args;
}
const browser = await puppeteer.launch(options);
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (error) => pageErrors.push(error.message));
let failNext = true;
await page.setRequestInterception(true);
page.on("request", (request) => {
  if (new URL(request.url()).pathname === "/api/dashboard" && failNext) {
    failNext = false;
    request.abort("failed");
  } else {
    request.continue();
  }
});
await page.goto(url, { waitUntil: "domcontentloaded", timeout: 180000 });
await page.waitForSelector(".dashboard-failure button", { timeout: 60000 });
const failureText = await page.$eval(".dashboard-failure", (node) => node.innerText.split("\n")[0]);
await page.screenshot({ path: `${outPrefix}-failure.png` });
await page.click(".dashboard-failure button");
await page.waitForSelector(".hero-card", { timeout: 240000 });
const recovered = (await page.$(".dashboard-failure")) === null;
await page.screenshot({ path: `${outPrefix}-recovered.png` });
const report = { failureTitle: failureText, recovered, pageErrors: pageErrors.slice(0, 5) };
console.log(JSON.stringify(report, null, 2));
await browser.close();
process.exit(recovered && pageErrors.length === 0 ? 0 : 1);
