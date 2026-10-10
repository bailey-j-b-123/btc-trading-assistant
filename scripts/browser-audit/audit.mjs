#!/usr/bin/env node
/**
 * Browser audit for the BRAIN dashboard: one page load per run, readiness-based waits, and an
 * explicit timeout. It does not sleep for a fixed time.
 *
 * It records, for every /api/ request, the HTTP status and the wall-clock duration, plus any
 * request that the browser failed to complete. That makes a "Something went wrong" state
 * traceable to the exact call that failed.
 *
 * Usage:
 *   node audit.mjs --url http://127.0.0.1:8040/ --out ./shots/desktop --width 1440 --height 900
 *   node audit.mjs --url ... --out ./shots/desktop --width 1440 --height 900 --clip .terminal-chart-card
 *
 * Options:
 *   --timeout SECONDS   maximum wait for the dashboard to become ready (default 240)
 *   --clip SELECTOR     also write <out>-clip.png for this element
 *   --no-full           skip the full-page screenshot
 *   --fail-dashboard    test hook: abort /api/dashboard so the app shows its error state
 *
 * Browser: set CHROME_PATH to a local Chrome/Chromium (for example on macOS:
 * /Applications/Google Chrome.app/Contents/MacOS/Google Chrome). Without it, the
 * @sparticuz/chromium package is used (sandbox fallback; needs LD_LIBRARY_PATH if it ships libs).
 *
 * Exit codes: 0 ready and no overflow or page errors; 1 otherwise. The JSON report is printed.
 */

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import puppeteer from "puppeteer-core";

function parseArgs(argv) {
  const args = { timeout: 240, full: true, clip: null, url: null, out: null, width: 1440, height: 900, failDashboard: false };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => argv[(i += 1)];
    if (key === "--url") args.url = next();
    else if (key === "--out") args.out = next();
    else if (key === "--width") args.width = Number(next());
    else if (key === "--height") args.height = Number(next());
    else if (key === "--timeout") args.timeout = Number(next());
    else if (key === "--clip") args.clip = next();
    else if (key === "--no-full") args.full = false;
    else if (key === "--fail-dashboard") args.failDashboard = true;
    else throw new Error(`unknown option ${key}`);
  }
  if (!args.url || !args.out) throw new Error("--url and --out are required");
  if (!Number.isFinite(args.timeout) || args.timeout <= 0) throw new Error("--timeout must be positive");
  return args;
}

async function launchBrowser(width, height) {
  const options = {
    headless: true,
    defaultViewport: { width, height, deviceScaleFactor: 1 },
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
  return puppeteer.launch(options);
}

// Waits for the dashboard's own state, not a clock. Resolves to "ready", "error" or rejects on timeout.
async function waitForDashboardState(page, timeoutMs) {
  const handle = await page.waitForFunction(
    () => {
      const failure = document.querySelector(".dashboard-failure");
      if (failure) {
        return { state: "error", message: (failure.textContent || "").slice(0, 300) };
      }
      const busy = document.querySelector('[aria-busy="true"]');
      if (document.querySelector(".hero-card") && !busy) return { state: "ready" };
      return false;
    },
    { timeout: timeoutMs, polling: 500 },
  );
  return handle.jsonValue();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  mkdirSync(dirname(args.out + "-x"), { recursive: true });
  const started = Date.now();
  const browser = await launchBrowser(args.width, args.height);
  const page = await browser.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const apiCalls = new Map();
  const failedRequests = [];

  if (args.failDashboard) {
    await page.setRequestInterception(true);
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/dashboard") request.abort("failed");
      else request.continue();
    });
  }
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (/Content Security Policy|WebSocket|binance|net::ERR/i.test(text)) return;
    consoleErrors.push(text.slice(0, 200));
  });
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) apiCalls.set(request, { path, startedAt: Date.now() });
  });
  page.on("response", (response) => {
    const entry = apiCalls.get(response.request());
    if (!entry) return;
    entry.status = response.status();
    entry.durationMs = Date.now() - entry.startedAt;
  });
  page.on("requestfailed", (request) => {
    const entry = apiCalls.get(request);
    if (entry) {
      entry.failed = request.failure()?.errorText || "failed";
      entry.durationMs = Date.now() - entry.startedAt;
    }
  });

  const timeoutMs = args.timeout * 1000;
  await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: timeoutMs });

  let outcome;
  try {
    outcome = await waitForDashboardState(page, timeoutMs);
  } catch (error) {
    outcome = { state: "timeout", message: `no dashboard state within ${args.timeout}s` };
  }

  if (outcome.state === "ready") {
    await page.waitForSelector(".terminal-chart-card canvas", { timeout: 30000 }).catch(() => null);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }

  const layout = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const offenders = [];
    for (const element of document.querySelectorAll("body *")) {
      const rect = element.getBoundingClientRect();
      if (rect.width === 0) continue;
      if (rect.right > vw + 0.5 && getComputedStyle(element).position !== "fixed") {
        const cls = String(element.className).split(" ").slice(0, 2).join(".");
        offenders.push(`${element.tagName.toLowerCase()}.${cls} right=${Math.round(rect.right)}`);
      }
    }
    return {
      viewportWidth: vw,
      overflowPx: document.documentElement.scrollWidth - vw,
      offenders: offenders.slice(0, 8),
    };
  });

  const screenshots = [];
  if (args.full) {
    await page.screenshot({ path: `${args.out}-full.png`, fullPage: true });
    screenshots.push(`${args.out}-full.png`);
  }
  if (args.clip) {
    const element = await page.$(args.clip);
    if (element) {
      await element.screenshot({ path: `${args.out}-clip.png` });
      screenshots.push(`${args.out}-clip.png`);
    }
  }

  const report = {
    url: args.url,
    viewport: { width: args.width, height: args.height },
    outcome,
    elapsedSeconds: Math.round((Date.now() - started) / 100) / 10,
    layout,
    apiCalls: [...apiCalls.values()].map(({ path, status, durationMs, failed }) => ({ path, status, durationMs, failed })),
    pageErrors: pageErrors.slice(0, 6),
    consoleErrors: consoleErrors.slice(0, 6),
    screenshots,
  };
  console.log(JSON.stringify(report, null, 2));
  await browser.close();

  const pass = outcome.state === "ready" && layout.overflowPx === 0 && pageErrors.length === 0 && consoleErrors.length === 0;
  process.exit(pass ? 0 : 1);
}

main().catch((error) => {
  console.error(`audit failed: ${error.message}`);
  process.exit(1);
});
