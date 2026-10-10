/**
 * One-page trading terminal. All market, setup, plan, observation, performance,
 * and system values are rendered from existing backend API responses. The
 * frontend does not calculate a trading verdict, create levels, or invent
 * candles, outcomes, or performance metrics.
 */

import { api } from "../api.js";
import { canShowForming } from "../forming-display.js";
import { asOfLine, freshnessBadge } from "../freshness.js";
import { createBinanceFormingStream } from "../binance-forming-display.js";
import { lookingForCard, scenarioBand } from "../looking-for.js";
import {
  buildDecisionRequest,
  confirmationSummary,
  decisionAvailability,
  DECISION_LABELS,
  DECISION_MEANINGS,
} from "../decision.js";
import {
  setZoneBands,
  applyOverlays,
  clearEvidence,
  clearOverlays,
  createPriceChart,
  destroyPriceChart,
  OVERLAY_COLORS,
  setCandles,
  setEvidence,
  setFormingCandle,
  subscribeChartEvents,
  toChartCandles,
} from "../chart.js";
import {
  buildEvidenceModel,
  evidenceCountText,
  evidenceItemRows,
  isoMs,
  itemsAtTime,
} from "../evidence.js";
import { explainEvidence, explainZoneBand } from "../explain.js";
import { capBandsForDisplay, lastConfirmedClose, relocateBands } from "../zone-position.js";
import { botWatchingCard, rawRuleRows } from "../bot-watching.js";
import {
  directionArrow,
  directionLabel,
  displayOrUnknown,
  displayRounded,
  familyLabel,
  formatUtc,
  isMissing,
  shortId,
} from "../format.js";
import {
  backendNoticeText,
  decisionText,
  disabledReasonText,
  hasValidTradePlan,
  healthDetailText,
  healthLabel,
  hierarchyAllowsReadyWording,
  hierarchyDecisionLabel,
  hierarchyStatusLabel,
  metricLabelText,
  metricStatusText,
  outcomeStatusText,
  planReasonText,
  planRefusalSentence,
  planStateLabel,
  rangeTransitionText,
  referenceTypeText,
  rejectionKindText,
  setupStateLabel,
  snapshotReasonText,
  translateRule,
  trendMomentumText,
  trendReasonText,
  trendTransitionText,
  unavailableReasonText,
  verdictStateLabel,
} from "../plain.js";
import {
  clearNode,
  el,
  emptyState,
  errorState,
  loadPrefs,
  openModal,
  savePrefs,
  setLayerPref,
  spinner,
  toast,
} from "../util.js";
import {
  integerOrNull,
  runnerDetailsViewModel,
  setTopbarWarning,
  systemHealthViewModel,
  timeframeLabel,
  updateTopbar,
} from "../topbar.js";

export { systemHealthViewModel };
export { multiTimeframeCard };
export { compactHierarchyStrip };
export { hasValidTradePlan };

let renderGeneration = 0;
let activeChart = null;

function countFromMap(map, key) {
  if (!map || typeof map !== "object") return null;
  return integerOrNull(map[key]);
}

function timeMatches(left, right) {
  if (isMissing(left) || isMissing(right)) return false;
  const leftMs = Date.parse(left);
  const rightMs = Date.parse(right);
  return Number.isFinite(leftMs) && Number.isFinite(rightMs) && leftMs === rightMs;
}

function activeCandidate(dashboard) {
  const qualification = dashboard?.qualification || {};
  const summaries = Array.isArray(qualification.setups) ? qualification.setups : [];
  const snapshotSetups = Array.isArray(qualification.snapshot?.setups)
    ? qualification.snapshot.setups
    : [];
  let summary = null;

  if (qualification.selected_setup_id) {
    summary = summaries.find((setup) => setup.id === qualification.selected_setup_id) || null;
    if (!summary) {
      summary = snapshotSetups.find((setup) => setup.id === qualification.selected_setup_id) || null;
    }
  }
  if (!summary && qualification.state === "WATCH") {
    summary = summaries.find((setup) => setup.state === "WATCH") || null;
  }
  if (!summary && qualification.state === "QUALIFIED") {
    summary = summaries.find((setup) => setup.state === "QUALIFIED") || null;
  }
  if (!summary) return null;

  const details = snapshotSetups.find((setup) => setup.id === summary.id) || {};
  const rules = Array.isArray(details.rules)
    ? details.rules
    : Array.isArray(summary.rules)
      ? summary.rules
      : [];
  return { ...details, ...summary, rules };
}

function backendReasons(values) {
  return (Array.isArray(values) ? values : [])
    .filter((value) => typeof value === "string" && value.trim())
    .map((value) => value.trim());
}

/** Rounded one-glance figure; the exact backend spelling stays on hover. */
function roundedSpan(value, decimals = 2) {
  const { display, raw } = displayRounded(value, decimals);
  return el("span", { class: "mono", text: display, title: raw === null ? undefined : `Exact: ${raw}` });
}

/** Level-3 collapsible block: raw backend strings, verbatim, never reinterpreted. */
function technicalDetails(summaryText, rows) {
  const items = (Array.isArray(rows) ? rows : []).filter(Boolean);
  if (!items.length) return null;
  return el("details", { class: "details-disclosure" }, [
    el("summary", { text: summaryText }),
    el("div", { class: "details-body" }, items.map((row) => el("div", { class: "chart-note mono", text: row }))),
  ]);
}

/** Translated blocking sentences (vetoes first), deduplicated. */
function blockingRuleSentences(candidate) {
  if (!candidate || !Array.isArray(candidate.rules)) return [];
  const direction = candidate.direction || null;
  const vetoed = [];
  const failed = [];
  const pending = [];
  for (const rule of candidate.rules) {
    if (rule?.required === false) continue;
    if (rule?.outcome !== "failed" && rule?.outcome !== "pending") continue;
    const translated = translateRule(rule, { direction });
    if (rule?.veto === true && rule?.outcome === "failed") vetoed.push(translated.sentence);
    else if (rule?.outcome === "failed") failed.push(translated.sentence);
    else pending.push(translated.sentence);
  }
  return [...new Set([...vetoed, ...failed, ...pending])];
}

function decisionExplanation(dashboard, candidate, plannable) {
  const qualification = dashboard?.qualification || {};
  const planning = dashboard?.planning || {};
  if (plannable) {
    return hierarchyAllowsReadyWording(dashboard?.multi_timeframe)
      ? "The complete deterministic hierarchy supports this plan; no order is placed."
      : "Deterministic entry, stop and target levels are calculated; trade confirmation is separate.";
  }
  if (qualification.available !== true) {
    return "The current qualification state is unavailable from the backend.";
  }

  if (qualification.state === "WATCH") {
    const blocking = blockingRuleSentences(candidate);
    if (blocking.length) return blocking.slice(0, 3).join(" ");
    const reasons = backendReasons(qualification.reasons).map(snapshotReasonText).filter(Boolean);
    if (reasons.length) return reasons.join(" ");
    return "Watching: evidence is still developing.";
  }

  if (qualification.state === "NO_SETUP") {
    const reasons = backendReasons(qualification.reasons).map(snapshotReasonText).filter(Boolean);
    return reasons.length ? reasons.join(" ") : "No active setup in this snapshot.";
  }

  if (qualification.state === "QUALIFIED") {
    const codes = backendReasons(planning.reasons);
    if (codes.length) {
      const details = codes.map(planReasonText).filter(Boolean);
      return details.length
        ? `The setup is qualified, but no plan could be built (${details.join("; ")}).`
        : "The setup is qualified, but no valid trade plan is available.";
    }
    const detail = typeof planning.state_detail === "string" ? planReasonText(planning.state_detail) : null;
    if (detail) return `The setup is qualified, but no plan could be built (${detail}).`;
    return "The setup is qualified, but no valid trade plan is available.";
  }

  return "The current qualification state is unavailable from the backend.";
}

/** Presentation-only projection of deterministic dashboard and runner facts. */
export function verdictViewModel(dashboard, forward = null) {
  const qualification = dashboard?.qualification || {};
  const setups = Array.isArray(qualification.setups) ? qualification.setups : [];
  const plannable = hasValidTradePlan(dashboard);
  const candidate = qualification.available === true ? activeCandidate(dashboard) : null;
  const setupListAvailable = qualification.available === true && Array.isArray(qualification.setups);
  const qualifiedCount = setupListAvailable
    ? integerOrNull(setups.filter((setup) => setup.state === "QUALIFIED").length)
    : null;
  const watchCount = setupListAvailable
    ? integerOrNull(setups.filter((setup) => setup.state === "WATCH").length)
    : null;

  let plannedCount = null;
  const current = forward?.status?.current_state;
  const sameRecordedBoundary = current?.available === true &&
    timeMatches(current.as_of, dashboard?.meta?.as_of);
  if (sameRecordedBoundary) {
    plannedCount = countFromMap(current.plan_state_counts, "PLANNABLE");
  } else if (qualification.available === true) {
    // The dashboard plans the selected setup only, so the known plannable
    // count is 1 or 0 whenever a qualified setup exists — regardless of how
    // many qualified setups share the snapshot.
    if (qualification.state === "NO_SETUP") plannedCount = 0;
    else if (qualification.state === "QUALIFIED" && dashboard?.planning?.state) plannedCount = plannable ? 1 : 0;
  }

  const hierarchy = dashboard?.multi_timeframe;
  const hierarchyReady = plannable && hierarchyAllowsReadyWording(hierarchy);
  const hierarchyStatus = hierarchyStatusLabel(hierarchy);
  let state = "UNKNOWN";
  let tone = "unknown";
  if (plannable) {
    state = hierarchyReady ? hierarchyStatus : "PLAN CALCULATED";
    tone = hierarchyReady ? "green" : "amber";
  } else if (qualification.available === true && qualification.state === "WATCH") {
    state = "WATCH";
    tone = "amber";
  } else if (qualification.available === true &&
    (qualification.state === "NO_SETUP" || qualification.state === "QUALIFIED")) {
    state = "NO TRADE";
    tone = "neutral";
  }
  if (!plannable) state = verdictStateLabel(state);

  const plan = plannable ? dashboard.plan : null;
  const direction = plan?.direction || candidate?.direction || null;
  const family = plan?.family || candidate?.family || null;

  return {
    state,
    tone,
    direction,
    family,
    watchCount,
    qualifiedCount,
    plannedCount,
    candidate,
    plannable,
    hierarchyReady,
    hierarchyStatus,
    planStatus: plannable
      ? hierarchyReady
        ? "Hierarchy complete · decision support only; no order placed."
        : "Trade not confirmed yet"
      : null,
    hierarchyGate: plannable ? (hierarchyReady ? "COMPLETE" : hierarchyStatus) : null,
    explanation: decisionExplanation(dashboard, candidate, plannable),
  };
}

export function performanceViewModel(forward) {
  const status = forward?.status || {};
  const report = forward?.report || {};
  const combinedAvailable = report.combined_metrics_available !== false &&
    Boolean(report.metrics);
  const versionSeparated = report.combined_metrics_available === false;
  const metrics = combinedAvailable ? report.metrics : null;
  const distribution = metrics?.raw_observational_r || null;
  const frictionDistribution = metrics?.friction_adjusted_hypothetical_r || null;
  // Do not turn a cross-version status total into a combined performance figure.
  const reportedPlans = versionSeparated ? null : integerOrNull(status.sample?.paper_plans);
  const paperPlans = reportedPlans ?? integerOrNull(metrics?.paper_plan_count);
  const targetRates = Array.isArray(metrics?.target_hit_rates)
    ? metrics.target_hit_rates.map((rate, index) => ({
        label: `Target T${index + 1} reached`,
        metric: rate,
      }))
    : [];
  const rates = metrics
    ? [
        { label: "Entry reached", metric: metrics.entry_reached_rate },
        { label: "Entry not reached", metric: metrics.entry_not_reached_rate },
        { label: "Stopped after ordered entry", metric: metrics.stopped_rate },
        { label: "Unresolved / open", metric: metrics.unresolved_rate },
        { label: "Ambiguous outcome", metric: metrics.ambiguous_rate },
        { label: "Incomplete data", metric: metrics.incomplete_rate },
        ...targetRates,
      ].filter((item) => item.metric && typeof item.metric === "object")
    : [];

  return {
    paperPlans,
    resolvedOutcomes: integerOrNull(metrics?.completed_count),
    unresolvedOutcomes: integerOrNull(metrics?.unresolved_count),
    eligibleRSample: integerOrNull(distribution?.sample_size),
    consideredRSample: integerOrNull(distribution?.records_considered),
    rSampleStatus: typeof distribution?.status === "string" ? distribution.status : null,
    averageObservedR: distribution?.average ?? null,
    observedRDistribution: distribution,
    averageFrictionAdjustedR: frictionDistribution?.average ?? null,
    frictionAdjustedRDistribution: frictionDistribution,
    outcomeStatusCounts: Array.isArray(metrics?.outcome_status_counts)
      ? metrics.outcome_status_counts
      : [],
    rates,
    combinedAvailable,
    versionSeparated,
    combinedUnavailableReason: report.combined_metrics_unavailable_reason || null,
    frictionAssumptions: report.friction && typeof report.friction === "object" ? report.friction : null,
    versionCohorts: Array.isArray(report.version_cohorts) ? report.version_cohorts : [],
    disclaimer: forward?.disclaimer ||
      "Paper trading and historical performance do not establish future profitability.",
  };
}

function chartEmpty(title, detail) {
  return el("div", { class: "chart-empty", role: "status" }, [
    el("div", { class: "big", text: title }),
    el("div", { text: detail }),
  ]);
}

// ---------------------------------------------------------------------------
// Step 13 UI: view-only chart timeframe switching.
//
// The switcher below lets the operator LOOK AT the stored closed candles of
// any hierarchy timeframe (5m / 15m / 1h / 4h). It is presentation only:
//
// - it only issues read-only GETs to /api/market/candles and
//   /api/market/structure (the same stored-candle tables the engine uses),
// - it never calls the dashboard endpoint again, never records anything, and
//   never writes the engine timeframe preference, so the qualification,
//   plan, hierarchy, journal, and forward-testing state cannot change,
// - candles and structure always belong to the selected timeframe at the
//   dashboard's own decision instant (end_time/as_of pinned to meta.as_of),
// - overlays are cleared before every switch, so a level from the previous
//   timeframe is never shown as though it belonged to the new one,
// - entry/stop/target lines always come from the deterministic trade plan in
//   the dashboard payload, never from the chart selection.
// ---------------------------------------------------------------------------

export const CHART_TIMEFRAMES = [
  { id: "5m", label: "5M" },
  { id: "15m", label: "15M" },
  { id: "1h", label: "1H" },
  { id: "4h", label: "4H" },
];

const CHART_CANDLE_LIMIT = 500;
const ENGINE_CHART_NOTE = "Confirmed history is stored closed candles only. Any ghost forming candle is public Binance Spot data, display only; it is never confirmed here.";
const BINANCE_EXCHANGE_ID = "binance";
const BINANCE_FORMING_LABEL = "BINANCE SPOT KLINE";

function viewedTimeframeLabel(timeframe) {
  const known = CHART_TIMEFRAMES.find((entry) => entry.id === timeframe);
  return known ? known.label : timeframeLabel(timeframe);
}

function switcherEntries(engineTimeframe) {
  if (CHART_TIMEFRAMES.some((entry) => entry.id === engineTimeframe)) return CHART_TIMEFRAMES;
  // The engine timeframe is always reachable, even when it is outside the
  // fixed hierarchy set (e.g. a 1d engine view): the switcher gains one extra
  // button rather than stranding the operator on another timeframe.
  return [...CHART_TIMEFRAMES, { id: engineTimeframe, label: timeframeLabel(engineTimeframe) }];
}

function emptyOverlays() {
  return { equal_levels: [], zones: [], range: null, swings: [], setup_reference: null };
}

/**
 * Overlays for the timeframe being viewed. The engine view keeps the exact
 * dashboard snapshot overlays (zones, range, equal levels, setup reference).
 * Any other timeframe uses only the structure payload fetched for THAT
 * timeframe; equal levels and the setup reference are engine-timeframe
 * artefacts, so they are honestly absent elsewhere rather than reused.
 */
export function overlaysForViewedTimeframe({ dashboard, viewedTimeframe, structure = null }) {
  const engineTimeframe = dashboard?.meta?.timeframe;
  if (viewedTimeframe === engineTimeframe) {
    const overlays = dashboard?.overlays;
    return overlays && typeof overlays === "object" ? overlays : emptyOverlays();
  }
  if (!structure || typeof structure !== "object" || structure.timeframe !== viewedTimeframe) {
    return emptyOverlays();
  }
  return {
    equal_levels: [],
    zones: Array.isArray(structure.zones) ? structure.zones : [],
    range: structure.range && typeof structure.range === "object" ? structure.range : null,
    swings: Array.isArray(structure.swings) ? structure.swings : [],
    setup_reference: null,
  };
}

function chartHeadingMeta(viewedTimeframe, engineTimeframe) {
  if (viewedTimeframe === engineTimeframe) return "Stored closed candles · separate display-only forming candle when available";
  const viewed = viewedTimeframeLabel(viewedTimeframe);
  const engine = viewedTimeframeLabel(engineTimeframe);
  return `View only — ${viewed} stored candles with ${viewed} structure · engine hierarchy unchanged · plan levels from the ${engine} engine plan`;
}

/** Layer toggles on the chart. `overlay` keeps the legacy overlay key pinned by existing tests. */
const CHART_LAYER_TOGGLES = Object.freeze([
  { id: "structure", label: "Structure", layer: "structure", color: "#8ea0bd", hint: "Confirmed swing labels (HH, HL, LH, LL) at their candle, shown from confirmation." },
  { id: "patterns", label: "Patterns", layer: "patterns", color: "#e8a33d", hint: "Double top/bottom and head and shoulders only: formed, confirmed or invalidated." },
  { id: "breakouts", label: "Breakouts & sweeps", layer: "breakouts", color: "#2fbf7f", hint: "Breakouts, failed breakouts, sweeps and retests that BRAIN detected." },
  { id: "levels", label: "Support / resistance", layer: "levels", overlay: "zones", color: OVERLAY_COLORS.zones, hint: "Shaded support and resistance bands from stored zones, nearest first. Click a band label to inspect it." },
  { id: "htfLevels", label: "Higher-TF levels", layer: "htfLevels", color: OVERLAY_COLORS.reference, hint: "Higher-timeframe zone bands, dashed and labelled with their timeframe. Never mixed with this chart's own levels." },
  { id: "plan", label: "Trade planning", layer: "plan", overlay: "planLevels", color: OVERLAY_COLORS.entry, hint: "Entry, stop, invalidation and targets from the engine's paper plan. Never an order." },
  { id: "candleSignals", label: "Candle shapes", layer: "candleSignals", color: "#a7b0c2", hint: "Descriptive candle shapes. Not trade signals and never part of qualification." },
  { id: "equalLevels", label: "Liquidity", overlay: "equalLevels", color: OVERLAY_COLORS.equalLevels, hint: "Equal highs and lows. Only on the engine timeframe.", legacy: true },
]);

/** Next higher timeframe whose stored structure may be shown on this chart. */
const HIGHER_TIMEFRAME = Object.freeze({ "5m": "15m", "15m": "1h", "1h": "4h" });
const CHART_MARKER_KEY = "▲ breakout (green up, red down) · ▼ sweep · grey = failed breakout · ● retest · HH/HL/LH/LL swings · patterns: DT double top, DB double bottom, H&S head and shoulders, iH&S inverse H&S (labels give the lifecycle state) · a dot with no label = label hidden to avoid collisions · click any marker for detail";
const CHART_EXPLAIN_HINT = "Click a candle to see what BRAIN recorded there, when it became known, and whether it influenced the current assessment.";

function evidenceMetaText(evidence, timeframe) {
  const label = viewedTimeframeLabel(timeframe);
  if (!evidence || evidence.available !== true) {
    const reason = evidence && typeof evidence.reason === "string" && evidence.reason.trim() ? ` (${evidence.reason.trim()})` : "";
    return `Chart evidence unavailable for ${label}${reason}. No substitute markers are drawn.`;
  }
  const window = evidence.evidence_window && typeof evidence.evidence_window === "object" ? evidence.evidence_window : {};
  const count = Number.isInteger(window.candle_count) ? window.candle_count : "UNKNOWN";
  const limit = Number.isInteger(window.candle_limit) ? window.candle_limit : "UNKNOWN";
  const excluded = Number.isInteger(evidence.excluded_future_count) ? evidence.excluded_future_count : "UNKNOWN";
  return `Evidence window: last ${count} closed ${label} candles (limit ${limit}) · ${excluded} later item(s) excluded. Display only: the chart does not change qualification, plans or observations.`;
}

function explanationNode(explanation) {
  const [what, ...rest] = explanation.sections;
  return el("article", {
    class: "evidence-item",
    dataset: { tone: explanation.tone || "neutral", kind: explanation.kind || "evidence" },
  }, [
    el("div", { class: "evidence-item-head" }, [
      el("strong", { class: "evidence-item-title", text: explanation.title }),
      el("span", { class: "evidence-status", dataset: { tone: explanation.tone || "neutral" }, text: explanation.status }),
    ]),
    el("div", { class: "evidence-item-meta", text: [explanation.kindTitle, explanation.timeframe].filter(Boolean).join(" · ") }),
    el("p", { class: "evidence-what", text: what ? what.body : "UNKNOWN" }),
    el("details", { class: "evidence-details" }, [
      el("summary", { text: "Why, timing and influence" }),
      ...rest.map((section) => el("div", { class: "evidence-section" }, [
        el("div", { class: "evidence-section-title", text: section.heading }),
        el("div", { class: "evidence-section-body", text: section.body }),
      ])),
    ]),
    explanation.note ? el("p", { class: "evidence-disclaimer", text: explanation.note }) : null,
  ]);
}

function chartCard(dashboard) {
  const meta = dashboard?.meta || {};
  const symbol = meta.symbol || "UNKNOWN";
  const exchange = typeof meta.exchange === "string" ? meta.exchange.toLowerCase() : "";
  const engineTimeframe = typeof meta.timeframe === "string" && meta.timeframe ? meta.timeframe : "1h";
  const decisionAsOf = typeof meta.as_of === "string" && meta.as_of ? meta.as_of : null;
  const decisionAsOfMs = isoMs(decisionAsOf);
  const rows = Array.isArray(dashboard?.market?.candles) ? dashboard.market.candles : [];
  const validCandles = toChartCandles(rows);
  const host = el("div", {
    class: "chart-wrap terminal-chart-wrap",
    "aria-label": `${symbol} ${viewedTimeframeLabel(engineTimeframe)} candlestick chart`,
  });
  const toolbar = el("div", { class: "chart-toolbar", role: "group", "aria-label": "Chart timeframe and layers" });
  const handleRef = { current: null };
  const formingStatus = el("div", { class: "forming-status", role: "status",
    "aria-label": "Forming candle display only", text: "FORMING — DISPLAY ONLY" });
  const confirmedStatus = el("div", { class: "chart-note", text: "Last confirmed stored close: unavailable" });
  const ohlcLine = el("div", { class: "chart-ohlc", "aria-live": "off", text: "Move over a candle for its confirmed OHLC." });
  const evidenceMeta = el("div", { class: "chart-evidence-meta", role: "note", text: "" });
  // Text alternative to the chart markers: every drawn item, reachable without hovering or clicking the canvas.
  const itemListBody = el("div", { class: "chart-items-body" });
  const itemListSummary = el("summary", { text: "Evidence items on this chart" });
  const itemList = el("details", { class: "chart-items" }, [itemListSummary, itemListBody]);
  // Key for the textless arrow and circle markers. Labels (HH, LL, Double top…) stay on the chart.
  const markerKey = el("div", { class: "chart-marker-key", "aria-label": "Marker key", text: CHART_MARKER_KEY });
  const explainBody = el("div", { class: "evidence-explain-body", "aria-live": "polite" }, [
    el("p", { class: "evidence-explain-hint", text: CHART_EXPLAIN_HINT }),
  ]);
  const explainPanel = el("section", { class: "evidence-explain", "aria-label": "Click-to-explain evidence" }, [
    el("div", { class: "evidence-explain-title", text: "Click-to-explain" }),
    explainBody,
  ]);
  let formingStream = null;
  let eventRelease = null;
  const viewed = {
    timeframe: engineTimeframe,
    generation: 0,
    cancelled: false,
    overlays: dashboard?.overlays && typeof dashboard.overlays === "object" ? dashboard.overlays : emptyOverlays(),
    evidence: dashboard?.chart_evidence && typeof dashboard.chart_evidence === "object"
      ? dashboard.chart_evidence
      : { available: false, reason: "the dashboard payload did not include chart evidence" },
    candleRows: rows,
    model: null,
    htfBands: [],
    htfStatus: null,
  };
  let emptyNode = null;

  const headingTitle = el("div", { class: "chart-heading-title", text: `${symbol} · ${viewedTimeframeLabel(engineTimeframe)} chart` });
  const headingMeta = el("div", { class: "chart-heading-meta", text: chartHeadingMeta(engineTimeframe, engineTimeframe) });
  const viewNote = el("div", { class: "chart-note terminal-chart-note chart-view-note", text: ENGINE_CHART_NOTE });

  const showChartOverlay = (node) => {
    if (emptyNode) emptyNode.remove();
    emptyNode = node;
    host.append(node);
  };
  const removeChartOverlay = () => {
    if (emptyNode) emptyNode.remove();
    emptyNode = null;
  };
  const planForOverlays = () => (hasValidTradePlan(dashboard) ? dashboard.plan : null);
  function stopForming() {
    formingStream?.stop();
    formingStream = null;
    setFormingCandle(handleRef.current, null);
  }
  function startForming(timeframe, storedRows) {
    stopForming();
    const confirmed = toChartCandles(storedRows);
    const latest = confirmed.at(-1);
    confirmedStatus.textContent = latest
      ? `LAST CONFIRMED ${viewedTimeframeLabel(timeframe)} CLOSE · $${latest.close.toLocaleString("en-US")} · stored candle`
      : "Last confirmed stored close: unavailable";
    formingStatus.dataset.freshness = "UNAVAILABLE";
    formingStatus.textContent = `FORMING ${viewedTimeframeLabel(timeframe)} — DISPLAY ONLY`;
    if (!handleRef.current || exchange !== BINANCE_EXCHANGE_ID || symbol !== "BTC/USDT" || !latest ||
        !canShowForming(timeframe, latest.time * 1000, Date.now())) return;
    // This public display-only socket is bound ONLY to the selected chart view,
    // never the setup snapshot or any BRAIN/paper-trading input.
    formingStream = createBinanceFormingStream({
      symbol,
      timeframe,
      confirmedOpenMs: latest.time * 1000,
      onCandle: (candle) => setFormingCandle(handleRef.current, candle),
      onStatus: (status, receivedAt) => {
        formingStatus.dataset.freshness = status;
        formingStatus.textContent = status === "CURRENT"
          ? `FORMING ${viewedTimeframeLabel(timeframe)} — DISPLAY ONLY · ${BINANCE_FORMING_LABEL} CURRENT · last update ${new Date(receivedAt).toISOString().slice(11, 19)} UTC`
          : `FORMING ${viewedTimeframeLabel(timeframe)} — DISPLAY ONLY · ${BINANCE_FORMING_LABEL} ${status} · stored chart unchanged`;
      },
    });
    formingStream.start();
  }
  const applyViewedOverlays = () => {
    if (!handleRef.current) return;
    const prefs = loadPrefs();
    applyOverlays(handleRef.current, {
      overlays: viewed.overlays || {},
      plan: planForOverlays(),
      scenarioBand: scenarioBand(dashboard?.looking_for, viewed.timeframe),
      prefs,
    });
    // Support/resistance bands: the viewed timeframe's presented bands, plus higher-timeframe bands when that layer is on.
    // Every band is positioned against the VIEWED chart's last confirmed close, not the close of the timeframe
    // that produced it. The source close and source position stay on the band as evidence.
    const viewedClose = lastConfirmedClose(viewed.candleRows);
    // Calm default: at most the nearest band per side plus one containing price, per timeframe. The
    // rest are still stored and listed in the nearby-zones card; they are simply not painted on candles.
    const viewedBands = prefs.overlays?.zones === true && Array.isArray(viewed.overlays?.zone_bands?.bands)
      ? capBandsForDisplay(relocateBands(viewed.overlays.zone_bands.bands, viewedClose)).shown
      : [];
    const higherBands = prefs.layers?.htfLevels === true
      ? capBandsForDisplay(relocateBands(viewed.htfBands || [], viewedClose)).shown
      : [];
    setZoneBands(handleRef.current, [...viewedBands, ...higherBands], { onSelect: selectZoneBand });
  };
  /** Draw evidence for the viewed timeframe. Clears previous evidence first. */
  const renderEvidence = () => {
    const handle = handleRef.current;
    if (!handle) return;
    const layers = loadPrefs().layers;
    viewed.model = buildEvidenceModel({
      evidence: viewed.evidence,
      candles: viewed.candleRows,
      asOfMs: decisionAsOfMs,
      layers,
    });
    setEvidence(handle, viewed.model);
    const counts = viewed.model.hiddenFuture
      ? ` · ${viewed.model.hiddenFuture} item(s) not yet known at this instant are hidden`
      : "";
    const htf = layers.htfLevels === true && viewed.htfStatus ? ` · ${viewed.htfStatus}` : "";
    const zb = viewed.overlays?.zone_bands;
    const zoneText = loadPrefs().overlays?.zones === true && zb && zb.reason !== "no_zones"
      ? ` · S/R bands: ${zb.bands?.length ?? 0} shown, ${zb.hidden_count ?? 0} hidden, ${zb.merged_count ?? 0} merged`
      : "";
    evidenceMeta.textContent = `${evidenceMetaText(viewed.evidence, viewed.timeframe)} ${evidenceCountText(viewed.model)}${counts}${htf}${zoneText}`;
    renderItemList(viewed.model);
  };
  /** One button per drawn evidence item; selecting it opens the same click-to-explain panel as a chart click. */
  const renderItemList = (model) => {
    clearNode(itemListBody);
    const rows = evidenceItemRows(model);
    itemListSummary.textContent = rows.length
      ? `Evidence items on this chart (${rows.length}) — select one to explain it`
      : "Evidence items on this chart (none in the loaded window)";
    if (!rows.length) return;
    const list = el("ul", { class: "chart-items-list", role: "list" });
    for (const row of rows) {
      list.append(el("li", {}, [
        el("button", {
          type: "button",
          class: "chart-item-button",
          onclick: () => showExplanationAt(row.timeSeconds),
        }, [
          el("span", { class: "chart-item-time mono", text: formatUtc(new Date(row.timeSeconds * 1000).toISOString()) }),
          el("span", { class: "chart-item-name", text: row.name }),
        ]),
      ]));
    }
    itemListBody.append(list);
  };
  const showOhlc = (bar) => {
    if (!bar) {
      ohlcLine.textContent = "Move over a candle for its confirmed OHLC.";
      return;
    }
    const when = formatUtc(new Date(bar.time * 1000).toISOString());
    const volumeText = Number.isFinite(bar.volume) ? ` · V ${bar.volume}` : "";
    ohlcLine.textContent = `${when} · O ${bar.open} · H ${bar.high} · L ${bar.low} · C ${bar.close}${volumeText} · confirmed stored candle`;
  };
  /** Click a band label: explain that zone (bounds, origin, touches, last test, position vs price). */
  const selectZoneBand = (band) => {
    clearNode(explainBody);
    const explanation = explainZoneBand(band, { timeframe: band.source_timeframe, close: band.viewed_close ?? band.latest_close ?? null });
    if (!explanation) {
      explainBody.append(el("p", { class: "evidence-explain-hint", text: "This zone has no complete stored bounds, so it cannot be explained." }));
      return;
    }
    explainBody.append(el("div", { class: "evidence-explain-when", text: band.htf ? `Higher-timeframe zone (${band.source_timeframe})` : `Zone on the ${viewed.timeframe} chart` }));
    explainBody.append(explanationNode(explanation));
  };
  const showExplanationAt = (timeSeconds) => {
    if (!Number.isFinite(timeSeconds)) return;
    clearNode(explainBody);
    const when = formatUtc(new Date(timeSeconds * 1000).toISOString());
    if (!viewed.model) {
      explainBody.append(el("p", { class: "evidence-explain-hint", text: `No chart evidence is loaded for this view (${when}).` }));
      return;
    }
    const items = itemsAtTime(viewed.model, timeSeconds);
    const explained = items
      .map((item) => explainEvidence(item, { timeframe: viewed.timeframe, qualification: dashboard?.qualification || null }))
      .filter(Boolean);
    explainBody.append(el("div", { class: "evidence-explain-when", text: `Candle opened ${when}` }));
    if (!explained.length) {
      explainBody.append(el("p", { class: "evidence-explain-hint", text: "Nothing BRAIN recorded at this candle for the layers you have switched on." }));
      return;
    }
    for (const explanation of explained) explainBody.append(explanationNode(explanation));
  };
  const ensureChart = () => {
    if (handleRef.current || viewed.cancelled) return handleRef.current;
    try {
      const handle = createPriceChart(host);
      if (!handle) {
        showChartOverlay(chartEmpty("Chart unavailable", "The chart library did not load, so stored candles cannot be drawn. Stored data has not been replaced."));
        return null;
      }
      handleRef.current = handle;
      eventRelease = subscribeChartEvents(handle, { onClick: showExplanationAt, onCrosshair: showOhlc });
      return handle;
    } catch {
      handleRef.current = null;
      showChartOverlay(chartEmpty("Chart unavailable", "Stored data could not be rendered. No substitute candles are shown."));
      return null;
    }
  };

  // Timeframe switcher: which stored candles to LOOK AT. View only — the
  // engine hierarchy below the chart never reads this selection.
  const switcher = el("div", { class: "chart-timeframe-switch", role: "group", "aria-label": "Chart timeframe (view only)" });
  const switchButtons = new Map();
  for (const entry of switcherEntries(engineTimeframe)) {
    const button = el("button", {
      class: "tf-button",
      type: "button",
      "data-timeframe": entry.id,
      "aria-pressed": String(entry.id === viewed.timeframe),
      "aria-label": `View ${entry.label} chart (view only)`,
      title: "View only — switching never changes the engine hierarchy, plan, or journal",
      onclick: () => { void showTimeframe(entry.id); },
    }, [entry.label]);
    switchButtons.set(entry.id, button);
    switcher.append(button);
  }
  toolbar.append(switcher);
  toolbar.append(el("span", { class: "chart-toolbar-sep", "aria-hidden": "true" }));

  // Layer toggles. Each button either flips a layer (persisted with its legacy
  // overlay keys together) or a legacy overlay key directly (Liquidity).
  const layerButtons = new Map();
  for (const toggle of CHART_LAYER_TOGGLES) {
    const attributes = {
      class: "overlay-toggle layer-toggle",
      type: "button",
      "aria-label": `${toggle.label} ${toggle.legacy ? "overlay" : "layer"}`,
      title: toggle.hint,
      onclick: () => { void toggleLayer(toggle); },
    };
    if (toggle.overlay) attributes["data-overlay"] = toggle.overlay;
    if (toggle.layer) attributes["data-layer"] = toggle.layer;
    const button = el("button", attributes, [el("span", { class: "swatch", "aria-hidden": "true" }), toggle.label]);
    // Custom properties need setProperty (Object.assign on CSSStyleDeclaration ignores them).
    if (typeof button.style?.setProperty === "function") button.style.setProperty("--swatch", toggle.color);
    layerButtons.set(toggle.id, button);
    toolbar.append(button);
  }
  const refreshLayerButtons = () => {
    const prefs = loadPrefs();
    for (const toggle of CHART_LAYER_TOGGLES) {
      const button = layerButtons.get(toggle.id);
      if (!button) continue;
      const pressed = toggle.layer ? prefs.layers?.[toggle.layer] === true : prefs.overlays?.[toggle.overlay] === true;
      button.setAttribute("aria-pressed", String(pressed));
    }
  };
  const updateLiquidityToggle = () => {
    const button = layerButtons.get("equalLevels");
    if (!button) return;
    const engineView = viewed.timeframe === engineTimeframe;
    button.disabled = !engineView;
    if (engineView) button.title = CHART_LAYER_TOGGLES.find((entry) => entry.id === "equalLevels").hint;
    else button.title = `Equal-level overlays are only available on the engine timeframe (${viewedTimeframeLabel(engineTimeframe)})`;
  };
  async function toggleLayer(toggle) {
    const button = layerButtons.get(toggle.id);
    if (button?.disabled) return;
    const prefs = loadPrefs();
    if (toggle.layer) {
      setLayerPref(prefs, toggle.layer, prefs.layers?.[toggle.layer] !== true);
    } else {
      // Liquidity: its own persisted key, never driven by a layer.
      prefs.overlays = { ...prefs.overlays, equalLevels: prefs.overlays?.equalLevels !== true };
      savePrefs(prefs);
    }
    refreshLayerButtons();
    if (toggle.layer === "htfLevels") {
      await refreshHigherLevels();
      return;
    }
    applyViewedOverlays();
    renderEvidence();
  }

  /** Higher-timeframe zones: fetched only when the layer is on, cached per view, never mixed into the viewed timeframe's levels. */
  async function refreshHigherLevels() {
    const generation = viewed.generation;
    const target = HIGHER_TIMEFRAME[viewed.timeframe];
    if (loadPrefs().layers?.htfLevels !== true) {
      viewed.htfBands = [];
      viewed.htfStatus = null;
      applyViewedOverlays();
      renderEvidence();
      return;
    }
    if (!target) {
      viewed.htfBands = [];
      viewed.htfStatus = `no higher timeframe is shown above ${viewedTimeframeLabel(viewed.timeframe)}`;
      applyViewedOverlays();
      renderEvidence();
      return;
    }
    let payload = null;
    let failure = null;
    try {
      payload = await api.structure({ symbol, timeframe: target, as_of: decisionAsOf || undefined });
    } catch (error) {
      failure = error;
    }
    if (viewed.cancelled || generation !== viewed.generation) return;
    if (failure || payload?.timeframe !== target) {
      viewed.htfBands = [];
      viewed.htfStatus = `${viewedTimeframeLabel(target)} levels unavailable`;
    } else {
      const label = viewedTimeframeLabel(target);
      // Backend presentation: merged, nearest-first, classified by position relative to price.
      const bands = Array.isArray(payload.zone_bands?.bands) ? payload.zone_bands.bands : [];
      viewed.htfBands = bands.map((band) => ({ ...band, htf: true }));
      viewed.htfStatus = bands.length
        ? `${label} zones: ${bands.length} band(s) shown`
        : `${label} zones: none stored at this instant`;
    }
    applyViewedOverlays();
    renderEvidence();
  }

  async function showTimeframe(timeframe) {
    if (timeframe === viewed.timeframe || viewed.cancelled) return;
    const generation = ++viewed.generation;
    const label = viewedTimeframeLabel(timeframe);
    viewed.timeframe = timeframe;
    for (const [id, button] of switchButtons) button.setAttribute("aria-pressed", String(id === timeframe));
    headingTitle.textContent = `${symbol} · ${label} chart`;
    headingMeta.textContent = chartHeadingMeta(timeframe, engineTimeframe);
    host.setAttribute("aria-label", `${symbol} ${label} candlestick chart (view only)`);
    updateLiquidityToggle();
    removeChartOverlay();
    stopForming(); // remove the old temporary candle immediately, before any read
    // No stale evidence or levels: the previous timeframe's markers, pattern
    // lines, price lines, and explanation leave the chart before any read.
    if (handleRef.current) {
      clearOverlays(handleRef.current);
      clearEvidence(handleRef.current);
    }
    viewed.model = null;
    viewed.htfBands = [];
    viewed.htfStatus = null;
    clearNode(explainBody).append(el("p", { class: "evidence-explain-hint", text: CHART_EXPLAIN_HINT }));
    evidenceMeta.textContent = "";
    clearNode(itemListBody);
    itemListSummary.textContent = "Evidence items on this chart";
    showOhlc(null);
    viewNote.textContent = `Loading stored ${label} closed candles…`;

    // The engine view restores the exact dashboard snapshot (no refetch, no
    // drift): these are the same stored rows the verdict was computed from.
    if (timeframe === engineTimeframe) {
      if (generation !== viewed.generation || viewed.cancelled) return;
      viewed.overlays = dashboard?.overlays && typeof dashboard.overlays === "object"
        ? dashboard.overlays
        : emptyOverlays();
      viewed.evidence = dashboard?.chart_evidence && typeof dashboard.chart_evidence === "object"
        ? dashboard.chart_evidence
        : { available: false, reason: "the dashboard payload did not include chart evidence" };
      viewed.candleRows = rows;
      const engineValid = toChartCandles(rows);
      if (!engineValid.length) {
        if (handleRef.current) setCandles(handleRef.current, []);
        showChartOverlay(chartEmpty(
          "Candle data unavailable",
          rows.length
            ? "The stored candle payload contains no renderable rows. No substitute data is shown."
            : "No stored closed candles were returned for this symbol and timeframe.",
        ));
        viewNote.textContent = ENGINE_CHART_NOTE;
        return;
      }
      const handle = ensureChart();
      if (!handle || generation !== viewed.generation || viewed.cancelled) return;
      setCandles(handle, rows);
      startForming(timeframe, rows);
      renderEvidence();
      applyViewedOverlays();
      viewNote.textContent = ENGINE_CHART_NOTE;
      if (loadPrefs().layers?.htfLevels === true) await refreshHigherLevels();
      return;
    }

    // Any other timeframe: read-only GETs at the dashboard's own decision
    // instant, so the viewed chart can never run ahead of the verdict.
    let candlesPayload = null;
    let candlesError = null;
    try {
      candlesPayload = await api.candles({
        symbol,
        timeframe,
        limit: CHART_CANDLE_LIMIT,
        end_time: decisionAsOf || undefined,
      });
    } catch (error) {
      candlesError = error;
    }
    if (generation !== viewed.generation || viewed.cancelled) return;
    const timeframeEcho = candlesPayload?.timeframe;
    if (candlesError || timeframeEcho !== timeframe) {
      viewed.overlays = emptyOverlays();
      viewed.evidence = { available: false, reason: "no candles for this timeframe" };
      if (handleRef.current) setCandles(handleRef.current, []);
      const detail = candlesError
        ? `${candlesError.message} No substitute data is shown.`
        : `The backend returned ${timeframeEcho || "unlabelled"} data for a ${label} request; it is not shown.`;
      showChartOverlay(chartEmpty(`${label} chart unavailable`, detail));
      viewNote.textContent = `${label} stored candles unavailable — nothing rendered in their place.`;
      return;
    }
    const fetchedRows = Array.isArray(candlesPayload.candles) ? candlesPayload.candles : [];
    if (!toChartCandles(fetchedRows).length) {
      viewed.overlays = emptyOverlays();
      viewed.evidence = { available: false, reason: "no stored closed candles at this decision time" };
      if (handleRef.current) setCandles(handleRef.current, []);
      const returned = Number.isInteger(candlesPayload.returned_count) ? candlesPayload.returned_count : fetchedRows.length;
      showChartOverlay(chartEmpty(
        `${label} data unavailable`,
        `No stored ${label} closed candles at this decision time (returned ${returned}). No substitute data is shown.`,
      ));
      viewNote.textContent = `No stored ${label} closed candles at this decision time — the chart is honestly empty.`;
      return;
    }
    const handle = ensureChart();
    if (!handle || generation !== viewed.generation || viewed.cancelled) return;
    viewed.candleRows = fetchedRows;
    setCandles(handle, fetchedRows);
    startForming(timeframe, fetchedRows);

    // Evidence for this timeframe, computed by the backend at the same instant.
    let evidencePayload = null;
    let evidenceError = null;
    try {
      evidencePayload = await api.annotations({ symbol, timeframe, as_of: decisionAsOf || undefined });
    } catch (error) {
      evidenceError = error;
    }
    if (generation !== viewed.generation || viewed.cancelled) return;
    viewed.evidence = evidenceError
      ? { available: false, reason: `${evidenceError.message}` }
      : evidencePayload && typeof evidencePayload === "object"
        ? evidencePayload
        : { available: false, reason: "the backend returned no evidence" };
    renderEvidence();

    let structurePayload = null;
    let structureError = null;
    try {
      structurePayload = await api.structure({ symbol, timeframe, as_of: decisionAsOf || undefined });
    } catch (error) {
      structureError = error;
    }
    if (generation !== viewed.generation || viewed.cancelled) return;
    if (structureError || structurePayload?.timeframe !== timeframe) {
      viewed.overlays = emptyOverlays();
      applyViewedOverlays();
      renderEvidence();
      viewNote.textContent = structureError
        ? `${label} candles shown · ${label} structure unavailable (${structureError.message}) — levels hidden, candles only.`
        : `${label} candles shown · the backend returned ${structurePayload?.timeframe || "unlabelled"} structure for a ${label} request — levels hidden, candles only.`;
      return;
    }
    viewed.overlays = overlaysForViewedTimeframe({ dashboard, viewedTimeframe: timeframe, structure: structurePayload });
    applyViewedOverlays();
    const zoneCount = Array.isArray(viewed.overlays.zones) ? viewed.overlays.zones.length : 0;
    const returned = Number.isInteger(candlesPayload.returned_count) ? candlesPayload.returned_count : fetchedRows.length;
    viewNote.textContent = `${label} stored closed candles (${returned}) · ${label} structure (${zoneCount} zone(s)${viewed.overlays.range ? " · range shown" : ""}) · plan levels from the engine plan · liquidity overlays unavailable on this view.`;
    if (loadPrefs().layers?.htfLevels === true) await refreshHigherLevels();
  }

  const card = el("section", {
    class: "card terminal-card terminal-chart-card",
    "aria-label": `${symbol} price chart`,
  }, [
    el("div", { class: "card-head" }, [
      el("div", {}, [headingTitle, headingMeta]),
      toolbar,
    ]),
    formingStatus,
    confirmedStatus,
    ohlcLine,
    host,
    evidenceMeta,
    itemList,
    markerKey,
    explainPanel,
    viewNote,
  ]);

  refreshLayerButtons();
  updateLiquidityToggle();
  if (!validCandles.length) {
    showChartOverlay(chartEmpty(
      "Candle data unavailable",
      rows.length
        ? "The stored candle payload contains no renderable rows. No substitute data is shown."
        : "No stored closed candles were returned for this symbol and timeframe.",
    ));
  }

  return {
    node: card,
    mount() {
      if (!validCandles.length) {
        renderEvidence();
        return;
      }
      const handle = ensureChart();
      if (!handle) return;
      setCandles(handle, rows);
      startForming(engineTimeframe, rows);
      applyViewedOverlays();
      renderEvidence();
      if (loadPrefs().layers?.htfLevels === true) void refreshHigherLevels();
    },
    destroy() {
      stopForming();
      viewed.cancelled = true;
      viewed.generation += 1;
      eventRelease?.();
      eventRelease = null;
      destroyPriceChart(handleRef.current);
      handleRef.current = null;
    },
  };
}


function decisionCard(dashboard) {
  const availability = decisionAvailability(dashboard);
  const latest = dashboard?.journal?.latest_decision || null;
  const buttons = ["ACCEPTED", "REJECTED", "SKIPPED"].map((decision) =>
    el("button", {
      class: `btn btn-${decision === "ACCEPTED" ? "accept" : decision === "REJECTED" ? "reject" : "skip"}`,
      type: "button",
      disabled: !availability.enabled || undefined,
      title: DECISION_MEANINGS[decision],
      "aria-label": `${DECISION_LABELS[decision]} — ${DECISION_MEANINGS[decision]}`,
      onclick: () => openDecisionConfirmation(dashboard, decision),
    }, [DECISION_LABELS[decision]]),
  );
  const summary = latest
    ? `Journal decision · latest ${decisionText(latest.decision)}`
    : "Record a journal decision (optional)";

  return el("details", { class: "details-disclosure journal-decision" }, [
    el("summary", { text: summary }),
    el("div", { class: "details-body" }, [
      el("div", { class: "decision-buttons" }, buttons),
      el("div", {
        class: "decision-note",
        role: "note",
        text: availability.enabled
          ? "There is no default. This appends a paper journal decision only — it never places an order. Corrections append; prior history is preserved."
          : `Controls unavailable: ${disabledReasonText(availability.reason)}`,
      }),
    ]),
  ]);
}

function openDecisionConfirmation(dashboard, decision) {
  const summary = confirmationSummary(dashboard);
  let submitting = false;
  const row = (label, value) => el("div", { class: "kv" }, [
    el("span", { class: "k", text: label }),
    el("span", { class: "v mono" }, [typeof value === "string" ? value : value]),
  ]);
  const levelCell = (value) => {
    if (isMissing(value)) return "UNKNOWN";
    return roundedSpan(value, 2);
  };
  const reasonInput = el("textarea", {
    class: "input-inline",
    rows: 2,
    maxlength: 2000,
    placeholder: "Optional note (stored verbatim, never interpreted)",
    style: { width: "100%", resize: "vertical" },
  });
  const confirmButton = el("button", {
    class: "btn btn-primary",
    type: "button",
    text: `Record ${DECISION_LABELS[decision]}`,
  });
  const cancelButton = el("button", { class: "btn btn-quiet", type: "button", text: "Cancel" });
  const content = el("div", {}, [
    el("h3", { text: `${DECISION_LABELS[decision]} this proposal?` }),
    el("p", {
      style: { color: "var(--text-dim)", fontSize: "13.5px" },
      text: DECISION_MEANINGS[decision],
    }),
    el("div", { class: "kv", style: { marginBottom: "12px" } }, [
      row("Snapshot as-of", formatUtc(summary.asOf)),
      row("Symbol / timeframe", `${summary.symbol} · ${summary.timeframe}`),
      row("Setup state", `${setupStateLabel(summary.setupState)} · ${familyLabel(summary.family)} · ${directionLabel(summary.direction)}`),
      row("Plan state", planStateLabel(summary.planState)),
      row("Entry", levelCell(summary.entry)),
      row("Stop", levelCell(summary.stop)),
      row("Targets", summary.targets.length
        ? el("span", {}, summary.targets.flatMap((target, index) => (
            index === 0 ? [levelCell(target)] : [", ", levelCell(target)]
          )))
        : "UNKNOWN"),
      row("Setup id", summary.setupId ? shortId(summary.setupId, 16) : "UNKNOWN"),
    ]),
    reasonInput,
    el("div", { class: "modal-actions" }, [cancelButton, confirmButton]),
  ]);

  const close = openModal(content);
  cancelButton.onclick = close;
  confirmButton.onclick = async () => {
    if (submitting) return;
    submitting = true;
    confirmButton.disabled = true;
    confirmButton.textContent = "Recording…";
    try {
      const body = buildDecisionRequest({ decision, dashboard, reason: reasonInput.value });
      const result = await api.dashboardDecision(body);
      close();
      if (result.duplicate) {
        toast("This identical decision was already recorded — nothing was appended.", "neutral");
      } else {
        toast(`Decision ${result.decision.decision} recorded in the journal.`, "green");
      }
      const view = document.getElementById("view");
      if (view) renderDashboard(view);
    } catch (error) {
      submitting = false;
      confirmButton.disabled = false;
      confirmButton.textContent = `Record ${DECISION_LABELS[decision]}`;
      toast(error.message, "red", 6500);
    }
  };
}


function planLevel(label, value, tone = null, { numeric = true, decimals = 2 } = {}) {
  const content = numeric ? roundedSpan(value, decimals) : el("span", { text: displayOrUnknown(value) });
  return el("div", { class: "plan-level", dataset: tone ? { tone } : {} }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value" }, [content]),
  ]);
}

function planningReason(dashboard) {
  const planning = dashboard?.planning || {};
  const reasons = backendReasons(planning.reasons);
  if (reasons.length) return planRefusalSentence(reasons);
  if (typeof planning.state_detail === "string" && planning.state_detail.trim()) {
    const detail = planReasonText(planning.state_detail);
    return detail ? `The planner could not build a plan: ${detail}.` : null;
  }
  return null;
}

function planCard(dashboard) {
  const plan = hasValidTradePlan(dashboard) ? dashboard.plan : null;
  const planning = dashboard?.planning || {};
  const body = [];
  if (!plan) {
    body.push(el("div", { class: "plan-empty", text: "No qualified trade plan right now." }));
    const reason = planningReason(dashboard);
    if (reason) body.push(el("div", { class: "plan-reason", text: reason }));
  } else {
    body.push(
      el("div", { class: "plan-levels" }, [
        planLevel("Direction", directionLabel(plan.direction), null, { numeric: false }),
        planLevel("Entry", plan.entry?.value, "green"),
        planLevel("Stop", plan.stop?.value, "red"),
        planLevel("Invalidation", plan.invalidation?.value, "amber"),
        planLevel("Risk / unit", plan.risk_per_unit),
      ]),
    );
    const targets = Array.isArray(plan.targets) ? plan.targets : [];
    const targetRows = targets.map((target, index) => el("div", { class: "plan-target-row" }, [
      el("span", { class: "target-index", text: `T${index + 1}` }),
      el("span", { class: "target-value" }, [roundedSpan(target?.level?.value, 2)]),
      el("span", { class: "target-r" }, [roundedRMultiple(target?.r_multiple)]),
    ]));
    body.push(el("div", { class: "plan-targets" }, [
      el("div", { class: "plan-targets-title", text: "Targets · risk / reward" }),
      ...(targetRows.length
        ? targetRows
        : [el("div", { class: "plan-reason", text: "Target levels unavailable from the backend." })]),
    ]));
    body.push(el("div", { class: "plan-caveat", text: "Deterministic paper plan only · not an order, fill, position, or profit." }));
  }
  body.push(technicalDetails("Technical plan record", [
    `planning state: ${planning.state || "UNKNOWN"}`,
    ...backendReasons(planning.reasons).map((reason) => `reason: ${reason}`),
    ...(Array.isArray(planning.missing_inputs) ? planning.missing_inputs : [])
      .filter((value) => typeof value === "string" && value.trim())
      .map((value) => `missing input: ${value.trim()}`),
    planning.state_detail ? `state detail: ${planning.state_detail}` : null,
    plan?.id ? `plan id: ${plan.id}` : null,
  ]));

  return el("section", { class: "card terminal-card plan-card", "aria-label": "Trade plan" }, [
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "Trade plan" })]),
    ...body,
  ]);
}

function roundedRMultiple(value) {
  if (isMissing(value)) return el("span", { text: "R/R UNKNOWN" });
  const { display, raw } = displayRounded(value, 2);
  return el("span", { text: `${display} R`, title: raw === null ? undefined : `Exact: ${raw}` });
}

function scenarioText(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function trendSummaryText(trend) {
  if (!trend || typeof trend !== "object") return "UNKNOWN";
  if (trend.sufficient !== true) {
    return `Unknown (${trendReasonText(trend.reason)})`;
  }
  const rawDirection = directionLabel(trend.direction);
  const direction = rawDirection.charAt(0).toUpperCase() + rawDirection.slice(1);
  return `${direction} · ${trendTransitionText(trend.transition)} · ${trendMomentumText(trend.momentum)}`;
}

function volatilitySummaryText(volatility) {
  if (!volatility || typeof volatility !== "object") return "UNKNOWN";
  if (volatility.available !== true) {
    return `Unknown (${unavailableReasonText(volatility.reason)})`;
  }
  const value = displayRounded(volatility.atr_percent_of_price, 2).display;
  const label = metricLabelText(volatility.direction?.label);
  if (label === "trend unknown") return `${value}% of price · trend unknown`;
  return `${value}% of price · ${label}`;
}

function volumeSummaryText(volume) {
  if (!volume || typeof volume !== "object") return "UNKNOWN";
  if (volume.sufficient !== true) {
    return `Unknown (${unavailableReasonText(volume.reason)})`;
  }
  const value = displayRounded(volume.relative_volume, 2).display;
  const label = metricLabelText(volume.direction?.label);
  if (label === "trend unknown") return `${value}x average · trend unknown`;
  return `${value}x average · ${label}`;
}

function rangeSummaryText(range) {
  if (!range || typeof range !== "object") return "UNKNOWN";
  const transition = rangeTransitionText(range.transition);
  if (range.active !== true) return `No active range (${transition})`;
  const detected = range.detected && typeof range.detected === "object" ? range.detected : null;
  if (!detected) return "Active range reported without detected levels";
  const low = displayRounded(detected.range_low, 2).display;
  const high = displayRounded(detected.range_high, 2).display;
  return `${low} → ${high} · ${transition}`;
}

function zoneBandNode(zone) {
  if (!zone || typeof zone !== "object") return null;
  return el("span", {}, [
    roundedSpan(zone.band_low, 2),
    "–",
    roundedSpan(zone.band_high, 2),
    ` (${zone.touch_count ?? "?"} touches)`,
  ]);
}

function equalLevelNode(entry) {
  if (!entry || typeof entry !== "object") return null;
  const kind = entry.type === "equal_high" ? "equal highs" : entry.type === "equal_low" ? "equal lows" : "level";
  return el("span", {}, [
    roundedSpan(entry.level, 2),
    ` (${kind} ×${entry.member_count ?? "?"})`,
  ]);
}

function marketNowUnavailable(reason) {
  return el("section", { class: "card terminal-card", "aria-label": "Market now" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Market now" }),
      el("span", { class: "card-hint", text: "backend facts · this close" }),
    ]),
    el("div", { class: "plan-reason", text: backendNoticeText(reason, "Market-state facts unavailable from the backend.") }),
  ]);
}

function eventCountsLine(events) {
  const count = (group) => (group && typeof group === "object" && Number.isInteger(group.count) ? group.count : "?");
  const retests = events.retests && typeof events.retests === "object" ? events.retests : {};
  const patterns = events.chart_patterns && typeof events.chart_patterns === "object" ? events.chart_patterns : {};
  return `breakouts ${count(events.breakouts)} · failed breakouts ${count(events.failed_breakouts)} · ` +
    `sweeps ${count(events.sweeps)} · retests ${count(events.retests)} ` +
    `(held ${retests.held_count ?? "?"} / failed ${retests.failed_count ?? "?"}) · ` +
    `chart patterns ${count(events.chart_patterns)} (${patterns.confirmed_count ?? "?"} confirmed)`;
}

function latestEventLine(label, latest, detail) {
  if (!latest || typeof latest !== "object") return null;
  return el("div", {
    class: "chart-note",
    text: `${label}: ${detail} at ${formatUtc(latest.known_at)}`,
  });
}

function breakoutChips(breakout) {
  const chips = [];
  for (const item of Array.isArray(breakout.attempts) ? breakout.attempts : []) {
    chips.push(el("span", {}, [`breakout ${directionLabel(item.direction)} @ `, roundedSpan(item.close, 2)]));
  }
  for (const item of Array.isArray(breakout.acceptances) ? breakout.acceptances : []) {
    chips.push(`held retest ${directionLabel(item.direction)}`);
  }
  for (const item of Array.isArray(breakout.rejections) ? breakout.rejections : []) {
    chips.push(rejectionKindText(item.kind));
  }
  for (const item of Array.isArray(breakout.sweeps) ? breakout.sweeps : []) {
    chips.push(el("span", {}, [`sweep ${directionLabel(item.direction)} → `, roundedSpan(item.reclaim_close, 2)]));
  }
  return chips;
}

function marketNowCard(dashboard) {
  const state = dashboard?.market_state;
  if (!state || typeof state !== "object" || state.available !== true) {
    const reason = state && typeof state === "object" ? scenarioText(state.reason) : null;
    return marketNowUnavailable(reason);
  }
  const levels = state.levels && typeof state.levels === "object" ? state.levels : {};
  const events = state.events && typeof state.events === "object" ? state.events : {};
  const breakout = state.breakout_state && typeof state.breakout_state === "object" ? state.breakout_state : {};
  const htf = state.higher_timeframes && typeof state.higher_timeframes === "object" ? state.higher_timeframes : {};
  const chips = breakoutChips(breakout);
  const support = zoneBandNode(levels.nearest_support);
  const resistance = zoneBandNode(levels.nearest_resistance);
  const below = equalLevelNode(levels.nearest_level_below);
  const above = equalLevelNode(levels.nearest_level_above);
  const children = [
    el("div", { class: "performance-summary" }, [
      performanceMetric("Trend", trendSummaryText(state.trend)),
      performanceMetric("Volatility", volatilitySummaryText(state.volatility)),
      performanceMetric("Volume", volumeSummaryText(state.volume)),
      performanceMetric("Range", rangeSummaryText(state.range)),
    ]),
    el("div", { class: "chart-note" }, [
      "Nearest levels: support ",
      support || "none in range",
      " · resistance ",
      resistance || "none in range",
      " · equal below ",
      below || "none",
      " · equal above ",
      above || "none",
      ` · ${levels.zone_count ?? "?"} zones · ${levels.equal_level_count ?? "?"} equal levels`,
    ]),
    el("div", { class: "chart-note", text: `Event catalog: ${eventCountsLine(events)}` }),
  ];
  const latestBreakout = events.breakouts?.latest;
  if (latestBreakout) {
    children.push(latestEventLine(
      "Latest breakout",
      latestBreakout,
      `${directionLabel(latestBreakout.direction)} of ${referenceTypeText(latestBreakout.reference_type)}`,
    ));
  }
  const latestSweep = events.sweeps?.latest;
  if (latestSweep) children.push(latestEventLine("Latest sweep", latestSweep, directionLabel(latestSweep.direction)));
  const freshCounts = ["attempts", "acceptances", "rejections", "sweeps"]
    .map((key) => (Array.isArray(breakout[key]) ? breakout[key].length : "?"));
  children.push(el("div", {
    class: "chart-note",
    text: `Fresh at this close: ${freshCounts[0]} attempt(s) · ${freshCounts[1]} acceptance(s) · ` +
      `${freshCounts[2]} rejection(s) · ${freshCounts[3]} sweep(s)`,
  }));
  if (chips.length) {
    const chipSpans = chips.map((chip) => (
      typeof chip === "string"
        ? el("span", { class: "outcome-chip", text: chip })
        : el("span", { class: "outcome-chip" }, [chip])
    ));
    children.push(el("div", { class: "outcome-breakdown", "aria-label": "Fresh breakout activity" }, chipSpans));
  }
  // Higher timeframes are never requested with dashboard defaults, so an empty
  // request list shows nothing instead of a permanent "not requested" note.
  const requested = Array.isArray(htf.requested) ? htf.requested : [];
  if (requested.length) {
    const contexts = htf.contexts && typeof htf.contexts === "object" ? htf.contexts : {};
    for (const timeframe of requested) {
      const context = contexts[timeframe] && typeof contexts[timeframe] === "object" ? contexts[timeframe] : {};
      const trend = directionLabel(context.trend).toLowerCase();
      children.push(el("div", {
        class: "chart-note",
        text: `${timeframe}: trend ${trend}` +
          (context.available === false ? " (unavailable)" : "") +
          (context.reason ? ` — ${unavailableReasonText(context.reason)}` : ""),
      }));
    }
  }
  return el("section", { class: "card terminal-card", "aria-label": "Market now" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Market now" }),
      el("span", { class: "card-hint", text: "backend facts · this close" }),
    ]),
    ...children,
  ]);
}

function explanationCard(dashboard) {
  const explanation = dashboard?.explanation;
  const sections = Array.isArray(explanation?.sections) ? explanation.sections : [];
  const limitations = Array.isArray(explanation?.limitations) ? explanation.limitations : [];
  // The collapsed label is plain English; the verbatim backend headline and
  // sections stay inside as the Level-3 technical record.
  const meta = dashboard?.meta || {};
  const summaryLine = explanation && typeof explanation === "object" && explanation.available !== false
    ? `Technical record · ${meta.symbol || "UNKNOWN"} · ${timeframeLabel(meta.timeframe)} · ${setupStateLabel(dashboard?.qualification?.state)}`
    : "Grounded explanation";
  const body = [];
  if (scenarioText(explanation?.headline)) {
    body.push(el("div", { class: "chart-note", text: `Backend headline: ${explanation.headline.trim()}` }));
  }
  if (explanation && typeof explanation === "object" && explanation.available === false) {
    const message = explanation.error && typeof explanation.error === "object"
      ? scenarioText(explanation.error.message)
      : null;
    body.push(el("div", {
      class: "plan-reason",
      text: message || "No grounded explanation was supplied.",
    }));
  } else if (!sections.length) {
    body.push(el("div", {
      class: "plan-reason",
      text: "No grounded explanation sections were supplied.",
    }));
  } else {
    for (const section of sections) {
      body.push(el("div", { class: "terminal-evidence-item" }, [
        el("div", { class: "evidence-meta", text: section.title || `Section ${section.number ?? "?"}` }),
        el("div", { class: "evidence-reason", text: section.text || "No text supplied." }),
      ]));
    }
    for (const limitation of limitations) {
      body.push(el("div", { class: "performance-caveat", text: String(limitation) }));
    }
    body.push(el("div", {
      class: "chart-note",
      text: `Grounded locally by ${explanation.renderer_id || "unknown renderer"} ` +
        `(${explanation.renderer_version || "unknown version"}) · provenance ${explanation.provenance || "UNKNOWN"}`,
    }));
  }
  return el("details", { class: "card terminal-card expandable", "aria-label": "Grounded explanation" }, [
    el("summary", { text: summaryLine }),
    el("div", { class: "details-body" }, body),
  ]);
}

// ---------------------------------------------------------------------------
// Decision centre: three lanes that are never merged.
//   1. Observation  — what BRAIN's qualification currently says (backend).
//   2. Proposed paper plan — the deterministic plan, only when the backend built one.
//   3. Actual orders — none. The backend reports execution disabled.
// ---------------------------------------------------------------------------

/** Real status for the decision, from backend fields only. Exported for tests. */
export function decisionCentreStatus(dashboard) {
  const freshness = dashboard?.freshness;
  const qualification = dashboard?.qualification || {};
  const candles = Array.isArray(dashboard?.market?.candles) ? dashboard.market.candles : [];
  if (candles.length === 0) return { key: "waiting", label: "WAITING FOR DATA", tone: "neutral" };
  if (qualification.available !== true) return { key: "unavailable", label: "UNAVAILABLE", tone: "neutral" };
  if (freshness && freshness.status === "STALE") return { key: "stale", label: "STALE — DECISION MAY BE OUT OF DATE", tone: "amber" };
  if (freshness && freshness.status === "HISTORICAL") return { key: "historical", label: "HISTORICAL INSTANT", tone: "blue" };
  if (qualification.state === "QUALIFIED") {
    return hasValidTradePlan(dashboard)
      ? { key: "qualified-plan", label: "QUALIFIED · PLAN CALCULATED", tone: "green" }
      : { key: "qualified", label: "QUALIFIED · NO PLAN", tone: "amber" };
  }
  if (qualification.state === "WATCH") return { key: "watching", label: "WATCHING · WAITING FOR CONFIRMATION", tone: "amber" };
  if (qualification.state === "NO_SETUP") return { key: "no-setup", label: "NO QUALIFIED SETUP", tone: "neutral" };
  return { key: "unavailable", label: "UNAVAILABLE", tone: "neutral" };
}


/** Paper observations: active and completed plans, outcome status, ambiguous and unscored counts, with limitations. */
export function paperObservationsViewModel(forward) {
  const model = performanceViewModel(forward);
  const metrics = model.combinedAvailable ? forward?.report?.metrics || null : null;
  const plans = Array.isArray(forward?.observations?.paper_plans) ? forward.observations.paper_plans : [];
  return {
    available: Boolean(forward),
    paperPlans: model.paperPlans,
    active: model.unresolvedOutcomes,
    completed: model.resolvedOutcomes,
    ambiguous: integerOrNull(metrics?.ambiguous_count),
    unscored: integerOrNull(metrics?.incomplete_data_count),
    withOutcome: integerOrNull(metrics?.paper_plans_with_outcome),
    averageObservedR: model.averageObservedR,
    eligibleRSample: model.eligibleRSample,
    consideredRSample: model.consideredRSample,
    versionSeparated: model.versionSeparated,
    limitations: Array.isArray(forward?.limitations) ? forward.limitations.filter((item) => typeof item === "string") : [],
    disclaimer: model.disclaimer,
    plans: plans.slice(0, 8).map((plan) => {
      const outcome = plan.latest_outcome && typeof plan.latest_outcome === "object" ? plan.latest_outcome : null;
      const statusValue = outcome?.observation?.status ?? outcome?.status ?? null;
      return {
        id: plan.paper_plan_id || null,
        planTime: plan.plan_as_of || null,
        direction: plan.direction || null,
        entry: plan.entry ?? null,
        stop: plan.stop ?? null,
        targets: Array.isArray(plan.targets) ? plan.targets : [],
        status: statusValue,
        outcomeVersions: integerOrNull(plan.outcome_version_count),
      };
    }),
  };
}

function paperObservationsCard(forward) {
  const model = paperObservationsViewModel(forward);
  const count = (value) => (value === null ? "UNKNOWN" : String(value));
  const summary = el("div", { class: "paper-summary" }, [
    performanceMetric("Paper plans", count(model.paperPlans)),
    performanceMetric("Active (unresolved)", count(model.active)),
    performanceMetric("Completed", count(model.completed)),
    performanceMetric("Ambiguous", count(model.ambiguous), "Outcome could not be ordered from candles"),
    performanceMetric("Unscored · incomplete data", count(model.unscored), "Not scored; excluded from R"),
    performanceMetric("Sample for R", model.eligibleRSample === null || model.consideredRSample === null
      ? "UNKNOWN"
      : `${model.eligibleRSample} / ${model.consideredRSample}`, "eligible / considered"),
  ]);
  const rows = model.plans.map((plan) => el("tr", {}, [
    el("td", { class: "mono", text: formatUtc(plan.planTime) }),
    el("td", { text: directionLabel(plan.direction) }),
    el("td", { class: "mono", text: displayOrUnknown(plan.entry) }),
    el("td", { class: "mono", text: displayOrUnknown(plan.stop) }),
    el("td", { class: "mono", text: plan.targets.length ? plan.targets.map((t) => displayOrUnknown(t)).join(" / ") : "UNKNOWN" }),
    el("td", { class: "state-cell", text: plan.status ? outcomeStatusText(plan.status) : "No outcome recorded yet" }),
  ]));
  let body;
  if (!model.available) {
    body = emptyState("Paper observation data unavailable", "Forward report did not load, so every figure stays UNKNOWN.");
  } else if (!model.plans.length) {
    body = emptyState("No paper plans recorded yet", "No paper plan has been recorded for this symbol and timeframe.");
  } else {
    body = el("div", { class: "recent-table-wrap" }, [
      el("table", { class: "recent-table paper-table" }, [
        el("thead", {}, [el("tr", {}, [
          el("th", { scope: "col", text: "Plan time" }),
          el("th", { scope: "col", text: "Direction" }),
          el("th", { scope: "col", text: "Entry" }),
          el("th", { scope: "col", text: "Stop" }),
          el("th", { scope: "col", text: "Targets" }),
          el("th", { scope: "col", text: "Outcome" }),
        ])]),
        el("tbody", {}, rows),
      ]),
    ]);
  }
  return el("section", { class: "card terminal-card paper-observations", "aria-label": "Paper observations" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Paper observations" }),
      el("span", { class: "card-hint", text: forward?.paper_label || "PAPER OBSERVATION — NO REAL ORDER" }),
    ]),
    summary,
    body,
    model.averageObservedR === null
      ? null
      : el("p", { class: "performance-caveat", text: `Mean observed R ${displayRounded(model.averageObservedR, 2).display} · descriptive OHLC observation, not expectancy or realised P&L.` }),
    model.limitations.length
      ? el("details", { class: "paper-limitations" }, [
          el("summary", { text: `Limitations (${model.limitations.length})` }),
          el("ul", {}, model.limitations.map((item) => el("li", { text: item }))),
        ])
      : null,
    el("p", { class: "performance-caveat", text: model.disclaimer }),
  ]);
}

function recentDecisionsCard(forward) {
  const observations = Array.isArray(forward?.observations?.observations)
    ? forward.observations.observations.slice(0, 6)
    : [];
  const body = observations.length
    ? el("div", { class: "recent-table-wrap" }, [
        el("table", { class: "recent-table" }, [
          el("thead", {}, [el("tr", {}, [
            el("th", { scope: "col", text: "Time" }),
            el("th", { scope: "col", text: "Setup" }),
            el("th", { scope: "col", text: "Direction" }),
            el("th", { scope: "col", text: "State" }),
          ])]),
          el("tbody", {}, observations.map((item) => {
            const states = [
              setupStateLabel(item.setup_state),
              item.plan_state ? planStateLabel(item.plan_state) : null,
            ].filter(Boolean);
            return el("tr", {}, [
              el("td", { class: "mono", text: formatUtc(item.as_of) }),
              el("td", { text: familyLabel(item.setup_family) }),
              el("td", { text: directionLabel(item.setup_direction) }),
              el("td", { class: "state-cell", text: states.join(" · ") }),
            ]);
          })),
        ]),
      ])
    : emptyState(
        "No closed-candle observations available",
        forward ? "No recorded forward observations were returned." : "Forward-observation data is unavailable.",
      );

  return el("section", { class: "card terminal-card", "aria-label": "Recent closed-candle decisions" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Recent decisions" }),
      el("span", { class: "card-hint", text: "Recorded closed-candle observations" }),
    ]),
    body,
  ]);
}

function performanceMetric(label, value, note = null) {
  return el("div", { class: "performance-metric" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }),
    note ? el("div", { class: "note", text: note }) : null,
  ]);
}

function versionCohortDetails(model) {
  if (!model.versionCohorts.length) return null;
  return el("details", { class: "details-disclosure" }, [
    el("summary", { text: "Version-separated cohorts · never combined" }),
    el("div", { class: "details-body" }, model.versionCohorts.map((cohort) => {
      const metrics = cohort.metrics || {};
      const distribution = metrics.raw_observational_r || {};
      return el("div", { class: "system-detail" }, [
        el("div", { class: "label", text: cohort.version_fingerprint || "Version UNKNOWN" }),
        el("div", { class: "value", text: `Paper plans ${displayOrUnknown(metrics.paper_plan_count)} · settled ${displayOrUnknown(metrics.completed_count)} · eligible R ${displayOrUnknown(distribution.sample_size)} / ${displayOrUnknown(distribution.records_considered)} (${metricStatusText(distribution.status)}) · mean observed R ${displayRounded(distribution.average, 2).display}` }),
      ]);
    })),
  ]);
}

function rateObservationText(rate) {
  const numerator = integerOrNull(rate?.numerator);
  const denominator = integerOrNull(rate?.denominator);
  if (numerator === null || denominator === null) return "UNKNOWN";
  const percentage = isMissing(rate.percentage) ? null : displayRounded(rate.percentage, 1).display;
  const measured = percentage === null ? "" : `${percentage}% · `;
  return `${measured}${numerator}/${denominator} · ${metricStatusText(rate?.status)}`;
}

function outcomeRateDetails(model) {
  if (!model.rates.length) return null;
  return el("details", { class: "details-disclosure" }, [
    el("summary", { text: "Outcome observations · exact denominators, not win rates" }),
    el("div", { class: "details-body" }, model.rates.map(({ label, metric }) => el("div", { class: "performance-rate-row" }, [
      el("div", { class: "performance-rate-head" }, [
        el("span", { class: "label", text: label }),
        el("span", { class: "value mono", text: rateObservationText(metric) }),
      ]),
      el("div", { class: "note", text: metric.denominator_definition || "Denominator definition unavailable." }),
    ]))),
  ]);
}

function rDistributionDetails(model) {
  const distributions = [
    ["Raw observational R", model.observedRDistribution],
    ["Friction-adjusted hypothetical R", model.frictionAdjustedRDistribution],
  ].filter(([, distribution]) => distribution && typeof distribution === "object");
  if (!distributions.length) return null;
  const detailRows = distributions.map(([label, distribution]) => el("div", { class: "performance-rate-row" }, [
    el("div", { class: "performance-rate-head" }, [
      el("span", { class: "label", text: label }),
      el("span", { class: "value mono", text: `eligible ${displayOrUnknown(distribution.sample_size)} / ${displayOrUnknown(distribution.records_considered)} · ${metricStatusText(distribution.status)}` }),
    ]),
    el("div", { class: "note", text: `mean ${displayRounded(distribution.average, 2).display} · median ${displayRounded(distribution.median, 2).display} · min ${displayRounded(distribution.minimum, 2).display} · max ${displayRounded(distribution.maximum, 2).display}` }),
    el("div", { class: "note", text: distribution.definition || "Metric definition unavailable." }),
    (Array.isArray(distribution.excluded) && distribution.excluded.length)
      ? el("div", { class: "note", text: `Excluded: ${distribution.excluded.map((item) => `${displayRounded(item.value, 2).display} ×${item.count}`).join(" · ")}` })
      : null,
  ]));
  if (model.frictionAssumptions) {
    const friction = model.frictionAssumptions;
    detailRows.push(el("div", { class: "performance-rate-row" }, [
      el("div", { class: "performance-rate-head" }, [
        el("span", { class: "label", text: "Reported friction assumptions" }),
        el("span", { class: "value mono", text: friction.version || "UNKNOWN" }),
      ]),
      el("div", { class: "note", text: `Fee ${displayOrUnknown(friction.fee_bps)} bps · entry slippage ${displayOrUnknown(friction.entry_slippage_bps)} bps · exit slippage ${displayOrUnknown(friction.exit_slippage_bps)} bps` }),
    ]));
  }
  return el("details", { class: "details-disclosure" }, [
    el("summary", { text: "R sample definitions and ranges" }),
    el("div", { class: "details-body" }, detailRows),
  ]);
}

function historicalValidationDetails(meta) {
  const content = el("div", { class: "details-body" }, [
    el("div", { class: "historical-load-note", text: "Historical validation loads from the existing read-only API when expanded." }),
  ]);
  const details = el("details", { class: "details-disclosure" }, [
    el("summary", { text: "Historical validation · separate from forward observations" }),
    content,
  ]);
  let requested = false;

  details.addEventListener("toggle", async () => {
    if (!details.open || requested) return;
    requested = true;
    clearNode(content).append(spinner("Loading the existing historical validation report…"));
    try {
      const report = await api.validation({
        symbol: meta?.symbol || undefined,
        timeframe: meta?.timeframe || undefined,
      });
      clearNode(content).append(historicalReportNode(report));
    } catch (error) {
      clearNode(content).append(errorState(error.message));
    }
  });
  return details;
}

function historicalReportNode(report) {
  const cohort = report?.out_of_sample || null;
  const metrics = cohort?.metrics || null;
  if (!metrics) {
    return el("div", { class: "historical-load-note", text: "No out-of-sample cohort is available in the historical report." });
  }
  const r = metrics.raw_observational_r || {};
  return el("div", {}, [
    el("div", { class: "performance-summary" }, [
      performanceMetric("OOS replay records", displayOrUnknown(metrics.total_records)),
      performanceMetric("Settled outcomes", displayOrUnknown(metrics.completed_count)),
      performanceMetric("Eligible / considered R", `${displayOrUnknown(r.sample_size)} / ${displayOrUnknown(r.records_considered)}`, metricStatusText(r.status)),
      performanceMetric("Mean observed R", displayRounded(r.average, 2).display, "Backend distribution · not expectancy"),
    ]),
    el("div", { class: "performance-caveat", text: "Historical validation is a separate stored-candle replay. It is not paper execution, realised profit, or evidence of future profitability." }),
    (report.limitations || []).length
      ? el("div", { class: "performance-caveat", text: report.limitations.join(" ") })
      : null,
  ]);
}

function performanceCard(forward, meta) {
  const model = performanceViewModel(forward);
  const rSample = model.eligibleRSample === null || model.consideredRSample === null
    ? "UNKNOWN"
    : `${model.eligibleRSample} / ${model.consideredRSample}`;
  const meanObserved = displayRounded(model.averageObservedR, 2);
  const meanFriction = displayRounded(model.averageFrictionAdjustedR, 2);
  const metrics = [
    performanceMetric("Paper plans", model.paperPlans === null ? "UNKNOWN" : String(model.paperPlans)),
    performanceMetric("Settled outcomes", model.resolvedOutcomes === null ? "UNKNOWN" : String(model.resolvedOutcomes)),
    performanceMetric("Unresolved", model.unresolvedOutcomes === null ? "UNKNOWN" : String(model.unresolvedOutcomes)),
    performanceMetric("Eligible / considered R", rSample, metricStatusText(model.rSampleStatus)),
    performanceMetric("Mean observed R", meanObserved.display, `Descriptive OHLC observation · not expectancy${meanObserved.raw === null ? "" : ` · exact ${meanObserved.raw}`}`),
    performanceMetric("Mean friction-adjusted R", meanFriction.display, `Hypothetical only · not realised P&L${meanFriction.raw === null ? "" : ` · exact ${meanFriction.raw}`}`),
  ];

  const children = [el("div", { class: "performance-summary" }, metrics)];
  if (model.versionSeparated) {
    children.push(el("div", { class: "performance-caveat", role: "note", text: model.combinedUnavailableReason || "Version-separated results are withheld rather than combined." }));
  }

  if (model.outcomeStatusCounts.length && model.combinedAvailable) {
    children.push(
      el("div", {}, [
        el("div", { class: "performance-caveat", text: "Recorded outcome statuses · counts only, not win/loss classifications" }),
        el("div", { class: "outcome-breakdown", "aria-label": "Recorded outcome states" },
          model.outcomeStatusCounts.map((item) => el("span", { class: "outcome-chip", text: `${outcomeStatusText(item.value)}: ${item.count}` })),
        ),
      ]),
    );
  }
  if (model.combinedAvailable) {
    children.push(outcomeRateDetails(model));
    children.push(rDistributionDetails(model));
  }
  children.push(el("div", { class: "performance-caveat", text: "Win/loss classification, win rate, expectancy, drawdown, and realised P&L are unavailable in the existing read-only report." }));
  children.push(el("div", { class: "performance-caveat", text: model.disclaimer }));
  if (!forward) {
    children.push(el("div", { class: "performance-caveat", text: "Forward report unavailable; missing figures remain UNKNOWN." }));
  }
  children.push(versionCohortDetails(model));
  children.push(historicalValidationDetails(meta));

  return el("section", { class: "card terminal-card", "aria-label": "Paper and historical performance" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Measured performance" }),
      el("span", { class: "card-hint", text: "Paper / historical records only" }),
    ]),
    ...children,
  ]);
}

function systemDetail(label, value) {
  return el("div", { class: "system-detail" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }),
  ]);
}

function systemDetailsCard(dashboard, forward) {
  const status = forward?.status || {};
  const market = status.market_data || {};
  const missing = integerOrNull(market.missing_candle_count) ??
    integerOrNull(dashboard?.market?.missing_candle_count);
  const latestStored = market.latest_stored_candle_open || dashboard?.freshness?.latest_stored;
  const expected = market.expected_latest_closed_candle_open || dashboard?.freshness?.expected_latest_closed;
  const health = systemHealthViewModel(dashboard, forward);
  const runner = runnerDetailsViewModel(forward);

  return el("details", { class: "card terminal-card expandable system-details" }, [
    el("summary", { text: "System details" }),
    el("div", { class: "system-details-grid" }, [
      systemDetail("Summary", health.label),
      systemDetail("Data health", healthLabel(market.data_health || dashboard?.freshness?.status)),
      systemDetail("Data health detail", healthDetailText(market.data_health_detail || dashboard?.freshness?.reason)),
      systemDetail("Latest stored candle", latestStored ? formatUtc(latestStored) : "UNKNOWN"),
      systemDetail("Expected latest closed", expected ? formatUtc(expected) : "UNKNOWN"),
      systemDetail("Missing candles", missing === null ? "UNKNOWN" : String(missing)),
      systemDetail("Runner state", runner.state),
      systemDetail("Runner detail", runner.detail),
      systemDetail("Pending catch-up", runner.pending),
      systemDetail("Latest processed close", runner.latestCycle),
      systemDetail("Runner heartbeat", runner.heartbeat),
      systemDetail("Last runner error", runner.lastError),
    ]),
  ]);
}

export function disposeDashboard() {
  renderGeneration += 1;
  if (activeChart) activeChart.destroy();
  activeChart = null;
  // The header is owned by the topbar module now: the dashboard view resets
  // and repopulates it during its own render, and every other route goes
  // through refreshTopbar. Disposal must not blank what it cannot refill.
}

// ---------------------------------------------------------------------------
// Step 13: multi-timeframe ladder (4H context → 1H setup → 15M confirmation →
// 5M execution → overall). The frontend only renders the backend's deterministic
// plain-English payload; it never computes a layer state, a level, or a verdict.
// ---------------------------------------------------------------------------

const LADDER_TONE_CLASS = {
  good: "mtf-tone-good",
  warn: "mtf-tone-warn",
  bad: "mtf-tone-bad",
  neutral: "mtf-tone-neutral",
};

export function ladderViewModel(dashboard) {
  const payload = dashboard?.multi_timeframe;
  if (!payload || typeof payload !== "object" || payload.available !== true) {
    return null;
  }
  const ladder = Array.isArray(payload.ladder) ? payload.ladder : [];
  return {
    overall: hierarchyStatusLabel(payload),
    decisionLabel: hierarchyDecisionLabel(payload),
    alignmentLabel:
      typeof payload.alignment_label === "string" && payload.alignment_label
        ? payload.alignment_label
        : "Unknown",
    counterTrend: payload.counter_trend === true,
    status: typeof payload.status === "string" ? payload.status : "unknown",
    decisionTime: typeof payload.decision_time === "string" ? payload.decision_time : null,
    rows: ladder.map((row) => ({
      role: typeof row?.role === "string" ? row.role : "",
      label: typeof row?.label === "string" && row.label ? row.label : "STEP",
      timeframe: typeof row?.timeframe === "string" ? row.timeframe : "",
      state: typeof row?.state === "string" && row.state ? row.state : "Unknown",
      detail: typeof row?.detail === "string" && row.detail ? row.detail : "",
      boundaryOpen: typeof row?.boundary_open === "string" ? row.boundary_open : null,
      boundaryClose: typeof row?.boundary_close === "string" ? row.boundary_close : null,
      available: row?.available === true,
      tone: LADDER_TONE_CLASS[row?.tone] ? row.tone : "neutral",
    })),
    waitingForText:
      typeof payload.waiting_for_text === "string" && payload.waiting_for_text
        ? payload.waiting_for_text
        : "",
    invalidatedIfText:
      typeof payload.invalidated_if_text === "string" && payload.invalidated_if_text
        ? payload.invalidated_if_text
        : "",
    limitations: Array.isArray(payload.limitations) ? payload.limitations : [],
  };
}

function ladderRow(row) {
  return el("div", { class: `mtf-step mtf-${row.tone}` }, [
    el("div", { class: "mtf-step-head" }, [
      el("span", { class: "mtf-step-label", text: row.label }),
      el("span", { class: "mtf-step-state", text: row.state }),
    ]),
    row.detail
      ? el("div", { class: "mtf-step-detail", text: row.detail })
      : null,
    row.boundaryOpen
      ? el("div", { class: "mtf-step-boundary", text: `Closed candle ${formatUtc(row.boundaryOpen)} → ${formatUtc(row.boundaryClose)}` })
      : null,
  ].filter(Boolean));
}

/**
 * Compact hierarchy status: the same deterministic Step 13 payload the ladder
 * renders, projected to one glanceable strip (one state per engine role plus
 * the overall decision and what the bot is waiting for). Nothing is
 * recomputed here: every string comes from ladderViewModel, which only reads
 * the backend's multi_timeframe payload.
 */
export function compactHierarchyViewModel(dashboard) {
  const ladder = ladderViewModel(dashboard);
  if (!ladder) {
    const payload = dashboard?.multi_timeframe;
    return {
      available: false,
      reason: payload?.error?.message || "The multi-timeframe hierarchy is not available right now.",
    };
  }
  return {
    available: true,
    rows: ladder.rows,
    overall: ladder.overall,
    decisionLabel: ladder.decisionLabel,
    alignmentLabel: ladder.alignmentLabel,
    counterTrend: ladder.counterTrend,
    decisionTime: ladder.decisionTime,
    waitingForText: ladder.waitingForText,
  };
}

function compactHierarchyStrip(dashboard) {
  const model = compactHierarchyViewModel(dashboard);
  if (!model.available) {
    return el("section", { class: "card terminal-card mtf-compact", "aria-label": "Multi-timeframe status" }, [
      el("div", { class: "section-title-row" }, [
        el("h2", { class: "card-title", text: "Multi-timeframe status" }),
        el("span", { class: "card-hint", text: "engine hierarchy · read-only" }),
      ]),
      el("div", { class: "mtf-compact-unavailable", role: "status", text: model.reason }),
    ]);
  }
  return el("section", { class: "card terminal-card mtf-compact", "aria-label": "Multi-timeframe status" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Multi-timeframe status" }),
      el("span", { class: "card-hint", text: "engine hierarchy · read-only" }),
    ]),
    el("div", { class: "mtf-compact-rows", role: "list" }, model.rows.map((row) =>
      el("div", { class: "mtf-compact-row", role: "listitem", dataset: { tone: row.tone } }, [
        el("div", { class: "mtf-compact-label", text: row.label }),
        el("div", { class: "mtf-compact-state", text: row.state }),
      ])
    )),
    el("div", {
      class: "mtf-compact-overall",
      role: "status",
      dataset: model.counterTrend ? { tone: "warn" } : {},
    }, [
      el("span", { class: "mtf-compact-label", text: "OVERALL" }),
      el("span", { class: "mtf-compact-state", text: model.overall }),
    ]),
    el("div", {
      class: "mtf-compact-meta",
      text: `${model.decisionLabel} · ${model.alignmentLabel}${model.counterTrend ? " · counter-trend setup flagged" : ""}`,
    }),
    model.waitingForText
      ? el("div", { class: "mtf-compact-waiting", text: model.waitingForText })
      : null,
  ]);
}

function multiTimeframeCard(dashboard) {
  const model = ladderViewModel(dashboard);
  if (!model) {
    const payload = dashboard?.multi_timeframe;
    const reason =
      payload?.error?.message || "The multi-timeframe hierarchy is not available right now.";
    return el("section", { class: "card terminal-card", "aria-label": "Multi-timeframe ladder" }, [
      el("div", { class: "section-title-row" }, [
        el("h2", { class: "card-title", text: "Multi-timeframe ladder" }),
        el("span", { class: "card-hint", text: "unavailable" }),
      ]),
      el("div", { class: "card-body" }, [el("p", { class: "mtf-unavailable", text: reason })]),
    ]);
  }
  const children = [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Multi-timeframe ladder" }),
      el("span", { class: "card-hint", text: model.decisionLabel }),
    ]),
    el("div", { class: "mtf-ladder", role: "list" }, [
      ...model.rows.flatMap((row, index) =>
        index === 0
          ? [ladderRow(row)]
          : [
              el("div", { class: "mtf-arrow", text: "↓", "aria-hidden": "true" }),
              ladderRow(row),
            ]
      ),
    ]),
    el("div", { class: `mtf-overall mtf-${model.counterTrend ? "warn" : "neutral"}` }, [
      el("span", { class: "mtf-overall-label", text: "OVERALL" }),
      el("span", { class: "mtf-overall-state", text: model.overall }),
      el("span", { class: "mtf-overall-meta", text: `${model.alignmentLabel}${model.counterTrend ? " · counter-trend setup flagged" : ""}` }),
    ]),
  ];
  if (model.waitingForText) {
    children.push(el("div", { class: "mtf-note", text: model.waitingForText }));
  }
  if (model.invalidatedIfText) {
    children.push(el("div", { class: "mtf-note mtf-note-warn", text: model.invalidatedIfText }));
  }
  children.push(
    el("details", { class: "mtf-limitations" }, [
      el("summary", { text: "What this card is (and is not)" }),
      el("ul", {}, model.limitations.map((line) => el("li", { text: line }))),
    ])
  );
  // The verbose ladder stays available for auditing, but collapsed: the
  // normal trading screen answers from the compact status strip next to the
  // chart instead. Collapsed by default; nothing inside is recomputed.
  return el("details", { class: "card terminal-card expandable mtf-details", "aria-label": "Multi-timeframe ladder" }, [
    el("summary", { class: "mtf-details-summary", text: `Multi-timeframe ladder — technical details · ${model.decisionLabel}` }),
    el("div", { class: "details-body" }, children),
  ]);
}

// ---------------------------------------------------------------------------
// Dashboard layout (PR #38, P3). One BRAIN decision with its reasons, one chart with
// the multi-timeframe and nearby-zone context beside it, what BRAIN is waiting for,
// the paper ledger, and collapsed diagnostics. Every value comes from the backend
// payload; this layer only arranges and labels it. Nothing here changes a decision.
// ---------------------------------------------------------------------------

function heroFact(label, value) {
  return el("div", { class: "hero-fact" }, [
    el("dt", { text: label }),
    el("dd", { text: String(value) }),
  ]);
}

/** The single BRAIN decision: state, reason, what it waits for, and the trust boundary. */
function decisionHero(dashboard, forward) {
  const model = verdictViewModel(dashboard, forward);
  const status = decisionCentreStatus(dashboard);
  const direction = model.direction ? `${directionArrow(model.direction)} ${directionLabel(model.direction)}` : null;
  const family = model.family ? familyLabel(model.family) : null;
  const freshness = dashboard?.freshness || {};
  const asOf = dashboard?.meta?.as_of ? formatUtc(dashboard.meta.as_of) : "UNKNOWN";
  const latest = freshness.latest_stored ? formatUtc(freshness.latest_stored) : "UNKNOWN";
  const symbol = dashboard?.meta?.symbol || "BTC/USDT";
  const engine = dashboard?.meta?.timeframe ? String(dashboard.meta.timeframe).toUpperCase() : "";
  return el("section", { class: "card hero-card verdict-card", "aria-label": "BRAIN decision" }, [
    el("div", { class: "hero-main" }, [
      el("div", { class: "hero-kicker", text: `BRAIN decision · ${symbol}${engine ? ` · ${engine} engine` : ""}` }),
      el("h2", {
        class: "hero-state",
        dataset: { tone: model.tone },
        role: "status",
        text: model.state,
      }),
      el("div", { class: "hero-identity" }, [
        direction ? el("span", { class: "hero-direction", text: direction }) : null,
        family ? el("span", { class: "tag", text: family }) : null,
        !direction && !family ? el("span", { class: "hero-muted", text: "No directional candidate" }) : null,
      ]),
      el("p", { class: "hero-reason", text: model.explanation }),
      model.planStatus
        ? el("p", { class: "hero-plan-status", dataset: { tone: model.tone }, text: model.planStatus })
        : null,
      model.hierarchyGate
        ? el("div", {
            class: "hierarchy-gate",
            dataset: { tone: model.hierarchyReady ? "ready" : "waiting" },
            role: "status",
            "aria-label": "Multi-timeframe hierarchy gate",
          }, [
            el("span", { class: "hierarchy-gate-label", text: "Hierarchy gate" }),
            el("strong", { class: "hierarchy-gate-state", text: model.hierarchyGate }),
          ])
        : null,
    ]),
    el("div", { class: "hero-side" }, [
      el("span", { class: "hero-status", dataset: { tone: status.tone }, text: status.label }),
      el("dl", { class: "hero-facts" }, [
        heroFact("Paper plan", model.plannable ? "Calculated by the backend — see Trade plan" : "None — the backend did not build one"),
        heroFact("Real orders", "0 — none by design; execution is disabled"),
      ]),
      el("p", { class: "hero-data", text: `As of ${asOf} · latest stored candle ${latest}` }),
    ]),
    el("div", { class: "hero-journal" }, [decisionCard(dashboard)]),
  ]);
}

function zoneRow(band) {
  const above = band.position === "above_price";
  const below = band.position === "below_price";
  const role = above ? "Resistance" : below ? "Support" : "Price inside";
  const tone = above ? "red" : below ? "green" : "amber";
  const tf = band.source_timeframe ? String(band.source_timeframe).toUpperCase() : "";
  const touches = Number.isFinite(band.touch_count) ? `${band.touch_count} touch${band.touch_count === 1 ? "" : "es"}` : "touches unknown";
  const meta = [tf && `${tf} zone`, touches, band.faded ? "older, faded" : null].filter(Boolean).join(" · ");
  return el("li", { class: "zone-row", dataset: { tone } }, [
    el("div", { class: "zone-row-head" }, [
      el("strong", { class: "zone-role", text: role }),
      el("span", { class: "mono zone-bounds", text: `${displayRounded(band.band_low, 2).display} – ${displayRounded(band.band_high, 2).display}` }),
    ]),
    el("div", { class: "zone-meta", text: meta }),
    band.position_changed
      ? el("div", {
          class: "zone-note",
          text: `${tf || "Source"} close placed it on the other side; shown against this chart's close.`,
        })
      : null,
  ]);
}

/** Nearby support and resistance for the engine timeframe, positioned against the latest confirmed close. */
function nearbyZonesCard(dashboard) {
  const zb = dashboard?.overlays?.zone_bands;
  const close = lastConfirmedClose(dashboard?.market?.candles);
  const bands = relocateBands(Array.isArray(zb?.bands) ? zb.bands : [], close);
  const tf = String(zb?.timeframe || dashboard?.meta?.timeframe || "").toUpperCase();
  const hidden = Number.isFinite(zb?.hidden_count) ? zb.hidden_count : 0;
  const merged = Number.isFinite(zb?.merged_count) ? zb.merged_count : 0;
  return el("section", { class: "card context-card", "aria-label": "Nearby support and resistance" }, [
    el("div", { class: "card-kicker", text: `Nearby zones${tf ? ` · ${tf}` : ""}` }),
    bands.length === 0
      ? el("p", {
          class: "muted",
          text: zb?.reason === "no_zones" ? "No support or resistance zones are stored at this instant." : "No zone bands to show.",
        })
      : el("ul", { class: "zone-list" }, bands.map(zoneRow)),
    el("p", {
      class: "hint",
      text: `${hidden} further zone(s) hidden · ${merged} merged for display. Zones are context; they do not change decisions.`,
    }),
  ]);
}

/** What BRAIN is waiting for: the watched area, what it needs, what blocks it, and when the idea is void. */
function waitingCard(dashboard, forward) {
  const model = verdictViewModel(dashboard, forward);
  const blockers = model.candidate ? blockingRuleSentences(model.candidate).slice(0, 4) : [];
  const counts = [
    model.watchCount !== null ? `${model.watchCount} watching` : null,
    model.qualifiedCount !== null ? `${model.qualifiedCount} qualified` : null,
  ].filter(Boolean).join(" · ");
  return el("section", { class: "card waiting-card", "aria-label": "What BRAIN is waiting for" }, [
    lookingForCard(dashboard),
    blockers.length
      ? el("div", { class: "waiting-blockers" }, [
          el("div", { class: "waiting-subhead", text: "Blocked by" }),
          el("ul", {}, blockers.map((text) => el("li", { text }))),
        ])
      : el("p", { class: "muted", text: model.candidate ? "No blocking rule is reported." : "No candidate setup is being tracked." }),
    el("p", { class: "hint", text: counts ? `Setups: ${counts}. Full list in Diagnostics.` : "Full setup list in Diagnostics." }),
  ]);
}

/**
 * Empty ledger: one plain statement instead of three panels of zeros. Only shown when the forward
 * report confirms zero paper plans; the engine builds a plan only from a qualified setup.
 */
function paperEmptyCard(forward) {
  return el("section", { class: "card terminal-card paper-empty", "aria-label": "No paper plans" }, [
    el("h2", { class: "card-title", text: "No paper plans recorded yet" }),
    el("p", {
      class: "paper-empty-text",
      text: "BRAIN records a paper plan only when its engine builds one from a qualified setup. None has been recorded for this symbol and timeframe, so there are no outcomes, R-multiples or performance figures to show.",
    }),
    el("p", { class: "performance-caveat", text: forward?.paper_label || "PAPER OBSERVATION — NO REAL ORDER" }),
  ]);
}

/** Paper observations, recent decisions and measured performance, grouped as one ledger. */
export function paperSection(dashboard, forward) {
  const model = paperObservationsViewModel(forward);
  const empty = model.available === true && model.paperPlans === 0;
  return el("section", { class: "paper-section", "aria-label": "Paper trading results" }, [
    el("div", { class: "section-head" }, [
      el("h2", { text: "Paper trading" }),
      el("p", { class: "muted", text: "Observations and plans on stored candles. No real orders are placed; these are records, not performance claims." }),
    ]),
    empty
      ? paperEmptyCard(forward)
      : el("div", { class: "paper-grid" }, [
          paperObservationsCard(forward),
          recentDecisionsCard(forward),
          performanceCard(forward, dashboard?.meta || {}),
        ]),
  ]);
}

/** Expandable diagnostics: every setup, the raw technical record, and system details. Nothing is removed. */
function diagnosticsSection(dashboard, forward) {
  return el("details", { class: "card diagnostics", "aria-label": "Diagnostics" }, [
    el("summary", { text: "Diagnostics — every setup, the technical record and system checks" }),
    el("div", { class: "diagnostics-body" }, [
      multiTimeframeCard(dashboard),
      botWatchingCard(dashboard),
      explanationCard(dashboard),
      systemDetailsCard(dashboard, forward),
    ]),
  ]);
}

export async function renderDashboard(view) {
  const generation = ++renderGeneration;
  if (activeChart) activeChart.destroy();
  activeChart = null;
  view.className = "view terminal-dashboard";
  clearNode(view).append(spinner("Loading stored market data and deterministic state…"));
  setTopbarWarning();

  const prefs = loadPrefs();
  const [dashboardResult, forwardResult] = await Promise.allSettled([
    api.dashboard({
      symbol: prefs.preferredSymbol || undefined,
      timeframe: prefs.preferredTimeframe || undefined,
    }),
    api.forward({ limit: 8 }),
  ]);
  if (generation !== renderGeneration) return;

  if (dashboardResult.status === "rejected") {
    view.className = "view";
    clearNode(view).append(errorState(dashboardResult.reason?.message || "Dashboard data is unavailable."));
    setTopbarWarning();
    return;
  }

  const dashboard = dashboardResult.value || {};
  const forward = forwardResult.status === "fulfilled" ? forwardResult.value : null;
  updateTopbar(dashboard, forward);

  const chart = chartCard(dashboard);
  clearNode(view).append(
    decisionHero(dashboard, forward),
    el("div", { class: "dash-main" }, [
      el("div", { class: "dash-chart" }, [chart.node]),
      el("aside", { class: "dash-context", "aria-label": "Multi-timeframe and zone context" }, [
        compactHierarchyStrip(dashboard),
        nearbyZonesCard(dashboard),
      ]),
    ]),
    el("div", { class: "dash-row" }, [
      waitingCard(dashboard, forward),
      planCard(dashboard),
      marketNowCard(dashboard),
    ]),
    paperSection(dashboard, forward),
    diagnosticsSection(dashboard, forward),
  );
  chart.mount();
  activeChart = chart;
}
