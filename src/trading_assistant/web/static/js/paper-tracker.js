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

/** One tracked plan as the card renders it; missing facts stay UNKNOWN. */
function trackedPlanViewModel(plan) {
  const outcome = plan?.outcome && typeof plan.outcome === "object" ? plan.outcome : null;
  const settled = plan?.settled === true;
  const occupies = plan?.occupies_slot === true;
  const state = settled ? "settled" : occupies ? "active" : "unresolved";
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
    outcomeStatus: outcome?.status ?? null,
    outcomeRecordedAt: outcome?.recorded_at || null,
    outcomeObservedThrough: outcome?.observed_through || null,
    state,
    stateLabel: settled ? "SETTLED" : occupies ? "ACTIVE" : "UNRESOLVED",
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
  if (model.unresolvedCount > 0) return model.unresolvedCount === 1 ? "1 UNRESOLVED" : `${model.unresolvedCount} UNRESOLVED`;
  return "ALL SETTLED";
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
function featuredPlanNode(plan) {
  return el("div", { class: "tracker-plan", dataset: { state: plan.state } }, [
    el("div", { class: "tracker-plan-head" }, [
      el("span", { class: "tracker-direction", dataset: { tone: plan.direction === "bearish" ? "red" : "green" }, text: `${directionArrow(plan.direction)} ${directionLabel(plan.direction)}`.trim() }),
      el("span", { class: "tracker-family", text: plan.family ? familyLabel(plan.family) : "" }),
      el("span", { class: "tracker-plan-id mono", text: `PLAN ${plan.shortId}` }),
      el("span", { class: "tracker-state-pill", dataset: { tone: plan.state === "settled" ? "neutral" : plan.state === "active" ? "amber" : "blue" }, text: plan.stateLabel }),
    ]),
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
      trackerFact("Plan state", plan.stateLabel === "SETTLED" ? "Settled — final" : "Unresolved — still observing"),
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
