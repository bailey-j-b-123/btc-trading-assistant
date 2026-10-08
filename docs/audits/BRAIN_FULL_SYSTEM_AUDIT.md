# BRAIN — Full System Audit and Coordinated Repair Plan

**Repository:** `bailey-j-b-123/btc-trading-assistant`  
**Audit type:** read-only diagnostic. No application code, tests, configuration, dependencies, or databases were modified as part of this work.  
**Date:** 2026-10-08  
**Code revision inspected:** `cc86f4b` (`main`), which merged PR #31 (`trade-planning-v2` + `forward-ledger-v2`).  
**Mac SQLite database:** `data/trading_assistant.sqlite3` was **not** opened in this environment. Counts and field values below are the operator-verified evidence supplied with the audit request.

---

## 1. Purpose and method

Two completed audits are consolidated here:

1. **Full pipeline diagnostic** — why paper-trade plans look questionable and why recorded outcomes cannot establish reliable trading performance.
2. **Stage 1 1R investigation** — the exact path from target selection through reward-to-risk validation, plan state, and forward-ledger persistence, given that all 21 stored paper plans are `trade-planning-v1`.

The intended architecture was verified before classifying anything as a bug. Strategy families, swing detection, and liquidity rules were **not** assumed to be the cause and are **not** redesigned here.

**Classification key**

| Label | Meaning |
|---|---|
| **Confirmed defect** | Present in this checkout and/or proven by the combination of this code plus verified DB evidence. |
| **Confirmed v1-era behaviour** | Correct under `trade-planning-v1`; contradicts the later v2 policy. |
| **Intended behaviour** | Matches documented contracts in this repository. |
| **Hypothesis** | Plausible; needs a local DB check or a policy decision before treating it as a bug. |

---

## 2. Operator-verified evidence (Mac DB)

These facts were supplied by the operator from the local SQLite file. They were **not** re-queried here.

| Fact | Value |
|---|---|
| Forward cycles | 49 |
| Forward observations | 749 |
| Paper plans | 21 |
| Paper outcomes | 21 |
| Planning rules version on all 21 plans | `trade-planning-v1` |
| Plans with every retained target below 1R | 20 / 21 |
| Plan state | all 21 `PLANNABLE` |
| Stored R vs independently recalculated R | match (no arithmetic error) |
| Configured minimum as understood by the operator | 1R |
| Outcomes | all 21 `AMBIGUOUS` |
| `ambiguity_kind` | `entry_and_exit_same_candle` |
| `entry_ordered` | 0 on all 21 |
| Grouping | 21 plans form **five** groups sharing entry time, direction, entry price and targets, with different setups or stop-losses |
| Observation `plan_state` | 63 `PLANNABLE`, 18 `NO_PLAN`, 668 unset |
| `journal_records` / `journal_decisions` / `journal_outcomes` | empty |
| Paper observation horizon | 20 candles |

---

## 3. Intended architecture

BRAIN is a **closed-candle observation system**, not a broker. There is no order, fill, account, position, or size object anywhere in the pipeline.

| Step | Module | Role | Durable writes |
|---|---|---|---|
| 2 | `market_data` | Public OHLCV, closed candles only | `ohlcv_candles` |
| 3–4 | `market_structure`, `pattern_liquidity` | Structure and pattern/liquidity evidence | none (ephemeral) |
| 5 | `setup_qualification` | Setup qualification replay | none |
| 6 | `trade_planning.plan_trade` | **Only** authority on entry, stop, targets, R | none |
| 7 | `journaling` | Human journal + OHLC `observe_outcome` | `journal_*` **only** when the dashboard records a decision |
| 8 | `statistics` | Derived statistics | reads **journal only** |
| 9 | `ai_explanation` | Explanation text | fingerprints on forward cycles |
| 11 | `historical_validation` | Historical cohorts | derived reports |
| 12 | `forward_testing` | Live forward paper ledger | `forward_*` automatically |
| 13 | `multi_timeframe` | Hierarchy gate for dashboard wording | none; **does not gate paper plans** |

Documented invariants that this checkout implements:

- Forming candles are never analysed (`candles_closed_by`, `latest_closed_candle_open_time`).
- Planning `as_of` is a **candle-close boundary**.
- The outcome window starts at that close, which is the **next** candle’s open. The decision candle is not scored as a fill.
- Step 12 adds **no** planning threshold. It stores whatever Step 6 called `PLANNABLE`.
- Same-candle entry and exit is `AMBIGUOUS`; the favourable result is never chosen.
- `PLANNABLE` means “a complete deterministic proposal exists”, never “approved”, “filled”, or “profitable”.
- There is no second “executable” state. Journal `ACCEPTED` is the human gate and was unused on the Mac.
- Configuration defaults are explicit and **uncalibrated**.

---

## 4. End-to-end path (setup → paper row → outcome)

```
closed candle (Step 2)
  → Steps 3–4 structure / patterns
  → Step 5 QUALIFIED SetupResult
  → plan_trade()                          # only authority on R
        structural_target_candidates()    # nearest-N, trade-side filter
        _select_targets()                 # attach reward and R
        _apply_minimum_r()                # v1: no-op if min_r_multiple is None
                                          # v2: mandatory floor of 1
        state = PLANNABLE iff reasons == ()
  → ForwardTestService._record_cycle
        plan only if QUALIFIED and window complete
        paper plan only if plan.state is PLANNABLE
  → forward_paper_plans                   # no second R check
  → later closes: observe_outcome         # Step 7 OHLC semantics
  → forward_paper_outcomes                # AMBIGUOUS is final
```

---

## 5. Confirmed findings (ranked by severity)

### Finding 1 — Every paper outcome is frozen as `AMBIGUOUS` on the first future candle

**Classification:** **confirmed defect of composition.** Each individual rule is intended; together they make the paper book unscorable.

**Effect:** All 21 paper trades are terminal non-results. Raw observational R, stop rate, target-hit rate, and any win-rate analogue have a clean sample of **zero**. Later candles are never considered. This is why recorded outcomes cannot establish reliable trading performance.

**Code path**

1. Entry is the already-closed close at planning `as_of`:

```678:720:src/trading_assistant/trade_planning/planner.py
    """Entry is always the actual decision-time price.
    The anchor is the latest closed candle's close at the planning ``as_of``.
    ...
    """
    value, source_id, observed, confirmed = plan_close_level(...)
```

Helper: `src/trading_assistant/trade_planning/levels.py` `plan_close_level` (approx. lines 126–152).

2. `as_of` is a candle-close boundary, not an open:

```136:138:src/trading_assistant/setup_qualification/service.py
        last_open = latest_closed_candle_open_time(as_of, timeframe)
        if last_open + interval != as_of:
            raise ValueError("as_of must be a base candle-close boundary")
```

Closed-candle prefix: `src/trading_assistant/market_structure/candles.py` lines 77–90 (`timestamp + interval <= as_of`).

3. Outcome evaluation starts at `plan.plan_as_of`. Because close(N) = open(N+1), the first candle evaluated is the **next** candle. Same-cycle observation is skipped:

```2183:2211:src/trading_assistant/forward_testing/service.py
            horizon_last_open = plan.plan_as_of + interval * (
                plan.observation_horizon_candles - 1
            )
            ...
            observed_through = min(horizon_last_open, last_closed_open)
            if observed_through < plan.plan_as_of:
                continue
            ...
            candles = self.candles.get_candles(
                ...
                start_time=plan.plan_as_of,
                end_time=observed_through,
            ).candles
```

Historical validation uses the same window rule (`historical_validation/service.py` approx. 575–577: “A plan at boundary T can only use a future candle opened at T”).

4. On that first future candle, entry is touched if `low <= entry <= high` (`journaling/observation.py` `_evaluate`). In a continuous BTC book the next candle almost always trades through the previous close, so entry is touched on bar 1.

5. If that **same** candle also touches stop or any target, evaluation stops and never picks a winner:

```316:336:src/trading_assistant/journaling/observation.py
        if not entry_ordered:
            if entry_touch and (stop_touch or target_touch_list):
                # Same-candle entry and exit: the order is unknowable from OHLC.
                ambiguous = True
                ambiguity_kind = ENTRY_AND_EXIT_SAME_CANDLE
                ...
                entry_reached = True
                ...
                terminal = OutcomeStatus.AMBIGUOUS
                break
```

Constant: `ENTRY_AND_EXIT_SAME_CANDLE = "entry_and_exit_same_candle"` at `observation.py` lines 75–76.  
`entry_ordered` is left `False` (only `entry_reached` is set). That matches all 21 rows.

6. `AMBIGUOUS` is a **final** status. Tracking stops; later candles cannot resolve it:

```268:286:src/trading_assistant/forward_testing/models.py
FINAL_OUTCOME_STATUSES = frozenset(
    {
        OutcomeStatus.INVALIDATED_BEFORE_ENTRY,
        OutcomeStatus.STOPPED,
        OutcomeStatus.STOPPED_AFTER_TARGETS,
        OutcomeStatus.TARGETS_REACHED,
        OutcomeStatus.AMBIGUOUS,
    }
)
...
    if status in FINAL_OUTCOME_STATUSES:
        return True
```

Reporting then excludes these rows from raw R (`historical_validation/metrics.py` `r_exclusion`, approx. 175–177, reason `AMBIGUOUS_OUTCOME`). Clean ordered-entry rates use `entry_ordered` (`forward_testing/reporting.py` approx. 364–381), so those denominators are also 0.

**What is intended vs broken**

| Piece | Status |
|---|---|
| Conservative OHLC same-candle rule | Intended (`tests/test_forward_service.py` `test_same_candle_entry_and_exit_is_ambiguous_never_favourable`, approx. line 886) |
| Entry = last close | Intended v2 planning policy |
| Not scoring the decision candle | Intended; timestamps match |
| Treating `AMBIGUOUS` as settled (never reinterpret as a win) | Intended |
| **Net result: 21/21 frozen ambiguous** | **Confirmed defect of the system as wired** |

Sub-1R targets make this almost certain: remaining target distance is smaller than stop distance, and the first bar already contains the entry. Swing/liquidity bugs are not required.

**Higher-resolution data:** **hypothesis, not a confirmed bug.** There is no 1m/tick outcome path. 1m could order some same-bar events; it would not by itself fix v1 sub-1R plans or correlated duplicates. Treat as a later, explicit policy (1m **only** to order events, never to re-plan).

**Local verification (Mac):** for one paper plan, compare `plan_as_of`, the first outcome `entry_timestamp`, and that candle’s OHLC versus `entry_level` / `stop_level` / `targets_json`. Confirm the evaluated candle is the next open and that its range contains both entry and stop or a target.

---

### Finding 2 — `AMBIGUOUS` immediately releases the one-active paper-trade lock; 21 plans are not 21 independent bets

**Classification:** **confirmed defect** (identity + settlement interaction).

**Effect:** The instrument-wide “one BTC paper trade at a time” policy does not constrain this book. Each ambiguous plan is treated as finished, so the next cycle may freeze another plan. Performance reporting would double-count correlated ideas if they were ever scored. Today they only inflate `ambiguous_count`.

**Code path**

- Policy text and reason string: `src/trading_assistant/forward_testing/parameters.py` lines 32–50, `PAPER_TRADE_ACTIVE_REASON`.
- Active = latest outcome **not settled**: `ForwardTestService._paper_trade_is_active` (`service.py` approx. 2338–2375).
- Settled includes `AMBIGUOUS` (Finding 1).

Uniqueness is **per setup instance**, not per market opportunity:

```314:320:src/trading_assistant/forward_testing/tables.py
        UniqueConstraint(
            "exchange",
            "symbol",
            "timeframe",
            "setup_id",
            name="uq_forward_paper_plans_setup_instance",
        ),
```

Setup IDs are `fingerprint(instrument, config, family, seed_event_id)` (`src/trading_assistant/setup_qualification/engine.py` approx. 229–231). Different seeds/families at the same close are different paper plans, even with the same entry (the close), direction, and structural targets, and only a different stop.

That matches: **21 plans, five groups sharing entry time / direction / entry price / targets, different setups or stops.**

Reporting denominators are paper plans, not opportunities (`forward_testing/reporting.py` approx. 81–83, 441).

v1 ledgers allowed concurrent paper plans by design (`FORWARD_LEDGER_RULES_VERSION` comments in `parameters.py` 32–37).

**Same-cycle hole (hypothesis until grouped on the Mac DB):** if the first `PLANNABLE` setup in a cycle already has a frozen plan, `_build_paper_plan` reuses it and returns `frozen_plan=None`, so this does **not** set `paper_trade_active = True`:

```1768:1771:src/trading_assistant/forward_testing/service.py
                if frozen_plan is not None:
                    paper_trade_active = True
```

```2106:2110:src/trading_assistant/forward_testing/service.py
        if existing is not None:
            return existing.paper_plan_id, None
        if not allow_new_paper_trade:
            return None, None
```

If that existing plan is already settled-ambiguous, `_paper_trade_is_active` is false, and a **different** setup in the **same** cycle can open a new plan at the same `plan_as_of`.

**Local verification:** group `forward_paper_plans` by `(plan_as_of, direction, entry_level, targets_json)`. If any timestamp has more than one row, the same-cycle guard failed or multiple timeframes raced. Also check `ledger_rules_version` (`forward-ledger-v2` vs v1).

---

### Finding 3 — Sub-1R `PLANNABLE` paper plans were produced by `trade-planning-v1`, whose 1R gate was optional and off by default

**Classification:** **confirmed v1-era behaviour.** Not a bypass of `trade-planning-v2` in this checkout. Stored R matching independent R confirms the numbers are honest v1 output.

**This is the Stage 1 1R root cause.**

#### v1 (what wrote the Mac DB)

Recovered from `dcfeed8` `src/trading_assistant/trade_planning/parameters.py`:

```python
PLANNING_RULES_VERSION = "trade-planning-v1"
...
min_r_multiple: Decimal | None = None   # default: gate OFF
```

Validation ran only if a caller set a value; `None` was legal; there was no floor of 1.

v1 planner:

- `BASE_RULES` **omits** `minimum_r_multiple` (ends at `target_levels_valid`).
- `finalize` appends the rule only when configured:

```python
expected = list(BASE_RULES) + (
    ["minimum_r_multiple"] if self.parameters.min_r_multiple is not None else []
)
```

- `_apply_minimum_r`:

```python
def _apply_minimum_r(context):
    threshold = context.parameters.min_r_multiple
    if threshold is None:
        return          # default path: no filter, no refusal
```

- If the optional gate *was* on and nothing reached it, v1 treated that as **`INVALID`** (`minimum_r_multiple_not_met` was in `INVALID_CODES`), not `NO_PLAN`.

Under default v1 config, any on-side structural target made a **`PLANNABLE`** plan, including 0.3R. Paper plans require only `state is PLANNABLE`. Hence 20/21 sub-1R `PLANNABLE` rows.

**Contradiction with “configured minimum appears to be 1R”:** if v1 had actually been configured with `min_r_multiple=1`, those rows **could not** be `PLANNABLE` (they would be `INVALID`, and the ledger would not freeze a paper plan). The Mac book therefore ran with **`min_r_multiple is None`**. A “configured minimum of 1R” is the **v2** policy in this checkout and README, not the v1 config that wrote the rows.

v1 also had `r_multiple_fallbacks = (Decimal(2),)` — synthetic 2R targets **only when no structural target existed**. The 20 plans have structural targets below 1R, so fallbacks did not apply.

#### v2 (this checkout — already merged as PR #31 / `cc86f4b`)

| Location | What v2 does |
|---|---|
| `src/trading_assistant/trade_planning/parameters.py:28` | `PLANNING_RULES_VERSION = "trade-planning-v2"` |
| `parameters.py:35, 91, 132–138` | `min_r_multiple` defaults to 1; `< 1` is rejected (`MINIMUM_R_MULTIPLE_FLOOR`) |
| `src/trading_assistant/trade_planning/planner.py:117–134` | `minimum_r_multiple` is a **base** rule (always run) |
| `planner.py:76–97` | `minimum_r_multiple_not_met` is **not** `INVALID` |
| `planner.py:189–194` | empty `reasons` → `PLANNABLE`; else `INVALID` or `NO_PLAN` |
| `planner.py:447–453, 862–911` | `_apply_minimum_r`: drop targets with `r < min`; if none left → `NO_PLAN` / `MINIMUM_R_MULTIPLE_NOT_MET` |
| `src/trading_assistant/forward_testing/service.py:1738–1753, 1963–1967` | paper plan only if `plan.state is PlanState.PLANNABLE` |
| `src/trading_assistant/forward_testing/tables.py:254–256` | `paper_plan_id IS NULL OR plan_state = 'PLANNABLE'` |

The forward ledger **still does not enforce 1R**. It never did. It trusts Step 6. That is by design (“Step 12 adds no threshold”). Under v2, Step 6 no longer emits sub-1R `PLANNABLE` plans, so the ledger cannot store **new** ones.

**Intended v2 1R rule (do not invent another):**

1. Take genuine structural candidates (nearest first, cap `max_structural_targets`, default 2).
2. Exclude any with `r_multiple < min_r_multiple` (default 1).
3. **At least one retained target must reach 1R.** If none remain → `NO_PLAN`.
4. Therefore **every retained target** on a `PLANNABLE` plan is ≥ 1R.
5. It is **not** “every originally selected candidate must be 1R”. A 0.5R + 2.0R set becomes `PLANNABLE` with only 2.0R kept.
6. `preferred_r_multiple` (1.5) only sets `preferred_r_multiple_met`. It does not block.

**Are paper plans “preliminary” vs “executable”?** No. In both v1 and v2, `PLANNABLE` **is** the paper-tradeable state. The 21 rows are not drafts that leaked; they are v1 paper observations of plans that v1 considered complete. Journal `ACCEPTED` is a separate unused human ledger.

**Local verification (Mac):** stored `plan_json.config_fingerprint` vs a v1 `PlanningParameters(min_r_multiple=None).fingerprint()` should match default-off, not min=1. After deploying this repo, new paper plans (if any) must show `trade-planning-v2` and retained `target_r_json` ≥ 1.

---

### Finding 4 — Empty journal tables are expected

**Classification:** **intended behaviour.**

**Effect:** Step 8 `/api/statistics` reads only `journal_*` (`src/trading_assistant/statistics/dataset.py` lines 1–15, 40–48). With empty journal tables that report is empty, even though 21 paper outcomes exist. Forward metrics live under the forward/validation APIs.

Journal writes happen only when a human hits Accept/Reject/Skip:

```390:452:src/trading_assistant/web/dashboard_service.py
        """Journal the exact proposal at ``as_of`` and append one decision.
        ...
        """
        ...
        plan = plan_trade(...)
        if plan.state is not PlanState.PLANNABLE:
            raise DashboardError(...)
        record = state.journal.journal_plan(snapshot=snapshot, plan=plan)
```

`ForwardTestService` imports `observe_outcome` and `snapshot_identity` from journaling, **not** `JournalService`. Paper outcomes go to `forward_paper_outcomes`.

Empty `journal_decisions` / `journal_records` / `journal_outcomes` means nobody used the human journal, not that forward recording failed. There is also **no approval gate** on paper trades.

---

### Finding 5 — Observation `plan_state` mix is mostly intended

**Classification:** **intended behaviour**, with one local check remaining.

Operator counts: 63 `PLANNABLE`, 18 `NO_PLAN`, 668 no plan state, 21 paper plans.

Planning runs only for `QUALIFIED` setups on a complete window (`service.py` approx. 1738–1741). Everyone else is stored without a plan:

- `WATCH` and first-time terminal `NO_SETUP` are recorded on purpose (`_should_record_setup`, approx. 1872–1896).
- Incomplete required windows **withhold** Step 6 (`planning_withheld`, approx. 1718–1730).

668 null `plan_state` ≈ live watches + terminals + QUALIFIED-but-withheld. Not a missing 1R check.

63 `PLANNABLE` vs 21 paper plans is expected: one paper plan per `setup_id`, plus refusals while another paper trade is “active” (which, given Finding 2, is only a brief window).

18 `NO_PLAN` is where `minimum_r_multiple_not_met` / `MISSED` would show (`service.py` approx. 1972–1979, 2377–2420). **Local check:** how many of the 18 have `no_trade_reason` = `MISSED — price moved...` vs other gaps.

---

### Finding 6 — Reporting does not invent P&L, and it correctly refuses to score this book

**Classification:** **intended**, with one confirmed metrics defect.

Intended:

- Ambiguous outcomes are excluded from raw R (`historical_validation/metrics.py` `r_exclusion`).
- Stop/target rates use only clean ordered-entry outcomes (`reporting.py` approx. 364–381). With `entry_ordered=0` on all 21, those denominators are 0.
- Sample floor is 20 (`ForwardParameters.minimum_sample_size`). 21 plans exist, but **0** clean R points → `INSUFFICIENT_DATA`.
- Warnings include `ambiguous_outcomes_separate:N` (`reporting.py` approx. 645–646).
- Copy states these are not realised profit (`reporting.py` 8–16, 70–98).
- 20-candle horizon (`parameters.py` approx. 196) matches the paper plans. It never comes into play because ambiguity settles on candle 1 of 20.

**Confirmed metrics defect (lower severity):** `entry_reached_rate` counts `entry_reached` on ambiguous rows (`reporting.py` `_known_entry` approx. 338–347, 354–358). Same-candle ambiguity sets `entry_reached=True`, so fill rate can read ~100% while nothing was ordered. It is not a win rate, but it looks like entries work.

**Friction:** `FrictionAssumptions` defaults to `fee_bps = 0`, `entry_slippage_bps = 0`, `exit_slippage_bps = 0` (`historical_validation/parameters.py` approx. 40–52). Intended default; **unsuitable** for a profitability claim. Do not change the code default without a separate configuration decision.

---

### Finding 7 — Market data integrity and lookahead

**Classification:** **intended, and holding in this code.**

| Check | Result | Reference |
|---|---|---|
| Forming candle excluded on ingest | `excluded_open_count` | `market_data/validation.py` approx. 185–219 |
| Download cap at latest closed open | yes | `market_data/service.py` approx. 155–156, 509–510, 665–689 |
| Structure uses only closed prefix | `timestamp + interval <= as_of` | `market_structure/candles.py` 77–90 |
| Incomplete required window | planning withheld; cycle `INCOMPLETE` | `forward_testing/service.py` `planning_withheld` |
| Future evidence in planning | `INVALID` / `future_evidence_used` | `planner.py` approx. 399–410, 716–724 |
| Outcome uses future opens only | yes | Finding 1 |
| Catch-up labelled | `DataHealth.HISTORICAL`, not `CURRENT` | `service.py` approx. 1478–1485 |
| Missing candles in outcome window | `INCOMPLETE_DATA`, not a win | `observation.py` approx. 481–485 |

**Unverified on Mac:** `forward_cycles.data_health`, `missing_candle_count`, actual runner timeframe. Repo default timeframe is **1h** (`config.py` line 62). A 1h range routinely covers last close plus a nearby target/stop, which fits 21/21 first-candle ambiguity.

This is **not** lookahead of unclosed data. The live failure is evaluation semantics (Finding 1), not dirty candles.

---

### Finding 8 — Setup detection and signal quality

**Classification:** **uncalibrated intended defaults, not a confirmed detector bug.** Do not retune families to chase these 21 rows.

- HTF alignment **off** (`QualificationParameters.require_higher_timeframe_alignment = False`, `higher_timeframes = ()`) — `setup_qualification/parameters.py` lines 21–28.
- Expiry 10 bars; `min_relative_volume = 1`; `max_atr_percent = 10`.
- Step 13 hierarchy is **dashboard-only** (`web/dashboard_service.py` approx. 202–215, 813–854). Paper plans do **not** require COMPLETE hierarchy / 5M `TRIGGERED`. UI “Plan ready — hierarchy complete” (`web/static/js/plain.js`, `views/dashboard.js`) can disagree with the paper book.
- `decide_current` journals a Step 6 `PLANNABLE` plan **without** requiring the hierarchy (`dashboard_service.py` 390–452).

Making hierarchy a paper-trade gate would be a **new policy**, not a bugfix.

---

### Finding 9 — `max_structural_targets` is applied before the 1R filter

**Classification:** **confirmed defect of filter order** (v2). Different symptom from Finding 3.

`structural_target_candidates` caps at `max_structural_targets` (default 2) **before** `_apply_minimum_r` (`trade_planning/levels.py` approx. 283–294). The two nearest levels can both be &lt; 1R while a farther ≥1R level is discarded as “beyond max_structural_targets”. That produces `NO_PLAN`, not a sub-1R paper plan. It can inflate `NO_PLAN` / `MISSED` (the 18 observations) but does not explain 20 sub-1R **paper** plans.

---

### Finding 10 — `OPEN_AT_CUTOFF` is not treated as settled

**Classification:** **hypothesis.** Not in the 21 Mac rows (all `AMBIGUOUS`).

`paper_outcome_is_settled` returns `True` for `FINAL_OUTCOME_STATUSES` and for `ENTRY_NOT_REACHED` only after the horizon. `OPEN_AT_CUTOFF` and `INCOMPLETE_DATA` fall through to `False`. An `OPEN_AT_CUTOFF` plan may occupy the one-active lock indefinitely. Docstring at `models.py` 280–284 is slightly inconsistent with the implementation.

---

## 6. Ranked summary

| Sev | Finding | Class | Impact |
|---|---|---|---|
| 1 | 21/21 `AMBIGUOUS` / `entry_and_exit_same_candle`; `AMBIGUOUS` is final | **Confirmed defect (composition)** | No scorable performance |
| 2 | Ambiguity settles the one-trade lock; uniqueness is per `setup_id` | **Confirmed defect** | 21 correlated plans / 5 opportunities |
| 3 | Sub-1R `PLANNABLE` paper plans | **Confirmed v1-era behaviour**; v2 already refuses them | Mac book violates the **current** 1R policy |
| 4 | Empty journal | **Intended** | Step 8 stats empty; forward ledger is the real book |
| 5 | 668 observations with no `plan_state` | **Intended** | Watches / terminals / withheld planning |
| 6 | `entry_reached_rate` counts ambiguous touches | **Confirmed defect (metrics)** | Inflated fill rate |
| 7 | Lookahead / closed-candle policy | **Intended and holding** | Not the cause |
| 8 | HTF off; hierarchy not in paper path | **Intended defaults / additive Step 13** | Not a detector bug |
| 9 | `max_structural_targets` before 1R | **Confirmed filter-order issue** | Extra `NO_PLAN`, not sub-1R paper plans |
| 10 | `OPEN_AT_CUTOFF` may never settle | **Hypothesis** | Not in current evidence |
| 11 | Friction defaults 0/0/0 | **Intended default** | Unsuitable for profitability claims |
| 12 | No human approval on paper path | **Intended** | `PLANNABLE` → paper plan automatically |

**What is actually broken:** paper performance measurement. Last-close entry + first future OHLC bar + “entry and exit in one bar = `AMBIGUOUS`” + “`AMBIGUOUS` is settled” means every paper plan dies on bar 1 with `entry_ordered=0`. The one-active-trade rule then lets the next correlated setup be frozen. The book cannot establish a win rate or R distribution.

**What is functioning as designed:** closed-candle-only planning; no lookahead of unclosed bars; 1R rule **in this v2 source** (tests and gate); paper vs journal split; no silent P&L; ambiguous rows excluded from R; 20-bar horizon; planning only on `QUALIFIED` + complete windows; v1 optional 1R gate (historical).

**What is blocking “rigorous testing”:** not swing highs, not missing journal rows, not miscomputed R.

1. v1 plans are not the v2 1R policy.
2. Paper outcomes cannot produce a clean R sample under the current fill + same-bar + final-`AMBIGUOUS` model.
3. Opportunity identity is per seed, so one close becomes many plans; `AMBIGUOUS` immediately frees the slot.

Until (1)–(3) are addressed **in that order**, more cycles only add unscored ambiguous rows. Do not retune the strategy first.

---

## 7. Contradictions to keep explicit

1. **Docs/current code say min 1R; Mac paper plans are sub-1R `PLANNABLE`.** Resolved: plans are `trade-planning-v1`; v1 default `min_r_multiple` was `None`.
2. **`PLANNABLE` sounds executable; it is only “complete proposal”.** Forward testing automatically papers every `PLANNABLE` result. Journal `ACCEPTED` was never required.
3. **Dashboard “hierarchy complete” vs paper book.** Step 13 does not gate Step 12.
4. **Step 8 statistics vs forward metrics.** Two ledgers; only forward has the 21 outcomes.
5. **`entry_reached` vs `entry_ordered`.** Ambiguous rows are “entry touched” but not ordered; fill rate can mislead.
6. **One-active BTC trade vs 21 plans.** Lock releases on `AMBIGUOUS`.
7. **v2 `_apply_minimum_r` cannot persist sub-1R retained targets; the DB has them.** Resolved by version: those rows are v1.

---

## 8. Coordinated repair plan

**Do not implement until approved.** Policy choices that must be decided first:

**P1 — Ambiguity vs one-active lock.** Keep “never call same-bar a win” (required). Decide whether `AMBIGUOUS` **releases** the one-BTC paper slot. Today it does; that is why 21 plans exist.

**P2 — Fill / outcome model.** Keep last-close entry (v2 policy). Optionally change **only outcome sequencing** in a new `journal-outcome-v2`. Do not do this in Stage 1.

**P3 — 1m ordering.** Optional later. 1m/tick used **only** inside `observe_outcome` to order entry/stop/target. Planning stays on the engine timeframe. New `OUTCOME_RULES_VERSION`. Not required to fix 1R or duplicates.

**P4 — Hierarchy-gated paper book.** New policy. Out of scope until measurement is honest.

### What not to do

- Do not “fix” 1R again in `planner.py` (already v2).
- Do not add a second 1R gate in `ForwardTestService` (Step 12 must not own planning thresholds).
- Do not delete or rewrite v1 rows to look like v2.
- Do not treat empty journal as a forward bug.
- Do not retune swings, equal highs, or HTF to chase these 21 rows.
- Do not assume 1m data until Phase 3 is approved.
- Do not merge v1 ambiguous R into a live win rate.
- Do not change strategy parameters as part of making measurement honest.

---

### Phase 0 — Operational (no code)

| Action | Why |
|---|---|
| Confirm the Mac runner process is this checkout (`trade-planning-v2`) | A v1 runner will keep writing sub-1R `PLANNABLE` plans |
| Leave the 21 v1 rows in place | Fingerprint identities; rewriting is a different ledger |
| Score v1 as `trade-planning-v1` only; never merge with v2 | Already supported via `version_fingerprint` |
| Confirm timeframe, `data_health`, `ledger_rules_version` | Needed for P1/P2 |

**Migration:** none. No deletes.

---

### Phase 1 — 1R is already in v2 (smallest planner change: none)

| Item | Detail |
|---|---|
| Files already containing the fix | `trade_planning/parameters.py`, `planner.py` `_apply_minimum_r`, `forward_testing/service.py` `_plan` / `_build_paper_plan` |
| Root | v1 gate off; v2 on |
| Dependencies | Phase 0 deploy |
| Minimum change | **No planner edit.** Add tests only (section 9) |
| History | v1 rows stay `PLANNABLE`; new sub-1R → `NO_PLAN` / possible `MISSED` (`service.py` `_missed_reason`, approx. 2377–2420) |

---

### Phase 2 — Honest paper counting (after P1)

**Minimum necessary if the only goal is “one opportunity at a time” without changing fills:**

| ID | Change | File / function | What |
|---|---|---|---|
| 2a | Do not treat `AMBIGUOUS` as freeing the instrument | `models.paper_outcome_is_settled` **or** `ForwardTestService._paper_trade_is_active` | Ambiguous plan still occupies the slot until horizon elapses **or** an explicit “slot release” is split from “scored result”. Requires **`forward-ledger-v3`** (v2 is already used). |
| 2b | Same-cycle `paper_trade_active` | `_record_cycle` | Set active when `paper_plan_id` is set, including reused IDs, not only `frozen_plan is not None`. |
| 2c | `entry_reached_rate` | `reporting._known_entry` / `_metrics` | Exclude `AMBIGUOUS` / `not entry_ordered` from fill rate. |

**Do not** change `observe_outcome` in this phase.

---

### Phase 3 — Outcome model (only after P2 approval)

Only if Phase 2 still yields ~100% ambiguous **on new v2 ≥1R plans** (hypothesis: 1h range vs 1R target).

Pick **one**:

- **3A.** New `journal-outcome-v2`: keep same-bar `AMBIGUOUS`, or require a bar that touches entry **without** exit before exits count. The second option is a new fill rule.
- **3B.** Optional 1m series **only** for ordering inside `_evaluate`, same plan levels. New market-data use + `OUTCOME_RULES_VERSION`.

**Not** “assume stop before target” or “assume target before stop”.

---

### Phase 4 — Operator / evaluation hygiene (no strategy change)

| Change | Where | Minimum? |
|---|---|---|
| Dashboard/status: “paper book is `forward_*`; journal is human” | web copy / forward status | Yes, docs/UI only |
| Non-zero friction **configuration** for evaluation reports | `FrictionAssumptions` at runner CLI | Config, not a new code default unless approved |
| Filter order: apply 1R before or while capping structural targets | `levels.structural_target_candidates` vs `_apply_minimum_r` | Optional; does not fix the Mac 21 |
| Do not retune HTF/volume/ATR | — | Out of this plan |

---

### Dependencies

```
Phase 0 deploy v2
    → Phase 1 tests (1R on the forward path)
        → Phase 2 settlement / one-active / fill-rate   [needs P1]
            → Phase 3 outcome-v2 or 1m ordering        [needs P2; only if still ~100% ambiguous]
                → Phase 4 friction config / UI copy
                    → (later, separate approval) hierarchy-gated paper book
```

Do not start Phase 3 or strategy calibration before Phases 0–2.

---

## 9. Regression tests and end-to-end requirements

### Already covering v2 1R (keep)

| Test | File | What it pins |
|---|---|---|
| Sub-1R structural target → `NO_PLAN`, empty `targets` | `tests/test_trade_planning.py` ~1055 `test_minimum_r_floor_refuses_a_sub_1r_structural_target` | Floor |
| Exactly 1R actionable; 1.5R classification only | `test_trade_planning.py` ~1072–1108 | Preference ≠ gate |
| Stricter configured floor | `test_trade_planning.py` ~1111 | |
| `min_r_multiple` 0 / 0.99 rejected | `test_trade_planning.py` ~1506–1508 | Floor cannot be configured away |
| Wide stop, far target, no squeeze | `test_trade_planning.py` ~809–812 | |
| Non-`PLANNABLE` creates no paper plan | `tests/test_forward_service.py` ~731–741 | Uses `min_r_multiple=100`, not default 1 |
| `MISSED` when a setup was ≥1R then falls below | `tests/test_forward_no_trade_reason.py` | |
| Same-candle entry+exit is ambiguous, never a win | `test_forward_service.py` ~886 | Finding 1 semantics |

### Missing — Phase 1 (1R on the forward path)

| Test | Assert |
|---|---|
| Mixed 0.5R + 1.5R | `PLANNABLE`, retained targets only the 1.5R (documents “at least one retained”, not “every original candidate”) |
| Forward + **default** `PlanningParameters()`, only sub-1R targets | `paper_plans_created == 0`, `plan_state=NO_PLAN`, `paper_plan_id is None` |
| New paper plans stamp `planning_rules_version == "trade-planning-v2"` | |
| Invariant: no `trade-planning-v2` paper plan may have `max(target_r_multiples) < 1` | Do **not** apply this to v1 fixtures |

### Missing — Phase 2 (counting)

| Test | Assert |
|---|---|
| Ambiguous first bar does not allow a second paper plan on the next setup | one unresolved plan (if P1 so decides) |
| Two `QUALIFIED` setups at the same close | ≤1 new `PaperPlan` |
| Reused settled `paper_plan_id` still counts as active if that is the approved P1 | |
| `entry_reached_rate` numerator ignores `AMBIGUOUS` / `not entry_ordered` | |
| Raw R sample still excludes `AMBIGUOUS` | already true; keep |

### End-to-end (existing `tests/forward_fixtures.py` harness)

1. Bootstrap closed history on the configured timeframe (repo default 1h).
2. `QUALIFIED` + structural target **0.7R** → no paper plan, `minimum_r_multiple_not_met`.
3. `QUALIFIED` + target **1.2R** → one paper plan, `trade-planning-v2`.
4. Next bar trades through entry **and** target → `AMBIGUOUS`, `entry_ordered=0`, **no** second plan from another setup (Phase 2 / P1).
5. Report: `ambiguous_count=1`, raw R sample 0, versions not mixed with a v1 fixture row.
6. Journal tables still empty unless `decide_current` is called.

Do **not** assert profitability.

---

## 10. Historical data compatibility and migration

| Object | Action |
|---|---|
| 21 v1 paper plans | **Keep.** `planning_rules_version=trade-planning-v1` |
| Their `AMBIGUOUS` outcomes | **Keep.** Do not re-observe under new outcome rules without a new version chain |
| `forward-ledger-v2` rows | **Keep.** Phase 2 needs **v3** so v2 and v3 are not merged |
| `journal_*` | Empty; nothing to migrate |
| Schema | Phase 1: none. Phase 2: probably none (query/rule version only). Phase 3: outcome version only |
| Deletes | **Forbidden** |

Reporting already separates fingerprints (`VersionSeparation.SEPARATED`). After Phase 2, combined metrics stay withheld across v2/v3.

Optional Mac checks (still not done here):

1. One plan: `plan_as_of`, first outcome candle OHLC, entry/stop/targets — next-bar same-range hit, not the decision candle.
2. `target_r_json` vs price-implied R vs `plan_json.excluded_targets`.
3. Group plans by `(plan_as_of, entry, direction, targets)` — five groups / multiple `setup_id`s / timeframes / `ledger_rules_version`.
4. v1 `config_fingerprint` vs `PlanningParameters(min_r_multiple=None)`.
5. `forward_cycles.data_health` and timeframe actually run.

---

## 11. Recommended implementation order (when approved)

1. Approve this plan and **P1** (does `AMBIGUOUS` occupy the paper slot?).
2. **Phase 0:** run v2 on the Mac; snapshot the DB; do not delete.
3. **Phase 1 tests only** (no planner rewrite).
4. **Phase 2** ledger-v3: one-active + fill-rate, after P1.
5. Re-measure **new** v2 cycles only. If still ~100% ambiguous, **then** approve P2/P3.
6. Friction as config for evaluation reports.
7. Hierarchy-gated paper book only as a **new** later policy.

---

## 12. Exact files and functions referenced

| Area | Files / functions |
|---|---|
| 1R policy v2 | `trade_planning/parameters.py` (`PlanningParameters`, `MINIMUM_R_MULTIPLE_FLOOR`); `trade_planning/planner.py` (`BASE_RULES`, `_apply_minimum_r`, `_select_targets`, `_reward_and_r`, `finalize`); `trade_planning/levels.py` (`structural_target_candidates`, `plan_close_level`) |
| 1R policy v1 (historical) | `dcfeed8` same modules; `min_r_multiple=None`; `_apply_minimum_r` early return; `r_multiple_fallbacks` |
| Paper persistence | `forward_testing/service.py` (`_record_cycle`, `_plan`, `_build_observation`, `_build_paper_plan`, `_update_outcomes`, `_observe`, `_paper_trade_is_active`, `_missed_reason`); `forward_testing/tables.py`; `forward_testing/repository.py` (`insert_cycle_bundle`, `append_outcome`); `forward_testing/models.py` (`FINAL_OUTCOME_STATUSES`, `paper_outcome_is_settled`) |
| Outcome OHLC | `journaling/observation.py` (`observe_outcome`, `_evaluate`); `journaling/types.py` (`OutcomeStatus`, `ProposedPlanLevels`) |
| Reporting | `forward_testing/reporting.py` (`_metrics`, `_known_entry`, `_warnings`); `historical_validation/metrics.py` (`r_values`, `r_exclusion`, `friction_adjusted_r`) |
| Journal vs forward | `web/dashboard_service.py` (`decide_current`); `journaling/service.py`; `statistics/dataset.py` |
| Market data | `market_data/service.py`, `validation.py`, `integrity.py`; `market_structure/candles.py` |
| Setup identity | `setup_qualification/engine.py` (setup_id fingerprint); `setup_qualification/parameters.py` |
| Hierarchy (display only) | `multi_timeframe/decision.py` (`gate_decision`); `web/dashboard_service.py` `_multi_timeframe` |
| Runner | `forward_testing/runner.py`, `forward_testing/__main__.py` |

---

## 13. Concise conclusion

The Mac book cannot demonstrate profitability because it is a **v1, sub-1R, same-bar-ambiguous, per-seed-duplicated** paper cohort — not because R is miscomputed, candles look ahead, or journal recording failed.

Current `main` already contains the mandatory 1R policy (`trade-planning-v2`). The smallest safe next step is **deploy v2, isolate history, then (with approved P1) stop ambiguous rows from opening the next correlated plan**. Outcome-model or 1m work comes after that, not before. No application code is changed in this pull request.
