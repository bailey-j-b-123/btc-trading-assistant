/**
 * Persistent Active/Recent paper-trade tracker.
 *
 * Every displayed value comes from the forward ledger payload
 * (`/api/forward` → `paper_tracker`): recorded paper-plan levels, recorded
 * outcome versions, and the backend's own settled/unresolved verdicts. This
 * module never reads a live quote, never derives a level, and never decides
 * an outcome — it mirrors the ledger.
 *
 * Persistence is the point: a recorded plan stays on the tracker whatever the
 * newest assessment says. When the latest close concluded "No trade", the
 * already-recorded plan is still shown next to that assessment instead of
 * being hidden, and the newest outcome version is shown with its timestamp
 * without overwriting the older versions (the count stays visible).
 */

import {
  directionArrow,
  directionLabel,
  displayOrUnknown,
  displayRounded,
  familyLabel,
  formatUtc,
  isMissing,
  shortId,
} from "./format.js";
import { outcomeStatusText, setupStateLabel } from "./plain.js";
import { el, emptyState } from "./util.js";

const UNKNOWN_TEXT = "UNKNOWN";
const booleanOrNull = value => typeof value === "boolean" ? value : null;

/** Tracker poll cadence. Cheap ledger read; never triggers analysis. */
export const TRACKER_REFRESH_INTERVAL_MS = 15_000;
export const TRACKER_REFRESH_MAX_INTERVAL_MS = 60_000;

/** Delay before the next tracker poll after `failures` consecutive failures. */
export function nextTrackerDelay(failures) {
  if (!Number.isInteger(failures) || failures <= 0) return TRACKER_REFRESH_INTERVAL_MS;
  return Math.min(
    TRACKER_REFRESH_MAX_INTERVAL_MS,
    TRACKER_REFRESH_INTERVAL_MS * 2 ** Math.min(failures, 3),
  );
}

/**
 * Stable signature of the ledger facts the tracker displays: recorded plan
 * count, active/unresolved counts, and per-plan outcome version identity.
 * A new or updated outcome version changes the signature; unchanged ledger
 * data never re-renders the card.
 */
export function trackerLedgerSignature(tracker) {
  if (!tracker || typeof tracker !== "object") return null;
  const plans = Array.isArray(tracker.tracked_plans) ? tracker.tracked_plans : [];
  return JSON.stringify({
    recorded: Number.isInteger(tracker.recorded_paper_plan_count) ? tracker.recorded_paper_plan_count : null,
    unresolved: Number.isInteger(tracker.unresolved_paper_plan_count) ? tracker.unresolved_paper_plan_count : null,
    active: tracker.active_paper_plan_id || null,
    asOf: tracker.as_of || null,
    plans: plans.map((plan) => [
      plan?.paper_plan_id || null,
      booleanOrNull(plan?.settled),
      booleanOrNull(plan?.occupies_slot),
      Number.isInteger(plan?.outcome_version_count) ? plan.outcome_version_count : null,
      plan?.outcome
        ? [
            plan.outcome.outcome_id || null,
            plan.outcome.sequence ?? null,
            plan.outcome.status || null,
            plan.outcome.recorded_at || null,
          ]
        : null,
      plan?.signal_provenance?.four_timeframe_confirmation
        ? [
            plan.signal_provenance.four_timeframe_confirmation.status || null,
            plan.signal_provenance.four_timeframe_confirmation.reason || null,
            plan.signal_provenance.four_timeframe_confirmation.observation_id || null,
            plan.signal_provenance.four_timeframe_confirmation.recorded_at || null,
          ]
        : null,
    ]),
  });
}

const SIGNAL_STATUS_LABELS = {
  confirmed: "FOUR-TIMEFRAME CONFIRMED",
  not_confirmed: "NOT FOUR-TIMEFRAME CONFIRMED",
  unavailable: "CONFIRMATION UNAVAILABLE",
  incomplete: "HIERARCHY EVIDENCE INCOMPLETE",
  stale: "HIERARCHY EVIDENCE STALE",
  conflicting: "HIERARCHY EVIDENCE CONFLICTING",
  not_applicable: "NOT APPLICABLE",
};

/**
 * Presentation-only projection of the backend's immutable-record join.
 * A `plannable` live hierarchy payload is deliberately not a substitute.
 */
export function signalProvenanceViewModel(provenance, { timeframe = null } = {}) {
  const candidate = provenance?.paper_candidate && typeof provenance.paper_candidate === "object"
    ? provenance.paper_candidate
    : null;
  const confirmation = provenance?.four_timeframe_confirmation && typeof provenance.four_timeframe_confirmation === "object"
    ? provenance.four_timeframe_confirmation
    : null;
  let status = Object.hasOwn(SIGNAL_STATUS_LABELS, confirmation?.status)
    ? confirmation.status
    : "unavailable";
  const sameBoundary = typeof candidate?.plan_as_of === "string" &&
    candidate.plan_as_of === confirmation?.decision_time;
  const matchingRecord = Array.isArray(confirmation?.records) &&
    confirmation.records.some((record) =>
      record?.observation_id === confirmation?.observation_id &&
      record?.hierarchy_fingerprint === confirmation?.hierarchy_fingerprint &&
      record?.recorded_at === confirmation?.recorded_at &&
      record?.decision_time === confirmation?.decision_time &&
      record?.decision === "plannable" &&
      record?.status === "evaluated"
    );
  const planRecordedAt = Date.parse(candidate?.recorded_at || "");
  const hierarchyRecordedAt = Date.parse(confirmation?.recorded_at || "");
  const evidencePrecedesPlan = Number.isFinite(planRecordedAt) &&
    Number.isFinite(hierarchyRecordedAt) && hierarchyRecordedAt <= planRecordedAt;
  const hasStoredProof = candidate?.timeframe === "1h" &&
    candidate?.label === "1H PAPER CANDIDATE" &&
    candidate?.is_recorded_paper_plan === true &&
    typeof candidate?.paper_plan_id === "string" &&
    candidate.paper_plan_id.length > 0 &&
    confirmation?.confirmed === true &&
    confirmation?.matching_boundary === true &&
    confirmation?.available_by_plan === true &&
    confirmation?.evidence_complete === true &&
    confirmation?.decision === "plannable" &&
    typeof confirmation?.observation_id === "string" &&
    typeof confirmation?.hierarchy_fingerprint === "string" &&
    typeof confirmation?.recorded_at === "string" &&
    sameBoundary && evidencePrecedesPlan && matchingRecord;
  if (status === "confirmed" && !hasStoredProof) status = "conflicting";
  const resolvedTimeframe = candidate?.timeframe || timeframe;
  const candidateLabel = typeof candidate?.label === "string"
    ? candidate.label
    : resolvedTimeframe === "1h"
      ? "1H PAPER CANDIDATE"
      : resolvedTimeframe
        ? `${String(resolvedTimeframe).toUpperCase()} PAPER CANDIDATE`
        : "PAPER CANDIDATE";
  const reason = typeof confirmation?.reason === "string" && confirmation.reason
    ? confirmation.reason
    : "No matching stored hierarchy decision is linked to this paper candidate.";
  return {
    candidateLabel,
    confirmationStatus: status,
    confirmationLabel: SIGNAL_STATUS_LABELS[status],
    confirmed: status === "confirmed" && hasStoredProof,
    reason,
    decisionTime: confirmation?.decision_time || candidate?.plan_as_of || null,
    recordedAt: confirmation?.recorded_at || null,
    decision: confirmation?.decision || null,
    observationId: confirmation?.observation_id || null,
    matchingBoundary: confirmation?.matching_boundary === true,
    availableByPlan: confirmation?.available_by_plan === true,
  };
}

/**
 * Read-only tracker poll controller, modelled on the dashboard updater.
 *
 * The dashboard refreshes its expensive analysis when a NEW candle is stored,
 * but the forward runner records outcome versions afterwards — the tracker can
 * therefore lag the ledger without any new candle appearing. This controller
 * polls the lightweight tracker endpoint, compares ledger signatures, and
 * applies only genuine changes through `applyTracker`. It never reruns
 * dashboard analysis, never touches the chart, and never invents data: a slow
 * or failed poll keeps the last recorded state on screen and backs off.
 *
 * - `fetchTracker()` resolves to the tracker payload block (cheap read).
 * - `applyTracker(tracker)` re-renders the tracker card in place.
 * - `isBlocked()` pauses polls (hidden tab, open dialog) without stopping them.
 * - `start(initialTracker)` seeds the signature so a poll right after a full
 *   dashboard refresh does not needlessly re-render.
 * - `sync(tracker)` re-seeds the signature after the card was rebuilt elsewhere.
 * - `stop()` cancels polling; a late in-flight response is discarded.
 */
export function createTrackerRefresher({
  fetchTracker,
  applyTracker,
  isBlocked = () => false,
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (handle) => clearTimeout(handle),
} = {}) {
  if (typeof fetchTracker !== "function" || typeof applyTracker !== "function") {
    throw new Error("createTrackerRefresher requires fetchTracker and applyTracker callbacks");
  }
  let timer = null;
  let stopped = true;
  let inFlight = false;
  let failures = 0;
  let signature = null;

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
    // resume as soon as the tab is visible again.
    if (inFlight || isBlocked()) {
      scheduleNext();
      return Promise.resolve(false);
    }
    inFlight = true;
    return Promise.resolve()
      .then(fetchTracker)
      .then((tracker) => {
        if (stopped) return false;
        const next = trackerLedgerSignature(tracker);
        const changed = next !== signature;
        signature = next;
        failures = 0;
        if (changed) applyTracker(tracker);
        return changed;
      })
      .catch(() => {
        // Slow or failed poll: keep the last recorded state, never invent
        // data; back off and try again.
        failures += 1;
        return false;
      })
      .finally(() => {
        inFlight = false;
        scheduleNext();
      });
  }

  return {
    start(initialTracker = null) {
      stopped = false;
      failures = 0;
      signature = trackerLedgerSignature(initialTracker);
      scheduleNext();
    },
    /** Re-seed the signature after the card was rebuilt by a full refresh. */
    sync(tracker) {
      signature = trackerLedgerSignature(tracker);
    },
    /** Poll now. An in-flight poll is never duplicated. */
    checkNow,
    stop() {
      stopped = true;
      if (timer !== null) cancel(timer);
      timer = null;
    },
    /** Diagnostics and tests only. */
    state() {
      return { stopped, inFlight, failures, signature, hasPendingTimer: timer !== null };
    },
  };
}

/** One tracked plan as the card renders it; missing facts stay UNKNOWN. */
function trackedPlanViewModel(plan) {
  const outcome = plan?.outcome && typeof plan.outcome === "object" ? plan.outcome : null;
  const settled = booleanOrNull(plan?.settled);
  const occupies = booleanOrNull(plan?.occupies_slot);
  const state = settled === true ? "settled" : occupies === true ? "active" : settled === false ? "unresolved" : "unknown";
  return {
    id: plan?.paper_plan_id || null,
    shortId: isMissing(plan?.paper_plan_id) ? UNKNOWN_TEXT : shortId(plan.paper_plan_id),
    direction: plan?.direction || null,
    family: plan?.family || null,
    timeframe: plan?.timeframe || null,
    planTime: plan?.plan_as_of || null,
    entry: plan?.entry ?? null,
    stop: plan?.stop ?? null,
    invalidation: plan?.invalidation ?? null,
    targets: Array.isArray(plan?.targets) ? plan.targets : [],
    targetRMultiples: Array.isArray(plan?.target_r_multiples) ? plan.target_r_multiples : [],
    entryReached: typeof plan?.entry_reached === "boolean" ? plan.entry_reached : null,
    entryTimestamp: plan?.entry_timestamp || null,
    settled,
    occupiesSlot: occupies,
    outcomeVersionCount: Number.isInteger(plan?.outcome_version_count) ? plan.outcome_version_count : null,
    signalProvenance: signalProvenanceViewModel(plan?.signal_provenance, {
      timeframe: plan?.timeframe || null,
    }),
    outcomeStatus: outcome?.status ?? null,
    outcomeRecordedAt: outcome?.recorded_at || null,
    outcomeObservedThrough: outcome?.observed_through || null,
    state,
    stateLabel: `${settled === null ? "OUTCOME UNKNOWN" : settled ? "SETTLED" : "UNSETTLED"} · ` +
      `${occupies === null ? "SLOT UNKNOWN" : occupies ? "SLOT OCCUPIED" : "SLOT FREE"}`,
  };
}

/**
 * View model of the `paper_tracker` block. Nothing here consults the live
 * price or the dashboard decision payload: only the tracker payload itself.
 */
export function paperTrackerViewModel(tracker) {
  if (!tracker || typeof tracker !== "object") {
    return {
      available: false,
      unavailableReason: "The tracker payload did not load, so recorded plans stay UNKNOWN rather than guessed.",
      label: null,
      disclaimer: null,
      asOf: null,
      activePlanId: null,
      unresolvedCount: null,
      recordedCount: null,
      latestAssessment: null,
      plans: [],
    };
  }
  const plans = (Array.isArray(tracker.tracked_plans) ? tracker.tracked_plans : [])
    .filter((plan) => plan && typeof plan === "object")
    .map(trackedPlanViewModel);
  const assessment = tracker.latest_assessment && typeof tracker.latest_assessment === "object"
    ? tracker.latest_assessment
    : null;
  return {
    available: true,
    unavailableReason: null,
    label: typeof tracker.label === "string" ? tracker.label : null,
    disclaimer: typeof tracker.disclaimer === "string" ? tracker.disclaimer : null,
    asOf: tracker.as_of || null,
    activePlanId: tracker.active_paper_plan_id || null,
    unresolvedCount: Number.isInteger(tracker.unresolved_paper_plan_count) ? tracker.unresolved_paper_plan_count : null,
    recordedCount: Number.isInteger(tracker.recorded_paper_plan_count) ? tracker.recorded_paper_plan_count : null,
    latestAssessment: assessment
      ? {
          available: assessment.available === true,
          asOf: assessment.as_of || null,
          setupState: assessment.setup_state || null,
          headline: assessment.explanation_headline || null,
        }
      : null,
    plans,
  };
}

function trackerTone(model) {
  if (!model.available) return "neutral";
  if (model.unresolvedCount === null) return "neutral";
  return model.unresolvedCount > 0 ? "amber" : "neutral";
}

function trackerStateLine(model) {
  if (!model.available) return "UNKNOWN";
  if (model.unresolvedCount === null) return UNKNOWN_TEXT;
  if (model.unresolvedCount > 0) return `${model.unresolvedCount} ${model.unresolvedCount === 1 ? "SLOT" : "SLOTS"} OCCUPIED · shown plans`;
  return "NO SLOTS OCCUPIED · shown plans";
}

/** One level cell; `display` is already presentation-formatted, `raw` the exact ledger text. */
function trackerLevelCell(label, display, tone, raw = null) {
  return el("div", { class: "tracker-level", dataset: { tone: tone || "neutral" } }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value mono", text: isMissing(display) ? UNKNOWN_TEXT : display, title: raw ? `Exact: ${raw}` : undefined }),
  ]);
}

function targetsDisplay(plan) {
  if (!plan.targets.length) return null;
  return plan.targets.map((target) => displayRounded(target, 2).display).join(" / ");
}

function trackerFact(label, value) {
  return el("div", { class: "tracker-fact" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }),
  ]);
}

function entryReachedText(plan) {
  if (plan.entryReached === true) {
    const at = plan.entryTimestamp ? ` at ${formatUtc(plan.entryTimestamp)}` : "";
    return `YES${at}`;
  }
  if (plan.entryReached === false) return "NO";
  return UNKNOWN_TEXT;
}

function outcomeText(plan) {
  if (!plan.outcomeStatus) return "No outcome recorded yet";
  return outcomeStatusText(plan.outcomeStatus);
}

function outcomeTimestampText(plan) {
  if (!plan.outcomeStatus) return "—";
  const recorded = plan.outcomeRecordedAt ? formatUtc(plan.outcomeRecordedAt) : UNKNOWN_TEXT;
  const through = plan.outcomeObservedThrough ? formatUtc(plan.outcomeObservedThrough) : null;
  return through ? `${recorded} · observed through ${through}` : recorded;
}

function assessmentText(model) {
  const assessment = model.latestAssessment;
  if (!assessment || assessment.available !== true) {
    return "No assessment recorded yet — a recorded plan stays here until its outcome settles.";
  }
  const state = assessment.setupState ? setupStateLabel(assessment.setupState) : UNKNOWN_TEXT;
  const asOf = assessment.asOf ? ` as of ${formatUtc(assessment.asOf)}` : "";
  return `Latest assessment: ${state}${asOf} — this recorded plan stays on the tracker until its outcome settles.`;
}

/** The featured (most recent) tracked plan, levels first, ledger facts under it. */
function signalProvenanceNode(plan) {
  const provenance = plan.signalProvenance;
  const timing = provenance.recordedAt
    ? `Hierarchy decision ${provenance.decisionTime ? formatUtc(provenance.decisionTime) : UNKNOWN_TEXT} · recorded ${formatUtc(provenance.recordedAt)}`
    : provenance.decisionTime
      ? `Candidate boundary ${formatUtc(provenance.decisionTime)} · hierarchy record time UNKNOWN`
      : "Candidate boundary and hierarchy record time UNKNOWN";
  return el("div", { class: "tracker-provenance", "aria-label": "Signal provenance" }, [
    el("div", { class: "tracker-provenance-pills" }, [
      el("span", { class: "tracker-provenance-pill candidate", text: provenance.candidateLabel }),
      el("span", {
        class: "tracker-provenance-pill confirmation",
        dataset: { status: provenance.confirmationStatus },
        text: provenance.confirmationLabel,
      }),
    ]),
    el("p", { class: "tracker-provenance-reason", text: provenance.reason }),
    el("div", { class: "tracker-provenance-time mono", text: timing }),
  ]);
}

function featuredPlanNode(plan) {
  return el("div", { class: "tracker-plan", dataset: { state: plan.state } }, [
    el("div", { class: "tracker-plan-head" }, [
      el("span", { class: "tracker-direction", dataset: { tone: plan.direction === "bearish" ? "red" : "green" }, text: `${directionArrow(plan.direction)} ${directionLabel(plan.direction)}`.trim() }),
      el("span", { class: "tracker-family", text: plan.family ? familyLabel(plan.family) : "" }),
      el("span", { class: "tracker-plan-id mono", text: `PLAN ${plan.shortId}` }),
      el("span", { class: "tracker-state-pill", dataset: { tone: plan.state === "settled" ? "neutral" : plan.state === "active" ? "amber" : "blue" }, text: plan.stateLabel }),
    ]),
    signalProvenanceNode(plan),
    el("div", { class: "tracker-levels" }, [
      trackerLevelCell("Entry", displayRounded(plan.entry, 2).display, "blue", plan.entry),
      trackerLevelCell("Stop", displayRounded(plan.stop, 2).display, "red", plan.stop),
      trackerLevelCell(
        plan.targets.length > 1 ? "Targets" : "Target",
        targetsDisplay(plan),
        "green",
        plan.targets.length ? plan.targets.join(" / ") : null,
      ),
    ]),
    el("div", { class: "tracker-facts" }, [
      trackerFact("Entry reached", entryReachedText(plan)),
      trackerFact("Latest outcome", outcomeText(plan)),
      trackerFact("Outcome recorded", outcomeTimestampText(plan)),
      trackerFact("Outcome state", plan.settled === null ? UNKNOWN_TEXT : plan.settled ? "Settled" : "Unsettled — still observing"),
      trackerFact("Paper slot", plan.occupiesSlot === null ? UNKNOWN_TEXT : plan.occupiesSlot ? "Occupied" : "Free"),
    ]),
    el("div", { class: "tracker-meta mono", text: [
      `Planned ${plan.planTime ? formatUtc(plan.planTime) : UNKNOWN_TEXT}`,
      plan.timeframe ? plan.timeframe.toUpperCase() : null,
      `Outcome versions ${plan.outcomeVersionCount === null ? UNKNOWN_TEXT : plan.outcomeVersionCount}`,
    ].filter(Boolean).join(" · ") }),
  ]);
}

/** Recent history: every older recorded plan stays visible below the featured one. */
function historyNode(plans) {
  if (plans.length === 0) return null;
  return el("div", { class: "recent-table-wrap" }, [
    el("table", { class: "recent-table tracker-history" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { scope: "col", text: "Planned" }),
        el("th", { scope: "col", text: "Direction" }),
        el("th", { scope: "col", text: "Signal provenance" }),
        el("th", { scope: "col", text: "Entry" }),
        el("th", { scope: "col", text: "Stop" }),
        el("th", { scope: "col", text: "Target" }),
        el("th", { scope: "col", text: "Entry reached" }),
        el("th", { scope: "col", text: "Latest outcome" }),
        el("th", { scope: "col", text: "State" }),
      ])]),
      el("tbody", {}, plans.map((plan) => el("tr", {}, [
        el("td", { class: "mono", text: plan.planTime ? formatUtc(plan.planTime) : UNKNOWN_TEXT }),
        el("td", { text: directionLabel(plan.direction) }),
        el("td", { class: "tracker-provenance-cell", text: `${plan.signalProvenance.candidateLabel} · ${plan.signalProvenance.confirmationLabel}` }),
        el("td", { class: "mono", text: displayOrUnknown(plan.entry) }),
        el("td", { class: "mono", text: displayOrUnknown(plan.stop) }),
        el("td", { class: "mono", text: plan.targets.length ? plan.targets.map((target) => displayRounded(target, 2).display).join(" / ") : UNKNOWN_TEXT }),
        el("td", { text: entryReachedText(plan) }),
        el("td", { class: "state-cell", text: outcomeText(plan) }),
        el("td", { class: "state-cell", text: plan.stateLabel }),
      ]))),
    ]),
  ]);
}

/** The prominent, persistent Active/Recent paper-trade tracker card. */
export function paperTrackerCard(forward) {
  const model = paperTrackerViewModel(forward?.paper_tracker);
  const paperLabel = forward?.paper_label || model.label || "PAPER OBSERVATION — NO REAL ORDER";

  let body;
  if (!model.available) {
    body = [emptyState("Paper trade tracker unavailable", model.unavailableReason)];
  } else if (model.plans.length === 0) {
    body = [el("div", { class: "tracker-empty" }, [
      el("div", { class: "big", text: "No paper trade recorded yet" }),
      el("div", { text: "The tracker lists a paper trade as soon as the engine records a paper plan, and keeps it here — settled or not — even when later assessments say No trade." }),
    ])];
  } else {
    body = [
      featuredPlanNode(model.plans[0]),
      model.plans.length > 1
        ? el("div", { class: "tracker-history-head", text: `Recent recorded plans (${model.plans.length - 1} earlier)` })
        : null,
      historyNode(model.plans.slice(1)),
      model.latestAssessment
        ? el("p", { class: "tracker-assessment", text: assessmentText(model) })
        : null,
    ];
  }

  return el("section", {
    class: "card terminal-card paper-tracker",
    "aria-label": "Active and recent paper trade tracker",
    dataset: { trackerState: model.available ? (model.unresolvedCount > 0 ? "active" : "settled") : "unknown" },
  }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Paper trade tracker" }),
      el("span", { class: "tracker-state-pill", dataset: { tone: trackerTone(model) }, text: trackerStateLine(model) }),
      el("span", { class: "card-hint", text: paperLabel }),
    ]),
    ...body,
    el("p", {
      class: "performance-caveat",
      text: "Ledger facts only — the live price strip is display-only and never changes a recorded plan or outcome." + (model.disclaimer ? ` ${model.disclaimer}` : ""),
    }),
  ]);
}
