/** Read-only compact projection of backend setup and hierarchy facts. */
import { directionLabel, displayPrice } from "./format.js";
import { hierarchyStatusLabel, translateRule } from "./plain.js";
import { el } from "./util.js";

const FAMILY_SUMMARIES = {
  breakout_retest_continuation: "breakout → retest",
  failed_breakout_sweep_reversal: "failed breakout → sweep",
  range_rejection_reversal: "range rejection",
};

const EVENT_SUMMARIES = {
  breakout: "breakout",
  failed_breakout: "failed breakout",
  sweep: "liquidity sweep",
  retest: "retest",
};

export function scenarioBand(lookingFor, viewedTimeframe) {
  if (lookingFor?.available !== true || !lookingFor.timeframe || lookingFor.timeframe !== viewedTimeframe) return null;
  const ref = lookingFor.reference;
  if (!ref || typeof ref !== "object") return null;
  // Validation is for display only; these numbers are NEVER passed to analysis.
  const low = Number(ref.band_low);
  const high = Number(ref.band_high);
  if (ref.band_low == null || ref.band_high == null || !Number.isFinite(low) ||
      !Number.isFinite(high) || low <= 0 || high <= 0 || low > high) return null;
  return { low, high }; // only known band bounds; no future time or price path
}

function setupTitle(fact) {
  const direction = fact.direction === "bullish" || fact.direction === "bearish"
    ? directionLabel(fact.direction)
    : "Unknown direction";
  const summary = FAMILY_SUMMARIES[fact.family] || EVENT_SUMMARIES[fact.seed_event?.kind] || "active setup";
  return `${direction} ${summary}`;
}

function compactRuleSentence(rule, direction) {
  const sentence = translateRule({
    rule_id: rule?.rule_id,
    reason: rule?.reason,
    outcome: "pending",
    required: true,
  }, { direction }).sentence;
  return sentence
    .replace(/^Still needs a /i, "")
    .replace(/^Still needs /i, "")
    .replace(/^Waiting for /i, "")
    .replace(/[.!?]+$/, "");
}

function priceText(value) {
  const display = displayPrice(value).display;
  return display === "UNKNOWN" ? display : `$${display}`;
}

export function lookingForViewModel(dashboard) {
  const fact = dashboard?.looking_for;
  const status = hierarchyStatusLabel(dashboard?.multi_timeframe);
  if (fact?.available !== true) return {
    title: "No single active scenario",
    watching: "No unique setup reference",
    need: "Await a deterministic setup",
    invalidation: "Not specified",
    status,
    reference: null,
  };

  const band = scenarioBand(fact, fact.timeframe);
  const ref = fact.reference;
  const pending = Array.isArray(fact.pending_required) ? fact.pending_required : [];
  return {
    title: setupTitle(fact),
    watching: band ? `${priceText(ref.band_low)}–${priceText(ref.band_high)}` : "Reference unavailable",
    need: pending.length
      ? compactRuleSentence(pending[0], fact.direction)
      : "No pending required check",
    invalidation: fact.invalidation != null && Number.isFinite(Number(fact.invalidation))
      ? priceText(fact.invalidation)
      : (Array.isArray(dashboard?.multi_timeframe?.invalidated_if) &&
          typeof dashboard.multi_timeframe.invalidated_if[0] === "string"
        ? dashboard.multi_timeframe.invalidated_if[0]
        : "Not specified"),
    status,
    reference: band,
  };
}

function technicalRows(dashboard) {
  const rows = [];
  const fact = dashboard?.looking_for;
  if (fact && typeof fact === "object") {
    rows.push(`available: ${String(fact.available === true)}`);
    if (fact.reason) rows.push(`reason: ${fact.reason}`);
    for (const key of ["setup_id", "timeframe", "family", "direction", "state", "invalidation"]) {
      if (fact[key] !== undefined && fact[key] !== null) rows.push(`${key}: ${fact[key]}`);
    }
    if (fact.seed_event) {
      rows.push(`seed event: ${fact.seed_event.kind || "unknown"} · known at ${fact.seed_event.known_at || "unknown"}`);
    }
    if (fact.reference && typeof fact.reference === "object") {
      rows.push(`reference: ${fact.reference.type || "unknown"} · low ${fact.reference.band_low ?? "unknown"} · high ${fact.reference.band_high ?? "unknown"}`);
    }
    for (const pending of Array.isArray(fact.pending_required) ? fact.pending_required : []) {
      rows.push(`pending required ${pending?.rule_id || "rule"}: ${pending?.reason || "reason unavailable"}`);
    }
  }

  const hierarchy = dashboard?.multi_timeframe;
  if (hierarchy && typeof hierarchy === "object") {
    for (const key of ["decision", "status", "waiting_for_text", "invalidated_if_text"]) {
      if (hierarchy[key] !== undefined && hierarchy[key] !== null) rows.push(`hierarchy ${key}: ${hierarchy[key]}`);
    }
  }
  return rows;
}

export function lookingForCard(dashboard) {
  const model = lookingForViewModel(dashboard);
  const technical = technicalRows(dashboard);
  const disclosure = technical.length
    ? el("details", { class: "looking-for-details" }, [
        el("summary", { text: "Technical details" }),
        el("div", { class: "looking-for-technical" }, technical.map((row) =>
          el("div", { class: "chart-note mono", text: row })
        )),
      ])
    : null;

  const row = (label, value, className = "") => el("div", { class: `looking-for-row ${className}`.trim() }, [
    el("b", { text: `${label}:` }),
    el("span", { class: "looking-for-value", text: value }),
  ]);

  return el("section", { class: "looking-for", "aria-label": "Looking for" }, [
    el("div", { class: "looking-for-kicker", text: "LOOKING FOR" }),
    el("strong", { class: "looking-for-title", text: model.title }),
    row("Watching", model.watching),
    row("Need", model.need),
    row("Invalid if", model.invalidation),
    row("Status", model.status, "looking-for-status"),
    el("div", { class: "looking-for-disclaimer", text: "Scenario — not prediction" }),
    disclosure,
  ]);
}
