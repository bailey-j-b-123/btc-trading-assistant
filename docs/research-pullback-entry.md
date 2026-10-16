# Optional pullback-open-v1 entry research

This is an opt-in Strategy Lab experiment, not a replacement for the production
planner or a claim of better trading. The default remains `entry_policy=None`:
existing admission, next-open market fills, sizing, costs and exits are preserved.
New core code creates a new source fingerprint; old result rows are never changed.

`PullbackEntry(retracement_fraction=Decimal('0.25'), max_wait_bars=6)` freezes a
maximum execution price when the existing decision function actually emits a
valid long signal:

```
cap = original_reference - fraction * (original_reference - original_stop)
```

The quantity is the **unchanged original algorithm**, committed at the original
decision using its original conservative ceiling/risk budget. Stops, targets,
source identity and admission gates stay fixed. A lower fill does not increase
quantity or leverage. This is alternative C (conditional pullback); alternative B
(earlier five-minute confirmation) is not implemented here.

Only a subsequent candle OPEN whose price plus adverse spread/slippage fits the
cap may fill. A low touching the cap does not establish a fill. We intentionally
miss intrabar retracements rather than guess OHLC ordering. Open prices at/below
the original stop are refused. The original same-bar adverse stop-first rule
applies after a genuine open fill. A position can remain open at the data end;
it retains the existing conservative liquidation mark and original exit policy.

Unfilled candles cancel the intent on structural-stop contact, original target
contact, missing data, or after six eligible opens (including a possible fill on
the sixth). A gap cancels rather than assumes an intermediate fill. A recorded
late decision cannot fill before `not_before`; the wait count starts only on
eligible opens. Pending intents occupy the single slot. Filled source plans
cannot fill twice; a cancelled plan can only be offered by a fresh real decision
at a later boundary under the existing retry rules. End-of-data pending intents
remain pending, never counted as trades or invented cancellations.

`entry_intent`, `entry_wait` and explicit cancellation rejection records preserve
missed/unfilled opportunities. Trades carry the version, cap and wait count.
The full policy definition is included only in experimental cohort definitions;
changing either parameter creates a different identity. Resuming a pending
checkpoint under a different policy is refused.

## Reuse and exact comparison

Pass `entry_policy=PullbackEntry(...)` to the existing `run_experiment`,
`novel.run`, `alpha.run`, or research `compare_brain` API. All 22 existing
configuration families share the same simulator switch without duplicating
strategy logic. The hierarchical BRAIN adapter keeps every hierarchy gate; it
does not promote diagnostic-only candidates from PR #84 or bypass alignment.
The live/forward planner is not changed.

Before market data is read, this command freezes one already-existing
`breakout_retest` hypothesis and four fixed 2023/2024 periods, both entries and
base/stress costs (16 separate wallets). It uses cached checksum-verified public
archives only, refuses existing output/storage, and disables network access.
2024 is **reused** validation; no protected 2025 price rows are read. The
2025-01-01 exclusive boundary is not 2025 data.

```bash
PYTHONPATH=src .venv/bin/python scripts/compare_pullback_entry.py \
  --source /absolute/marked/archive-lab \
  --root /absolute/new/pullback-lab \
  --output /absolute/new/pullback-evidence
```

This is one execution-policy comparison, not another tester or a 22-strategy
campaign. The chosen research breakout/retest is **not the full hierarchical
BRAIN strategy**. Its stop is the observed post-breakout low minus one-quarter
ATR; its target is the existing 2R rule, not an independently detected resistance
zone. Charts and reports must preserve this distinction. An execution benefit
on shared fills does not prove improved portfolio profitability. Do not promote
unless independent accounting, default parity, causality and recovery pass and
normal/stress net expectancy is positive in development and all reused folds.
No retuning after observing these results.
