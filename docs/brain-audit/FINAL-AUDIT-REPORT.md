# BRAIN — FINAL INDEPENDENT AUDIT REPORT (Agent 1, session 2)

- Audit date: **2026-10-14 UTC**. Auditor: independent Agent 1 (second auditor session; the first published `BRAIN-AUDIT-0001`–`0004` and PR #68).
- Repository: `bailey-j-b-123/btc-trading-assistant`. This feed: branch `arena/0d6ba3f7-btc-trading-assistant` (PR #71), mirrored to `arena/d3f9aa87-btc-trading-assistant` (PR #68).
- Scope: read-only review, isolated offline tests, reproducible probes. **No** application code was modified by this auditor; **no** orders, deployments, production-database writes, credential use, risk-limit changes, or merges into `main`.
- All data used below is either synthetic fixtures shipped with the repository or the clearly-labelled synthetic demo database created by `scripts/seed_sr_demo.py`. **No genuine market history exists in the repository** and none was fetched.

## 1. Exact state audited

| Subject | Commit | Notes |
|---|---|---|
| Common development base (PR #62 head; base of #65/#66/#67) | `a0f1fafd7ed6841eaec14809c03b7556ff0949c4` | branch `arena/3e360305-btc-trading-assistant` |
| PR #65 Chart Patterns | `ee6ab770b71957a7c30f5d90db119c187330bcc8` | draft; +17608/−47 |
| PR #66 Support & Resistance | `a5c9013c491938dbbf8317af75da3f4793a5a3a9` | draft; +4689/−127; backend + frontend |
| PR #67 Swing Highs & Lows | `e2237723a8d9437504fdb54fa491ca1759e0215b` | ready for review; +114/−7 |
| PR #68 audit feed (session 1) | `4193b09…` → now `a03cd23…` | findings 0001–0004 |
| **PR #69 repair integration (Agent 2)** | **`09f5ee2113b3cf35015e4d0058f45fa212cbe529`** | +24097/−220, 125 files; base `arena/3e360305…` |

The three feature branches are independent of each other: each was cut from `#62`, so none contains the others and individual CI says nothing about combined behaviour. Dependencies between them are textual (shared `dashboard.js`, `chart.js`, `chart_functions.js`, `function_panel.js`, `styles.css`, tests) and behavioural (the dashboard composes patterns and zones on one canvas).

## 2. Test commands and actual results (all run in this session)

| Command | Where | Result |
|---|---|---|
| `.venv/bin/python -m pytest -q` | dev base `a0f1faf` | **1182 passed, 1 xfailed, 1 warning in 211.33 s** (exit 0) |
| `npm test` | dev base `a0f1faf` | **425 passed / 0 failed** (17.9 s) |
| `.venv/bin/python -m pytest -q` | PR #69 head `09f5ee2` | **1254 passed, 1 xfailed in 204.67 s** (exit 0) |
| `npm test` | PR #69 head `09f5ee2` | **481 passed / 0 failed** (18.5 s) |
| `node --test pattern-view zone-bands zone-position breakout-stages timeframeswitch` | PR #69 | **91 passed / 0 failed** |
| `.venv/bin/ruff check --isolated --select E4,E7,E9,F src tests` | base **and** PR #69 | **15 findings on both** (11 × F401, 2 × F841, 2 × E741) — pre-existing, unchanged |
| Audited probes (below) | 4 trees (`base`, `pr67`, `pr69`, clone) | see findings |

The single documented xfail remains
`tests/test_decision_invariance.py::test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary`
(“a shorter stored history misses live setups whose reference level formed before the stored start”). It is unchanged on every branch audited here and is a **known, pinned defect**, not a regression.

## 3. Confirmed defects

| ID | Sev | Subject | Status |
|---|---|---|---|
| BRAIN-AUDIT-0005 | P2 | **Retests can never be linked to a zone band.** `web/chart_evidence.py::_event_item`'s retest branch omits `reference_type`/`reference_id`; `web/zone_evidence.py::link_events_to_bands` (line 182) drops any event whose `reference_type` is not `"zone"`. On #66's own demo DB (4h) 30 retests are projected, **0** carry a reference while **10** are genuinely zone-referenced. | **CONFIRMED, open** |
| BRAIN-AUDIT-0006 | P2 | **Zone↔event linkage cannot fire for historical references, and the UI states an absolute negative.** Exact-bound identity is tested against the *current* frame's zones while each event keeps its detection-instant bounds (e.g. band `85958.6–87052.5` vs the reference `85958.6–86543.0` that 1 breakout + 2 retests used). All 6 zone-referenced projected events overlap a displayed band; none links; `explain.js` then prints *“No stored breakout, sweep or retest used this zone as its own reference level.”* Positive control: at the event's own `as_of=2026-08-26T08:00Z` the link **does** fire, proving the rule, not the data, is the cause. The same finding corrects #66's stated reason (“their events use swing references”), which the measurements contradict. | **CONFIRMED, open** |
| (pinned) decision invariance | — | The documented shallow-history under-reporting xfail above. | **CONFIRMED, pre-existing** |

Failing → passing evidence and reproduction commands are in the finding documents and in `evidence/zone-linkage-*.txt`.

## 4. Repairs independently rechecked (Phase 4)

Rechecked at `09f5ee2` against base `a0f1faf`, with the production functions, not the repairer's tests:

| Finding | Verdict | Decisive evidence (mine) |
|---|---|---|
| 0001 Patterns/S&R integration conflict | **VERIFIED** | `git merge-tree ee6ab77 a5c9013` still exits 1 on the base; the integrated tree keeps every parent symbol (0 gaps across the five shared files: dashboard.js 132/133→136, chart.js 94/66→98, chart_functions.js 27/26→28, function_panel.js 35/28→37, styles.css 525/468→526); feature suites 91/91 |
| 0002 dead runner can show green | **VERIFIED** | base returns `healthy:true/green` for a 7-day-stale, missing and future heartbeat; repaired UI is green **only** on a server verdict `CONFIRMED_ALIVE` + `alive_confirmed` + consistent age ≤ tolerance, and amber otherwise in 10 adversarial cases; backend matrix: 359 s alive / 361 s stale / future clock-inconsistent / ERROR failed / unknown status unconfirmed / never-run; observed 600 s cadence widens tolerance to 1800 s; invalid overrides (`0`, `-1`, `86401`, `True`, `1.5`, `"600"`) refused; naive `recorded_at` refused |
| 0003 wrong-timeframe trend fallback | **VERIFIED** | base borrows the 1h trend in 8/10 adversarial cases; repaired panel returns UNKNOWN for unavailable/untimed/malformed evidence, allows the fallback only when `evidence.timeframe === marketState.timeframe` (identity proven), and no longer prints a non-allowlisted direction (`"sideways"`) verbatim |
| 0004 retest anchored to the observation candle | **VERIFIED** | base renders Held at `06:00`/price 109/bar 6 with “1 of 2 held”; repaired renders the outcome candle `07:00`/price 111/bar 7 with `knownAt 08:00` **and** `observedKnownAt 07:00` and corrected lifecycle wording; commit `98b7e19` touches no detector/backend file |

Auditor-added check of the repairer's extra commit `09f5ee2` (backend S/R bands on non-engine timeframe views): the spread is guarded by `structure.timeframe === viewedTimeframe`, so no engine bands are borrowed and the linkage on that path is honestly “not evaluated”. **No new defect found.**

## 5. Subsystems reviewed and their standing

**Verified sound (with independent evidence)**

- **Entry / stop / target / R arithmetic** — `probes/plan-math-invariants.py` re-derives every published number for 7 qualification fixtures (continuation long/short, reversal long/short, failed-breakout reversal, range, rich continuation): entry equals the frame's latest closed close; `risk == quantize(|entry − stop|)`; stops on the losing side; every target on the correct side with `reward` and `R = quantize(reward/risk)` exactly re-derived; every retained target ≥ the mandatory 1R floor. **0 invariant failures**, and the entry-anchor candle always closes at or before the plan's `as_of` (no future evidence in the plan).
- **Timestamp / timeframe boundaries** — `latest_closed_candle_open_time` correct at exact boundaries and one second either side across 1m/15m/1h/4h/1d (8/8 cases).
- **Swing detection** — `market_structure/swings.py` is lookahead-safe by construction: left/right windows of 2, `confirmed_at = confirming candle open + interval`, only swings with `confirmed_at <= as_of` returned, gaps never spanned, ties resolved by an explicit policy.
- **Trade planning policy** — decision-time entry (no chasing an old price), stop never tightened, structural targets only, explicit `NO_PLAN` reasons (`no_valid_target_available`, `minimum_r_multiple_not_met`), `INVALID` outranks `NO_PLAN`.
- **Runner liveness** — as in §4; the policy is server-owned, evidence-based and fails safe.
- **Integration composition** — #65 + #66 + #67 coexist in `09f5ee2`; no symbol loss anywhere both features touched.
- **Web security surface** — no execution/credential endpoints, no `/docs`/`/openapi`, JSON-only errors, unhandled exceptions logged server-side only, security headers on every response, no inline scripts/handlers, `innerHTML`/`eval` absent from the frontend, static assets served by `StaticFiles` (traversal-safe). `python -m trading_assistant.web` binds `127.0.0.1` by default.
- **Data integrity gate** — `market_data/integrity.py` documents and implements a required-contiguous-window check with explicit cold-start/leading-shortfall semantics; gap-aware components (swing windows, replay resets) are separated from count-indexed ones (ATR, relative volume).

**Verified gaps (missing features, explicitly NOT defects)**

- **No position sizing, capital allocation, fees, slippage, funding, balances, leverage or realised P&L anywhere** — by explicit, repeated design statements in `README.md` (e.g. §“Risk and R”), enforced by tests that scan planner source and JSON keys for forbidden tokens. Risk is expressed as unit-neutral R distances with a mandatory 1R floor. Any future “performance” claim must therefore remain a *relative-R* claim.
- **Classical pattern detectors are limited** to double top / double bottom / head-and-shoulders / inverse head-and-shoulders with horizontal necklines and 3- or 5-swing geometry (`pattern_liquidity/classical_patterns.py`). Triangles, wedges, flags/pennants, channels, triple formations, cup-and-handle and rounding patterns are not implemented (no false claims were found for them either).
- **No authentication in front of the dashboard** (documented: “deployment as a private web app should add authentication first”). Anyone who can reach the port can read everything and append journal rows.
- **Supported timeframes default to `1m, 5m, 15m, 1h, 4h`** — `1d` is honoured only when a deployment configures and stores it; the ladder refuses to invent a frame above 4H otherwise.
- **No approved visual reference was available to any auditor session**, so no design-match acceptance is claimed anywhere in this feed; the available screenshots are behavioural evidence only.

**Untested or only partially tested here**

- No genuine historical market data: everything is synthetic fixtures or the synthetic demo DB, so nothing below tests *real* market behaviour.
- Live Binance connectivity, ccxt behaviour, and the forward runner across multiple real boundaries (covered by the repository's offline fake-exchange tests only; the sandbox blocks exchange hosts).
- Browser-level accessibility/contrast and responsive layout: session 1 produced offline-browser evidence at four viewports; this session verified the JS/DOM contracts by Node tests and the live HTTP contract indirectly, not by re-running a browser.
- Performance at scale (the #66 sign-off reports 13.1 s cold / 3.2 s warm for the 1h dashboard; not re-measured here).
- Multi-worker/concurrent access, non-Linux platforms, and long-running forward endurance.

## 6. Integration status and conflicts

- **#65 + #66 conflict (0001) is resolved** in `#69` and independently verified: both feature sets are present, no parent symbols were dropped, feature suites pass, the whole suite grows without a single new failure.
- #66 + #67 and #65 + #67 were already textually clean; the merged tree keeps their behaviour (91 feature tests, 481 Node tests).
- No merge into `main` was performed. Nothing has been pushed to `main`; the audit feed changes only `docs/brain-audit/**` (verified: the diff against the development base contains no non-audit file).

## 7. Safety risks and technical debt

1. **P2 (open):** the S/R “linked events” accounting is inert in practice and its operator-facing sentence overstates a negative (0006), partly because retests are structurally unlinkable (0005). Fix before treating any “this level was broken/retested” statement as evidence.
2. **P3:** `explain.js` renders “an unknown reason” when an evidence trend carries no `reason` (0003 residual) — cosmetic, invents nothing.
3. **P3:** no authentication + operator-triggerable journal writes; keep the binding local or front the app with authentication.
4. **P3 (debt):** 15 pre-existing ruff findings (11 unused imports, 2 unused variables, 2 ambiguous names) unchanged by every branch audited.
5. **Known defect (pinned):** the shallow-history decision-invariance xfail remains; the reference-provenance gate bounds what a shallow record may plan, but not what it can see.
6. **Evidence hygiene:** the demo seeder and all fixtures are explicitly labelled synthetic; any screenshot or figure produced from them must never be presented as market history or performance.

## 8. Readiness for further paper-trading validation

**Yes, with two conditions.** The decision path itself — structure, patterns, qualification, planning arithmetic, entry/stop/target derivations and the anti-lookahead guards — passed independent re-derivation, and the repair integration preserves every feature and every test. Nothing found in this audit touches position sizing (none exists), the forward ledger, or the journal’s append-only guarantees, and no P0/P1 defect was found.

Conditions:
1. Repair `BRAIN-AUDIT-0005` and `BRAIN-AUDIT-0006`, or explicitly stop advertising per-band event linkage in the UI and documentation; as it stands, an operator can be told “no stored breakout, sweep or retest used this zone” while the stored evidence says the opposite.
   * **Addendum (2026-10-14, repair commit `926fea2`, feed branch):** both defects are repaired;
     see `REPAIR-0005-0006.md`. Condition 1 is satisfied **pending independent recheck** — the
     repair is not independently verified, and the browser regression could not be executed in the
     repairing environment.
2. Continue paper trading on **stored, genuine** market data with the runner-health semantics now in place (stale/future/absent heartbeats can no longer read healthy), and keep the historical/forward results separate as the UI already does.

## 9. Reproduction index

- Findings: `findings/BRAIN-AUDIT-0005.md`, `findings/BRAIN-AUDIT-0006.md`; session-1 findings `0001`–`0004`.
- Recheck write-up: `RECHECK-0001-0004.md`.
- Probes (read-only): `probes/zone-linkage-{evidence,cause,asof}.py`, `probes/swing-bias-identity.mjs`, `probes/runner-liveness-probe.mjs`, `probes/runner-health-backend-probe.py`, `probes/plan-math-invariants.py`, `probes/integration-retention.py`, plus session-1 probes.
- Evidence: `evidence/zone-linkage-*.txt`, `evidence/recheck-000{1..4}-*.txt`, `evidence/session2-baseline-and-integration.txt`, plus session-1 evidence.
