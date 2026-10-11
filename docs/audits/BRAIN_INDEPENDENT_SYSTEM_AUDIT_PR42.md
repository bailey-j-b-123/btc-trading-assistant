# BRAIN — independent system audit (post-PR #42)

**Date:** 2026-10-12

**Repository:** `bailey-j-b-123/btc-trading-assistant`

**Revision inspected:** `b81f24deff6c40a9996718c2564dddc6522a5511` (`origin/main`, merge of PR #42)

**Scope of this PR:** audit documentation only. No strategy gates, application code, tests, records, backfill, live orders or hosting were changed.

## Verdict

**Not ready for credible paper-trading performance evaluation.** BRAIN has useful safeguards for prospective *signal observation*, but a demonstrated dependence on the start of stored history, proposals without a minimum economically meaningful stop distance, and historical validation that does not reproduce forward-book selection/occupancy preclude credible performance claims. Separate runner timing means a recorded 1H paper candidate need not have pre-plan four-timeframe confirmation. None of this proves the trading strategy profitable or unprofitable. Software correctness and trading profitability are distinct questions.

This is an implementation and synthetic-test audit, **not an audit of the operator's Mac SQLite database or running browser**. No real Binance data, deployed server, private database or external exchange account was accessed. The supplied $83,529.47 entry / $83,521.15 stop is treated as an operator-reported example, not as a row independently verified here.

## Method and reproducibility

- Fetched `origin/main` and confirmed its commit equals the inspected branch's `HEAD`; reviewed actual implementation from ingestion through web UI, schema/migrations, and tests. Prior audit documents were context, not proof.
- Ran `python -m pytest -q` in an isolated environment with `.[dev]` installed: **1,161 passed, 1 xfailed, 1 warning** (286.63 s). Ran `npm test`: **298 passed**.
- Ran `python -m pytest tests/test_decision_invariance.py -q --runxfail -k shallow`: **1 failed, 2 deselected** (17.94 s). This intentionally disables the expected-failure marker and exposes the known assertion failure. The fixture is **synthetic**, not the Mac's history.
- Ran a read-only synthetic frontend projection for an ambiguous, settled but slot-occupying plan: tracker occupied = 1, performance unresolved = 0, paper-card active = 0. Reproduce from the repository root with:

  ```sh
  node --input-type=module - <<'JS'
  import { paperObservationsViewModel, performanceViewModel } from './src/trading_assistant/web/static/js/views/dashboard.js';
  const synthetic = {
    report: { combined_metrics_available: true, metrics: {
      paper_plan_count: 1, unresolved_count: 0, completed_count: 1,
      ambiguous_count: 1,
      raw_observational_r: { sample_size: 0, records_considered: 1 },
    } },
    paper_tracker: { unresolved_paper_plan_count: 1, active_paper_plan_id: 'synthetic-only' },
  };
  console.log(synthetic.paper_tracker.unresolved_paper_plan_count,
              performanceViewModel(synthetic).unresolvedOutcomes,
              paperObservationsViewModel(synthetic).active);
  JS
  ```

Passing tests establish fixture behaviour, not correctness on live data. The report below distinguishes **VERIFIED DEFECTS**, **DESIGN LIMITATIONS**, **UNVERIFIED CONCERNS**, and **CONFIRMED WORKING BEHAVIOUR**. Severity describes impact on the stated paper-evaluation objective, not likelihood of financial loss (this application has no order path).

## Pipeline traced

1. `market_data/exchange.py:60-125` builds a public-only, rate-limited Binance Spot CCXT client without credentials. `market_data/service.py:112-531`, `validation.py:132-311`, and `timeframes.py:81-107` download and accept only validated closed OHLCV rows. `market_data/repository.py:154-207` inserts new candles, accepts exact duplicates, and rejects changes to existing values.
2. `market_structure/service.py`, `pattern_liquidity/service.py` and their pure analysis modules build ephemeral structure, reference levels, breakouts and retests. `setup_qualification/engine.py:252-437` replays setup lifecycles over frames; `trade_planning/planner.py:231-294,341-455` plans from the chosen setup and known evidence.
3. `forward_testing/service.py:721-1150,1831-2097` refreshes one base timeframe, catches up closed boundaries, withholds planning over a missing required window, chooses plannable setups subject to an instrument-wide slot check, then stores cycle/observations/plans as a transaction (`repository.py:130-160`). Its outcome pass (`service.py:2379-2664`) observes subsequent candles using `journaling/observation.py` and optionally 1M ordering evidence; outcome *versions* append rather than overwrite.
4. The independent hierarchy runner reads **4H context, 1H setup, 15M confirmation and 5M execution** (`multi_timeframe/service.py:216-355,509-739`; `engine.py:128-208`; `boundaries.py:126-199`). It does not gate the independent paper-plan writer. `web/signal_provenance.py:318-717` performs PR #42's read-only exact-boundary, pre-plan provenance join.
5. `historical_validation/service.py:93-295,461-601` derives split reports from stored candles. `forward_testing/reporting.py:122-249,328-490` derives observational forward statistics from ledger rows. `web/dashboard_service.py:133-318`, `web/routers/forward.py`, and `web/static/js/views/dashboard.js` assemble the dashboard and chart. `/api/statistics` (`web/routers/statistics.py:50-78`) reports **journal** data, not forward paper plans.

**Time semantics:** candle timestamps are aligned UTC opens; a timeframe's candle is available at `open + interval <= decision_time` (`market_data/timeframes.py`, `multi_timeframe/boundaries.py:126-199`). The outcome window starts with the *next* candle, opening at plan `as_of` (`journaling/observation.py:176-189,807-849`). 4H can legitimately refer to an earlier closed bar when a 1H boundary is not a 4H close; that is not by itself staleness. Staleness and missing expected opens need to be interpreted at each timeframe's exact boundary.

## VERIFIED DEFECTS

### V1 — CRITICAL: history-start dependence invalidates decision invariance

**Evidence/root cause:** `market_data/integrity.py:64-117` calculates a default required depth of **131** closes, but `assess_required_window` at `:243-265` truncates to the first stored open. Older reference levels can still influence pattern/setup selection. `setup_qualification/engine.py:310-338` seeds only catalog events present in replayed history. `tests/test_decision_invariance.py:141-145` records the mismatch as strict xfail; disabling xfail yields an assertion failure with different setup lists for full versus shallower stored starts at the same later boundary. The full-history and future-candle invariance tests in that file are separate and pass.

**Impact:** adding older candles can change a recomputed decision near the previous start while frozen ledger rows remain unchanged. A 131-close gate is not proof of sufficient reference provenance. This is dependence on **missing past** information, not demonstrated future-candle leakage. **Do not backfill yet.**

**Reproduce:** `python -m pytest tests/test_decision_invariance.py -q --runxfail -k shallow` (expected to *fail* on current code). Missing: compare full/shallow *plan eligibility and identities* as well as setup lists, and make a missing-reference-provenance boundary report no plan.

### V2 — HIGH: selected chart timeframe can disagree with initial paper/performance timeframe

**Evidence/root cause:** `web/static/js/views/dashboard.js:2557-2564` passes preferred symbol/timeframe to `api.dashboard` but calls `api.forward({limit: 8})` with neither. `web/routers/forward.py:66-73` resolves absent timeframe to the configured default (normally `1h`). `dashboard.js:2599-2624` displays that forward result alongside the selected chart. The later tracker poll *does* pass the dashboard symbol/timeframe (`:2527-2531`), so an initial wrong-timeframe tracker can subsequently switch while the original report is unchanged.

**Impact:** paper counts and performance can be presented beside a different timeframe's chart, even though each API response is individually valid. Reproduce with a preferred `5m` view and distinct 5M/1H synthetic ledgers. Missing integration test: initial report/tracker/chart and subsequent poll must all match the selected instrument/timeframe.

### V3 — MEDIUM: “active” conflates slot occupancy with unsettled outcomes

**Evidence/root cause:** `forward_testing/models.py:279-337` intentionally makes `AMBIGUOUS` *settled* for scoring but still slot-occupying until its horizon policy releases it. `forward_testing/reporting.py:339-354,464-469` counts that outcome as completed, not unresolved. `web/static/js/views/dashboard.js:1675-1685,1709-1716` labels the report's `unresolved_count` **“Active (unresolved)”**. The tracker instead computes occupancy (`forward_testing/service.py:619-650`). The synthetic projection above prints `1 0 0` (occupied, report unresolved, paper-card active).

**Impact:** “active 0 / completed 1” beside a tracker showing an occupying ambiguous plan is explainable but misleading. `AMBIGUOUS` is not an ordered fill, a win or an eligible R observation. Missing cross-panel test: show outcome-settled, trajectory-unsettled, slot-occupied and eligible-for-R as different explicitly named values at the same instant.

### V4 — MEDIUM: confirmation on the main dashboard can lag tracker confirmation

**Evidence/root cause:** `web/dashboard_service.py:273-306` attaches stored provenance only when assembling a full dashboard response. The cheap automatic full-refresh trigger compares the latest *candle* boundary (`web/static/js/live-updates.js:38-46`; `views/dashboard.js:2485-2499`). A hierarchy process can write the matching observation *after* the candle-driven dashboard request. The independent tracker poll then replaces only the tracker card (`views/dashboard.js:2501-2536`), not the main verdict/hierarchy explanation.

**Impact:** until manual refresh or the next base candle, two panels can reflect different ledger snapshots. This is a code-path finding, **not** verification of what happened on the operator's browser. Missing integration test: insert an eligible hierarchy observation after an initial unavailable dashboard response, poll tracker without advancing a candle, and verify synchronized or clearly timestamped displays.

## DESIGN LIMITATIONS / CORRECTNESS RISKS

### D1 — HIGH: tiny stops, zero-default friction and non-executable closed-close entry

For the **operator-supplied, unverified** levels, `83529.47 - 83521.15 = 8.32` USDT/BTC, or approximately **0.996 bp** of entry. Default `PlanningParameters.stop_buffer_mode=NONE` (`trade_planning/parameters.py:86-92`); `trade_planning/levels.py:296-338` then retains the logical invalidation. `planner.py:670-696,730-808` checks correct side and strictly positive risk but no minimum USD, percentage, ATR-relative or friction-relative distance. `planner.py:811-922` checks *gross* structural target R, not a fill-sensitive net R. `historical_validation/parameters.py:39-65` defaults hypothetical fees and slippage to zero; there is no spread or order-fill model.

**Illustration only:** fees of **1 bp on each side** at those levels would cost about **$16.71/BTC** round trip before spread/slippage, exceeding the proposed $8.32 risk. This is **not a claim about the operator's fee tier**. A closed candle's close is known only *after* it closes (`planner.py:576-637`); it is a transparent proposed level, not a proved immediately executable fill. The runner copies any `PLANNABLE` Step 6 plan to paper without adding a minimum stop test (`forward_testing/service.py:1943-1977,2282-2373`). This is an uncalibrated policy limitation, not evidence the reported plan was arithmetically wrong.

**Missing tests:** labelled synthetic plans with $8.32-equivalent long/short risk and valid gross targets, and explicit fee/slippage/spread and missed-entry scenarios. Inspect the operator row's direction, reference, stop-buffer version, ATR, target and assumptions locally before explaining its exact provenance. Do **not** alter signal gates in this PR.

### D2 — HIGH: historical replay does not reproduce the prospective paper book

`historical_validation/service.py:461-525` evaluates each qualified setup on every snapshot and observes each plannable plan; `:563-601` uses the default outcome observer, without the forward runner's v2 1M-resolution acquisition. Forward testing instead freezes one plan per setup and checks an instrument-wide slot (`forward_testing/service.py:1930-1993,2282-2338,2492-2577`). Historical records can overlap, repeat opportunities, and have different ambiguous rates/denominators even with identical base candles. It is a **diagnostic historical proposal replay**, not a simulation of the forward paper ledger.

**Missing test:** on one explicitly synthetic sequence with repeated qualification, two competing setups and ambiguous candles, reconcile historical records with the actual sequential forward choices, refusals, latest outcome versions and denominators. Do not compare their headline R as equivalent samples until this is repaired.

### D3 — HIGH: one-active-paper-trade policy is not cross-process atomic

`ForwardTestService._paper_trade_is_active` reads plans/outcomes (`forward_testing/service.py:2785-2820`) before the transaction that inserts a cycle bundle (`:1930-1993,2071-2074`). The database uniqueness key covers `(exchange,symbol,timeframe,setup_id)`, **not one occupying plan per instrument** (`migrations/versions/0004_forward_testing.py:310-317`). Two timeframe runners can each observe an empty slot before either inserts a different plan. WAL and a busy timeout serialize *writes*, not the preceding read-and-decide. **Actual duplicate live plans are unverified.**

**Missing test:** two-process/barrier-controlled *synthetic SQLite* interleaving, including different timeframes and restart, with both occupancy reads before either insert. Assert at most one occupying plan or explicitly report failure. Do not use the operator database for this experiment.

### D4 — HIGH: 1H candidate and four-timeframe confirmation are different propositions

The default 1H qualification does not require additional higher timeframes (`setup_qualification/parameters.py:20-28`). PR #42 only calls a 1H plan **confirmed** if a stored hierarchy row has the *exact plan boundary*, matching setup identity, correct 4H/1H/15M/5M boundaries, complete non-stale layer evidence and `plannable` verdict (`web/signal_provenance.py:318-535,539-717`), and `hierarchy.recorded_at <= plan.recorded_at` (`:628-642`). A *later* stored result at the same historical boundary cannot be retrospectively advertised as pre-plan evidence. `tests/test_signal_provenance.py:81-260` covers several of these contracts.

The forward runner wakes near the 1H close plus grace (`forward_testing/runner.py:547-564`), while the independent hierarchy runner polls on its own cadence (`multi_timeframe/runner.py:235-242`), refreshes all four timeframes, and can hold an incomplete boundary (`multi_timeframe/service.py:509-672`). No ordering barrier guarantees a hierarchy row is written first. Pre-plan confirmation of an *older* active setup is possible but timing-dependent. A setup **created at that same 1H close** has no subsequently closed 15M/5M candle at that instant because the confirmation/execution windows begin at setup creation (`multi_timeframe/engine.py:164-197`); under the current gates it cannot be genuinely four-timeframe confirmed at its birth boundary.

**Missing tests:** both runner orders at an exact hourly boundary, newly created versus older setup, matching versus different selected setup and precise `recorded_at` cutoffs. Do not change actual signal gates during this audit.

### D5 — MEDIUM: retest tolerance and extended-entry acceptance need calibration

`pattern_liquidity/parameters.py:10-18` defaults breakout tolerance **0.1%**, retest band-touch tolerance **0.2%**, hold threshold **0.1%**, and a 10-candle retest window. `pattern_liquidity/breakouts.py:37-76` requires a fresh close crossing a known reference; `retests.py:15-61` requires a touch for the initial `observed` state, but an already observed retest can later be labelled `held` based on directional close without a fresh near-band touch. `setup_qualification/rules.py:124-141` selects the earliest later held retest, and `:263-277` requires the current close to remain on the trade side. The planner uses the present closed close and gross target-R, without a separate maximum distance from band or ATR-extension limit (`trade_planning/planner.py:576-637,640-727,862-910`). A distant entry **can** qualify if other gates remain satisfied; its frequency and effect on real BTC candles are unverified.

**Missing test:** labelled synthetic touch → later distant hold → qualified setup, logging selected confirmation ID, elapsed candles, band/entry distance, ATR, stop and remaining gross/net target R. This is a strategy policy question, not an invitation to tune the gates against observed results.

### D6 — MEDIUM: snapshot and performance limitations

`multi_timeframe/service.py:236-280` loads each stored timeframe through the boundary then rebuilds setup frames; `web/dashboard_service.py:178-254` separately builds qualification and hierarchy on full requests. Reads are not one cross-timeframe SQLite transaction/snapshot; concurrent candle writes can leave the assembled view temporarily mixed or incomplete, which should be measured rather than assumed to produce a wrong decision. The frontend explicitly allows a 180-second dashboard request (`web/static/js/api.js:14-20`); the cheap candle-boundary and tracker polls avoid some reruns, not expensive full evaluations. **Mac latency and frequency of mixed reads are unverified.**

**Missing tests:** controlled candle insert between four reads; cold/warm growing synthetic database latency, concurrent runner/dashboard requests, and a stable snapshot or explicit incomplete state. Keep large generated datasets out of Git.

### D7 — LOW: application-enforced immutability is not tamper-proof SQLite

`market_data/repository.py:154-207` rejects changed values for existing candle keys; forward cycle bundles are atomic and verify identity (`forward_testing/repository.py:130-184,229-264`); hierarchy observations likewise verify stored identity (`multi_timeframe/repository.py:160-190`). Migrations provide keys, FKs and constraints, but no trigger prevents a different SQLite client with write permissions from altering rows. This is an operational integrity boundary, not evidence that the application overwrote any rows. Add controlled-copy conflict/rollback tests and a documented read-only inspection/backup procedure; do not edit the Mac ledger as a test.

## UNVERIFIED CONCERNS / limits on interpretation

- **The operator's $8.32 row:** its existence, timestamp, timeframe, setup, source reference and outcome cannot be confirmed without the Mac DB. Its values demonstrate why the current *policy permits* tiny risk; they do not identify the exact generating setup.
- **Observed duplicate/overlapping live plans, runner order, uptime, missing candles, migrations, restart recovery and BTC/USDT 1M reconciliation on the Mac:** no Mac database, service logs or process inventory was available. The D3 and D4 scenarios are code-supported risks, not claimed observed incidents.
- **Missing PR #42 labels in the browser:** the API provenance and frontend strings exist (`web/signal_provenance.py`; `web/static/js/paper-tracker.js:84-92`). `web/app.py:183-199` sets `Cache-Control: no-cache` for shell/assets; `tests/test_web_cache.py` checks revalidation. An old running server, cached/installed-app context, selected timeframe, no stored paper plan, missing hierarchy row or an ineligible late row must be distinguished on the Mac. No live website was audited here.
- **Look-ahead and selection bias:** explicit historical splits cap source reads and development outcomes (`historical_validation/service.py:136-166,563-601`); `tests/test_historical_validation.py:198-315` checks future additions/higher-timeframe closes and cohort boundaries on synthetic data. No general future-candle leak was established. But D1/D2, the V1 history-start defect, overlapping opportunities, rule choice after viewing history, and an automatically derived 70/30 split (`historical_validation/parameters.py:105-120`) block claims of truly untouched OOS evidence. No backfill was performed.
- **Exchange realism:** 1M OHLC orders some touches, not fills within a minute or at bid/ask. Spread, queue position, size, adverse selection, price impact, outages and actual exchange fee tier remain outside the model. Historical and paper R are not realised P&L.

## CONFIRMED WORKING BEHAVIOUR (code and synthetic tests, not live certification)

- Public Binance Spot OHLCV client has no API credentials or private-order/account call (`market_data/exchange.py:89-125`); a source search found no order-placement path in `src/trading_assistant`. Dashboard journal POSTs record decisions, not exchange orders (`web/routers/dashboard.py:1-46`). There is **no real-money execution surface** found in the inspected application.
- Closed-candle and aligned-boundary filtering, gap/duplicate handling, immutable-by-application candle values and required-window planning withholding have targeted tests (`tests/test_ingestion_binance.py`, `test_market_data.py`, `test_required_window_gate.py`). Default 131 closes are **not** a proof that reference history is complete (V1).
- Same-candle entry and exit are ambiguous rather than automatically favourable (`journaling/observation.py:807-849`). When complete and exactly consistent, stored 1M candles can resolve their ordering; missing/conflicting or same-minute co-touch evidence remains ambiguous (`:420-545`; `tests/test_outcome_resolution.py`). Forward v2 may append a new outcome for formerly missing 1M evidence; v1 ambiguous history is not silently rewritten (`forward_testing/service.py:2379-2490`; `tests/test_forward_phase3.py`). An `entry_reached` touch without `entry_ordered` must **not** count as a confirmed ordered fill.
- Settled, unresolved and occupied have distinct definitions (`forward_testing/models.py:279-337`); ambiguous outcomes are excluded from eligible R (`forward_testing/reporting.py:328-490`). Version-separated cohorts are not aggregated into one performance figure. Friction-adjusted R is explicitly hypothetical, with zero defaults unless configured.
- Chart uses backend millisecond **candle opens**, mapped to chart seconds without synthetic prices (`web/static/js/chart.js:38-69`); evidence emits both the plotted candle open and `known_at` (`web/chart_evidence.py:196-259,320-413`). A marker drawn at an open **must not** be read as evidence known at that open. Overlays use returned levels (`chart.js:408-505`). Frontend tests do not verify exact appearance in the operator's browser.
- WAL, finite busy timeout and per-connection foreign keys are configured in `database/engine.py:108-177`; lock errors stop runners rather than being swallowed (`forward_testing/runner.py:98-145`, `multi_timeframe/runner.py:83-145`). Ledger duplicate/identity handling and restart cases have tests. Mac operational behaviour still needs local inspection.

## Recommended sequence; smallest safe first repair

1. **Review these findings before an implementation PR.** The smallest safe first repair is to withhold a plan when material reference history may predate the available stored start, surface an explicit *warm-up/reference-history incomplete* reason through forward/historical/dashboard views, and prove the V1 fixture no longer yields a misleading plan. This need not retune breakout/retest/target thresholds. It **does** alter decision availability, so version the contract, preserve existing ledger rows and review historical comparability.
2. Make historical cohorts replay forward **one-plan-per-setup, instrument-wide occupancy, outcome-version/1M-resolution** semantics, or keep them unambiguously labelled as non-comparable proposal diagnostics. Establish cohort parity on synthetic fixtures before any OOS performance claims.
3. On immutable existing observations, measure tiny stops, entry extension, ambiguous outcomes and cost sensitivity. Obtain an explicit operator decision on realistic minimum stop/ATR distance, executable entry, fees/spread/slippage; version any later policy change and do not retrofit old rows.
4. Serialize or atomically arbitrate cross-timeframe paper slots; test concurrent writers. Record/display hierarchy-versus-forward decision timing and setup identity without retroactively promoting late evidence.
5. Fix the selected-timeframe UI request and clarify occupied versus unsettled counts; independently refresh provenance or show separate snapshot times. Benchmark and optimize recalculation only after correctness is pinned.
6. **Only after these steps**, consider a controlled historical backfill and predeclared, untouched out-of-sample protocol. Do not infer profitability from passing tests or a positive hypothetical mean R.

## Checks requiring the operator's real Mac database (read-only)

Use a *read-only* SQLite connection/backup and retain WAL sidecars with any backup; never mutate the live file as an audit probe. Verify `alembic_version`, `PRAGMA integrity_check`, journal mode, FK setting and process start/log/error times. Compare contiguous, aligned, latest closed opens and gap ranges separately for **4H, 1H, 15M, 5M**; inspect only the 1M spans used for ambiguous outcomes and verify OHLC aggregation against their parent candles.

For the reported row, inspect `forward_paper_plans.paper_plan_id, timeframe, setup_id, plan_as_of, recorded_at, entry_level, stop_level, risk_per_unit, plan_json, planning_rules_version, friction_assumptions_json, version_fingerprint`, source reference, ATR, target R and neighbouring candles; join latest `forward_paper_outcomes` by plan ID and sequence. Group plans by setup/timeframe, entry/stop distance in bps, observation version, entry ordering and ambiguity reason. For every 1H plan, compare `forward_hierarchy_observations` by **exact exchange + symbol + decision_time = plan_as_of**, then check `recorded_at <= plan.recorded_at`, setup ID, 4H/1H/15M/5M candle boundaries, gaps, stale flags and supporting layer evidence. Count eligible, late, absent and conflicting results **separately**. Reconcile the exact same as-of and timeframe across `/api/dashboard`, `/api/forward`, `/api/forward/paper-tracker`, and the **journal-only** `/api/statistics` rather than comparing unrelated denominators.

Finally, inspect the *running* server's `/api/dashboard` `signal_provenance`, tracker provenance, `/` and `/static/js/paper-tracker.js` cache headers and served JS, browser refresh/network log and runner logs. These checks are necessary before claiming why PR #42 labels were absent or that true pre-plan four-timeframe confirmations occur at a particular rate. **None were performed in this audit.**
