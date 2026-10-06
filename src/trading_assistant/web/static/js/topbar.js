/**
 * Single source of truth for the global header.
 *
 * Every route shows the same topbar, so every route must populate it from
 * the same backend facts: the dashboard payload (instrument identity and
 * latest closed candle) plus the forward status (runner/data health). The
 * dashboard view calls updateTopbar with payloads it already fetched; all
 * other routes go through refreshTopbar, which fetches the same two
 * payloads. Nothing else in the frontend writes these header nodes.
 */

import { api } from "./api.js";
import { displayPrice, formatUtc, isMissing } from "./format.js";
import { loadPrefs } from "./util.js";

export function integerOrNull(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function timeframeLabel(timeframe) {
  return isMissing(timeframe) ? "UNKNOWN" : String(timeframe).toUpperCase();
}

export function systemHealthViewModel(dashboard, forward) {
  const forwardStatus = forward?.status || {};
  const marketStatus = forwardStatus.market_data || {};
  const runner = forwardStatus.runner;
  const pending = integerOrNull(forwardStatus.sample?.pending_catch_up_boundaries) ??
    integerOrNull(runner?.pending_boundaries);
  const runnerHealthy = Boolean(
    runner &&
    ["STARTED", "PROCESSED", "IDLE"].includes(runner.status) &&
    !runner.last_error,
  );
  const currentData = dashboard?.freshness?.status === "CURRENT" &&
    dashboard?.market?.complete === true &&
    marketStatus.data_health === "CURRENT";
  const healthy = currentData && pending === 0 && runnerHealthy;
  return {
    healthy,
    label: healthy ? "SYSTEM OK" : "SYSTEM WARNING",
    tone: healthy ? "green" : "amber",
  };
}

export function setTopbarWarning() {
  if (typeof document === "undefined") return;
  const symbol = document.getElementById("topbar-symbol");
  if (symbol) symbol.textContent = "UNKNOWN";
  const timeframe = document.getElementById("topbar-timeframe");
  if (timeframe) timeframe.textContent = "UNKNOWN";
  const status = document.getElementById("topbar-status");
  if (status) {
    status.textContent = "SYSTEM WARNING";
    status.dataset.tone = "amber";
  }
  const time = document.getElementById("topbar-candle-time");
  if (time) {
    time.textContent = "UNKNOWN";
    time.removeAttribute("datetime");
  }
  const price = document.getElementById("topbar-price");
  if (price) price.textContent = "UNKNOWN";
}

export function updateTopbar(dashboard, forward) {
  if (typeof document === "undefined") return;
  const meta = dashboard?.meta || {};
  const latest = dashboard?.market?.latest_closed_candle || null;
  const symbol = document.getElementById("topbar-symbol");
  const timeframe = document.getElementById("topbar-timeframe");
  const time = document.getElementById("topbar-candle-time");
  const price = document.getElementById("topbar-price");
  const status = document.getElementById("topbar-status");

  if (symbol) symbol.textContent = meta.symbol || "UNKNOWN";
  if (timeframe) timeframe.textContent = timeframeLabel(meta.timeframe);
  if (time) {
    time.textContent = latest?.timestamp ? formatUtc(latest.timestamp) : "UNKNOWN";
    if (latest?.timestamp) time.setAttribute("datetime", latest.timestamp);
    else time.removeAttribute("datetime");
  }
  if (price) price.textContent = latest ? displayPrice(latest.close).display : "UNKNOWN";
  if (status) {
    const health = systemHealthViewModel(dashboard, forward);
    status.textContent = health.label;
    status.dataset.tone = health.tone;
    status.title = health.healthy
      ? "Stored closed-candle data is current; no forward catch-up is pending."
      : "One or more data/runner health checks are not current or are unavailable. Expand System details.";
  }
}

let topbarGeneration = 0;

/**
 * Populate the header on routes that do not fetch the dashboard payload
 * themselves. Same inputs and same updater as the dashboard view, so the
 * header can never disagree with the dashboard for the same backend state.
 * A stale overlapping refresh never overwrites a newer one.
 */
export async function refreshTopbar() {
  if (typeof document === "undefined") return;
  const generation = ++topbarGeneration;
  setTopbarWarning();
  const prefs = loadPrefs();
  const [dashboardResult, forwardResult] = await Promise.allSettled([
    api.dashboard({
      symbol: prefs.preferredSymbol || undefined,
      timeframe: prefs.preferredTimeframe || undefined,
    }),
    api.forward({ limit: 1 }),
  ]);
  if (generation !== topbarGeneration) return;
  try {
    if (dashboardResult.status === "rejected") {
      setTopbarWarning();
      return;
    }
    const dashboard = dashboardResult.value || {};
    const forward = forwardResult.status === "fulfilled" ? forwardResult.value : null;
    updateTopbar(dashboard, forward);
  } catch {
    setTopbarWarning();
  }
}
