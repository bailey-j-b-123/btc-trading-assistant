# Optional research exit policy — stop/target only

This is a small extension to the EXISTING Strategy Lab simulator. It is not a
new tester, strategy, production change or full22-strategy campaign.

`exit_policy="strategy_default"` (the omitted default) keeps every strategy's
existing stop, target AND maximum holding-time exits exactly as before.
`exit_policy="stop_target_only"` disables ONLY the time-exit branch. After
entry, a trade can settle only through its existing stop or target. It can
remain open for an arbitrary number of observed bars; `max_hold` is unchanged
in the original strategy definition and simply does not force an exit.

Entries, qualification, signals, stops, targets, position-sizing algorithm,
fees, spread, slippage, lot/notional rules, single-position occupancy and
adverse ambiguity/gap assumptions are unchanged. Longer occupancy can block
later entries, and different equity naturally changes later sized quantities
under the same sizing algorithm. That is expected, not a duplicated strategy.
Invalid policy names refuse work before an experiment is inserted.

Historical end-of-data NEVER triggers liquidation. An open position retains
its quantity, fill, stop, target and accumulated held bars in saved state.
Its reported portfolio equity uses the EXISTING conservative net-liquidation
mark (last recorded close less assumed exit friction/fees); cash is not
credited and no closed trade/exit event is invented. Distinguish that marked
return from realised closed-trade PnL. Missing data still creates an unresolved
observation and freezes entries; it does not fabricate a stop or target hit.
No gap or end-of-data record is presented as a successful settlement.

## Reuse across all22 configurations

The setting lives in shared `simulation.step`, with one validated branch.
All16 original configurations use `runner.run_experiment`; the four PR76
hypotheses use `novel.run`; the two Alpha hypotheses use `alpha.run`. Each
existing function now accepts the SAME optional keyword:

```python
run_experiment(store, candles, manifest, original_strategy, execution,
               exit_policy="stop_target_only")
novel.run(store, candles, manifest, novel_hypothesis, "base", "development",
          exit_policy="stop_target_only")
alpha.run(store, candles, manifest, alpha_hypothesis, "base", "development",
          exit_policy="stop_target_only")
```

Their existing entry/exit price generation is not copied or rewritten.
The shared independent audit rejects time-exit records under the new policy.
Existing defaults, campaign scheduling, paper runner, production BRAIN,
Execution model and research source datasets are not changed.

Existing CLI entry points also expose `--exit-policy` with validated choices:

```sh
python -m trading_assistant.strategy_lab --root /NEW/marked-lab run \
  --start 2023-04-01T00:00:00+00:00 --end 2024-01-01T00:00:00+00:00 \
  --family sma_cross --timeframe 1h --exit-policy stop_target_only

# Existing full novel/Alpha CLI commands accept the same flag. Do not launch
# their matrices until separately authorised. Defaults remain strategy_default.
python -m trading_assistant.strategy_lab.novel --help
python -m trading_assistant.strategy_lab.alpha --help
```

Every newly recorded experiment definition explicitly includes `exit_policy`.
The same strategy/data/cost with different exit settings receives different
experiment IDs. Existing source fingerprints also change because the simulator
changed; new default runs are a new SOURCE cohort, numerically unchanged.
Earlier experiment rows/results/frozen receipts are never retagged or rewritten.
Use a NEW marked research root; do not overwrite earlier evidence output paths.
The unchanged strategy version describes its original rules; the separate
serialized exit setting records this explicit research override.

## Actual ONE-pair demonstration

Starting source: verified PR78`597d0eec334c10c4d58caa54ed32f8064a0b0d40`.
One original Alpha UTC-first/last-hour hypothesis; normal costs only;
Apr1–Dec31 2023 inclusive hourly opens, end Jan1 2024 exclusive. Exactly6600
recorded hourly candles. Nine cached official Binance BTCUSDT ZIP/checksum
pairs were independently decoded/reverified into a NEW marked lab. No downloads
and no original source SQLite opened. Source bytes remained unchanged.

The frozen PR78 simulator raw SHA256 is
`fe49b41adf8e76cc66676913d7c6a8bcce1fb5335ae37f46d75349387e1649d4`.
Executing that actual old code and the new omitted-default path produced
EXACTLY equal states AND events at EVERY one of6600boundaries using identical
current provenance IDs. Strategy/cost calculation ASTs also match the base.
All56 actual signal objects are identical between default and stop/target-only:
exact entries, stops, targets, evidence and signal identities are preserved.
New historical cohort source fingerprint:
`9fed6125aba96c04c79fea7e7cd4318b446981ca267ac0820d151c7d95a5eb54`.

| Metric | Original default | Stop/target only |
|---|---:|---:|
| Qualified entry opportunities |56|56|
| Accepted simulated fills |56|45|
| Completed trades |56|44|
| Open at historical end |0|1|
| Later entries blocked by occupied slot |0|11|
| Time exits |54|0|
| Stop exits |2|27|
| Target exits |0|17|
| Realised closed-trade PnL,USDT |-395.271636|-100.722834|
| Final marked portfolio equity,USDT |9604.728364|9901.185585|
| Marked net return |-3.952716%|-0.988144%|
| Maximum close-mark drawdown |3.952716%|3.331360%|

ALL11 blocked signals match trades that actually filled in the default replay.
For example2023-10-25 23:00UTC, entry reference34606.48, stop33876.03074776,
target36067.37850448;2023-11-06 23:00UTC, reference35115.73,
stop34758.16957610,target35830.85084780. These became occupied-slot rejections,
not lost observations or fabricated hypothetical profits. Every blocked event
and its matching default entry is in the [receipt](research/exit-policy-demo.json).

The final open position entered2023-12-30 23:00UTC and remained open after25
hourly bars despite its original1hour maximum:0.05842BTC, stop41674.92106540,
target43048.34786920. Cash7434.436142905923USDT remains debited for the entry.
Net-liquidation marking contributes1.908419USDT of unrealised net PnL, yielding
-98.814415USDT total marked PnL. No terminal closing trade is booked. The44
settled trades alone lost100.722834USDT.

This is one mechanical demonstration, not a profitable-strategy claim,
untouched validation or evidence about all22 configurations. It still loses
money and ends with unresolved economic exposure in an OPEN (not data-gap)
position. The full22comparison has NOT been run. No sealed2025 data accessed;
no2026 prices used; no production, credentials, orders, merge or deployment.

## Tests and verification

45 new offline cases cover all22 strategy configurations through the same
switch, unchanged omitted/explicit default, stops/targets after hold expiry,
adverse stop-first ambiguity and opening gaps, no end-date closure, marked
open equity, unresolved data gaps, invalid settings, distinct experiment IDs,
immutable completed results, restart recovery, novel/Alpha runner propagation,
independent accounting of open positions, and occupied-slot blocking of an
otherwise EXACTLY unchanged real strategy signal.

Targeted command: `python -m pytest tests/test_research_exit_policy.py`.
Full suite: `python -m pytest`; frontend: Node22 `npm test`. Exact final counts
and published-head GitHub CI evidence are recorded in the draft PR description.
The original documented shallow-history strict expected failure is untouched.
Local guarded tests use only temporary synthetic data; synthetic outcomes are
never included in the actual historical comparison above.

## Run the full comparison LATER — instructions only, not executed

Reuse the existing functions; no new simulator or duplicate strategy logic.
Load a VERIFIED dataset into a NEW marked root first (for example the existing
`alpha.import_archives(store, marked_cached_source)` followed by `dataset`).
Keep original datasets/results intact. Use the declared development window
below, not the original automatic campaign/holdout workflow.

```python
from datetime import UTC, datetime
from trading_assistant.strategy_lab import alpha, novel
from trading_assistant.strategy_lab.data import dataset
from trading_assistant.strategy_lab.models import candidates
from trading_assistant.strategy_lab.runner import run_experiment
from trading_assistant.strategy_lab.store import Store

# AFTER separate authorisation to launch the full comparison:
store = Store("/absolute/NEW-comparison-lab")
try:
    alpha.import_archives(store, "/absolute/marked-2023-24-cached-archives")
    candles, manifest = dataset(store, datetime(2023,4,1,tzinfo=UTC),
                               datetime(2024,1,1,tzinfo=UTC))
    for cost in ("base", "stress"):
        execution = novel.costs(cost)
        for policy in ("strategy_default", "stop_target_only"):
            for strategy in candidates(16):
                run_experiment(store, candles, manifest, strategy, execution,
                               exit_policy=policy)
            for name in novel.NAMES:
                novel.run(store, candles, manifest, novel.Hypothesis(name),
                          cost, "development", exit_policy=policy)
            for name in alpha.NAMES:
                alpha.run(store, candles, manifest, alpha.Hypothesis(name),
                          cost, "development", exit_policy=policy)
finally:
    store.close()
```

That would be44normal-cost wallets or88including stress, NOT independent
profitability samples. Each existing runner supports atomic checkpoints and
`max_boundaries=N` to bound an individual attempt; rerun identical calls to
resume until complete. Inspect open positions, occupied-slot rejections and
marked versus settled PnL before comparing returns. Never force a last-price
exit or count an open marked gain as a completed win. Test each additional
period independently and label already-examined dates honestly.2025stays sealed.
