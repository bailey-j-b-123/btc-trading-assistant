/**
 * "Bot is watching" — the setup-focus presentation (Fix #2).
 *
 * PRESENTATION ONLY. Every identity, state, count, and sentence below is a
 * projection of existing backend payload fields. This module never selects,
 * scores, ranks, or re-evaluates setup candidates:
 *
 *  - The one primary setup is the backend's own deterministic choice: the
 *    qualification snapshot's `selected_setup_id` (the engine's selected
 *    setup), or — only when the engine selected nothing — the single subject
 *    the backend itself resolved for its `looking_for` payload. When the
 *    backend provides neither, NO setup is promoted; an honest state is shown
 *    instead. Array order is never used to choose a setup.
 *  - "Other setups" rows are grouped summaries of the backend's live
 *    candidate collection (`scenario.bot_seeing.live_setups`, or the
 *    qualification setup list filtered by the backend's own WATCH/QUALIFIED
 *    states when the scenario section is unavailable). Grouping counts and
 *    labels only; it never mutates, merges, or re-orders candidates, and
 *    never changes a state or a qualification.
 *  - The full per-candidate audit material (setup ids, seed times, passed /
 *    failed / pending rules, strengthen / wait / invalidate evidence, ended
 *    candidates, exact backend strings) stays available behind the single
 *    collapsed "Technical details / All setups" disclosure.
 *
 * No setup state is ever upgraded here: the state chip is the existing
 * `setupStateLabel` translation of the backend's own `state` value.
 */

import { directionLabel, displayOrUnknown, familyLabel, formatUtc, isMissing, shortId } from "./format.js";
import {
  aggregateSeeingLine,
  backendNoticeText,
  evidenceCategoryLabel,
  hasValidTradePlan,
  hierarchyDecisionLabel,
  invalidateMetaLine,
  levelSourceText,
  liveSetupMetaLine,
  setupStateLabel,
  shortRuleTitle,
  terminalSentence,
  translateRule,
} from "./plain.js";
import { el } from "./util.js";

/** How many compact group rows the normal view may show. */
export const MAX_GROUP_ROWS = 3;

/** How many translated confirmed/supporting facts the primary card shows. */
export const MAX_WHY_FACTS = 3;

const FAMILY_SHORT_LABELS = {
  breakout_retest_continuation: "breakout → retest",
  failed_breakout_sweep_reversal: "failed breakout → sweep reversal",
  range_rejection_reversal: "range rejection",
};

function familyShortLabel(family) {
  if (isMissing(family)) return "setup";
  return FAMILY_SHORT_LABELS[family] || familyLabel(family).toLowerCase();
}

function directionWord(direction) {
  if (direction === "bullish" || direction === "bearish") return direction;
  return "unknown-direction";
}

// ---------------------------------------------------------------------------
// Backend candidate collections (read-only projections)
// ---------------------------------------------------------------------------

/**
 * The backend's live candidate collection, verbatim-shaped. Prefers the
 * scenario payload's `bot_seeing.live_setups` (exactly what the engine is
 * "seeing"); when that section is unavailable, falls back to the
 * qualification setup list filtered by the backend's own live states
 * (WATCH / QUALIFIED — the same definition `_scenario` uses server-side).
 * No rule is re-evaluated; returns null when neither collection is usable.
 */
export function liveCandidates(dashboard) {
  const scenario = dashboard?.scenario;
  if (scenario && typeof scenario === "object" && scenario.available === true) {
    const seeing = scenario.bot_seeing && typeof scenario.bot_seeing === "object"
      ? scenario.bot_seeing
      : {};
    if (Array.isArray(seeing.live_setups)) {
      // Verbatim copies (same field names); nothing is recomputed.
      return seeing.live_setups.map((setup) => ({ ...setup }));
    }
  }
  const qualification = dashboard?.qualification;
  if (qualification?.available === true && Array.isArray(qualification.setups)) {
    return qualification.setups
      .filter((setup) => setup?.state === "WATCH" || setup?.state === "QUALIFIED")
      .map((setup) => ({
        setup_id: setup?.id ?? null,
        family: setup?.family ?? null,
        direction: setup?.direction ?? null,
        state: setup?.state ?? null,
        created_at: setup?.created_at ?? null,
      }));
  }
  return null;
}

/**
 * The backend's own primary-setup identity, never a frontend choice.
 * `source` is "selected" (engine `selected_setup_id`), "single-subject" (the
 * subject the backend resolved for `looking_for`), or null.
 * `note` explains an unresolvable primary ("selected-data-missing" when the
 * backend named a setup whose data is absent — no substitute is invented).
 */
export function primarySetup(dashboard) {
  const qualification = dashboard?.qualification || {};
  if (qualification.available !== true) {
    return { setup: null, source: null, note: "qualification-unavailable" };
  }
  const summaries = Array.isArray(qualification.setups) ? qualification.setups : [];
  const snapshotSetups = Array.isArray(qualification.snapshot?.setups)
    ? qualification.snapshot.setups
    : [];

  const selectedId = typeof qualification.selected_setup_id === "string" &&
    qualification.selected_setup_id
    ? qualification.selected_setup_id
    : null;
  const fact = dashboard?.looking_for;
  const factId = fact?.available === true &&
    typeof fact.setup_id === "string" && fact.setup_id
    ? fact.setup_id
    : null;

  let id = null;
  let source = null;
  if (selectedId) {
    id = selectedId;
    source = "selected";
  } else if (factId) {
    id = factId;
    source = "single-subject";
  }
  if (!id) return { setup: null, source: null, note: null };

  const summary = summaries.find((setup) => setup?.id === id) ||
    snapshotSetups.find((setup) => setup?.id === id) ||
    null;
  if (!summary) {
    // The backend named a setup whose data is missing: never show another.
    return { setup: null, source, note: "selected-data-missing" };
  }
  const details = snapshotSetups.find((setup) => setup?.id === id) || null;
  const rules = Array.isArray(details?.rules)
    ? details.rules
    : Array.isArray(summary.rules)
      ? summary.rules
      : [];
  return {
    setup: { ...details, ...summary, rules, selected: source === "selected" },
    source,
    note: null,
  };
}

/**
 * Group live candidates by the backend's own family + direction values.
 * Presentation only: rows are count summaries; row order is by count (largest
 * first, backend first-seen order on ties) and never implies trading
 * priority, equivalence, or a ranking of candidates.
 */
export function otherSetupGroups(candidates) {
  const list = Array.isArray(candidates) ? candidates : [];
  const groups = [];
  const byKey = new Map();
  for (const candidate of list) {
    const family = candidate?.family ?? null;
    const direction = candidate?.direction ?? null;
    const key = `${String(family)}|${String(direction)}`;
    let group = byKey.get(key);
    if (!group) {
      group = { key, family, direction, candidates: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.candidates.push(candidate);
  }
  return groups
    .map((group, firstSeen) => ({ ...group, firstSeen }))
    .sort((a, b) => b.candidates.length - a.candidates.length || a.firstSeen - b.firstSeen);
}

// ---------------------------------------------------------------------------
// Primary-setup facts (translations of existing backend fields)
// ---------------------------------------------------------------------------

function rulePendingRequired(rule) {
  return rule?.required !== false && rule?.outcome === "pending";
}

function ruleFailedRequired(rule) {
  return rule?.required !== false && rule?.veto !== true && rule?.outcome === "failed";
}

/** Required pending rules of the primary setup, from whichever backend list carries them. */
export function pendingRequiredRules(dashboard, setup) {
  if (!setup) return [];
  const rules = Array.isArray(setup.rules) ? setup.rules : null;
  if (rules && rules.length) {
    return rules.filter(rulePendingRequired);
  }
  const fact = dashboard?.looking_for;
  if (fact?.available === true && fact.setup_id === setup.id &&
      Array.isArray(fact.pending_required)) {
    return fact.pending_required.map((item) => ({
      rule_id: item?.rule_id ?? item?.rule ?? null,
      reason: typeof item?.reason === "string" ? item.reason : "",
      outcome: "pending",
      required: true,
    }));
  }
  const live = liveCandidates(dashboard)?.find((entry) => entry.setup_id === setup.id) || null;
  const pending = Array.isArray(live?.pending_required) ? live.pending_required : [];
  return pending.map((item) => ({
    rule_id: item?.rule ?? item?.rule_id ?? null,
    reason: typeof item?.reason === "string" ? item.reason : "",
    outcome: "pending",
    required: true,
  }));
}

/**
 * Up to MAX_WHY_FACTS translated confirmed/supporting facts for the primary
 * setup, taken in the backend's own rule order (never re-ranked): required
 * passed rules first, then backend-flagged supportive/neutral evidence items.
 * Administrative timing bookkeeping is not a "why" fact. Nothing is invented;
 * an empty list stays empty.
 */
export function supportingFacts(setup, limit = MAX_WHY_FACTS) {
  if (!setup) return [];
  const facts = [];
  const seen = new Set();
  const direction = setup.direction || null;
  const push = (sentence) => {
    if (!sentence || seen.has(sentence) || facts.length >= limit) return;
    seen.add(sentence);
    facts.push(sentence);
  };
  const rules = Array.isArray(setup.rules) ? setup.rules : [];
  for (const rule of rules) {
    if (facts.length >= limit) break;
    if (rule?.required === false) continue;
    if (rule?.outcome !== "passed") continue;
    // Skip pure timing bookkeeping (e.g. "assessment runs after the seed"):
    // a real backend check, but not a reason the setup is interesting.
    const categories = (Array.isArray(rule.evidence) ? rule.evidence : [])
      .map((item) => item?.category)
      .filter(Boolean);
    if (categories.length && categories.every((category) => category === "timing")) continue;
    push(translateRule(rule, { direction }).sentence);
  }
  for (const rule of rules) {
    if (facts.length >= limit) break;
    for (const item of Array.isArray(rule?.evidence) ? rule.evidence : []) {
      if (facts.length >= limit) break;
      if (item?.status !== "supportive" && item?.status !== "neutral") continue;
      if (item?.category === "timing") continue;
      const source = typeof item?.reason === "string" && item.reason.trim()
        ? { ...rule, reason: item.reason }
        : rule;
      push(translateRule(source, { direction }).sentence);
    }
  }
  return facts;
}

/**
 * One clear "next" requirement for the primary setup, translated from the
 * backend's own rule outcomes — never a frontend judgement:
 * pending required rule → blocking failed rule →, when the deterministic
 * plan is actually PLANNABLE, the hierarchy's own decision label.
 */
export function nextRequirement(dashboard, setup) {
  if (!setup) return null;
  const direction = setup.direction || null;
  const pending = pendingRequiredRules(dashboard, setup);
  if (pending.length) return translateRule(pending[0], { direction }).sentence;
  const rules = Array.isArray(setup.rules) ? setup.rules : [];
  const failed = rules.filter(ruleFailedRequired);
  if (failed.length) return translateRule(failed[0], { direction }).sentence;
  if (setup.selected === true && hasValidTradePlan(dashboard)) {
    // No outstanding setup checks: the next gate is the deterministic
    // multi-timeframe hierarchy (its own label, never upgraded here).
    return hierarchyDecisionLabel(dashboard?.multi_timeframe);
  }
  return "No outstanding setup checks reported by the backend.";
}

/**
 * One invalidation statement from existing backend facts: the hierarchy's own
 * `invalidated_if` sentences first (condition form), then — only for the
 * engine-selected setup with a PLANNABLE plan — the plan's invalidation
 * level. Never invents a level or condition.
 */
export function invalidationStatement(dashboard, setup) {
  const hierarchy = dashboard?.multi_timeframe;
  const items = Array.isArray(hierarchy?.invalidated_if)
    ? hierarchy.invalidated_if.filter((value) => typeof value === "string" && value.trim())
    : [];
  if (items.length) return items.join("; ");
  if (setup?.selected === true && hasValidTradePlan(dashboard)) {
    const value = dashboard?.plan?.invalidation?.value;
    if (!isMissing(value)) {
      return `the deterministic plan invalidation level ${displayOrUnknown(value)}`;
    }
  }
  return "Not specified by the backend.";
}

// ---------------------------------------------------------------------------
// View model
// ---------------------------------------------------------------------------

/**
 * Pure projection for the "Bot is watching" card. Counts come directly from
 * the backend candidate collection; `hiddenGroupCount` keeps the accounting
 * exact when the normal view shows only MAX_GROUP_ROWS group rows.
 */
export function botWatchingViewModel(dashboard) {
  const qualification = dashboard?.qualification || {};
  const scenario = dashboard?.scenario || {};
  const scenarioAvailable = scenario.available === true;
  if (qualification.available !== true && !scenarioAvailable) {
    return {
      available: false,
      reason: backendNoticeText(
        typeof scenario.reason === "string" ? scenario.reason : null,
        "Setup information unavailable from the backend.",
      ),
    };
  }

  const primary = primarySetup(dashboard);
  const live = liveCandidates(dashboard);
  const liveCount = live === null ? null : live.length;
  const others = live === null
    ? []
    : live.filter((entry) => !(primary.setup && entry.setup_id === primary.setup.id));
  const groups = otherSetupGroups(others);
  const shownGroups = groups.slice(0, MAX_GROUP_ROWS);

  const waiting = scenario.waiting_for && typeof scenario.waiting_for === "object"
    ? scenario.waiting_for
    : {};

  return {
    available: true,
    scenarioAvailable,
    scenarioReason: scenarioAvailable || typeof scenario.reason !== "string" ? null : scenario.reason,
    primary: primary.setup,
    primarySource: primary.source,
    primaryNote: primary.note,
    liveCount,
    otherCount: live === null ? null : others.length,
    // Every group (not just the rows the normal view shows), so the exact
    // accounting stays verifiable: primary + every group count === live count.
    allGroups: groups,
    groups: shownGroups,
    hiddenGroupCount: Math.max(groups.length - shownGroups.length, 0),
    noLiveNote: liveCount === 0 && typeof waiting.note === "string" && waiting.note.trim()
      ? waiting.note.trim()
      : null,
  };
}

// ---------------------------------------------------------------------------
// Raw Level-3 rows (verbatim backend strings, for the collapsed disclosure)
// ---------------------------------------------------------------------------

/** Raw rule rows: "rule_id: outcome (required|optional · veto) — reason". */
export function rawRuleRows(candidate) {
  if (!candidate || !Array.isArray(candidate.rules)) return [];
  return candidate.rules.map((rule) => {
    const outcome = rule?.outcome || "UNKNOWN";
    const id = rule?.rule_id || "?";
    const requirement = rule?.required === false ? "optional" : "required";
    const veto = rule?.veto === true ? " · veto" : "";
    const reason = typeof rule?.reason === "string" && rule.reason.trim()
      ? ` — ${rule.reason.trim()}`
      : "";
    return `${id}: ${outcome} (${requirement}${veto})${reason}`;
  });
}

/**
 * Pending-required entries carry {rule, reason} only: by backend construction
 * (live_setup_payload) they are always required + pending, so the translator
 * fills those in. Unknown shapes fall back to a humanized title.
 */
function pendingRequiredSentences(pending, direction = null) {
  const items = Array.isArray(pending) ? pending : [];
  return items.map((item) => translateRule(
    { rule_id: item?.rule, outcome: "pending", reason: item?.reason, required: true },
    { direction },
  ).sentence);
}

/** Translated evidence columns for one setup (the old Evidence card detail). */
export function setupEvidence(setup) {
  const positive = [];
  const missing = [];
  const optional = [];
  if (!setup) return { positive, missing, optional };
  const seen = new Set();
  const direction = setup.direction || null;
  const pushRow = (list, rule, item) => {
    // Each evidence item is translated from its own reason (the backend
    // always repeats the rule reason there, but an item-level sentence
    // preserves information if they ever diverge).
    const source = item && typeof item.reason === "string" && item.reason.trim()
      ? { ...rule, reason: item.reason }
      : rule;
    const translated = translateRule(source, { direction });
    if (seen.has(translated.sentence)) return;
    seen.add(translated.sentence);
    list.push({
      meta: [evidenceCategoryLabel(item?.category), item?.timeframe || null, translated.title].filter(Boolean).join(" · "),
      reason: translated.sentence,
    });
  };
  for (const rule of setup.rules || []) {
    const evidence = Array.isArray(rule.evidence) ? rule.evidence : [];
    for (const item of evidence) {
      if (item.status === "supportive" || item.status === "neutral") {
        pushRow(positive, rule, item);
      } else if (item.status === "opposing" || item.status === "unknown") {
        pushRow(rule?.required === false ? optional : missing, rule, item);
      }
    }
    // Rules without evidence rows (or whose evidence was filtered) still
    // surface here when they fail or wait; optional checks never block.
    if (["failed", "pending"].includes(rule.outcome)) {
      pushRow(rule?.required === false ? optional : missing, rule, null);
    }
  }
  return { positive, missing, optional };
}

// ---------------------------------------------------------------------------
// Disclosure blocks (Level 3 — the full audit material, collapsed by default)
// ---------------------------------------------------------------------------

function detailHeader(text) {
  return el("div", { class: "bw-detail-header", text });
}

function monoRow(text) {
  return el("div", { class: "chart-note mono", text });
}

function liveSetupNode(setup) {
  const failed = Array.isArray(setup.failed_rules) ? setup.failed_rules : [];
  const passed = Array.isArray(setup.passed_rules) ? setup.passed_rules : [];
  const pending = pendingRequiredSentences(setup.pending_required, setup.direction);
  const pendingIds = (Array.isArray(setup.pending_required) ? setup.pending_required : [])
    .map((item) => item?.rule)
    .filter(Boolean);
  return el("div", { class: "terminal-evidence-item" }, [
    monoRow(`setup id: ${setup.setup_id ?? "UNKNOWN"}`),
    el("div", {
      class: "evidence-meta",
      text: `${familyLabel(setup.family)} · ${directionLabel(setup.direction)} · ${liveSetupMetaLine(setup)}` +
        (setup.created_at ? ` · seeded ${formatUtc(setup.created_at)}` : ""),
    }),
    monoRow(`rules — passed: ${passed.join(", ") || "none"} · failed: ${failed.join(", ") || "none"} · pending required: ${pendingIds.join(", ") || "none"}`),
    el("div", { class: "evidence-reason", text: `Passed: ${passed.length ? passed.map(shortRuleTitle).join(", ") : "none"}` }),
    el("div", { class: "evidence-reason", text: `Failed: ${failed.length ? failed.map(shortRuleTitle).join(", ") : "none"}` }),
    el("div", { class: "evidence-reason", text: pending.length ? `Still required: ${pending.join(" ")}` : "Nothing still required." }),
  ]);
}

function strengthenBlock(title, side) {
  const direction = side?.direction ? directionLabel(side.direction) : title;
  const developing = Array.isArray(side?.developing_setups) ? side.developing_setups : [];
  const starters = side?.to_start_a_setup && typeof side.to_start_a_setup === "object"
    ? side.to_start_a_setup
    : {};
  const rows = [
    el("div", { class: "evidence-meta", text: title }),
    developing.length || side?.none_developing !== true
      ? null
      : el("div", { class: "evidence-reason", text: `No developing ${direction} setups.` }),
    ...developing.map((item) => {
      const pending = pendingRequiredSentences(item.pending_required);
      return el("div", {
        class: "evidence-reason",
        text: `setup ${shortId(item.setup_id)} (${setupStateLabel(item.state)}): ` +
          (pending.length ? `still required: ${pending.join(" ")}` : "nothing still required."),
      });
    }),
    ...Object.entries(starters).map(([family, text]) => el("div", {
      class: "evidence-reason",
      text: `${familyLabel(family)} starts with: ${text}`,
    })),
  ];
  return el("div", { class: "terminal-evidence-item" }, rows);
}

function planLevelNode(level) {
  if (!level || typeof level !== "object" || isMissing(level.value)) return null;
  const source = level.source_type ? ` (${levelSourceText(level.source_type)})` : "";
  return el("span", {}, [el("span", { class: "mono", text: displayOrUnknown(level.value) }), source]);
}

function invalidateNode(item) {
  const evidence = Array.isArray(item.invalidation_evidence) ? item.invalidation_evidence : [];
  const rows = [
    monoRow(`setup id: ${item.setup_id ?? "UNKNOWN"}`),
    el("div", {
      class: "evidence-meta",
      text: invalidateMetaLine(item),
    }),
    ...(evidence.length
      ? evidence.map((entry) => el("div", {
          // Invalidation evidence only ever comes from required rules (the
          // optional rules carry higher_timeframe/classical_pattern evidence),
          // so the missing `required` field is safely filled in.
          class: "evidence-reason",
          text: translateRule(
            { rule_id: entry?.rule, outcome: entry?.outcome, reason: entry?.reason, required: true },
          ).sentence,
        }))
      : [el("div", { class: "evidence-reason", text: "No invalidation/lifecycle evidence yet." })]),
  ];
  const entry = planLevelNode(item.plan_entry);
  const stop = planLevelNode(item.plan_stop);
  const invalidation = planLevelNode(item.plan_invalidation);
  if (entry || stop || invalidation) {
    rows.push(el("div", { class: "evidence-reason" }, [
      "Selected plan — entry ",
      entry || "?",
      " · stop ",
      stop || "?",
      " · invalidation ",
      invalidation || "?",
    ]));
  }
  return el("div", { class: "terminal-evidence-item" }, rows);
}

function evidenceColumn(title, tone, rows, emptyText) {
  const items = rows.map((row) => el("div", { class: "terminal-evidence-item" }, [
    el("div", { class: "evidence-meta", text: row.meta }),
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

/** Ended (invalidated/expired) candidates from the qualification setup list. */
function endedCandidates(dashboard) {
  const qualification = dashboard?.qualification;
  if (qualification?.available !== true || !Array.isArray(qualification.setups)) return [];
  return qualification.setups.filter((setup) => setup?.ended_at || setup?.terminal_reason);
}

function endedCandidatesNode(dashboard) {
  const ended = endedCandidates(dashboard);
  if (!ended.length) return null;
  return el("div", { class: "terminal-evidence-item" }, [
    el("div", {
      class: "evidence-meta",
      text: `${ended.length} candidate(s) already ended (invalidated or expired) — retained for audit`,
    }),
    ...ended.map((setup) => el("div", {
      class: "evidence-reason mono",
      text: `setup id: ${setup.id ?? "UNKNOWN"} · ${familyLabel(setup.family)} · ${directionLabel(setup.direction)} · ended ${formatUtc(setup.ended_at)} · ${setup.terminal_reason ? terminalSentence(setup.terminal_reason) : "reason unknown"}`,
    })),
  ]);
}

/** The ONE collapsed disclosure holding the full audit/debug material. */
function technicalDisclosure(dashboard, model) {
  const scenario = dashboard?.scenario || {};
  const body = [];

  if (model.scenarioAvailable) {
    const seeing = scenario.bot_seeing && typeof scenario.bot_seeing === "object"
      ? scenario.bot_seeing
      : {};
    body.push(detailHeader("Aggregate state (exact backend wording)"));
    body.push(monoRow(aggregateSeeingLine({
      ...seeing,
      live_count: typeof seeing.live_count === "number" ? seeing.live_count : model.liveCount ?? undefined,
    })));
    if (typeof scenario.doing_now === "string" && scenario.doing_now.trim()) {
      body.push(monoRow(`doing_now: ${scenario.doing_now.trim()}`));
    }
  } else {
    body.push(detailHeader("Scenario audit"));
    body.push(monoRow(
      `scenario unavailable: ${model.scenarioReason || "reason not supplied"}`,
    ));
  }

  if (model.primary) {
    const fact = dashboard?.looking_for;
    body.push(detailHeader(`Primary setup — full rule record (${model.primarySource === "selected" ? "engine-selected" : "the only watched setup"})`));
    body.push(monoRow(`setup id: ${model.primary.id ?? "UNKNOWN"}`));
    if (model.primary.seed_event_id) body.push(monoRow(`seed event id: ${model.primary.seed_event_id}`));
    if (fact?.available === true && fact.setup_id === model.primary.id && fact.seed_event &&
        typeof fact.seed_event === "object") {
      body.push(monoRow(
        `seed event: ${fact.seed_event.kind || "unknown"} · known at ${fact.seed_event.known_at || "unknown"}`,
      ));
    }
    if (model.primary.created_at) body.push(monoRow(`seeded (created_at): ${model.primary.created_at}`));
    for (const row of rawRuleRows(model.primary)) body.push(monoRow(row));
    // The normal-view invalidation line is clamped for one-glance reading;
    // the full hierarchy wording stays here, verbatim.
    const hierarchy = dashboard?.multi_timeframe;
    if (typeof hierarchy?.invalidated_if_text === "string" && hierarchy.invalidated_if_text.trim()) {
      body.push(monoRow(`invalidation (hierarchy wording): ${hierarchy.invalidated_if_text.trim()}`));
    } else if (Array.isArray(hierarchy?.invalidated_if) && hierarchy.invalidated_if.length) {
      body.push(monoRow(`invalidation (hierarchy wording): ${hierarchy.invalidated_if.join("; ")}`));
    }
    const evidence = setupEvidence(model.primary);
    body.push(el("div", { class: "evidence-grid" }, [
      evidenceColumn("FOR THE SETUP", "var(--green)", evidence.positive, "No supportive evidence was supplied."),
      evidenceColumn("AGAINST / STILL MISSING", "var(--amber)", evidence.missing, "No opposing or missing-rule detail was supplied."),
    ]));
    if (evidence.optional.length) {
      body.push(el("div", { class: "terminal-evidence-item" }, [
        el("div", { class: "evidence-meta", text: "OPTIONAL CONTEXT · NEVER BLOCKS" }),
        ...evidence.optional.map((row) => el("div", { class: "evidence-reason", text: row.reason })),
      ]));
    }
  }

  const live = liveCandidates(dashboard) || [];
  if (model.scenarioAvailable) {
    // The scenario payload's own candidate list, verbatim.
    const seeing = scenario.bot_seeing && typeof scenario.bot_seeing === "object"
      ? scenario.bot_seeing
      : {};
    const scenarioLive = Array.isArray(seeing.live_setups) ? seeing.live_setups : [];
    body.push(detailHeader("All live setups (every candidate, verbatim)"));
    body.push(...(scenarioLive.length
      ? scenarioLive.map(liveSetupNode)
      : [el("div", { class: "evidence-empty", text: "No live setups at this close." })]));

    body.push(detailHeader("What would strengthen each side"));
    body.push(strengthenBlock("BULLISH case", scenario.strengthen_bullish));
    body.push(strengthenBlock("BEARISH case", scenario.strengthen_bearish));

    const waiting = scenario.waiting_for && typeof scenario.waiting_for === "object"
      ? scenario.waiting_for
      : {};
    const pending = Array.isArray(waiting.pending) ? waiting.pending : [];
    body.push(detailHeader("Waiting for (merged across live setups)"));
    body.push(...(pending.length
      ? pending.map((item) => {
          const translated = translateRule(
            { rule_id: item?.rule, outcome: "pending", reason: item?.reason, required: true },
          );
          const count = Array.isArray(item.setup_ids) ? item.setup_ids.length : "?";
          return el("div", { class: "terminal-evidence-item" }, [
            el("div", { class: "evidence-meta", text: translated.title }),
            el("div", {
              class: "evidence-reason",
              text: `${translated.sentence} (${count} setup(s))`,
            }),
          ]);
        })
      : [el("div", {
          class: "evidence-empty",
          text: typeof waiting.note === "string" && waiting.note.trim()
            ? waiting.note.trim()
            : "Nothing outstanding.",
        })]));

    const cases = Array.isArray(scenario.invalidate?.cases) ? scenario.invalidate.cases : [];
    body.push(detailHeader("What would invalidate (per live setup)"));
    body.push(...(cases.length
      ? cases.map(invalidateNode)
      : [el("div", { class: "evidence-empty", text: "No live setups to invalidate." })]));
  } else if (live.length) {
    // Scenario audit is unavailable: still list every live candidate the
    // qualification payload carries, verbatim.
    body.push(detailHeader("All live setups (qualification list)"));
    body.push(...live.map((setup) => el("div", {
      class: "evidence-reason mono",
      text: `setup id: ${setup.setup_id ?? "UNKNOWN"} · ${familyLabel(setup.family)} · ${directionLabel(setup.direction)} · ${setupStateLabel(setup.state)}${setup.created_at ? ` · seeded ${formatUtc(setup.created_at)}` : ""}`,
    })));
  }

  const ended = endedCandidatesNode(dashboard);
  if (ended) {
    body.push(detailHeader("Ended candidates"));
    body.push(ended);
  }

  return el("details", { class: "details-disclosure bot-watching-details" }, [
    el("summary", { text: "Technical details / All setups" }),
    el("div", { class: "details-body" }, body),
  ]);
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

function primaryTitle(setup) {
  const summary = familyShortLabel(setup.family);
  if (setup.direction === "bullish" || setup.direction === "bearish") {
    return `${directionLabel(setup.direction)} ${summary}`;
  }
  return `Unknown direction · ${summary}`;
}

function primaryBlock(dashboard, model) {
  const setup = model.primary;
  const state = setupStateLabel(setup.state);
  const tone = setup.state === "QUALIFIED" ? "green" : setup.state === "WATCH" ? "amber" : "neutral";
  const facts = supportingFacts(setup);
  return el("div", { class: "bot-watching-primary" }, [
    el("div", { class: "bot-watching-title-row" }, [
      el("strong", { class: "bot-watching-title", text: primaryTitle(setup) }),
      el("span", { class: "bot-watching-state", dataset: { tone }, text: state }),
    ]),
    el("div", { class: "bw-why" }, [
      el("div", { class: "bw-label", text: "Why" }),
      ...(facts.length
        ? facts.map((fact) => el("div", { class: "bw-fact" }, [
            el("span", { class: "bw-tick", "aria-hidden": "true", text: "✓" }),
            el("span", { class: "bw-fact-text", text: fact }),
          ]))
        : [el("div", { class: "bw-fact", text: "Supporting facts unavailable from the backend." })]),
    ]),
    el("div", { class: "bw-row" }, [
      el("b", { text: "Next" }),
      el("span", { class: "bw-next", text: nextRequirement(dashboard, setup) || "Unknown." }),
    ]),
    el("div", { class: "bw-row" }, [
      el("b", { text: "Invalid if" }),
      el("span", { class: "bw-invalid", text: invalidationStatement(dashboard, setup) }),
    ]),
    el("div", { class: "looking-for-disclaimer", text: "Scenario — not prediction" }),
  ]);
}

function honestStateBlock(model) {
  let line;
  if (model.primaryNote === "selected-data-missing") {
    line = "The backend named a setup whose details are unavailable — no substitute setup is shown.";
  } else if (model.liveCount === 0) {
    line = "No setups being monitored at this close.";
  } else if (model.liveCount === null) {
    line = "Setup focus unavailable from the backend.";
  } else {
    const noun = model.liveCount === 1 ? "setup" : "setups";
    line = `${model.liveCount} ${noun} being monitored — no single setup selected.`;
  }
  const body = [el("div", { class: "bot-watching-none-title", text: line })];
  if (model.liveCount === 0 && model.noLiveNote) {
    body.push(el("div", { class: "chart-note", text: model.noLiveNote }));
  }
  return el("div", { class: "bot-watching-primary bot-watching-none" }, body);
}

function groupRow(group) {
  const count = group.candidates.length;
  const qualifiedCount = group.candidates.filter((entry) => entry.state === "QUALIFIED").length;
  return el("div", { class: "other-setup-row", role: "listitem" }, [
    el("span", { class: "other-setup-count", text: String(count) }),
    el("span", { class: "other-setup-label", text: `${directionWord(group.direction)} ${familyShortLabel(group.family)}` }),
    qualifiedCount
      ? el("span", { class: "other-setup-state", text: `${qualifiedCount} qualified` })
      : null,
  ]);
}

function otherSetupsBlock(model) {
  const children = [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Other setups being watched" }),
      model.otherCount === null
        ? null
        : el("span", {
            class: "card-hint",
            text: `${model.otherCount} other live candidate${model.otherCount === 1 ? "" : "s"}`,
          }),
    ]),
  ];
  if (model.liveCount === null) {
    children.push(el("div", {
      class: "plan-reason",
      text: "The live candidate list is unavailable from the backend.",
    }));
  } else if (model.otherCount === 0) {
    children.push(el("div", {
      class: "chart-note",
      text: model.liveCount === 0
        ? "Nothing else is being monitored."
        : "No other setups being watched.",
    }));
  } else {
    children.push(el("div", { class: "other-setups", role: "list" }, model.groups.map(groupRow)));
    if (model.hiddenGroupCount > 0) {
      children.push(el("div", {
        class: "other-groups-more",
        text: `+ ${model.hiddenGroupCount} more setup group${model.hiddenGroupCount === 1 ? "" : "s"}`,
      }));
    }
  }
  return el("div", { class: "other-setups-block" }, children);
}

/** The "Bot is watching" card: one primary setup, grouped others, one audit disclosure. */
export function botWatchingCard(dashboard) {
  const model = botWatchingViewModel(dashboard);
  if (!model.available) {
    return el("section", { class: "card terminal-card bot-watching-card", "aria-label": "Bot is watching" }, [
      el("div", { class: "section-title-row" }, [
        el("h2", { class: "card-title", text: "Bot is watching" }),
        el("span", { class: "card-hint", text: "setup focus" }),
      ]),
      el("div", { class: "plan-reason", text: model.reason }),
    ]);
  }
  const hint = model.primary
    ? (model.primarySource === "selected" ? "engine-selected setup" : "the only watched setup")
    : "no single setup selected";
  const children = [
    el("div", { class: "section-title-row" }, [
      el("h2", { class: "card-title", text: "Bot is watching" }),
      el("span", { class: "card-hint", text: hint }),
    ]),
  ];
  if (!model.scenarioAvailable) {
    children.push(el("div", {
      class: "chart-note",
      text: backendNoticeText(model.scenarioReason, "Setup audit details unavailable from the backend."),
    }));
  }
  children.push(model.primary ? primaryBlock(dashboard, model) : honestStateBlock(model));
  children.push(otherSetupsBlock(model));
  children.push(technicalDisclosure(dashboard, model));
  return el("section", { class: "card terminal-card bot-watching-card", "aria-label": "Bot is watching" }, children);
}
