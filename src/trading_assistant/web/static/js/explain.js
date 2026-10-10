/**
 * Plain-English explanations for one chart evidence item (pure, unit-tested).
 *
 * Every sentence is assembled from fields the backend returned for that item:
 * exact prices, candle times, known_at instants, states, and reference bands.
 * Where a field is absent the explanation says so instead of guessing. The
 * "did it influence the assessment" answer is derived from the selected setup's
 * own rule evidence (source_reference / seed / reference ids), never inferred.
 */

import { displayOrUnknown, displayRounded, formatUtc, isMissing } from "./format.js";

const KIND_TITLES = {
  swing: "Swing point",
  pattern: "Chart pattern",
  breakout: "Breakout",
  failed_breakout: "Failed breakout",
  sweep: "Liquidity sweep",
  retest: "Retest",
  equal_level: "Equal highs / lows",
  zone: "Support / resistance zone",
  range: "Active range",
  candle_shape: "Candle shape",
  plan_level: "Paper plan level",
};

const SWING_MEANINGS = {
  HH: "a higher high — higher than the previous confirmed swing high",
  LH: "a lower high — lower than the previous confirmed swing high",
  HL: "a higher low — higher than the previous confirmed swing low",
  LL: "a lower low — lower than the previous confirmed swing low",
  SH: "the first confirmed swing high in this window, so there is nothing earlier to compare with",
  SL: "the first confirmed swing low in this window, so there is nothing earlier to compare with",
  EH: "an equal high — level with the previous confirmed swing high",
  EL: "an equal low — level with the previous confirmed swing low",
};

const PATTERN_STATE_STATUS = {
  formed: ["Formed", "amber", "The geometry is complete, but the neckline has not been closed through yet."],
  confirmed: ["Confirmed", "green", "A candle closed beyond the neckline by more than the neckline tolerance. Confirmation is shown from the next candle open."],
  invalidated: ["Invalidated", "red", "A candle closed beyond the invalidation level by more than the invalidation tolerance. The pattern is no longer tracked as live."],
};

const PATTERN_TEXT = {
  double_top: "Double top — two swing highs of similar height with a swing low between them.",
  double_bottom: "Double bottom — two swing lows of similar depth with a swing high between them.",
  head_and_shoulders: "Head and shoulders — five alternating swings: three swing highs (the middle head is higher than both shoulders, and the shoulders agree within tolerance) and two swing lows that form a horizontal neckline.",
  inverse_head_and_shoulders: "Inverse head and shoulders — five alternating swings: three swing lows (the middle head is lower than both shoulders, and the shoulders agree within tolerance) and two swing highs that form a horizontal neckline.",
};

const SHAPE_TEXT = {
  strong_bullish_body: ["Strong bullish body", "The candle closed above its open and the body fills most of the candle's range."],
  strong_bearish_body: ["Strong bearish body", "The candle closed below its open and the body fills most of the candle's range."],
  lower_wick_rejection: ["Long lower wick", "Price traded well below the body and closed back up, so the lower wick is long relative to the body and range."],
  upper_wick_rejection: ["Long upper wick", "Price traded well above the body and closed back down, so the upper wick is long relative to the body and range."],
  indecision: ["Indecision", "The body is very small compared with the range: opening and closing prices were close together."],
  bullish_engulfing: ["Bullish engulfing (two candles)", "A bullish candle's body fully contains the body of the previous bearish candle and is larger than it."],
  bearish_engulfing: ["Bearish engulfing (two candles)", "A bearish candle's body fully contains the body of the previous bullish candle and is larger than it."],
};

const SHAPE_DISCLAIMER = "Descriptive candle shape only. It is not a BRAIN trading signal, is not an input to setup qualification, planning or outcomes, and does not imply a trade.";

function price(value) {
  return isMissing(value) ? "UNKNOWN" : displayOrUnknown(value);
}

function utc(value) {
  return isMissing(value) ? "UNKNOWN" : formatUtc(value);
}

function timeframeText(timeframe) {
  return isMissing(timeframe) ? "UNKNOWN timeframe" : `${String(timeframe).toUpperCase()} timeframe`;
}

/**
 * Whether the selected setup's own rules or seed reference this evidence id.
 * Returns { used, detail } so the panel can state exactly what matched.
 */
export function influenceFor(itemId, qualification) {
  if (!itemId || !qualification || qualification.available !== true) {
    return { used: false, detail: "The qualification state is unavailable, so influence cannot be established." };
  }
  const snapshotSetups = Array.isArray(qualification.snapshot?.setups) ? qualification.snapshot.setups : [];
  const selectedId = qualification.selected_setup_id || null;
  const setups = selectedId
    ? snapshotSetups.filter((setup) => setup.id === selectedId)
    : [];
  if (!setups.length) {
    return {
      used: false,
      detail: selectedId
        ? "The selected setup is not present in the snapshot, so no link can be shown."
        : "No setup is selected at this decision time, so this evidence did not feed a setup.",
    };
  }
  for (const setup of setups) {
    if (setup.seed_event_id === itemId) {
      return { used: true, detail: `It is the seed event of the selected ${setup.family || "setup"} (${setup.state || "state unknown"}).` };
    }
    if (setup.reference_id === itemId) {
      return { used: true, detail: "It is the structural reference the selected setup is evaluated against." };
    }
    for (const rule of Array.isArray(setup.rules) ? setup.rules : []) {
      const match = (Array.isArray(rule.evidence) ? rule.evidence : []).some((entry) => entry?.source_reference === itemId);
      if (match) {
        return {
          used: true,
          detail: `Rule "${rule.rule_id}" in the selected setup cites it (outcome: ${rule.outcome || "unknown"}).`,
        };
      }
    }
  }
  return {
    used: false,
    detail: "The selected setup does not cite this item in its rules or references.",
  };
}

function result({ kind, title, status, tone, timeframe, what, why, when, state, influence, rows = [], note = null }) {
  return {
    kind,
    kindTitle: KIND_TITLES[kind] || "Evidence",
    title,
    status,
    tone,
    timeframe,
    sections: [
      { heading: "What BRAIN detected", body: what },
      { heading: "Why it was detected", body: why },
      { heading: "When the evidence became available", body: when },
      { heading: "Confirmed or invalidated", body: state },
      { heading: "Did it influence the current assessment?", body: influence.used ? `Yes. ${influence.detail}` : `${influence.unknown ? "Not established" : "No"}. ${influence.detail}` },
    ],
    influenced: influence.used === true,
    rows,
    note,
  };
}

function explainSwing(item, ctx) {
  const side = item.kind === "high" ? "high" : "low";
  const label = item.label || "";
  const meaning = SWING_MEANINGS[label] || "a confirmed swing point";
  return result({
    kind: "swing",
    title: `${label || "Swing"} · swing ${side} at ${price(item.price)}`,
    status: "Confirmed",
    tone: "green",
    timeframe: timeframeText(ctx.timeframe),
    what: `BRAIN recorded a swing ${side} at ${price(item.price)} on the candle that opened at ${utc(item.time)}. The label ${label} means ${meaning}.`,
    why: `A swing ${side} needs ${item.left_window ?? "?"} candle(s) on the left and ${item.right_window ?? "?"} on the right where it is the strict extreme (tie policy: ${item.tie_policy || "unknown"}).`,
    when: `The swing could only be known once the candle at ${utc(item.confirmed_by)} had closed. BRAIN first knew it at ${utc(item.known_at)}. Before that instant it was not structure and is not drawn.`,
    state: "Confirmed: the swing is fixed. A confirmed swing is never rewritten by later candles.",
    influence: {
      used: false,
      detail: "Swings are context for the engine's zones and references. Check the setup rules for the exact references used.",
    },
    rows: [
      { label: "Price", value: price(item.price) },
      { label: "Candle open", value: utc(item.time) },
      { label: "Known at", value: utc(item.known_at) },
    ],
  });
}

function explainPattern(item, ctx) {
  const [status, tone, stateText] = PATTERN_STATE_STATUS[item.state] || ["Unknown", "neutral", "The backend reported an unrecognised state."];
  const components = Array.isArray(item.components) ? item.components : [];
  const pointList = components.map((point) => `${point.kind === "high" ? "high" : "low"} ${price(point.price)} (${utc(point.time)})`).join(" → ");
  const influence = influenceFor(item.id, ctx.qualification);
  const patternInfluence = influence.used ? influence : {
    used: false,
    detail: "Chart patterns are optional evidence in the engine: they never qualify or veto a setup on their own, and the selected setup does not cite this one.",
  };
  return result({
    kind: "pattern",
    title: `${item.label} · ${status.toLowerCase()}`,
    status,
    tone,
    timeframe: timeframeText(ctx.timeframe),
    what: `${PATTERN_TEXT[item.type] || item.label}. Its swing points are ${pointList || "not available"}.`,
    why: `Neckline ${price(item.neckline)}; invalidation level ${price(item.invalidation_level)}. Matching-extreme difference ${item.geometry?.matching_extremes_difference_pct ?? "UNKNOWN"}%, depth ${item.geometry?.depth_pct ?? "UNKNOWN"}% of the neckline, span ${item.geometry?.span_candles ?? "UNKNOWN"} candles.`,
    when: `Formed (all swing points confirmed) at ${utc(item.formed_at)}. This state record became known at ${utc(item.known_at)}${item.confirmation_time ? `; neckline closed through at ${utc(item.confirmation_time)}` : ""}.`,
    state: `${status}: ${stateText}`,
    influence: patternInfluence,
    rows: [
      { label: "Neckline", value: price(item.neckline) },
      { label: "Invalidation", value: price(item.invalidation_level) },
      { label: "Known at", value: utc(item.known_at) },
      { label: "Records in this pattern", value: "Earlier states stay in history; only the latest state is drawn on the chart." },
    ],
  });
}

function explainBreakout(item, ctx) {
  const dir = item.direction === "bullish" ? "upward" : "downward";
  return result({
    kind: "breakout",
    title: `${dir === "upward" ? "Upward" : "Downward"} breakout · ${item.reference_type || "reference"}`,
    status: "Detected",
    tone: item.direction === "bullish" ? "green" : "red",
    timeframe: timeframeText(ctx.timeframe),
    what: `A ${dir} breakout of the ${item.reference_type || "structural"} band ${price(item.band_low)}–${price(item.band_high)} was recorded. The breakout candle closed at ${price(item.breakout_close)} on ${utc(item.time)}.`,
    why: `The close passed beyond the band by ${item.penetration_pct ?? "UNKNOWN"}% and was confirmed by ${item.confirmation_candles ?? "UNKNOWN"} following candle(s) before BRAIN recorded it.`,
    when: `BRAIN first knew about the breakout at ${utc(item.known_at)}.`,
    state: "Detected. A later retest or failed breakout is recorded separately.",
    influence: influenceFor(item.id, ctx.qualification),
    rows: [
      { label: "Band", value: `${price(item.band_low)} – ${price(item.band_high)}` },
      { label: "Breakout close", value: price(item.breakout_close) },
      { label: "Known at", value: utc(item.known_at) },
    ],
  });
}

function explainFailedBreakout(item, ctx) {
  return result({
    kind: "failed_breakout",
    title: "Failed breakout",
    status: "Failed",
    tone: "amber",
    timeframe: timeframeText(ctx.timeframe),
    what: `Price closed back inside the ${item.reference_type || "structural"} band ${price(item.band_low)}–${price(item.band_high)} after an earlier breakout, on ${utc(item.time)}.`,
    why: `The re-entry happened within the configured failure window (${item.elapsed_candles ?? "UNKNOWN"} candle(s) after the breakout); re-entry distance ${price(item.reentry_distance)}.`,
    when: `BRAIN first knew about the failure at ${utc(item.known_at)}.`,
    state: "Failed: the earlier breakout did not hold.",
    influence: influenceFor(item.id, ctx.qualification),
    rows: [{ label: "Known at", value: utc(item.known_at) }],
  });
}

function explainSweep(item, ctx) {
  const dir = item.direction === "above" ? "above" : "below";
  return result({
    kind: "sweep",
    title: `Liquidity sweep ${dir} ${item.reference_type || "reference"}`,
    status: "Recorded",
    tone: "amber",
    timeframe: timeframeText(ctx.timeframe),
    what: `Price traded ${dir} the ${item.reference_type || "structural"} band ${price(item.band_low)}–${price(item.band_high)} to ${price(item.extreme)} on ${utc(item.time)}, then closed back at ${price(item.reclaim_close)}.`,
    why: "A wick through a reference that reclaims it on the same candle is recorded as a sweep of resting orders at that level.",
    when: `BRAIN first knew about the sweep at ${utc(item.known_at)}.`,
    state: "Recorded as a sweep. It is not a confirmation of direction on its own.",
    influence: influenceFor(item.id, ctx.qualification),
    rows: [{ label: "Extreme", value: price(item.extreme) }, { label: "Reclaim close", value: price(item.reclaim_close) }],
  });
}

function explainRetest(item, ctx) {
  const state = item.state === "held" ? "Held" : item.state === "failed" ? "Failed" : "Observed";
  return result({
    kind: "retest",
    title: `Retest · ${state.toLowerCase()}`,
    status: state,
    tone: item.state === "held" ? "green" : item.state === "failed" ? "red" : "neutral",
    timeframe: timeframeText(ctx.timeframe),
    what: `After a ${item.direction === "bullish" ? "bullish" : "bearish"} breakout of ${price(item.band_low)}–${price(item.band_high)}, price came back to the band on ${utc(item.time)}. Its state is "${state.toLowerCase()}".`,
    why: `Distance from the band ${price(item.distance_from_band)}; ${item.elapsed_candles ?? "UNKNOWN"} candle(s) after the breakout.`,
    when: `BRAIN first knew about this retest state at ${utc(item.known_at)}.`,
    state: `${state}: ${state === "Held" ? "the band held on a closed candle" : state === "Failed" ? "the band did not hold" : "the retest is still being observed"}.`,
    influence: influenceFor(item.id, ctx.qualification),
    rows: [{ label: "Known at", value: utc(item.known_at) }],
  });
}

function explainZone(item, ctx) {
  const role = item.role === "support" ? "support" : item.role === "resistance" ? "resistance" : "price zone";
  return result({
    kind: "zone",
    title: `${role[0].toUpperCase()}${role.slice(1)} zone ${price(item.band_low)}–${price(item.band_high)}`,
    status: "Clustered",
    tone: "blue",
    timeframe: timeframeText(ctx.timeframe),
    what: `BRAIN clustered confirmed swing points into a ${role} zone from ${price(item.band_low)} to ${price(item.band_high)} (centre ${price(item.center)}).`,
    why: `${item.touch_count ?? "UNKNOWN"} touch(es) (${item.high_source_count ?? 0} high, ${item.low_source_count ?? 0} low source swings) inside the zone tolerance.`,
    when: `Built from confirmed swings. First observed ${utc(item.first_observed)}; last tested ${utc(item.last_tested)}. Snapshot instant ${utc(item.known_at)}.`,
    state: "Clustered zone: a descriptive level, not an order or a target.",
    influence: { used: false, detail: "Zones are structural context. The selected setup's rules show whether it uses a zone as its reference." },
    rows: [{ label: "Band", value: `${price(item.band_low)} – ${price(item.band_high)}` }],
  });
}

function explainRange(item, ctx) {
  return result({
    kind: "range",
    title: `Active range ${price(item.range_low)}–${price(item.range_high)}`,
    status: "Active",
    tone: "purple",
    timeframe: timeframeText(ctx.timeframe),
    what: `BRAIN detected an active consolidation range from ${price(item.range_low)} to ${price(item.range_high)}, from ${utc(item.start_time)} to the current candle.`,
    why: `${item.touch_count ?? "UNKNOWN"} touch(es) of its boundaries; width ${item.width_pct ?? "UNKNOWN"}% of price.`,
    when: `Evaluated at the snapshot instant ${utc(item.known_at)}.`,
    state: "Active: price has stayed inside the bounds on closed candles so far.",
    influence: influenceFor(item.id, ctx.qualification),
    rows: [{ label: "Range", value: `${price(item.range_low)} – ${price(item.range_high)}` }],
  });
}

function explainEqual(item, ctx) {
  const side = item.type === "equal_high" ? "highs" : "lows";
  return result({
    kind: "equal_level",
    title: `Equal ${side} at ${price(item.level)}`,
    status: "Recorded",
    tone: "amber",
    timeframe: timeframeText(ctx.timeframe),
    what: `${item.member_count ?? "UNKNOWN"} confirmed swing ${side} sit within ${price(item.band_low)}–${price(item.band_high)}.`,
    why: "Equal extremes are a liquidity reference: resting orders often gather just beyond them.",
    when: `First member known at ${utc(item.first_known_at)}; cluster known at ${utc(item.known_at)}.`,
    state: "Recorded cluster. It describes where equal extremes sit, nothing more.",
    influence: influenceFor(item.id, ctx.qualification),
    rows: [{ label: "Level", value: price(item.level) }],
  });
}

function explainCandleShape(item, ctx) {
  const [title, why] = SHAPE_TEXT[item.kind] || [item.kind, "A descriptive candle shape."];
  return result({
    kind: "candle_shape",
    title,
    status: "Descriptive",
    tone: item.direction === "bullish" ? "green" : item.direction === "bearish" ? "red" : "neutral",
    timeframe: timeframeText(ctx.timeframe),
    what: `${title} on the candle that opened at ${utc(item.time)}: open ${price(item.open)}, high ${price(item.high)}, low ${price(item.low)}, close ${price(item.close)}.`,
    why,
    when: `The candle closed at ${utc(item.known_at)}; this shape was known only from that close.`,
    state: SHAPE_DISCLAIMER,
    influence: { used: false, detail: "Candle shapes never enter setup qualification, planning, journaling or outcome evaluation." },
    rows: [
      { label: "Body / range", value: item.body_ratio ?? "UNKNOWN" },
      { label: "Upper wick / range", value: item.upper_wick_ratio ?? "UNKNOWN" },
      { label: "Lower wick / range", value: item.lower_wick_ratio ?? "UNKNOWN" },
    ],
    note: SHAPE_DISCLAIMER,
  });
}

/** Explain a chart evidence item. `item.group` (or `item.kind`) selects the template. */
export function explainEvidence(item, context = {}) {
  if (!item || typeof item !== "object") return null;
  const ctx = {
    timeframe: context.timeframe || null,
    qualification: context.qualification || null,
  };
  // The backend tags every item with evidence_kind. Swings are the exception in
  // spirit only: their own kind is high|low, so they are recognised by that too.
  const kind = item.evidence_kind || (item.kind === "high" || item.kind === "low" ? "swing" : item.kind);
  if (kind === "swing") return explainSwing(item, ctx);
  if (kind === "pattern") return explainPattern(item, ctx);
  if (kind === "breakout") return explainBreakout(item, ctx);
  if (kind === "failed_breakout") return explainFailedBreakout(item, ctx);
  if (kind === "sweep") return explainSweep(item, ctx);
  if (kind === "retest") return explainRetest(item, ctx);
  // Display bands carry a position relative to price; their label must not use the detector's raw role.
  if (kind === "zone") return item.position ? explainZoneBand(item, { timeframe: ctx.timeframe, close: ctx.close ?? null }) : explainZone(item, ctx);
  if (kind === "range") return explainRange(item, ctx);
  if (kind === "equal_level") return explainEqual(item, ctx);
  if (kind === "candle_shape") return explainCandleShape(item, ctx);
  return null;
}

/** Explanation for a deterministic plan level chip (entry/stop/target). */
export function explainPlanLevel({ label, value, plan, timeframe, planState }) {
  const isTarget = /^T\d+$/.test(label);
  return result({
    kind: "plan_level",
    title: `${label} · paper plan level ${price(value)}`,
    status: "Proposed (paper)",
    tone: label === "Stop" ? "red" : label === "Entry" ? "green" : "blue",
    timeframe: timeframeText(timeframe),
    what: `${label} ${price(value)} is part of the engine's ${planState || "deterministic"} paper plan${plan?.direction ? ` (${plan.direction})` : ""}.`,
    why: isTarget
      ? "Targets are recorded by the planner with their own level source and R multiple. They are observation levels, not promises."
      : "Entry, stop and invalidation are derived from the qualified setup's frozen evidence by the planning rules.",
    when: "The plan was calculated at the displayed decision instant. Later candles are not used to change it.",
    state: "Paper observation only: no order is placed and no fill is implied.",
    // A plan level only feeds the assessment when the backend actually produced a plan.
    influence: plan
      ? { used: true, detail: "It is part of the plan the engine calculated for the selected setup." }
      : { used: false, detail: "No plan was supplied for this level, so it is not part of any calculated plan." },
    rows: [{ label, value: price(value) }],
  });
}

// --- Support / resistance zones (display bands from the backend's zone_bands) -------------

function utcText(iso) {
  if (typeof iso !== "string" || !iso) return "unknown";
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return match ? `${match[1]} ${match[2]} UTC` : iso;
}

function zonePositionSentence(band, close) {
  const at = close ? ` the latest close ${price(close)}` : " the latest close";
  if (band.position === "price_inside") return `The latest close${close ? ` ${price(close)}` : ""} is inside this band.`;
  if (band.position === "above_price") return `The band is above${at}, so price would have to rise into it.`;
  if (band.position === "below_price") return `The band is below${at}, so price would have to fall into it.`;
  return "Price position relative to this band is unknown.";
}

/** Explains the detector label when it disagrees with where the band actually sits. */
function detectorLabelNote(band) {
  const raw = Array.isArray(band.raw_roles) ? band.raw_roles.find((role) => role && role !== "at_price") : null;
  if (!raw || band.display_role === "price_inside") {
    return band.display_role === "price_inside" && raw
      ? `The detector's rule labelled this zone "${raw}" because its centre sits on that side of the close. The band itself spans the close, so it is shown as inside.`
      : null;
  }
  const mapped = raw === "support" ? "support" : raw === "resistance" ? "resistance" : null;
  if (mapped && mapped !== band.display_role) {
    return `The detector's centre rule labelled this zone "${raw}", but the band sits ${band.display_role === "support" ? "below" : "above"} the close, so it is shown as ${band.display_role}.`;
  }
  return null;
}

/**
 * Explanation for one support/resistance display band. The position comes from the band edges
 * against the detector's close, so a band that contains price is never called support or resistance.
 * Zones feed the engine's higher-timeframe context as neutral evidence; this explanation does not
 * claim a link to any specific rule (the qualification snapshot holds that link, if any).
 */
export function explainZoneBand(band, { close = band?.viewed_close ?? band?.latest_close ?? null, timeframe = null } = {}) {
  if (!band || typeof band !== "object") return null;
  const tf = band.source_timeframe || timeframe || "";
  const tfLabel = tf ? tf.toUpperCase() : "";
  const kindWord = band.display_role === "support" ? "Support" : band.display_role === "resistance" ? "Resistance" : "Price inside zone";
  // Two decimals for display; the exact backend strings remain on the band and in technical details.
  const bounds = `${displayRounded(band.band_low, 2).display} – ${displayRounded(band.band_high, 2).display}`;
  const swingCount = Array.isArray(band.source_swing_timestamps) ? band.source_swing_timestamps.length : 0;
  const age = Number.isFinite(band.age_candles) ? ` (${band.age_candles} candle${band.age_candles === 1 ? "" : "s"} ago)` : "";
  const touches = band.isolated
    ? `Only ${band.touch_count} swing touch: it has not yet been tested by a second swing.`
    : `${band.touch_count} swing touches (${band.swing_high_count} highs, ${band.swing_low_count} lows).`;
  const merged = band.merged_zone_count > 1
    ? ` Merged from ${band.merged_zone_count} overlapping detector zones for display; touches are summed.`
    : "";
  const faded = band.faded ? " Last tested long ago, so it is drawn faded." : "";
  const basisNote = band.position_changed
    ? ` Its position on the ${tfLabel || "source"} chart was ${band.source_position === "above_price" ? "above" : band.source_position === "below_price" ? "below" : "inside"} the ${tfLabel || "source"} close ${price(band.source_close)}; on this chart it is shown against the viewed close ${price(close)}. The source evidence is kept, and the display follows the viewed chart.`
    : band.position_basis === "source"
      ? " The viewed chart's close was unavailable, so this position is the source timeframe's own and is not verified against this chart."
      : "";
  const note = detectorLabelNote(band);
  return result({
    kind: "zone",
    title: `${kindWord}${tfLabel ? ` · ${tfLabel}` : ""} · ${bounds}`,
    status: band.isolated ? "Single swing" : band.display_role === "price_inside" ? "Price inside" : "Tested",
    tone: band.display_role === "support" ? "green" : band.display_role === "resistance" ? "red" : "amber",
    timeframe: timeframeText(tf),
    what: `${kindWord} band ${bounds} on the ${tfLabel || "viewed"} chart. ${zonePositionSentence(band, close)}${basisNote}`,
    why: `Built by clustering ${swingCount} confirmed swing extreme${swingCount === 1 ? "" : "s"} whose prices sit within the detector's tolerance. The band runs from the lowest to the highest of those swings.`,
    when: `First seen ${utcText(band.first_seen)}. Last tested ${utcText(band.last_tested)}${age}. Source swings are drawn only from their confirmation time onward.`,
    state: `${touches}${merged}${faded}`,
    influence: {
      used: false,
      unknown: true,
      detail: "This band is a display of the detector's zone. The zone is neutral evidence in the higher-timeframe context. It is also a reference that breakout, sweep and retest detection can use, and a candidate structural target when a trade plan is produced. Whether a specific setup used it is recorded in that setup's rules, not on this band.",
    },
    rows: [
      { label: "Bounds", value: bounds },
      { label: "Position", value: band.position === "price_inside" ? "price inside" : band.position === "above_price" ? "above price" : "below price" },
      { label: "Touches", value: String(band.touch_count) },
      { label: "Last tested", value: utcText(band.last_tested) },
      { label: "Source timeframe", value: tfLabel || "unknown" },
    ],
    note: note || "Display only. Showing this band does not change any zone, setup, plan or paper observation.",
  });
}
