# Entry-quality audit — breakout/retest paper signal (task 1, read-only)

**Type:** read-only investigation. No trading rule, parameter, schema, ledger row or paper record was changed.
**Base:** `main` at `723fd98` (PR #38 and #39 merged; their work is not repeated here).
**Trigger:** the first real Binance BTC/USDT 1H paper plan — bullish breakout/retest continuation, entry 83,916.01, stop 83,606.01, target 84,379.31, outcome STOPPED (-1R). Concern: BRAIN entered near the top of the move instead of waiting for a pullback.

**Held / unchanged:** the decision-invariance failure (`docs/decision-invariance.md`, strict xfail in `tests/test_decision_invariance.py`) remains documented and unresolved. Historical backfill remains held. No real orders, no hosting.

**Evidence labels.** "Verified" = read in code and, where stated, reproduced with the probes in `docs/audits/entry-quality-probes/`. **All probe candles are SYNTHETIC. They are not the first trade and say nothing about Binance.** Facts that need the Mac database are listed in section 6 and were not seen.

## 1. Verified behaviour

1. **The breakout candle cannot be the retest candle.** `pattern_liquidity/analysis.py` (~L305) passes only candles with `timestamp >= breakout.known_at` (the breakout candle's close) to the retest detector, and `retests.py::detect_retest` (L23) requires `elapsed >= 1`. The earliest retest is the next hourly candle.
2. **That next candle can be "observed" and "held" at once.** `detect_retest` (L38–50) appends both states for one candle, so a setup can go WATCH → QUALIFIED one close after the breakout.
3. **A retest need not touch the broken level.** A touch counts when `low <= band_high + retest_tolerance_pct` (0.2%, ≈ $167 at these prices). "Held" needs only a close above the level + 0.1%. Probe B: a candle whose low stayed $147 above the level and only ~$35 below the breakout close counted as a held retest.
4. **Entry is always the latest closed 1H close at the planning boundary.** `trade_planning/planner.py::_resolve_entry` (L576) via `levels.py::plan_close_level` (L121). Deliberate: the module docstring (L9) says no earlier or better price is substituted. Locked by `test_trade_planning.py::test_entry_is_always_the_decision_time_close` and `::test_continuation_uses_the_decision_time_close_not_the_retest_close`. The retest or pullback price is never used.
5. **QUALIFIED is re-evaluated at every close, not frozen at the retest.** `setup_qualification/engine.py::enumerate_qualifications` re-runs the rules each bar, so `volume` and `location` (`rules.py`) use the *current* candle. Probe D: a held retest on a low-volume bar left the setup WATCH; it became QUALIFIED and PLANNABLE two bars later at a higher close. Probe F: QUALIFIED/PLANNABLE persisted through bar 10 (`continuation_max_bars = 10`), expired at bar 11, entry tracked the current close and risk grew from 221.5 to 761.5.
6. **Stop = the broken reference's `band_low`, no buffer.** `planner.py::_resolve_invalidation_and_side` (L640); `PlanningParameters.stop_buffer_mode` defaults to `NONE`.
7. **The 1R floor is the only distance-based check.** `planner.py::_apply_minimum_r` (L862). The stop moves away as entry rises, so a plan drops out only when no target reaches 1R. Probe A: first bar PLANNABLE at 2.16R, next bar `minimum_r_multiple_not_met`.
8. **The ledger freezes the first PLANNABLE plan per setup.** `forward_testing/service.py::_record_cycle` (~L1917–1961) and `_build_paper_plan` (L2282). If the instrument-wide slot is busy nothing is frozen, so a later bar at a worse price can be the one frozen.
9. **A stop is a wick touch.** `journaling/observation.py` L504 uses `low <= stop`, no slippage.

## 2. Hierarchy: intended architecture vs implementation

**Intended** (README Step 13, `multi_timeframe/decision.py`): one decision system. PLANNABLE needs 4H context, a QUALIFIED 1H setup, 15M CONFIRMING and 5M ARMED/TRIGGERED, on complete and current data.

**Implemented:**
- `forward_testing/` has no reference to `multi_timeframe` or the hierarchy.
- The forward service uses default `QualificationParameters()` (`higher_timeframes=()`), so the 4H rule is "not_requested".
- Planning data health (`_live_health`, `_boundary_context`) looks at the 1H window only.
- Therefore BRAIN **can** freeze a 1H paper plan while the 4H/15M/5M layers are uncertain, waiting or not armed.
- Dashboard wording is honest (`plain.js::hierarchyAllowsReadyWording` requires hierarchy PLANNABLE); the ledger and the PR #39 tracker do not use it.
- Even a complete hierarchy would not change the price: `multi_timeframe/execution.py` outputs timing states only, and TRIGGERED (a 5M close through the far side of the zone) is arguably a chase signal.

`BRAIN_FULL_SYSTEM_AUDIT.md` Finding 8 / P4 already recorded this as a policy decision, not a bugfix. Classification: **documented architecture gap**, not a coding defect.

## 3. Protections against chasing

**Present:** close-only breakout with no reference already outside; retest required within 10 bars; failed breakout / failed retest / close below band end the setup; relative volume >= 1 on the current bar; 10-bar expiry; 1R floor; one active paper trade per instrument; MISSED labelling.

**Absent** (no use of `penetration_atr`, `penetration_pct` or `distance_from_band` as gates anywhere in `src/`): no cap on distance from the band or entry-to-stop in ATR or %; no decision-candle size limit; no wait-for-pullback after confirmation; `max_atr_percent = 10` is effectively no filter; no 4H/15M/5M requirement on the ledger path. The word "chase" in the code only means "do not reuse an old entry".

## 4. Potential bugs (separate from strategy)

1. **Arbitrary choice among same-breakout setups.** Probe 7: one breakout candle produced two PLANNABLE setups (swing-high reference, stop distance 221.5; zone reference, stop distance 659). `setups` are sorted by `(created_at, id)` (`engine.py` ~L389) and `id` is a SHA-256, so hash order decides which one takes the single paper slot and therefore which stop is used.
2. **Stop and setup invalidation disagree.** The setup tolerates a close up to 0.1% below the band (`failed_retest`), and `opposite_close_through_reference` needs a close below `band_low`; the paper stop is touched by a wick at exactly `band_low`. A retest-style trade has its stop on the level it expects to be probed again.
3. **Late slot claim** (1.8) — design behaviour; needs a decision.
4. **Fixture data in PR #39 tests is invented.** `tests/frontend/paper-tracker.test.mjs` and `dashboard.test.mjs` use the real levels with made-up timestamps (14:00, 15:00), `outcome_version_count: 12` and `target_r_multiples: ["1.5"]`. From the levels, R = 463.30 / 310.00 = **1.4945**, not 1.5. They are fixtures, not evidence.

## 5. Candidate strategy improvements (not implemented, not yet recommended)

Pullback-anchored entry at the broken band (uses the existing `ENTRY_NOT_REACHED` model); extension gate in ATR or risk units; small stop buffer; deterministic tie-break among co-seeded setups; hierarchy-gated paper book; decision-candle size limit. Each trades fill rate against expectancy; none can be judged on the current sample.

## 6. First-trade evidence

**In the repo:** only the three levels and the STOPPED result (inside PR #39 fixtures, supplied by the operator). Derived arithmetic: risk 310.00 (0.369% of entry); reward 463.30 (0.552%); R 1.4945 (below the preferred 1.5, above the 1R floor); entry 0.371% above the stop. No database, candles or 1m data are committed (`data/` is empty).

**Needs the Mac database (not seen, nothing invented):**
- reference type (swing high / zone / range high), its band, and whether the stop equals a swing price or a zone low;
- timestamps of breakout, retest, first QUALIFIED, first PLANNABLE and frozen bars (`forward_observations`, `forward_cycles`);
- OHLC, volume and ATR of the breakout, retest and plan candles (`ohlcv_candles`);
- `recorded_at` vs `plan_as_of` and the cycle's data health (CURRENT vs HISTORICAL);
- sibling setups from the same breakout, and whether the slot was busy earlier;
- the stop candle (wick or close), outcome versions, and whether 1m resolution was used (`forward_paper_outcomes`);
- the 4H/15M/5M hierarchy state at the time (`forward_hierarchy_observations`, if that runner was running).

The read-only report (`python -m trading_assistant.trade_forensics`, README "First paper trade: read-only forensic report") answers the items above from the Mac database. It cannot answer the reference type (only the reference id is stored), absolute ATR (only ATR as a percent of price is stored), touch-only retests, or whether a lower entry would have filled; those are reported as UNAVAILABLE.

## 7. Recommended next change (not implemented here)

A **read-only paired shadow-evaluation harness for entry quality** — no rule change, no ledger writes. For every boundary where baseline BRAIN is PLANNABLE: compute decision-time features (entry distance from the band in ATR and in risk units, decision-candle range and close location, bars since breakout / held retest, first-qualified vs later bar, sibling count, 4H/15M/5M states), evaluate pre-registered candidate rules on the same events, and send baseline and candidate through the unchanged Step 7 observer.

Why not a rule now: one loss, 21 v1 `AMBIGUOUS` plans that can never be scored, and no backfilled history.

**Testing without look-ahead or fitting to one loss:**
- Pre-register two or three candidates with thresholds fixed from structural reasoning before any result; exclude the first trade from threshold selection.
- Features come only from the frame at `as_of`; add a test that the candidate decision is unchanged when future candles are appended. Outcomes use only candles from `plan_as_of` onward.
- Pair and cluster: one event per breakout (collapse siblings); report kept *and* skipped events; report fill rate and ambiguous rate per rule; friction above zero; block bootstrap by week/regime.
- Use Step 11's `ChronologicalSplit`: freeze dates before any run, choose on development data, touch out-of-sample once.
- Decision-invariance caveat: label historical results, apply a warm-up exclusion, report sensitivity to it, and exclude boundaries whose references predate the stored start once option A is decided.
- Real history needs the held backfill. Until then: validate on synthetic data and on the Mac DB as it stands. The first output should be the count of independent breakout-retest events; no required sample size is claimed before that is known.

After the harness: decide the tie-break (4.1), then the hierarchy gate (policy), then entry rules, each under its own approval.

## 8. Reproducing the probes

`docs/audits/entry-quality-probes/` (SYNTHETIC, read-only, import the unchanged engine; not collected by pytest):

| Script | Shows |
|---|---|
| `probe3.py` (A) | breakout → held retest next candle → entry = that candle's close |
| `probe4.py` (B, C) | retest tolerance (low $147 above the level); a huge breakout candle gets no plan |
| `probe5.py` (D) | held retest on a low-volume bar → QUALIFIED on a later bar → entry = later close |
| `probe6.py` (E) | observed touch, then a later impulsive candle labelled held |
| `probe7.py` | one breakout candle → two PLANNABLE setups with different stops |
| `probe8.py` (F) | QUALIFIED persists to bar 10, expires at bar 11 |

Run from the repo root with the project's dependencies installed: `python docs/audits/entry-quality-probes/probe3.py`.

Test subset run for this audit (no changes): `tests/test_trade_planning.py tests/test_pattern_liquidity.py tests/test_setup_qualification.py tests/test_forward_paper_tracker.py tests/test_decision_invariance.py` → 203 passed, 1 xfailed (the known invariance defect).
