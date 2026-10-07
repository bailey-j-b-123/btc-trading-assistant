/**
 * Fix #2 regression tests: the "Bot is watching" presentation.
 *
 * Proves the normal dashboard shows ONE primary setup (the backend's own
 * selection, never a frontend pick) plus compact grouped counts of the other
 * candidates, keeps raw setup/rule ids out of the normal view, preserves the
 * full audit material behind the collapsed "Technical details / All setups"
 * disclosure, never upgrades a setup state, and never invents missing data.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  botWatchingCard,
  botWatchingViewModel,
  invalidationStatement,
  liveCandidates,
  MAX_GROUP_ROWS,
  nextRequirement,
  otherSetupGroups,
  pendingRequiredRules,
  primarySetup,
  supportingFacts,
} from "../../src/trading_assistant/web/static/js/bot-watching.js";

// ---------------------------------------------------------------------------
// Minimal DOM harness (the shipped modules render through el()/textContent)
// ---------------------------------------------------------------------------

class MockNode {
  constructor(name, textNode = false) {
    this.tagName = textNode ? "#TEXT" : String(name).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = "";
    this.value = "";
    this.open = false;
    this._text = "";
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent || "").join("");
  }

  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }

  append(...nodes) {
    for (const child of nodes) {
      if (child === null || child === undefined) continue;
      const node = child instanceof MockNode ? child : new MockNode("#TEXT", true);
      if (!(child instanceof MockNode)) node._text = String(child);
      node.parentNode = this;
      this.children.push(node);
    }
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
    if (name === "open") this.open = true;
  }

  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  remove() { this.parentNode?.removeChild?.(this); }
}

function findNodes(node, predicate, found = []) {
  if (predicate(node)) found.push(node);
  for (const child of node.children || []) findNodes(child, predicate, found);
  return found;
}

/** Card text with every <details> disclosure excluded = the "normal view". */
function normalViewText(node) {
  if (node.tagName === "DETAILS") return "";
  const own = node.tagName === "#TEXT" ? node.textContent : node._text || "";
  return own + (node.children || []).map((child) => normalViewText(child)).join("");
}

function withDom(callback) {
  const key = "document";
  const prior = { present: Object.hasOwn(globalThis, key), value: globalThis[key] };
  globalThis.Node = MockNode;
  globalThis.document = {
    createElement: (name) => new MockNode(name),
    createTextNode: (text) => {
      const node = new MockNode("#TEXT", true);
      node._text = String(text);
      return node;
    },
  };
  try {
    return callback();
  } finally {
    if (prior.present) globalThis[key] = prior.value;
    else delete globalThis[key];
    delete globalThis.Node;
  }
}

// ---------------------------------------------------------------------------
// Fixtures shaped exactly like the real /api/dashboard payload
// ---------------------------------------------------------------------------

const AS_OF = "2026-10-06T12:00:00Z";

function liveSetupEntry({ id, family, direction, state = "WATCH", pending = [], passed = [], invalidation = [] }) {
  return {
    setup_id: id,
    family,
    direction,
    state,
    created_at: "2026-10-06T10:00:00Z",
    age_bars: 2,
    max_bars: 10,
    bars_remaining: 8,
    vetoed: false,
    vetoed_by: [],
    passed_rules: passed,
    failed_rules: [],
    pending_required: pending,
    invalidation_evidence: invalidation,
  };
}

function summaryEntry({ id, family, direction, state = "WATCH", ended = null, terminal = null }) {
  return {
    id,
    family,
    direction,
    state,
    created_at: "2026-10-06T10:00:00Z",
    as_of: AS_OF,
    ended_at: ended,
    terminal_reason: terminal,
  };
}

/** 17 live candidates: 1 engine-selected QUALIFIED + 16 WATCH across 5 groups. */
function manyCandidatesDashboard() {
  const selectedRules = [
    {
      rule_id: "seed_event",
      required: true,
      outcome: "passed",
      reason: "confirmed Breakout; reference=ref-1",
      evidence: [{ status: "supportive", category: "event", timeframe: "1h", reason: "confirmed Breakout; reference=ref-1" }],
    },
    {
      rule_id: "held_retest",
      required: true,
      outcome: "passed",
      reason: "requires Step 4 held retest of the seed breakout",
      evidence: [{ status: "supportive", category: "confirmation", timeframe: "1h", reason: "requires Step 4 held retest of the seed breakout" }],
    },
    {
      rule_id: "structure",
      required: true,
      outcome: "passed",
      reason: "trend=bullish; reason=higher_highs_and_higher_lows",
      evidence: [{ status: "supportive", category: "structure", timeframe: "1h", reason: "trend=bullish; reason=higher_highs_and_higher_lows" }],
    },
    {
      rule_id: "volume",
      required: true,
      outcome: "passed",
      reason: "relative_volume=1.20; minimum=1; source_reason=None",
      evidence: [{ status: "supportive", category: "volume", timeframe: "1h", reason: "relative_volume=1.20; minimum=1; source_reason=None" }],
    },
  ];
  const watchRule = {
    rule_id: "held_retest",
    required: true,
    outcome: "pending",
    reason: "requires Step 4 held retest of the seed breakout",
    evidence: [],
  };

  const others = [
    ...Array.from({ length: 6 }, (_, i) => liveSetupEntry({ id: `watch-br-${i}`, family: "breakout_retest_continuation", direction: "bearish", passed: ["seed_event"], pending: [{ rule: "held_retest", reason: "requires Step 4 held retest of the seed breakout" }] })),
    ...Array.from({ length: 2 }, (_, i) => liveSetupEntry({ id: `watch-bf-${i}`, family: "failed_breakout_sweep_reversal", direction: "bullish", passed: ["seed_event"], pending: [{ rule: "reversal_breakout", reason: "requires later directional Step 4 breakout at a different reference" }] })),
    ...Array.from({ length: 3 }, (_, i) => liveSetupEntry({ id: `watch-rr-${i}`, family: "range_rejection_reversal", direction: "bearish", passed: ["seed_event"], pending: [{ rule: "range_followthrough", reason: "requires a later close inside the frozen range, farther inward" }] })),
    ...Array.from({ length: 3 }, (_, i) => liveSetupEntry({ id: `watch-sf-${i}`, family: "failed_breakout_sweep_reversal", direction: "bearish", passed: ["seed_event"], pending: [{ rule: "reversal_breakout", reason: "requires later directional Step 4 breakout at a different reference" }] })),
    ...Array.from({ length: 2 }, (_, i) => liveSetupEntry({ id: `watch-rb-${i}`, family: "range_rejection_reversal", direction: "bullish", passed: ["seed_event"], pending: [{ rule: "range_followthrough", reason: "requires a later close inside the frozen range, farther inward" }] })),
  ];
  const selectedLive = liveSetupEntry({
    id: "setup-selected-1",
    family: "breakout_retest_continuation",
    direction: "bullish",
    state: "QUALIFIED",
    passed: ["seed_event", "held_retest", "structure", "volume"],
  });

  return {
    looking_for: {
      available: true,
      setup_id: "setup-selected-1",
      timeframe: "1h",
      family: "breakout_retest_continuation",
      direction: "bullish",
      state: "QUALIFIED",
      seed_event: { kind: "breakout", known_at: "2026-10-06T10:00:00Z" },
      reference: { type: "swing_high", band_low: "117", band_high: "118" },
      pending_required: [],
      invalidation: "117",
    },
    qualification: {
      available: true,
      state: "QUALIFIED",
      status: "evaluated",
      reasons: ["at_least_one_candidate_satisfies_all_required_rules_without_veto"],
      selected_setup_id: "setup-selected-1",
      setups: [
        summaryEntry({ id: "setup-selected-1", family: "breakout_retest_continuation", direction: "bullish", state: "QUALIFIED" }),
        ...others.map((entry) => summaryEntry({ id: entry.setup_id, family: entry.family, direction: entry.direction })),
        summaryEntry({ id: "ended-1", family: "failed_breakout_sweep_reversal", direction: "bearish", state: "NO_SETUP", ended: "2026-10-06T11:00:00Z", terminal: "failed_breakout" }),
      ],
      snapshot: {
        setups: [
          { id: "setup-selected-1", family: "breakout_retest_continuation", direction: "bullish", state: "QUALIFIED", seed_event_id: "seed-1", reference_id: "ref-1", created_at: "2026-10-06T10:00:00Z", rules: selectedRules },
          ...others.map((entry) => ({ id: entry.setup_id, family: entry.family, direction: entry.direction, state: "WATCH", created_at: "2026-10-06T10:00:00Z", rules: [watchRule] })),
        ],
      },
    },
    planning: { state: "PLANNABLE", reasons: [], missing_inputs: [], state_detail: null },
    plan: {
      state: "PLANNABLE",
      direction: "bullish",
      family: "breakout_retest_continuation",
      entry: { value: "124" },
      stop: { value: "117" },
      invalidation: { value: "117" },
      risk_per_unit: "7",
      targets: [{ level: { value: "138" }, r_multiple: "2" }],
    },
    multi_timeframe: {
      available: true,
      status: "evaluated",
      decision: "awaiting_confirmation",
      decision_label: "Waiting for lower-timeframe confirmation",
      overall: "WAITING FOR CONFIRMATION",
      counter_trend: false,
      waiting_for: ["15M acceptance of the setup reference level"],
      waiting_for_text: "Waiting for: 15M acceptance of the setup reference level",
      invalidated_if: ["a closed lower-timeframe candle closing below the setup reference level 117–118"],
      invalidated_if_text: "Invalidated if: a closed lower-timeframe candle closing below the setup reference level 117–118",
    },
    scenario: {
      available: true,
      doing_now: "Trend is BULLISH. Qualification state: QUALIFIED.",
      bot_seeing: {
        state: "QUALIFIED",
        status: "evaluated",
        live_count: 17,
        live_setups: [selectedLive, ...others],
      },
      strengthen_bullish: {
        direction: "bullish",
        developing_setups: [{ setup_id: "setup-selected-1", state: "QUALIFIED", pending_required: [] }],
        none_developing: false,
        to_start_a_setup: { breakout_retest_continuation: "a fresh breakout of a structural band, then a retest that holds it" },
      },
      strengthen_bearish: {
        direction: "bearish",
        developing_setups: [
          { setup_id: "watch-br-0", state: "WATCH", pending_required: [{ rule: "held_retest", reason: "requires Step 4 held retest of the seed breakout" }] },
        ],
        none_developing: false,
        to_start_a_setup: {},
      },
      waiting_for: {
        pending: [
          { rule: "held_retest", reason: "requires Step 4 held retest of the seed breakout", setup_ids: others.slice(0, 6).map((entry) => entry.setup_id) },
          { rule: "reversal_breakout", reason: "requires later directional Step 4 breakout at a different reference", setup_ids: ["watch-bf-0", "watch-bf-1", "watch-sf-0", "watch-sf-1", "watch-sf-2"] },
          { rule: "range_followthrough", reason: "requires a later close inside the frozen range, farther inward", setup_ids: ["watch-rr-0", "watch-rr-1", "watch-rr-2", "watch-rb-0", "watch-rb-1"] },
        ],
        note: null,
      },
      invalidate: {
        cases: [
          {
            setup_id: "setup-selected-1",
            state: "QUALIFIED",
            bars_remaining: 8,
            max_bars: 10,
            invalidation_evidence: [{ rule: "no_failed_breakout", outcome: "passed", reason: "catalog must contain no confirmed failure of seed breakout" }],
            vetoed: false,
            vetoed_by: [],
            plan_entry: { value: "124", source_type: "step3_volatility_latest_close" },
            plan_stop: { value: "117", source_type: "planner_stop_buffer_none" },
            plan_invalidation: { value: "117", source_type: "step4_reference_swing_low" },
          },
          ...others.map((entry) => ({
            setup_id: entry.setup_id,
            state: entry.state,
            bars_remaining: 8,
            max_bars: 10,
            invalidation_evidence: [],
            vetoed: false,
            vetoed_by: [],
          })),
        ],
      },
    },
  };
}

// ---------------------------------------------------------------------------
// 1. Many candidates never become many full cards
// ---------------------------------------------------------------------------

test("many candidates render one primary summary plus at most three grouped rows", () => {
  withDom(() => {
    const card = botWatchingCard(manyCandidatesDashboard());
    assert.equal(findNodes(card, (node) => (node.className || "").split(" ").includes("bot-watching-primary")).length, 1);
    // Why stays a small summary: at most three facts.
    assert.ok(supportingFacts(manyCandidatesDashboard().qualification.snapshot.setups[0]).length <= 3);
    assert.ok(findNodes(card, (node) => (node.className || "").split(" ").includes("bw-fact")).length <= 3);
    // Other setups collapse to compact group rows, never per-candidate cards.
    const rows = findNodes(card, (node) => (node.className || "").split(" ").includes("other-setup-row"));
    assert.equal(rows.length, MAX_GROUP_ROWS);
    const more = findNodes(card, (node) => (node.className || "").split(" ").includes("other-groups-more"));
    assert.equal(more.length, 1);
    assert.match(more[0].textContent, /\+ 2 more setup groups/);
    // The audit disclosure exists and is collapsed by default.
    const details = findNodes(card, (node) => node.tagName === "DETAILS")[0];
    assert.ok(details);
    assert.equal(details.open, false);
    assert.match(details.children[0].textContent, /Technical details \/ All setups/);
  });
});

// ---------------------------------------------------------------------------
// 2. The backend-selected setup is the primary
// ---------------------------------------------------------------------------

test("the engine's selected setup id is the primary shown", () => {
  const dashboard = manyCandidatesDashboard();
  const resolution = primarySetup(dashboard);
  assert.equal(resolution.source, "selected");
  assert.equal(resolution.setup.id, "setup-selected-1");
  assert.equal(resolution.setup.state, "QUALIFIED");
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.primary.id, "setup-selected-1");
  assert.equal(model.primarySource, "selected");
  withDom(() => {
    const card = botWatchingCard(dashboard);
    assert.match(card.textContent, /Bullish breakout → retest/);
    assert.match(card.textContent, /Qualified/);
    assert.match(normalViewText(card), /engine-selected setup/);
    // Why facts are translations of the selected setup's own passed rules.
    const facts = findNodes(card, (node) => (node.className || "").split(" ").includes("bw-fact"));
    assert.ok(facts.some((node) => /A breakout was confirmed\./.test(node.textContent)));
    assert.ok(facts.some((node) => /A retest held the breakout level\./.test(node.textContent)));
  });
});

test("with no engine selection, the backend's single-subject looking_for setup is the primary", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.qualification.selected_setup_id = null;
  dashboard.qualification.state = "WATCH";
  // Remove every live candidate except the looking_for subject.
  const keep = dashboard.scenario.bot_seeing.live_setups.slice(0, 1);
  dashboard.scenario.bot_seeing.live_setups = keep;
  dashboard.scenario.bot_seeing.live_count = 1;
  dashboard.qualification.setups = dashboard.qualification.setups.slice(0, 1);
  dashboard.qualification.snapshot.setups = dashboard.qualification.snapshot.setups.slice(0, 1);
  const resolution = primarySetup(dashboard);
  assert.equal(resolution.source, "single-subject");
  assert.equal(resolution.setup.id, "setup-selected-1");
  // pending rules still resolve via looking_for when snapshot rules are absent
  dashboard.qualification.snapshot.setups = [];
  dashboard.looking_for.pending_required = [{ rule_id: "held_retest", reason: "Waiting for a held retest." }];
  const subject = primarySetup(dashboard).setup;
  const pending = pendingRequiredRules(dashboard, subject);
  assert.equal(pending.length, 1);
  assert.equal(pending[0].rule_id, "held_retest");
  assert.match(nextRequirement(dashboard, subject), /Still needs a retest that holds the breakout level\./);
});

// ---------------------------------------------------------------------------
// 3. No unambiguous selection → honest state, never an arbitrary pick
// ---------------------------------------------------------------------------

test("multiple WATCH candidates with no backend selection show an honest state and no primary", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.qualification.selected_setup_id = null;
  dashboard.qualification.state = "WATCH";
  dashboard.looking_for = { available: false, reason: "Several setups are developing; no single chart scenario is selected." };
  // Drop the selected setup's special status: everyone is a WATCH candidate.
  dashboard.scenario.bot_seeing.live_setups.forEach((entry) => { entry.state = "WATCH"; });
  dashboard.scenario.bot_seeing.state = "WATCH";
  dashboard.qualification.setups.forEach((entry) => { entry.state = "WATCH"; });

  const model = botWatchingViewModel(dashboard);
  assert.equal(model.primary, null);
  assert.equal(model.liveCount, 17);
  const resolution = primarySetup(dashboard);
  assert.equal(resolution.setup, null);
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const normal = normalViewText(card);
    assert.match(normal, /17 setups being monitored — no single setup selected\./);
    // No primary facts are shown for any arbitrarily chosen candidate.
    assert.equal(findNodes(card, (node) => (node.className || "").split(" ").includes("bw-why")).length, 0);
    assert.doesNotMatch(normal, /Why/);
    // All 17 candidates are still accounted for in the grouped rows.
    assert.equal(model.otherCount, 17);
    const shown = model.groups.reduce((sum, group) => sum + group.candidates.length, 0);
    const hidden = model.allGroups
      .filter((group) => !model.groups.includes(group))
      .reduce((sum, group) => sum + group.candidates.length, 0);
    assert.equal(shown + hidden, 17);
  });
});

test("a lone WATCH candidate is never promoted without the backend's own resolution", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.qualification.selected_setup_id = null;
  dashboard.qualification.state = "WATCH";
  dashboard.looking_for = { available: false, reason: "Setup information unavailable." };
  dashboard.scenario.bot_seeing.live_setups = dashboard.scenario.bot_seeing.live_setups.slice(0, 1);
  dashboard.scenario.bot_seeing.live_count = 1;
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.primary, null);
  withDom(() => {
    const card = botWatchingCard(dashboard);
    assert.match(normalViewText(card), /1 setup being monitored — no single setup selected\./);
    assert.match(normalViewText(card), /1 other live candidate/);
  });
});

test("a backend-named setup with missing data is never substituted by another candidate", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.qualification.setups = dashboard.qualification.setups.filter((entry) => entry.id !== "setup-selected-1");
  dashboard.qualification.snapshot.setups = [];
  const resolution = primarySetup(dashboard);
  assert.equal(resolution.setup, null);
  assert.equal(resolution.note, "selected-data-missing");
  withDom(() => {
    const card = botWatchingCard(dashboard);
    assert.match(
      normalViewText(card),
      /The backend named a setup whose details are unavailable — no substitute setup is shown\./,
    );
  });
});

// ---------------------------------------------------------------------------
// 4. Other-setup counts/grouping exactly reflect the backend candidates
// ---------------------------------------------------------------------------

test("group rows and counts mirror the backend candidate collection exactly", () => {
  const dashboard = manyCandidatesDashboard();
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.liveCount, 17);
  assert.equal(model.otherCount, 16);
  // Exact accounting: primary + every group count === live count.
  const grouped = model.allGroups.reduce((sum, group) => sum + group.candidates.length, 0);
  assert.equal(grouped + 1, 17);
  // Grouping is by the backend's own family + direction values.
  const byLabel = new Map(model.allGroups.map((group) => [
    `${group.direction}|${group.family}`,
    group.candidates.length,
  ]));
  assert.equal(byLabel.get("bearish|breakout_retest_continuation"), 6);
  assert.equal(byLabel.get("bullish|failed_breakout_sweep_reversal"), 2);
  assert.equal(byLabel.get("bearish|range_rejection_reversal"), 3);
  assert.equal(byLabel.get("bearish|failed_breakout_sweep_reversal"), 3);
  assert.equal(byLabel.get("bullish|range_rejection_reversal"), 2);
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const rows = findNodes(card, (node) => (node.className || "").split(" ").includes("other-setup-row"));
    assert.deepEqual(rows.map((row) => row.textContent), [
      "6bearish breakout → retest",
      "3bearish range rejection",
      "3bearish failed breakout → sweep reversal",
    ]);
    assert.match(normalViewText(card), /16 other live candidates/);
  });
});

test("groups containing QUALIFIED candidates are labelled, others are not upgraded", () => {
  const candidates = [
    { setup_id: "a", family: "failed_breakout_sweep_reversal", direction: "bullish", state: "WATCH" },
    { setup_id: "b", family: "failed_breakout_sweep_reversal", direction: "bullish", state: "QUALIFIED" },
    { setup_id: "c", family: "range_rejection_reversal", direction: "bearish", state: "WATCH" },
  ];
  const groups = otherSetupGroups(candidates);
  assert.equal(groups.length, 2);
  const sweepGroup = groups.find((group) => group.family === "failed_breakout_sweep_reversal");
  assert.equal(sweepGroup.candidates.length, 2);
  withDom(() => {
    const card = botWatchingCard({
      qualification: { available: true, state: "QUALIFIED", setups: [], selected_setup_id: "x", snapshot: { setups: [] } },
      scenario: {
        available: true,
        bot_seeing: { state: "QUALIFIED", status: "evaluated", live_count: 3, live_setups: candidates },
        waiting_for: { pending: [], note: null },
        invalidate: { cases: [] },
        strengthen_bullish: { direction: "bullish", developing_setups: [], none_developing: true, to_start_a_setup: {} },
        strengthen_bearish: { direction: "bearish", developing_setups: [], none_developing: true, to_start_a_setup: {} },
      },
    });
    const rows = findNodes(card, (node) => (node.className || "").split(" ").includes("other-setup-row"));
    const sweepRow = rows.find((row) => /failed breakout/.test(row.textContent));
    assert.match(sweepRow.textContent, /1 qualified/);
    const rangeRow = rows.find((row) => /range rejection/.test(row.textContent));
    assert.doesNotMatch(rangeRow.textContent, /qualified/i);
  });
});

// ---------------------------------------------------------------------------
// 5. Grouping never mutates backend data
// ---------------------------------------------------------------------------

test("projection, grouping and rendering never mutate the backend payload", () => {
  const dashboard = manyCandidatesDashboard();
  const before = structuredClone(dashboard);
  const model = botWatchingViewModel(dashboard);
  otherSetupGroups(liveCandidates(dashboard));
  withDom(() => { botWatchingCard(dashboard); });
  assert.deepEqual(dashboard, before);
  assert.deepEqual(model.allGroups[0].candidates[0], dashboard.scenario.bot_seeing.live_setups.find((entry) => entry.setup_id === model.allGroups[0].candidates[0].setup_id));
});

// ---------------------------------------------------------------------------
// 6. Raw setup ids / rule ids stay out of the normal view
// ---------------------------------------------------------------------------

test("raw setup ids and rule ids appear only inside the collapsed disclosure", () => {
  withDom(() => {
    const dashboard = manyCandidatesDashboard();
    const card = botWatchingCard(dashboard);
    const normal = normalViewText(card);
    const details = findNodes(card, (node) => node.tagName === "DETAILS")[0];
    // No candidate id, no rule id, no seed timestamp on the normal screen.
    for (const id of ["setup-selected-1", "watch-br-0", "watch-bf-1", "watch-rr-2", "watch-sf-0", "watch-rb-1"]) {
      assert.ok(!normal.includes(id), `${id} must not appear in the normal view`);
      assert.ok(details.textContent.includes(id), `${id} must remain in the disclosure`);
    }
    for (const rule of ["seed_event", "held_retest", "reversal_breakout", "range_followthrough"]) {
      assert.ok(!normal.includes(rule), `${rule} must not appear in the normal view`);
      assert.ok(details.textContent.includes(rule), `${rule} must remain in the disclosure`);
    }
    // No raw rule dump pattern ("rule: passed (required) — ...") either; the
    // plain-English Why facts may legitimately use words like "structure".
    assert.doesNotMatch(normal, /[a-z_]+: (passed|failed|pending) \(required/);
    assert.match(details.textContent, /seed_event: passed \(required\) — confirmed Breakout; reference=ref-1/);
    assert.ok(!normal.includes("seeded 2026-10-06 10:00"));
    assert.ok(details.textContent.includes("seeded 2026-10-06 10:00"));
  });
});

// ---------------------------------------------------------------------------
// 7. The disclosure retains the full audit material
// ---------------------------------------------------------------------------

test("the collapsed Technical details / All setups disclosure keeps every audit fact", () => {
  withDom(() => {
    const dashboard = manyCandidatesDashboard();
    const card = botWatchingCard(dashboard);
    const details = findNodes(card, (node) => node.tagName === "DETAILS")[0];
    const text = details.textContent;
    // Every live candidate, by id.
    for (const entry of dashboard.scenario.bot_seeing.live_setups) {
      assert.ok(text.includes(entry.setup_id), `${entry.setup_id} missing from the audit`);
    }
    // Rule dumps, states, directions, families, seed time.
    assert.match(text, /Passed: Seed event, Held retest, Structure, Volume/);
    assert.match(text, /Passed: Seed event/);
    assert.match(text, /Failed: none/);
    assert.match(text, /Still required: Still needs a retest that holds the breakout level\./);
    assert.match(text, /seeded 2026-10-06 10:00 UTC/);
    // Strengthen / wait / invalidate evidence.
    assert.match(text, /BULLISH case/);
    assert.match(text, /BEARISH case/);
    assert.match(text, /starts with: a fresh breakout of a structural band, then a retest that holds it/);
    assert.match(text, /Waiting for \(merged across live setups\)/);
    assert.match(text, /\(6 setup\(s\)\)/);
    assert.match(text, /What would invalidate \(per live setup\)/);
    assert.match(text, /Selected plan — entry 124 \(latest close\) · stop 117 \(no buffer \(equals invalidation\)\) · invalidation 117 \(swing low band\)/);
    // The engine-selected setup's full rule record with raw reasons.
    assert.match(text, /Primary setup — full rule record \(engine-selected\)/);
    assert.match(text, /setup id: setup-selected-1/);
    assert.match(text, /seed event id: seed-1/);
    assert.match(text, /seed event: breakout · known at 2026-10-06T10:00:00Z/);
    assert.match(text, /seed_event: passed \(required\) — confirmed Breakout; reference=ref-1/);
    assert.match(text, /volume: passed \(required\) — relative_volume=1\.20; minimum=1; source_reason=None/);
    // Exact backend strings stay verbatim.
    assert.match(text, /doing_now: Trend is BULLISH\. Qualification state: QUALIFIED\./);
    assert.match(text, /Qualified: 17 developing setups · fully evaluated/);
    // The full hierarchy invalidation wording (the normal-view line clamps).
    assert.match(text, /invalidation \(hierarchy wording\): Invalidated if: a closed lower-timeframe candle closing below the setup reference level 117–118/);
    // Ended (invalidated/expired) candidates remain visible for audit.
    assert.match(text, /Ended candidates/);
    assert.match(text, /setup id: ended-1/);
    assert.match(text, /This setup ended because the breakout failed\./);
    // The old Evidence-card detail (translated for/against columns) survives,
    // with the evidence category still humanised (not the raw enum).
    assert.match(text, /FOR THE SETUP/);
    assert.match(text, /AGAINST \/ STILL MISSING/);
    assert.match(text, /A breakout was confirmed\./);
    assert.match(text, /Seed event · 1h · Seed event/);
    assert.match(text, /Confirmation · 1h · Held retest/);
  });
});

// ---------------------------------------------------------------------------
// 8. No frontend state upgrade
// ---------------------------------------------------------------------------

test("a WATCH primary is labelled Watching and never upgraded", () => {
  const dashboard = manyCandidatesDashboard();
  // One single WATCH setup: the backend resolves it via looking_for only.
  dashboard.qualification.selected_setup_id = null;
  dashboard.qualification.state = "WATCH";
  dashboard.qualification.setups = dashboard.qualification.setups.slice(0, 2);
  dashboard.qualification.setups[0].state = "WATCH";
  dashboard.qualification.snapshot.setups = dashboard.qualification.snapshot.setups.slice(0, 2);
  // A WATCH setup genuinely still has an outstanding required check.
  dashboard.qualification.snapshot.setups[0].rules = [
    {
      rule_id: "seed_event",
      required: true,
      outcome: "passed",
      reason: "confirmed Breakout; reference=ref-1",
      evidence: [{ status: "supportive", category: "event", timeframe: "1h", reason: "confirmed Breakout; reference=ref-1" }],
    },
    {
      rule_id: "held_retest",
      required: true,
      outcome: "pending",
      reason: "requires Step 4 held retest of the seed breakout",
      evidence: [],
    },
  ];
  dashboard.scenario.bot_seeing.state = "WATCH";
  dashboard.scenario.bot_seeing.live_count = 1;
  dashboard.scenario.bot_seeing.live_setups = dashboard.scenario.bot_seeing.live_setups.slice(0, 1);
  dashboard.scenario.bot_seeing.live_setups[0].state = "WATCH";
  dashboard.looking_for = {
    ...dashboard.looking_for,
    state: "WATCH",
    setup_id: "setup-selected-1",
    pending_required: [{ rule_id: "held_retest", reason: "requires Step 4 held retest of the seed breakout" }],
    invalidation: null,
  };
  dashboard.plan = null;
  dashboard.planning = { state: null, reasons: [], missing_inputs: [], state_detail: null };
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.primary.state, "WATCH");
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const chip = findNodes(card, (node) => (node.className || "").split(" ").includes("bot-watching-state"))[0];
    assert.equal(chip.textContent, "Watching");
    const normal = normalViewText(card);
    assert.doesNotMatch(normal, /Qualified|PLANNABLE|Trade ready|PLAN READY|TRADE READY/i);
    // The one pending requirement is the translated backend reason.
    assert.match(normal, /NextStill needs a retest that holds the breakout level\./);
  });
});

test("a QUALIFIED primary with a plan keeps hierarchy-gated wording", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.multi_timeframe.decision_label = "Trade ready";
  dashboard.multi_timeframe.overall = "TRADE READY";
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const chip = findNodes(card, (node) => (node.className || "").split(" ").includes("bot-watching-state"))[0];
    assert.equal(chip.textContent, "Qualified");
    const normal = normalViewText(card);
    // Next is the hierarchy's own waiting label — the plan is NOT trade-ready.
    assert.match(normal, /NextWaiting for lower-timeframe confirmation/);
    assert.doesNotMatch(normal, /Trade ready|TRADE READY|PLAN READY|Plan ready/);
  });
});

test("a QUALIFIED setup without a plannable plan never reads as plan-ready", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.planning = { state: "NO_PLAN", reasons: ["confirmation_event_missing"], missing_inputs: [], state_detail: null };
  dashboard.plan = { state: "NO_PLAN" };
  withDom(() => {
    const card = botWatchingCard(dashboard);
    assert.match(normalViewText(card), /Qualified/);
    assert.match(normalViewText(card), /NextNo outstanding setup checks reported by the backend\./);
    assert.doesNotMatch(normalViewText(card), /PLANNABLE|Trade ready|PLAN READY/);
  });
});

// ---------------------------------------------------------------------------
// 9. Missing data is never invented
// ---------------------------------------------------------------------------

test("unavailable facts render as unavailable, never filled in", () => {
  const dashboard = manyCandidatesDashboard();
  // Primary with no rule detail, no plan, and no hierarchy invalidation facts.
  dashboard.qualification.snapshot.setups = [];
  dashboard.plan = null;
  dashboard.planning = { state: null, reasons: [], missing_inputs: [], state_detail: null };
  dashboard.multi_timeframe = { available: false, error: { message: "hierarchy down" } };
  const setup = primarySetup(dashboard).setup;
  assert.deepEqual(supportingFacts(setup), []);
  assert.equal(nextRequirement(dashboard, setup), "No outstanding setup checks reported by the backend.");
  assert.equal(invalidationStatement(dashboard, setup), "Not specified by the backend.");
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const normal = normalViewText(card);
    assert.match(normal, /Supporting facts unavailable from the backend\./);
    assert.match(normal, /Invalid ifNot specified by the backend\./);
    assert.ok(!normal.includes("✓"));
  });
});

test("invalidation falls back to the plan level only for the selected plannable setup", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.multi_timeframe = { available: true, status: "evaluated", decision: "awaiting_confirmation", counter_trend: false };
  const setup = primarySetup(dashboard).setup;
  assert.equal(invalidationStatement(dashboard, setup), "the deterministic plan invalidation level 117");
  // A WATCH subject (via looking_for) has no plan level to borrow.
  const watch = { ...setup, id: "watch-br-0", selected: false };
  assert.equal(invalidationStatement(dashboard, watch), "Not specified by the backend.");
});

test("unavailable qualification and scenario sections stay honestly unavailable", () => {
  const dashboard = {
    qualification: { available: false },
    scenario: { available: false, reason: "no evaluated snapshot at this boundary" },
  };
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.available, false);
  withDom(() => {
    const card = botWatchingCard(dashboard);
    assert.match(card.textContent, /No completed analysis at this point\./);
    assert.doesNotMatch(card.textContent, /Bot is watchingBearish|Bot is watchingBullish/);
  });
});

test("an unavailable scenario section still shows the backend-selected primary and qualification-based groups", () => {
  const dashboard = manyCandidatesDashboard();
  dashboard.scenario = { available: false, reason: "no evaluated snapshot at this boundary" };
  const model = botWatchingViewModel(dashboard);
  assert.equal(model.primary.id, "setup-selected-1");
  // The live collection falls back to the qualification list's WATCH/QUALIFIED
  // entries (the backend's own live states), minus the selected primary.
  assert.equal(model.liveCount, 17);
  assert.equal(model.otherCount, 16);
  withDom(() => {
    const card = botWatchingCard(dashboard);
    const normal = normalViewText(card);
    assert.match(normal, /No completed analysis at this point\./);
    assert.match(normal, /Bullish breakout → retest/);
    assert.match(normal, /16 other live candidates/);
  });
});

// ---------------------------------------------------------------------------
// Extra: grouping helpers are pure and order-stable
// ---------------------------------------------------------------------------

test("group row order is count-only and never implies trading priority", () => {
  const candidates = [
    { setup_id: "a", family: "range_rejection_reversal", direction: "bearish", state: "WATCH" },
    { setup_id: "b", family: "breakout_retest_continuation", direction: "bearish", state: "WATCH" },
    { setup_id: "c", family: "breakout_retest_continuation", direction: "bearish", state: "WATCH" },
  ];
  const groups = otherSetupGroups(candidates);
  assert.equal(groups[0].candidates.length, 2);
  assert.equal(groups[1].candidates.length, 1);
  assert.deepEqual(
    otherSetupGroups(candidates).map((group) => group.candidates.map((entry) => entry.setup_id)),
    [["b", "c"], ["a"]],
  );
  // Empty and missing inputs stay empty.
  assert.deepEqual(otherSetupGroups([]), []);
  assert.deepEqual(otherSetupGroups(null), []);
});
