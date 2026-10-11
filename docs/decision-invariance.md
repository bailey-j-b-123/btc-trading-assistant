# Decision invariance under history extension

**Status: FAILED. Historical backfill is on hold.** The check exists to decide whether older
candles can be appended without changing decisions that were already made. It does not pass,
so no backfill is run. Nothing in the database was changed to produce these results.

**All data here is SYNTHETIC.** A seeded random walk (`tests/test_decision_invariance.py`,
`synthetic_series`) written in Binance decimal-string shape. It is not Binance data, and it
says nothing about live BRAIN decisions on Binance. The sandbox is blocked from Binance.

## What was compared

- **Decision path:** `DashboardService._evaluate` (the bounded replay used by the dashboard
  and planning), then `plan_trade` for each qualified setup. Fields compared exactly: snapshot
  state; each setup's id, family, direction, state and terminal reason; each qualified plan's
  state.
- **Full payload** (`dashboard(as_of=…)`): every top-level section, in a separate probe.
- **Method:** the same synthetic series is loaded into a disposable SQLite DB twice. The
  "stored start" varies. Each boundary's output is compared with the full-history output at
  the same boundary.
- **Identifiers** that are hashes of their evidence (`source_reference`, `snapshot_id`,
  `context_fingerprint`, and so on) are reported separately, not treated as decision changes.

## Results

| Stored start (closes before boundary) | Boundary hour 700 (200 candles of warm-up in the start-500 case) | 760 | 820 | 940 |
|---|---|---|---|---|
| start 0 (full history) | reference | reference | reference | reference |
| start 300 | differs (400 closes) | differs (460) | differs (520) | **identical** (640) |
| start 400 | differs (300) | differs (360) | differs (420) | **identical** (540) |
| start 450 | differs (250) | differs (310) | differs (370) | **identical** (490) |
| start 500 | differs (200) | — | — | **identical** (440) |

Live setups (WATCH or QUALIFIED) at the same boundary, full versus shorter history:

- hour 700, start 500: 15 versus 12. The 12 shared live setups are identical in state, family,
  direction and terminal reason. Three live setups exist only in full history: two WATCH
  `breakout_retest_continuation`, and one **QUALIFIED** bearish `failed_breakout_sweep_reversal`.
  No live setup exists only in the shorter history.
- hour 820, start 300: 45 versus 24 live setups.

Strict test status: `test_decision_is_identical_when_stored_history_starts_well_before_the_boundary`
passes (hour 940). `test_candles_after_as_of_never_change_the_decision_at_as_of` passes.
`test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary` is an xfail
(strict). It fails for the expected reason: the setup sets differ at hour 820.

## Explanation of each difference class

1. **Live decision changes (the failure).** The missing setups are sweep reversals whose seed
   needs a reference level formed before the stored start. A reference formed before the
   stored start is invisible to a shorter history. The data are consistent with this: setups
   whose references lie inside the stored window are unaffected, which is why hour 940 is
   identical for every start tested. (Not separately profiled per reference.)
   The defect is not a warm-up problem that more history can fix. Hour 820 still differs with
   520 closes of warm-up, so reference lookback in this detector set is effectively unbounded
   by the 131-close depth gate.
2. **The depth gate is too shallow for references.** `required_trailing_depth` gives 131 closes.
   A boundary with 200 closes behind it passed that gate and still changed. The gate therefore
   reports a shallow history as complete when the decision can still change. This is a
   **calculation and gating question**, so it is recorded here as a proposal and not changed.
3. **Identifier-only differences (expected).** At hour 940, all 68 `qualification` differences
   are `source_reference` hashes. Setup ids, states, families and plans are identical. The
   `snapshot_id` and `context_fingerprint` change because they hash the evidence window.
4. **Descriptive whole-window statistics (not decision inputs, but misleading).** These grow
   with the loaded window: `market_state.events.*.count` (for example breakouts 290 → 1,065 at
   hour 940), `chart_evidence.breakouts` (290 → 329), `overlays.equal_levels` (83 → 221),
   `explanation.referenced_fact_ids` (5,213 → 16,305), and the chart candle list length
   (440 → 500). They must be labelled as counts over the loaded window before backfilled
   history is shown, or users will read them as recent counts.
5. **Plan differences at hour 700.** `plan.excluded_targets` changes only because the
   candidate set changes (item 1). At hour 940 the plan section is identical.
6. **No look-ahead found.** Every `known_at`, `time`, `timestamp`, `observed_at`,
   `confirmed_at`, `confirmed_by_timestamp` and `source_swing_timestamps` value in the full
   payloads for both states was at or before `as_of` (0 future values, hours 700 and 940). The
   decision also passed `test_candles_after_as_of_never_change_the_decision_at_as_of`.

## What this means for backfill

- Appending older candles changes live decisions only at boundaries that depend on reference
  levels older than the current stored start. The later the boundary relative to the stored
  start, the less likely a change, but no finite warm-up was enough in this data.
- Forward-ledger and journal rows are immutable and are not rewritten by backfill. The live
  dashboard, however, would recompute different decisions near the old start.
- Running the backfill now would change what BRAIN shows near the start of stored history.
  It is therefore held.

## Options (not implemented; each needs your decision)

- **A. Gate the decision by real warm-up.** Withhold planning at boundaries whose references
  predate the stored start, and show "warm-up incomplete" instead. This changes what is
  displayed, not strategy rules. It needs a tested depth derived from reference age, and your
  approval.
- **B. Bounded reference lookback (calculation change).** Limit reference lookback to a fixed
  span. This changes strategy calculations and must be validated on historical data first.
  The brief keeps that separate.
- **C. Accept the change and backfill anyway.** Backfill would then be run with a documented
  list of boundaries whose decisions change. Not recommended without (A).

## Performance observed (synthetic, sandbox)

| Measurement | Candles loaded | Boundaries | Seconds |
|---|---|---|---|
| Full dashboard payload, recent-only | 500 | 2 | 12.0 (about 6 s per call) |
| Full dashboard payload, after backfill-style extension | 1,000 | 2 | 61.7 (about 31 s per call) |
| Decision path only, full history | 1,000 | 2 | 32.5 |
| Decision path only, recent-only | 500 | 2 | 6.5 |
| Decision path only, start 300 | 700 | 4 | 23.6 |
| Decision path only, full history | 1,000 | 4 | 52.5 |

The cost is consistent with `build_frames` reading the whole stored history with
`get_candles(end_time=…)` before applying the bounded replay start. This was read from the code
and not profiled line by line. Dashboard latency grows with stored history, which is a Part 4
issue. It is not fixed in this change.

## Reproduce

```
.venv/bin/python -m pytest tests/test_decision_invariance.py -q
.venv/bin/python -m pytest tests/test_decision_invariance.py -q --runxfail -k shallow
```

On the Mac the same tests run against synthetic data and do not need Binance access. Real
Binance verification of decision invariance requires genuine stored candles and stays
unverified here.
