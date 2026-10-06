/**
 * Step 12 presentation: LIVE forward paper validation.
 *
 * Read-only. Everything rendered here is a recorded fact or a derived
 * statistic: the closed-candle forward ledger, the runner heartbeat, the
 * data-health verdict, and the forward report. There is no order control, no
 * account, no balance, no position, no leverage, and no sizing anywhere — and
 * the comparison against historical validation is always shown as two labelled
 * sides, never as one merged number.
 */

import { api } from "../api.js";
import { displayOrUnknown, formatUtc, shortId } from "../format.js";
import { clearNode, el, emptyState, errorState, spinner } from "../util.js";

export const FORWARD_LABEL = "LIVE FORWARD VALIDATION — NOT REAL PERFORMANCE";
export const PAPER_LABEL = "PAPER OBSERVATION — NO REAL ORDER";
export const MARKET_DATA_LABEL = "LIVE MARKET DATA";
export const DISCLAIMER =
  "Paper trading and historical performance do not establish future profitability.";

function card(title, children) {
  return el("section", { class: "card" }, [
    el("h3", { class: "card-title", text: title }),
    ...children,
  ]);
}

function statCard(label, value, note) {
  return el("div", { class: "stat-card" }, [
    el("div", { class: "k", text: label }),
    el("div", { class: "v mono", text: String(value ?? "—") }),
    note ? el("div", { class: "s", text: note }) : null,
  ]);
}

function countValue(rows, value) {
  return (rows || []).find((item) => item.value === value)?.count ?? 0;
}

function healthTone(health) {
  if (health === "CURRENT") return "ok";
  if (health === "STALE" || health === "UNKNOWN") return "warn";
  if (health === "INCOMPLETE") return "warn";
  return "neutral";
}

function rateText(rate) {
  if (!rate) return "UNKNOWN";
  const counts = `numerator ${rate.numerator} / denominator ${rate.denominator}`;
  if (rate.percentage === null || rate.percentage === undefined) {
    return `${counts} · below reporting floor (status ${rate.status})`;
  }
  return `${rate.percentage}% · ${counts}`;
}

function distributionBlock(title, distribution) {
  const values = distribution?.values || [];
  return el("div", { class: "kv", style: { marginTop: "8px" } }, [
    el("div", { class: "k", text: title }),
    el("span", { class: "k", text: "Eligible / considered" }),
    el("span", {
      class: "v mono",
      text: `${distribution?.sample_size ?? 0} / ${distribution?.records_considered ?? 0}`,
    }),
    el("span", { class: "k", text: "Status" }),
    el("span", { class: "v mono", text: distribution?.status || "INSUFFICIENT_DATA" }),
    el("span", { class: "k", text: "Average / median" }),
    el("span", {
      class: "v mono",
      text: `${displayOrUnknown(distribution?.average)} / ${displayOrUnknown(distribution?.median)}`,
    }),
    el("span", { class: "k", text: "Min / max" }),
    el("span", {
      class: "v mono",
      text: `${displayOrUnknown(distribution?.minimum)} / ${displayOrUnknown(distribution?.maximum)}`,
    }),
    el("span", { class: "k", text: "Exact eligible observations" }),
    el("span", { class: "v mono", text: values.length ? values.join(", ") : "—" }),
    distribution?.definition
      ? el("span", { class: "k", text: "Definition" })
      : null,
    distribution?.definition ? el("span", { class: "v", text: distribution.definition }) : null,
    (distribution?.excluded || []).length
      ? el("span", { class: "k", text: "Excluded (never counted as wins/losses)" })
      : null,
    (distribution?.excluded || []).length
      ? el("span", {
          class: "v mono",
          text: distribution.excluded
            .map((item) => `${item.value} ×${item.count}`)
            .join(", "),
        })
      : null,
  ]);
}

function marketDataCard(status) {
  const market = status.market_data || {};
  const runner = status.runner;
  return card("LIVE MARKET DATA", [
    el("div", { class: "stat-banner", dataset: { tone: healthTone(market.data_health) } }, [
      el("span", { text: `${MARKET_DATA_LABEL}: ${market.data_health || "UNKNOWN"}` }),
      el("span", { class: "sub", text: market.data_health_detail || "no verdict yet" }),
    ]),
    el("div", { class: "stat-cards", style: { marginTop: "12px" } }, [
      statCard(
        "Latest stored closed candle",
        market.latest_stored_candle_open ? formatUtc(market.latest_stored_candle_open) : "UNKNOWN",
        "open time (UTC)",
      ),
      statCard(
        "Expected latest closed candle",
        market.expected_latest_closed_candle_open
          ? formatUtc(market.expected_latest_closed_candle_open)
          : "UNKNOWN",
        "from the clock and timeframe",
      ),
      statCard(
        "Staleness",
        market.staleness_intervals === null || market.staleness_intervals === undefined
          ? "UNKNOWN"
          : `${market.staleness_intervals} interval(s)`,
        `${market.missing_candle_count ?? "UNKNOWN"} missing candle(s)`,
      ),
      statCard(
        "Runner",
        runner ? runner.status : "never run",
        runner ? `last event ${formatUtc(runner.recorded_at)}` : "start it from the CLI",
      ),
      statCard(
        "Last successful processing",
        runner && runner.latest_cycle_as_of ? formatUtc(runner.latest_cycle_as_of) : "UNKNOWN",
        runner && runner.heartbeat_age_seconds !== null && runner.heartbeat_age_seconds !== undefined
          ? `heartbeat age ${runner.heartbeat_age_seconds}s`
          : "heartbeat age UNKNOWN",
      ),
      statCard(
        "Pending catch-up",
        status.sample?.pending_catch_up_boundaries ?? 0,
        "closed candles not yet processed",
      ),
    ]),
    runner && runner.last_error
      ? el("div", { class: "chart-note", style: { color: "var(--amber)" }, text: `Last runner error (${runner.error_type || "unknown"}): ${runner.last_error}` })
      : null,
    el("div", { class: "chart-note", style: { marginTop: "10px" }, text: market.closed_candle_policy || "" }),
  ]);
}

function currentStateCard(status) {
  const current = status.current_state || {};
  const plan = status.current_paper_plan;
  const setupCounts = current.setup_state_counts || {};
  const planCounts = current.plan_state_counts || {};
  return card("Current state at the last recorded close", [
    current.available
      ? el("div", { class: "card-hint", text: `Recorded ${formatUtc(current.recorded_at)} for the close at ${formatUtc(current.as_of)} · ${current.cycle_status || "UNKNOWN"}` })
      : el("div", { class: "card-hint", text: current.unavailable_reason || "no recorded close yet" }),
    el("div", { class: "stat-cards", style: { marginTop: "12px" } }, [
      statCard("Setup state", current.setup_state || "UNKNOWN", "aggregate snapshot state"),
      statCard("WATCH", setupCounts.WATCH ?? 0, "candidates on watch"),
      statCard("QUALIFIED", setupCounts.QUALIFIED ?? 0, "candidates qualified"),
      statCard("NO_SETUP", setupCounts.NO_SETUP ?? 0, "no candidate"),
      statCard("PLANNABLE", planCounts.PLANNABLE ?? 0, "Step 6 proposals"),
      statCard(
        "Unresolved paper plans",
        status.unresolved_paper_plan_count ?? 0,
        "outcomes still open",
      ),
    ]),
    current.explanation_headline
      ? el("div", { class: "chart-note", style: { marginTop: "10px" }, text: `Engine explanation headline: ${current.explanation_headline}` })
      : null,
    plan
      ? el("div", { class: "kv", style: { marginTop: "12px" } }, [
          el("span", { class: "k", text: PAPER_LABEL }),
          el("span", { class: "v", text: "current paper plan (frozen projection)" }),
          el("span", { class: "k", text: "Plan / setup" }),
          el("span", { class: "v mono", text: `${shortId(plan.plan_id)} · ${shortId(plan.setup_id)}` }),
          el("span", { class: "k", text: "Family / direction" }),
          el("span", { class: "v", text: `${plan.family} · ${plan.direction}` }),
          el("span", { class: "k", text: "Entry / stop" }),
          el("span", { class: "v mono", text: `${displayOrUnknown(plan.entry_level)} / ${displayOrUnknown(plan.stop_level)}` }),
          el("span", { class: "k", text: "Invalidation / risk" }),
          el("span", { class: "v mono", text: `${displayOrUnknown(plan.invalidation_level)} / ${displayOrUnknown(plan.risk_per_unit)}` }),
          el("span", { class: "k", text: "Targets" }),
          el("span", { class: "v mono", text: (plan.targets || []).map((value) => displayOrUnknown(value)).join(" → ") || "—" }),
          el("span", { class: "k", text: "Recorded at" }),
          el("span", { class: "v mono", text: formatUtc(plan.recorded_at) }),
          el("span", { class: "k", text: "Observation horizon" }),
          el("span", { class: "v mono", text: `${plan.observation_horizon_candles} closed candles` }),
        ])
      : el("div", { class: "chart-note", style: { marginTop: "12px" }, text: "No PLANNABLE proposal has been recorded yet. WATCH and NO_SETUP never create a paper observation." }),
  ]);
}

function observationsCard(payload) {
  const observations = (payload.observations || []).slice(0, 25);
  if (!observations.length) {
    return card("Latest forward observations", [
      emptyState(
        "No forward observations recorded",
        "Start the runner (python -m trading_assistant.forward_testing run) so each confirmed closed candle is recorded once.",
      ),
    ]);
  }
  return card("Latest forward observations", [
    el("div", { class: "chart-note", text: `${PAPER_LABEL}. Each row is a candidate state the deterministic engine recorded at one closed candle.` }),
    el("div", { class: "history-list", style: { marginTop: "10px" } }, observations.map((item) =>
      el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: formatUtc(item.as_of, { withDate: true }) }),
        el("span", { text: `${item.setup_family} · ${item.setup_direction}` }),
        el("span", { class: "mono", text: `${item.setup_state}${item.plan_state ? ` · ${item.plan_state}` : ""}${item.paper_plan_id ? " · PAPER PLAN" : ""}` }),
        el("span", { class: "mono", text: `health ${item.data_health}${item.missing_candle_count ? ` · missing ${item.missing_candle_count}` : ""}` }),
      ]),
    )),
  ]);
}

function paperPlansCard(payload) {
  const plans = (payload.paper_plans || []).slice(0, 25);
  if (!plans.length) {
    return card("Paper observations", [
      emptyState("No paper observations", "A paper observation exists only for a PLANNABLE Step 6 result."),
    ]);
  }
  return card("Paper observations — no real order", [
    el("div", { class: "history-list" }, plans.map((plan) => {
      const outcome = plan.latest_outcome;
      const observation = outcome ? outcome.observation : null;
      return el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: formatUtc(plan.plan_as_of, { withDate: true }) }),
        el("span", { text: `${plan.family} · ${plan.direction}` }),
        el("span", { class: "mono", text: `${displayOrUnknown(plan.entry_level)} / ${displayOrUnknown(plan.stop_level)} → ${(plan.targets || []).map((t) => displayOrUnknown(t)).join(", ")}` }),
        el("span", {
          class: "mono",
          text: observation
            ? `${observation.status} · entry ${observation.entry_reached ? "touched" : "not touched"}${observation.ambiguous ? " · AMBIGUOUS" : ""}${observation.incomplete ? " · INCOMPLETE_DATA" : ""} · through ${formatUtc(observation.observed_through, { withDate: true })} · ${plan.outcome_version_count} version(s)`
            : "NO OUTCOME YET — unresolved",
        }),
      ]);
    })),
  ]);
}

function forwardStatsCard(report) {
  const metrics = report.metrics || {};
  const children = [];
  if (!report.combined_metrics_available) {
    children.push(
      el("div", { class: "stat-banner", dataset: { tone: "warn" } }, [
        el("span", { text: "COMBINED FORWARD FIGURES WITHHELD" }),
        el("span", { class: "sub", text: report.combined_metrics_unavailable_reason || "recorded cycles use different versions" }),
      ]),
    );
    (report.version_cohorts || []).forEach((cohort) => {
      const cohortMetrics = cohort.metrics || {};
      children.push(
        el("div", { class: "kv", style: { marginTop: "12px" } }, [
          el("span", { class: "k", text: "Version" }),
          el("span", { class: "v mono", text: shortId(cohort.version_fingerprint) }),
          el("span", { class: "k", text: "Range" }),
          el("span", { class: "v mono", text: `${formatUtc(cohort.first_as_of)} → ${formatUtc(cohort.last_as_of)}` }),
          el("span", { class: "k", text: "Cycles / observations / paper plans" }),
          el("span", { class: "v mono", text: `${cohortMetrics.total_cycles ?? 0} / ${cohortMetrics.observation_records ?? 0} / ${cohortMetrics.paper_plan_count ?? 0}` }),
          el("span", { class: "k", text: "Raw observational R n" }),
          el("span", { class: "v mono", text: String(cohortMetrics.raw_observational_r?.sample_size ?? 0) }),
        ]),
      );
    });
    children.push(el("div", { class: "chart-note", style: { marginTop: "10px" }, text: "Never mixed: results produced by different rules stay separated." }));
    return card("LIVE FORWARD VALIDATION — forward statistics", children);
  }
  children.push(
    el("div", { class: "stat-cards", style: { marginTop: "12px" } }, [
      statCard("Recorded closes", metrics.total_cycles ?? 0, `${metrics.incomplete_cycles ?? 0} without a complete conclusion`),
      statCard("Distinct boundaries", metrics.distinct_boundaries ?? 0, "processed candle closes"),
      statCard("NO_SETUP closes", metrics.no_setup_cycles ?? 0, "nothing to observe"),
      statCard("Candidate observations", metrics.observation_records ?? 0, `${metrics.distinct_setup_ids ?? 0} distinct setup ids`),
      statCard("Paper observations", metrics.paper_plan_count ?? 0, `${metrics.paper_plans_with_outcome ?? 0} with an outcome`),
      statCard("Completed / unresolved", `${metrics.completed_count ?? 0} / ${metrics.unresolved_count ?? 0}`, "settled vs still open"),
      statCard("Ambiguous", metrics.ambiguous_count ?? 0, "never resolved favourably"),
      statCard("Incomplete data", metrics.incomplete_data_count ?? 0, "reported separately"),
    ]),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Denominated rates" }),
    el("div", { class: "kv" }, [
      el("span", { class: "k", text: "Entry reached" }), el("span", { class: "v mono", text: rateText(metrics.entry_reached_rate) }),
      el("span", { class: "k", text: "Entry not reached" }), el("span", { class: "v mono", text: rateText(metrics.entry_not_reached_rate) }),
      el("span", { class: "k", text: "Stopped after ordered entry" }), el("span", { class: "v mono", text: rateText(metrics.stopped_rate) }),
      el("span", { class: "k", text: "Ambiguous" }), el("span", { class: "v mono", text: rateText(metrics.ambiguous_rate) }),
      el("span", { class: "k", text: "Incomplete data" }), el("span", { class: "v mono", text: rateText(metrics.incomplete_rate) }),
      el("span", { class: "k", text: "Unresolved / open" }), el("span", { class: "v mono", text: rateText(metrics.unresolved_rate) }),
      ...(metrics.target_hit_rates || []).flatMap((rate) => [
        el("span", { class: "k", text: String(rate.metric || "target").replaceAll("_", " ") }),
        el("span", { class: "v mono", text: rateText(rate) }),
      ]),
    ]),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Raw observational R — not realised profit" }),
    distributionBlock("Raw observational R", metrics.raw_observational_r),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Friction-adjusted hypothetical R — not realised profit" }),
    distributionBlock("Friction-adjusted hypothetical R", metrics.friction_adjusted_hypothetical_r),
    el("div", { class: "chart-note", style: { marginTop: "10px" }, text: `Friction assumptions ${report.friction?.version || "UNKNOWN"} · fee ${displayOrUnknown(report.friction?.fee_bps)} bps · entry slippage ${displayOrUnknown(report.friction?.entry_slippage_bps)} bps · exit slippage ${displayOrUnknown(report.friction?.exit_slippage_bps)} bps` }),
  );
  if ((report.warnings || []).length) {
    children.push(
      el("div", { class: "chart-note", style: { marginTop: "12px", color: "var(--amber)" }, text: `Warnings: ${report.warnings.join(" · ")}` }),
    );
  }
  return card("LIVE FORWARD VALIDATION — forward statistics", children);
}

function breakdownCard(report) {
  const groups = report.breakdowns || [];
  if (!groups.length) {
    return card("Forward breakdowns", [
      emptyState("No paper observations", "Breakdowns require at least one paper observation."),
    ]);
  }
  return card("Forward breakdowns (descriptive only)", [
    el("div", { class: "chart-note", text: "Labels are read from the recorded observation at paper-plan time. They never alter Steps 3–6, and no breakdown value is fed back into any rule." }),
    el("div", { class: "history-list", style: { marginTop: "10px" } }, groups.map((group) => {
      const raw = group.metrics?.raw_observational_r || {};
      return el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: group.dimension }),
        el("span", { text: group.value }),
        el("span", { class: "mono", text: `paper plans ${group.metrics?.paper_plan_count ?? 0} · raw R eligible ${raw.sample_size ?? 0}/${raw.records_considered ?? 0} · avg ${displayOrUnknown(raw.average)}` }),
        el("span", { class: "mono", text: `unresolved ${group.metrics?.unresolved_count ?? 0}` }),
      ]);
    })),
  ]);
}

function comparisonRow(row) {
  const render = (side) => (side === null || side === undefined
    ? "—"
    : typeof side === "object"
      ? `${side.percentage ?? "below floor"} (${side.numerator ?? side.sample_size ?? 0}/${side.denominator ?? "—"})${side.average !== undefined ? ` avg ${displayOrUnknown(side.average)}` : ""}`
      : String(side));
  return el("div", { class: "history-item" }, [
    el("span", { class: "seq", text: row.metric }),
    el("span", { text: `historical ${render(row.historical)} · forward ${render(row.forward)}` }),
    el("span", { class: "mono", text: row.comparable === false ? "NOT COMPARABLE" : row.comparable === null ? "comparability UNKNOWN" : "comparable rule versions" }),
    el("span", { class: "mono", text: `denominators: historical ${row.historical_denominator} / forward ${row.forward_denominator}` }),
    row.note ? el("span", { class: "chart-note", text: row.note }) : null,
  ]);
}

function comparisonCard(container) {
  const node = card("Historical validation vs live forward paper observations", []);
  const body = el("div", {});
  node.append(body);
  const button = el("button", { class: "btn", type: "button" }, ["Compute comparison"]);
  const status = el("div", { class: "chart-note", text: "The comparison replays stored candles for the HISTORICAL VALIDATION side. It is descriptive only and is never shown as a single merged result." });
  button.addEventListener("click", async () => {
    button.disabled = true;
    clearNode(body).append(spinner("Replaying stored candles for the historical side…"));
    try {
      const payload = await api.forwardComparison();
      const comparison = payload.comparison;
      clearNode(body);
      body.append(
        el("div", { class: "stat-banner", dataset: { tone: "neutral" } }, [
          el("span", { text: comparison.historical.label }),
          el("span", { class: "sub", text: comparison.historical.disclaimer }),
        ]),
        el("div", { class: "stat-banner", dataset: { tone: "neutral" } }, [
          el("span", { text: comparison.forward.label }),
          el("span", { class: "sub", text: comparison.forward.disclaimer }),
        ]),
        el("div", { class: "chart-note", style: { marginTop: "10px" }, text: `Version comparability: ${comparison.version_comparability_note || "UNKNOWN"}` }),
        el("div", { class: "history-list", style: { marginTop: "10px" } }, (comparison.rows || []).map(comparisonRow)),
        (comparison.warnings || []).length
          ? el("div", { class: "chart-note", style: { marginTop: "12px", color: "var(--amber)" }, text: `Warnings: ${comparison.warnings.join(" · ")}` })
          : null,
        el("div", { class: "chart-note", style: { marginTop: "10px" }, text: (comparison.limitations || []).join(" ") }),
      );
    } catch (error) {
      clearNode(body).append(errorState(error.message));
    } finally {
      button.disabled = false;
    }
  });
  node.append(el("div", { style: { marginTop: "10px" } }, [button]), status, body);
  return node;
}

export async function renderLive(view) {
  clearNode(view).append(spinner("Reading the forward ledger…"));
  let payload;
  try {
    payload = await api.forward();
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }
  const status = payload.status || {};
  clearNode(view);
  view.append(
    el("div", { class: "stat-banner insufficient", role: "status" }, [
      el("span", { text: payload.label || FORWARD_LABEL }),
      el("span", { class: "sub", text: `${payload.paper_label || PAPER_LABEL}. ${payload.disclaimer || DISCLAIMER}` }),
    ]),
    marketDataCard(status),
    currentStateCard(status),
    observationsCard(payload.observations || {}),
    paperPlansCard(payload.observations || {}),
    forwardStatsCard(payload.report || {}),
    breakdownCard(payload.report || {}),
    comparisonCard(view),
    card("Limitations", [
      el("ul", { class: "limitations" }, (payload.limitations || []).map((item) =>
        el("li", { text: item }),
      )),
    ]),
  );
}
