/**
 * Step 11 presentation: a read-only derived historical validation report.
 * This intentionally has no decisions, orders, account data, equity curve, or
 * controls that could tune Steps 3–6. The browser only renders backend facts.
 */

import { api } from "../api.js";
import { displayOrUnknown, formatUtc } from "../format.js";
import { clearNode, el, emptyState, errorState, spinner } from "../util.js";

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

function rangeText(split) {
  if (!split || !split.development_start) return "No decision boundaries available";
  const dev = `${formatUtc(split.development_start)} → ${formatUtc(split.development_end)}`;
  if (!split.out_of_sample_start) return `Development: ${dev} · out-of-sample unavailable`;
  return `Development: ${dev} · Out-of-sample: ${formatUtc(split.out_of_sample_start)} → ${formatUtc(split.out_of_sample_end)}`;
}

function rateRows(metrics) {
  const rates = [metrics.entry_reached_rate, metrics.stop_rate_after_ordered_entry, ...(metrics.target_hit_rates || [])];
  return el("div", { class: "bar-chart" }, rates.map((rate) => {
    const percent = rate.percentage === null || rate.percentage === undefined ? "—" : `${rate.percentage}%`;
    return el("div", { class: "bar-row" }, [
      el("span", { class: "lbl", text: String(rate.metric || "metric").replaceAll("_", " ") }),
      el("div", { class: "bar-track" }, [
        el("div", { class: "bar-fill", style: { width: rate.percentage === null || rate.percentage === undefined ? "0%" : `${Math.min(100, Number(rate.percentage))}%`, "--bar-color": "var(--blue)" } }),
      ]),
      el("span", { class: "val", text: `${percent} (${rate.numerator}/${rate.denominator})` }),
    ]);
  }));
}

function distributionRows(distribution) {
  const values = distribution?.values || [];
  return el("div", { class: "kv", style: { marginTop: "8px" } }, [
    el("span", { class: "k", text: "Eligible / considered" }),
    el("span", { class: "v mono", text: `${distribution?.sample_size ?? 0} / ${distribution?.records_considered ?? 0}` }),
    el("span", { class: "k", text: "Status" }),
    el("span", { class: "v mono", text: distribution?.status || "INSUFFICIENT_DATA" }),
    el("span", { class: "k", text: "Average / median" }),
    el("span", { class: "v mono", text: `${displayOrUnknown(distribution?.average)} / ${displayOrUnknown(distribution?.median)}` }),
    el("span", { class: "k", text: "Min / max" }),
    el("span", { class: "v mono", text: `${displayOrUnknown(distribution?.minimum)} / ${displayOrUnknown(distribution?.maximum)}` }),
    el("span", { class: "k", text: "Exact R observations" }),
    el("span", { class: "v mono", text: values.length ? values.join(", ") : "—" }),
  ]);
}

function cohort(name, cohort) {
  if (!cohort) return card(`${name} period`, [emptyState("No out-of-sample period", "The source dataset did not contain enough chronological decision boundaries.")]);
  const m = cohort.metrics || {};
  return card(`${name} period`, [
    el("div", { class: "card-hint", text: `${formatUtc(cohort.start)} → ${formatUtc(cohort.end)} · ${cohort.decision_boundary_count ?? 0} decision boundaries` }),
    el("div", { class: "stat-cards", style: { marginTop: "12px" } }, [
      statCard("Replay records", m.total_records ?? 0, `${m.distinct_setup_ids ?? 0} distinct setup candidates`),
      statCard("Qualified", countValue(m.setup_state_counts, "QUALIFIED"), "candidate observations"),
      statCard("Plannable", countValue(m.plan_state_counts, "PLANNABLE"), `${countValue(m.plan_state_counts, "NO_PLAN") + countValue(m.plan_state_counts, "INVALID")} explicit refusals/invalid`),
      statCard("Observed", m.outcomes_observed_count ?? 0, `${m.outcomes_not_observed_count ?? 0} not observed inside cohort`),
      statCard("Ambiguous", m.ambiguous_count ?? 0, "reported separately"),
      statCard("Incomplete", m.incomplete_count ?? 0, "reported separately"),
      statCard("Completed / unresolved", `${m.completed_count ?? 0} / ${m.unresolved_count ?? 0}`, "OHLC observation states"),
    ]),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Raw observational rates (numerator / denominator)" }),
    rateRows(m),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Raw observational hypothetical R — not realised profit" }),
    distributionRows(m.raw_observational_r),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Friction-adjusted hypothetical R — not realised profit" }),
    distributionRows(m.friction_adjusted_hypothetical_r),
    (cohort.warnings || []).length
      ? el("div", { class: "chart-note", style: { marginTop: "12px", color: "var(--amber)" }, text: `Warnings: ${(cohort.warnings || []).join(" · ")}` })
      : null,
  ]);
}

function countValue(rows, value) {
  return (rows || []).find((item) => item.value === value)?.count || 0;
}

function breakdowns(report) {
  const cohort = report.development;
  const groups = cohort?.breakdowns || [];
  if (!groups.length) return card("Diagnostic breakdowns", [emptyState("No replay records", "Breakdowns require historical validation records.")]);
  return card("Diagnostic breakdowns", [
    el("div", { class: "chart-note", text: "Descriptive only; these labels never alter qualification or planning rules." }),
    el("div", { class: "history-list", style: { marginTop: "10px" } }, groups.map((group) => {
      const raw = group.metrics?.raw_observational_r || {};
      return el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: group.dimension }),
        el("span", { text: group.value }),
        el("span", { class: "mono", text: `records ${group.metrics?.total_records ?? 0} · raw R n=${raw.sample_size ?? 0} avg=${displayOrUnknown(raw.average)}` }),
      ]);
    })),
  ]);
}

function dataset(report) {
  const ranges = report.dataset_ranges || [];
  return card("Dataset and reproducibility", [
    el("div", { class: "kv" }, [
      el("span", { class: "k", text: "Instrument" }), el("span", { class: "v mono", text: `${report.exchange}/${report.symbol} · ${report.timeframe}` }),
      el("span", { class: "k", text: "Dataset fingerprint" }), el("span", { class: "v mono", text: report.dataset_fingerprint || "—" }),
      el("span", { class: "k", text: "Report ID" }), el("span", { class: "v mono", text: report.report_id || "—" }),
      el("span", { class: "k", text: "Split" }), el("span", { class: "v", text: rangeText(report.split) }),
    ]),
    el("div", { class: "history-list", style: { marginTop: "12px" } }, ranges.map((range) =>
      el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: range.timeframe }),
        el("span", { text: `${range.candle_count} candles` }),
        el("span", { class: "mono", text: `${formatUtc(range.first_open)} → ${formatUtc(range.last_close)}` }),
      ]),
    )),
  ]);
}

export async function renderValidation(view) {
  clearNode(view).append(spinner("Replaying historical candles without lookahead…"));
  let report;
  try {
    report = await api.validation();
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }
  clearNode(view);
  view.append(
    el("div", { class: "stat-banner insufficient", role: "status" }, [
      el("span", { text: "HISTORICAL VALIDATION — NOT LIVE PERFORMANCE" }),
      el("span", { class: "sub", text: "Read-only chronological replay. Results are observations of stored candles versus proposed levels, not fills, realised profit, or a profitability claim." }),
    ]),
    dataset(report),
    cohort("Development", report.development),
    cohort("Out-of-sample", report.out_of_sample),
    breakdowns(report),
    card("Overfitting and data-quality diagnostics", [
      (report.warnings || []).length
        ? el("div", { class: "chart-note", style: { color: "var(--amber)" }, text: (report.warnings || []).join(" · ") })
        : el("div", { class: "chart-note", text: "No report-level warning was triggered; this is not evidence of future profitability." }),
      el("div", { class: "chart-note", style: { marginTop: "10px" }, text: (report.limitations || []).join(" ") }),
    ]),
  );
}
