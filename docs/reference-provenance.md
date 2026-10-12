# Reference provenance: what stored history can account for

**Status: implemented for planning eligibility (`reference-provenance-v1`). The
detection horizon is a documented, still-open limit.** This change decides *which
plans may be recorded*; it changes no breakout, retest, target, stop, or
observation rule, rewrites no ledger row, and backfills nothing.

## The problem in one paragraph

The required-window gate (`market_data/integrity.py`, README "Required-window
gate") proves that the trailing closes a decision indexes are all stored. It
cannot prove that the **levels** those closes were tested against were themselves
formed inside stored history. A confirmed swing, a clustered zone, or an active
range is carried in the analysis' own catalog, and its band, touch counts, and
breakout/failure/sweep record accumulate from the first candle the analysis
loaded. So a decision can depend on evidence arbitrarily far back, while the
record starts wherever ingestion started. Measured in
[`decision-invariance.md`](decision-invariance.md): at one synthetic boundary,
520 closes of warm-up behind the decision was still not enough for the live
setup set to match the full-history answer — the reference lookback is
effectively unbounded by any fixed depth, which is why no bigger constant fixes
this and why one was not introduced here.

`docs/audits/BRAIN_INDEPENDENT_SYSTEM_AUDIT_PR42.md` records this as **V1
(CRITICAL)**. Its recommended smallest safe first repair is what this document
describes.

## The rule

`setup_qualification/provenance.py` answers one question per close, read-only,
from the frame the decision already built:

> Is every live setup's reference level accounted for by stored history?

For each live setup (`WATCH`/`QUALIFIED`) the assessment collects the open times
of the evidence its reference actually rests on:

* the swings the level was built from,
* the zone's own formation and member record, or the range's start and touches,
* the **oldest catalog event recorded against the same reference id** — the
  earliest moment the record can show that level mattered.

A level is *accountable* at a close only when that oldest evidence sits far
enough inside stored history for its own formation to have been observed:

| Reference kind | Accountable when | Why |
| --- | --- | --- |
| `swing_high` / `swing_low` | `oldest ≥ first_stored_open + swings.left_window × interval` | an older candle could hold the extreme instead, so the left window must be stored |
| `zone`, `range_high`, `range_low` | `oldest > first_stored_open` | evidence *on the first stored candle* is itself the proof the record started mid-formation |

Verdicts are `COMPLETE`, `TRUNCATED`, or `UNAVAILABLE`. `UNAVAILABLE` (no frame,
no snapshot, no stored candle, or a seed event that is not in the loaded catalog)
withholds planning exactly as `TRUNCATED` does: an unverifiable level is not a
verified one, and confidence is never inferred from missing evidence.

## What the verdict changes

* **Forward runner.** A `TRUNCATED`/`UNAVAILABLE` setup gets no Step 6 plan, so
  no paper trade and no outcome trajectory. The candidate itself is still
  recorded in full (setup, rules, evidence, fingerprints), and its observation
  carries the deterministic reason
  `NO TRADE — reference-level provenance is not accounted for by stored history
  (warm-up incomplete); planning withheld.`
  It is never recorded as a *missed opportunity* and never as a market verdict.
  The cycle row gains a note quoting the assessment summary. Every candidate whose
  provenance *is* accounted for is planned exactly as before.
* **Dashboard.** `reference_provenance` is a first-class payload section (per-setup
  verdicts, the stored start, the loaded window start, swing counts, the advisory
  notes). When the selected setup is blocked, `plan` is `null`, `planning.reasons`
  carries the withheld-planning sentence, the decision centre is disabled, and
  `POST /api/dashboard/decisions` refuses with `proposal_not_decidable` — the same
  contract the required-window gate already enforces. Nothing is displayed as a
  plan that the record cannot vouch for.
* **Versions, not rewrites.** `_strategy_versions` gains
  `("reference_provenance_rules", "reference-provenance-v1")`, which flows into
  `version_fingerprint` and therefore into new cycle/observation identities. Rows
  recorded before this contract keep their stored fingerprint, so the two policies
  can never be merged into one cohort silently. `forward-ledger-v3` is unchanged:
  row shape, slot occupancy, and outcome-resolution semantics are untouched.
* **No migration.** The verdict reuses existing columns (`no_trade_reason`,
  `notes`, `strategy_versions`). No table, constraint, or historical row changed.
* **Historical validation** deliberately does **not** apply the gate. It reports a
  new limitation instead: cohort replay does not reproduce forward selection or
  occupancy, so cohort results are non-comparable with the forward paper book.
  Making them comparable (or keeping them labelled as proposal diagnostics) is
  step 2 of the audit's recommended sequence.

## What it does not claim

Two limits are stated because overstating this gate would be worse than not
shipping it:

1. **It bounds what a record may plan, not what a record can see.** A boundary
   whose stored history is too short to contain a level can still never learn
   that the level existed, and so can report no setup at all. Withholding cannot
   recover an opportunity that was never visible: the V1 setup-list difference in
   `decision-invariance.md` is **unchanged**, and its strict xfail stays.
2. **Under-observation is reported, never inferred away.** When the earliest
   confirmed swing sits at the start of stored history, the assessment sets
   `inventory_at_history_start` and an advisory reason saying that this close
   cannot be shown to include every level that existed, so a quiet close there is
   not a settled no-trade verdict. That advisory does **not** withhold anything: a
   run cannot decide its own blindness from evidence it does not have, so the
   statement is recorded and displayed rather than acted on silently.

The same distinction applies to counts: `AMBIGUOUS`, occupancy, and unsettled
outcomes are unchanged, and no reporting denominator moved. No performance claim
is supported by this change.

## Evidence for the shipped behaviour

* `tests/test_reference_provenance.py` — the rule and its boundary cases (level at
  the edge, lead-in stored exactly, a same-level touch on the first stored candle,
  a zone observed one candle in), that the gate follows the parameters rather than
  a constant, that only the affected setups are blocked, that unverifiable means
  withheld, that the forward path records the reason without a paper plan, and
  that the dashboard withholds the plan and refuses the decision.
* `tests/test_decision_invariance.py` — plan eligibility and identities are now
  compared between full and shallow starts (a shallow record is a strict subset:
  it can under-report, never over-report), plus the safety property: whatever a
  short record plans, it plans on levels its own history accounts for.
* Measured on the synthetic invariance series at hour 820: stored starts 300 and
  500 both leave 24 live setups with every accepted plan provenance-`COMPLETE`,
  while full history has 45. The gate withheld nothing there — because nothing in
  that shallow decision rested on an unaccountable level. The 21 missing setups
  are the documented residual, not a withheld plan.

## Measured cost (synthetic, sandbox)

One assessment at hour 940 of the 1,000-candle invariance series, where the loaded
catalog holds 4,520 events and 254 confirmed swings:

| Step | Time |
| --- | --- |
| `DashboardService._evaluate` (frame build + qualification replay) | 10,555 ms |
| `assess_required_window` | 0.32 ms |
| `assess_reference_provenance` | 4.0 ms |

The verdict re-reads objects the decision already built and runs one pass over the
event catalog, so it is two orders of magnitude below the replay it annotates and
does not grow the replay. Real-history cost tracks catalog size the same way; it
adds no query, no write, and no second analysis.

## Reading it on the Mac

The audit's Mac-only checks still apply. Expected reading of a fresh or young
database:

* Plans withheld with the provenance reason near the **start of stored history**
  is correct behaviour, not a bug: those levels were already forming when the
  record began. The rate should fall as history accumulates.
* To quantify it, read `forward_observations.no_trade_reason` for the recorded
  reason, `forward_cycles.notes` for the per-close summary, and
  `GET /api/dashboard` → `reference_provenance` for the live verdict. Use a
  read-only connection; never edit the ledger to test.
* Backfill remains on hold. Appending older candles changes recomputed decisions
  near the old start (see `decision-invariance.md`) while frozen rows stay
  unchanged; this gate does not make backfill safe, and nothing here backfilled.

## Follow-ups, in order

1. Decide the reference-lookback policy properly: either bound reference lookback
   to a fixed span (a strategy-calculation change requiring historical validation
   and explicit approval), or keep the gate and treat boundaries inside the
   horizon as explicitly excluded from any performance claim.
2. Cohort parity: make historical replay reproduce one-plan-per-setup,
   instrument-wide occupancy, and outcome-version semantics, or keep the two
   permanently labelled as non-comparable.
3. Only after 1 and 2, a controlled backfill with a pre-declared, untouched
   out-of-sample protocol.
