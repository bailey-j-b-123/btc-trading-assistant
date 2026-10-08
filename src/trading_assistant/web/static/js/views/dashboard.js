/**
 * One-page trading terminal. All market, setup, plan, observation, performance,
 * and system values are rendered from existing backend API responses. The
 * frontend does not calculate a trading verdict, create levels, or invent
 * candles, outcomes, or performance metrics.
 */

import { api } from "../api.js";
import { canShowForming, createFormingStream, currentBucketMs, FORMING_INTERVALS } from "../forming-display.js";
import { brainStatusViewModel } from "../brain-status.js";
import { lookingForCard, scenarioBand } from "../looking-for.js";
import {
  buildDecisionRequest,
  confirmationSummary,
  decisionAvailability,
  DECISION_LABELS,
  DECISION_MEANINGS,
} from "../decision.js";
import {
  applyOverlays,
  clearOverlays,
  createPriceChart,
  destroyPriceChart,
  OVERLAY_COLORS,
  setCandles,
  setFormingCandle,
  toChartCandles,
} from "../chart.js";
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

export const BRAIN_STATUS_REFRESH_INTERVAL_MS = 30_000;
export const BRAIN_STATUS_REFRESH_TIMEOUT_MS = 5_000;
export const BRAIN_STATUS_STALE_AFTER_MS = 90_000;

let renderGeneration = 0;
let activeChart = null;
let activeBrainStatusCard = null;
let brainRefreshTimer = null;
let brainRefreshRequestTimeout = null;
let brainRefreshController = null;
let brainRefreshGeneration = 0;

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
const ENGINE_CHART_NOTE = "Confirmed history is stored closed candles only. The separate forming candle and volume come from Kraken's real public trades/OHLC and are display-only; they never enter BRAIN, journal, or forward-testing inputs.";

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
  if (viewedTimeframe === engineTimeframe) return "Stored closed candles · live Kraken forming candle is a separate display-only series";
  const viewed = viewedTimeframeLabel(viewedTimeframe);
  const engine = viewedTimeframeLabel(engineTimeframe);
  return `View only — ${viewed} stored candles with ${viewed} structure · engine hierarchy unchanged · plan levels from the ${engine} engine plan`;
}

function candleHistoryStatus(timeframe, rows, payload = {}) {
  const candles = toChartCandles(rows);
  const latest = candles.at(-1);
  const interval = (FORMING_INTERVALS[timeframe] || 0) * 60000;
  const currentOpen = currentBucketMs(timeframe, Date.now());
  if (!latest || !interval || currentOpen === null) {
    return "STORED CLOSED HISTORY · no confirmed closed candle is available for this timeframe.";
  }
  const expectedLatest = currentOpen - interval;
  const latestOpen = latest.time * 1000;
  const missingTail = latestOpen < expectedLatest
    ? Math.max(0, Math.floor((expectedLatest - latestOpen) / interval))
    : 0;
  let candleGaps = 0;
  for (let index = 1; index < candles.length; index += 1) {
    const delta = candles[index].time - candles[index - 1].time;
    if (delta > interval / 1000) candleGaps += Math.max(0, Math.round(delta / (interval / 1000)) - 1);
  }
  const reportedGaps = (Array.isArray(payload.gaps) ? payload.gaps : []).reduce((sum, gap) => {
    const count = Number(gap?.missing_count ?? gap?.missing_candle_count ?? gap?.intervals_missing);
    return Number.isFinite(count) && count > 0 ? sum + Math.floor(count) : sum;
  }, 0);
  const knownMissing = Math.max(missingTail + candleGaps, reportedGaps,
    Number.isInteger(payload.missing_candle_count) ? payload.missing_candle_count : 0);
  const complete = payload.complete ?? payload.market?.complete;
  const quality = knownMissing > 0 || complete === false
    ? "GAP / STALE · live forming candle remains separate"
    : "CURRENT · confirmed history only";
  const closeTime = new Date(latestOpen + interval).toISOString();
  const gapText = knownMissing > 0
    ? ` · ${knownMissing} known missing interval(s)`
    : complete === false
      ? " · backend reports incomplete history (gap count unavailable)"
      : "";
  return `STORED CLOSED HISTORY ${quality} · latest $${latest.close.toLocaleString("en-US")} closed ${formatUtc(closeTime)}${gapText}`;
}

function chartCard(dashboard, initialPrefs, brainModel) {
  const meta = dashboard?.meta || {};
  const symbol = meta.symbol || "UNKNOWN";
  const engineTimeframe = typeof meta.timeframe === "string" && meta.timeframe ? meta.timeframe : "1h";
  const decisionAsOf = typeof meta.as_of === "string" && meta.as_of ? meta.as_of : null;
  const rows = Array.isArray(dashboard?.market?.candles) ? dashboard.market.candles : [];
  const validCandles = toChartCandles(rows);
  const host = el("div", {
    class: "chart-wrap terminal-chart-wrap",
    "aria-label": `${symbol} ${viewedTimeframeLabel(engineTimeframe)} candlestick chart`,
  });
  const toolbar = el("div", { class: "chart-toolbar", role: "group", "aria-label": "Chart timeframe and overlays" });
  const handleRef = { current: null };
  const streamBadge = el("span", { class: "stream-state-badge", dataset: { status: "CONNECTING" }, text: "CONNECTING" });
  const formingCaption = el("span", { class: "forming-caption", text: `FORMING ${viewedTimeframeLabel(engineTimeframe)} · DISPLAY ONLY` });
  const livePriceValue = el("strong", { class: "live-price-value", text: "—" });
  const livePriceTime = el("span", { class: "live-price-time", text: "Awaiting Kraken trade" });
  const streamDetail = el("span", { class: "live-stream-detail", text: "Opening Kraken public trade + OHLC channels…" });
  const formingStatus = el("div", { class: "forming-status live-market-strip", dataset: { freshness: "CONNECTING" },
    "aria-label": "Live Kraken BTC/USDT price and display-only forming candle" }, [
    el("div", { class: "live-status-line" }, [
      streamBadge,
      formingCaption,
    ]),
    el("div", { class: "live-price-line" }, [
      el("span", { class: "live-price-symbol", text: "BTC/USDT" }),
      livePriceValue,
      el("span", { class: "live-price-caption", text: "last Kraken trade · USDT" }),
    ]),
    livePriceTime,
    streamDetail,
  ]);
  const formingOhlc = el("div", { class: "forming-ohlc chart-note", text: "Waiting for the current-bucket Kraken OHLC baseline…" });
  const confirmedStatus = el("div", { class: "chart-note confirmed-history-status", text: "STORED CLOSED HISTORY · unavailable" });
  let formingStream = null;
  const viewed = {
    timeframe: engineTimeframe,
    generation: 0,
    cancelled: false,
    overlaySnapshotFresh: true,
    dataAsOf: decisionAsOf,
    overlays: dashboard?.overlays && typeof dashboard.overlays === "object" ? dashboard.overlays : emptyOverlays(),
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
  const planForOverlays = () => (
    brainModel?.currentDecisionAvailable && viewed.overlaySnapshotFresh &&
    timeMatches(viewed.dataAsOf, decisionAsOf) && hasValidTradePlan(dashboard)
      ? dashboard.plan
      : null
  );
  function stopForming() {
    formingStream?.stop();
    formingStream = null;
    setFormingCandle(handleRef.current, null);
  }
  function updateFormingText(timeframe, candle) {
    const label = viewedTimeframeLabel(timeframe);
    if (!candle) {
      formingOhlc.textContent = `FORMING ${label} · waiting for a current-bucket Kraken OHLC baseline · display only`;
      return;
    }
    const price = (value) => `$${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const volume = Number(candle.volume);
    const volumeText = Number.isFinite(volume)
      ? `${volume.toLocaleString("en-US", { maximumFractionDigits: 8 })} BTC`
      : "volume unavailable";
    formingOhlc.textContent = `FORMING ${label} · display only · O ${price(candle.open)} · H ${price(candle.high)} · L ${price(candle.low)} · C ${price(candle.close)} · V ${volumeText} · ${candle.trades ?? "?"} trades`;
    formingOhlc.title = `Exact Kraken OHLCV: O ${candle.open}, H ${candle.high}, L ${candle.low}, C ${candle.close}, V ${candle.volume ?? "unknown"}`;
  }
  function startForming(timeframe, initialRows, historyPayload = {}) {
    stopForming();
    let storedRows = Array.isArray(initialRows) ? initialRows : [];
    const label = viewedTimeframeLabel(timeframe);
    formingStatus.dataset.freshness = "CONNECTING";
    streamBadge.dataset.status = "CONNECTING";
    streamBadge.textContent = "CONNECTING";
    formingCaption.textContent = `FORMING ${label} · DISPLAY ONLY`;
    confirmedStatus.textContent = candleHistoryStatus(timeframe, storedRows, historyPayload);
    updateFormingText(timeframe, null);
    if (!handleRef.current || symbol !== "BTC/USDT" || !FORMING_INTERVALS[timeframe]) {
      const reason = symbol !== "BTC/USDT"
        ? `Live Kraken trade stream is configured for BTC/USDT; ${symbol} is not connected.`
        : `No live stream is configured for ${label}.`;
      streamBadge.dataset.status = "DISCONNECTED";
      streamBadge.textContent = "DISCONNECTED";
      formingStatus.dataset.freshness = "DISCONNECTED";
      streamDetail.textContent = reason;
      return;
    }
    const streamGeneration = viewed.generation;
    formingStream = createFormingStream({
      timeframe,
      onPrice: (tick) => {
        if (viewed.cancelled || streamGeneration !== viewed.generation) return;
        const exact = String(tick.price);
        livePriceValue.textContent = `$${tick.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        livePriceValue.title = `Exact Kraken trade price ${exact} · trade ID ${tick.tradeId}`;
        livePriceTime.textContent = `Last trade ${formatUtc(tick.timestamp, { withSeconds: true })}`;
      },
      onCandle: (candle) => {
        if (viewed.cancelled || streamGeneration !== viewed.generation) return;
        const latest = toChartCandles(storedRows).at(-1);
        const latestOpenMs = latest ? latest.time * 1000 : null;
        if (!candle) {
          setFormingCandle(handleRef.current, null);
          updateFormingText(timeframe, null);
          return;
        }
        if (!canShowForming(timeframe, latestOpenMs, Date.now())) {
          setFormingCandle(handleRef.current, null);
          formingOhlc.textContent = `FORMING ${label} hidden · stored history already contains this or a later candle; no duplicate is drawn.`;
          return;
        }
        removeChartOverlay();
        setFormingCandle(handleRef.current, candle);
        updateFormingText(timeframe, candle);
      },
      onStatus: (nextStatus, info = {}) => {
        if (viewed.cancelled || streamGeneration !== viewed.generation) return;
        formingStatus.dataset.freshness = nextStatus;
        streamBadge.dataset.status = nextStatus;
        streamBadge.textContent = nextStatus;
        streamDetail.textContent = info.reason || "Kraken stream status unavailable.";
        streamDetail.title = info.reason || "";
        if (info.lastTradeAt && livePriceTime.textContent === "Awaiting Kraken trade") {
          livePriceTime.textContent = `Last trade ${formatUtc(info.lastTradeAt, { withSeconds: true })}`;
        }
      },
      onRollover: ({ bucketOpenMs }) => {
        if (viewed.cancelled || streamGeneration !== viewed.generation) return;
        // Boundary-triggered read only: this refreshes stored closed history
        // once per selected interval. It is not a live-price polling loop.
        const bucketGeneration = viewed.generation;
        api.candles({ symbol, timeframe, limit: CHART_CANDLE_LIMIT }).then((payload) => {
          if (viewed.cancelled || bucketGeneration !== viewed.generation || viewed.timeframe !== timeframe) return;
          if (payload?.timeframe !== timeframe || !Array.isArray(payload.candles)) {
            throw new Error("The stored-candle response did not match the selected timeframe.");
          }
          storedRows = payload.candles;
          viewed.dataAsOf = payload.as_of || null;
          if (handleRef.current) setCandles(handleRef.current, storedRows, { fit: false });
          confirmedStatus.textContent = candleHistoryStatus(timeframe, storedRows, payload);
          viewed.overlays = emptyOverlays();
          viewed.overlaySnapshotFresh = false;
          if (handleRef.current) clearOverlays(handleRef.current);
          viewNote.textContent = `${label} stored closed history refreshed at ${formatUtc(new Date(bucketOpenMs).toISOString())}. Structure/plan overlays are hidden until their source snapshot matches this boundary.`;
          if (!toChartCandles(storedRows).length) {
            showChartOverlay(chartEmpty(`${label} stored history unavailable`, "No confirmed closed candles were returned at the rollover. A real Kraken forming bar may still appear separately."));
          }
        }).catch((error) => {
          if (viewed.cancelled || bucketGeneration !== viewed.generation) return;
          confirmedStatus.textContent = `STORED CLOSED HISTORY · rollover refresh failed (${error.message}); previous confirmed rows were left unchanged.`;
        });
      },
    });
    formingStream.start();
  }
  const applyViewedOverlays = () => {
    if (!handleRef.current) return;
    const overlays = viewed.overlaySnapshotFresh ? viewed.overlays || {} : emptyOverlays();
    applyOverlays(handleRef.current, {
      overlays,
      plan: planForOverlays(),
      scenarioBand: viewed.overlaySnapshotFresh && brainModel?.currentDecisionAvailable &&
        timeMatches(viewed.dataAsOf, decisionAsOf)
        ? scenarioBand(dashboard?.looking_for, viewed.timeframe)
        : null,
      prefs: loadPrefs(),
    });
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

  const overlayControls = [
    ["zones", "S/R levels", OVERLAY_COLORS.zones],
    ["range", "Range", OVERLAY_COLORS.range],
    ["equalLevels", "Liquidity", OVERLAY_COLORS.equalLevels],
    ["planLevels", "Plan", OVERLAY_COLORS.entry],
    ["swings", "Swing points", OVERLAY_COLORS.swings],
  ];
  const overlayButtons = new Map();
  for (const [key, label, color] of overlayControls) {
    const button = el("button", {
      class: "overlay-toggle",
      type: "button",
      "data-overlay": key,
      "aria-pressed": String(initialPrefs.overlays?.[key] === true),
      "aria-label": `${label} chart overlay`,
      style: { "--swatch": color },
      onclick: () => {
        const next = loadPrefs();
        next.overlays[key] = next.overlays[key] === false;
        savePrefs(next);
        button.setAttribute("aria-pressed", String(next.overlays[key]));
        // Toggles re-apply the overlays of the timeframe currently being
        // viewed — never the engine overlays onto another timeframe.
        applyViewedOverlays();
      },
    }, [el("span", { class: "swatch", "aria-hidden": "true" }), label]);
    overlayButtons.set(key, button);
    toolbar.append(button);
  }
  const updateLiquidityToggle = () => {
    const button = overlayButtons.get("equalLevels");
    if (!button) return;
    const engineView = viewed.timeframe === engineTimeframe;
    button.disabled = !engineView;
    if (engineView) button.removeAttribute("title");
    else button.title = `Equal-level overlays are only available on the engine timeframe (${viewedTimeframeLabel(engineTimeframe)})`;
  };

  async function showTimeframe(timeframe) {
    if (timeframe === viewed.timeframe || viewed.cancelled) return;
    const generation = ++viewed.generation;
    const label = viewedTimeframeLabel(timeframe);
    const mayRestoreLoadedOverlays = viewed.overlaySnapshotFresh;
    viewed.overlaySnapshotFresh = false;
    viewed.timeframe = timeframe;
    for (const [id, button] of switchButtons) button.setAttribute("aria-pressed", String(id === timeframe));
    headingTitle.textContent = `${symbol} · ${label} chart`;
    headingMeta.textContent = chartHeadingMeta(timeframe, engineTimeframe);
    host.setAttribute("aria-label", `${symbol} ${label} candlestick chart (view only)`);
    updateLiquidityToggle();
    removeChartOverlay();
    stopForming(); // remove the old temporary candle immediately, before any read
    // No stale overlays: the previous timeframe's levels leave the chart
    // before any new data is requested.
    if (handleRef.current) clearOverlays(handleRef.current);
    viewNote.textContent = `Loading stored ${label} closed candles…`;

    // The engine view restores the exact dashboard snapshot (no refetch, no
    // drift): these are the same stored rows the verdict was computed from.
    if (timeframe === engineTimeframe) {
      if (generation !== viewed.generation || viewed.cancelled) return;
      viewed.overlays = dashboard?.overlays && typeof dashboard.overlays === "object"
        ? dashboard.overlays
        : emptyOverlays();
      viewed.dataAsOf = decisionAsOf;
      viewed.overlaySnapshotFresh = mayRestoreLoadedOverlays;
      const handle = ensureChart();
      if (!handle || generation !== viewed.generation || viewed.cancelled) return;
      setCandles(handle, rows);
      confirmedStatus.textContent = candleHistoryStatus(timeframe, rows, dashboard?.market || {});
      if (!toChartCandles(rows).length) {
        showChartOverlay(chartEmpty(
          "No stored closed candles",
          rows.length
            ? "The stored payload contains no renderable rows. A separate real Kraken candle may appear when available."
            : "No confirmed closed candles were returned. No substitute history is shown.",
        ));
      }
      startForming(timeframe, rows, dashboard?.market || {});
      applyViewedOverlays();
      viewNote.textContent = ENGINE_CHART_NOTE;
      return;
    }

    // Other timeframes are a display-only view of the latest stored closed
    // candles. Their read-only market view never feeds the dashboard verdict,
    // qualification, plan, or forward-testing runner.
    let candlesPayload = null;
    let candlesError = null;
    try {
      candlesPayload = await api.candles({
        symbol,
        timeframe,
        limit: CHART_CANDLE_LIMIT,
      });
    } catch (error) {
      candlesError = error;
    }
    if (generation !== viewed.generation || viewed.cancelled) return;
    const timeframeEcho = candlesPayload?.timeframe;
    if (candlesError || timeframeEcho !== timeframe) {
      viewed.overlays = emptyOverlays();
      viewed.overlaySnapshotFresh = false;
      viewed.dataAsOf = null;
      const handle = ensureChart();
      if (handle) {
        setCandles(handle, []);
        startForming(timeframe, [], {});
      }
      const detail = candlesError
        ? `${candlesError.message} No substitute stored data is shown; the independent Kraken stream may still provide a real forming bar.`
        : `The backend returned ${timeframeEcho || "unlabelled"} data for a ${label} request; it is not shown. The separate Kraken stream remains view-only.`;
      showChartOverlay(chartEmpty(`${label} stored history unavailable`, detail));
      viewNote.textContent = `${label} confirmed history is unavailable; no substitute candles are rendered.`;
      return;
    }
    const fetchedRows = Array.isArray(candlesPayload.candles) ? candlesPayload.candles : [];
    const handle = ensureChart();
    if (!handle || generation !== viewed.generation || viewed.cancelled) return;
    viewed.dataAsOf = candlesPayload.as_of || null;
    setCandles(handle, fetchedRows);
    confirmedStatus.textContent = candleHistoryStatus(timeframe, fetchedRows, candlesPayload);
    startForming(timeframe, fetchedRows, candlesPayload);
    if (!toChartCandles(fetchedRows).length) {
      viewed.overlays = emptyOverlays();
      viewed.overlaySnapshotFresh = false;
      const returned = Number.isInteger(candlesPayload.returned_count) ? candlesPayload.returned_count : fetchedRows.length;
      showChartOverlay(chartEmpty(
        `${label} stored history unavailable`,
        `No stored ${label} closed candles were returned (count ${returned}). A real Kraken forming candle may still appear separately.`,
      ));
      viewNote.textContent = `No stored ${label} closed candles are available — no history is invented or substituted.`;
      return;
    }

    let structurePayload = null;
    let structureError = null;
    try {
      structurePayload = await api.structure({ symbol, timeframe, as_of: candlesPayload.as_of || undefined });
    } catch (error) {
      structureError = error;
    }
    if (generation !== viewed.generation || viewed.cancelled) return;
    if (structureError || structurePayload?.timeframe !== timeframe) {
      viewed.overlays = emptyOverlays();
      applyViewedOverlays();
      viewNote.textContent = structureError
        ? `${label} candles shown · ${label} structure unavailable (${structureError.message}) — levels hidden, candles only.`
        : `${label} candles shown · the backend returned ${structurePayload?.timeframe || "unlabelled"} structure for a ${label} request — levels hidden, candles only.`;
      return;
    }
    viewed.overlays = overlaysForViewedTimeframe({ dashboard, viewedTimeframe: timeframe, structure: structurePayload });
    viewed.overlaySnapshotFresh = true;
    applyViewedOverlays();
    const zoneCount = Array.isArray(viewed.overlays.zones) ? viewed.overlays.zones.length : 0;
    const returned = Number.isInteger(candlesPayload.returned_count) ? candlesPayload.returned_count : fetchedRows.length;
    viewNote.textContent = `${label} stored closed candles (${returned}) · ${label} structure (${zoneCount} zone(s)${viewed.overlays.range ? " · range shown" : ""}) · plan levels from the engine plan · liquidity overlays unavailable on this view.`;
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
    formingOhlc,
    confirmedStatus,
    brainModel?.currentDecisionAvailable
      ? lookingForCard(dashboard)
      : el("div", { class: "chart-note brain-scenario-unavailable", role: "status", text: `Current BRAIN scenario is not shown: ${brainModel?.reason || "no matching persisted cycle"}` }),
    host,
    viewNote,
  ]);

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
      const handle = ensureChart();
      if (!handle) return;
      setCandles(handle, rows);
      confirmedStatus.textContent = candleHistoryStatus(engineTimeframe, rows, dashboard?.market || {});
      startForming(engineTimeframe, rows, dashboard?.market || {});
      applyViewedOverlays();
    },
    destroy() {
      stopForming();
      viewed.cancelled = true;
      viewed.generation += 1;
      destroyPriceChart(handleRef.current);
      handleRef.current = null;
    },
  };
}

function candidateCounts(model) {
  const countNode = (value, label) => el("div", { class: "candidate-count" }, [
    el("div", { class: "value", text: value === null ? "—" : String(value) }),
    el("div", { class: "label", text: label }),
  ]);
  return el("div", { class: "candidate-counts", "aria-label": "Candidate counts" }, [
    countNode(model.watchCount, "On watch"),
    countNode(model.qualifiedCount, "Qualified"),
    countNode(model.plannedCount, "Plannable"),
  ]);
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

function verdictCard(dashboard, forward) {
  const model = verdictViewModel(dashboard, forward);
  const direction = model.direction
    ? `${directionArrow(model.direction)} ${directionLabel(model.direction)}`
    : "Direction UNKNOWN";
  const family = model.family ? familyLabel(model.family) : "Setup UNKNOWN";
  const qualification = dashboard?.qualification || {};
  const technical = [
    `backend state: ${qualification.state || "UNKNOWN"} (snapshot status: ${qualification.status || "UNKNOWN"})`,
    `selected setup: ${qualification.selected_setup_id || "none"}`,
    ...backendReasons(qualification.reasons).map((reason) => `snapshot reason: ${reason}`),
    ...rawRuleRows(model.candidate),
  ];
  if (qualification.rules_version) technical.push(`rules version: ${qualification.rules_version}`);
  if (qualification.config_fingerprint) technical.push(`config fingerprint: ${qualification.config_fingerprint}`);
  return el("section", {
    class: "card terminal-card verdict-card",
    "aria-label": "Current verdict",
  }, [
    el("div", { class: "verdict-kicker", text: "Current verdict" }),
    el("div", {
      class: model.plannable ? "verdict-state verdict-state-plan" : "verdict-state",
      dataset: { tone: model.tone },
      role: "status",
      text: model.state,
    }),
    model.planStatus
      ? el("div", { class: "verdict-plan-status", dataset: { tone: model.tone }, text: model.planStatus })
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
    el("div", { class: "verdict-identity" }, [
      el("span", { class: "verdict-direction", text: direction }),
      el("span", { class: "tag verdict-family", text: family }),
    ]),
    el("p", { class: "verdict-summary", text: model.explanation }),
    decisionCard(dashboard),
    candidateCounts(model),
    technicalDetails("Technical details", technical),
  ]);
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

function persistedWaitingRules(observation) {
  const rules = (Array.isArray(observation?.rules) ? observation.rules : [])
    .filter((rule) => rule?.required !== false && rule?.outcome === "pending");
  if (rules.length) return rules;
  return (Array.isArray(observation?.pendingRules) ? observation.pendingRules : [])
    .filter((ruleId) => typeof ruleId === "string" && ruleId.trim())
    .map((ruleId) => ({ rule_id: ruleId, outcome: "pending", required: true }));
}

function persistedPlanRecord(observation) {
  const plan = observation?.plan;
  if (observation?.planState !== "PLANNABLE" || plan?.state !== "PLANNABLE") {
    return observation?.planState
      ? el("div", { class: "brain-plan-unavailable", text: `Recorded plan state: ${planStateLabel(observation.planState)}${plan?.state_detail ? ` · ${plan.state_detail}` : ""}` })
      : null;
  }
  const targets = Array.isArray(plan.targets) ? plan.targets : [];
  const targetText = targets.map((target, index) => {
    const value = target?.level?.value;
    return isMissing(value) ? null : `T${index + 1} ${displayRounded(value, 2).display}`;
  }).filter(Boolean);
  return el("div", { class: "brain-plan-record" }, [
    el("div", { class: "brain-detail-label", text: "Plan from this recorded close · paper only" }),
    el("div", { class: "plan-levels brain-plan-levels" }, [
      planLevel("Entry", plan.entry?.value, "green"),
      planLevel("Stop", plan.stop?.value, "red"),
      planLevel("Invalidation", plan.invalidation?.value, "amber"),
    ]),
    el("div", { class: "brain-recorded-targets", text: targetText.length
      ? `Structural targets: ${targetText.join(" · ")}`
      : "No structural target levels were recorded." }),
  ]);
}

function persistedObservationCard(observation) {
  const heading = `${familyLabel(observation.family)} · ${directionLabel(observation.direction)}`;
  const pending = persistedWaitingRules(observation);
  const children = [
    el("div", { class: "brain-observation-heading" }, [
      el("strong", { text: heading }),
      el("span", { class: "tag", text: setupStateLabel(observation.state) }),
    ]),
  ];
  if (pending.length) {
    children.push(el("div", { class: "brain-waiting-record" }, [
      el("span", { class: "brain-detail-label", text: "Recorded waiting state" }),
      el("ul", {}, pending.map((rule) => el("li", { text: translateRule(rule, { direction: observation.direction }).sentence }))),
    ]));
  } else if (observation.state === "WATCH" || observation.state === "QUALIFIED") {
    children.push(el("div", { class: "brain-waiting-record", text: "No pending required rule is recorded on this observation." }));
  }
  if (observation.noTradeReason) {
    children.push(el("div", { class: "brain-no-trade-reason" }, [
      el("span", { class: "brain-detail-label", text: "Recorded no-paper-plan reason" }),
      el("span", { text: observation.noTradeReason }),
    ]));
  }
  const plan = persistedPlanRecord(observation);
  if (plan) children.push(plan);
  return el("div", { class: "brain-observation-record" }, children);
}

function brainStatusCard(model) {
  const badgeTone = model.cycleCurrent
    ? "good"
    : ["STOPPED", "STALE", "ERROR"].includes(model.statusLabel) ? "warn" : "neutral";
  const cycleTime = model.cycleAsOf ? formatUtc(model.cycleAsOf) : "no cycle recorded";
  const counts = Object.entries(model.setupStateCounts || {})
    .filter(([, count]) => Number.isInteger(count) && count >= 0)
    .map(([state, count]) => `${setupStateLabel(state)} ${count}`);
  const children = [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "BRAIN status" }),
      el("span", { class: "brain-state-badge", dataset: { tone: badgeTone }, role: "status", text: model.statusLabel }),
    ]),
    el("div", { class: "brain-runner-state" }, [
      el("span", { class: "brain-detail-label", text: "Runner" }),
      el("strong", { text: model.runnerStatus }),
      model.runner?.recorded_at ? el("span", { class: "mono", text: `heartbeat ${formatUtc(model.runner.recorded_at)}` }) : null,
    ]),
    el("div", { class: "brain-cycle-meta", text: `Latest persisted cycle: ${cycleTime} · ${model.cycleStatus || "no cycle status"} · snapshot ${model.snapshotState ? setupStateLabel(model.snapshotState) : "UNKNOWN"}` }),
    el("div", { class: model.currentDecisionAvailable ? "brain-current-note" : "brain-not-current", role: "note", text: model.currentDecisionAvailable
      ? "The current verdict/setup/plan panels match this completed persisted cycle and confirmed closed-candle boundary."
      : model.reason }),
  ];
  if (counts.length) {
    children.push(el("div", { class: "brain-state-counts", text: `Recorded setup counts · ${counts.join(" · ")}` }));
  }
  if (model.explanation) {
    children.push(el("div", { class: "brain-persisted-explanation" }, [
      el("span", { class: "brain-detail-label", text: "Persisted BRAIN explanation" }),
      el("span", { text: model.explanation }),
    ]));
  } else if (model.cycle) {
    children.push(el("div", { class: "brain-persisted-explanation", text: "No persisted explanation headline is available for this cycle." }));
  }

  if (model.activeSetups.length === 1) {
    children.push(persistedObservationCard(model.activeSetups[0]));
  } else if (model.activeSetups.length > 1) {
    children.push(el("div", { class: "brain-not-current", text: `${model.activeSetups.length} WATCH/QUALIFIED setups are recorded at the latest cycle; this display does not select one.` }));
    children.push(...model.activeSetups.slice(0, 3).map(persistedObservationCard));
  } else if (model.observations.length) {
    children.push(el("div", { class: "brain-observation-record" }, [
      el("div", { class: "brain-detail-label", text: "Latest recorded setup observation" }),
      el("div", { text: `${familyLabel(model.observations[0].family)} · ${directionLabel(model.observations[0].direction)} · ${setupStateLabel(model.observations[0].state)}` }),
      persistedObservationCard(model.observations[0]),
    ]));
  } else if (model.cycle) {
    const note = model.snapshotState === "NO_SETUP"
      ? "The persisted snapshot state is NO_SETUP; there is no setup observation row for this cycle."
      : "No setup observation rows are associated with this cycle; an empty row list is not treated as a no-setup conclusion.";
    children.push(el("div", { class: "brain-observation-record", text: note }));
  }

  if (model.observations.length > 1) {
    const visible = model.activeSetups.length > 1 ? model.activeSetups.slice(0, 3) : model.observations.slice(0, 1);
    const visibleIds = new Set(visible.map((row) => row.id));
    const remaining = model.observations.filter((row) => !visibleIds.has(row.id));
    if (remaining.length) {
      children.push(el("details", { class: "brain-recorded-details" }, [
        el("summary", { text: `Other setup observations from this cycle (${remaining.length})` }),
        ...remaining.map(persistedObservationCard),
      ]));
    }
  }
  if (model.cycle?.observation_count > model.observations.length) {
    children.push(el("div", { class: "chart-note", text: `The API returned ${model.observations.length} of ${model.cycle.observation_count} cycle observation rows; additional rows are outside its response limit.` }));
  }
  if (model.notes.length) {
    children.push(el("details", { class: "brain-recorded-details" }, [
      el("summary", { text: `Recorded cycle notes (${model.notes.length})` }),
      el("ul", {}, model.notes.map((note) => el("li", { text: String(note) }))),
    ]));
  }
  return el("section", {
    class: "card terminal-card brain-status-card",
    "aria-label": "Persisted BRAIN status",
    dataset: { status: model.statusLabel },
  }, children);
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

function explanationCard(dashboard, brainModel) {
  if (!brainModel?.currentDecisionAvailable) {
    return el("section", { class: "card terminal-card", "aria-label": "Current explanation unavailable" }, [
      el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "Current explanation" })]),
      el("div", { class: "brain-not-current", role: "status", text: `No fresh BRAIN explanation is shown: ${brainModel?.reason || "no matching persisted cycle"}` }),
      el("div", { class: "chart-note", text: "The last persisted BRAIN headline, when present, is shown in the BRAIN status card." }),
    ]);
  }
  const explanation = dashboard?.explanation;
  const sections = Array.isArray(explanation?.sections) ? explanation.sections : [];
  const limitations = Array.isArray(explanation?.limitations) ? explanation.limitations : [];
  // The collapsed label is plain English; the verbatim backend headline and
  // sections stay inside as the Level-3 technical record.
  const meta = dashboard?.meta || {};
  const summaryLine = explanation && typeof explanation === "object" && explanation.available !== false
    ? `Technical record · ${meta.symbol || "UNKNOWN"} · ${timeframeLabel(meta.timeframe)} · ${setupStateLabel(dashboard?.qualification?.state)}`
    : "Grounded explanation";
  const body = [el("div", { class: "chart-note", text: "Read-only dashboard explanation for the exact stored close. The persisted BRAIN headline is displayed separately in the BRAIN status card." })];
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

function stopBrainStatusRefresh() {
  brainRefreshGeneration += 1;
  if (brainRefreshTimer !== null) clearTimeout(brainRefreshTimer);
  if (brainRefreshRequestTimeout !== null) clearTimeout(brainRefreshRequestTimeout);
  brainRefreshTimer = null;
  brainRefreshRequestTimeout = null;
  if (brainRefreshController) brainRefreshController.abort();
  brainRefreshController = null;
}

function replaceBrainStatusCard(model) {
  if (!activeBrainStatusCard?.parentNode) return false;
  const replacement = brainStatusCard(model);
  activeBrainStatusCard.parentNode.replaceChild(replacement, activeBrainStatusCard);
  activeBrainStatusCard = replacement;
  return true;
}

function showBrainStatusStale(view, model) {
  if (activeChart) activeChart.destroy();
  activeChart = null;
  activeBrainStatusCard = brainStatusCard(model);
  view.className = "view terminal-dashboard";
  clearNode(view).append(
    activeBrainStatusCard,
    el("div", {
      class: "brain-refresh-stale chart-note",
      role: "alert",
      text: `BRAIN status has not refreshed for ${Math.floor(BRAIN_STATUS_STALE_AFTER_MS / 1000)} seconds. The persisted record above is historical; current decision panels are withheld until refresh succeeds.`,
    }),
  );
  setTopbarWarning();
}

function startBrainStatusRefresh({ view, prefs, pageGeneration, dashboard, forward, brainModel }) {
  const refreshGeneration = ++brainRefreshGeneration;
  const lastDashboard = dashboard;
  let latestForward = forward;
  let latestModel = brainModel;
  let lastSuccessAt = forward ? Date.now() : null;
  let staleScreen = false;

  const isCurrent = () => refreshGeneration === brainRefreshGeneration &&
    pageGeneration === renderGeneration;
  const schedule = () => {
    if (!isCurrent()) return;
    brainRefreshTimer = setTimeout(() => {
      brainRefreshTimer = null;
      void refresh();
    }, BRAIN_STATUS_REFRESH_INTERVAL_MS);
  };

  async function refresh() {
    if (!isCurrent()) return;
    const controller = new AbortController();
    brainRefreshController = controller;
    const timeout = setTimeout(
      () => controller.abort(),
      BRAIN_STATUS_REFRESH_TIMEOUT_MS,
    );
    brainRefreshRequestTimeout = timeout;
    try {
      const nextForward = await api.forward({
        limit: 50,
        symbol: prefs.preferredSymbol || undefined,
        timeframe: prefs.preferredTimeframe || undefined,
      }, { signal: controller.signal });
      if (!isCurrent()) return;

      const nextModel = brainStatusViewModel(lastDashboard, nextForward);
      const previousCycleId = latestForward?.status?.latest_cycle?.cycle_id ?? null;
      const nextCycleId = nextForward?.status?.latest_cycle?.cycle_id ?? null;
      const cycleChanged = previousCycleId !== nextCycleId;
      const decisionAvailabilityChanged =
        latestModel.currentDecisionAvailable !== nextModel.currentDecisionAvailable;
      latestForward = nextForward;
      latestModel = nextModel;
      lastSuccessAt = Date.now();

      if (staleScreen || cycleChanged || decisionAvailabilityChanged) {
        staleScreen = false;
        void renderDashboard(view);
        return;
      }
      updateTopbar(lastDashboard, nextForward);
      replaceBrainStatusCard(nextModel);
    } catch {
      if (!isCurrent()) return;
      if (lastSuccessAt !== null &&
          Date.now() - lastSuccessAt >= BRAIN_STATUS_STALE_AFTER_MS) {
        const staleModel = brainStatusViewModel(lastDashboard, latestForward, {
          refreshStale: true,
        });
        latestModel = staleModel;
        if (!staleScreen) {
          showBrainStatusStale(view, staleModel);
          staleScreen = true;
        }
      }
    } finally {
      clearTimeout(timeout);
      if (brainRefreshRequestTimeout === timeout) brainRefreshRequestTimeout = null;
      if (brainRefreshController === controller) brainRefreshController = null;
      schedule();
    }
  }

  schedule();
}

export function disposeDashboard() {
  renderGeneration += 1;
  stopBrainStatusRefresh();
  activeBrainStatusCard = null;
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

function hierarchyNotCurrentCard(label, model) {
  return el("section", { class: "card terminal-card mtf-compact", "aria-label": label }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: label }),
      el("span", { class: "card-hint", text: "not a persisted current BRAIN cycle" }),
    ]),
    el("div", { class: "mtf-compact-unavailable", role: "status", text: `Current hierarchy is withheld: ${model?.reason || "no matching completed BRAIN cycle"}` }),
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

export async function renderDashboard(view) {
  stopBrainStatusRefresh();
  activeBrainStatusCard = null;
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
    api.forward({
      limit: 50,
      symbol: prefs.preferredSymbol || undefined,
      timeframe: prefs.preferredTimeframe || undefined,
    }),
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
  const brainModel = brainStatusViewModel(dashboard, forward);
  updateTopbar(dashboard, forward);

  const chart = chartCard(dashboard, prefs, brainModel);
  const brainCard = brainStatusCard(brainModel);
  const hierarchySummary = brainModel.currentDecisionAvailable
    ? compactHierarchyStrip(dashboard)
    : hierarchyNotCurrentCard("Multi-timeframe status", brainModel);
  const sideCards = brainModel.currentDecisionAvailable
    ? [brainCard, verdictCard(dashboard, forward), botWatchingCard(dashboard), planCard(dashboard)]
    : [brainCard];
  clearNode(view).append(
    el("div", { class: "primary-layout" }, [
      el("div", { class: "chart-stack" }, [
        chart.node,
        hierarchySummary,
      ]),
      // Current verdict/setup/plan cards are shown only when they match the
      // latest completed persisted BRAIN cycle at this exact closed boundary.
      // When stopped or stale, the BRAIN card shows recorded facts instead.
      el("div", { class: "side-stack" }, sideCards),
    ]),
    brainModel.currentDecisionAvailable
      ? multiTimeframeCard(dashboard)
      : hierarchyNotCurrentCard("Multi-timeframe ladder", brainModel),
    el("div", { class: "tertiary-grid" }, [
      marketNowCard(dashboard),
      explanationCard(dashboard, brainModel),
    ]),
    el("div", { class: "secondary-grid" }, [
      recentDecisionsCard(forward),
      performanceCard(forward, dashboard.meta || {}),
    ]),
    systemDetailsCard(dashboard, forward),
  );
  chart.mount();
  activeChart = chart;
  activeBrainStatusCard = brainCard;
  startBrainStatusRefresh({
    view,
    prefs,
    pageGeneration: generation,
    dashboard,
    forward,
    brainModel,
  });
}
