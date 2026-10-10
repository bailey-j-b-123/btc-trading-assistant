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
import { displayRounded, formatUtc, isMissing } from "./format.js";
import { runnerStatusLabel } from "./plain.js";
import { loadPrefs } from "./util.js";

export { runnerStatusLabel };

export function integerOrNull(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function timeframeLabel(timeframe) {
  return isMissing(timeframe) ? "UNKNOWN" : String(timeframe).toUpperCase();
}

/**
 * Runner presence and detail labels with distinct honest states.
 *
 * - "unavailable": no forward status payload (fetch failed or malformed);
 *   every runner field stays UNKNOWN.
 * - "never-run": the backend reports runner:null, i.e. no heartbeat row was
 *   ever recorded. Pending catch-up may still be a real number: it counts
 *   stored closed candles no cycle has processed yet.
 * - "reported": a heartbeat row exists; state is its plain-English label while
 *   detail/heartbeat/error come from it verbatim. Heartbeat age is the
 *   server-computed heartbeat_age_seconds (recorded_at vs the backend
 *   clock), never the browser clock.
 */
export function runnerDetailsViewModel(forward) {
  const status = forward?.status;
  if (!status || typeof status !== "object" || !("runner" in status)) {
    return {
      presence: "unavailable",
      state: "UNKNOWN",
      detail: "UNKNOWN",
      pending: "UNKNOWN",
      latestCycle: "UNKNOWN",
      heartbeat: "UNKNOWN",
      lastError: "UNKNOWN",
    };
  }
  const pending = integerOrNull(status.sample?.pending_catch_up_boundaries) ??
    integerOrNull(status.runner?.pending_boundaries);
  const pendingText = pending === null
    ? "UNKNOWN"
    : pending === 1
      ? "1 closed candle not yet processed"
      : `${pending} closed candles not yet processed`;
  const runner = status.runner || null;
  if (!runner) {
    return {
      presence: "never-run",
      state: "never run",
      detail: "not applicable (the runner has never run)",
      pending: pendingText,
      latestCycle: "none recorded yet",
      heartbeat: "never run",
      lastError: "not applicable (the runner has never run)",
    };
  }
  const age = integerOrNull(runner.heartbeat_age_seconds);
  const heartbeatTime = runner.recorded_at ? formatUtc(runner.recorded_at) : null;
  return {
    presence: "reported",
    state: runnerStatusLabel(runner.status),
    detail: runner.detail || "no detail recorded",
    pending: pendingText,
    latestCycle: runner.latest_cycle_as_of ? formatUtc(runner.latest_cycle_as_of) : "none recorded yet",
    heartbeat: heartbeatTime
      ? (age === null ? heartbeatTime : `${heartbeatTime} (${age}s ago)`)
      : "UNKNOWN",
    lastError: runner.last_error || "No errors reported",
  };
}

/** Plain-language reasons for the freshness codes the backend returns (web/freshness.py). */
const FRESHNESS_REASON_TEXT = {
  as_of_is_not_the_current_boundary: "The displayed instant is not the newest closed-candle boundary.",
  no_stored_candles: "No closed candles are stored for this timeframe. Run the Binance ingestion process to fetch them.",
  stored_candle_after_expected_boundary: "A stored candle is newer than the expected newest closed candle, so the series cannot be confirmed current.",
};

/** UTC wall-clock label from an ISO timestamp, e.g. "2026-10-10 17:00 UTC". */
function utcLabel(iso) {
  if (typeof iso !== "string" || iso.length < 16) return String(iso ?? "unknown");
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
}

/**
 * Specific freshness detail: names the stored end, the newest closed candle that should be stored,
 * and how far behind the clock the series is. Never a generic "not confirmed current".
 */
function freshnessDetailText(freshness) {
  if (freshness.reason === "stored_candles_stop_before_expected_boundary" && freshness.latest_stored && freshness.expected_latest_closed) {
    const n = Number(freshness.staleness_intervals);
    const behind = Number.isFinite(n) && n > 0 ? ` ${n} candle${n === 1 ? "" : "s"} behind the clock.` : "";
    return `Stored closed candles end at ${utcLabel(freshness.latest_stored)}; the newest closed candle that should be stored is ${utcLabel(freshness.expected_latest_closed)}.${behind} New closed candles are not being stored: check the Binance ingestion process is running.`;
  }
  const reason = freshness.reason;
  if (!reason) return "Stored closed candles are not confirmed current.";
  return FRESHNESS_REASON_TEXT[reason] || String(reason).replaceAll("_", " ");
}

/**
 * Health verdict as a list of specific failing conditions, each with the component it concerns.
 *
 * Checks (all must hold for "current"): dashboard freshness CURRENT; stored window complete;
 * forward data_health CURRENT; no pending catch-up; runner present, STARTED/PROCESSED/IDLE and
 * without last_error. The top bar shows the most severe condition by name instead of a generic
 * warning. A healthy result states what was checked, never a generic all-clear.
 *
 * Severity: 0 = processing failure or missing payload (red), 1 = stale, missing, stopped or
 * incomplete data (amber), 2 = catch-up backlog (amber). Only the NEWEST heartbeat feeds the runner
 * checks, so a stale error in the trail cannot persist once the next pass overwrites it.
 */
export function systemHealthViewModel(dashboard, forward) {
  const forwardStatus = forward?.status || {};
  const marketStatus = forwardStatus.market_data || {};
  const runner = forwardStatus.runner;
  const pending = integerOrNull(forwardStatus.sample?.pending_catch_up_boundaries) ??
    integerOrNull(runner?.pending_boundaries);
  const issues = [];
  if (!dashboard) {
    issues.push({ severity: 0, component: "Dashboard data", condition: "Not loaded",
      detail: "The dashboard request has not returned, so no health verdict is shown." });
  }
  if (!runner) {
    issues.push({ severity: 1, component: "Forward runner", condition: "Never reported",
      detail: "The forward runner has not recorded a heartbeat, so paper observations are not being advanced." });
  } else if (runner.last_error) {
    issues.push({ severity: 0, component: "Forward runner", condition: "Processing failure",
      detail: `Last error: ${runner.last_error}` });
  } else if (!["STARTED", "PROCESSED", "IDLE"].includes(runner.status)) {
    issues.push({ severity: 1, component: "Forward runner", condition: `Status ${runner.status || "UNKNOWN"}`,
      detail: runner.detail || "The runner reported a status that is not a running state." });
  }
  if (pending === null) {
    issues.push({ severity: 2, component: "Forward runner", condition: "Catch-up unknown",
      detail: "The number of closed candles still to process is not reported." });
  } else if (pending > 0) {
    issues.push({ severity: 2, component: "Forward runner", condition: "Catch-up pending",
      detail: `${pending} closed candle${pending === 1 ? "" : "s"} stored but not yet processed.` });
  }
  if (dashboard) {
    const freshness = dashboard.freshness || {};
    if (freshness.status !== "CURRENT") {
      issues.push({ severity: 1, component: "Market data", condition: `Freshness ${freshness.status || "UNKNOWN"}`,
        detail: freshnessDetailText(freshness) });
    }
    if (dashboard.market?.complete !== true) {
      issues.push({ severity: 1, component: "Market data", condition: "Stored window incomplete",
        detail: "The stored candle window has gaps, so some structure may be missing." });
    }
  }
  if (marketStatus.data_health !== "CURRENT") {
    issues.push({ severity: 1, component: "Forward data health", condition: `Data health ${marketStatus.data_health || "UNKNOWN"}`,
      detail: "The forward side's own freshness check does not confirm current data." });
  }
  issues.sort((a, b) => a.severity - b.severity);
  const healthy = issues.length === 0;
  const primary = issues[0] || null;
  const runnerState = runner?.status ? runner.status : "not reported";
  const label = primary
    ? `${primary.component.toUpperCase()} · ${primary.condition.toUpperCase()}`
    : `DATA CURRENT · RUNNER ${String(runnerState).toUpperCase()}`;
  return {
    healthy,
    label,
    tone: healthy ? "green" : primary.severity === 0 ? "red" : "amber",
    primary,
    issues,
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
    status.textContent = "DASHBOARD UNAVAILABLE";
    status.dataset.tone = "amber";
    status.title = "The dashboard request has not returned, so no health verdict is shown. Nothing is assumed current.";
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
  if (price) {
    if (latest && !isMissing(latest.close)) {
      const { display, raw } = displayRounded(latest.close, 2);
      price.textContent = display;
      if (raw !== null) price.title = `Exact close: ${raw}`;
      else price.removeAttribute("title");
    } else {
      price.textContent = "UNKNOWN";
      price.removeAttribute("title");
    }
  }
  if (status) {
    const health = systemHealthViewModel(dashboard, forward);
    status.textContent = health.label;
    status.dataset.tone = health.tone;
    status.title = health.healthy
      ? "Stored closed-candle data is current, the forward runner is running and no catch-up is pending."
      : `${health.primary.detail} Expand System details for every check.`;
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
