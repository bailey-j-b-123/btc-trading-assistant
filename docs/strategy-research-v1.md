# Bounded strategy research v1 — all four hypotheses rejected

This is an auditable negative research result, not a profitable trading system.
The owner explicitly authorised isolated strategy research. The four new
hypotheses are research-only and do not modify the original BRAIN hierarchy,
planner, simulator, dashboard, forward ledger, thresholds, or risk rules.

## Baseline and dependency

At investigation time main was `9da1d3cf7a8fcc902b734f964a30c95032ee8d0d`.
PR74 remained an open draft at `8798b4985aec1dcd4c115a6d0643de109e5ed1d2`.
PR75 remained an open draft at `c5bac5a2176f4c7855486823887f6f41318197ce`,
stacked on74; both head-SHA Python and JavaScript checks were successful.
Neither feature is on main. This work is a separate branch stacked on75.
Its starting whole tree was `7c0c927c83885714239a44e9ed3d997f243806bd`.
No existing PR branch or stored research result was edited.

Previous PR75 verification: Python1232 passed,1original strictxfail;
Node22.23.3 298 passed. Actual CI38747580801 passed both jobs and its checkout
tree matched75. This is baseline evidence, not the new PR's CI result.

The specification was frozen BEFORE evaluating these hypotheses, at
2026-10-15T16:36:01.452637UTC. Its raw SHA256 is
`439e3837eabed3ae47afb813e153b19a46c7966535136325d61b12ec3e298a25`.
Source checkpoint70eb3d4 preceded the run; the exact new module SHA256 is
`34bcf5004adb8381ad1e9e722d52da5e5ac5f51b10d1260fee60d45d464b0c86`.
Existing decision/simulator source fingerprint remained
`f20a9bf3a433b74b72a45335c0dcb7dcb44c7679544177c7c9e29766c052c9c8`.
New experiment IDs include BOTH source fingerprints, the frozen specification,
candidate definition, dataset manifest and execution costs. Old cohorts are
never re-tagged. Subsequent tests/docs do not change the decision module.

## January A1/A2/A3: complete, but no economic comparison possible

Run `21ebae8915c06c1e64314ff3b0819c95ff66e7d612498a082086fe339dd32dee`
completed8929/8929 boundary evaluations. All three wallets ended with10000USDT,
zero signals, fills, completed trades, open/pending/unresolved positions,
PnL, net return, drawdown and exposure. PF, win rate, average win/loss and
holding duration are **undefined**, not zero. This is INSUFFICIENT EVIDENCE
for comparing policy profitability.

| Fact | A1 | A2 | A3 |
|---|---:|---:|---:|
| Plannable hierarchy-gate evaluations |30|90|138|
| Completed simulated trades |0|0|0|
| Net PnL / return |0 /0%|0 /0%|0 /0%|
| Equity-mark events |8929|8929|8929|
| Unsupported bearish actionable evaluations |5|5|5|

The actual full planner refused133 evaluations:61bullish and72bearish,
all with `minimum_r_multiple_not_met`. These are six distinct refused plans
per direction, repeatedly observed. The remaining five evaluations describe
ONE distinct actionable bearish plan, excluded by the declared cash Spot
long-only simulator. There was no actionable bullish plan to execute. Thus
extra A2/A3 gate permissions did not produce additional executable trades.
The adapter asserts A1 equality with the original hierarchy at each boundary.
No original trading rule was relaxed to manufacture a sample.

The endpoints are inclusive closed boundaries: Jan1 00:00 throughFeb1 00:00.
That includes the Dec31 23:55 opening bar and totals744h5m, not precisely744h.
Runtime9007.679seconds; peakRSS534282240bytes; SQLite integrityOK/FK0.
The completed marked-lab receipt contains policy metrics and refusal counts.

## Genuine data, fixed periods and execution

Reused and independently checksum-verified24 cached official Binance Spot
BTCUSDT hourly monthly archives for2023–2024. Zero downloads this task.
The [Binance public-data documentation](https://github.com/binance/binance-public-data)
describes the archive fields/checksums and warns that archives may later be
revised. Frozen ZIP hashes and candle fingerprints are in the receipt.

There are17542 confirmed hourly candles; March24 2023 12:00 and13:00UTC
are missing. One invalid/partial raw hourly source row was excluded, not
repaired or interpolated. The new full2023 diagnostic wallets happened to
have no position crossing that gap: zero unresolved positions does NOT mean
the data gap disappeared. Signals require a fresh contiguous121bar window.

Primary development is Apr1–Dec31 2023 (the previously declared continuous
period, selected for data quality). A full2023 gap-bearing diagnostic is
separate and not used for selection. Three2024 chronological reused checks
cover Jan–Apr, May–Aug and Sep–Dec. **2024 was already examined previously;
these are not untouched out-of-sample results.** Each wallet starts separately
with121bar coldstart; no cross-fold position/equity carry. No2025 prices are
downloaded/read, including at the exclusive Jan1 2025 endpoint;2026 is not
used. Archived2020–22 data/results stay isolated.

Execution is the unchanged cash-simulator-v2:10000USDT, one long position,
0.5% maximum risk budget,25% maximum notional exposure,0.00001BTC lot,
10USDT minimum,50bp maximum entry-gap ceiling. Decisions follow closed
candles; entries use next open; no favourable target-gap improvement;
stop first if both stop and target occur in one bar. Active gaps stay
unresolved and freeze the wallet. No invented end-of-window liquidations.

Normal costs:10bp fee each side,2bp full spread,3bp adverse slippage each
side. Stress:20bp fee,4bp full spread,9bp slippage each side. These are
explicit research assumptions, NOT verified historical fee tiers/filters or
order-book impact. Position-size caps often prevent using the entire risk
budget. No shorts are simulated: Spot candles plus a long cash ledger cannot
prove a funded short edge. Borrow/funding/liquidation and appropriate market
data would need a separate frozen, tested model.

## Four predefined hypotheses and ranking

All rules/parameters/rationale/gates are in `configs/strategy-research-v1.json`.
No variants were added or retuned after seeing results.

- Trend pullback: rising SMA100, SMA20 above it, reclaim SMA20 after a
  pullback; recent three-bar low minus0.25ATR stop,2R target,48h maximum.
- Breakout/retest: prior20high broken in previous six bars, then a genuine
  return to that **frozen** level without deep intervening failure;2R,48h.
  Breakout timestamp+level identity can be consumed only once per wallet.
- Compression/expansion: low preceding close dispersion relative to its own
 100window history, followed by a wide-range prior20high breakout;2ATR,3R,48h.
- Range-low reclaim: failed excursion below prior20low in a mechanically
  flat SMA100 context, bullish recovery below SMA20; stop below excursion,
  target frozen SMA20, minimum1.5R,24h. This is observable price geometry,
  not a claim that actual liquidity or hidden orders were detected.

Ranked only by primary development normal-cost net return, least negative
first. Ranking does not select a survivor. All positions below are closed.

| Hypothesis | Trades | Net PnL USDT | Net return | PF | Win% | Mean win/loss USDT | Mean hold h | Max DD | Stress return | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| compression_expansion | 59 | -48.98 | -0.490% | 0.950 | 33.90 | 46.95 / -25.33 | 14.8 | 4.446% | -5.342% | REJECTED |
| range_sweep_reclaim | 21 | -159.55 | -1.596% | 0.551 | 33.33 | 27.93 / -25.36 | 8.8 | 1.638% | -3.285% | REJECTED |
| trend_pullback | 80 | -479.73 | -4.797% | 0.610 | 33.75 | 27.78 / -23.20 | 14.9 | 5.358% | -10.501% | REJECTED |
| breakout_retest | 74 | -527.07 | -5.271% | 0.586 | 29.73 | 33.87 / -24.47 | 9.6 | 6.005% | -10.653% | REJECTED |

The exact40wallet rows include all normal/stress periods, per-year,
mechanical-regime and exit-reason breakdowns, mean netPnL/netR, grossPnL,
fees/impact, exposure, rejections, open/unresolved counts and reconciliation.
See [the CSV](research/strategy-research-v1.csv) and
[the source/result receipt](research/strategy-research-v1-receipt.json).
These1836 trade RECORDS overlap across models/costs/diagnostic periods;
they are not1836 independent market observations.193136 processed wallet
boundaries likewise are not unique candles.40/40 jobs completed in89.184s,
peakRSS140820480bytes. MainSQLite snapshot at reporting96980992bytes;
this is not a measured peak combined SQLite+WAL footprint.

Every primary screen lost at both cost assumptions. Trend pullback had only
45.76USDT gross-before-costs versus525.49costs; breakout/retest lost50.06
even before477.01costs (exact values inCSV); compression gross352.88 was
erased by401.86costs; range reclaim lost15.73 before143.82costs. Small
positive late2024 fragments do not rescue these failures. Range reclaim
Sep–Dec2024 normal-cost return+0.613% used only23trades, turned-1.384%
under stress and had a block-bootstrap lower meanR of-0.6824. Trend
pullback's+0.665% late2024 fragment still had negative equal-trade meanR;
PnL and meanR can disagree because risk/notional weights differ. Neither
is a simulator arithmetic error or evidence of a durable advantage.

Predeclared gates reject any primary/reused-check nonpositive netreturn
or meanR, >20%DD, >10%ambiguous trades or unresolved gap. Even without
economic failures, <100development/<20eachfold trades, open/pending
positions or nonpositive corrected lower bootstrap bound mean insufficient
evidence. Correction uses20known configurations (old16+new4), circular
blocks5,2000resamples, fixed seed173, lower-tail0.05/20. Extra cost choices,
folds and analyst discretion further increase effective multiplicity;
this exploratory correction is NOT a guarantee against overfitting.
No candidate is eligible for holdout approval/promotion/forward deployment.

## Why the previous16 failed — reused records, no reruns

[The32 primary-cost diagnostic rows](research/previous16-diagnostics.csv)
describe each of16 frozen configurations at both cost assumptions.
All160 previous experiments were inspected read-only, including all2024
folds and gap-bearing diagnostics. Their immutable tables hash was checked
before and after; it stayed identical (receipt records exact digest).

| Prior configuration | Closed trades | Raw gross | Fees+impact | Closed net PnL | Stop/target/time exits | Failure |
|---|---:|---:|---:|---:|---|---|
| sma_cross 10/30 2R | 116 | 370.00 | 762.11 | -392.11 | 61/36/19 | Gross edge erased by costs |
| sma_cross 10/30 3R | 115 | 93.77 | 743.15 | -649.37 | 67/20/28 | Gross edge erased by costs |
| sma_cross 20/50 2R | 82 | -420.32 | 527.43 | -947.75 | 58/21/3 | Negative before costs |
| sma_cross 20/50 3R | 79 | -442.54 | 507.50 | -950.04 | 60/11/8 | Negative before costs |
| donchian 10/30 2R | 105 | 319.99 | 683.39 | -363.41 | 63/34/8 | Gross edge erased by costs |
| donchian 10/30 3R | 93 | 134.13 | 599.62 | -465.50 | 62/19/12 | Gross edge erased by costs |
| donchian 20/50 2R | 81 | 172.46 | 525.77 | -353.30 | 50/24/7 | Gross edge erased by costs |
| donchian 20/50 3R | 71 | -222.36 | 454.54 | -676.90 | 52/9/10 | Negative before costs |
| rsi_recovery 10/30 2R | 134 | -76.60 | 829.72 | -906.32 | 79/36/19 | Negative before costs |
| rsi_recovery 10/30 3R | 132 | 24.94 | 818.66 | -793.72 | 81/23/28 | Gross edge erased by costs |
| rsi_recovery 20/50 2R | 132 | -16.30 | 824.45 | -840.75 | 78/37/17 | Negative before costs |
| rsi_recovery 20/50 3R | 130 | 107.17 | 812.48 | -705.31 | 79/24/27 | Gross edge erased by costs |
| momentum 10/30 2R | 144 | 108.35 | 924.30 | -815.95 | 83/42/19 | Gross edge erased by costs |
| momentum 10/30 3R | 135 | 182.70 | 870.31 | -687.61 | 82/26/27 | Gross edge erased by costs |
| momentum 20/50 2R | 120 | 625.79 | 787.45 | -161.66 | 66/45/9 | Gross edge erased by costs |
| momentum 20/50 3R | 113 | 499.49 | 732.76 | -233.26 | 68/25/20 | Gross edge erased by costs |

The before-cost figures use the **same realised trade paths and sizes**
with raw entry/exit references, not a new frictionless simulation. They
isolate accounting costs but do not establish what a zero-cost strategy
with different sizes/occupancy would do.

- All16 failed after costs. Five already had negative raw grossPnL; the
  other11 had a small positive gross total overwhelmed by fees/impact.
  Momentum20/50 2R, for example:625.79gross minus787.45costs =-161.66closed
  PnL. This directly identifies cost fragility, not a profitable survivor.
- Stops dominated: primary normal-cost configurations had50–83stop exits,
  versus9–45targets and3–28time exits. Wider3R targets often lowered hit
  frequency/increased timeouts; this is descriptive, not permission to
  optimise those values after seeing results. Exact counts are inCSV.
- Trading71–144times per primary wallet generated454.54–924.30USDT in
  normal costs. Mean holding12.6–22.1hours; average initial price-risk used
  about47–55% of the0.5% maximum budget due to exposure/cash/lot caps.
  Scaling up would magnify losses; sizing is not evidence of missing edge.
- SMA-cross lag, close-only channel entries, ungated arithmetic-RSI
  recovery and direction-only momentum are weak hypotheses in these
  samples. Their code establishes what they do; it does not prove a
  particular market narrative caused every loss. The mechanical MA
  labels and recorded ATR/close ratios are available in the diagnostic
  rows, but are not independent validated regime or causal attribution.
  No after-the-fact regime filter or profitable month is selected.
- The continuous primary screen has no data gap/unresolved position.
  Missing data therefore cannot explain away its losses. The previous
  full2023–24 diagnostic had20unresolved wallets, which remain unscored,
  plus12without unresolved positions that also lost. Existing late-open
  primary trades remain open/marked; closedPnL can differ from wallet
  marked return. They are not fabricated as closed trades.

## Correctness checks and reproducibility

New offline tests cover positive detections for all four actual functions,
counterexamples, future-candle exclusion, confirmation timing, missing and
duplicate bars, wrong timeframe, insufficient history, fixed-level identity,
invalidation, plan deduplication, transaction rollback, reopen/resume,
cash/mark reconciliation, corrupted-PnL refusal, an active-position gap,
data-content tampering, sealed-period refusal and complete-matrix gates.
Synthetic fixtures prove contracts only, never profitability.

Independent60digit accounting recomputed all1836completed trade records:
raw next-open entries, adverse fills, fees, lot/notional/exposure checks,
causal signal time, actual exit reason/levels, duration, nonoverlap, netPnL,
netR, finalcash and every equity mark/drawdown. Maximum trade/cash/mark
residual was0. Unresolved controls preserve debited cash and do not invent
exits. SQLite integrityOK, foreign key errors0.

From this isolated source checkout, with Python3.11+ and Node22 installed:

```bash
python -m pytest tests/test_strategy_research_novel.py
python -m pytest
node --test tests/frontend/*.test.mjs

# Reuse/copy only public archives+CHECKSUMs into a NEW marked lab, or acquire
# exactly these complete2023–24 months. Never point --root at an old results
# lab or operating-bot data. There is no holdout argument to the new command.
python -m trading_assistant.strategy_lab --root .research-v1 acquire --months \
  2023-01 2023-02 2023-03 2023-04 2023-05 2023-06 \
  2023-07 2023-08 2023-09 2023-10 2023-11 2023-12 \
  2024-01 2024-02 2024-03 2024-04 2024-05 2024-06 \
  2024-07 2024-08 2024-09 2024-10 2024-11 2024-12 --timeframe 1h
python -m trading_assistant.strategy_lab.novel --root .research-v1 \
  --spec configs/strategy-research-v1.json --output .research-v1-report
```

The completed experiment store is resumable/content addressed: identical
inputs return immutable results; an interruption rolls back its whole batch.
The command writes checkpoints after each job. No network function is called
by novel.py. Actual run used only cached archives and a downloader that raises
on any attempted network request. Test/CI commands are entirely offline.

Actual new-source checks: **14 targeted passed in0.52s; full guarded Python1246passed,1original strictxfail,1existing dependencywarning in151.51s,exit0; Node22.23.3 full298passed/0failed in15.232s,exit0.** Scoped Ruff E4/E7/E9/F passed. Public PR CI must be verified separately on its actual head.

## Remaining limitations and next work

No credible cost-adjusted edge was demonstrated by any of20tested
configurations. No2025 approval should be requested for these rejected
definitions. BRAIN is not ready for profitability-driven deployment.
Continuous real-market paper reliability remains a separate unproven claim;
this historical research does not certify it.

Original shallow-history strictxfail remains unchanged. The cash-only model
cannot compare the only actionable January bearish plan. Costs/filters are
assumed; no order book, queue position, funding, borrow, intrabar path or
market impact was observed. Only twoalready-examinedyears and four finite
new configurations were studied. Candle-close drawdown misses intrabar
excursions. No strategy/risk modification is suggested for production.

Next research should first build a separately frozen short-execution/data
validation contract (fees, adverse fills, funding/borrow, margin and gap
semantics) before testing a genuinely different directional hypothesis on
2023–24. It should not repeatedly retune these losers or unlock2025. New
instrument/data or modelling boundary changes require explicit review.
