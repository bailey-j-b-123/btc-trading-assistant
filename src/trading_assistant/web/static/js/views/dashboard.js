/**
 * Dashboard view: market strip, setup/planning status, chart, plan, evidence,
 * grounded explanation, and explicit decision controls.
 *
 * Everything displayed comes from the backend payload; this module never
 * computes levels, qualification, or statistics itself.
 */

import { api, ApiError } from "../api.js";
import { applyOverlays, clearOverlays, createPriceChart, setCandles, OVERLAY_COLORS } from "../chart.js";
import { buildDecisionRequest, confirmationSummary, decisionAvailability, DECISION_LABELS, DECISION_MEANINGS } from "../decision.js";
import { directionArrow, directionLabel, displayOrUnknown, displayPrice, familyLabel, formatUtc, isMissing, shortId } from "../format.js";
import { asOfLine, freshnessBadge, isCurrentData } from "../freshness.js";
import { clearNode, el, emptyState, errorState, loadPrefs, openModal, savePrefs, spinner, toast } from "../util.js";

const SETUP_STATE_META = {
  QUALIFIED: {
    label: "QUALIFIED",
    tone: "green",
    description: "The deterministic qualification rules are satisfied. Qualification is not a prediction and not a recommendation.",
  },
  WATCH: {
    label: "WATCH",
    tone: "amber",
    description: "Evidence is developing but qualification is incomplete.",
  },
  NO_SETUP: {
    label: "NO TRADE",
    tone: "neutral",
    description: "No qualifying setup is currently present.",
  },
};

const PLAN_STATE_META = {
  PLANNABLE: { label: "PLANNABLE", tone: "green" },
  NO_PLAN: { label: "NO_PLAN", tone: "neutral" },
  INVALID: { label: "INVALID", tone: "red" },
};

export async function renderDashboard(view) {
  const prefs = loadPrefs();
  clearNode(view).append(spinner("Evaluating deterministic engine…"));

  let dashboard;
  try {
    dashboard = await api.dashboard({
      symbol: prefs.preferredSymbol || undefined,
      timeframe: prefs.preferredTimeframe || undefined,
    });
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }

  const meta = await api.meta().catch(() => null);
  clearNode(view);
  view.append(
    marketStrip(dashboard, meta, prefs),
    statusHero(dashboard),
    chartCard(dashboard, prefs),
    planCard(dashboard),
    evidenceCard(dashboard),
    explanationCard(dashboard, prefs),
    decisionCard(dashboard),
  );
  updateTopbar(dashboard);
}

function updateTopbar(dashboard) {
  const symbolNode = document.getElementById("topbar-symbol");
  const priceNode = document.getElementById("topbar-price");
  const freshNode = document.getElementById("topbar-freshness");
  if (!symbolNode || !priceNode || !freshNode) return;
  symbolNode.textContent = dashboard.meta ? dashboard.meta.symbol : "—";
  const latest = dashboard.market && dashboard.market.latest_closed_candle;
  priceNode.textContent = latest ? displayPrice(latest.close).display : "—";
  const badge = freshnessBadge(dashboard.freshness);
  freshNode.textContent = badge.label;
  freshNode.dataset.tone = badge.tone;
  freshNode.title = badge.detail;
}

/* --------------------------------------------------------------------- */
/* Market strip                                                          */
/* --------------------------------------------------------------------- */

function marketStrip(dashboard, meta, prefs) {
  const { meta: dm, market, freshness } = dashboard;
  const badge = freshnessBadge(freshness);
  const latest = market && market.latest_closed_candle;
  const price = latest ? displayPrice(latest.close) : null;

  const timeframeSelect = el(
    "select",
    {
      class: "select-inline",
      "aria-label": "Timeframe",
      onchange: (event) => {
        const next = loadPrefs();
        next.preferredTimeframe = event.target.value;
        savePrefs(next);
        location.hash = "#/dashboard";
        renderDashboard(document.getElementById("view"));
      },
    },
    (meta ? meta.supported_timeframes : [dm.timeframe]).map((tf) =>
      el("option", { value: tf, text: tf, selected: tf === dm.timeframe || undefined }),
    ),
  );

  const qualityText =
    market && market.complete === false
      ? `Data incomplete: ${market.missing_candle_count} missing candle(s) in window`
      : "Stored window complete";

  return el("section", { class: "card market-strip", "aria-label": "Market status" }, [
    el("div", { class: "market-symbol", text: dm.symbol }),
    el("div", { class: "market-price mono", text: price ? price.display : "UNKNOWN", title: price && price.raw ? `exact close ${price.raw}` : "no stored candle" }),
    el("span", { class: "badge", dataset: { tone: badge.tone }, text: badge.label, title: badge.detail }),
    el("div", { class: "market-meta" }, [
      el("span", {}, [el("b", { text: "Exchange " }), dm.exchange]),
      el("span", {}, [el("b", { text: "Timeframe " }), timeframeSelect]),
    ]),
    el("div", { class: "market-meta" }, [
      el("span", { text: asOfLine(dm, freshness) }),
      el("span", { text: qualityText }),
      !isCurrentData(freshness) && freshness.status !== "UNKNOWN"
        ? el("span", { style: { color: "var(--amber)" }, text: freshness.status === "HISTORICAL" ? "Historical view — never live data" : "Data is not current" })
        : null,
    ]),
  ]);
}

/* --------------------------------------------------------------------- */
/* Setup + planning status hero                                          */
/* --------------------------------------------------------------------- */

function statusHero(dashboard) {
  const q = dashboard.qualification || {};
  const state = q.state;
  const stateMeta = SETUP_STATE_META[state] || { label: state || "UNKNOWN", tone: "neutral", description: "Qualification state is unavailable." };
  const planningState = dashboard.planning ? dashboard.planning.state : null;
  const planMeta = PLAN_STATE_META[planningState] || { label: "—", tone: "neutral" };

  const planningReasons = dashboard.planning ? dashboard.planning.reasons : [];
  const missing = dashboard.planning ? dashboard.planning.missing_inputs : [];

  return el("section", { class: "card status-hero", "aria-label": "Setup status" }, [
    el("div", { class: "setup-state-block" }, [
      el("div", { class: "setup-state-label", text: "Setup status" }),
      el("div", { class: "setup-state-value", dataset: { tone: stateMeta.tone }, text: stateMeta.label, role: "status" }),
      el("div", { class: "setup-state-desc", text: stateMeta.description }),
      q.reasons && q.reasons.length
        ? el("details", { class: "expandable" }, [
            el("summary", { text: `Engine reasons (${q.reasons.length})` }),
            el("div", { class: "detail-body" }, q.reasons.map((r) => el("div", { class: "kv" }, [el("span", { class: "k", text: "•" }), el("span", { class: "v", text: r })]))),
          ])
        : null,
    ]),
    el("div", { class: "hero-side" }, [
      el("div", { class: "hero-fact" }, [
        el("span", { class: "k", text: "Planning state" }),
        el("span", { class: "v" }, [el("span", { class: "badge", dataset: { tone: planMeta.tone }, text: planMeta.label })]),
      ]),
      planningState === "NO_PLAN" || planningState === "INVALID"
        ? el("div", { class: "hero-fact" }, [
            el("span", { class: "k", text: "Planner verdict" }),
            el("span", { class: "v", style: { fontSize: "12.5px", fontWeight: 500, maxWidth: "40ch" }, text: planningReasons.join("; ") || dashboard.planning.state_detail || "no reason recorded" }),
          ])
        : null,
      missing && missing.length
        ? el("div", { class: "hero-fact" }, [
            el("span", { class: "k", text: "Missing inputs" }),
            el("span", { class: "v", style: { fontSize: "12.5px", fontWeight: 500 }, text: missing.join(", ") }),
          ])
        : null,
      el("div", { class: "hero-fact" }, [
        el("span", { class: "k", text: "Qualified setups" }),
        el("span", { class: "v mono", text: String((q.setups || []).filter((s) => s.state === "QUALIFIED").length) }),
      ]),
      el("div", { class: "hero-fact" }, [
        el("span", { class: "k", text: "Rules version" }),
        el("span", { class: "v mono", style: { fontSize: "12px" }, text: q.rules_version || "UNKNOWN" }),
      ]),
    ]),
  ]);
}

/* --------------------------------------------------------------------- */
/* Chart                                                                 */
/* --------------------------------------------------------------------- */

function chartCard(dashboard, prefs) {
  const qualificationTimeframe = dashboard.meta.timeframe;
  const wrap = el("div", { class: "chart-wrap" });
  const toggles = {};

  const makeToggle = (key, label, swatch) => {
    const button = el(
      "button",
      {
        class: "toggle-chip",
        type: "button",
        "aria-pressed": String(prefs.overlays[key] === true),
        style: { "--swatch": swatch },
        onclick: (event) => {
          const next = loadPrefs();
          next.overlays[key] = !(next.overlays[key] === true);
          savePrefs(next);
          event.currentTarget.setAttribute("aria-pressed", String(next.overlays[key]));
          redraw();
        },
      },
      [el("span", { class: "swatch", "aria-hidden": "true" }), label],
    );
    toggles[key] = button;
    return button;
  };

  const timeframeNote = el("span", { class: "card-hint", text: "" });

  const card = el("section", { class: "card", "aria-label": "Price chart" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: "Price chart" }),
      el("div", { class: "chart-toolbar" }, [
        makeToggle("zones", "S/R zones", OVERLAY_COLORS.zones),
        makeToggle("range", "Range", OVERLAY_COLORS.range),
        makeToggle("equalLevels", "Equal levels", OVERLAY_COLORS.equalLevels),
        makeToggle("planLevels", "Plan levels", OVERLAY_COLORS.entry),
        makeToggle("swings", "Swings", OVERLAY_COLORS.swings),
        el("span", { class: "spacer" }),
        timeframeNote,
      ]),
    ]),
    wrap,
    el("div", { class: "chart-note", text: "Candles and overlays come from stored, validated data. Overlays only appear when the engine produced them." }),
  ]);

  let handle = null;
  let currentCandles = dashboard.market ? dashboard.market.candles : [];
  let currentOverlays = dashboard.overlays || {};
  let currentPlan = dashboard.plan;
  let currentTimeframe = qualificationTimeframe;

  function redraw() {
    if (!handle) return;
    setCandles(handle, currentCandles);
    const prefsNow = loadPrefs();
    const onQualificationTf = currentTimeframe === qualificationTimeframe;
    applyOverlays(handle, {
      overlays: currentOverlays,
      plan: onQualificationTf ? currentPlan : null,
      prefs: prefsNow,
    });
    timeframeNote.textContent = onQualificationTf
      ? `Qualification timeframe (${qualificationTimeframe})`
      : `Structure-only view — plan levels apply to ${qualificationTimeframe}`;
  }

  // Initial render or empty state.
  if (!currentCandles.length) {
    wrap.append(
      el("div", { class: "chart-empty" }, [
        el("div", { class: "big", text: "No stored candles" }),
        el("div", { text: `No validated ${dashboard.meta.symbol} ${qualificationTimeframe} candles are stored for this window. Nothing is simulated.` }),
      ]),
    );
  } else {
    handle = createPriceChart(wrap);
    if (handle) redraw();
  }

  // Timeframe switching for the chart only (dashboard state stays pinned).
  const strip = card.querySelector(".chart-toolbar");
  const tfSelect = el(
    "select",
    {
      class: "select-inline",
      "aria-label": "Chart timeframe",
      style: { minHeight: "34px", padding: "4px 8px" },
      onchange: async (event) => {
        const tf = event.target.value;
        currentTimeframe = tf;
        if (tf === qualificationTimeframe) {
          currentCandles = dashboard.market ? dashboard.market.candles : [];
          currentOverlays = dashboard.overlays || {};
          currentPlan = dashboard.plan;
          redraw();
          return;
        }
        try {
          const [candles, structure] = await Promise.all([
            api.candles({ symbol: dashboard.meta.symbol, timeframe: tf, limit: 500 }),
            api.structure({ symbol: dashboard.meta.symbol, timeframe: tf }),
          ]);
          currentCandles = candles.candles;
          currentOverlays = { zones: structure.zones, range: structure.range, equal_levels: [], swings: structure.swings };
          currentPlan = null;
          redraw();
        } catch (error) {
          toast(`Chart data unavailable: ${error.message}`, "red");
        }
      },
    },
    [],
  );
  api.meta().then((meta) => {
    for (const tf of meta.supported_timeframes) {
      tfSelect.append(el("option", { value: tf, text: tf, selected: tf === qualificationTimeframe || undefined }));
    }
  }).catch(() => {});
  strip.prepend(tfSelect);
  void toggles;

  return card;
}

/* --------------------------------------------------------------------- */
/* Plan card                                                             */
/* --------------------------------------------------------------------- */

function planCard(dashboard) {
  const planning = dashboard.planning || {};
  const plan = dashboard.plan;
  const head = el("div", { class: "card-head" }, [
    el("h3", { class: "card-title", text: "Proposed plan" }),
    plan ? el("span", { class: "tag mono", text: shortId(plan.id), title: plan.id }) : null,
  ]);

  if (!plan || planning.state !== "PLANNABLE") {
    const reason =
      planning.state === "NO_PLAN"
        ? `Step 6 refused a plan: ${planning.reasons.join("; ") || planning.state_detail || "required evidence missing"}`
        : planning.state === "INVALID"
          ? `Step 6 produced an INVALID plan and will not correct it: ${planning.reasons.join("; ") || "hard invariant violated"}`
          : "No qualified setup is available to plan.";
    return el("section", { class: "card", "aria-label": "Proposed plan" }, [
      head,
      emptyState("No proposed plan", reason),
    ]);
  }

  const cell = (label, display, raw, tone, extra) =>
    el("div", { class: "level-cell", dataset: { tone } }, [
      el("div", { class: "k", text: label }),
      el("div", { class: "v mono", text: display, title: raw ? `exact ${raw}` : "" }),
      extra ? el("div", { class: "s", text: extra }) : null,
    ]);
  const levelCell = (label, level, tone, extra) => {
    const value = level && !isMissing(level.value) ? displayPrice(level.value) : null;
    return cell(label, value ? value.display : "UNKNOWN", value ? value.raw : null, tone, extra);
  };

  const targets = plan.targets || [];
  const risk = plan.risk_per_unit;
  const riskDisplay = !isMissing(risk) ? displayPrice(risk) : null;

  return el("section", { class: "card", "aria-label": "Proposed plan" }, [
    head,
    el("div", { class: "level-grid" }, [
      cell("Direction", directionLabel(plan.direction), null, null, `${directionArrow(plan.direction)} ${familyLabel(plan.family)}`),
      levelCell("Entry", plan.entry, "blue"),
      levelCell("Invalidation", plan.invalidation, "amber"),
      levelCell("Protective stop", plan.stop, "red"),
      cell("Risk / unit", riskDisplay ? riskDisplay.display : "UNKNOWN", riskDisplay ? riskDisplay.raw : null, null, "price distance per unit"),
    ]),
    targets.length
      ? el("div", { style: { marginTop: "14px" } }, [
          el("div", { class: "card-title", style: { marginBottom: "6px" }, text: "Targets" }),
          targets.map((target, index) => {
            const value = target.level && !isMissing(target.level.value) ? displayPrice(target.level.value) : null;
            const rr = !isMissing(target.r_multiple) ? `${target.r_multiple} R` : "R UNKNOWN";
            const reward = !isMissing(target.reward_per_unit) ? `reward ${displayOrUnknown(target.reward_per_unit)}/unit` : "reward UNKNOWN";
            return el("div", { class: "target-row" }, [
              el("span", { class: "idx", text: `T${index + 1}` }),
              el("span", { class: "lvl mono", text: value ? value.display : "UNKNOWN", title: value && value.raw ? `exact ${value.raw}` : "" }),
              el("span", { class: "tag", text: target.is_structural ? "structural" : "R-derived" }),
              el("span", { class: "s", style: { color: "var(--text-muted)", fontSize: "12.5px" }, text: reward }),
              el("span", { class: "rr tag mono", text: rr }),
            ]);
          }),
        ])
      : null,
    el("div", { class: "plan-disclaimer", text: "Proposed deterministic plan — not an executed trade. Nothing here places an order or claims profitability." }),
    el("details", { class: "expandable" }, [
      el("summary", { text: "Traceability (plan identity and sources)" }),
      el("div", { class: "detail-body" }, [
        el("div", { class: "kv" }, [
          el("span", { class: "k", text: "plan_id" }), el("span", { class: "v mono", text: plan.id }),
          el("span", { class: "k", text: "setup_id" }), el("span", { class: "v mono", text: plan.setup_id || "UNKNOWN" }),
          el("span", { class: "k", text: "as_of" }), el("span", { class: "v mono", text: formatUtc(plan.as_of) }),
          el("span", { class: "k", text: "planning_rules_version" }), el("span", { class: "v mono", text: plan.planning_rules_version }),
          el("span", { class: "k", text: "config_fingerprint" }), el("span", { class: "v mono", text: plan.config_fingerprint }),
          el("span", { class: "k", text: "setup_config_fingerprint" }), el("span", { class: "v mono", text: plan.setup_config_fingerprint || "UNKNOWN" }),
        ]),
      ]),
    ]),
  ]);
}

/* --------------------------------------------------------------------- */
/* Evidence                                                              */
/* --------------------------------------------------------------------- */

function selectedSetupSnapshot(dashboard) {
  const q = dashboard.qualification || {};
  const snapshot = q.snapshot;
  if (!snapshot || !q.selected_setup_id) return null;
  return (snapshot.setups || []).find((s) => s.id === q.selected_setup_id) || null;
}

function evidenceCard(dashboard) {
  const setup = selectedSetupSnapshot(dashboard);
  const rules = setup ? setup.rules || [] : [];

  const buckets = { for: [], against: [], unknown: [] };
  const vetoes = [];
  for (const rule of rules) {
    if (rule.veto) vetoes.push(rule);
    for (const item of rule.evidence || []) {
      const row = { ...item, rule_id: rule.rule_id, rule_outcome: rule.outcome, required: rule.required };
      if (item.status === "supportive") buckets.for.push(row);
      else if (item.status === "opposing") buckets.against.push(row);
      else if (item.status === "unknown") buckets.unknown.push(row);
    }
  }
  // Failed required rules are engine evidence against qualification even when
  // they carry no explicit evidence rows.
  const failedRules = rules.filter((r) => r.outcome === "failed");

  const column = (title, tone, items, emptyText) =>
    el("div", { class: "evidence-col" }, [
      el("h4", {}, [
        el("span", { class: "dot", style: { background: tone } }),
        `${title} (${items.length})`,
      ]),
      items.length
        ? items.map((item) =>
            el("div", { class: "evidence-item" }, [
              el("div", { class: "cat", text: `${item.category || "evidence"} · ${item.timeframe || ""} · rule ${item.rule_id}` }),
              el("div", { class: "why", text: item.reason }),
            ]),
          )
        : el("div", { class: "evidence-empty", text: emptyText }),
    ]);

  return el("section", { class: "card", "aria-label": "Qualification evidence" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: "Evidence" }),
      el("span", { class: "card-hint", text: setup ? `setup ${shortId(setup.id)}` : "no active setup"),
    ]),
    setup
      ? null
      : emptyState("No setup evidence", "The current snapshot contains no active setup candidate; evidence appears once the engine tracks one."),
    setup
      ? el("div", { class: "evidence-grid" }, [
          column("Evidence for", "var(--green)", buckets.for, "No supportive evidence recorded."),
          column("Evidence against", "var(--red)", buckets.against, "No opposing evidence recorded."),
        ])
      : null,
    setup
      ? el("div", { class: "evidence-grid", style: { marginTop: "14px" } }, [
          column("Unknown / missing", "var(--text-faint)", buckets.unknown, "No unknowns recorded."),
          el("div", { class: "evidence-col" }, [
            el("h4", {}, [el("span", { class: "dot", style: { background: "var(--red)" } }), `Vetoes (${vetoes.length})`]),
            vetoes.length
              ? vetoes.map((rule) =>
                  el("div", { class: "evidence-item" }, [
                    el("div", { class: "cat", text: `veto rule · ${rule.rule_id} · ${rule.outcome}` }),
                    el("div", { class: "why", text: rule.reason }),
                  ]),
                )
              : el("div", { class: "evidence-empty", text: "No veto rules active." }),
          ]),
        ])
      : null,
    setup && failedRules.length
      ? el("details", { class: "expandable" }, [
          el("summary", { text: `Failed rules (${failedRules.length})` }),
          el("div", { class: "detail-body" }, failedRules.map((rule) =>
            el("div", { class: "kv" }, [
              el("span", { class: "k mono", text: rule.rule_id }),
              el("span", { class: "v", text: rule.reason }),
            ]),
          )),
        ])
      : null,
    setup
      ? el("details", { class: "expandable" }, [
          el("summary", { text: "All rules (deterministic detail)" }),
          el("div", { class: "detail-body" }, rules.map((rule) =>
            el("div", { class: "kv" }, [
              el("span", { class: "k mono", text: `${rule.rule_id}${rule.required ? " *" : ""}` }),
              el("span", { class: "v", text: `${rule.outcome}${rule.veto ? " (veto)" : ""} — ${rule.reason}` }),
            ]),
          )),
        ])
      : null,
  ]);
}

/* --------------------------------------------------------------------- */
/* Explanation                                                           */
/* --------------------------------------------------------------------- */

function explanationCard(dashboard, prefs) {
  const explanation = dashboard.explanation;
  const head = el("div", { class: "card-head" }, [
    el("h3", { class: "card-title", text: "Grounded explanation" }),
    explanation && explanation.provenance
      ? el("span", { class: "tag", text: explanation.provenance })
      : null,
  ]);

  if (!explanation || explanation.available === false) {
    const message = explanation && explanation.error ? explanation.error.message : "No explanation is available.";
    return el("section", { class: "card", "aria-label": "Explanation" }, [head, emptyState("No explanation", message)]);
  }

  const compact = prefs.explanationDetail === "compact";
  const sections = explanation.sections || [];
  return el("section", { class: "card", "aria-label": "Grounded explanation" }, [
    head,
    el("p", { class: "explanation-headline", text: explanation.headline }),
    compact
      ? el("details", { class: "expandable" }, [
          el("summary", { text: `Explanation sections (${sections.length})` }),
          el("div", { class: "detail-body" }, sections.map(sectionNode)),
        ])
      : sections.map(sectionNode),
    explanation.limitations && explanation.limitations.length
      ? el("div", { class: "explanation-limitations" }, [
          el("div", { class: "card-title", style: { marginBottom: "6px" }, text: "Limitations" }),
          el("ul", { style: { margin: 0, paddingLeft: "18px" } }, explanation.limitations.map((limitation) => el("li", { text: limitation }))),
        ])
      : null,
    el("div", { class: "provenance-note", text: `Renderer ${explanation.renderer_id} v${explanation.renderer_version} · generated ${formatUtc(explanation.generated_at)} · narrative text never overrides deterministic state.` }),
  ]);
}

function sectionNode(section) {
  return el("div", { class: "explanation-section" }, [
    el("h4", { text: `${section.number}. ${section.title}` }),
    el("p", { text: section.text }),
  ]);
}

/* --------------------------------------------------------------------- */
/* Decision controls                                                     */
/* --------------------------------------------------------------------- */

function decisionCard(dashboard) {
  const availability = decisionAvailability(dashboard);
  const journal = dashboard.journal || {};
  const latest = journal.latest_decision;

  const buttons = ["ACCEPTED", "REJECTED", "SKIPPED"].map((decision) =>
    el("button", {
      class: `btn btn-${decision === "ACCEPTED" ? "accept" : decision === "REJECTED" ? "reject" : "skip"}`,
      type: "button",
      disabled: !availability.enabled || undefined,
      title: DECISION_MEANINGS[decision],
      "aria-label": `${DECISION_LABELS[decision]} — ${DECISION_MEANINGS[decision]}`,
      onclick: () => openConfirmation(dashboard, decision),
    }, [DECISION_LABELS[decision]]),
  );

  return el("section", { class: "card", "aria-label": "Decision controls" }, [
    el("div", { class: "card-head" }, [
      el("h3", { class: "card-title", text: "Bailey's decision" }),
      latest
        ? el("span", { class: `badge decision-${latest.decision}`, dataset: { tone: toneForDecision(latest.decision) }, text: `Last recorded: ${latest.decision}` })
        : el("span", { class: "tag", text: "No decision recorded for this proposal" }),
    ]),
    el("div", { class: "decision-buttons" }, buttons),
    availability.enabled
      ? el("div", { class: "decision-note", text: "There is no default. Choosing records the decision in the append-only journal — it never places an order. Re-choosing later appends a correction; history is preserved." })
      : el("div", { class: "decision-note", role: "note", text: `Controls disabled: ${availability.reason}` }),
  ]);
}

function toneForDecision(decision) {
  if (decision === "ACCEPTED") return "green";
  if (decision === "REJECTED") return "red";
  if (decision === "SKIPPED") return "amber";
  return "neutral";
}

function openConfirmation(dashboard, decision) {
  const summary = confirmationSummary(dashboard);
  let submitting = false;

  const row = (k, v) => el("div", { class: "kv" }, [el("span", { class: "k", text: k }), el("span", { class: "v mono", text: v })]);

  const reasonInput = el("textarea", {
    class: "input-inline",
    rows: 2,
    maxlength: 2000,
    placeholder: "Optional note (stored verbatim, never interpreted)",
    style: { width: "100%", resize: "vertical" },
  });

  const confirmButton = el("button", { class: "btn btn-primary", type: "button", text: `Record ${DECISION_LABELS[decision]}` });
  const cancelButton = el("button", { class: "btn btn-quiet", type: "button", text: "Cancel" });

  const content = el("div", {}, [
    el("h3", { text: `${DECISION_LABELS[decision]} this proposal?` }),
    el("p", { style: { color: "var(--text-dim)", fontSize: "13.5px" }, text: DECISION_MEANINGS[decision] }),
    el("div", { class: "kv", style: { marginBottom: "12px" } }, [
      row("Snapshot as-of", formatUtc(summary.asOf)),
      row("Symbol / timeframe", `${summary.symbol} · ${summary.timeframe}`),
      row("Setup state", `${summary.setupState} · ${familyLabel(summary.family)} · ${directionLabel(summary.direction)}`),
      row("Plan state", summary.planState || "UNKNOWN"),
      row("Entry", displayOrUnknown(summary.entry)),
      row("Stop", displayOrUnknown(summary.stop)),
      row("Targets", summary.targets.length ? summary.targets.map((t) => displayOrUnknown(t)).join(", ") : "UNKNOWN"),
      row("Setup id", summary.setupId ? shortId(summary.setupId, 16) : "UNKNOWN"),
    ]),
    reasonInput,
    el("div", { class: "modal-actions" }, [cancelButton, confirmButton]),
  ]);

  const close = openModal(content);
  cancelButton.onclick = close;
  confirmButton.onclick = async () => {
    if (submitting) return; // duplicate-submission guard
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
      renderDashboard(document.getElementById("view"));
    } catch (error) {
      submitting = false;
      confirmButton.disabled = false;
      confirmButton.textContent = `Record ${DECISION_LABELS[decision]}`;
      toast(error.message, "red", 6500);
    }
  };
}
