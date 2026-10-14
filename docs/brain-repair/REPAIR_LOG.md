# BRAIN integration & repair log — DO NOT MERGE

## Resume here (repairs at `dc6fabe`; this block added by the documentation-only commit `1cb3885`, 2026-10-14)

- Branch `arena/178a68a2-btc-trading-assistant`; repair PR: **#70 `BRAIN INTEGRATION & FIXES — DO NOT
  MERGE`** (draft, never to be merged by this session). Audit feed: PR #68 on
  `origin/arena/d3f9aa87-btc-trading-assistant`, findings 0001-0006.
- Repaired and pushed: BRAIN-AUDIT-0001 .. 0004 (inherited lineage), REPAIR-SR-001,
  REPAIR-CHECK-SR-002, and now **BRAIN-AUDIT-0005 and 0006** — both ready for auditor recheck, with
  evidence files listed in the entry below. Nothing is self-marked VERIFIED.
- Suites on this head: `pytest` 1272 passed / 1 xfailed; `npm test` 485 / 485;
  `support-resistance-check.mjs` exit 0 (3 runs); `zone-reference-check.mjs` exit 0 on the end-state
  and positive-control databases, exit 1 under both negative controls.
- Settings used for the browser work (`.cache` only, disposable): demo databases
  `.cache/brain-repair/sr.sqlite3` (end state), `.cache/brain-repair/sr-linked.sqlite3` (truncated at
  the positive-control instant), servers on :8125 (1h engine, for the S/R switch check), :8126
  (4h engine) and :8127 (4h engine, positive-control DB). Rebuild a database with
  `.cache/verify/make_sr_db.py <path> <days> <anchor> [--until ISO]`.
- Next steps for a successor: (1) re-read PR #68 for new findings and treat them as the work queue —
  do not re-fix 0001-0006; (2) the two "Outstanding" observations below need either an owner
  decision or a separate auditable change; (3) keep every number in this log traceable to a command
  in `evidence/`.

## Guardrails
- Repair branch: `arena/0090b00d-btc-trading-assistant` (session-fixed; no other branches will be modified).
- No merge into main, production deployment, real-money trading, production database changes, or strategy/risk/execution changes without owner approval.
- Agent 1 audit feed: PR #68, `BRAIN AUDIT FEED — VERIFIED FINDINGS`.
- Repairs will be labelled READY FOR AUDITOR RECHECK, not independently verified here.

## Progress — 2026-10-14
- Initial checkout: `9da1d3cf7a8fcc902b734f964a30c95032ee8d0d`; clean worktree.
- Opened tracking work before application changes. PRs #62/#65/#66/#67 and audit #68 located.
- In progress: fetch ancestry, inspect changes/dependencies, establish test baseline, then integrate only compatible changes.
- Tests: not yet run. No application code changed.
- Branch constraint: preserve this session's branch; bring verified #62 ancestry into it rather than creating/switching to a different branch.

### Investigation checkpoint
- Draft repair PR #69 opened immediately; retargeted to #62 after ancestry verification. `gh pr edit` hit an installed CLI/Classic Projects compatibility error; equivalent `gh api PATCH` succeeded.
- #62 base import: `fb80c11` (normal ancestry-preserving merge). Full pinned SHAs/file manifests and overlap analysis: `INTEGRATION.md`.
- `npm test` baseline: **425 passed / 0 failed**. Python baseline still running; no result claimed yet.
- Received and reproduced **BRAIN-AUDIT-0001 (P2)**: #65/#66 dashboard merge conflict. Mechanical combined resolution identified; application integration pending baseline completion.
- Browser tooling installed locally (untracked): puppeteer-core with optional sandbox Chromium 133. npm reports 7 high-severity dependency advisories; not blindly applying breaking audit fixes. Browser harness dependencies are separate from the application.

### Baseline complete
- `.venv/bin/python -m pytest -q`: **1182 passed, 1 xfailed, 1 warning**, 257.96 s. The documented shallow-history expected failure remains untouched. Warning is the installed Starlette/httpx deprecation.
- `.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests`: **15 pre-existing findings**; log retained for comparison.
- Baseline evidence and resolved Python dependencies are in `evidence/`.
- Added a disposable offline browser-server wrapper: accepts only existing databases under ignored `.cache/`, disables HTTP writes and external display-only quote/socket calls, allows sandbox framing without altering production security headers. Application market/evidence APIs are not mocked. Synthetic 600-candle DB seeded under `.cache/brain-repair/`; no genuine market data available.
- Chromium initially failed to launch (missing system NSS/NSPR libraries). Extracted the optional Chromium package's bundled libraries into ignored `.cache/`; rerun uses `LD_LIBRARY_PATH`. No system package or application changes needed.

### BRAIN-AUDIT-0001 — integration repair
- #65 merged at `a51344db23a0ac0691afe0149bf5dde215b9add7`.
- #66 merged with mechanical conflict resolution at **`f15cd1a558a56b1e62db2fd9b6ef6595a2de0ee6`**: retained `drawnModel`, `paintPatternAnnotation`, and the extracted `updateEvidenceMeta` called after both evidence and overlay updates. No entire-file replacement.
- #67 merged cleanly at **`e07e4e6affcff4d742f336e6460e6451bb5a9583`**. All three source heads are ancestors, not cherry-picked duplicates.
- Existing combined `npm test`: **464 passed / 0 failed**. Added a combined refresh/selection regression in `tests/frontend/dashboard.test.mjs`: all three S/R tallies agree after new overlays, pattern selection remains functional, chart instance and trade decision payloads are preserved.
- Targeted regression passes. An isolated copy with the overlay-meta refresh removed fails the new assertion (see `evidence/0001-mutation.txt`); no tracked implementation was mutated for that negative control.
- Baseline and combined Breakouts browser checks: all four sizes pass, zero page/console errors. Further combined Python/browser checks running.
- Status: **READY FOR AUDITOR RECHECK** of implementation; final regression matrix still in progress.

### Incoming queue
- **BRAIN-AUDIT-0002 (P2)**: investigate existing PR #53 liveness repair; do not duplicate it.
- **BRAIN-AUDIT-0003 (P2)**: residual wrong-timeframe swing fallback in #67, reproduced by audit probe; pending repair.
- **BRAIN-AUDIT-0004 (P2)**: retest lifecycle outcome attached to observation candle; pending read-only UI repair/tests.
- Sent new non-engine `zone_bands` omission observation to Agent 1 for independent confirmation/ID; not yet labelled verified.

### BRAIN-AUDIT-0003 — wrong-timeframe swing fallback
- Reproduced **5/5 new regressions failing** before repair: model invalid/unavailable evidence plus actual 15M/4H failed and empty candle responses with a bullish 1H engine.
- Root causes: unconditional engine-trend fallback, and failure/empty paths refreshing only the Pattern panel, leaving the Swing panel stale.
- Repair: validate trend directions, require available evidence and explicit matching timeframe for legacy fallback; caller supplies engine identity only on the engine view. Unavailable candle paths refresh every function panel. Source records and trading decisions unchanged.
- The prior #67 fallback test lacked timeframe identity. It now supplies and explicitly requires same-frame identity, alongside the new adverse controls; assertions were not removed or weakened.
- Changed files: `src/trading_assistant/web/static/js/function_panel.js`, `src/trading_assistant/web/static/js/views/dashboard.js`, `tests/frontend/function-panel.test.mjs`, `tests/frontend/timeframeswitch.test.mjs`, this log and `evidence/0003-{before,after}.txt`.
- `node --test tests/frontend/function-panel.test.mjs tests/frontend/timeframeswitch.test.mjs tests/frontend/dashboard.test.mjs`: **87 passed / 0 failed** (15.77 s).
- Status: **READY FOR AUDITOR RECHECK**. Exact repair SHA recorded in the subsequent GitHub handoff/comment.

### Browser checkpoint
- Combined `breakouts-check.mjs` and `patterns-check.mjs`: exit 0.
- `mobile-layout-check.mjs` and `desktop-bottom-check.mjs`: exit 1, uncaught `Incorrect locale information provided`. Initial mobile geometry had zero overflow/issues. Investigating the error rather than suppressing assertions; not claiming these suites pass.

### BRAIN-AUDIT-0004 — retest outcome timestamp attribution
- Reproduced 4 new failing cases: delayed Held/Failed and same-candle observation/outcome controls.
- Repair: retain existing status precedence but choose the actual matching outcome record for candle anchor, jump index, price and distance/elapsed metadata. Explicitly expose first observation and outcome known-at times. Describe stored lifecycle records rather than independent tests.
- `node --test tests/frontend/breakout-stages.test.mjs tests/frontend/function-panel.test.mjs`: **47 passed / 0 failed**. Each new case also checks pre-confirmation stays pending despite later candle prices and source evidence remains unmodified.
- Re-ran Agent 1's actual-detector synthetic probe: Held now anchors at **07:00**, known **08:00**, while metadata separately states observation candle **06:00**, known **07:00** (see `evidence/0004-detector-after.json`).
- Files: `src/trading_assistant/web/static/js/breakout_stages.js`, `tests/frontend/breakout-stages.test.mjs`, this log and `evidence/0004-*`.
- No detector, threshold, event contract, backend causality or stored record changes. **READY FOR AUDITOR RECHECK**.

### Integrated Python regression complete
- At combined application head `e07e4e6affcff4d742f336e6460e6451bb5a9583`: `.venv/bin/python -m pytest -q` → **1236 passed, 1 xfailed, 1 warning**, 280.21 s. Includes all decision-digest, causal detector, zone/evidence, API, persistence and paper-ledger suites. Subsequent 0003/0004 application changes are JavaScript-only.
- BRAIN-AUDIT-0003 repair commit: **`ccde86fa955e07e884b2edf11da06e2b7763a2a3`**.
- Audit checkpoint: `4193b09` adds independent baseline/per-feature evidence, no new defect ID yet. Auditor's browser probe also pins an explicit en-US locale; rerunning responsive tests with that environment rather than changing/weakening assertions.

### BRAIN-AUDIT-0002 — reuse existing runner-health repair
- Confirmed all adverse no-verdict probe inputs were green before repair; no duplicate fix written.
- Reviewed and merged PR #53 head **`cd0df0499a2d44dd8f0af397d2d9f05c3e17cca6`** at **`04b076db4d3fa3a73ea4848ea397454b3a57c58e`**, conflict-free. Its monitoring tolerance is a read-only liveness policy, not a trading risk/execution threshold. No production setting changed.
- Inherited tests cover stale/missing/future/healthy control, signed fractional-future age, bounded read-only heartbeat query, configured monitoring tolerance, and unchanged stored data. Existing fixtures updated by the original PR to carry the new contract; no assertions weakened here.
- Frontend runner-heartbeat/topbar/dashboard/timeframeswitch: **72 passed**. Python `tests/test_runner_heartbeat_health.py`: **18 passed, 1 warning**.
- Original audit probe without the new verdict now returns amber/unconfirmed for all cases, including its recent *legacy-contract* case. Valid recent new-contract control remains healthy in regression tests.
- **READY FOR AUDITOR RECHECK**. Fourteen imported paths are listed by `git diff 98b7e19 04b076d --name-only` and in `INTEGRATION.md`.

### REPAIR-SR-001 — non-engine bands omitted (audit ID requested)
- Repairer-discovered and reproduced; **not independently verified**. Sent to #68 before implementation for permanent audit-ID assignment.
- `support-resistance-check.mjs` on the isolated 60-day multi-timeframe synthetic database failed when switching 1H → 4H: no labels naming 4H.
- Root cause: `overlaysForViewedTimeframe` dropped the backend `zone_bands` block; the renderer reads that block rather than deriving client-side bands.
- Minimal passthrough only after existing exact timeframe validation. Bounds, counts, linkage and source labels remain the backend's own; no zone calculation or strategy change.
- Added failing-before/passing-after regression for exact object identity, wrong-frame rejection and delayed structure arrival updating all S/R surfaces. Entire timeframe suite: **27 passed**.
- Files: `src/trading_assistant/web/static/js/views/dashboard.js`, `tests/frontend/timeframeswitch.test.mjs`, this log and `evidence/sr-*`.
- Browser recheck and final full frontend/Python suites running; not yet claimed complete.

### Responsive browser result correction
- Locale-controlled reruns of mobile (390/768), desktop bottom (1440/1920) and Breakouts (four sizes) all exit 0. No assertion was modified. Browser locale is configured via `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`; the host shell warns that it lacks this locale, but Chromium accepts it. Earlier invalid-locale failures remain recorded, not hidden.
- BRAIN-AUDIT-0004 repair commit: **`98b7e190ebde1a0746cc6d10d33bfb8b2a4aa775`**.

## Session `arena/178a68a2` — continuation (2026-10-14)

Session-fixed branch for this session; repair PR #70 (draft, DO NOT MERGE) tracks it. Work resumes
from the already-integrated tip `09f5ee2113b3cf35015e4d0058f45fa212cbe529` (PR #69) rather than
re-integrating #62/#65/#66/#67 or rewriting the four repairs that already exist there: no feature
branch, no audit branch and no `main` was touched, and no commit was duplicated.

### Independent verification of the inherited state
- Ancestry: #62 `a0f1faf`, #65 `ee6ab77`, #66 `a5c9013`, #67 `e223772` and the #53 dependency
  `cd0df04` are all ancestors of this branch's tip (`git merge-base --is-ancestor`), so nothing was
  cherry-picked or re-created, and no conflict markers remain in tracked source.
- Agent 1's own probes re-run against this branch: BRAIN-AUDIT-0002 fails closed (amber/unconfirmed
  for stale, missing, future and no-verdict input); BRAIN-AUDIT-0003 shows the viewed 15m bias
  (Bearish) and UNKNOWN when 15m evidence is unavailable; BRAIN-AUDIT-0004 anchors Held to the
  outcome candle 07:00 / known 08:00 while exposing the first observation 06:00 / known 07:00
  separately.
- **Decision invariance** — `tests/fixtures/decision_digests/dashboard_n500.json` was regenerated
  during integration, so a matching digest could not show whether trade decisions changed. Both
  revisions were instead dumped field by field (read-only worktree at #62 `a0f1faf` vs this head):
  at boundaries 250/375/400/500 the replay frames, enumerated qualification snapshots and trade
  plans are **identical — 0 added, 0 removed, 0 changed paths**. The dashboard payload has 0 removed
  paths; the only changed value per boundary is one presentation string
  (`.overlays.zone_bands.rules.merge`); every other difference is additive zone-evidence fields plus
  `.meta.supported_timeframes[0..4]`. Evidence: `evidence/decision-invariance-178a68a2.txt`.
- Suites on this head: frontend **481/481**; Python **1254 passed, 1 xfailed, 1 warning** (300.74 s,
  the xfail being the documented shallow-history defect, marker untouched); browser suites
  breakouts (4 sizes), patterns (5 sizes), mobile (390/768) and desktop bottom (1440/1920) all exit
  0 against synthetic demo databases. Evidence: `evidence/session-178a68a2-suites.txt`.

### REPAIR-CHECK-SR-002 — the S/R browser check could not verify a timeframe switch
- Repairer-discovered and reproduced; **not independently verified**. Audit ID requested from
  Agent 1 on #68 (same route as REPAIR-SR-001).
- Reproduced: `support-resistance-check.mjs … --timeframe 1h --switch 4h` exits 1 at all four
  viewports on the integrated head — but the application is correct. A probe drove the real
  dashboard: within ~0.9 s of the click the chart paints three 4H bands labelled `R 4H · 3×`,
  `IN 4H · 11×`, `S 4H · 7×` with the status line reporting 3 shown / 2 not drawn, while the API
  stores those same 4H zones. Root cause is in the check, in three parts:
  1. the post-switch read waited a fixed 900 ms after a control state that flips instantly, so it
     read the previous timeframe's band once (stale `IN 1H · 37×`);
  2. the band comparison ignored pane-edge clipping — the very case the check's own default-view
     geometry assertion documents and allows — so any switched band reaching a pane edge failed;
  3. the comparison passed **vacuously** when the switched view painted nothing, so the original
     SR-001 defect (a non-engine timeframe dropping the backend's bands) was not detectable.
- Repair, `scripts/browser-audit/support-resistance-check.mjs` only — **no application code changed**:
  a deterministic wait for the app's own completion facts (the evidence line names the switched
  timeframe's candle window AND the band layer holds exactly the bands the status line reports),
  clipping-aware matching that mirrors the documented default-view rule, and a non-vacuous
  requirement that the switched timeframe's stored bands are actually painted.
- Negative control: removing the `zone_bands` passthrough from `views/dashboard.js` in the working
  tree only (never committed; restored with `git checkout`) makes the repaired check fail at all
  four viewports naming the missing bands. The same mutation passed vacuously before the repair.
- After the repair the check exits 0 in three consecutive runs (32 s each). Evidence:
  `evidence/sr-check-before.txt`, `evidence/sr-check-after.txt`, `evidence/sr-check-mutation.txt`,
  `evidence/sr-switch-probe.txt`.
- Status: **READY FOR AUDITOR RECHECK**.

### BRAIN-AUDIT-0005 (P2) — retests could never be linked to a zone band

- Read the full finding on PR #68 (`docs/brain-audit/findings/BRAIN-AUDIT-0005.md`) and reproduced it
  on the repaired lineage before touching code: the probe showed `retest projected= 30 carrying
  reference_type= 0` against `retest n= 30 {'swing_high': 16, 'zone': 10, 'range_high': 4}` — ten
  retests really held a `zone` reference and none of them reached the linkage.
- Root cause: `src/trading_assistant/web/chart_evidence.py::_event_item` sets `reference_type`/
  `reference_id` on the breakout, failed-breakout and sweep branches but not on the retest branch,
  so every retest arrived at `link_events_to_bands` with no reference and was filtered out.
- Repair (acceptance criterion 1): the retest branch now carries the reference of the breakout the
  retest follows (`reference_type`, `reference_id`), exactly as the other branches carry theirs. No
  detector, threshold, identity, plan, journal or ledger change; the fields are additive and
  read-only.
- Regression tests (acceptance criterion 2): `tests/test_zone_evidence.py` now projects a **real**
  `Retest` (real `Reference`/`Breakout` objects, not hand-made dicts) through
  `chart_evidence.project_event` and asserts the linkage counts it under `counts.retest` with
  `reference_ids == ["ref-zone"]`; four separate cases use `swing_high`/`swing_low`/`range_high`/
  `range_low` references and must stay unlinked. New `tests/test_zone_linkage_end_to_end.py` drives
  the real detectors over stored candles (the S&R demo generator, anchored at a fixed date) and
  asserts a retest's counted reference equals its own breakout's reference bounds.
- Post-repair reproduction (acceptance criterion 3, with 0006's rule settled): the auditor's own
  probe now reads `retest projected= 30 carrying reference_type= 30`, and the ten zone-referenced
  retests are counted on the bands as overlaps (0006) — see the end state below.
- Negative control: reverting only the retest projection fails three tests
  (`test_a_real_retest_projection_carries_the_reference_of_the_breakout_it_follows`,
  `test_a_zone_referenced_retest_is_linked_and_counted_as_a_retest`, and the end-to-end retest test).
- Status: **READY FOR AUDITOR RECHECK**.

### BRAIN-AUDIT-0006 (P2) — a referenced band could state an absolute negative

- Read the full finding and reproduced the end state on the repaired lineage: all five presented 4h
  bands reported `linked=False count=0` while the same payload carried ten zone-referenced events
  overlapping them (e.g. `85958.6 – 86543.0`, `86076.2 – 86543.0`, `86543.0 – 86543.0` against the
  displayed `85958.6 – 87052.5`). Cause: the identity rule is exact decimal-bounds equality between
  the event's reference and the band (or a member zone), while zone bounds are re-derived every
  analysis frame and an event keeps the bounds it was detected with — so a historical reference to
  the same level almost never links.
- Repair (acceptance criterion 1): the identity rule is unchanged and now stated explicitly in
  `zone_evidence`'s docstring. Alongside it the linkage reports what the stored evidence actually
  establishes — `overlapping`, `overlapping_count`, `overlapping_counts`, `overlapping_events`
  (each with `overlap_relation` in `contained_by` / `contains` / `overlaps`) and `overlap_note`.
  A band is only ever reported as having no stored reference when nothing overlaps it and nothing is
  identical to it. The operator-facing wording changed on four surfaces: the band explanation
  (`explain.js::linkedEventSentence`, `linkedRows`), the Support & Resistance panel row
  (`function_panel.js::linkedEventText`), the nearby-zones card row
  (`views/dashboard.js::zoneRow`), and the payload's own `overlap_note`. Every overlap sentence
  says the reference is NOT counted as the same level.
- Regression tests (acceptance criterion 2): `tests/test_zone_evidence.py` (30 tests) covers shifted
  bounds, containment both ways, partial overlap, non-overlap, identical bounds, merged bands
  measured through their member zones, and per-kind counts; `tests/test_zone_linkage_end_to_end.py`
  pins true positives and true negatives at fixed `as_of` instants against stored detector output
  (one band linked at the positive-control instant, three bands reporting overlaps and two bands
  reporting nothing at the end state); `tests/frontend/{explain,function-panel,dashboard}.test.mjs`
  pin the wording and the singular/plural forms, and that a row with no reference makes no claim.
- Negative control: disabling the overlap detector in the working tree fails five unit/end-to-end
  tests, and the browser check fails 16 assertions on target; a card-row mutation fails exactly the
  new card test.
- Payload effect, measured key by key on the deterministic synthetic series: only additive keys
  (`reference_type`/`reference_id` on retests, the five overlap fields) plus the three linked-event
  values that must change because retests are now counted (`event_count 2 -> 4`,
  `counts.retest 0 -> 2`, the events list). No key removed; no decision, plan, scenario,
  qualification or risk value changed; `decision_n1000` digest unchanged.
- Docs correction (acceptance criterion 3): the #66 `README.md`/`STATUS.md` claims that "the demo
  series' events use swing references" are corrected to the measured evidence below.
- Status: **READY FOR AUDITOR RECHECK**.

### Recheck evidence for 0005 / 0006 (repaired head)

- End state (4h, `as_of=2026-10-14T00:00:00Z`, the finding's own instant): bands
  `88363.8–89574.0`, `87180.3–88340.1` and `85958.6–87052.5` each report
  `linked=False, overlapping=True` with 3, 3 and 10 overlapping references; bands
  `90227.9–91022.4` and `85158.0–85158.0` report nothing, which the payload supports. Every retest
  reaches the linkage with its reference: `retest projected= 30 carrying reference_type= 30`.
- Positive control (4h, `as_of=2026-08-26T08:00:00Z`): band `85958.6–86543.0` still links exactly
  (`linked=True, count=1, counts={'breakout': 1, ...}`) and additionally reports its three
  overlapping, non-identical references. The overlap branch never swallows a true link.
- In a real browser (`scripts/browser-audit/zone-reference-check.mjs`, 4 viewports): the explanation,
  the panel row and the card row all state the overlap for the three bands above, none states the
  absolute negative, the two unreferenced band checks stay clean, and the figures on screen are the
  figures the API reports. Positive-control database: the linked band states its link on all four
  surfaces. Negative controls (JS surfaces reverted; overlap detector disabled) fail the check.
- Transcripts: `evidence/0005-0006-recheck.txt`, `evidence/0005-0006-browser.txt`.

### Outstanding
- Found while proving reachability in a browser (recorded, not repaired here): the linkage is only
  evaluated on the ENGINE timeframe, so on the demo series — whose zone-referenced events are all
  on 4h while the default engine timeframe is 1h — the operator sees the overlap fact only when the
  engine timeframe is 4h. On a 1h-engine view of the same series every band honestly says
  `not checked on this view` (the 1h frame stores no reference events). This is the documented
  engine/non-engine split, not a regression, but it is worth an explicit decision: either the
  demo/default engine timeframe should match the timeframe the series stores references on, or the
  reader should be told why the fact is absent on other frames.
- Recorded for the auditor: `support-resistance-check.mjs` must be run against a server whose engine
  (default) timeframe equals its `--timeframe` (its usage block does this). Pointed at a 4H-engine
  server with `--timeframe 1h` its first phase reads the previous view's bands before the requested
  view applies and fails 29 assertions; an independent probe shows the application paints the
  correct 1H band once settled. A one-line fix (wait for the viewed timeframe's own settle facts in
  the first phase too, as the switch phase already does) is deliberately left to a separate,
  auditable change rather than bundled into these two repairs.
- REPAIR-SR-001 (non-engine `zone_bands` passthrough) is confirmed effective in a real browser by
  the probe above; it still awaits its audit ID and independent recheck.
- Nothing else outstanding on this branch at this checkpoint; the audit feed is rechecked at each
  work checkpoint rather than by polling.
