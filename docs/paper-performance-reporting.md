# Paper-trading performance reporting (follow-up plan)

**Status: not implemented. This document is a specification for separate work, independently
reviewed, and is not part of the Dashboard V2 presentation PR.**

## Why this document exists

The approved Dashboard V2 designs carry two tiles that the current build cannot fill in: a
**Win rate** and a **Total P&L**. The figures drawn in the reference pictures were illustrative
placeholders, not values from this system.

BRAIN cannot compute either figure today, and that is a property of what the system stores
rather than an oversight in the dashboard:

* The ledger records **proposed prices** (entry, stop, invalidation, targets) and a **reason
  code for the observed outcome** — for example that a stored candle's price touched a proposed
  stop level. It records no fill, no size, no position, no fee and no slippage.
* Step 8 already publishes an explicitly hypothetical level measure,
  `HYPOTHETICAL_PROPOSED_LEVEL_R`, whose own definition ends: *"These are proposed-level
  observations, not fills, executed trades, realized P&L, fees, or slippage."* Step 11
  publishes a second variant after modelled costs, likewise labelled hypothetical.
* `trading_assistant.statistics.analysis` states in its own documentation that the report
  contains no *"executed trades, realized P&L, fees, or slippage"*.

So the dashboard's two tiles currently read **"Not available yet"**. That is the correct
display, not a gap to be papered over with an example number, a count-based ratio or an R
multiple converted into currency.

**This document exists so the tiles can be filled in honestly.** It lists the decisions that
must be made and recorded *before* any figure is put on screen, because the main risk here is
not arithmetic — it is publishing a confident-looking number that means something other than
what a reader will assume it means.

## Scope

In scope: realised (paper) performance metrics computed from BRAIN's recorded paper trades,
shown on the Win rate and Total P&L tiles.

Out of scope, and to stay out of scope: real order execution, exchange credentials, balances,
leverage, position sizing policy changes, and any change to qualification, planning or outcome
detection logic. A performance report **describes** decisions the engine already made; it must
never feed back into them. In particular, no threshold, rule, parameter or plan-selection
behaviour may change as a result of this work.

## The decisions that must be recorded first

Each item below ends in a written definition that goes into the payload's `definitions` block
and into this repository's documentation of record. A metric without its definition on screen
is not shippable.

### 1. What counts as a trade

The unit of account must be named and justified. Candidates, with their problems:

| Candidate unit | Problem |
| --- | --- |
| A stored paper plan | A plan may never be entered; counting it as a trade inflates the denominator with trades that never existed. |
| A paper plan whose entry touch was observed | Closer, but an entry *touch* is a candle-level observation, not a fill. |
| A plan with a terminal outcome state | The natural choice — but see §2 for the states that are not terminal in the required sense. |

**Required decision:** the unit, the inclusion rule, and what happens to plans that are still
open at the report's `as_of`. The recommendation is: one unit per stored paper plan with an
observed entry touch **and** a terminal outcome, with open plans counted separately as
`open_count` and excluded from every denominator (see §6).

### 2. Win and loss definitions, and the exclusion of ambiguous outcomes

This is the heart of the work. The existing outcome states do **not** partition cleanly into
win and loss, and Step 8's treatment of them is the precedent to follow: exclude what cannot
be interpreted unambiguously, and say how much was excluded.

The states that must each be explicitly assigned to `win`, `loss`, `excluded`, or a separate
bucket, with a written reason:

| Outcome state | Question that must be answered before it can be classified |
| --- | --- |
| `TARGETS_REACHED` | Which target? Is reaching T1 a win? A partial win? Does reaching T1 then returning to entry count as a win? |
| `STOPPED` | A stop *touch* on a stored candle is not an executed stop. Does the report assume the fill at the proposed stop, at the candle's open, or at a modelled price with slippage? |
| `STOPPED_AFTER_TARGETS` | Step 7 specifies **no partial-exit policy**, so this outcome has no defined profit. Step 8 excludes it from its R measure for exactly this reason. It must not silently become a "win" or a "loss". |
| `AMBIGUOUS` | Two or more levels touched within one candle, so the order cannot be determined. **Must be excluded from both numerator and denominator**, and reported as a count. |
| `INCOMPLETE_DATA` | The observation window ended before a terminal state was reached, or data was missing. **Excluded**, and reported. |
| `ENTRY_NOT_REACHED`, `INVALIDATED_BEFORE_ENTRY` | The plan never became a trade. Excluded from win/loss, and reported as its own non-trade count. |
| Any status added later | The classifier must **fail loudly** on an unrecognised state rather than defaulting it into a bucket. A silent default is how a report starts lying. |

**Required decision:** a written definition of `win` and `loss`, an explicit list of excluded
states, and the rule for ties (a candle touching both the stop and a target).

### 3. Trade sizing

A win rate needs no size, but any money figure does. The stored plan carries `risk_per_unit`;
there is no size. Options: a fixed notional per trade, a fixed fractional risk, or a size the
operator supplies.

**Required decision:** the sizing rule, including whether it is a constant recorded with the
report so a reader can reproduce the figure. Whatever is chosen, **the size must be part of
the stored report**, not a display-time assumption, because the same trades must always
produce the same total. Sizing is a reporting parameter only: it must not become a position
size for any future order path.

### 4. Fees

**Required decision:** the fee model (for example, taker fee on entry and exit, as basis
points), whether fees are applied per side, and the source and date of the rate. The rate must
be recorded in the report, not hard-coded in the frontend or in a display path. If fees are
not modelled, the tile must say **"gross"** — showing a gross figure as if it were net is a
misstatement.

### 5. Slippage

Step 11 already models adverse entry and exit slippage for its hypothetical variant; that
convention is the precedent to reuse rather than reinvent.

**Required decision:** how slippage is applied (entry and exit, adverse only, absolute or
basis points), and whether it is modelled per trade or as a scenario. As with fees: either it
is modelled and labelled, or the figure is labelled **"before costs"**.

### 6. Correct denominators

The most common way a performance report misleads is a denominator that quietly changes
between the headline rate and the table beneath it. Every rate must state its own denominator.

**Required decisions:**

* Every rate names its denominator, and the denominator counts only units that could have been
  a win **or** a loss — excluded and non-trade units are never in it.
* The sample size is shown next to the rate. A rate over three trades must not look like a
  rate over three hundred.
* Per-target rates keep their own denominators (Step 8's precedent: each target ordinal is
  measured over the observations that proposed that target).
* Rolling windows and filters must state the window and the filters applied; a filtered rate
  must never be presented as the overall rate.
* If the denominator is zero, the metric is **absent** (`null`) and the tile reads "Not
  available yet" — never `0%` or `NaN`.

### 7. Statistical honesty

A win rate and a total P&L over a handful of paper observations are not evidence of edge.
Before these tiles ship, the report must carry: the sample size, the observation period, and
the standing statement that paper results do not establish future profitability. The existing
`limitations` convention (already returned by `/api/forward`) is the place for that text.
Deliberate non-features: no annualised return, no Sharpe-style ratio, no equity curve, no
"expectancy" figure — each would imply a fidelity this data does not have.

## Proposed payload shape

The dashboard's tiles read exactly one key, `paper_performance`, on the `/api/dashboard`
payload (see `static/js/paper-performance.js`). The frontend understands a deliberately narrow
shape, so a future backend change is a small, reviewable diff:

```jsonc
"paper_performance": {
  "available": true,               // false when the report cannot be computed at all
  "reason": null,                  // shown verbatim on the tiles when available is false
  "as_of": "2026-10-17T23:00:00Z", // the boundary every figure is measured to
  "sample_size": 42,               // units counted as a potential win or loss
  "win_rate": "0.4762",            // fraction in [0, 1], or null when not computable
  "total_pnl": { "amount": "123.45", "currency": "USDT" },  // or null
  "gross_or_net": "net",           // "net" only if fees and slippage were applied
  "slippage_and_fees": { "fee_bps_per_side": "4", "slippage_bps_per_side": "5" },
  "excluded": { "ambiguous": 3, "incomplete_data": 1, "stopped_after_targets": 2 },
  "not_traded": { "entry_not_reached": 7, "invalidated_before_entry": 4 },
  "open_count": 5,
  "definitions": {
    "unit": "...", "win": "...", "loss": "...", "denominator": "...",
    "sizing": "...", "fees": "...", "slippage": "...", "ties": "..."
  },
  "limitations": ["Paper results do not establish future profitability."]
}
```

The frontend contract is only the first part of that object:

* `win_rate` — a fraction in `[0, 1]`. A value outside that range is treated as unavailable
  rather than displayed.
* `total_pnl` — `{ amount, currency }` with a finite amount.
* `available`, `reason`, `sample_size`, `as_of` — for the tile's caption and the unavailable
  state.

Anything else in the block is documentation for a human reader. **The frontend performs no
arithmetic on any of it**: no count is divided by another count, and no price distance is
multiplied by anything. If the numbers are wrong, they must be wrong in one auditable place —
the backend service that writes this block.

## Implementation sketch

1. **Definitions first, code second.** Write §1–§7 into this file, with the actual chosen
   rules, and get them reviewed before writing the service. A metric implemented before its
   definition is agreed will be redefined after publication, and the two versions will
   disagree in public.
2. **A new read-only service module** (for example `statistics/paper_performance.py`) that
   reads the journal/forward records for a boundary and computes: the unit count, the
   win/loss/excluded/not-traded/open partitions, the size and cost model applied, and the two
   headline figures. It writes its own definitions into the payload.
3. **Determinism tests**: the same stored records and the same `as_of` must produce byte-equal
   output. The report must be reproducible from the ledger alone.
4. **Boundary tests**: every outcome state in §2, including a synthetic record with an
   unknown status (which must raise, not default).
5. **Denominator tests**: an empty ledger, an all-excluded ledger, a single trade, and a
   ledger where every unit is ambiguous → `null`, never `0` or `NaN`.
6. **A guard that this cannot influence trading**: assert that the qualification and planning
   outputs for a fixed ledger are byte-identical before and after the feature is enabled.
7. **Provenance on screen**: the tiles show `as_of`, the sample size, and whether the total is
   net or gross. The dashboard never displays a performance figure without them.
8. **Independent review** of the definitions and the arithmetic, as with any other analytical
   phase in this project.

## What the dashboard does until then

* Both tiles stay in the approved layout and read **"Not available yet"**.
* Each tile explains why in its own caption, and the note under them states that no example
  figure is shown and that proposed-level observations are not realised profit.
* Nothing on the page substitutes for them: not the ledger's outcome counts, not Step 8's
  hypothetical R, not Step 11's after-costs R.
* The tests in `tests/frontend/paper-performance.test.mjs` pin that behaviour, including the
  negative cases — that a count-based ratio and a hypothetical R are both refused.
