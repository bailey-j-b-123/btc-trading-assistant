# BRAIN — Entry Timing and Stop-Loss Investigation

**Type:** Read-only system and strategy investigation. No trading rules, parameters, database schemas, ledger rows, paper records, or real-money order paths were changed.  
**Base:** `main` at commit `a2dbc952d5671734962d9409580ebee4022c3eed` (including PR #44).  
**Context:** Continues from `ENTRY_QUALITY_AUDIT.md` (PR #40), `BRAIN_FULL_SYSTEM_AUDIT.md`, and `BRAIN_INDEPENDENT_SYSTEM_AUDIT_PR42.md` (PR #43). Outstanding issues—including the history-start invariance defect (`docs/decision-invariance.md`, strict xfail in `tests/test_decision_invariance.py`) and the historical validation cohort vs. forward ledger occupancy mismatch—remain documented and held.

---

## Executive Summary

This investigation diagnoses two critical forward paper-trading anomalies observed in Binance BTC/USDT 1H operations on 11 October 2026:

1. **Trade 1 (17:00 UTC — "Early / Extended Entry"):** Entry $83,916.01, Stop $83,606.01, Target $84,379.31, Outcome STOPPED (-1R).
   * **Root Cause:** A 0.2% tolerance (`Decimal("0.2")`) in retest detection qualified a "touch" even though price remained **$128.71 above** the broken reference boundary. Furthermore, the engine emitted both "observed" and "held" confirmation on the exact same candle, and entered at the close of that candle—**$310.00 above** the reference band—buying at the extreme of the continuation move.
2. **Trade 2 (22:00 UTC — "Tiny Stop"):** Entry $83,529.47, Stop $83,521.15, Targets $83,572.96 and $83,577.00, Risk **$8.32** (0.996 bps), Outcome AMBIGUOUS.
   * **Root Cause:** BRAIN defaults to `StopBufferMode.NONE` and contains **no minimum risk distance floor** in dollar, basis-point, or ATR terms. Any risk $> 0$ passes planning. Because risk was negligible ($8.32), minuscule price movements of $43.49 and $47.53 generated inflated R-multiples (> 5.2R), clearing the mandatory 1R floor. In reality, illustrative round-trip market friction (8–25 bps) exceeds this nominal stop distance by 8x to 24x, and normal 1-minute BTC volatility ($20–$100) triggered `ENTRY_AND_EXIT_SAME_CANDLE` ambiguity within the same minute.

---

## 1. Trade 1 Forensic Analysis — Early / Extended Entry

### Parameters and Observed Values
* **Date/Time:** 11 October 2026, 17:00 UTC
* **Setup Type:** Bullish Breakout/Retest Continuation (`BTC/USDT`, 1H)
* **Frozen Reference Boundary:** $83,606.01
* **Retest Candle Low:** ~$83,734.72
* **Entry:** $83,916.01 (decision candle close)
* **Stop:** $83,606.01 (`reference.band_low`)
* **Target:** $84,379.31 (structural swing/level)
* **Risk per Unit:** $310.00 (0.369% / 36.9 bps)
* **Reward per Unit:** $463.30 (0.552% / 55.2 bps)
* **Reward-to-Risk (R):** 1.4945R
* **Outcome:** STOPPED (-1R)

### Findings
1. **The reference boundary was never touched:**
   * Candle low ($83,734.72) stayed **$128.71 above** the reference level ($83,606.01).
2. **Retest Tolerance Unit and Value:**
   * In `PatternLiquidityParameters` (`src/trading_assistant/pattern_liquidity/parameters.py`), parameter units are percentages (0 to 100), not fractional rates:
     ```python
     retest_tolerance_pct: Decimal = Decimal("0.2")  # 0.2 percent
     ```
   * In `market_structure/numeric.py::tolerance_band`, the percentage is converted to an absolute price distance by dividing by 100:
     ```python
     def tolerance_band(price: Decimal, tolerance_pct: Decimal) -> Decimal:
         return quantize_derived(Decimal(price) * Decimal(tolerance_pct) / Decimal(100))
     ```
   * At $83,606.01 with `tolerance_pct = Decimal("0.2")`:
     $$\text{Tolerance} = 83,606.01 \times \frac{0.2}{100} = \$167.21$$
   * In `pattern_liquidity/retests.py::detect_retest` (lines 30–35), a touch is detected if:
     $$\text{candle.low} \le \text{ref.band\_high} + \text{tolerance} \implies \text{candle.low} \le 83,606.01 + 167.21 = \mathbf{\$83,773.22}$$
   * Because $\$83,734.72 \le \$83,773.22$, the candle was qualified as having touched the reference band.
3. **Double State Transition on a Single Bar:**
   * In `detect_retest` (lines 40–53), after recording `"observed"`, the same candle closed above the level by more than `retest_hold_pct` (0.1%), appending `"held"`.
   * Both states were committed at once for the 16:00 UTC bar.
4. **Adverse Entry Extension:**
   * In `trade_planning/planner.py::_resolve_entry` (line 576), entry is fixed to the latest closed candle close ($83,916.01).
   * Entry was taken **$310.00 above the reference** and **$181.29 above the retest low**. The stop was set at $83,606.01. The position absorbed maximum adverse extension and was stopped out when price subsequently returned to retest the level.

---

## 2. Trade 2 Forensic Analysis — Tiny Stop Loss

### Parameters and Observed Values
* **Date/Time:** 11 October 2026, 22:00 UTC
* **Setup Type:** Bullish Breakout/Retest Continuation (`BTC/USDT`, 1H)
* **Frozen Reference Boundary:** $83,521.15
* **Entry:** $83,529.47 (decision candle close)
* **Stop:** $83,521.15 (`reference.band_low`)
* **Stop Distance / Risk:** **$8.32** (0.00996% / **0.996 bps** — less than 1/100th of 1%)
* **Targets:** 
  * Target 1: $83,572.96 (Reward: $43.49 $\rightarrow$ **5.227R**)
  * Target 2: $83,577.00 (Reward: $47.53 $\rightarrow$ **5.712R**)
* **Outcome:** AMBIGUOUS (`ENTRY_AND_EXIT_SAME_CANDLE`)

### Findings
1. **Precise Stop and Target Derivation:**
   * **Stop derivation:** The setup's reference had `band_low = $83,521.15`. In `planner.py::_resolve_invalidation_and_side` (lines 670–678), invalidation is set to `reference.band_low`. In `planner.py::_resolve_stop` (lines 727–760), `PlanningParameters.stop_buffer_mode` defaults to `StopBufferMode.NONE`. Thus, `stop = invalidation = $83,521.15`.
   * **Target derivation:** In `trade_planning/levels.py::structural_target_candidates` (lines 210–300), the planner extracted structural swing/zone levels strictly above entry ($83,529.47), sorted nearest first: $83,572.96 and $83,577.00.
2. **Why It Passed Planning Checks:**
   * **Absence of a risk floor:** In `planner.py::_resolve_stop` (lines 794–807), the risk check is simply:
     ```python
     risk = abs(entry_value - stop_value)
     if risk > 0:
         context.risk_per_unit = risk
     ```
     There is **no minimum risk check** in dollars, basis points, or ATR multiples.
   * **Distorted R-multiples:** Because risk was $8.32, modest price targets $43.49 and $47.53 away produced huge R-multiples (5.23R and 5.71R).
   * **Blindness of the 1R floor:** `planner.py::_apply_minimum_r` (line 862) checks only `r_multiple >= min_r_multiple` (1.0). Since 5.23R $\ge$ 1.0R, it passed without warning.
   * **Default zero friction:** Friction assumptions (`friction_assumptions_json`) default to zero; fees and spread are not deducted during planning.
3. **Why the Outcome Was Ambiguous:**
   * In `journaling/observation.py::observe_outcome_at_1m` (lines 500–535), when entry and stop (or target) are both touched within the same candle/minute, `terminal = OutcomeStatus.AMBIGUOUS` with reason `ENTRY_AND_EXIT_SAME_CANDLE`.
   * An $8.32 distance is far smaller than normal 1-minute BTC volatility ($20 to $100). The trade was instantly swamped by microstructure noise.

---

## 3. Pipeline Tracing Through the Code

### 3.1 Breakout Detection
* **Files:** `pattern_liquidity/breakouts.py`, `pattern_liquidity/analysis.py`
* **Mechanism:** A breakout requires a candle close beyond the reference boundary (`close > ref.band_high` for bullish). Known at `breakout_candle.open + interval`.

### 3.2 Retest Observation and Held Confirmation
* **File:** `pattern_liquidity/retests.py::detect_retest`
* **Mechanism:** Evaluates subsequent candles (`elapsed >= 1`). A touch is registered if `low <= band_high + tolerance`. A hold is registered if `close > level + tolerance_band(level, retest_hold_pct)`. Both states can occur on the same bar.

### 3.3 Setup Qualification
* **Files:** `setup_qualification/engine.py`, `setup_qualification/rules.py`
* **Mechanism:** Re-evaluated at every closed bar. Requires `held_retest`, `relative_volume >= 1.0`, `ATR_percent <= 10.0`, and `close >= ref.band_high`.

### 3.4 Entry Planning
* **Files:** `trade_planning/planner.py`, `trade_planning/levels.py`
* **Mechanism:** 
  * `_resolve_entry`: Strictly the decision-time close (`volatility.latest_close`).
  * `_resolve_stop`: Reference `band_low` (with zero buffer).
  * `_select_targets`: Nearest structural levels above entry.
  * `_apply_minimum_r`: Retains targets with $R \ge 1.0$.

### 3.5 Forward Ledger Freezing
* **File:** `forward_testing/service.py::_build_paper_plan`
* **Mechanism:** Freezes the first `PLANNABLE` plan for each setup instance, provided the instrument-wide single active paper slot is unoccupied.

### 3.6 Outcome Resolution
* **File:** `journaling/observation.py`
* **Mechanism:** Scans subsequent 1H and 1M candles for touches. If entry and stop touch within the same 1-minute bar, records `AMBIGUOUS`.

---

## 4. Key Architectural Distinctions

### Dashboard Setup Zone vs. Frozen Setup Reference
* **Dashboard Display Zones:** Assembled dynamically by `web/zone_presentation.py::present_zones` from Step 3 support/resistance swing clusters (`context.levels.zones`). They are merged, re-classified relative to current price, and faded after 48 candles.
* **Frozen Setup Reference:** In `pattern_liquidity/references.py`, a `Reference` is an immutable snapshot (`id`, `band_low`, `band_high`, `known_at`, `type`).
* **Operational Hazard:** A single breakout bar can penetrate both a point-level swing high (`band_low == band_high`) and a wider S/R zone. Sibling setups are created. Because setups are sorted by `(created_at, setup_id)` where `setup_id` is a SHA-256 hash, an arbitrary hash tie-break determines whether the paper slot receives a wide zone stop or a microscopic point stop.

### Anti-Lookahead Scoping
* Within the isolated Step 6 planning module, `_future_violations` and `is_available_at` enforce that candidate levels and seed events must satisfy `known_at <= as_of`.
* **Important qualification:** Sweeping claims of "zero look-ahead leakage across the entire system" cannot be supported:
  - Cross-timeframe reads across 4H, 1H, 15M, and 5M in `multi_timeframe/service.py` are not wrapped in an atomic cross-table SQLite snapshot transaction. Concurrent ingestion writes can result in mixed-boundary states.
  - As established in `docs/decision-invariance.md`, truncation of past history alters reference catalog generation and setup eligibility.
  - Production concurrency and database state on the operator's Mac remain unverified.

### Waiting for Limit Entries
* BRAIN **cannot** currently wait for a limit order at the broken level or reference band. `planner.py::_resolve_entry` is hard-coded to the latest closed candle's close, enforced by unit tests (`test_entry_is_always_the_decision_time_close`).

---

## 5. Economic Viability: Measured Costs vs. Illustrative Assumptions

### 5.1 Measured System Facts
* **Trade 2 Risk:** $8.32 per BTC, which is **0.996 bps** (0.00996%) of the entry price ($83,529.47).
* **BRAIN Planner Assumptions:** `friction_assumptions_json` defaults to zero unless configured. BRAIN planned Trade 2 assuming zero commission, zero spread, and zero slippage.
* **Microstructure Noise:** On BTC/USDT 1-minute data, normal candle high-low spans range between $20 and $100. Because $8.32 sits far inside this noise envelope, `journaling/observation.py` triggered `ENTRY_AND_EXIT_SAME_CANDLE` ambiguity.

### 5.2 Illustrative Friction Model (Assumed, Not Account-Specific)
*The following parameters are illustrative market friction assumptions for institutional vs. retail crypto trading. They do not represent the operator's actual Binance VIP tier, account fee schedule, or executed fills:*

| Cost Component | Assumed Institutional Futures | Assumed Retail Spot |
|---|---|---|
| **Maker / Taker Commission** | 2 bps maker / 4 bps taker (6 bps round-trip) | 10 bps maker / 10 bps taker (20 bps round-trip) |
| **Bid-Ask Spread** | ~0.5 to 1.5 bps ($0.50 to $1.25) | ~1.0 to 3.0 bps ($0.80 to $2.50) |
| **Stop Slippage** | ~1.5 to 2.5 bps ($1.25 to $2.10) | ~2.0 to 5.0 bps ($1.67 to $4.17) |
| **Total Illustrative Friction** | **~9 to 10 bps ($75 to $85 per BTC)** | **~23 to 28 bps ($190 to $235 per BTC)** |

### 5.3 Economic Assessment of Trade 2
* Even under the lowest-cost institutional assumption (~9 bps), the transaction friction alone is **~9x the entire nominal stop distance** ($8.32 / ~1 bp).
* Under standard retail spot assumptions (~25 bps), transaction friction is **~25x the nominal stop distance**.
* A nominal -1R loss on Trade 2 would translate to an actual realized loss of **-10R to -26R** after friction.
* For expectancy to remain positive, round-trip friction should not exceed 10–15% of nominal risk, requiring a stop of at least **35 to 50 bps** ($290 to $420 on $83.5k BTC), or $\ge 0.5 \times \text{ATR}_{1\text{H}}$.

---

## 6. Close-Based Entry vs. Pullback/Limit Entry

| Characteristic | Close-Based Entry (Current BRAIN) | Pullback / Limit-Style Entry |
|---|---|---|
| **Execution Timing** | Immediate at 1H candle close | Deferred limit order placed at reference band edge |
| **Fill Reliability** | 100% simulated fill at close | Conditional on price revisiting the level |
| **Execution Cost** | High (market order into momentum) | Low (limit order, potential maker rebate) |
| **Risk / Stop Size** | Wide and volatile (`entry - stop`); sensitive to bar size | Tight and controlled (`limit_price - stop`); optimal R:R |
| **Failure Modes** | **Severe chasing:** buys local top of impulse; stopped by normal pullbacks. | **Missed trades:** strong runaway moves leave limit orders unfilled. |
| **Adverse Selection** | Low (fills on all moves, including winning runners) | **High:** limit orders that fill are often those where order flow aggressively blows through the level. |
| **State Tracking** | Simple: immediate outcome monitoring | Complex: requires managing `PENDING_LIMIT`, timeouts, and cancellation on invalidation (`ENTRY_NOT_REACHED`). |

---

## 7. Categorization: Defects vs. Deliberate Design Choices

### Deliberate Strategy Design Choices
1. **Decision-Time Close Entry:** Explicitly intended in `planner.py::_resolve_entry` to avoid historical hindsight or flattering R:R with expired prices.
2. **Raw Reference Stop (`StopBufferMode.NONE`):** Default policy in `PlanningParameters` places stops at the structural boundary.
3. **Double State Progression on Retests:** `detect_retest` intentionally emits both `"observed"` and `"held"` if a single candle satisfies both touch tolerance and hold distance.
4. **Independent Ledger Runner:** `forward_testing/` runs independently of `multi_timeframe/` (the hierarchy runner).

### Deficiencies and Latent Defects
1. **Missing Economic Risk Floor:** `planner.py::_resolve_stop` permits any `risk > 0`. There is no floor based on ATR, basis points, or friction.
2. **Excessive Retest Tolerance (0.2%):** Allows price $167 away on BTC to count as a "touch".
3. **Missing Entry Extension Gate:** No check prevents entry when the close has extended multiple ATRs away from the reference level.
4. **Deterministic Tie-Break Flaw Among Sibling Setups:** Sibling setups from the same breakout bar are sorted by hash ID (`setup_id`), arbitrarily deciding which stop distance occupies the single instrument slot.

---

## 8. Verified Read-Only Evidence from Mac SQLite Database

The repository contains no real market data (`data/` is empty). The following read-only SQLite queries have been **verified against the actual database table definitions** (`src/trading_assistant/forward_testing/tables.py` and `migrations/versions/`):

### 8.1 Paper Plans Query
*(Verified table: `forward_paper_plans`. Note column names `targets_json` and `target_r_json`, not `plan_targets`)*
```sql
SELECT paper_plan_id, setup_id, plan_as_of, recorded_at, entry_level, stop_level, 
       invalidation_level, risk_per_unit, targets_json, target_r_json, version_fingerprint
FROM forward_paper_plans 
WHERE plan_as_of IN ('2026-10-11 17:00:00', '2026-10-11 22:00:00');
```

### 8.2 Observations Query
*(Verified table: `forward_observations`. Note column names `passed_rules_json` and `failed_rules_json`, not `passed_rules`)*
```sql
SELECT observation_id, setup_id, setup_reference_id, setup_state, passed_rules_json, 
       failed_rules_json, pending_rules_json, no_trade_reason, plan_state, setup_json
FROM forward_observations 
WHERE as_of IN ('2026-10-11 17:00:00', '2026-10-11 22:00:00');
```

### 8.3 1H OHLCV Candles Query
*(Verified table: `ohlcv_candles`. Note column name `timestamp`, not `open_time`)*
```sql
SELECT timestamp, open, high, low, close, volume 
FROM ohlcv_candles 
WHERE exchange = 'binance' AND symbol = 'BTC/USDT' AND timeframe = '1h'
  AND timestamp BETWEEN '2026-10-11 14:00:00' AND '2026-10-11 23:00:00'
ORDER BY timestamp ASC;
```

### 8.4 Outcome Resolution Query
*(Verified table: `forward_paper_outcomes`. Note column names `status` and `ambiguity_kind`; outcome payload and R metrics are in `payload_json`)*
```sql
SELECT outcome_id, paper_plan_id, sequence, status, entry_reached, entry_ordered, 
       entry_timestamp, stop_reached, ambiguous, ambiguity_kind, payload_json, recorded_at
FROM forward_paper_outcomes 
WHERE paper_plan_id IN (
    SELECT paper_plan_id FROM forward_paper_plans 
    WHERE plan_as_of IN ('2026-10-11 17:00:00', '2026-10-11 22:00:00')
)
ORDER BY paper_plan_id, sequence ASC;
```

---

## 9. Evaluation of Candidate Safeguards & Assessment of Trade 1

### 9.1 Would an Entry-Extension Gate Have Prevented Trade 1?
* **Trade 1 Parameters:**
  - Reference level: $83,606.01
  - Decision candle close (entry): $83,916.01
  - Nominal entry extension: $83,916.01 - $83,606.01 = **$310.00**.
  - Relative extension: $\frac{310.00}{83606.01} = \mathbf{0.37\% \text{ (37 basis points)}}$.
* **ATR Evaluation:**
  - On BTC/USDT 1H at ~$83,600, 1H ATR is typically between $400 and $600.
  - If 1H ATR was $450: the extension was $\frac{310}{450} = \mathbf{0.69 \times \text{ATR}}$.
  - If 1H ATR was $550: the extension was $\frac{310}{550} = \mathbf{0.56 \times \text{ATR}}$.
* **Assessment:**
  - A standard extension gate of `max_entry_extension_atr_multiple = 1.5` **would NOT have prevented Trade 1**, because $0.69 < 1.5$.
  - Even a `1.0 ATR` gate would not have prevented Trade 1.
  - Only a strict gate ($\le 0.5 \text{ ATR}$ or $\le 25 \text{ bps}$) would have blocked Trade 1.
* **Critical Architectural Note: Extension Gate $\neq$ Waiting for a Pullback:**
  - An entry-extension gate is solely an **exclusion filter**. If price has extended beyond the threshold, BRAIN emits a `NO_PLAN` with refusal reason `entry_extended_beyond_reference`.
  - It **does not** place a limit order, nor does it wait for a pullback to occur. The setup is simply abandoned.

---

## 10. Candidate Solutions and Trade-Offs

### Option 1: Defensive Gates on Close Entry (Recommended First Step)
* **Design:** Retain the close-based entry model, but add two defensive gates to `trade_planning/planner.py`:
  1. `MinStopDistanceGate`: Require $\text{risk\_per\_unit} \ge \max(0.5 \times \text{ATR}, 0.0035 \times \text{entry})$ (~35 bps / ~$300 on BTC). Plans failing this are refused with `stop_distance_below_economic_minimum`.
  2. `MaxEntryExtensionGate`: Require $(\text{entry} - \text{ref.band\_high}) \le 1.5 \times \text{ATR}$. Plans where price has extended too far are refused with `entry_extended_beyond_reference`.
* **Trade-Offs:**
  * *Pros:* Simple, zero execution complexity, preserves deterministic completed-candle paradigm, eliminates micro-stops and severe chasing immediately.
  * *Cons:* Reduces trade frequency; does not improve entry price on shallow retests.

### Option 2: Pullback / Limit-Style Order Model
* **Design:** Introduce a pending order state (`PlanState.PENDING_LIMIT`). Propose entry at `ref.band_high`. Monitor subsequent candles for fills before invalidation.
* **Trade-Offs:**
  * *Pros:* Optimal theoretical R:R; structural stops.
  * *Cons:* Misses strong momentum moves; introduces adverse selection; complex state machine.

### Option 3: Lower-Timeframe Hierarchy Execution
* **Design:** Gate 1H setups through 5M execution structures (`multi_timeframe/execution.py`), requiring a 5M pullback and reversal before planning.
* **Trade-Offs:**
  * *Pros:* Refines timing without guessing static limit prices.
  * *Cons:* Requires cross-timeframe runner serialization and full hierarchy stability.

---

## 11. Executable Synthetic Test Demonstrating the Tiny-Stop Issue

An executable synthetic test reproducing the Trade 2 tiny-stop planning pass is provided at [`docs/audits/entry-quality-probes/test_tiny_stop_planning.py`](entry-quality-probes/test_tiny_stop_planning.py) (and wrapped by [`probe9.py`](entry-quality-probes/probe9.py)).

### Test Execution Proof
```
$ python docs/audits/entry-quality-probes/test_tiny_stop_planning.py
PASS: test_tiny_stop_distance_accepted_by_planner
  Entry: $83529.47
  Stop:  $83521.15 (Risk: $8.32 / 0.996 bps)
  Target: $83572.96 (Reward: $43.49)
  R-Multiple: 5.22716346R
  State: PLANNABLE

$ pytest docs/audits/entry-quality-probes/test_tiny_stop_planning.py -v
docs/audits/entry-quality-probes/test_tiny_stop_planning.py::test_tiny_stop_distance_accepted_by_planner PASSED [100%]
```

### Core Verified Assertions
```python
assert plan.state is PlanState.PLANNABLE
assert plan.entry.value == Decimal("83529.47")
assert plan.stop.value == Decimal("83521.15")
assert plan.risk_per_unit == Decimal("8.32")
assert (plan.risk_per_unit / plan.entry.value) * Decimal(10000) < Decimal("1.0")  # < 1 bp
assert plan.targets[0].reward_per_unit == Decimal("43.49")
assert plan.targets[0].r_multiple == Decimal("5.22716346")
assert rule_for(plan, "positive_risk").outcome is RuleOutcome.PASS
assert rule_for(plan, "minimum_r_multiple").outcome is RuleOutcome.PASS
```

---

## 12. Minimal, Testable Repair Plan & Parameter Unit Specifications

When implementation is authorized, changes must strictly adhere to the codebase's existing parameter unit conventions:

### Parameter Unit Checking
In `PlanningParameters` (`trade_planning/parameters.py`), percentage parameters are stored as **percentage numbers (0 to 100)**, not decimal fractions:
* Current: `stop_buffer_percentage: Decimal = Decimal("0.1")` $\implies$ 0.1% (10 bps).
* **35-Basis-Point Minimum Risk Floor:**
  - 35 basis points is $0.35\%$.
  - Therefore, the parameter **must be specified as**:
    ```python
    min_stop_distance_percentage: Decimal = Decimal("0.35")  # 0.35 percent = 35 bps
    ```
  - **Caution:** If erroneously specified as `Decimal("0.0035")`, `tolerance_band` would divide by 100, resulting in $0.0035\% = 0.35 \text{ bps}$ (a 100-fold error).

### Proposed Versioned Steps
1. **Preserve Historical Immutability:**
   * Update `PLANNING_RULES_VERSION = "trade-planning-v3"`.
   * Stored `forward_paper_plans` rows remain untouched; new plan identities incorporate the v3 fingerprint.
2. **Add Parameters to `PlanningParameters`:**
   ```python
   min_stop_distance_atr_multiple: Decimal = Decimal("0.5")
   min_stop_distance_percentage: Decimal = Decimal("0.35")  # 35 bps
   max_entry_extension_atr_multiple: Decimal = Decimal("1.5")
   ```
3. **Add Rule Checks to `planner.py`:**
   * In `_resolve_stop`: require `risk >= max(min_stop_atr, min_stop_pct)`. Refusal reason: `stop_distance_below_economic_minimum`.
   * In `_resolve_entry`: require `(entry - ref.band_high) <= max_extension_atr`. Refusal reason: `entry_extended_beyond_reference`.
4. **Synthetic Test Coverage:**
   * Verify sub-35-bps stops are refused.
   * Verify extended continuation bars are refused.
   * Verify v2 vs. v3 plan identity isolation.
