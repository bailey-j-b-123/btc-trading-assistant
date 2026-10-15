# Project Alpha v1 — frozen exploratory protocol

Owner explicitly authorises this isolated research campaign. Base PR77
b3ae710efa015c35cc5452ee64dd76ea75e04d93; main/PR46/74/75/76/77 unchanged.
Two hypotheses, ONE configuration each, NO replacement or parameter search.
The remaining four allowed slots are deliberately unused. No 2025 prices,
2026 prices, production rules, dashboard, accounts, shorts, orders or deployment.
Commit this specification AND implementation fingerprints before backtests.
Synthetic fixtures test mechanics only; historical results use genuine candles.

## Prior failures and rationale

The 16 original configurations all failed normal costs on Apr–Dec2023.
Some had negative gross PnL; even the strongest gross momentum example
(625.79 USDT before all friction) lost161.66 after costs. The four later
hypotheses also failed: trend pullback gross45.76/net-479.73; breakout/retest
gross-50.06/net-527.07; compression gross352.88/net-48.98; range sweep
gross-15.73/net-159.55. These are existing historical receipts, not reruns.
Small gross predictability, turnover and regime instability are the evidenced
problems. They do not prove a universal absence of BTC predictability.

Only pre-2023 research informs this campaign. Public claims are not evidence
that these specific rules work on Binance BTCUSDT. No published returns are
used as our model performance. Search also surfaced newer papers; they are
excluded from selection, and no holdout prices or returns were queried.

- Wen et al., 2022, “Intraday return predictability in the cryptocurrency
  markets: Momentum, reversal, or both”,
  https://www.sciencedirect.com/science/article/pii/S1062940822000833 :
  abstract reports both intraday continuation and reversal. Hourly rules below
  are OUR coarse, falsifiable adaptation, not a replication of half-hour data.
- Baur et al., 2019, “Bitcoin time-of-day, day-of-week and month-of-year
  effects in returns and trading volume”,
  https://www.sciencedirect.com/science/article/pii/S1544612319301710 :
  return patterns were not persistent; volume seasonality alone is not alpha.
- “Trading volume and liquidity provision in cryptocurrency markets”, 2022,
  https://www.sciencedirect.com/science/article/pii/S0378426622001418 :
  reversal evidence is conditional on activity in a cross-section of coins.
  This does NOT establish a BTC-specific edge. Large liquid coins can differ;
  https://www.sciencedirect.com/science/article/pii/S1057521921002349
  (2021, “Up or down?”) explicitly cautions about that distinction.
Publisher full pages returned403; use accessible abstracts/search-indexed
primary text only. No inaccessible methods or exact published thresholds
are claimed. Definitions below are engineering hypotheses, not paper claims.

## Shared mechanical rules

Instrument Binance Spot BTC/USDT, LONG or cash only. Confirmed1h candles;
169 contiguous same-instrument bars required. Reject gaps, duplicates,
out-of-order and future candles. One observed boundary offer per source ID;
actual filled plans stay consumed under PR77 lifecycle. No limit fills.
Next eligible hourly OPEN plus adverse impact; entry cannot gap more than
50bps versus last confirmed close before impact. One position, initial10000
USDT, nominal0.5% equity maximum stop-risk budget, maximum25% notional
exposure, quantity step0.00001BTC, minimum notional10USDT. Existing simulator
sizes conservatively at entry ceiling including stop fees/impact. Gap losses
may exceed nominal risk; do not call that guaranteed maximum loss.
Both stop and target hit: adverse stop first. Opening gaps through stop:
actual open; positive target gap receives target, no favourable improvement.
An active position across missing bars is unresolved and freezes entries;
never fabricate settlement. No forced final exit; open/pending are reported.
Maximum holding measured from actual entry, time exit at final held-bar close.
Target fixed at2 initial raw-price R for both hypotheses. No economic filter
changes between cost scenarios; all costs applied by existing simulator.
Normal: fee10bps/side, fullspread2bps, slip3bps/side (impact4bps/side).
Stress: fee20bps/side, fullspread4bps, slip9bps/side (impact11bps/side).
These are MODEL assumptions, not account quotes. Risk/exposure unchanged.
ATR14 is existing deterministic Wilder ATR computed on stated closed window.
Median uses sorted Decimal values, even sample average of middle two.
Regime reporting only: latest close above versus at/below prior168h mean;
no added trend qualification. Every anchor/timing/indicator saved in signal.

## A1: thin_volume_shock_reversal

Mechanism: a large downside displacement on unusually thin activity may be
inventory/temporary liquidity pressure; buy only after a first positive close.
Potential persistence is compensation for adverse-selection/crash exposure,
not free arbitrage. This differs from RSI recovery and range-sweep/reclaim:
no oscillator, no range/support break, no moving-average/trend condition.
Uses a six-hour volatility-scaled displacement plus actual activity filter.
Risk: genuinely informed selling, persistent crashes, costs and tiny sample.

At each hourly close with169 contiguous bars w:
prior=w[:-6] (163 bars); ATR14 from prior, so shock cannot inflate denominator.
anchor=w[-7].close; trough_close=min(close of w[-6:]);
require anchor-trough_close >=3*ATR, mean(volume of w[-6:]) strictly below
median(volume of w[-54:-6]), and latest.close > previous.close.
Stop=min(low of w[-6:])-ATR/4; reference=latest.close;
target=reference+2*(reference-stop); require0<stop<reference<target.
Source identity earliest timestamp attaining lowest LOW in six-bar shock
window, plus hypothesis fingerprint/instrument. New trough=new event;
same trough cannot fill twice. Max hold24h. No retries absent fresh evidence.
Three ATR and six hours specify a large multi-hour displacement; median48h
and quarter-ATR buffer are round structural choices, NOT fitted constants.
Support: sufficient trades, net positive across chronological/cost checks,
broad month participation and adjusted lower block interval above zero.
Falsify: nonpositive cost-adjusted returns/expectancy, stress failure,
concentration, unresolved data or insufficient sample for claimed reliability.

## A2: utc_open_close_continuation

Mechanism: dispersed global investors may digest early UTC-day information
late in the same day. A calendar-conditioned first-to-last-hour prediction,
not the old generic rolling momentum, MA or breakout rule. There is no
market close in Bitcoin; UTC convention may have no economic force. That is
a serious prior objection this experiment can falsify. No hour scan.

At23:00 UTC (latest closed candle opens22:00), using169 contiguous bars:
first = same UTC day's candle opening00:00; require first.close > first.open
AND latest.close >= first.close. No magnitude/volume/trend threshold.
ATR14 from w[:-1] (168 bars); stop=latest.close-2*ATR;
target=latest.close+2*(latest.close-stop); require positive ordered levels.
Source ID same UTC calendar date plus hypothesis fingerprint/instrument.
Enter next23:00 open; max hold1h, exit at00:00 close unless stopped/targeted.
One attempt per day may retry only if a fresh qualified boundary exists;
there is no second qualifying boundary that day. ATR stop controls tail
exposure; hypothesis tests time-conditioned flow, not optimal stop fitting.
Expected weakness: one-hour gross moves may be smaller than26bps round-trip
normal costs (approximately; exact notionals matter). Stress approx62bps.
Support/falsification same fixed tests asA1. No alternative UTC hour or
unconditional calendar control strategy is authorised after losses.

## Data and fixed matrix

Reverify checksum/geometry of24 already cached official Binance BTCUSDT1h
monthly archives2023-01..2024-12. Decode selected files directly from marked
research archive storage read-only, import into NEW own marked laboratory.
No downloader, no reading old experiment DB, no 2020–22 or holdout files.
Preserve reported March2023 missing intervals. Boundary count and archive
hashes are evidence, never assume coverage from calendar dates.

Five independently cold-started wallets per hypothesis/cost (20 jobs):
- development Apr1 2023–Jan1 2024 (already examined discovery)
- gap diagnostic Jan1 2023–Jan1 2024 (overlaps development; NOT extra sample)
- reused2024fold1 Jan1–May1; fold2 May1–Sep1; fold3 Sep1–Jan1 2025.
End dates exclusive. Cold169-bar warmup within EACH wallet; no cross-fold
position transfer, open ends remain explicit. No future2025 bar for last exit.
No “untouched validation” claim for2023/24. All previously examined.
Fixed decisions/execution on every eligible closed boundary, no thresholds
chosen from results. 2024 fold wallet returns are not one continuous wallet.

## Metrics and independent verification

Save every signal/rejection/trade/equity mark, immutable source/config/dataset
fingerprints. Existing PR76 independent60digit reconstruction checks entries,
next-open fills, ordered stop/target/time outcomes, fees, no overlap, cash,
all liquidation marks and close-mark drawdown; new report additionally
reconciles gross raw PnL - spread/slippage - fees = realised net PnL.
Record trade counts/frequency, mean win/loss, expectancy, PF, netreturn/DD,
exposure, holding time, monthly/regime results, losing streak, net excluding
top2 winning trades, gross profit fraction consumed by friction (undefined
if gross<=0), and break-even all-in roundtripbps on observed raw turnover.
Break-even is static arithmetic on realised fills, NOT an execution retest.
Compare actual stress model, plus labelled cash and25%/100% buy-and-hold
with same costs, firstopen tolastclose, reported gap limitations and unmatched
risk/exposure. No benchmark orders or new strategy candidate.

Inference uses fixed seed173,2000 circular moving-block resamples of DAILY
portfolio liquidation-equity CHANGES (cash/no-position days retained),
7 consecutive calendar days per block, not independent-trade sampling.
Bonferroni one-sided alpha0.05/2 gives2.5th percentile lower mean daily USDT
change for the TWO hypotheses. Diagnostic, not validated familywise control
under nonstationarity or repeated historical reuse. No interval across a gap
or incomplete/active wallet. No pooling overlapping variants/costs.

Classification before results:
C/no qualifying edge if any development or2024fold normal OR stress return
or expectancy <=0, any unresolved state, or drawdown>20%.
Otherwise B/inconclusive unless development>=100 closedtrades, total
nonoverlapping2024folds>=100, EACH2024fold>=20, all normal/stress adjusted
block lower bounds>0, no pending/open positions, <=10% ambiguous trades,
positive net after removing top2 winningtrades in each eligible wallet,
positive net in >half observed months per wallet. Every wallet must cover
>=60 full daily marks to apply block diagnostic.
Only if ALL hold: A/promising exploratory candidate, not proven profitable.
No sealedholdout request unless A and a separate exact protocol approved.
Strongest for reporting: largest worst eligible normal/stress net return,
then development normal return; never promotion by headline winner alone.
At campaign end STOP, no seventh hypothesis or loss-driven retuning.
