# One observed-rule discovery experiment

Research only. It does not change BRAIN's live planner, rules, runner, account
risk or dashboard. It adds no simulator, database schema or dependency. Existing
cash simulation, checksummed archive acquisition, immutable lab checkpoints,
holdout guard and independent accounting are reused. Optional pullback and stop
quality are **disabled**; effects are not combined. Base is draft85; the separate
stop-quality branch was reviewed and is unnecessary for this experiment.

## What is actually learned

A fixed, small regression tree learns feature choices, interactions and numeric
split thresholds from **April through September2023 only**. The input language is
raw completed-candle geometry and its observed price/volume context:

- Signed body / full range, upper/lower wick / range, close location in range.
- Candle volume / mean volume of the **preceding**24 candles.
- Candle range / mean range of the preceding24 candles.
- Opening gap / mean preceding24 ranges.
- Close position relative to the preceding24-candle high/low interval.

No RSI, moving-average entry grid, named strategy selection, random parameter
mining or executable generated code is used. All observations require31
contiguous completed Binance BTC/USDT hourly candles. Zero range or unavailable
volume/context is excluded honestly. Decisions cannot use unfinished candles.

The training response is the next24-hour **close return**, known only at that
future candle's close. Its entire horizon must be inside training. It is a
learning label, **not a simulated fill or claimed tradable profit**. The last24
hours cannot supply labels. Learned rules are only executable after training
ends. No test period price is used to build features, thresholds or labels in
learning. The learner is a pure tuple-in function with no store/file/network
handle; this is an auditable application boundary, not a hostile-Python sandbox.

At each node, choose the largest positive squared-error reduction from empirical
feature quartiles25/50/75, with deterministic feature/threshold ordering for ties.
Maximum depth3, minimum120 samples per child. At most8 disjoint leaf-rule
hypotheses (below the owner's24 maximum); at most168 split trials. Every leaf
and its training support/mean is saved, including rejected hypotheses. Retain a
nonempty leaf for backtesting only when its mean label exceeds both zero and the
unconditional training mean. This is a **hypothesis admission rule**, not evidence
that costs can be paid. No new candidate is derived after evaluation.

Each retained leaf becomes an explicit conjunction of at most3 typed conditions
and a content fingerprint covering its definition, training-candle fingerprint
and fitting source hash. The shared runner's `decision_adapter="discovery-v1"`
accepts only a frozen `Rule` matching the current source. Defaults are unchanged;
arbitrary callables/code strings cannot be supplied as an adapter.

## Execution and fair comparison

All candidates use the same **existing** template as the two frozen baselines:
31-bar warmup; ATR14 computed on those31 closed bars; long entry at the next
available open with adverse spread/slippage; stop2ATR below signal close;
target4ATR above; original48-bar maximum hold. No fabricated detection or
intrabar favourable fill. Both-stop-target candles resolve stop first; missing
data preserve unresolved status. Open positions retain conservative marks.

Baselines are existing SMA-cross10/30 and Donchian10/30 configurations, each
stop2ATR/2R/max48h. Nothing about their decisions is retuned. Every wallet uses
10000**USDT**, risk0.5%, exposure25%, one cash Spot-long position, existing lot/
notional/gap/duplicate/checkpoint safeguards. Base costs: fee10bps each side,
full spread2bps and slippage3bps (adverse4bps per side). Stress:20/4/9
(adverse11bps). Compare all retained candidates and both baselines under both
costs, with the same four primary periods:

1. October–December2023, separate from rule discovery but previously researched.
2. January–April2024, **reused** chronological validation.
3. May–August2024, reused validation.
4. September–December2024, reused validation.

Additional overlapping two-month checks cover Jan–Feb,Mar–Apr,May–Jun,Jul–Aug,Sep–Oct,Nov–Dec2024. These diagnose regime sensitivity and are not additional independent evidence.

No2025 price rows. January1,2025 is only the exclusive end boundary.
The command opens only the six discovery archives, saves learned rules and
hashes them, then opens later archives. Rules must remain byte-identical after
all backtests. The six-month learning period contains4,392 hourly candles; the final three2023months remain forward-only. Earlier Jan–Mar2023 includes already-documented market gaps and is not silently stitched into this initial contiguous learning cohort. No old results are overwritten.

## Multiple testing and limits

Save all split trials and leaf hypotheses, not just winners. Use the conservative
family bound176 (168 possible split trials +8 leaves), seven-calendar-day
circular blocks and20000 fixed-seed173 resamples for an exploratory lower daily
return bound at alpha.05/176. This sparse extreme bootstrap tail and reused data
**cannot prove post-selection significance or profitability**. Correlated
candles/trades are not independent observations. A data-derived leaf is not a
claim of worldwide original market insight.

Expansion gate fixed before prices: every primary period under base **and** stress must
have at least100 completed trades, positive net return and expectancy, and a
positive adjusted block lower bound. All technical/accounting checks must pass.
Even passing requires fresh independent validation before any promotion.
Insufficient samples or failed rules are reported, never fixed by retuning.

## Reproduce once, in fresh directories

```sh
PYTHONPATH=src .venv/bin/python scripts/run_observed_discovery.py \
  --source /absolute/marked/cached-archive-lab \
  --root /absolute/new/discovery-lab \
  --output /absolute/new/discovery-evidence
```

The source is read only, cached public archives only; network disabled. Output/
lab paths must be fresh and separate, no symlinks or production locations.
Reusing a previous output path deliberately fails. Artifacts contain the frozen
protocol, archive read order/checksums, training and evaluation manifests, all
learned rules/rejections, exact source identity, events and states, both cost
scenarios and independent built-in financial audit. No test result or historical
performance claim is implied by this document; actual execution receipts govern.

## Executed result (one frozen run)

Six-month4392bar learning generated8leaves, rejected5, tested3against2baselines;100wallets/197760steps. Independent raw-data/signal/ledger audit verified4382fills/4342closed/40open/4pending,0USDT monetary residual. All learned stress runs lost; samples per primaryperiod25–97closed fail100minimum. No reliable profitableedge or promotion. Actual machine-readable rules, primary metrics, source/data fingerprints and limitations: `research/observed-discovery-v1-results.json`. The six overlapping2monthwindows are descriptive robustness checks, not independent replication. Full detailed report,100-rowCSV and genuine winning/losing PNGs are preserved in the owner evidence package.
