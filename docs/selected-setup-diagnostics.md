# Recorded baseline setup diagnostics v1

This reads existing Strategy Lab hierarchy observations. It is not another
strategy tester: no replay, download, decision, simulated fill, cost change or
promotion occurs. It supplies descriptive evidence before trading-rule research.

## Existing evidence, with honest denominators

The frozen protocol and summary are in `docs/research/selected-setup-protocol.json`
and `docs/research/selected-setup-summary.json`. Selection is the complete already
examined January2024 cohort, not an attractive subperiod or fresh validation.
The source run and expected observation hash were fixed before aggregate analysis.

The8929 unique recorded boundaries contain316 distinct **selected** setup
identities.119 were observed QUALIFIED at least once;60 were observed aligned,
248 confirming,25 triggered and3 strict-baseline hierarchy-plannable. These are
**independent flags**, not a sequential conversion funnel: for example confirmation
may be reported for a WATCH setup. Counts must not be multiplied or interpreted
as causal proof that a rule harms returns.36 boundaries have no selected setup;
two selected setups were created before the cohort began, so their observed
stage times are left-censored rather than first-ever detection times.

The source contains13 recorded plans across the union of A1/A2/A3 planning
requests (12NO_PLAN,1PLANNABLE). Only4plan IDs were observed alongside a
strict-baseline hierarchy-plannable decision (3NO_PLAN,1PLANNABLE); this is not
four different setups. The complete plan catalog is retained unchanged, while
`baseline_observed_plan_ids` explicitly identifies baseline observations.
An actionable plan is counted as a baseline stage only when the same observation
also records the strict baseline decision as plannable. A2/A3 decisions are never
substituted for the original snapshot decision.

One complete planning proposal does not imply an executable cash-Spot long or
a profitable trade. Prior independent #77 accounting and cost diagnostics remain
the evidence for those downstream limitations. This module does not recompute
PnL or silently treat missing fills as successful trades.

## Identity, time and rejection records

Unique selected identities combine exchange, symbol, setup timeframe, setup ID,
creation time and qualification fingerprint. Similar prices are never an identity
join. A reference change under the same setup scope raises an error. The report
records first/last observed times, repeated observations, first observed stage
times, exact baseline decisions/reasons, alignment/confirmation/execution states,
pending/opposing qualification rules, recorded planning reasons and plan IDs.
It preserves the original recorded plan objects, including their exact levels,
rules and reasons; it invents no missing target or net-R value.

Identical duplicate boundaries are ignored in stage counts; conflicting duplicates
and new out-of-order boundaries raise errors. Snapshot decision times must match
their boundary; known evidence/plan timestamps cannot be in the future. The raw
source stream is independently hashed. Dictionary inputs and source rows remain
unchanged. A completed explicitly selected run and a marked source-only lab are
required. Protected2025 periods are denied before observation reads.

## Reuse

```bash
python -m trading_assistant.strategy_lab.opportunity_diagnostics \
  --source /absolute/path/to/own/marked/hierarchy-repaired \
  --run 21ebae8915c06c1e64314ff3b0819c95ff66e7d612498a082086fe339dd32dee \
  > /absolute/path/to/a/new/report.json
```

The command opens the source SQLite URI read-only and prints JSON; it does not
create or edit a database. Never supply an operating-bot database, production
directory, credentials or protected research data. The complete1.3MB local report
is preserved with the owner; the public summary records its checksum and source
hash for reproducibility. Independent SQL DISTINCT counts confirm all setup/stage
counts; the complete report reproduces byte-for-byte, and hashes of every stored
research table checked remain unchanged. The eight offline regression tests use synthetic
recorded contracts, never claim detected market events or market performance.

## Evidence still missing

Only the selected setup is stored at each boundary. `candidate_count` is not a
deduplicated catalog of unselected opportunities; this report cannot recover that
catalog, measure their hypothetical fills, classify exact retest contact from
unrecorded candle geometry, or establish counterfactual returns. Those fields stay
explicitly unavailable. It does not alter the previous shallow-history limitation.

Next instrumentation should record all causally available candidate IDs and
contact/near-touch geometry inside the existing research runner under a new
cohort version. Only after those diagnostics should a fixed protocol compare
one rule variation at a time with unchanged risk and realistic costs. No trading
rule has been loosened by this task. This draft depends on #82 and its unmerged
research chain; no production change, merge or deployment is included.
