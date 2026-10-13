# Offline forward endurance

`scripts/forward_endurance.py` drives the real `ForwardRunner`, market-data
normalizer, analysis, qualification, planning and paper-outcome observer with a
seeded synthetic BTC/USDT 1h candle source. A disposable SQLite database is
migrated inside `TemporaryDirectory`; the source offers the confirmed genesis
prefix and one forming candle which must never enter storage. A simulated event
advances the decision clock instead of sleeping. Default strategy, qualification,
planning, risk and runner parameters are unchanged. Explicit `ledger_start`
requests every simulated close, including initial insufficient-history closes.
No decisions or successful outcomes are inserted by the harness.

Run in a fresh source-only clone without `.env`, after installing `.[dev]`.
The CLI clears inherited environment before constructing settings; it has no
exchange, credential or database argument. JSON inputs/outputs must stay inside
the current workspace. The CLI is isolation by construction, not an OS sandbox.
For an enforced offline regression guard, use the development guard documented
in the accompanying PR evidence. Normal CI collects only the small tests below;
the 1,000-close runs are explicit opt-in commands.

```sh
python -m pytest -q tests/test_forward_endurance.py \
  tests/test_forward_endurance_comparison.py \
  tests/test_forward_endurance_recovery.py \
  tests/test_forward_retry_settlement.py
python scripts/forward_endurance.py --boundaries 1000 --case baseline \
  --output .pytest_cache/endurance-baseline.json
python scripts/forward_endurance.py --boundaries 1000 --case stress \
  --output .pytest_cache/endurance-stress.json
python scripts/forward_endurance.py \
  --compare .pytest_cache/endurance-baseline.json .pytest_cache/endurance-stress.json \
  --output .pytest_cache/endurance-comparison.json
```

Use separate fresh processes for baseline and stress resource measurements.
`--case both` is convenient for regression checks but shares process memo caches
and lifetime peak RSS. Progress is flushed every 25 confirmed boundaries to stdout
and atomically saved as JSON. A complete report is saved even if comparison fails;
the command then exits nonzero. `simulation_completed` describes execution, while
`checks_passed`, `canonical_agreement` and the individual hashes describe results.
A single-case report cannot establish agreement. Comparison requires matching
actual candle/config fingerprints when available; older reports lacking these
fields carry an explicit provenance limitation, not a retroactively certified
fingerprint.

## Exercised faults and assertions

The stress case repeats one processed boundary, redispatches already delivered
closed candles across pages, exhausts bounded exchange retries, gracefully stops
and reopens SQLite with a fresh engine/service, jumps three closes after downtime,
and injects exceptions after actual cycle/outcome SQL INSERTs before commit. Each
fault has an actual coverage flag or counter; short inputs may never naturally
produce a plan/outcome and must not claim outcome-fault coverage. The fault hooks
raise a test exception after real SQL. Separate regression tests issue failing
SQLite statements within the real transaction and immediately after bundle commit.

Checks verify every resolved close, unchanged stored candles, forming exclusion,
linked observations and plans, unique setup plans, consecutive outcome sequences
and supersession links, frozen prior rows, slot occupancy, same-boundary no-op,
foreign keys and SQLite integrity. The delayed-candle regression observes a real
plan, a missing entry candle, same-boundary arrival and target settlement while
retaining the earlier missing/incomplete audit versions. Entry, stop and target
transaction failures and recovery are checked separately.

Canonical comparison retains decision identities, setup facts, frozen levels,
configuration, observation links and outcome payloads. Only precisely listed
operational fields are excluded. Earlier missing-cycle siblings are retained and
reported separately from the final determinate conclusion. Full outcome-chain
hashes retain assigned IDs and parent links. Additional INCOMPLETE_DATA audit
versions may make literal chains differ; classification allows only that specific
extra audit history when every ordered non-incomplete material field still
matches. It reports literal inequality rather than claiming identical histories.
Negative controls prove that an altered prior payload or observation link is
caught even when the latest outcome is unchanged.

## Two narrow recovery repairs

A cycle/observation/plan bundle commits before its outcome transaction. Previously,
a failure in that second phase could leave a COMPLETE close whose outcome was
never repaired by an idle restart, and stale occupancy could block the next plan.
The service now reconciles lagging outcomes at the latest committed COMPLETE close
before returning IDLE or making a new slot decision. The query is scoped and
capped at the current confirmed boundary. The existing observer and recorded plan
levels are used; existing rows remain immutable. Already-current observations and
minute-resolution ambiguity rechecks are skipped on this recovery path. A same-end
INCOMPLETE_DATA version is reconsidered only if additional immutable base candles
have actually arrived.

An earlier MISSING_CANDLE audit row could also keep a close pending even after its
INSUFFICIENT_HISTORY conclusion, contrary to the existing documented settled-close
rule. Repeated retries widened later replay windows and changed snapshot/linked
observation identities for identical input. Pending selection now subtracts both
COMPLETE and INSUFFICIENT_HISTORY conclusions. It preserves both audit rows and
all identity algorithms; six isolated operational schedules demonstrated agreement
without dropping terminal setup facts or changing trading policy.

## Measurement and limits

Reports include actual wall time, pass median/p95/maximum, process-lifetime peak
RSS, cache counts, sampled scratch usage, and initial/final SQLite logical pages
and DB/WAL/SHM file sizes. Wall time includes harness integrity/hash checks;
per-pass timing measures the actual service call. Scratch totals include raw
responses and SQLite sidecars, not just the database. Sampled disk peaks are not
continuous peaks. RSS includes Python, imports, caches and end-of-run verification.
Measurements are machine-specific and may include other test workload contention.

No live-market endurance, process-kill/power-loss durability, disk-full recovery,
concurrent runner ownership, exchange-provider fidelity or monetary profitability
is established. Completed decisions remain immutable: arbitrary delayed data is
not promised to recreate an uninterrupted schedule. The original shallow-history
strict expected failure remains unchanged. Do not shorten reference history,
loosen thresholds or manufacture trades to make benchmarks or comparisons pass.
Existing functional/dashboard PRs #53–59 are separate; this patch changes no
visual implementation and requires human review/manual integration.

## Recorded initial engineering run

The initial machine run completed **1,000 closed boundaries per case** (2,000
in total), using real default rules and separate cold Python processes. Both
stored 17,133 observations, 34 naturally generated paper plans and 535 outcome
versions. Stress retained one additional missing-data cycle audit; its 1,000
resolved conclusions, frozen records, final slot and SQLite/FK checks passed.
Six injected operating scenarios actually fired, including six failed source
fetches, a fresh SQLite engine restart, a three-close catch-up, repeated polling,
and separate cycle/outcome transaction interruptions. There were 997 duplicate
closed-candle redispatches, without duplicate plans or terminal versions.

| Actual case | Wall seconds | Median pass | p95 pass | Peak lifetime RSS | Sampled scratch peak |
|---|---:|---:|---:|---:|---:|
| Baseline | 1,124.08 | 0.945s | 2.514s | 1,166,049,280 bytes | 418,969,788 bytes |
| Stress | 1,198.52 | 0.954s | 3.336s | 1,201,455,104 bytes | 420,000,175 bytes |

Stress SQLite logical allocation grew from 299,008 to 411,275,264 bytes; file and
sidecar growth was 411,009,248 bytes. The earlier baseline formatter did not
record separate SQLite growth or exact input signatures; these missing values
are not inferred. Its source hashes and full completed JSON were preserved. The
baseline's ignored launch wrapper failed on an incorrect result-field name after
successful engine checks/report save; the corrected stress launch passed.

**Strict saved comparison failed**: frozen plans, latest outcome payloads and full
outcome chains matched, but cycle/observation identity hashes differed. This is
reported as a failure rather than hidden by excluding terminal facts. The older
baseline predates report metadata and the settled-retry correction; final source
passed all 33 focused checks and the full 1,215 Python tests (one original strict
xfail), plus 298 frontend tests on Node22. Long runs do not claim exact final-build
or continuous live-market certification. The remaining identity difference and
substantial memory/storage growth require follow-up before unattended sustained
paper operation. There is no profitability or monetary-performance claim.
