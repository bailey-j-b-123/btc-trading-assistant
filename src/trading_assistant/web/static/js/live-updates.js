/**
 * Automatic dashboard refresh rules.
 *
 * Two different cadences, kept apart on purpose:
 *   1. A cheap boundary check (GET /api/market/latest-closed) every LATEST_POLL_MS. It reads
 *      one stored row and runs no analysis.
 *   2. The expensive dashboard snapshot, requested ONLY when the boundary the browser is showing
 *      differs from the boundary the server reports. Unchanged boundaries never rerun analysis.
 *
 * This module has no DOM, no chart library, and no exchange socket. The forming candle and the
 * live Binance quote are display-only and are never inputs here; nothing here feeds analysis,
 * qualification, planning, paper observations or recorded signals.
 */

export const LATEST_POLL_MS = 15_000;
export const LATEST_BACKOFF_MAX_MS = 60_000;
export const REFRESH_COOLDOWN_BASE_MS = 30_000;
export const REFRESH_COOLDOWN_MAX_MS = 300_000;

/** Delay before the next boundary check after `failures` consecutive failed checks. */
export function nextLatestDelay(failures) {
  if (!Number.isInteger(failures) || failures <= 0) return LATEST_POLL_MS;
  return Math.min(LATEST_BACKOFF_MAX_MS, LATEST_POLL_MS * 2 ** Math.min(failures, 3));
}

/** Cooldown after `failures` consecutive failed dashboard refreshes. Zero when none failed. */
export function refreshCooldown(failures) {
  if (!Number.isInteger(failures) || failures <= 0) return 0;
  return Math.min(REFRESH_COOLDOWN_MAX_MS, REFRESH_COOLDOWN_BASE_MS * 2 ** Math.min(failures - 1, 4));
}

function instantMs(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : null;
}

/**
 * True only when the server's default decision boundary differs from the one on screen.
 * A malformed or missing value never triggers a refresh (that would repeat the expensive call).
 */
export function boundaryChanged(latest, currentAsOf) {
  const next = instantMs(latest?.default_as_of);
  const current = instantMs(currentAsOf);
  if (next === null || current === null) return false;
  return next !== current;
}

function hhmm(ms) {
  return new Date(ms).toISOString().slice(11, 16);
}

function hhmmss(ms) {
  return new Date(ms).toISOString().slice(11, 19);
}

/**
 * Plain-English status for the stored chart, from the boundary check.
 *
 * `failed` means the most recent check did not succeed. The label is then UNAVAILABLE even if an
 * earlier check was CURRENT: the browser cannot know what the server holds now, so it must not
 * claim CURRENT.
 */
export function dataStatus(latest, { failed = false, lastSuccessMs = null, now = Date.now() } = {}) {
  if (failed || !latest || typeof latest !== "object") {
    const since = lastSuccessMs !== null
      ? ` The chart shows data loaded or checked at ${hhmmss(lastSuccessMs)} UTC.`
      : "";
    return {
      tone: "unavailable",
      label: "UNAVAILABLE",
      detail: `Cannot reach the dashboard server to check for new closed candles. Retrying automatically.${since}`,
    };
  }
  const freshness = latest.freshness && typeof latest.freshness === "object" ? latest.freshness : {};
  const checked = instantMs(latest.checked_at) ?? now;
  const latestStored = instantMs(latest.latest_stored_open);
  const expected = instantMs(latest.expected_latest_closed_open);
  const storedText = latestStored !== null ? `latest stored close opened ${hhmm(latestStored)} UTC` : "no stored candles";
  const checkedText = `checked ${hhmmss(checked)} UTC`;
  if (freshness.status === "CURRENT") {
    return {
      tone: "current",
      label: "CURRENT",
      detail: `The newest closed candle is stored (${storedText}; ${checkedText}). New closes appear here automatically.`,
    };
  }
  if (freshness.status === "STALE") {
    const behind = Number.isInteger(freshness.staleness_intervals) && freshness.staleness_intervals > 0
      ? `${freshness.staleness_intervals} closed candle${freshness.staleness_intervals === 1 ? " is" : "s are"} not stored yet`
      : "closed candles are not stored yet";
    const expectedText = expected !== null ? ` (expected close opened ${hhmm(expected)} UTC)` : "";
    return {
      tone: "stale",
      label: "STALE",
      detail: `${behind}${expectedText}; ${storedText}; ${checkedText}. Nothing is filled in or invented.`,
    };
  }
  return {
    tone: "unknown",
    label: "UNKNOWN",
    detail: `No freshness verdict is available (${freshness.reason || "unknown reason"}); ${storedText}; ${checkedText}.`,
  };
}

function validRow(row) {
  if (!Array.isArray(row) || row.length < 5) return null;
  const ms = typeof row[0] === "number" ? row[0] : instantMs(row[0]);
  return Number.isFinite(ms) && ms > 0 ? ms : null;
}

/**
 * Combine the rows already on screen (including older pages the user scrolled back to) with a
 * newer snapshot. One row per open time; the incoming row wins on the same open time. Invalid
 * rows are dropped. The result is ascending. `addedCount` counts open times not already present.
 */
export function mergeCandleRows(existingRows, incomingRows) {
  const byTime = new Map();
  for (const row of Array.isArray(existingRows) ? existingRows : []) {
    const ms = validRow(row);
    if (ms !== null) byTime.set(ms, row);
  }
  const before = new Set(byTime.keys());
  let addedCount = 0;
  for (const row of Array.isArray(incomingRows) ? incomingRows : []) {
    const ms = validRow(row);
    if (ms === null) continue;
    if (!before.has(ms) && !byTime.has(ms)) addedCount += 1;
    byTime.set(ms, row);
  }
  const rows = [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, row]) => row);
  return { rows, addedCount };
}

/**
 * Status while a newer boundary is stored but the page has not caught up yet. It must never say
 * CURRENT: the chart on screen is behind the server.
 */
export function pendingStatus({ blocked, refreshCooldownActive, refreshError = null }) {
  if (blocked) {
    return {
      tone: "paused",
      label: "PAUSED",
      detail: "A newer closed candle is stored. The page update is paused while a dialog is open or this tab is hidden, and it runs when you return.",
    };
  }
  if (refreshCooldownActive) {
    const reason = refreshError ? ` (${refreshError})` : "";
    return {
      tone: "unavailable",
      label: "UNAVAILABLE",
      detail: `The analysis refresh for the newest closed candle failed${reason}. The page still shows its last good data. Retrying automatically.`,
    };
  }
  return {
    tone: "updating",
    label: "UPDATING",
    detail: "A newer closed candle is stored. Refreshing the analysis now…",
  };
}

/**
 * Poll controller. Dependencies are injected so the schedule and the network are testable.
 *
 * - `fetchLatest()` resolves to the boundary-check payload (cheap).
 * - `getCurrentAsOf()` returns the boundary on screen.
 * - `refreshDashboard(latest)` requests and applies the expensive snapshot. It resolves when the
 *   page is updated and rejects when the request failed; the page keeps its last good content.
 * - `isBlocked()` pauses refreshes (open dialog, hidden tab). Checks continue; refreshes wait.
 * - `onStatus(model)` receives status models after every check and refresh attempt.
 */
export function createDashboardUpdater({
  fetchLatest,
  getCurrentAsOf,
  refreshDashboard,
  onStatus = () => {},
  isBlocked = () => false,
  now = () => Date.now(),
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (handle) => clearTimeout(handle),
}) {
  let timer = null;
  let stopped = true;
  let checking = false;
  let refreshing = false;
  let checkFailures = 0;
  let refreshFailures = 0;
  let refreshCooldownUntil = 0;
  let refreshError = null;
  let lastSuccessMs = null;

  function emit(model) {
    try {
      onStatus(model);
    } catch {
      // A status paint failure must never stop the update loop.
    }
  }

  function scheduleNext(delay) {
    if (stopped) return;
    if (timer !== null) cancel(timer);
    timer = schedule(() => {
      timer = null;
      void check();
    }, delay);
  }

  async function maybeRefresh(latest) {
    if (refreshing || stopped || !boundaryChanged(latest, getCurrentAsOf())) return;
    if (isBlocked()) return;
    if (now() < refreshCooldownUntil) return;
    refreshing = true;
    emit(pendingStatus({ blocked: false, refreshCooldownActive: false }));
    try {
      await refreshDashboard(latest);
      refreshFailures = 0;
      refreshCooldownUntil = 0;
      refreshError = null;
    } catch (error) {
      refreshFailures += 1;
      refreshCooldownUntil = now() + refreshCooldown(refreshFailures);
      refreshError = typeof error?.message === "string" && error.message ? error.message : "request failed";
      emit(pendingStatus({ blocked: false, refreshCooldownActive: true, refreshError }));
    } finally {
      refreshing = false;
    }
  }

  async function check() {
    if (stopped || checking) return;
    checking = true;
    try {
      const latest = await fetchLatest();
      checkFailures = 0;
      lastSuccessMs = now();
      if (boundaryChanged(latest, getCurrentAsOf())) {
        emit(pendingStatus({
          blocked: isBlocked(),
          refreshCooldownActive: now() < refreshCooldownUntil,
          refreshError,
        }));
      } else {
        emit(dataStatus(latest, { lastSuccessMs, now: lastSuccessMs }));
      }
      checking = false;
      await maybeRefresh(latest);
    } catch {
      checkFailures += 1;
      emit(dataStatus(null, { failed: true, lastSuccessMs }));
    } finally {
      checking = false;
      scheduleNext(nextLatestDelay(checkFailures));
    }
  }

  return {
    start() {
      if (!stopped) return;
      stopped = false;
      void check();
    },
    /** Check now (tab became visible, user asked). A check already running is not duplicated. */
    checkNow() {
      if (stopped || checking) return;
      if (timer !== null) cancel(timer);
      timer = null;
      void check();
    },
    stop() {
      stopped = true;
      if (timer !== null) cancel(timer);
      timer = null;
    },
    /** Diagnostics and tests only. */
    state() {
      return { checkFailures, refreshFailures, refreshing, lastSuccessMs, stopped };
    },
  };
}
