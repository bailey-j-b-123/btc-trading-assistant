# Phase 3 — Trustworthy Paper-Trade Outcome Evaluation: Implementation Report

Date: 2026-10-10 · Branch: `arena/d4d4ae62-btc-trading-assistant` ·
Base: `main` after merged PRs #34 (honest paper counting, `forward-ledger-v3`)
and #35 (Binance-only active market data).

This report is the evidence-based record required by the audit's coordinated
repair plan, §8 Phase 3 option **3B**: an optional 1-minute series used
**only** for ordering events inside `_evaluate`, with the same plan levels and
a new `OUTCOME_RULES_VERSION`. Nothing here changes planning, strategy
parameters, fills, sizing, or any trading capability.

---

## 1. What the investigation found

### 1.1 The ordering problem

Step 7's `observe_outcome` walks base-timeframe candles and applies exact
touch rules. When one candle's OHLC range contains the entry **and** an exit
level (or the stop and a target), the candle alone cannot say which touch
came first, so the walk stops at `AMBIGUOUS` — deliberately unscored, never
resolved in the favourable direction. The audit's Mac database confirmed the
consequence: 21 `trade-planning-v1` paper plans, **all** frozen as
`AMBIGUOUS` (`entry_and_exit_same_candle`), `entry_ordered = 0`.

### 1.2 Evidence available to order those events

Genuine ordering evidence exists only as finer-granularity price history.
The repository investigation established:

* **No stored 1-minute candles exist anywhere in the repository or its data
  directories** (`data/raw/`, `data/processed/` are empty; no SQLite
  databases are committed). The 21 historical ambiguous candles therefore
  **cannot be ordered from evidence currently in the repo**, and this change
  refuses to invent an ordering for them. They remain frozen `AMBIGUOUS`
  under `journal-outcome-v1`.
* **No new chronological evidence was fabricated.** The sandbox has no
  Binance connectivity, and even with it, fetching 1-minute candles older
  than Binance's retained kline history could not be *confirmed* genuine.
  The correct engineering answer — and the one implemented — is to resolve
  future ambiguities **at the moment they occur**, when closed 1-minute
  candles are still guaranteed downloadable through the audited Step 2
  pipeline.
* Where sufficient chronological evidence **does** exist (a complete,
  consistent run of closed 1-minute candles covering the ambiguous base
  candle), the event ordering inside that candle is knowable to the minute:
  each 1-minute candle's range constrains which levels were touched in that
  minute, and first-touch ordering across minutes is provable. Where it does
  not (a gap, a missing span, an inconsistent candle, or entry and stop
  co-touched inside a single 1-minute candle), the outcome stays explicitly
  unscored `AMBIGUOUS`. This is the policy now encoded in
  `journal-outcome-v2`.

## 2. What changed

### 2.1 New evaluation-policy version (no silent rewrites)

* `journaling/parameters.py` — `OUTCOME_RULES_VERSION` remains
  `journal-outcome-v1` (the default; historical behaviour byte-identical).
  New constant `OUTCOME_RESOLUTION_RULES_VERSION = "journal-outcome-v2"`.
* The forward runner is the only caller that opts in: it records outcomes
  with `OutcomeParameters(rules_version="journal-outcome-v2")` and stamps the
  same string into the cycle's `outcome_observation_rules` version
  fingerprint. v1 and v2 outcomes can therefore never be mixed in reports or
  in the ledger: identity is content.
* `journaling/types.py` — `OutcomeObservation` gained resolution fields
  (`resolution_timeframe`, `resolution_attempted`, `resolution_used`,
  `resolution_reason`, expected/present/missing candle counts, missing
  minute ranges). `to_json_dict()` **strips all resolution keys when
  `observation_rules_version == "journal-outcome-v1"`**, so every v1 payload
  and every v1 observation id is byte-for-byte identical to before Phase 3.
  Existing rows on any database (including the Mac database) are never
  rewritten, re-observed, or superseded: `_update_outcomes` skips any plan
  whose latest outcome is not a v2 ambiguity pending evidence.

### 2.2 Minute resolution inside `_evaluate` (ordering only)

* `journaling/observation.py` — new pure function
  `_attempt_minute_resolution(...)`: for an ambiguous base candle, replays the
  **identical** Step 7 touch/ordering rules over the candle's stored 1-minute
  candles (grouped per base candle, validated: exchange, symbol,
  timeframe `1m`, minute alignment, no duplicate opens, span membership,
  Decimal OHLC). Ordering semantics are exactly v1 semantics one timeframe
  down:
  * a minute touching entry **without** stop/target orders the entry first;
  * a stop or target touched in an earlier minute than the entry is a
    pre-entry touch (`INVALIDATED_BEFORE_ENTRY`, or a target touch that does
    not count toward the entry trajectory — same as v1 site-A/B rules);
  * entry plus stop (or entry plus target) co-touched **inside the same
    1-minute candle** remains `AMBIGUOUS` — `resolution_same_minute_ambiguous`,
    final, never re-checked;
  * a non-terminal resolution (e.g. one target reached, entry ordered) hands
    control back to the base-timeframe walk from the *next* candle — 1-minute
    evidence never extends or shortens the observation window.
* Resolution is consult-first: it is attempted only on candles the base walk
  already declared ambiguous under v1 rules. It can only resolve or confirm
  ambiguities; it can never create a touch the base candle did not already
  contain.

### 2.3 Evidence acquisition (genuine candles only)

* `forward_testing/service.py` — `_observe_with_resolution` re-evaluates an
  ambiguous window up to horizon+1 times; on each pass, every still-ambiguous
  candle without stored minutes triggers `_ensure_resolution_candles`, which
  downloads exactly `[candle_open, candle_open + interval − 1m]` of closed
  `1m` candles through the **unchanged** Step 2 pipeline: date-bounded
  millisecond cursor, 1000-candle pages, Decimal/OHLC/alignment validation,
  closed-candle exclusion, raw archiving, exact missing-candle reporting.
  Download failures (network, rate limits, gaps) never abort the pass and
  never fabricate data — the outcome simply records
  `resolution_no_candles` / `resolution_coverage_incomplete` and stays
  `AMBIGUOUS`, eligible for re-check on a later pass.
* Consistency guard: stored minutes must reproduce the base candle
  (first open, last close, extremes, full contiguous coverage); any conflict
  yields `resolution_consistency_conflict` and the ambiguity stands.

### 2.4 Config and guards

* `config.py` — `supported_timeframes` default is now
  `("1m", "5m", "15m", "1h", "4h")`: `1m` is acquisition/storage-only
  ordering evidence.
* The forward runner **refuses** `1m` as a base/planning timeframe
  (`resolve_instrument` guard: "ordering evidence only"); a 1-minute plan
  would have no finer evidence to order it.
* PR #35 isolation preserved: plans recorded under a legacy exchange
  identity are observed only against that exchange's candles and are never
  re-checked for Binance 1-minute evidence (`_resolution_recheck_due`
  requires `plan.exchange == settings.exchange`).
* Step 11 historical validation remains `journal-outcome-v1` unchanged.

### 2.5 Reporting

* `forward_testing/reporting.py` — the limitations block now states the
  1-minute evidence policy alongside the existing same-candle-ambiguity
  limitation. No denominator changed: PR #34's instrument-wide one-active
  rule, the occupancy horizon for unscored ambiguities, same-cycle plan-id
  locks, and the `entry_reached_rate` exclusions all behave exactly as in
  `forward-ledger-v3`. A *resolved* outcome is a clean terminal that settles
  and releases the slot through the existing settlement path.

## 3. Regression tests

New deterministic suites (all offline, temporary migrated SQLite, fake
exchange):

* `tests/test_outcome_resolution.py` (23 tests) — pure v2 semantics: v1
  baselines unchanged; v2 without evidence identical to v1; entry-before-stop
  and target-after-entry resolution; pre-entry stop across an inter-minute
  gap (`INVALIDATED_BEFORE_ENTRY`); pre-entry target touch followed by an
  ordered entry continuing to targets; stop-before-target and
  target-before-stop in both directions; two-candle multi-consultation;
  same-minute co-touch refusal; missing-span refusal; consistency-conflict
  refusal; 5-minute alignment; rejection of 1-minute plans; window/span
  validation; HTF-gap precedence; deterministic ids; v1/v2 payload
  round-trips.
* `tests/test_forward_phase3.py` (13 tests) — ledger end-to-end: resolved
  `STOPPED` releases the one-active slot; missing 1-minute evidence keeps
  unscored `AMBIGUOUS`, occupies the slot (Phase 2 preserved) and blocks a
  second paper trade with `PAPER_TRADE_ACTIVE_REASON`; same-minute ambiguity
  is final and never re-checked; late evidence appends a superseding version
  without rewriting the earlier row; same-window re-check waits for evidence
  with deterministic dedupe (no duplicate versions); restart/duplicate
  passes record nothing new; two ambiguous candles each fetch their own
  60-minute window (120 stored candles); cycle stamps carry
  `journal-outcome-v2` + `forward-ledger-v3` + `trade-planning-v2` with the
  1R floor intact; legacy v1 `AMBIGUOUS` rows are never re-observed or
  superseded; legacy-exchange plans never receive Binance minutes; `1m` is
  refused as a forward base timeframe while 5m/15m/1h/4h stay supported;
  re-check eligibility semantics (missing-evidence only, active exchange
  only, v1 never).

Full-suite results: see the PR description (backend + frontend, all passing).

## 4. What remains ambiguous

1. **The 21 historical Mac-database outcomes stay `AMBIGUOUS` (`journal-outcome-v1`).**
   There is no stored 1-minute evidence for them in this repository, and this
   phase refuses to order them from anything but genuine confirmed candles.
   They are frozen history, correctly excluded from every rate.
2. **Same-minute co-touches are permanently ambiguous** at 1-minute
   granularity. Ordering them would require tick/order-book data, which this
   system deliberately does not model.
3. **Missing or inconsistent 1-minute spans keep outcomes `AMBIGUOUS`**
   (`resolution_no_candles`, `resolution_coverage_incomplete`,
   `resolution_consistency_conflict`). These are re-checkable: if genuine
   evidence arrives in a later pass, a new superseding version records the
   resolution.
4. **Step 11 historical validation is still `journal-outcome-v1`.** Applying
   resolution there would require confirmed historical 1-minute candles for
   each replayed window and is explicitly out of scope here.
5. **1-minute candles order events to the minute, not within a minute.**
   Resolved event timestamps are 1-minute bar open times; the recorded
   `resolution_timeframe: "1m"` states this mixed granularity explicitly.

## 5. Evidence still required before profitability can be evaluated

* A meaningful sample of **forward** paper plans recorded under
  `trade-planning-v2` (mandatory 1R) and observed under
  `journal-outcome-v2`. The 21 v1 rows predate both policies and are
  excluded by design — they can never become a win-rate numerator.
* Sufficient closed-candle history for the forward ledger to elapse
  observation horizons; outcome counts must be judged against the reporting
  denominators (paper plans), never against raw candle counts.
* Genuine 1-minute candle availability for every ambiguous base candle the
  forward runner encounters (this change acquires them at occurrence time,
  which is the only point where availability is guaranteed).
* An explicit, separately approved decision on friction assumptions for any
  evaluation report (audit Phase 4); until then raw observational R remains
  the only hypothetical quantity, and it is still not realised P&L.
* Continued exclusion of `AMBIGUOUS`, `INCOMPLETE_DATA`, `OPEN_AT_CUTOFF`,
  and `STOPPED_AFTER_TARGETS` (no partial-exit policy) from any R or
  hit-rate aggregation.

Nothing in this phase converts an unscored outcome into a win or a loss,
synthesizes a fill, changes a plan, or executes anything. The ledger can now
prove more orderings than before, and it says exactly — on every row — which
evidence decided it.
