# Project Alpha v1 — C: no qualifying edge

No strategy qualifies for promotion, holdout validation or paper-trading
selection. This is a completed, bounded NEGATIVE research campaign. A code
implementation that works correctly is not evidence of trading alpha.

## What was built and frozen

Two genuinely distinct, one-configuration hypotheses; no grid or replacements:

1. **Thin-volume downside-shock reversal:** after a six-hour downside
   displacement of at least three pre-shock ATRs, unusually low volume and a
   first positive close, buy next open. Stop below the six-bar low by a
   quarter ATR, target2 rawR, maximum24h. No RSI, range break, reclaimed level,
   MA crossover or breakout qualifier. It tests a liquidity-pressure
   interpretation of a large move, not a re-tuned old indicator strategy.
2. **UTC first-hour/last-hour continuation:** if the00UTC candle gained and
   its gain is retained at23UTC, buy the23UTC next open and hold at most1h.
   Stop2prior-ATR below reference, target2rawR. It tests calendar-conditioned
   delayed information, not generic rolling momentum. BTC has no true UTC
   market close; that was an explicit economic objection BEFORE testing.

The precise inequalities, indicator windows, exit chronology, costs, risk,
source identities, failure standards, statistical protocol and public primary
research sources are in [the frozen specification](research/project-alpha-v1-spec.md).

The specification and initial implementation were committed locally at
`56daabfee0a899b465022d0500b75b45ecbd977c`, BEFORE historical backtests.
The tested source/freeze receipt was committed at`bc910f6`, still before
historical performance. The raw spec SHA256 is
`7ea73beedfabe7a27104bff442ddce9fcc3bc2f41f057620b0581426d49fad8a`.
The Alpha execution/report source fingerprint is
`eaa105ea7f2cb33cbb1526d10e933b0f3e3b3d1bea29f6c696f0876a1a65ddf0`;
module raw SHA256`6ba3ff7d216c6ece58c854d13c0d17bbb1661adf78d0aeebdd85dbab1920d25c`.
These local registration commits are preserved in the delivery Git bundle.
GitHub browser publication occurred AFTER evaluation; it is not represented
as an externally timestamped public pre-registration. The receipt and code
hashes match the actual evaluated source. No source/rule changed after results.

The PR77 engine fingerprint remains
`5772ecff045b6e04cc6d4b7d740e46e90a759700374705f35a65809c14dd443b`.
Existing simulation, planner, strategies, risk and dashboard source is UNCHANGED.
Only new isolated Alpha module/tests/docs/evidence are added.

## Reconciliation and prior failures

At baseline main was9da1d3cf7a8fcc902b734f964a30c95032ee8d0d. Open PRs:

| PR | Exact head | Base | Baseline Python / JS checks |
|---|---|---|---|
|46|198c5eac7d0602fb9f948513a3be3e3381f65d8d|main|success / success|
|74|8798b4985aec1dcd4c115a6d0643de109e5ed1d2|main|success / success|
|75|c5bac5a2176f4c7855486823887f6f41318197ce|74|success / success|
|76|2adb4ee243b45d10d43df3acb61be193c86dc308|75|success / success|
|77|b3ae710efa015c35cc5452ee64dd76ea75e04d93|76|success / success|

These branches were unmerged and are not assumed to exist on main. Alpha is
stacked on exact77. The source-plan lifecycle fix, signed cost calculations
and optional economic planning already exist there and were not duplicated.
Earlier20 configurations remain rejected; none was rerun or retuned here.
Their preserved trade diagnostics show small/negative gross advantage and
friction exceeding positive gross gains. Examples from Apr–Dec2023:

| Prior hypothesis | Trades | Gross raw USDT | Realised net USDT |
|---|---:|---:|---:|
|Trend pullback|80|45.76|-479.73|
|Breakout/retest|74|-50.06|-527.07|
|Compression/expansion|59|352.88|-48.98|
|Range sweep/reclaim|21|-15.73|-159.55|

This describes recorded outcomes; it does not prove that every possible
continuation/reversal mechanism fails. Source selection focused on two
mechanisms not in that frozen search. The 2022 primary intraday-predictability
paper motivates a clock-conditioned hypothesis; Baur2019 cautions temporal
return patterns are unstable. The 2022 liquidity-provision paper motivates
volume-conditioned reversals, but its cross-sectional findings are not a
BTC-specific promise; “Up or down?”2021 warns large liquid coins differ.
Only accessible abstracts/primary indexed text was used; full publisher
pages returned403. Post2023 search hits were excluded, not used for selection.
No 2025/2026 prices or their market performance were inspected.

## Authentic data and execution

Reverified ALL24 cached official Binance BTCUSDT1h monthly ZIP archives for
2023–2024 against their saved official checksums; redecoded OHLCV geometry
and recorded timestamp gaps. No downloads. Exactly17,542 valid candles:
2023 has8,758 of8,760 expected hourly bars; March's TWO missing hours remain
missing. 2024 has8,784. No interpolation, fabricated bars or hindsight fills.
Each archive URL/hash, checksum-file hash and gap count is in the results.
The original marked archive source was read-only; its SQLite was never opened.
A new own LAB-ONLY SQLite database contains this campaign. Source archive and
checksum hashes were rechecked unchanged after all work.

Five cold-start wallets per hypothesis/cost, total20 jobs:
Apr–Dec2023 development, full2023 gap diagnostic, and Jan–Apr/May–Aug/Sep–Dec2024
folds. 169bar warmup in EVERY wallet; no position carry between folds.
Both2023/2024 are previously examined EXPLORATORY periods, not independent
holdouts. Full2023 overlaps development and is not extra evidence. No2025
bar is read to close the final2024 position. All wallets actually ended flat.

Actual execution is the unchanged single-position cash-Spot model:10,000USDT,
nominal0.5% stop-risk budget, max25% notional exposure,0.00001BTC step,10USDT
minimum, next-open market buy with conservative50bps gap guard. Exit fees on
both notionals; both stop/target touched => adverse stop. Opening stop gaps
receive actual open; favourable target gaps get no optimistic improvement.
Unknown gap outcomes remain unresolved and freeze entries. Cash Spot cannot
short, and no short proxy or limit-touch guaranteed fill was added.

Normal assumptions:10bps fee per side,2bps fullspread,3bps slippage/side.
Stress:20bps fee,4bps fullspread,9bps slip/side. Impact is4/11bps per side.
Approximate roundtrip total28/62bps (exact differing notionals matter).
The frozen spec's prose says26bps for normal; its explicit formula and code
are10+1+3=14bps per side, i.e.28bps. This arithmetic typo is disclosed, not
silently edited after registration; all actual runs use the correct unchanged
existing model. These are declared model assumptions, not verified account
charges or guaranteed fills. No account access was attempted.

## Results — every hypothesis, not a selected winner

Returns are percentages of10,000USDT starting cash, net of ALL model costs.
Each fold is a separate cold-start wallet, not one compounded annual account.

| Hypothesis / period | Normal trades | Normal return | Normal max DD | Stress trades | Stress return |
|---|---:|---:|---:|---:|---:|
|thin_volume_shock_reversal / development|0|0.0000%|0.0000%|0|0.0000%|
|thin_volume_shock_reversal / diagnostic_gap_2023|1|0.3961%|0.3348%|1|0.3198%|
|thin_volume_shock_reversal / reused_validation_1|1|-0.2672%|0.2672%|1|-0.3512%|
|thin_volume_shock_reversal / reused_validation_2|0|0.0000%|0.0000%|0|0.0000%|
|thin_volume_shock_reversal / reused_validation_3|0|0.0000%|0.0000%|0|0.0000%|
|utc_open_close_continuation / development|56|-3.9527%|3.9527%|56|-7.4583%|
|utc_open_close_continuation / diagnostic_gap_2023|78|-4.8687%|5.0001%|78|-9.4772%|
|utc_open_close_continuation / reused_validation_1|27|-1.3592%|1.3592%|27|-2.9085%|
|utc_open_close_continuation / reused_validation_2|30|-1.3667%|1.7255%|30|-3.3078%|
|utc_open_close_continuation / reused_validation_3|24|-0.8679%|1.2922%|24|-2.2628%|

The434 stored completed-trade records are NOT434 independent observations:
normal/stress reuse market events, and full2023 overlaps development. The
normal-cost primary2023 plus nonoverlapping2024 folds contain137 UTC trades
(56+27+30+24) and ONE shock trade. Their wallets/risk capital are independent
resets, so summing their returns would not be a continuous portfolio backtest.
Including the disjoint early2023 diagnostic adds22UTC trades and one shock
trade; do not double-count the overlapping later2023 period.

All20 wallets have zero pending/open/unresolved positions and zero ambiguous
trades. Signals=accepted fills=closedtrades in this actual sample; no occupied
slot or entry-gap rejections occurred. That is a measured fact, not proof
that rejections cannot occur: injected mechanics tests cover them. Source IDs
were unique in each actual wallet. All96,568 correlated wallet-boundary marks
are saved, not treated as a sample of96,568 independent trading outcomes.

### Why these failed

**Shock reversal:** no qualifying event in development; only one2024 trade,
which lost26.72USDT normal/35.12stress. The overlapping full2023 diagnostic
has one separate earlier trade gaining39.61normal/31.98stress. One earlier win
and one later loss do not establish a distribution, edge or statistical power.
The classifier reports no qualifying candidate; this is a failure to provide
sufficient evidence, NOT proof that the liquidity hypothesis can never work.
Rules were mechanically feasible in positive synthetic tests and did produce
genuine historical signals. They were not loosened to force100trades.

**UTC continuation:** development56trades lost395.27USDT normal/745.83stress;
normal PF0.0160, mean net loss7.06USDT/trade. Gross raw PnL was ALREADY negative
40.72USDT, before354.55USDT friction. In the three2024 folds raw gross was
positive20.60/49.62/51.94USDT but friction156.51/186.29/138.72 overwhelmed it.
Observed gross break-even all-in roundtrip costs were only3.68/7.46/10.48bps
in those folds, below the normal approximately28bps model. Development's
break-even cost is negative: cheap execution alone cannot repair that sample.
These are static observed-turnover break-even calculations, NOT a zero-cost
strategy rerun or guarantee of attainable fees/queue fills.

This supports an observed weak/unstable gross clock effect plus excessive
friction. It does not establish exactly which trader behaviour caused it.
No hour scan, opposite-direction strategy or cheaper-cost rescue followed.
Both normal/stress were evaluated unchanged, not selected to make a winner.
Monthly, regime, exit-reason, holding, frequency, expectancy, win/loss,
capital/exposure, friction and per-trade details are in the linked artifacts.
UTC development normal mean entry notional was23.0582%of equity, maximum
24.8754%, below25%limit. Mean initial price risk used56.9263%of the nominal
0.5%budget; remaining capacity was constrained by notional exposure. Occupied
hourly-bar fraction was0.8485%. Entry utilisation is trade-weighted, not
continuous intrahour exposure; hourly exit timing cannot establish that.

### Strongest and benchmarks

There is no useful selected winner. The frozen minimax ranking places the
mostly-idle shock hypothesis ahead on losses; remaining in cash is not alpha.
The UTC strategy has more observations but is negative in every scored
normal/stress wallet and never reaches100development or100nonoverlapping2024
trades. Normal worst close-mark drawdown is3.9527% for UTC development;
stress7.4583%. Across its full2023 diagnostic, stress drawdown reaches9.4772%.
Shock's worst normal/stress2024 drawdown is0.2672%/0.3512% on ONE losing trade;
that is not a statistically dependable low-risk estimate.

All strategies failed against cash/no-trade (0%) where they traded in primary
windows. In Apr–Dec2023, normal-cost BTC buy-and-hold returned48.1282% at100%
capital, or12.0317% with25% cash-funded allocation, compared with0%shock and
-3.9527%UTC.2024fold buy-and-hold100% returns43.0857%/-3.0705%/58.2265%;25%
returns10.7714%/-0.7675%/14.5566%. Both hypotheses stayed in cash or lost less
than100%BTC in the middle down fold, but that is mostly a risk/exposure
comparison, not proven predictive advantage. UTC still lost more than25%BTC
there(-1.3667%vs-0.7675%).
Benchmark quantities pay same costs/lot rounding, invest at period first open,
liquidate last close and report observed close-mark drawdown. A25% all-in
cash allocation is approximate notional comparability, not matched dynamic
stop risk, timing or exposure. B&H is continuously exposed; strategies have
169cold warmup bars and sparse exposure. The full2023 benchmark also has an
intraperiod gap caveat. No risk-adjusted superiority or Sharpe claim is made.

### Statistical and economic uncertainty

Seven-day circular blocks of actual daily portfolio PnL changes retain cash
days and chronological clustering. Two-hypothesis one-sided Bonferroni
2.5th-percentile lower means are diagnostic only; reused dates and regime
nonstationarity prevent untouched statistical confirmation. Full2023's known
gap disqualifies its interval, so it stays undefined. UTC lower bounds are
negative across all scored normal/stress wallets; shock mostly gives exactly
zero because no trades, and negative for its one2024 loss. Sparse sample and
nonpositive economics independently fail promotion. Top-two-win removal and
monthly breadth are also recorded; no exceptional-trade selection rescues it.
Costs/variants/overlapping periods are not pooled for confidence calculations.

## Verification, runtime and custody

-25 targeted synthetic offline tests passed0.58s BEFORE historical evaluation.
  They test exact signal anchors, causal timing, wrong/duplicate/gapped bars,
  next-open fills, both-target/stop adversity, fee arithmetic, entry gaps,
  real time-exit holding limits, source dedup, unresolved interruptions,
  atomic SQLite rollback, actual restart equality, restricted dates and
  fixed A/B/C promotion logic. Synthetic positive patterns are test-only.
-Actual20-wallet independent60digit reconstruction: maximum trade PnL error0,
  cash error0, portfolio-mark error0. Independent gross-impact-fees-net
  residual0. Each trade/entry/exit/fee/lot/exposure/mark verified.
-Four actual development wallets were replayed from scratch with two deliberate
  process/store restarts each (1000then1700boundary increments): ALL result
  fields and every saved signal/trade/equity event match exactly. Eight actual
  historical restarts, not just a cache hit; no new candidate definition.
-Repeat full20matrix:20deduplicated,0newjobs, result equality true. Hash of
  datasets/experiments/events unchanged. Original archives/checksums unchanged.
-SQLite integrityOK, foreign-key errors0. No old research result rewritten.
-20initial jobs processed96,568correlated wallet boundaries in24.967753s;
  peakRSS150,175,744bytes (~143.22MiB), final DB47,865,856bytes (~45.65MiB).
  Initial import/diagnostics/export included. Wall time is measured on this Mac,
  not a claim about production latency. Recovery/repeat overhead is separate.
-Full guarded Python:1,291passed,1original strictxfail,1existing dependency
  warning,155.93s. Node22.23.3 full frontend:298passed/0failed,15.304625s.
  Scoped E4/E7/E9/F Ruff clean. Published-head/CI verification is recorded in
  the PR description after source publication; do not substitute baseline CI.

No software defect was demonstrated in the inherited simulator during this
campaign. Negative trading hypotheses were not disguised as software bugs.
The frozen spec's prose arithmetic typo26bps is disclosed above; actual
execution costs were always the pre-existing correct model. No source tuning
or original rule changes were needed. The shallow-history strictxfail remains.

## Reproduce safely

Use a source-only checkout of this PR with Python3.11+/project dev dependencies
and Node22. Do not use the operating bot checkout or any production database.
The marked source directory needs ONLY the24 exact official2023–24 hourly
archives and their matching CHECKSUM files. URLs/hashes are in results JSON;
archives can be fetched separately from the official public-data service if
not already cached. Alpha itself has no downloader or2025/2026 access path.

```sh
PYTHONPATH=src python -m trading_assistant.strategy_lab.alpha \
  --source /absolute/marked-research-archive-source \
  --root /absolute/NEW-alpha-lab \
  --spec docs/research/project-alpha-v1-spec.md \
  --output /absolute/NEW-alpha-evidence --max-jobs 20
python -m pytest tests/test_project_alpha.py
python -m pytest
npm test
```

The source LAB-ONLY.txt must contain exactly`BRAIN-STRATEGY-LAB-ONLY-v1\n`.
Never point source/root to data/,backups/,symlinks or the same directory.
CLI job budgets1..20 stop after completed jobs; rerun identical arguments to
resume. Per128boundary atomic checkpoints survive interruption inside a job.
No CLI switches permit additional hypotheses, thresholds, costs or dates.
`run(...,max_boundaries=N)` is the bounded test/recovery API, not new strategy
selection. All completion records are immutable and content-addressed.

The clean-source standard tests are offline fixtures; the local full run adds
an ignored external temporary-storage/socket guard. It is not an OS sandbox
or a hostile-code guarantee. Existing CI uses GitHub-hosted disposable runners.

Artifacts:
- [Frozen rules](research/project-alpha-v1-spec.md),
  [pre-evaluation fingerprints](research/project-alpha-v1-freeze.json).
- [All20 metrics, costs, archive receipts, monthly/regime groups and block diagnostics](research/project-alpha-v1-results.json).
- [Readable20-row summary](research/project-alpha-v1-summary.csv).
- [All434 individual trade records](research/project-alpha-v1-trades.csv),
  explicitly correlated/overlapping across experiment IDs.
- [Independent accounting, actual restart and immutable-repeat proof](research/project-alpha-v1-verification.json).
- Local delivery archive additionally preserves every signal/rejection/equity
  mark in per-experiment compressed JSONL, source Git bundle/pre-registration
  commits, guard, full raw test/CI logs and machine-readable delivery hashes.
  These are genuine historical replay artifacts, not production records.

## Owner decision and next step

**C — NO QUALIFYING EDGE.** No candidate is ready for the protected2025 test,
strategy promotion or paper-trading selection. No holdout approval is requested.
Neither a single early shock win nor small positive gross2024UTC periods
justify independent profitability claims. The campaign STOPS at its TWO
pre-registered configurations; the four spare slots remain unused.

Before any real-money consideration, evidence would still require a credible
frozen exploratory candidate, separately approved untouched validation with
one-time custody, realistic attainable venue execution/cost evidence, extended
safe forward-paper accounting/reliability, and independent human review.
BRAIN itself contains no live order or account path, and none was added.

**One economically justified next research direction:** investigate availability
of authentic point-in-time trade-level aggressor-flow data, to distinguish
transient inventory-driven selling from informed selling before proposing
another reversal hypothesis. Candle volume alone did not identify enough
credible opportunities here. This is a future data-feasibility task, not an
authorised seventh hypothesis, new data acquisition or automatic continuation.
