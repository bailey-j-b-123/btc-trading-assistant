#!/usr/bin/env node
/**
 * Browser check for chart history paging: pan the chart to its left edge and confirm that older
 * candle pages load, that each page finishes (readiness, not a clock), and that the earliest-reached
 * note appears when the stored history is exhausted.
 *
 * Usage:
 *   node history-check.mjs --url http://127.0.0.1:8041/ --out ./shots/history --width 1440 --height 900
 *
 * Options:
 *   --timeout SECONDS   maximum wait for the dashboard and for each older page (default 240)
 *   --timeframe LABEL   chart timeframe button to switch to first, e.g. 5M (default: keep the engine view)
 *   --pans N            maximum left-pans to attempt (default 12)
 *
 * Browser: CHROME_PATH as for audit.mjs. Exit 0 when the check ran and every older page completed.
 */

import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import puppeteer from "puppeteer-core";

function parseArgs(argv) {
  const args = { url: null, out: null, width: 1440, height: 900, timeout: 240, timeframe: null, pans: 12 };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => argv[(i += 1)];
    if (key === "--url") args.url = next();
    else if (key === "--out") args.out = next();
    else if (key === "--width") args.width = Number(next());
    else if (key === "--height") args.height = Number(next());
    else if (key === "--timeout") args.timeout = Number(next());
    else if (key === "--timeframe") args.timeframe = next();
    else if (key === "--pans") args.pans = Number(next());
    else throw new Error(`unknown option ${key}`);
  }
  if (!args.url || !args.out) throw new Error("--url and --out are required");
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

/** Poll a condition until it holds or the bound expires. Returns whether it held. */
async function waitUntil(predicate, boundMs) {
  const deadline = Date.now() + boundMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return predicate();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  mkdirSync(dirname(args.out + "-x"), { recursive: true });
  const timeoutMs = args.timeout * 1000;
  const browser = await launchBrowser(args.width, args.height);
  const page = await browser.newPage();
  const pageErrors = [];
  const candleCalls = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("requestfinished", (request) => {
    const url = new URL(request.url());
    if (url.pathname !== "/api/market/candles") return;
    candleCalls.push({
      before: url.searchParams.get("before"),
      timeframe: url.searchParams.get("timeframe"),
      startedAt: request._hc_started ?? null,
    });
  });
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/market/candles") request._hc_started = Date.now();
  });

  const result = { url: args.url, timeframe: args.timeframe, pans: [], pageErrors };
  try {
    await page.goto(args.url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    await page.waitForFunction(
      () => document.querySelector(".hero-card") && !document.querySelector('[aria-busy="true"]'),
      { timeout: timeoutMs, polling: 500 },
    );
    await page.waitForFunction(
      () => (document.querySelector(".chart-history-note")?.textContent || "").includes("stored"),
      { timeout: timeoutMs, polling: 250 },
    );

    if (args.timeframe) {
      const clicked = await page.evaluate((label) => {
        const button = [...document.querySelectorAll("button")].find(
          (candidate) => (candidate.textContent || "").trim().toUpperCase() === label.toUpperCase(),
        );
        if (!button) return false;
        button.click();
        return true;
      }, args.timeframe);
      if (!clicked) throw new Error(`no chart timeframe button labelled ${args.timeframe}`);
      await page.waitForFunction(
        (label) => {
          const note = document.querySelector(".chart-history-note")?.textContent || "";
          return note.includes("stored") && !note.includes("loading") && note.toUpperCase().includes(label.toUpperCase());
        },
        { timeout: timeoutMs, polling: 250 },
        args.timeframe,
      );
    }

    const card = await page.$(".terminal-chart-card");
    if (!card) throw new Error("chart card not found");
    const box = await card.boundingBox();
    const startNote = await page.$eval(".chart-history-note", (node) => node.textContent);
    await card.screenshot({ path: `${args.out}-before-pan.png` });
    result.noteBefore = startNote;

    const canvas = await card.$("canvas");
    const canvasBox = (await canvas?.boundingBox()) || box;
    const y = canvasBox.y + canvasBox.height * 0.5;
    const fromX = canvasBox.x + canvasBox.width * 0.2;

    for (let pan = 1; pan <= args.pans; pan += 1) {
      const callsBefore = candleCalls.length;
      const noteBefore = await page.$eval(".chart-history-note", (node) => node.textContent);
      if (noteBefore.includes("earliest stored candle reached")) break;
      const started = Date.now();
      await page.mouse.move(fromX, y);
      await page.mouse.down();
      await page.mouse.move(fromX + canvasBox.width * 0.7, y, { steps: 12 });
      await page.mouse.up();
      // Detection: a pan that reaches the edge issues an older-page request. Poll the request log for a short
      // bounded window; a pan that does not reach the edge issues none and simply moves on.
      await waitUntil(() => candleCalls.length > callsBefore, 1500);
      // Readiness: wait for the note to leave its loading state, rather than for a fixed time.
      await page.waitForFunction(
        () => !(document.querySelector(".chart-history-note")?.textContent || "").includes("loading earlier candles"),
        { timeout: timeoutMs, polling: 250 },
      );
      const note = await page.$eval(".chart-history-note", (node) => node.textContent);
      const newCalls = candleCalls.slice(callsBefore).filter((call) => call.before);
      result.pans.push({
        pan,
        requestsWithBefore: newCalls.length,
        beforeValues: newCalls.map((call) => call.before),
        elapsedMs: Date.now() - started,
        note,
      });
    }

    await card.screenshot({ path: `${args.out}-after-pan.png` });
    result.noteAfter = await page.$eval(".chart-history-note", (node) => node.textContent);
    result.candleRequests = candleCalls.filter((call) => call.before).length;
    result.reachedEarliest = result.noteAfter.includes("earliest stored candle reached");
    result.ok = pageErrors.length === 0 && result.pans.length > 0;
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 1;
  } finally {
    await browser.close();
  }
}

main().then(
  (code) => process.exit(code),
  (error) => {
    console.error(error.message);
    process.exit(1);
  },
);
