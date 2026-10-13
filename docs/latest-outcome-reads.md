# Latest paper-outcome reads

`ForwardLedgerRepository.latest_outcomes` serves runner reconciliation, slot
occupancy and forward reporting. It returns the greatest immutable sequence for
each matching paper plan, ordered by paper-plan ID. Previously it selected every
historical version, materialized all ORM rows and decoded every payload before
keeping only the last version in Python. Work grew with history that the caller
did not request.

The reader now selects `MAX(sequence)` per matching plan in a scoped subquery,
then joins the full row at that sequence. The existing unique plan/sequence
constraint guarantees one result per plan. Exchange, symbol and optional timeframe
filters are unchanged; `timeframe=None` still includes every timeframe of the
instrument. It executes one SELECT snapshot and decodes only returned payloads.
A plan without an outcome is still absent from the results. Timestamp ties do
not affect selection: sequence determines the latest version.

There is no migration, index addition, record update, trading-rule change,
fingerprint/version change or altered slot/outcome policy. Existing historical
`outcome_versions` and `get_outcome` reads retain every original record. SQLite
still scans the matching covering index to aggregate sequences; this is not a
constant-time lookup or a promise of faster reads on every empty/tiny ledger.

## Isolated evidence

The regression fixture uses two genuinely qualified stock-engine plans, 1h and
15m, and twenty actual observed versions each. It manually inserts no successful
outcomes. Before this change, three bounded-decoding cases failed (40 payloads for
two returned plans; 20 for one scoped plan), while three value/scope/history
controls passed. Afterward all six passed. Controls check exact domain equality,
plan ordering, instrument-wide/timeframe/empty scope, plans before their first
outcome, a real missing-then-delayed entry at the same clock, SELECT-only reads,
frozen full SQL history and SQLite/FK integrity.

An independent actual-accessor microbenchmark compared the original implementation
with this reader on the same disposable ledger: 3 warmups, then 25 measured reads
per implementation with GC and tracemalloc instrumentation. Median read time was
9.149 → 2.031 ms; median transient traced Python allocation was 201,632 → 59,409
bytes. Payload conversions were 40 → 2. These are measured warm read results for
this small synthetic fixture. They exclude native/SQLite allocations and establish
no whole-run RSS, cache, throughput, profitability or live-operation improvement.
Query-plan inspection used existing scoped-plan and plan/sequence indexes.

```sh
python -m pytest -q tests/test_forward_latest_outcome_reads.py
python -m pytest
npm test
```

Development verification uses the supplied ignored offline guard in a fresh
source-only checkout, with temporary SQLite and fake exchange data. Full Node
checks use Node22. No operating database, credentials, exchange, runner or dashboard
is accessed.

This is an independent main-based read optimization. PR63's interruption recovery
and endurance harness, the unresolved catch-up identity envelope, and Arena's
Dashboard V2 branches remain separate. Compact per-boundary evidence/streaming
harness projections are a future task; no old comparison failure is normalized
away by this change. Human review/manual integration remains required.
