# Causal candidate diagnostics (research only)

This extends the existing `strategy_lab.brain.compare_brain` runner; it is not
another backtester. It complements #83's selected-record reader. Enable it with
`diagnostics=True` **when starting a fresh marked research cohort**, and keep it
enabled on every resume. Default is off. Late activation on an already processed
cohort is refused; this cannot backfill old missing candidate records.

The source is the actual `enumerate_qualifications` replay over the same cached
qualification frames used by the hierarchy. Every candidate in that bounded
source replay is captured, including WATCH, QUALIFIED and terminal setups before
the deterministic one-setup selection. It does not recover seeds or reference
history outside that replay window. Completed source candles only; no hindsight
candidate construction or nearest-price joins.

## Contracts

- `candidate_diagnostic_runs` contains a separate version/source-hashed identity,
  linked to the existing brain run. The toggle is absent from the trading run
  definition. Installing changed core source naturally creates a new research
  cohort under the existing fingerprint contract; historical IDs/rows are never
  reassigned or rewritten.
- Content-addressed `candidate_diagnostic_facts` shares hourly candidate basis
  facts and records terminal states once. Identity includes exact configuration,
  timeframe, seed/reference IDs, detection time and source evidence. Existing
  qualification rule results, support structure, frozen sources, planner results,
  uncapped target catalog and declared base/stress economics are retained.
- Each target's `intervening_prefix_length` references the **ordered prefix of
  that same catalog**. It preserves every nearer source ID/price without
  quadratic repeated lists. These levels are potential intervening obstacles,
  never a forecast that price cannot cross them or that it will reach a target.
- Retest fields use the exact breakout and reference IDs, actual candle high/low,
  original band, configured tolerance, recorded and independently measured
  nearest distance. `physical`, `tolerance_only` and `no_current_contact` remain
  distinct. Later held/failed states are not substituted for original observed
  contact. An unrelated nearby reference is not associated.
- `candidate_diagnostic_observations` links each active candidate at every actual
  five-minute boundary to its basis, 4H context, unchanged 15M confirmation/5M
  execution functions, alignment, gate reasons and portfolio availability.
  Unselected gates/plans are explicitly **diagnostic assessments**, not admitted
  trades or fabricated alternative fills. Selected assessments must exactly
  match the original hierarchy or the diagnostic run raises.
- Actual paper events are separate exact-ID links from the unchanged runner.
  Only actual baseline filled positions/closed trades count as baseline fills;
  queued entries and an unselected hypothetical plan never do.
- Facts, observations, coverage and the original boundary/checkpoint commit in
  the **same SQLite transaction**. Faults roll everything back. Resume checks
  full matching coverage; repeated observation keys cannot inflate counts.
- Missing source evaluation is an explicit unavailable coverage row, not zero
  candidates. Terminal setups do not receive imagined later confirmation or
  retrospective targets. Missing stop/risk means unknown R, not an invented stop.

No changes to detectors, selection, qualification, entry, stop/target policy,
position sizing, fees, outcomes, production settings or existing results.
Diagnostic base/stress net-R is descriptive; it does not activate the optional
cost-aware planner or change its default. Gross 1R and net 1R remain distinct.

## Run and export

Use the existing isolated runner and its bounded work/resume arguments, with
verified permitted source data prepared in the lab. Never point it at the
operating bot, an unmarked store, or protected prices. Data preparation and date
scope remain subject to the existing sealed-period admission guards.

```python
from trading_assistant.strategy_lab.brain import compare_brain
# store is a NEW marked research Store; start/end/warmup are explicit UTC dates.
result = compare_brain(store, start=start, end=end, warmup_start=warmup,
                       prepare=False, max_boundaries=24, max_seconds=60,
                       diagnostics=True)
```

Read the diagnostic ID from `candidate_diagnostic_runs`, then export into a fresh
separate directory. The exporter opens SQLite in read-only/query-only mode and a
single read transaction, reads no candle tables and refuses overwrite.

```sh
python scripts/export_candidate_diagnostics.py --lab /path/to/marked-lab \
  --diagnostic EXACT_ID --output /path/to/new-export
```

Exports include one CSV/JSONL row per unique candidate, source basis/target facts,
all boundary observations, coverage, exact definition and file checksums. Stage
times and price movement are first observed facts, not proof of a better earlier
fillable entry. Lower-stage price marks use the closed execution candle; actual
fill prices are separate. Pre-start candidates are left-censored and separated
from new detections. End-of-window opportunities may be right-censored. A partial
month must be described as partial; missing windows cannot be inferred.

The restriction ranking counts unique affected candidates; flags overlap. It is
not a sequential funnel or causal profit attribution. A farther qualifying
hypothetical target is not necessarily a trade otherwise eligible under every
hierarchy rule. A separate count requires a plannable hierarchy and free slot.
No replay or data in these diagnostics establishes an economic edge.

## Regression commands

```sh
python -m pytest tests/test_candidate_diagnostics.py tests/test_strategy_lab_brain.py
python -m pytest
node --test tests/frontend/*.test.mjs
ruff check --isolated --select E4,E7,E9,F src tests
```

Run Python through the workspace offline guard where available. Full suites must
retain the original shallow-history strict xfail. Synthetic software fixtures
are never market-performance results. Review and a later controlled, frozen
experiment are required before any trading-rule alternative is introduced.
