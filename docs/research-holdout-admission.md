# Sealed-2025 admission checks

Research only. No real protected data was downloaded or read to develop this
repair. All 2025 test candles and archives are synthetic correctness fixtures,
never market-performance evidence. This does not grant permission to run the
real holdout.

## Reproduced gap

The existing campaign froze a candidate and required its exact explicit approval
receipt before holdout custody. Generic Strategy Lab commands did not share that
check: `acquire` accepted a protected month and `run_experiment` accepted protected
timestamps labelled `development`, `walk_forward`, `holdout` or `forward_paper`.
`dataset` also allowed cached protected prices to be queried directly.

A pre-network sentinel confirmed acquisition reached its download callback for
2025-01; the callback threw without contacting a host. A three-candle synthetic
2025 experiment completed as development. Nine new regressions fail against the
parent. Thus a stage label was not a custody boundary, despite the campaign's
separate correct approval flow.

## Narrow repair

`strategy_lab/holdout.py` rejects periods overlapping
`[2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)` by default. This fixed protected
period is a research custody boundary, not a trading threshold.

- `data.acquire` checks the entire requested month list before opening a
  repository, reading any archive/checksum or calling any downloader. A mixed
  2024/2025 request fails without partially acquiring the permitted month.
- `data.dataset` checks before accessing a repository, including cached prices.
- `runner.run_experiment` checks before experiment creation/resume or simulation.
  Supplying a different stage does not grant access.
- Only the existing campaign path, after its exact frozen-candidate approval and
  one-use/overlap custody checks, enters an internal context scoped to that Store
  object, period, timeframe and candidate. Experiment admission additionally
  requires `stage=holdout`. The scope is reset in `finally`, including interruption.
  There is no new CLI flag, boolean bypass, grant or automatic approval.

Periods ending exactly at the start of 2025 do not include 2025 candles; periods
starting exactly in 2026 do not contain sealed2025. Research selection dates
still need the owner's separate protocol; these boundary tests grant no permission
to evaluate 2026. Chronological campaign validation, explicit approval, one-use
custody, risk and financial gates stay intact.

## Regression evidence

`python -m pytest tests/test_research_holdout_admission.py tests/test_strategy_lab_validation.py tests/test_strategy_lab.py tests/test_research_source_identity.py`
passes52 tests. New12 cases cover mixed acquisition, pre-query cache guards, four
stage-label attempts, scope mismatch, exception reset, UTC/boundary semantics and
an actual detector/gate campaign using exclusively synthetic archives. That
campaign cannot request its synthetic2025 month before approval; a wrong receipt
fails; the exact test-only receipt permits ten frozen campaign jobs, after which
generic cached access is again refused. No trading decisions are injected.

Three existing archive-correctness fixtures used2025 timestamps for ordinary
acquisition. They now use synthetic2024-January instead, preserving every closure,
gap, checksum, idempotency and record-count assertion. The new tests explicitly
cover2025 refusal; no test is disabled or weakened to hide a failure.

## Limits and compatibility

These are supported research-entry-point admission checks, not an operating-system
sandbox. Someone deliberately executing arbitrary Python or editing source can
bypass application checks or call low-level decoders/network libraries directly.
They cannot be made into a security boundary by a boolean API. Existing cached
data and results are not deleted, rewritten or re-identified. The new core source
digest creates separate research cohorts; old rows stay immutable. Existing
already-displayed result metadata may still be reported without reopening prices.

No production data/ingestion, strategies, costs, stops/targets, risk, orders,
deployment or holdout evaluation is changed. This focused draft depends on #81
and its unmerged research chain; review and merge remain human actions.
