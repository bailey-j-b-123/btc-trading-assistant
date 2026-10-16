# Optional stop-quality-v1 research screen

This separate experiment rejects an economically unsuitable **original** stop.
It never moves a stop, changes quantity/risk limits, invents structural
invalidation or promotes a signal. The default `stop_quality=None` retains every
original decision and simulator event. The existing planner/forward ledger are
untouched. Previously saved experiments are immutable; source and explicit
policy parameters identify new cohorts.

`StopQuality` requires all five explicit parameters; there are no hidden policy
defaults. At the actual signal boundary it records:

- Original reference minus original stop, with the original detector/planner
  responsible for structural validity. ATR-only research stops do not magically
  become structural stops.
- The original strategy's observed ATR and source timeframe/availability. A
  missing/nonfinite ATR or future ATR timestamp refuses honestly.
- Recent mean true range and maximum opening gap from the configured number of
  completed, contiguous candles in the actual execution timeframe. Missing,
  future, cross-instrument/timeframe or insufficient observations refuse.
- Exact flat-price round-trip loss per BTC: `2 * reference * (impact + fee)`;
  impact is half the full spread plus slippage. Both notionals pay fees. This
  diagnostic is not a promise of maximum loss through an unseen future gap.

The stop distance must meet each separately configured minimum. All failed
metrics and exact numeric requirements are saved in a `stop_quality` event; a
single `stop_quality_refused` rejection includes all reasons. Occupied slots
retain their original refusal priority. Accepted signals keep original levels,
size and costs. No alternative stop is selected: deeper historical swings need
new causal structural justification and separate testing, not arbitrary widening.

The BRAIN research adapter uses its actual cached 1H structural ATR and records
that timeframe/close separately from the recent execution-window 5m ranges.
Its one-bar signal warmup does not truncate the required quality window. Existing
hierarchy admission, confirmed reference-band invalidation and no-shorts rule
remain intact. Missing data does not imply suitability.

## Fixed independent experiment

Before any prices were evaluated, the research profile froze:

```python
StopQuality(
    minimum_atr_fraction=Decimal('0.25'),
    minimum_roundtrip_cost_multiple=Decimal('1'),
    minimum_observed_range_fraction=Decimal('0.25'),
    minimum_observed_gap_multiple=Decimal('1'),
    volatility_lookback=14,
)
```

These are explicit unoptimised research hypotheses, not trading recommendations
or learned thresholds. No guarantee follows from passing them. Reuse the
**existing** comparison driver with one flag:

```bash
PYTHONPATH=src .venv/bin/python scripts/compare_pullback_entry.py \
  --stop-quality-v1 \
  --source /absolute/marked/archive-lab \
  --root /absolute/new/stop-quality-lab \
  --output /absolute/new/stop-quality-evidence
```

This runs the existing `breakout_retest` hypothesis twice (original vs filter),
with **original entries**, original exits/stops/targets, same capital/risk,
base/stress costs and four fixed2023/24periods. Pullback entries are disabled in
both wallets: effects are not combined. 2024 is reused chronological validation;
no protected2025prices, downloads, parameter search, live data or orders.
The command refuses previous outputs and freezes its protocol before reading
cached prices. Use `stop_quality=StopQuality(...)` on the existing runner APIs
for other predeclared studies; this is no full22configuration campaign.

Promotion requires technical/default/causal/recovery/accounting checks and
positive normal/stress expectancy in every preregistered period. Otherwise keep
research evidence and mark economic benefit NOT PROVEN. Lower rejection counts
or fewer trades alone establish no edge. Targets/confirmation and alternative
structural stops are separate research changes.
