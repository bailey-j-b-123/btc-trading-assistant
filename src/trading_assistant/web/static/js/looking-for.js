/** Read-only projection of the backend's one active setup. No strategy logic. */
import { familyLabel } from "./format.js";
import { translateRule } from "./plain.js";
import { el } from "./util.js";

const EVENTS = {
  breakout: "A breakout was recorded",
  failed_breakout: "A failed breakout was recorded",
  sweep: "A liquidity sweep was recorded",
  retest: "A retest was recorded",
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

export function lookingForViewModel(dashboard) {
  const fact = dashboard?.looking_for;
  if (fact?.available !== true) return {
    title: "No single active scenario",
    observed: fact?.reason || "Setup information unavailable.",
    wanted: "Unknown — wait for a deterministic setup.",
    invalidation: "Unknown",
    trade: "NO — no proposal from this scenario",
    timeframe: dashboard?.meta?.timeframe || "Unknown",
    reference: null,
  };
  const band = scenarioBand(fact, fact.timeframe);
  const pending = Array.isArray(fact.pending_required) ? fact.pending_required : [];
  const wanted = pending.length
    ? pending.map((rule) => translateRule({ rule_id: rule.rule_id, reason: rule.reason,
      outcome: "pending", required: true }, { direction: fact.direction }).sentence).join(" ")
    : "No required rule currently pending; check qualification and hierarchy.";
  const event = EVENTS[fact.seed_event?.kind] || "Seed event details unavailable";
  return {
    title: `${familyLabel(fact.family)} · ${fact.direction === "bullish" ? "LONG" : fact.direction === "bearish" ? "SHORT" : "direction unknown"}`,
    observed: `${event}. ${band ? `Watching reference $${band.low.toLocaleString("en-US")}–$${band.high.toLocaleString("en-US")}.` : "Reference level unavailable."}`,
    wanted,
    invalidation: fact.invalidation != null && Number.isFinite(Number(fact.invalidation))
      ? `Planned invalidation: $${fact.invalidation}` : "Price invalidation level unavailable; consult setup lifecycle evidence.",
    // A deterministic proposal is not an executed position or a trading order.
    trade: dashboard?.qualification?.state === "QUALIFIED" && dashboard?.planning?.state === "PLANNABLE" &&
      dashboard?.plan?.state === "PLANNABLE" && dashboard.plan.setup_id === fact.setup_id
      ? "Plan available — not an order; check engine hierarchy" : "NO — watching / no plannable proposal",
    timeframe: fact.timeframe,
    reference: band,
  };
}

export function lookingForCard(dashboard) {
  const model = lookingForViewModel(dashboard);
  const hierarchyWait = dashboard?.multi_timeframe?.available === true
    ? dashboard.multi_timeframe.waiting_for_text || "No additional hierarchy wait reported."
    : "Hierarchy confirmation unavailable.";
  return el("section", { class: "looking-for", "aria-label": "Looking for" }, [
    el("div", { class: "verdict-kicker", text: `LOOKING FOR · SETUP SNAPSHOT ${model.timeframe} · chart view independent · Scenario — not prediction` }),
    el("strong", { text: model.title }),
    el("div", { text: model.observed }),
    el("div", {}, [el("b", { text: "WANTED: " }), model.wanted]),
    el("div", {}, [el("b", { text: "HIERARCHY: " }), hierarchyWait]),
    el("div", {}, [el("b", { text: "INVALIDATED IF: " }), model.invalidation]),
    el("div", {}, [el("b", { text: "TRADE NOW: " }), model.trade]),
  ]);
}
