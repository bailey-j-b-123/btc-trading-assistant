/**
 * Journal view: searchable/filterable append-only history with an immutable
 * detail page. Historical rows are shown exactly as stored — they never
 * re-render against today's market state.
 */

import { api } from "../api.js";
import { directionLabel, displayOrUnknown, familyLabel, formatDate, formatTime, formatUtc, shortId } from "../format.js";
import { clearNode, el, emptyState, errorState, loadPrefs, openModal, savePrefs, spinner, toast } from "../util.js";

const FILTER_KEY = "ta.journal.filters.v1";

const DEFAULT_FILTERS = {
  symbol: "",
  setup_family: "",
  direction: "",
  setup_state: "",
  plan_state: "",
  decision_state: "",
  outcome_status: "",
  range_from: "",
  range_to: "",
};

function loadFilters() {
  try {
    return { ...DEFAULT_FILTERS, ...(JSON.parse(localStorage.getItem(FILTER_KEY)) || {}) };
  } catch {
    return { ...DEFAULT_FILTERS };
  }
}

function persistFilters(filters) {
  try {
    localStorage.setItem(FILTER_KEY, JSON.stringify(filters));
  } catch {
    /* non-fatal */
  }
}

export async function renderJournal(view, { journalId = null, offset = 0 } = {}) {
  if (journalId) return renderJournalDetail(view, journalId);
  clearNode(view).append(spinner("Reading journal…"));

  const filters = loadFilters();
  const params = { limit: 50, offset };
  for (const [key, value] of Object.entries(filters)) {
    if (value) params[key] = value;
  }

  let listing;
  try {
    listing = await api.journalList(params);
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }

  clearNode(view);
  view.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { class: "card-title", text: "Journal" }),
        el("span", { class: "card-hint", text: "Immutable proposals, decisions and observations. Rows never silently update." }),
      ]),
      filterBar(filters, offset),
      listing.returned_count
        ? el("div", { class: "journal-list" }, listing.items.map((item) => journalRow(item)))
        : emptyState("No journal entries", "Journaled proposals will appear here. Record a decision from the dashboard, or journal setups from the engine, to build history."),
      pagination(listing, offset),
    ]),
  );
}

function filterBar(filters, offset) {
  const make = (key, label, options) => {
    const select = el(
      "select",
      {
        class: "select-inline",
        "aria-label": label,
        onchange: (event) => applyFilter(key, event.target.value),
      },
      [el("option", { value: "", text: "All" })],
    );
    for (const [value, text] of options) {
      select.append(el("option", { value, text, selected: filters[key] === value || undefined }));
    }
    return el("div", { class: "filter-field" }, [el("label", { text: label }), select]);
  };

  const dateInput = (key, label) =>
    el("div", { class: "filter-field" }, [
      el("label", { text: label }),
      el("input", {
        class: "input-inline",
        type: "date",
        value: filters[key] || undefined,
        "aria-label": label,
        onchange: (event) => applyFilter(key, event.target.value ? `${event.target.value}T00:00:00Z` : ""),
      }),
    ]);

  function applyFilter(key, value) {
    const next = loadFilters();
    next[key] = value;
    persistFilters(next);
    renderJournal(document.getElementById("view"), { offset: 0 });
  }

  return el("div", { class: "filter-bar", role: "search", "aria-label": "Journal filters" }, [
    el("div", { class: "filter-field" }, [
      el("label", { text: "Symbol" }),
      el("input", {
        class: "input-inline",
        value: filters.symbol || undefined,
        placeholder: "e.g. BTC/USDT",
        "aria-label": "Symbol filter",
        onchange: (event) => applyFilter("symbol", event.target.value.trim()),
      }),
    ]),
    make("setup_family", "Setup family", [
      ["breakout_retest_continuation", "Breakout retest"],
      ["failed_breakout_sweep_reversal", "Sweep reversal"],
      ["range_rejection_reversal", "Range reversal"],
    ]),
    make("direction", "Direction", [["bullish", "Bullish"], ["bearish", "Bearish"]]),
    make("setup_state", "Setup state", [["QUALIFIED", "QUALIFIED"], ["WATCH", "WATCH"], ["NO_SETUP", "NO_SETUP"]]),
    make("plan_state", "Plan state", [["PLANNABLE", "PLANNABLE"], ["NO_PLAN", "NO_PLAN"], ["INVALID", "INVALID"]]),
    make("decision_state", "Decision", [["ACCEPTED", "ACCEPTED"], ["REJECTED", "REJECTED"], ["SKIPPED", "SKIPPED"], ["PENDING", "PENDING"]]),
    make("outcome_status", "Outcome", [
      ["TARGETS_REACHED", "TARGETS_REACHED"],
      ["STOPPED", "STOPPED"],
      ["STOPPED_AFTER_TARGETS", "STOPPED_AFTER_TARGETS"],
      ["ENTRY_NOT_REACHED", "ENTRY_NOT_REACHED"],
      ["INVALIDATED_BEFORE_ENTRY", "INVALIDATED_BEFORE_ENTRY"],
      ["OPEN_AT_CUTOFF", "OPEN_AT_CUTOFF"],
      ["AMBIGUOUS", "AMBIGUOUS"],
      ["INCOMPLETE_DATA", "INCOMPLETE_DATA"],
    ]),
    dateInput("range_from", "From"),
    dateInput("range_to", "To"),
    offset > 0 || Object.values(filters).some(Boolean)
      ? el("div", { class: "filter-field" }, [
          el("label", { text: " " }),
          el("button", {
            class: "btn btn-quiet",
            type: "button",
            text: "Reset",
            onclick: () => {
              persistFilters({ ...DEFAULT_FILTERS });
              renderJournal(document.getElementById("view"), { offset: 0 });
            },
          }),
        ])
      : null,
  ]);
}

function journalRow(item) {
  const decision = item.latest_decision ? item.latest_decision.decision : null;
  const outcome = item.latest_outcome;
  const levels = item.plan_levels;

  return el("button", {
    class: "journal-row",
    type: "button",
    "aria-label": `Open journal record from ${formatUtc(item.setup_as_of)}`,
    onclick: () => {
      location.hash = `#/journal/${encodeURIComponent(item.journal_id)}`;
    },
  }, [
    el("div", { class: "journal-when" }, [
      el("span", { class: "date", text: formatDate(item.setup_as_of) }),
      el("span", { class: "time mono", text: formatTime(item.setup_as_of) }),
      el("span", { class: "time", text: item.timeframe }),
    ]),
    el("div", { class: "journal-main" }, [
      el("div", { class: "line1" }, [
        el("span", { text: item.symbol }),
        item.setup_family ? el("span", { class: "tag", text: familyLabel(item.setup_family) }) : null,
        item.direction ? el("span", { class: "tag", text: `${directionLabel(item.direction)}` }) : null,
        el("span", { class: "tag", text: item.setup_state }),
        item.plan_state ? el("span", { class: "tag", text: item.plan_state }) : null,
      ]),
      el("div", { class: "line2" }, [
        levels
          ? el("span", { class: "mono", text: `E ${displayOrUnknown(levels.entry)} · S ${displayOrUnknown(levels.stop)} · T ${(levels.targets || []).map((t) => displayOrUnknown(t)).join(" / ")}` })
          : el("span", { text: item.record_kind === "snapshot" ? "Snapshot record (no plan)" : "No plan attached" }),
        outcome
          ? el("span", { text: `Outcome ${outcome.status}${outcome.ambiguous ? " · ambiguous" : ""}${outcome.incomplete ? " · incomplete" : ""}` })
          : null,
      ]),
    ]),
    el("div", { class: "journal-side" }, [
      decision
        ? el("span", { class: `badge decision-${decision}`, dataset: { tone: decisionTone(decision) }, text: decision })
        : el("span", { class: "badge", dataset: { tone: "neutral" }, text: "UNDECIDED" }),
      el("span", { class: "card-hint mono", text: shortId(item.journal_id) }),
    ]),
  ]);
}

function decisionTone(decision) {
  if (decision === "ACCEPTED") return "green";
  if (decision === "REJECTED") return "red";
  if (decision === "SKIPPED") return "amber";
  return "neutral";
}

function pagination(listing, offset) {
  if (!listing.returned_count) return el("div");
  const limit = listing.limit;
  const canPrev = offset > 0;
  const canNext = listing.returned_count >= limit;
  return el("div", { class: "pagination" }, [
    el("button", {
      class: "btn btn-quiet",
      type: "button",
      disabled: !canPrev || undefined,
      text: "← Newer",
      onclick: () => renderJournal(document.getElementById("view"), { offset: Math.max(0, offset - limit) }),
    }),
    el("span", { class: "card-hint", text: `Showing ${listing.returned_count} of ${listing.total_matching_records} matching record(s)` }),
    el("button", {
      class: "btn btn-quiet",
      type: "button",
      disabled: !canNext || undefined,
      text: "Older →",
      onclick: () => renderJournal(document.getElementById("view"), { offset: offset + limit }),
    }),
  ]);
}

/* --------------------------------------------------------------------- */
/* Detail                                                                */
/* --------------------------------------------------------------------- */

async function renderJournalDetail(view, journalId) {
  clearNode(view).append(spinner("Loading immutable record…"));
  let detail;
  try {
    detail = await api.journalDetail(journalId);
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }
  const record = detail.record;
  clearNode(view);

  view.append(
    el("div", { class: "card" }, [
      el("div", { class: "card-head" }, [
        el("h3", { class: "card-title", text: "Journal record" }),
        el("button", { class: "btn btn-quiet", type: "button", text: "← Journal", onclick: () => (location.hash = "#/journal") }),
      ]),
      el("div", { class: "immutable-note", text: "Immutable historical snapshot: the exact Step 5/Step 6 projections stored when this record was written. Current market data never alters it." }),
      el("div", { class: "kv" }, [
        el("span", { class: "k", text: "journal_id" }), el("span", { class: "v mono", text: record.journal_id }),
        el("span", { class: "k", text: "record kind" }), el("span", { class: "v", text: record.record_kind }),
        el("span", { class: "k", text: "instrument" }), el("span", { class: "v", text: `${record.exchange} · ${record.symbol} · ${record.timeframe}` }),
        el("span", { class: "k", text: "setup as-of" }), el("span", { class: "v mono", text: formatUtc(record.setup_as_of) }),
        el("span", { class: "k", text: "setup state" }), el("span", { class: "v", text: record.setup_state }),
        el("span", { class: "k", text: "setup family" }), el("span", { class: "v", text: record.setup_family ? familyLabel(record.setup_family) : "—" }),
        el("span", { class: "k", text: "direction" }), el("span", { class: "v", text: record.setup_direction ? directionLabel(record.setup_direction) : "—" }),
        el("span", { class: "k", text: "plan state" }), el("span", { class: "v", text: record.plan_state || "no plan" }),
        el("span", { class: "k", text: "rules / fingerprints" }), el("span", { class: "v mono", text: `${record.setup_rules_version} · setup ${shortId(record.setup_config_fingerprint, 12)}${record.planning_rules_version ? ` · planning ${shortId(record.plan_config_fingerprint, 12)}` : ""}` }),
      ]),
    ]),
    planSection(detail),
    decisionSection(detail),
    outcomeSection(detail),
    snapshotSection(detail),
  );
}

function planSection(detail) {
  const plan = detail.plan;
  if (!plan) {
    return el("div", { class: "card" }, [
      el("h3", { class: "card-title", text: "Proposed plan" }),
      emptyState("No plan attached", "This record was journaled without a Step 6 plan result."),
    ]);
  }
  const rows = [
    ["state", plan.state],
    ["direction", plan.direction || "UNKNOWN"],
    ["entry", plan.entry && plan.entry.value !== null ? plan.entry.value : "UNKNOWN"],
    ["invalidation", plan.invalidation && plan.invalidation.value !== null ? plan.invalidation.value : "UNKNOWN"],
    ["protective stop", plan.stop && plan.stop.value !== null ? plan.stop.value : "UNKNOWN"],
    ["risk per unit", plan.risk_per_unit === null ? "UNKNOWN" : plan.risk_per_unit],
    ["targets", (plan.targets || []).map((t, i) => `T${i + 1} ${t.level && t.level.value !== null ? t.level.value : "UNKNOWN"}${t.r_multiple !== null && t.r_multiple !== undefined ? ` (${t.r_multiple} R)` : ""}`).join(" · ") || "none"],
    ["reasons", (plan.reasons || []).join("; ") || "—"],
    ["missing inputs", (plan.missing_inputs || []).join(", ") || "—"],
    ["plan id", plan.id],
    ["planning as-of", formatUtc(plan.as_of)],
  ];
  return el("div", { class: "card" }, [
    el("h3", { class: "card-title", text: "Proposed plan (as stored)" }),
    el("div", { class: "kv" }, rows.flatMap(([k, v]) => [el("span", { class: "k", text: k }), el("span", { class: "v mono", text: String(v) })])),
  ]);
}

function decisionSection(detail) {
  const decisions = detail.decisions || [];
  const section = el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: `Decision history (${decisions.length})` }),
      el("button", { class: "btn btn-quiet", type: "button", text: "Record correction…", onclick: () => correctionDialog(detail) }),
    ]),
    decisions.length
      ? decisions.map((decision, index) =>
          el("div", { class: "history-item" }, [
            el("span", { class: "seq", text: `#${index + 1}` }),
            el("span", { class: `badge decision-${decision.decision}`, dataset: { tone: decisionTone(decision.decision) }, text: decision.decision }),
            el("span", { class: "mono", style: { fontSize: "12.5px" }, text: formatUtc(decision.decided_at) }),
            decision.reason ? el("span", { style: { color: "var(--text-dim)", fontSize: "13px" }, text: `“${decision.reason}”` }) : null,
            index < decisions.length - 1
              ? el("span", { class: "strike-note", text: "superseded by a later decision" })
              : el("span", { class: "tag", text: "effective" }),
          ]),
        )
      : emptyState("No decisions", "No decision has ever been recorded for this record."),
  ]);
  return section;
}

function correctionDialog(detail) {
  const select = el("select", { class: "select-inline", "aria-label": "Correction decision" }, [
    el("option", { value: "", text: "Choose… (required)" }),
    el("option", { value: "ACCEPTED", text: "ACCEPTED" }),
    el("option", { value: "REJECTED", text: "REJECTED" }),
    el("option", { value: "SKIPPED", text: "SKIPPED" }),
  ]);
  const note = el("input", { class: "input-inline", maxlength: 2000, placeholder: "Optional note", style: { width: "100%" } });
  const confirm = el("button", { class: "btn btn-primary", type: "button", text: "Append decision" });
  let busy = false;

  const close = openModal(
    el("div", {}, [
      el("h3", { text: "Record a decision on this historical record" }),
      el("p", { style: { color: "var(--text-dim)", fontSize: "13.5px" }, text: "Corrections append a new row; earlier decisions remain visible. This never executes anything." }),
      el("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } }, [select, note]),
      el("div", { class: "modal-actions" }, [confirm]),
    ]),
  );
  confirm.onclick = async () => {
    if (busy) return;
    if (!select.value) {
      toast("Choose a decision first — there is no default.", "red");
      return;
    }
    busy = true;
    confirm.disabled = true;
    try {
      const result = await api.recordDecision(detail.record.journal_id, {
        decision: select.value,
        reason: note.value.trim() || undefined,
      });
      close();
      toast(result.duplicate ? "Identical decision already recorded." : "Decision appended.", result.duplicate ? "neutral" : "green");
      renderJournalDetail(document.getElementById("view"), detail.record.journal_id);
    } catch (error) {
      busy = false;
      confirm.disabled = false;
      toast(error.message, "red", 6500);
    }
  };
}

function outcomeSection(detail) {
  const versions = detail.outcome_versions || [];
  const latest = detail.latest_outcome;
  if (!versions.length) {
    return el("div", { class: "card" }, [
      el("h3", { class: "card-title", text: "Outcome observations" }),
      emptyState("Not yet observed", "Outcome observation runs deterministically over stored candles after the plan instant."),
      detail.record.plan_state === "PLANNABLE" ? observeButton(detail) : null,
    ]);
  }
  const kv = (k, v) => [el("span", { class: "k", text: k }), el("span", { class: "v mono", text: String(v) })];
  return el("div", { class: "card" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: `Outcome observations (${versions.length} version${versions.length === 1 ? "" : "s"})` }),
      detail.record.plan_state === "PLANNABLE" ? observeButton(detail) : null,
    ]),
    latest
      ? el("div", { class: "kv" }, [
          ...kv("status", latest.status),
          ...kv("observed through", formatUtc(latest.observed_through)),
          ...kv("entry reached", String(latest.entry_reached)),
          ...kv("stop reached", String(latest.stop_reached)),
          ...kv("targets reached", latest.targets_reached.length ? latest.targets_reached.map((t) => `T${t + 1}`).join(", ") : "none"),
          ...kv("ambiguous", latest.ambiguous ? `yes (${latest.ambiguity_kind})` : "no"),
          ...kv("incomplete", latest.incomplete ? `yes (${latest.missing_candle_count} missing)` : "no"),
          ...kv("MFE (observational)", latest.mfe_r === null ? "UNKNOWN" : `${latest.mfe_r} R`),
          ...kv("MAE (observational)", latest.mae_r === null ? "UNKNOWN" : `${latest.mae_r} R`),
        ])
      : null,
    el("div", { class: "chart-note", text: "MFE/MAE here are observational price excursions of the proposed levels — never realized profit or loss of an executed trade." }),
    versions.length > 1
      ? el("details", { class: "expandable" }, [
          el("summary", { text: "Earlier observation versions" }),
          el("div", { class: "detail-body" }, versions.slice(0, -1).reverse().map((version) =>
            el("div", { class: "history-item" }, [
              el("span", { class: "seq", text: `v${version.sequence}` }),
              el("span", { class: "tag", text: version.observation.status }),
              el("span", { class: "mono", style: { fontSize: "12px" }, text: `through ${formatUtc(version.observation.observed_through)}` }),
            ]),
          )),
        ])
      : null,
  ]);
}

function observeButton(detail) {
  let busy = false;
  return el("button", {
    class: "btn btn-quiet",
    type: "button",
    text: "Observe at latest closed candle",
    onclick: async (event) => {
      if (busy) return;
      busy = true;
      event.currentTarget.disabled = true;
      try {
        const observation = await api.observeOutcome(detail.record.journal_id, {});
        toast(`Observation recorded: ${observation.status}`, "green");
        renderJournalDetail(document.getElementById("view"), detail.record.journal_id);
      } catch (error) {
        busy = false;
        event.currentTarget.disabled = false;
        toast(error.message, "red", 6500);
      }
    },
  });
}

function snapshotSection(detail) {
  return el("div", { class: "card" }, [
    el("h3", { class: "card-title", text: "Raw stored projections" }),
    el("details", { class: "expandable" }, [
      el("summary", { text: "Step 5 snapshot JSON (exact stored bytes)" }),
      el("pre", { class: "detail-body mono", style: { overflow: "auto", maxHeight: "360px", whiteSpace: "pre-wrap" }, text: JSON.stringify(detail.setup_snapshot, null, 2) }),
    ]),
    detail.plan
      ? el("details", { class: "expandable" }, [
          el("summary", { text: "Step 6 plan JSON (exact stored bytes)" }),
          el("pre", { class: "detail-body mono", style: { overflow: "auto", maxHeight: "360px", whiteSpace: "pre-wrap" }, text: JSON.stringify(detail.plan, null, 2) }),
        ])
      : null,
  ]);
}
