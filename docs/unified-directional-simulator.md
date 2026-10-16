# Unified LONG / SHORT research execution

This is an opt-in extension of `strategy_lab.simulation.step` and
`runner.run_experiment`, using the existing Store, checkpoints, one account,
one equity curve and reports. It is **historical research only**. Default cash
Spot LONG execution remains unchanged. No exchange/order/account API was added.
This is a fully collateralised linear **scenario**, not Spot short selling and
not a claim to reproduce a venue's historical perpetual or margin execution.

## Interface

```python
from trading_assistant.strategy_lab.directional import DirectionalModel, FundingEvent
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.models import Execution

result = run_experiment(
    store, closed_candles, verified_manifest, frozen_strategy, Execution(),
    directional_model=DirectionalModel(),
    signal_provider=your_existing_frozen_decision_function,
    funding_events=(),  # explicit zero-scheduled-funding scenario, NOT verified fees
)
```

The provider is a top-level, inspectable Python function with signature
`provider(closed_window, frozen_strategy, *, known_at)`. It returns the existing
signal contract (`reference`, `stop`, `target`, `known_at`, `candle_open`,
`evidence`) plus `direction="LONG"` or `"SHORT"`. Missing direction retains LONG.
`None` or `{"direction": "NO_TRADE"}` abstains. Reference must equal the last
closed price; stop/target must be on the correct side. Entry remains the next
bar open, with the same adverse spread/slippage and gap-admission constraints.
Existing rules' entries/stops/targets are never mirrored into a short strategy.
The user supplies separately frozen directional decisions later; this PR adds
no generator, discovery campaign, optimisation or profitability assertion.

The provider sees only the existing bounded closed-data window. Its source hash,
module and name are recorded; frozen strategy inputs also enter the experiment
identity. This is trusted research Python, not a sandbox: keep all provider
configuration in the frozen strategy, avoid mutable globals/dynamic imports,
and causally review any provider's feature dependencies before evaluating it.

## One account and bounded risk

Let `s=+1` LONG, `s=-1` SHORT, `q=quantity`, `E=entry fill`, `X=exit fill`.
Both sides post `C=q*E + entry_fee` out of the SAME cash account. No short-sale
proceeds become spendable cash and no leverage is added. Entry collateral less
entry fee is exactly 100% notional. At most one position (either side) is open.

* Entry fill is `open*(1+s*impact)`; exit fill is `raw_exit*(1-s*impact)`.
* Trade PnL is `s*q*(X-E)-entry_fee-exit_fee+funding-carry-liquidation_fee`.
* Exit releases `C+PnL` to the account. Short gains are from a falling price.
* Open equity adds segregated collateral, signed unrealised movement, paid
  funding/carry and reserves an adverse exit plus its fee. No end-of-data exit
  is fabricated; an open position stays open and is marked to market.
* Quantity is frozen at decision time; a cheaper entry never increases it.
  Worst accepted entry, protective exit impact, two fees and an explicit carry /
  adverse funding reserve fit within **0.5% of decision equity**. Entry notional
  is capped at **25%**. Lower configured limits remain lower. These are planned
  risk bounds, **not guarantees against gaps, funding spikes or insolvency**.
* `Execution` retains its original serialized cash sizing tag for backward
  identity compatibility. Only the explicit `directional_execution` definition
  and `unified-directional-v1` simulator version select collateral accounting;
  the shared Execution object supplies the numeric costs/risk/lot limits.

## Declared conservative costs, marks and liquidation

Defaults, all configurable/fingerprinted: 10bps/day adverse carry, 30bps/day
adverse funding **reserve**, 1% maintenance, 100bps liquidation clearance fee.
These are stress assumptions, **not verified exchange-specific historical
rates**. Trade fees/spread/slippage remain the original Execution inputs
(default 10bps each-side fee, 2bps full spread, 3bps each-side slippage).
An empty funding tuple means exactly zero scheduled funding, not complete data.
The reserve reduces quantity; it is not charged as a fictitious fee.

`FundingEvent(at, rate, mark)` supplies explicit UTC bar-open settlements.
Positive funding debits LONG and credits SHORT by `q*mark*rate`; negative funding
reverses that. Only a position held BEFORE that open pays/receives it. Inputs
must be ordered, unique, in-period, exactly aligned and include a positive
settlement mark. No missing historical marks/rates are invented/interpolated.
Use aligned recorded mark candles with
`DirectionalModel(mark_source="recorded_marks", provenance="...")` and
`mark_candles=tuple(...)`. Without them, `trade_price_proxy_assumption` explicitly
uses trade candles as marks. Neither provenance text nor a hash verifies a
venue dataset; verify it externally before claiming that level of evidence.

Carry is a conservative additional adverse cost on both sides, charged for each
whole occupied bar using the maximum of entry, trade high and mark high. It is
not claimed to be a perpetual's Spot borrow bill. Do not double-count verified
borrow/funding fees as both records and this scenario charge. Funded linear
contracts need no borrowed BTC; a true margin-Spot borrow product is outside v1.

Maintenance tests use each bar's worst mark including a clearance reserve. If
liquidation and a protective stop could both occur, liquidation is conservatively
chosen first, marked ambiguous, and closed at the worse observed extreme plus
adverse impact and fees. This intentionally pessimistic OHLC scenario cannot
establish intrabar ordering or a venue's liquidation/insurance behaviour.
Collateral deficits are fully debited to account cash and separately reported;
no free insurance payout or automatic top-up is invented. A deficit or adverse
carry+funding-debits reserve breach freezes subsequent entries and rejects the
run for selection. Funding credits never hide a gross debit budget breach.

Protective stop/target ordering mirrors by direction, with adverse stop first
when both are hit; stop gaps fill at the opening market, target gaps receive no
favourable price improvement. Missing candles leave positions unresolved,
freeze entries and suppress headline scored returns, preserving existing rules.

## Deliberate v1 limits / integration handoff

* Finite existing `strategy_default` holding caps (1..8760 bars) and next-open
  entry only. `stop_target_only` and pullback policies are rejected in this
  mode because unbounded carry / different admission needs a separate contract.
  They still work unchanged in default cash mode.
* Only aligned funding settlements are supported; use appropriately fine bars.
* No venue fee tiers, historical borrow availability, order-book depth, real
  market impact, insurance/ADL or liquidation-tick reconstruction is claimed.
* Historical mode only: `stage="forward_paper"` is refused, and the existing
  Spot-paper poller refuses directional promotions before reading any feed.
* Owner branch `research/long-move-discovery` was not modified. This draft is
  stacked on PR86. Owner integration later must combine the small runner/novel
  adapter edits explicitly; `signal_provider` is the reusable seam. Do not
  re-identify/reuse old fitted rule/source cohorts after code changes.
* Every model, mark-series fingerprint, explicit funding event, provider source
  and core-source hash enters a new experiment/cohort. Existing results remain
  immutable. LONG cash results are comparable numerically, but their new source
  version is not silently labelled as an old experiment.

## Verification

```bash
PYTHONPATH=src python -m pytest tests/test_unified_directional.py -q
PYTHONPATH=src python -m pytest
node --test tests/frontend/*.test.mjs  # Node22
ruff check --isolated --select E4,E7,E9,F src tests
```

New offline tests use disposable SQLite and predeclared synthetic financial
fixtures, including alternating LONG/SHORT trades in one account, open marks,
signed funding, liquidation deficits, costs, limits, interruptions and recovery.
The existing real decision function also executes through this same runner
under stressed costs; it is not replaced by fixture decisions. Independent
Decimal60 reconciliation reconstructs fills, cash, fees, carry, funding, every
equity mark and drawdown without simulator accounting helpers; corrupted ledgers
are rejected. Each completed directional run records this audit before immutable
completion. Synthetic passes prove accounting contracts, not a trading edge.
