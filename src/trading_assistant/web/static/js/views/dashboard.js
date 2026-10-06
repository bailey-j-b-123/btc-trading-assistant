/**
 * One-page trading terminal. All market, setup, plan, observation, performance,
 * and system values are rendered from existing backend API responses. The
 * frontend does not calculate a trading verdict, create levels, or invent
 * candles, outcomes, or performance metrics.
 */

import { api } from "../api.js";
import {
  buildDecisionRequest,
  confirmationSummary,
  decisionAvailability,
  DECISION_LABELS,
  DECISION_MEANINGS,
} from "../decision.js";
import {
  applyOverlays,
  createPriceChart,
  destroyPriceChart,
  OVERLAY_COLORS,
  setCandles,
  toChartCandles,
} from "../chart.js";
import {
  directionArrow,
  directionLabel,
  displayOrUnknown,
  familyLabel,
  formatUtc,
  isMissing,
  shortId,
} from "../format.js";
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

export function hasValidTradePlan(dashboard) {
  return Boolean(
    dashboard?.qualification?.available === true &&
    dashboard.qualification.state === "QUALIFIED" &&
    dashboard?.planning?.state === "PLANNABLE" &&
    dashboard?.plan &&
    dashboard.plan.state === "PLANNABLE",
  );
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

function watchMissingReasons(candidate) {
  if (!candidate) return [];
  const reasons = [];
  for (const rule of candidate.rules || []) {
    if (!["failed", "pending"].includes(rule.outcome)) continue;
    if (typeof rule.reason === "string" && rule.reason.trim()) reasons.push(rule.reason.trim());
  }
  for (const rule of candidate.rules || []) {
    for (const evidence of Array.isArray(rule.evidence) ? rule.evidence : []) {
      if (evidence.status !== "unknown" && evidence.status !== "opposing") continue;
      if (typeof evidence.reason === "string" && evidence.reason.trim()) reasons.push(evidence.reason.trim());
    }
  }
  return [...new Set(reasons)];
}

function decisionExplanation(dashboard, candidate, plannable) {
  const qualification = dashboard?.qualification || {};
  const planning = dashboard?.planning || {};
  if (plannable) return "A deterministic trade plan is available for this snapshot.";
  if (qualification.available !== true) {
    return "The current qualification state is unavailable from the backend.";
  }

  if (qualification.state === "WATCH") {
    const missing = watchMissingReasons(candidate);
    if (missing.length) return `Still missing / not confirmed: ${missing.join(" · ")}`;
    const reasons = backendReasons(qualification.reasons);
    if (reasons.length) return reasons.join(" · ");
    return "The backend reports WATCH; no additional reason was supplied.";
  }

  if (qualification.state === "NO_SETUP") {
    const reasons = backendReasons(qualification.reasons);
    return reasons.length
      ? reasons.join(" · ")
      : "The deterministic engine reports no active setup in this snapshot.";
  }

  if (qualification.state === "QUALIFIED") {
    const reasons = backendReasons(planning.reasons);
    if (reasons.length) return `The setup is qualified, but the planner did not produce a plan: ${reasons.join(" · ")}`;
    if (typeof planning.state_detail === "string" && planning.state_detail.trim()) {
      return `The setup is qualified, but the planner did not produce a plan: ${planning.state_detail}`;
    }
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
    if (qualification.state === "NO_SETUP") plannedCount = 0;
    else if (qualifiedCount === 1 && dashboard?.planning?.state) plannedCount = plannable ? 1 : 0;
  }

  let state = "UNKNOWN";
  let tone = "unknown";
  if (plannable) {
    state = "PLANNABLE";
    tone = "green";
  } else if (qualification.available === true && qualification.state === "WATCH") {
    state = "WATCH";
    tone = "amber";
  } else if (qualification.available === true &&
    (qualification.state === "NO_SETUP" || qualification.state === "QUALIFIED")) {
    state = "NO TRADE";
    tone = "neutral";
  }

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

function chartCard(dashboard, initialPrefs) {
  const meta = dashboard?.meta || {};
  const rows = Array.isArray(dashboard?.market?.candles) ? dashboard.market.candles : [];
  const validCandles = toChartCandles(rows);
  const chartLabel = `${meta.symbol || "UNKNOWN"} ${timeframeLabel(meta.timeframe)} candlestick chart`;
  const host = el("div", { class: "chart-wrap terminal-chart-wrap", "aria-label": chartLabel });
  const toolbar = el("div", { class: "chart-toolbar", role: "group", "aria-label": "Chart overlays" });
  const handleRef = { current: null };

  const overlayControls = [
    ["zones", "S/R levels", OVERLAY_COLORS.zones],
    ["range", "Range", OVERLAY_COLORS.range],
    ["equalLevels", "Liquidity", OVERLAY_COLORS.equalLevels],
    ["planLevels", "Plan", OVERLAY_COLORS.entry],
    ["swings", "Swing points", OVERLAY_COLORS.swings],
  ];
  for (const [key, label, color] of overlayControls) {
    const button = el("button", {
      class: "overlay-toggle",
      type: "button",
      "aria-pressed": String(initialPrefs.overlays?.[key] !== false),
      "aria-label": `${label} chart overlay`,
      style: { "--swatch": color },
      onclick: () => {
        const next = loadPrefs();
        next.overlays[key] = next.overlays[key] === false;
        savePrefs(next);
        button.setAttribute("aria-pressed", String(next.overlays[key]));
        if (handleRef.current) {
          applyOverlays(handleRef.current, {
            overlays: dashboard?.overlays || {},
            plan: hasValidTradePlan(dashboard) ? dashboard.plan : null,
            prefs: next,
          });
        }
      },
    }, [el("span", { class: "swatch", "aria-hidden": "true" }), label]);
    toolbar.append(button);
  }

  const card = el("section", {
    class: "card terminal-card terminal-chart-card",
    "aria-label": `${meta.symbol || "UNKNOWN"} price chart`,
  }, [
    el("div", { class: "card-head" }, [
      el("div", {}, [
        el("div", { class: "chart-heading-title", text: `${meta.symbol || "UNKNOWN"} · ${timeframeLabel(meta.timeframe)}` }),
        el("div", { class: "chart-heading-meta", text: "Stored closed candles · deterministic overlays only" }),
      ]),
      toolbar,
    ]),
    host,
    el("div", { class: "chart-note terminal-chart-note", text: "Only stored market rows and backend-produced levels are drawn. Missing data is left unavailable." }),
  ]);

  if (!validCandles.length) {
    host.append(chartEmpty(
      "Candle data unavailable",
      rows.length
        ? "The stored candle payload contains no renderable rows. No substitute data is shown."
        : "No stored closed candles were returned for this symbol and timeframe.",
    ));
  }

  return {
    node: card,
    mount() {
      if (!validCandles.length) return;
      try {
        const handle = createPriceChart(host);
        if (!handle) {
          host.append(chartEmpty("Chart unavailable", "The chart library did not load, so stored candles cannot be drawn. Stored data has not been replaced."));
          return;
        }
        handleRef.current = handle;
        setCandles(handle, rows);
        applyOverlays(handle, {
          overlays: dashboard?.overlays || {},
          plan: hasValidTradePlan(dashboard) ? dashboard.plan : null,
          prefs: loadPrefs(),
        });
      } catch {
        if (handleRef.current) destroyPriceChart(handleRef.current);
        handleRef.current = null;
        clearNode(host).append(chartEmpty("Chart unavailable", "Stored data could not be rendered. No substitute candles are shown."));
      }
    },
    destroy() {
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
    ? `Journal decision · latest ${latest.decision}`
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
          : `Controls unavailable: ${availability.reason}`,
      }),
    ]),
  ]);
}

function openDecisionConfirmation(dashboard, decision) {
  const summary = confirmationSummary(dashboard);
  let submitting = false;
  const row = (label, value) => el("div", { class: "kv" }, [
    el("span", { class: "k", text: label }),
    el("span", { class: "v mono", text: value }),
  ]);
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
      row("Setup state", `${summary.setupState} · ${familyLabel(summary.family)} · ${directionLabel(summary.direction)}`),
      row("Plan state", summary.planState || "UNKNOWN"),
      row("Entry", displayOrUnknown(summary.entry)),
      row("Stop", displayOrUnknown(summary.stop)),
      row("Targets", summary.targets.length ? summary.targets.map((target) => displayOrUnknown(target)).join(", ") : "UNKNOWN"),
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
  return el("section", {
    class: "card terminal-card verdict-card",
    "aria-label": "Current verdict",
  }, [
    el("div", { class: "verdict-kicker", text: "Current verdict" }),
    el("div", { class: "verdict-state", dataset: { tone: model.tone }, role: "status", text: model.state }),
    el("div", { class: "verdict-identity" }, [
      el("span", { class: "verdict-direction", text: direction }),
      el("span", { class: "tag verdict-family", text: family }),
    ]),
    el("p", { class: "verdict-summary", text: model.explanation }),
    decisionCard(dashboard),
    candidateCounts(model),
  ]);
}

function planLevel(label, value, tone = null) {
  return el("div", { class: "plan-level", dataset: tone ? { tone } : {} }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: displayOrUnknown(value) }),
  ]);
}

function planningReason(dashboard) {
  const planning = dashboard?.planning || {};
  const reasons = backendReasons(planning.reasons);
  if (reasons.length) return reasons.join(" · ");
  return typeof planning.state_detail === "string" && planning.state_detail.trim()
    ? planning.state_detail
    : null;
}

function planCard(dashboard) {
  const plan = hasValidTradePlan(dashboard) ? dashboard.plan : null;
  const body = [];
  if (!plan) {
    body.push(el("div", { class: "plan-empty", text: "No qualified trade plan right now." }));
    const reason = planningReason(dashboard);
    if (reason) body.push(el("div", { class: "plan-reason", text: reason }));
  } else {
    body.push(
      el("div", { class: "plan-levels" }, [
        planLevel("Direction", directionLabel(plan.direction)),
        planLevel("Entry", plan.entry?.value, "green"),
        planLevel("Stop", plan.stop?.value, "red"),
        planLevel("Invalidation", plan.invalidation?.value, "amber"),
        planLevel("Risk / unit", plan.risk_per_unit),
      ]),
    );
    const targets = Array.isArray(plan.targets) ? plan.targets : [];
    const targetRows = targets.map((target, index) => el("div", { class: "plan-target-row" }, [
      el("span", { class: "target-index", text: `T${index + 1}` }),
      el("span", { class: "target-value", text: displayOrUnknown(target?.level?.value) }),
      el("span", { class: "target-r", text: isMissing(target?.r_multiple) ? "R/R UNKNOWN" : `${target.r_multiple} R` }),
    ]));
    body.push(el("div", { class: "plan-targets" }, [
      el("div", { class: "plan-targets-title", text: "Targets · risk / reward" }),
      ...(targetRows.length
        ? targetRows
        : [el("div", { class: "plan-reason", text: "Target levels unavailable from the backend." })]),
    ]));
    body.push(el("div", { class: "plan-caveat", text: "Deterministic paper plan only · not an order, fill, position, or profit." }));
  }

  return el("section", { class: "card terminal-card plan-card", "aria-label": "Trade plan" }, [
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "Trade plan" })]),
    ...body,
  ]);
}

function candidateEvidence(dashboard) {
  const candidate = activeCandidate(dashboard);
  const positive = [];
  const missing = [];
  if (!candidate) return { candidate: null, positive, missing };

  const seenPositive = new Set();
  const seenMissing = new Set();
  for (const rule of candidate.rules || []) {
    for (const item of Array.isArray(rule.evidence) ? rule.evidence : []) {
      const reason = typeof item.reason === "string" ? item.reason.trim() : "";
      if (!reason) continue;
      const row = {
        category: item.category || "Evidence",
        timeframe: item.timeframe || null,
        ruleId: rule.rule_id || null,
        reason,
      };
      if (item.status === "supportive" && !seenPositive.has(reason)) {
        positive.push(row);
        seenPositive.add(reason);
      } else if (["opposing", "unknown"].includes(item.status) && !seenMissing.has(reason)) {
        missing.push(row);
        seenMissing.add(reason);
      }
    }
    if (["failed", "pending"].includes(rule.outcome) && typeof rule.reason === "string" && rule.reason.trim()) {
      const reason = rule.reason.trim();
      if (!seenMissing.has(reason)) {
        missing.push({ category: rule.outcome, timeframe: null, ruleId: rule.rule_id || null, reason });
        seenMissing.add(reason);
      }
    }
  }
  return { candidate, positive, missing };
}

function evidenceColumn(title, tone, rows, emptyText) {
  const items = rows.map((row) => el("div", { class: "terminal-evidence-item" }, [
    el("div", { class: "evidence-meta", text: [row.category, row.timeframe, row.ruleId].filter(Boolean).join(" · ") }),
    el("div", { class: "evidence-reason", text: row.reason }),
  ]));
  return el("div", { class: "evidence-col" }, [
    el("h4", {}, [
      el("span", { class: "dot", style: { background: tone } }),
      title,
    ]),
    ...(items.length ? items : [el("div", { class: "evidence-empty", text: emptyText })]),
  ]);
}

function evidenceCard(dashboard) {
  const evidence = candidateEvidence(dashboard);
  const subtitle = evidence.candidate
    ? `${familyLabel(evidence.candidate.family)} · ${directionLabel(evidence.candidate.direction)}`
    : "No active candidate in this snapshot";
  return el("section", { class: "card terminal-card", "aria-label": "Deterministic evidence" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Evidence" }),
      el("span", { class: "card-hint", text: subtitle }),
    ]),
    el("div", { class: "evidence-grid" }, [
      evidenceColumn("FOR THE SETUP", "var(--green)", evidence.positive, "No supportive evidence was supplied."),
      evidenceColumn("AGAINST / STILL MISSING", "var(--amber)", evidence.missing, "No opposing or missing-rule detail was supplied."),
    ]),
  ]);
}

function scenarioText(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function trendSummaryText(trend) {
  if (!trend || typeof trend !== "object") return "UNKNOWN";
  if (trend.sufficient !== true) {
    const reason = scenarioText(trend.reason);
    return reason ? `UNKNOWN (${reason})` : "UNKNOWN (insufficient structure)";
  }
  const direction = String(trend.direction || "unknown").toUpperCase();
  return `${direction} · ${trend.transition || "unknown"} · ${trend.momentum || "unknown"}`;
}

function volatilitySummaryText(volatility) {
  if (!volatility || typeof volatility !== "object") return "UNKNOWN";
  if (volatility.available !== true) {
    const reason = scenarioText(volatility.reason);
    return reason ? `UNKNOWN (${reason})` : "UNKNOWN";
  }
  const value = isMissing(volatility.atr_percent_of_price) ? "?" : volatility.atr_percent_of_price;
  const label = scenarioText(volatility.direction?.label);
  if (!label || label === "unknown") return `${value}% of price · trend unknown`;
  return `${value}% of price · ${label}`;
}

function volumeSummaryText(volume) {
  if (!volume || typeof volume !== "object") return "UNKNOWN";
  if (volume.sufficient !== true) {
    const reason = scenarioText(volume.reason);
    return reason ? `UNKNOWN (${reason})` : "UNKNOWN";
  }
  const value = isMissing(volume.relative_volume) ? "?" : volume.relative_volume;
  const label = scenarioText(volume.direction?.label);
  if (!label || label === "unknown") return `${value}x average · trend unknown`;
  return `${value}x average · ${label}`;
}

function rangeSummaryText(range) {
  if (!range || typeof range !== "object") return "UNKNOWN";
  if (range.active !== true) return `no active range (${range.transition || "unknown"})`;
  const detected = range.detected && typeof range.detected === "object" ? range.detected : null;
  if (!detected) return "active range reported without detected levels";
  return `${displayOrUnknown(detected.range_low)} → ${displayOrUnknown(detected.range_high)} · ${range.transition || "unknown"}`;
}

function zoneBandText(zone) {
  if (!zone || typeof zone !== "object") return null;
  return `${displayOrUnknown(zone.band_low)}–${displayOrUnknown(zone.band_high)} (${zone.touch_count ?? "?"} touches)`;
}

function equalLevelText(entry) {
  if (!entry || typeof entry !== "object") return null;
  return `${displayOrUnknown(entry.level)} (${entry.type || "level"} ×${entry.member_count ?? "?"})`;
}

function marketNowUnavailable(reason) {
  return el("section", { class: "card terminal-card", "aria-label": "Market now" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Market now" }),
      el("span", { class: "card-hint", text: "backend facts · this close" }),
    ]),
    el("div", { class: "plan-reason", text: reason || "Market-state facts unavailable from the backend." }),
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
    chips.push(`breakout ${directionLabel(item.direction)} @ ${displayOrUnknown(item.close)}`);
  }
  for (const item of Array.isArray(breakout.acceptances) ? breakout.acceptances : []) {
    chips.push(`held retest ${directionLabel(item.direction)}`);
  }
  for (const item of Array.isArray(breakout.rejections) ? breakout.rejections : []) {
    chips.push(item.kind || "rejection");
  }
  for (const item of Array.isArray(breakout.sweeps) ? breakout.sweeps : []) {
    chips.push(`sweep ${directionLabel(item.direction)} → ${displayOrUnknown(item.reclaim_close)}`);
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
  const children = [
    el("div", { class: "performance-summary" }, [
      performanceMetric("Trend", trendSummaryText(state.trend)),
      performanceMetric("Volatility", volatilitySummaryText(state.volatility)),
      performanceMetric("Volume", volumeSummaryText(state.volume)),
      performanceMetric("Range", rangeSummaryText(state.range)),
    ]),
    el("div", {
      class: "chart-note",
      text: `Nearest levels: support ${zoneBandText(levels.nearest_support) || "none in range"} · ` +
        `resistance ${zoneBandText(levels.nearest_resistance) || "none in range"} · ` +
        `equal below ${equalLevelText(levels.nearest_level_below) || "none"} · ` +
        `equal above ${equalLevelText(levels.nearest_level_above) || "none"} · ` +
        `${levels.zone_count ?? "?"} zones · ${levels.equal_level_count ?? "?"} equal levels`,
    }),
    el("div", { class: "chart-note", text: `Event catalog: ${eventCountsLine(events)}` }),
  ];
  const latestBreakout = events.breakouts?.latest;
  if (latestBreakout) {
    children.push(latestEventLine(
      "Latest breakout",
      latestBreakout,
      `${directionLabel(latestBreakout.direction)} of ${latestBreakout.reference_type || "unknown reference"}`,
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
    const chipSpans = chips.map((text) => el("span", { class: "outcome-chip", text }));
    children.push(el("div", { class: "outcome-breakdown", "aria-label": "Fresh breakout activity" }, chipSpans));
  }
  const requested = Array.isArray(htf.requested) ? htf.requested : [];
  if (!requested.length) {
    children.push(el("div", { class: "chart-note", text: scenarioText(htf.note) || "No higher timeframes requested." }));
  } else {
    const contexts = htf.contexts && typeof htf.contexts === "object" ? htf.contexts : {};
    for (const timeframe of requested) {
      const context = contexts[timeframe] && typeof contexts[timeframe] === "object" ? contexts[timeframe] : {};
      children.push(el("div", {
        class: "chart-note",
        text: `${timeframe}: trend ${context.trend || "UNKNOWN"}` +
          (context.available === false ? " (unavailable)" : "") +
          (context.reason ? ` — ${context.reason}` : ""),
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

function pendingRequiredText(pending) {
  const items = Array.isArray(pending) ? pending : [];
  if (!items.length) return "none";
  return items.map((item) => `${item.rule || "?"} — ${item.reason || "no reason"}`).join("; ");
}

function liveSetupNode(setup) {
  const failed = Array.isArray(setup.failed_rules) ? setup.failed_rules : [];
  const passed = Array.isArray(setup.passed_rules) ? setup.passed_rules : [];
  const vetoedBy = Array.isArray(setup.vetoed_by) ? setup.vetoed_by : [];
  return el("div", { class: "terminal-evidence-item" }, [
    el("div", {
      class: "evidence-meta",
      text: `${familyLabel(setup.family)} · ${directionLabel(setup.direction)} · ${setup.state || "UNKNOWN"} · ` +
        `age ${setup.age_bars ?? "?"}/${setup.max_bars ?? "?"} bars · ${setup.bars_remaining ?? "?"} left` +
        (setup.vetoed === true ? ` · VETOED by ${vetoedBy.length ? vetoedBy.join(", ") : "unknown rule"}` : "") +
        (setup.created_at ? ` · seeded ${formatUtc(setup.created_at)}` : ""),
    }),
    el("div", { class: "evidence-reason", text: `Passed: ${passed.length ? passed.join(", ") : "none"}` }),
    el("div", { class: "evidence-reason", text: `Failed: ${failed.length ? failed.join(", ") : "none"}` }),
    el("div", { class: "evidence-reason", text: `Pending required: ${pendingRequiredText(setup.pending_required)}` }),
  ]);
}

function strengthenBlock(title, side) {
  const direction = side?.direction || title;
  const developing = Array.isArray(side?.developing_setups) ? side.developing_setups : [];
  const starters = side?.to_start_a_setup && typeof side.to_start_a_setup === "object" ? side.to_start_a_setup : {};
  const rows = [
    el("div", { class: "evidence-meta", text: title }),
    developing.length || side?.none_developing !== true
      ? null
      : el("div", { class: "evidence-reason", text: `No developing ${direction} setups.` }),
    ...developing.map((item) => el("div", {
      class: "evidence-reason",
      text: `setup ${shortId(item.setup_id)} (${item.state || "UNKNOWN"}): ` +
        `still required: ${pendingRequiredText(item.pending_required)}`,
    })),
    ...Object.entries(starters).map(([family, text]) => el("div", {
      class: "evidence-reason",
      text: `${familyLabel(family)} starts with: ${text}`,
    })),
  ];
  return el("div", { class: "terminal-evidence-item" }, rows);
}

function planLevelText(level) {
  if (!level || typeof level !== "object" || isMissing(level.value)) return null;
  const source = level.source_type ? ` (${level.source_type})` : "";
  return `${displayOrUnknown(level.value)}${source}`;
}

function invalidateNode(item) {
  const evidence = Array.isArray(item.invalidation_evidence) ? item.invalidation_evidence : [];
  const vetoedBy = Array.isArray(item.vetoed_by) ? item.vetoed_by : [];
  const rows = [
    el("div", {
      class: "evidence-meta",
      text: `setup ${shortId(item.setup_id)} · ${item.state || "UNKNOWN"} · ` +
        `${item.bars_remaining ?? "?"}/${item.max_bars ?? "?"} bars left` +
        (item.vetoed === true ? ` · VETOED by ${vetoedBy.length ? vetoedBy.join(", ") : "unknown rule"}` : ""),
    }),
    ...(evidence.length
      ? evidence.map((entry) => el("div", {
          class: "evidence-reason",
          text: `${entry.rule || "?"}: ${entry.outcome || "UNKNOWN"} — ${entry.reason || "no reason"}`,
        }))
      : [el("div", { class: "evidence-reason", text: "No invalidation/lifecycle evidence yet." })]),
  ];
  const entry = planLevelText(item.plan_entry);
  const stop = planLevelText(item.plan_stop);
  const invalidation = planLevelText(item.plan_invalidation);
  if (entry || stop || invalidation) {
    rows.push(el("div", {
      class: "evidence-reason",
      text: `Selected plan — entry ${entry || "?"} · stop ${stop || "?"} · invalidation ${invalidation || "?"}`,
    }));
  }
  return el("div", { class: "terminal-evidence-item" }, rows);
}

function scenarioCard(dashboard) {
  const scenario = dashboard?.scenario;
  if (!scenario || typeof scenario !== "object" || scenario.available !== true) {
    const reason = scenario && typeof scenario === "object" ? scenarioText(scenario.reason) : null;
    return el("section", { class: "card terminal-card", "aria-label": "Scenario" }, [
      el("div", { class: "section-title-row" }, [
        el("h2", { class: "card-title", text: "Scenario" }),
        el("span", { class: "card-hint", text: "answers from backend facts" }),
      ]),
      el("div", { class: "plan-reason", text: reason || "Scenario answers unavailable from the backend." }),
    ]);
  }
  const seeing = scenario.bot_seeing && typeof scenario.bot_seeing === "object" ? scenario.bot_seeing : {};
  const live = Array.isArray(seeing.live_setups) ? seeing.live_setups : [];
  const waiting = scenario.waiting_for && typeof scenario.waiting_for === "object" ? scenario.waiting_for : {};
  const pending = Array.isArray(waiting.pending) ? waiting.pending : [];
  const cases = Array.isArray(scenario.invalidate?.cases) ? scenario.invalidate.cases : [];
  return el("section", { class: "card terminal-card", "aria-label": "Scenario" }, [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Scenario" }),
      el("span", { class: "card-hint", text: "answers from backend facts" }),
    ]),
    el("p", { class: "verdict-summary", text: scenario.doing_now || "The backend supplied no summary." }),
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "The bot is seeing" })]),
    el("div", {
      class: "chart-note",
      text: `Aggregate ${seeing.state || "UNKNOWN"} (${seeing.status || "unknown status"}) · ` +
        `${seeing.live_count ?? live.length} live setup(s)`,
    }),
    ...(live.length
      ? live.map(liveSetupNode)
      : [el("div", { class: "plan-reason", text: "No live setups at this close." })]),
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "What would strengthen each side" })]),
    strengthenBlock("BULLISH case", scenario.strengthen_bullish),
    strengthenBlock("BEARISH case", scenario.strengthen_bearish),
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "Waiting for" })]),
    ...(pending.length
      ? pending.map((item) => el("div", { class: "terminal-evidence-item" }, [
          el("div", { class: "evidence-meta", text: item.rule || "?" }),
          el("div", {
            class: "evidence-reason",
            text: `${item.reason || "no reason"} (${Array.isArray(item.setup_ids) ? item.setup_ids.length : "?"} setup(s))`,
          }),
        ]))
      : [el("div", {
          class: "plan-reason",
          text: scenarioText(waiting.note) || "Nothing outstanding.",
        })]),
    el("div", { class: "section-title-row" }, [el("h2", { class: "card-title", text: "What would invalidate" })]),
    ...(cases.length
      ? cases.map(invalidateNode)
      : [el("div", { class: "plan-reason", text: "No live setups to invalidate." })]),
  ]);
}

function explanationCard(dashboard) {
  const explanation = dashboard?.explanation;
  const sections = Array.isArray(explanation?.sections) ? explanation.sections : [];
  const limitations = Array.isArray(explanation?.limitations) ? explanation.limitations : [];
  const body = [];
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
    el("summary", { text: scenarioText(explanation?.headline) || "Grounded explanation" }),
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
            const states = [item.setup_state || "UNKNOWN", item.plan_state].filter(Boolean);
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
        el("div", { class: "value", text: `Paper plans ${displayOrUnknown(metrics.paper_plan_count)} · settled ${displayOrUnknown(metrics.completed_count)} · eligible R ${displayOrUnknown(distribution.sample_size)} / ${displayOrUnknown(distribution.records_considered)} (${distribution.status || "UNKNOWN"}) · mean observed R ${displayOrUnknown(distribution.average)}` }),
      ]);
    })),
  ]);
}

function rateObservationText(rate) {
  const numerator = integerOrNull(rate?.numerator);
  const denominator = integerOrNull(rate?.denominator);
  if (numerator === null || denominator === null) return "UNKNOWN";
  const percentage = isMissing(rate.percentage) ? null : displayOrUnknown(rate.percentage);
  const measured = percentage === null ? "" : `${percentage}% · `;
  return `${measured}${numerator}/${denominator} · ${rate.status || "UNKNOWN"}`;
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
      el("span", { class: "value mono", text: `eligible ${displayOrUnknown(distribution.sample_size)} / ${displayOrUnknown(distribution.records_considered)} · ${distribution.status || "UNKNOWN"}` }),
    ]),
    el("div", { class: "note", text: `mean ${displayOrUnknown(distribution.average)} · median ${displayOrUnknown(distribution.median)} · min ${displayOrUnknown(distribution.minimum)} · max ${displayOrUnknown(distribution.maximum)}` }),
    el("div", { class: "note", text: distribution.definition || "Metric definition unavailable." }),
    (Array.isArray(distribution.excluded) && distribution.excluded.length)
      ? el("div", { class: "note", text: `Excluded: ${distribution.excluded.map((item) => `${item.value} ×${item.count}`).join(" · ")}` })
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
      performanceMetric("Eligible / considered R", `${displayOrUnknown(r.sample_size)} / ${displayOrUnknown(r.records_considered)}`, r.status || "UNKNOWN"),
      performanceMetric("Mean observed R", displayOrUnknown(r.average), "Backend distribution · not expectancy"),
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
  const metrics = [
    performanceMetric("Paper plans", model.paperPlans === null ? "UNKNOWN" : String(model.paperPlans)),
    performanceMetric("Settled outcomes", model.resolvedOutcomes === null ? "UNKNOWN" : String(model.resolvedOutcomes)),
    performanceMetric("Unresolved", model.unresolvedOutcomes === null ? "UNKNOWN" : String(model.unresolvedOutcomes)),
    performanceMetric("Eligible / considered R", rSample, model.rSampleStatus || "UNKNOWN"),
    performanceMetric("Mean observed R", displayOrUnknown(model.averageObservedR), "Descriptive OHLC observation · not expectancy"),
    performanceMetric("Mean friction-adjusted R", displayOrUnknown(model.averageFrictionAdjustedR), "Hypothetical only · not realised P&L"),
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
          model.outcomeStatusCounts.map((item) => el("span", { class: "outcome-chip", text: `${item.value}: ${item.count}` })),
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
      systemDetail("Data health", market.data_health || dashboard?.freshness?.status || "UNKNOWN"),
      systemDetail("Data health detail", market.data_health_detail || dashboard?.freshness?.reason || "UNKNOWN"),
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

  const chart = chartCard(dashboard, prefs);
  clearNode(view).append(
    el("div", { class: "primary-layout" }, [
      chart.node,
      el("div", { class: "side-stack" }, [
        verdictCard(dashboard, forward),
        planCard(dashboard),
      ]),
    ]),
    evidenceCard(dashboard),
    el("div", { class: "tertiary-grid" }, [
      marketNowCard(dashboard),
      scenarioCard(dashboard),
    ]),
    explanationCard(dashboard),
    el("div", { class: "secondary-grid" }, [
      recentDecisionsCard(forward),
      performanceCard(forward, dashboard.meta || {}),
    ]),
    systemDetailsCard(dashboard, forward),
  );
  chart.mount();
  activeChart = chart;
}
