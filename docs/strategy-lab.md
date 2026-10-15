# BRAIN Strategy Lab

Standalone, resumable, public-data-only **research and hypothetical paper
simulation**, separate from the original bot, dashboard and forward ledger.
No private API, credentials, orders, balances or account path exists. Research
variants are explicitly authorised by this task; original BRAIN rules are unchanged.

## Mac startup

In this dedicated checkout, once:

```sh
python3 -m venv .strategy-lab-runtime
.strategy-lab-runtime/bin/pip install -e '.[dev]'
chmod +x scripts/strategy-lab.command
```

Double-click `scripts/strategy-lab.command` or run:

```sh
./scripts/strategy-lab.command overnight --minutes 480
./scripts/strategy-lab.command status
./scripts/strategy-lab.command report > research-report.json
```

The script clears inherited environment/settings, uses its own interpreter and
fixed `.strategy-lab/` directory, and never reads `.env`. On macOS an overnight
child runs under `caffeinate -i`; keep the Mac powered with the lid open. This
prevents idle sleep, not shutdown, closed-lid sleep or loss of internet. ChatGPT,
Codex and Arena can be closed after starting it. No login agent or deployment is
installed. Control-C/SIGTERM checkpoints and stops; restart the same command to
resume. A second OS worker is refused. Status/report can read during a worker.
One worker, lower OS scheduling priority (default nice10), bounded work bursts
and a target active/idle duty cycle constrain work. These are soft wall-time
budgets, not a hard CPU percentage: an in-flight public request (30s maximum)
or one hierarchy boundary can finish after the budget expires.

Useful explicit commands (same script):

```sh
./scripts/strategy-lab.command acquire --months 2024-01 --timeframe 1h
./scripts/strategy-lab.command run --start 2024-01-01T00:00:00+00:00 --end 2024-02-01T00:00:00+00:00
./scripts/strategy-lab.command campaign --max-jobs 2 --max-seconds 60
./scripts/strategy-lab.command brain-compare --start 2024-01-01T00:00:00+00:00 --end 2024-02-01T00:00:00+00:00 --max-boundaries 24 --max-seconds 60
./scripts/strategy-lab.command paper --verify-feed 1h
./scripts/strategy-lab.command overnight --minutes 480 --max-jobs 2 --burst-seconds 60 --idle-seconds 60 --duty-cycle 0.5
```

CLI module equivalent: `python -m trading_assistant.strategy_lab --root
<NEW-empty-lab-directory> <command>`. Storage refuses symlinks, `data`, `backups`,
`.env` components and unmarked nonempty directories. Never point it at operating
bot storage. All original history stays untouched; the lab has its own schema.
Configuration JSON via `campaign/overnight --config file.json` is fingerprinted;
unknown keys, invalid splits and invalid/bounded parameters fail. Execution and
validation dataclass fields are documented in `strategy_lab/models.py` and
`validation.py`. Defaults below are research assumptions, not original risk policy.

## Real data and reproducibility

Fixed [Binance public Spot archives](https://github.com/binance/binance-public-data),
BTCUSDT only. Independent SHA256 receipt must name the exact monthly ZIP.
Pre-2025 milliseconds and 2025+ microseconds are decoded explicitly. OHLCV values
and UTC alignment are validated, and stored candles cannot be rewritten.
Duplicate/disordered rows fail. Gaps are declared, never filled. Research may
exclude a source row whose close field is not its full nominal interval; its
raw row, recorded/nominal close, source checksum and exclusion remain in receipts.
The decoder's default strict behaviour remains unchanged.

An active position crossing missing market data stays **unresolved**: no invented
exit, no valid equity/return, no promotion. A flat portfolio may resume signals
after sufficient contiguous history. Dataset boundary candles are mandatory.
Fingerprint covers exact candles, source receipts, configuration and actual
decision/simulation source. IDs and atomic state/event batches make repeated
processing and restarts deterministic. Completed results are immutable. A source
change creates a separate cohort; existing forward candidates refuse source drift.

## Track A: actual BRAIN hierarchy

Existing read-only 4H structure, 1H qualification, 15M confirmation, 5M execution
and Step6 structural planning are reused. A1 is checked against the original gate
at each boundary. A2 admits a counter-trend case only for an already QUALIFIED
liquidity/range reversal, CONFIRMING15M, TRIGGERED5M, and a 5M close beyond the
same recorded reference band in the setup direction. A3 removes **only** the
directional counter-trend veto. Missing/stale data, unknown/conflicting context,
invalidation and all other gates remain. Actual alignment is always retained.

These policies are frozen before evaluation and never edit the live hierarchy.
The original planner must independently produce an actionable structural plan.
Research retains its exact entry/stop, uses the first genuine structural target,
next5M-open market fill and a declared48h research time exit. A source plan may
signal once per policy wallet. Bearish plans are counted as unsupported cash-Spot
shorts; they do not become imaginary profitable trades. The bounded initial
comparison cohort is January2024 with all December2023 warmup candles retained.
The known shallow-history limitation remains; these results are cohort-specific.
Hourly qualification tuples are reused for identical inputs, without changing
their contents. Larger cohorts cost substantially more than simple strategies.

## Track B: bounded candidate search

16 frozen sensible candidates: SMA crossover, Donchian closed-price breakout,
rolling arithmetic RSI recovery (not Wilder RSI), and momentum reversal above
slow MA; two predetermined lookback pairs10/30 and20/50, two targets2R and3R.
Existing BRAIN Wilder ATR supplies stop distance on each causal trailing window.
The simulator accepts a deterministic decision adapter; Track A demonstrates
extension using actual original plans. No unmerged12-pattern package is assumed
present on main. Additional standalone families and short/limit execution models
require new tested implementations; this initial engine does not claim them.

Every candidate wallet starts at10,000 hypothetical USDT, risks at most0.5%
planned equity, caps notional at25%, one position, cash Spot LONG only.
Base fees10bps each side, full spread2bps, adverse slippage3bps each side;
stress doubles fees/spread and triples slippage. BTC lot step0.00001 and minimum
notional10 are explicit research assumptions, not verified historical venue filters.
Size is frozen at the decision using the50bps entry-gap ceiling, stop impact and
roundtrip fees, then rounded down. Unfilled gaps, invalid levels, unavailable cash,
occupied slots and small lots are explicit refusals. Stops gapping adversely use
the open; target gaps get no favourable improvement. Both stop/target in one bar
use adverse stop first. A stop gap can exceed planned risk. No minute-bar ordering
or limit fill is invented. Equity/drawdown use liquidation-costed close marks;
intrabar drawdown, market impact, tax and future fee schedules are unmodelled.

## Validation, custody and forward separation

Default development2020–2022; three consecutive unchanged-rule validation folds
Jan–Apr, May–Aug, Sep–Dec2023. Final holdout2025 is not evaluated until rules and
one survivor are frozen. Minimum closed samples100/20/50 respectively. Each
base/stress job requires positive net expectancy/return, no unfinished/unresolved
position, close-mark drawdown<=20%, ambiguity<=10%, and a positive deterministic
trade-block bootstrap lower meanR (4000 resamples, block5, alpha0.05 divided by
the entire declared search size). This exploratory statistic is **not** proven
family-wise error control or a guarantee under nonstationary dependent returns.
Folds are fixed independent chronological tests, not rolling parameter retraining.

One survivor is selected by its weakest lower bound before holdout access. A
failed holdout cannot be retuned/reused in another campaign, timeframe or overlapping
period. Prior recorded experiments also taint a proposed holdout. Custody survives
restart; base/stress reuse is only the same frozen final evaluation. Manual external
viewing cannot be detected; operators must preserve untouched performance evidence.
If no candidate passes, the finite grid exhausts honestly and waits; it does not
generate random replacements to manufacture a winner. New sensible grids must
declare a new full search budget and genuinely unused holdout.

Only passing candidates enter separate immutable `promotions`, `paper_state`,
`paper_events`, `lab_live_candles` tables. One public GET writer serves all wallets.
[Binance public klines](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)
must be current, closed and contiguous. Actual response/decision timestamps and
source hashes are recorded. A decision cannot fill a bar that opened before it
was actually observed. Downtime may settle already-known positions from genuine
subsequent bars; missed decisions are recorded, never backdated into new entries.
Very long outages with lost ordering leave active positions unresolved. Repeated
polls do not duplicate candles, plans, trades or decisions; contradictory revised
closed candles stop rather than rewrite history. Historical next-open and forward
receipt-delayed fills differ explicitly; report shows separate periods/denominators.

## Operator health and verification

Status reports attempted/completed experiments, candidate rejection/validation/
promotion counts, research/brain checkpoints, worker lock and heartbeat age,
recent errors, SQLite integrity and foreign-key checks. A lock alone is not a
claim the worker is healthy. Temporary HTTP outages/stale public feeds back off;
permanent malformed data stops with a durable error. Reports expose costed metrics,
by-month/regime summaries, exclusions, raw sample counts and historical vs forward
evidence. No zero-trade candidate is labelled live-profitable.

Offline tests use synthetic markets **only to verify software**: actual detections,
costed fills, money recalculation,1200-boundary restart equality, SQL interruptions,
worker exclusion, frozen policy exceptions, weak-strategy rejection, the full
validation/promotion pipeline, holdout reuse refusal, and delayed forward fills.
Use `python -m pytest tests/test_strategy_lab*.py tests/test_public_archive.py`;
full Python/Node22 suites are required before PR publication.

Genuine runs and exact test/source evidence belong in the task's durable progress
report. A short positive historical experiment is not an edge. Public feed access
with zero qualified candidates verifies connectivity only, not forward performance.
