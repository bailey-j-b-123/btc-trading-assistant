/**
 * Statistics view: Step 8 report rendered visually.
 *
 * Every chart shows its denominator; INSUFFICIENT_DATA is impossible to miss;
 * observational/hypothetical R is never labelled realized profit.
 */

import { api } from "../api.js";
import { displayOrUnknown, familyLabel, formatUtc, isMissing } from "../format.js";
import { clearNode, el, emptyState, errorState, spinner, toast } from "../util.js";

const GROUP_OPTIONS = [
  ["setup_family", "Setup family"],
  ["direction", "Direction"],
  ["timeframe", "Timeframe"],
  ["decision_state", "Decision"],
  ["outcome_status", "Outcome"],
  ["period_month", "Month"],
];

export async function renderStatistics(view) {
  clearNode(view).append(spinner("Computing deterministic statistics…"));
  let report;
  try {
    report = await api.statistics({});
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }
  clearNode(view);
  view.append(banner(report), qualityCards(report), overallSection(report), comparisonSection(report), distributionSection(report), versionsSection(report));
}

function sampleStatus(group) {
  if (!group) return "INSUFFICIENT_DATA";
  const rate = group.setup_qualification_rate || {};
  return String(rate.status || "INSUFFICIENT_DATA").toUpperCase();
}

function banner(report) {
  const quality = report.data_quality || {};
  const status = sampleStatus(report.overall);
  const sufficient = status === "SUFFICIENT_DATA";
  return el("div", { class: `stat-banner ${sufficient ? "sufficient" : "insufficient"}`, role: "status" }, [
    el("span", { text: sufficient ? "SUFFICIENT_DATA" : "INSUFFICIENT_DATA" }),
    el("span", { class: "sub", text: sufficient
      ? `Sample meets the configured minimum for rate summaries. Considered: ${quality.total_journal_records_considered ?? 0} journal record(s).`
      : `Rate and distribution conclusions are withheld until the sample reaches the configured minimum (counts remain exact). Considered: ${quality.total_journal_records_considered ?? 0} journal record(s).` }),
  ]);
}

function statCard(k, v, s) {
  return el("div", { class: "stat-card" }, [
    el("div", { class: "k", text: k }),
    el("div", { class: "v mono", text: String(v) }),
    s ? el("div", { class: "s", text: s }) : null,
  ]);
}

function qualityCards(report) {
  const q = report.data_quality || {};
  return el("div", { class: "stat-cards" }, [
    statCard("Sample size", q.total_journal_records_considered ?? 0, "journal records considered"),
    statCard("Setup records", q.setup_records_eligible ?? 0, `${q.distinct_setup_ids ?? 0} distinct setup id(s)`),
    statCard("Plannable plans", q.plannable_plan_records ?? 0, `${q.non_plannable_or_missing_plan_records ?? 0} without plannable plan`),
    statCard("Observed outcomes", q.outcome_records_eligible ?? 0, `${q.outcome_records_determinate ?? 0} determinate`),
    statCard("Ambiguous", q.ambiguous_count ?? 0, "same-candle ordering unknown"),
    statCard("Incomplete", q.incomplete_unknown_count ?? 0, "missing candles → UNKNOWN"),
    statCard("Entry not reached", q.entry_not_reached_count ?? 0, "window observed, entry untouched"),
    statCard("Excluded", q.excluded_from_determinate_outcome_analysis ?? 0, "from determinate outcome analysis"),
  ]);
}

function barRows(valueCounts, { denominator, color } = {}) {
  const items = valueCounts || [];
  const max = Math.max(1, ...items.map((item) => item.count));
  return el("div", { class: "bar-chart" }, items.map((item) =>
    el("div", { class: "bar-row" }, [
      el("span", { class: "lbl", text: item.value === null ? "(none)" : String(item.value) }),
      el("div", { class: "bar-track" }, [el("div", { class: "bar-fill", style: { width: `${(item.count / max) * 100}%`, "--bar-color": color || "var(--blue)" } })]),
      el("span", { class: "val", text: `${item.count}${denominator !== undefined ? ` / ${denominator}` : ""}` }),
    ]),
  ));
}

function rateRow(label, rate) {
  if (!rate) return null;
  const pct = rate.percentage === null || rate.percentage === undefined ? "—" : `${rate.percentage}%`;
  return el("div", { class: "bar-row" }, [
    el("span", { class: "lbl", text: label }),
    el("div", { class: "bar-track" }, [
      el("div", { class: "bar-fill", style: { width: rate.percentage === null ? "0%" : `${Math.min(100, Number(rate.percentage))}%`, "--bar-color": "var(--green)" } }),
    ]),
    el("span", { class: "val", text: `${pct} (${rate.numerator}/${rate.denominator})` }),
  ]);
}

function overallSection(report) {
  const overall = report.overall;
  if (!overall) {
    return el("div", { class: "card" }, [
      el("h3", { class: "card-title", text: "Overall statistics" }),
      emptyState("No eligible records", "Journal entries will produce statistics once Step 7 records exist."),
    ]);
  }
  const q = overall.data_quality || {};
  const exclusionRows = (q.exclusion_reasons || []).map((reason) =>
    el("div", { class: "history-item" }, [
      el("span", { class: "seq", text: `×${reason.count}` }),
      el("span", { text: reason.value }),
    ]),
  );
  return el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: "Overall statistics" }),
      el("span", { class: "card-hint", text: `as of ${formatUtc(report.as_of)}${report.window_start ? ` · window from ${formatUtc(report.window_start)}` : ""}` }),
    ]),
    el("div", { class: "card-title", style: { marginTop: "8px" }, text: "Decision breakdown" }),
    barRows(overall.decision_state_counts, { color: "var(--blue)" }),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Outcome breakdown" }),
    barRows(overall.outcome_status_counts, { color: "var(--amber)" }),
    el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Rates (numerator / denominator shown)" }),
    el("div", { class: "bar-chart" }, [
      rateRow("Setup qualification rate", overall.setup_qualification_rate),
      rateRow("Entry touch rate", overall.entry_reach_rate),
      rateRow("Stop touch after ordered entry", overall.stop_touch_rate_after_ordered_entry),
      ...(overall.target_summaries || []).map((target) => rateRow(`${target.label} reach rate`, target.reach_rate)),
    ]),
    el("div", { class: "chart-note", text: "Every rate shows its exact numerator/denominator. Rates marked INSUFFICIENT_DATA withhold the percentage; counts stay exact." }),
    exclusionRows.length
      ? el("details", { class: "expandable" }, [
          el("summary", { text: `Outcome exclusion reasons (${exclusionRows.length})` }),
          el("div", { class: "detail-body" }, exclusionRows),
        ])
      : null,
  ]);
}

function comparisonSection(report) {
  const card = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: "Comparisons" }),
    ]),
  ]);
  const select = el("select", { class: "select-inline", "aria-label": "Comparison dimension" }, []);
  for (const [value, label] of GROUP_OPTIONS) select.append(el("option", { value, text: label }));
  const body = el("div");
  card.append(el("div", { class: "filter-bar" }, [el("div", { class: "filter-field" }, [el("label", { text: "Group by" }), select])]), body);

  async function load(dimension) {
    clearNode(body).append(spinner("Grouping…"));
    try {
      const grouped = await api.statistics({ group_by: dimension });
      clearNode(body);
      const groups = grouped.groups || [];
      if (!groups.length) {
        body.append(emptyState("No groups", "No records match this grouping."));
        return;
      }
      const rows = groups.map((group) => {
        const key = group.key.map(([, value]) => value ?? "(none)").join(" · ");
        const rate = group.setup_qualification_rate || {};
        return { group, key, rate };
      });
      body.append(
        el("div", { class: "card-title", style: { marginTop: "8px" }, text: "Qualification rate by group (eligible / considered shown)" }),
        el("div", { class: "bar-chart" }, rows.map(({ key, rate }) =>
          el("div", { class: "bar-row" }, [
            el("span", { class: "lbl", text: key }),
            el("div", { class: "bar-track" }, [el("div", { class: "bar-fill", style: { width: rate.percentage === null || rate.percentage === undefined ? "0%" : `${Math.min(100, Number(rate.percentage))}%`, "--bar-color": "var(--blue)" } })]),
            el("span", { class: "val", text: rate.percentage === null || rate.percentage === undefined ? `n=${rate.records_considered ?? 0} (insufficient)` : `${rate.percentage}% (${rate.numerator}/${rate.denominator})` }),
          ]),
        )),
        el("div", { class: "card-title", style: { marginTop: "16px" }, text: "Entry touch rate by group" }),
        el("div", { class: "bar-chart" }, rows.map(({ key, group }) => {
          const rate = group.entry_reach_rate || {};
          return el("div", { class: "bar-row" }, [
            el("span", { class: "lbl", text: key }),
            el("div", { class: "bar-track" }, [el("div", { class: "bar-fill", style: { width: rate.percentage === null || rate.percentage === undefined ? "0%" : `${Math.min(100, Number(rate.percentage))}%`, "--bar-color": "var(--green)" } })]),
            el("span", { class: "val", text: rate.percentage === null || rate.percentage === undefined ? `n=${rate.records_considered ?? 0} (insufficient)` : `${rate.percentage}% (${rate.numerator}/${rate.denominator})` }),
          ]);
        })),
        el("div", { class: "chart-note", text: "Groups are exact Step 8 cohorts. Observational metrics per group appear in the distributions section for the overall cohort; comparisons never imply realized performance." }),
      );
    } catch (error) {
      clearNode(body).append(errorState(error.message));
    }
  }

  select.addEventListener("change", (event) => load(event.target.value));
  load("setup_family").catch(() => {});
  void report;
  return card;
}

function distributionCard(title, distribution, note) {
  if (!distribution) return null;
  const insufficient = String(distribution.status || "").toUpperCase() !== "SUFFICIENT_DATA";
  const counts = distribution.distribution || [];
  const max = Math.max(1, ...counts.map((item) => item.count));
  return el("div", { style: { marginTop: "14px" } }, [
    el("div", { class: "card-title", text: `${title} — n=${distribution.sample_size} (${distribution.status})` }),
    insufficient
      ? el("div", { class: "chart-note", text: "Sample below the reporting floor: exact value counts only; summaries withheld." })
      : null,
    el("div", { class: "bar-chart" }, counts.slice(0, 24).map((item) =>
      el("div", { class: "bar-row" }, [
        el("span", { class: "lbl mono", text: displayOrUnknown(item.value) }),
        el("div", { class: "bar-track" }, [el("div", { class: "bar-fill", style: { width: `${(item.count / max) * 100}%`, "--bar-color": Number(item.value) >= 0 ? "var(--green)" : "var(--red)" } })]),
        el("span", { class: "val", text: String(item.count) }),
      ]),
    )),
    counts.length > 24 ? el("div", { class: "chart-note", text: `…and ${counts.length - 24} more exact values (see API for the full distribution).` }) : null,
    !insufficient
      ? el("div", { class: "kv", style: { marginTop: "8px" } }, [
          el("span", { class: "k", text: "positive / zero / negative" }),
          el("span", { class: "v mono", text: `${distribution.positive_count} / ${distribution.zero_count} / ${distribution.negative_count}` }),
          el("span", { class: "k", text: "average" }), el("span", { class: "v mono", text: displayOrUnknown(distribution.average) }),
          el("span", { class: "k", text: "median" }), el("span", { class: "v mono", text: displayOrUnknown(distribution.median) }),
          el("span", { class: "k", text: "min / max" }), el("span", { class: "v mono", text: `${displayOrUnknown(distribution.minimum)} / ${displayOrUnknown(distribution.maximum)}` }),
          el("span", { class: "k", text: "quantiles" }), el("span", { class: "v mono", text: (distribution.quantiles || []).map((q) => `p${q.probability}: ${q.value}`).join(" · ") || "—" }),
        ])
      : null,
    el("div", { class: "chart-note", text: note }),
  ]);
}

function distributionSection(report) {
  const overall = report.overall;
  if (!overall) return el("div");
  return el("div", { class: "card" }, [
    el("h3", { class: "card-title", text: "Distributions (hypothetical / observational only)" }),
    distributionCard("Hypothetical proposed-plan outcome R", overall.hypothetical_proposed_plan_outcome_r, "What the proposed levels did in stored candles. This is not realized profit: no trade was executed."),
    distributionCard("Observational MFE (R)", overall.observational_mfe_r, "Maximum favourable excursion of the proposed entry over the observed window."),
    distributionCard("Observational MAE (R)", overall.observational_mae_r, "Maximum adverse excursion of the proposed entry over the observed window."),
    distributionCard("Observational MFE (price move)", overall.observational_mfe_price_move, "Price-distance MFE, unit dependent."),
    distributionCard("Observational MAE (price move)", overall.observational_mae_price_move, "Price-distance MAE, unit dependent."),
  ]);
}

function versionsSection(report) {
  const profiles = report.version_profiles || [];
  if (!profiles.length) return el("div");
  return el("div", { class: "card" }, [
    el("h3", { class: "card-title", text: "Version / configuration separation" }),
    report.mixed_versions
      ? el("div", { class: "chart-note", style: { color: "var(--amber)" }, text: "Mixed rule/config versions are present in this cohort; interpret comparisons cautiously." })
      : el("div", { class: "chart-note", text: "All records in this cohort share one rule/config profile." }),
    el("div", { style: { marginTop: "10px" } }, profiles.map((profile) =>
      el("div", { class: "history-item" }, [
        el("span", { class: "seq", text: `×${profile.record_count}` }),
        el("span", { class: "mono", style: { fontSize: "12px" }, text: `journal ${profile.journal_rules_version} · setup ${profile.setup_rules_version} · planning ${profile.planning_rules_version ?? "—"}` }),
      ]),
    )),
  ]);
}
