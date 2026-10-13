/**
 * Scoped, read-only forward-ledger poll controller for the dashboard.
 *
 * The dashboard's expensive analysis refreshes only when a new closed candle is
 * stored. Everything the forward runner records afterwards (outcome versions,
 * a runner that has STOPPED or gone quiet, newly settled plans) changes WITHOUT a
 * new candle, so the header, tracker and paper results need their own cadence.
 * This controller owns that cadence. It knows nothing about the DOM: the
 * dashboard decides what to repaint from each accepted payload.
 *
 * Policy:
 * - one read in flight at a time; a slow read is never duplicated;
 * - the existing tracker cadence (15 s), doubling on failures up to 60 s;
 * - blocked ticks (hidden tab, open dialog) make no request and keep the loop;
 * - `rebase()` is called after every full render: a read that started before it
 *   is discarded when it returns, and it never counts as a failure;
 * - `stop()` (navigation) cancels the timer and discards any late response.
 *
 * A response that arrives after its generation was superseded is dropped without
 * touching the page. A read that fails, or whose payload cannot be applied, is
 * reported through `applyFailure` so the page can say the status is unavailable.
 */

import { nextTrackerDelay } from "./paper-tracker.js";

export function createForwardRefresher({
  fetchForward,
  applyForward,
  applyFailure,
  isBlocked = () => false,
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (handle) => clearTimeout(handle),
} = {}) {
  if (typeof fetchForward !== "function" || typeof applyForward !== "function" || typeof applyFailure !== "function") {
    throw new Error("createForwardRefresher requires fetchForward, applyForward and applyFailure callbacks");
  }
  let timer = null;
  let stopped = true;
  let inFlight = false;
  let failures = 0;
  let epoch = 0;

  function scheduleNext() {
    if (stopped) return;
    if (timer !== null) cancel(timer);
    timer = schedule(() => {
      timer = null;
      void checkNow();
    }, nextTrackerDelay(failures));
  }

  function checkNow() {
    if (stopped) return Promise.resolve(false);
    // Blocked or still busy: skip this tick but keep the loop alive so polls
    // resume as soon as the tab is visible and no dialog is open.
    if (inFlight || isBlocked()) {
      scheduleNext();
      return Promise.resolve(false);
    }
    inFlight = true;
    const started = epoch;
    let request;
    try {
      request = Promise.resolve(fetchForward());
    } catch (error) {
      request = Promise.reject(error);
    }
    return request
      .then((payload) => {
        if (stopped || started !== epoch) return false;
        applyForward(payload);
        failures = 0;
        return true;
      })
      .catch((error) => {
        if (stopped || started !== epoch) return false;
        failures += 1;
        applyFailure({ failures, error });
        return false;
      })
      .finally(() => {
        inFlight = false;
        scheduleNext();
      });
  }

  return {
    /** Begin the cadence; the first read happens one interval from now. */
    start() {
      stopped = false;
      failures = 0;
      scheduleNext();
    },
    /** A full render replaced the page: discard any read that started before it. */
    rebase() {
      epoch += 1;
      failures = 0;
      scheduleNext();
    },
    /** Read now unless a read is already in flight or polls are blocked. */
    checkNow,
    stop() {
      stopped = true;
      if (timer !== null) cancel(timer);
      timer = null;
    },
    /** Diagnostics and tests only. */
    state() {
      return { stopped, inFlight, failures, epoch, hasPendingTimer: timer !== null };
    },
  };
}
