# Runner health: heartbeat liveness (exact)

This document is the reference for one narrow question: **is the forward runner
process still reporting?** It describes the defect that made the dashboard answer
that question wrongly, the server-owned verdict that replaced the guess, and the
operational settings that control it. `README.md` remains the documentation of
record for the rest of Step 12.

Nothing here changes a strategy rule, a stored row, or a threshold. Liveness is
an assessment *about the runner process*, recomputed on every read from the
heartbeat trail already in the ledger; it never writes, fetches, repairs, or
trades.

## The defect (repaired)

The global header derived its all-clear from the newest heartbeat's **status**
alone. `heartbeat_age_seconds` (recorded_at vs the server clock) was displayed
in the details card but was never decisive, so a runner that had silently stopped
days earlier still read green whenever:

* stored candles were still `CURRENT` (market freshness is a different question),
* no catch-up boundaries were pending (the runner had already processed the last
  one it saw), and
* the newest stored heartbeat said `STARTED`, `PROCESSED`, or `IDLE`.

A heartbeat recorded at a **future** instant — clock skew between the runner host
and the reading host, or a hand-written row — read green too, because a negative
age was not inspected at all.

Reproduction before the repair (synthetic fixture: current candles, zero backlog):
a 30-minute-old `IDLE` heartbeat, a 9-day-old `STARTED` heartbeat, and a heartbeat
`7200s` in the future all produced `DATA CURRENT · RUNNER <STATUS>` with a green
tone. After the repair they produce `FORWARD RUNNER · HEARTBEAT STALE` (amber),
`FORWARD RUNNER · HEARTBEAT STALE` (amber), and
`FORWARD RUNNER · HEARTBEAT IN THE FUTURE` (red) respectively; a payload with no
verdict at all produces `FORWARD RUNNER · LIVENESS UNKNOWN`.

## The contract

`ForwardTestService.status()` always ships a top-level `runner_liveness` block
(version string `runner-liveness-v1`), even when
`runner` is `null`:

| field | meaning |
| --- | --- |
| `policy_version` | `runner-liveness-v1` — identity of the vetting semantics |
| `state` | one of the states below |
| `alive_confirmed` | `true` for `CONFIRMED_ALIVE` and nothing else |
| `heartbeat_present` | whether any heartbeat row exists for this instrument |
| `heartbeat_status` | the newest row's stored status, or `null` |
| `recorded_at` / `assessed_at` | newest row instant / instant the verdict was computed |
| `age_seconds` | **signed** seconds between them (negative means the future) |
| `tolerance_seconds` / `tolerance_source` | the tolerance applied and how it was resolved |
| `cadence_seconds` / `cadence_source` / `cadence_gap_count` | the runner's own observed cadence |
| `heartbeat_rows_examined` | newest heartbeat rows the assessment looked at |
| `clock_skew_diagnostic_seconds` | diagnostic-only band for the clock-disagreement wording |
| `configured_poll_interval_seconds` | the *reading* process's configured interval, reported and never used |
| `last_error` | the newest row's recorded error, or `null` |
| `detail` | one sentence with the exact numbers |

The dashboard (`web/static/js/topbar.js`) quotes this block verbatim. It never
computes an age, never reads the browser clock (`Date.now`, `new Date`,
`performance.now` do not appear in the file), and never turns a missing verdict
into an all-clear.

### States, most definitive first

1. `NEVER_RUN` — no heartbeat row exists for the instrument. Amber,
   `FORWARD RUNNER · NEVER REPORTED`.
2. `CLOCK_INCONSISTENT` — `recorded_at` is **later than the assessing instant**,
   by any amount. Red, `FORWARD RUNNER · HEARTBEAT IN THE FUTURE`. A negative age
   is never clamped and never certifies liveness; the diagnostic band only
   changes the wording. The runner and the reader must agree on the clock before
   any time-based verdict is trusted.
3. `STOPPED` — the newest row records a clean stop. Amber,
   `FORWARD RUNNER · RUNNER STOPPED`. Definitive: a clean stop outranks age.
4. `FAILED` — the newest row is `ERROR` or carries a `last_error`. Red,
   `FORWARD RUNNER · PROCESSING FAILURE`, with the recorded error quoted.
5. `STALE_UNCONFIRMED` — a running status whose age exceeds the tolerance. Amber,
   `FORWARD RUNNER · HEARTBEAT STALE`. It says **not confirmed alive**, never that
   the process died: nothing has been recorded since the last heartbeat.
6. `CONFIRMED_ALIVE` — a row whose status is `STARTED`, `PROCESSED`, `IDLE`, or
   `NO_DATA`, with `age_seconds <= tolerance_seconds`. This is the only state that
   sets `alive_confirmed`.
7. `UNKNOWN` — a status this version does not recognise. Amber,
   `FORWARD RUNNER · LIVENESS UNKNOWN`.

Only the newest row feeds the verdict, so a fresh pass clears an older failure;
recovery is read on the next `status()` call, with nothing written.

### Market freshness stays a separate dimension

`market_data.data_health` (CURRENT / STALE / …, `web/freshness.py`) answers a
different question: are the stored closed candles current? A stale runner leaves
`data_health` untouched and produces exactly one issue (`Forward runner ·
Heartbeat stale`), and a stale series with a live runner produces only
`Market data · Freshness …`. "Unconfirmed" is deliberately not "dead", and never
"your candles are stale".

`NO_DATA` is a statement about the market recorded by an alive process, so it
still reads `FORWARD RUNNER · STATUS NO_DATA` (amber) — but only when the server
confirms liveness; otherwise the liveness condition is what is named.

## Operational tolerance (configuration-driven, no magic numbers)

The tolerance is resolved in this documented precedence:

1. **Operator statement** — `RunnerLivenessPolicy.explicit_tolerance_seconds`, set
   from the environment as
   `TRADING_ASSISTANT_RUNNER_LIVENESS_TOLERANCE_SECONDS` (1–86400 s; `None` by
   default) or passed explicitly with a `RunnerSettings`. Reported as
   `operator_setting`. This is how a deliberately slow cadence or a long
   catch-up/processing pass is *stated* rather than guessed.
2. **Observed cadence** — three times the median positive gap between the newest
   heartbeat rows (`cadence_multiplier` × `cadence_seconds`), when at least three
   positive gaps agree (`minimum_cadence_gaps`, sample of the newest
   `cadence_sample_size = 12` rows). Reported as `observed_cadence`. The median is
   used, never the maximum, so a single outage or restart cannot buy days of
   silence; a cadence with too little evidence is reported but **not** used to
   widen the tolerance (`cadence_source = insufficient_evidence`).
3. **Policy floor** — `minimum_tolerance_seconds`, the documented default **360 s**
   (`DEFAULT_LIVENESS_TOLERANCE_SECONDS`): the runner's documented default poll
   interval (60 s, `RunnerSettings.interval_seconds`) plus its documented maximum
   transient backoff (300 s, `transient_backoff_max_seconds`) — the longest window
   in which a healthy default-cadence runner may legitimately stay silent while
   backing off after a transient network failure. Reported as `policy_floor`. A
   fast observed cadence can never tighten the tolerance below this floor.

The reading process's own configured interval is **reported and never used** as
the runner's cadence: the web app is not the runner, and silently assuming its
defaults equal the runner's actual cadence is exactly the class of assumption
that produced the defect. Cadence lives only in the gaps between the runner's own
heartbeat rows; it is not stored on the row, so it cannot be assumed — it has to
be observed.

### Boundaries (pinned by tests)

* `age_seconds == 0` (exact now) → `CONFIRMED_ALIVE`.
* `age_seconds == tolerance_seconds` → `CONFIRMED_ALIVE`.
* `age_seconds == tolerance_seconds + 1` → `STALE_UNCONFIRMED`.
* `age_seconds == -1`, `-3`, or `-7200` → `CLOCK_INCONSISTENT`, never healthy.

Invalid policy values (`minimum_tolerance_seconds` outside 1–86400,
`cadence_multiplier` outside 1–100, `clock_skew_diagnostic_seconds` outside
0–3600, `cadence_sample_size` outside 2–1000, `minimum_cadence_gaps` outside
1–1000, or an explicit tolerance outside 1–86400 s) raise `ValueError`/`TypeError`
at construction rather than degrading silently.

### Example: a slow runner

A runner that really polls every 600 s records twelve `IDLE` heartbeats 600 s
apart. The newest eleven gaps are all positive and agree, so
`cadence_seconds = 600`, `cadence_source = observed_heartbeats`, and the tolerance
becomes `3 × 600 = 1800` s (`tolerance_source = observed_cadence`). At
`age_seconds = 1500` the verdict is `CONFIRMED_ALIVE`; at `1801` it is
`STALE_UNCONFIRMED`. Under the floor alone, 1500 s would have been falsely stale.

### Example: an operator-declared long pass

A runner whose catch-up pass can take 90 minutes sets
`TRADING_ASSISTANT_RUNNER_LIVENESS_TOLERANCE_SECONDS=5400`. A 4800 s-old heartbeat
then reads `CONFIRMED_ALIVE` with `tolerance_source = operator_setting`, while the
same silence without the statement reads `STALE_UNCONFIRMED` and says so without
claiming the process died.

## What the verdict does *not* claim

* It does not claim the runner died, crashed, or stopped: an unconfirmed runner
  is exactly that — unconfirmed.
* It does not claim the runner is doing anything useful: a heartbeat proves the
  process reported, not that a pass produced an observation.
* It does not claim the market data is stale or fresh; that is `data_health`.
* It does not modify, repair, backfill, or write any heartbeat row (the dedicated
  tests assert the row counts are unchanged by repeated `status()` calls).
* It does not decide anything for the runner: the runner keeps polling, retrying,
  and recording exactly as before.

## Settings summary

| setting | default | bounds | effect |
| --- | --- | --- | --- |
| `TRADING_ASSISTANT_RUNNER_LIVENESS_TOLERANCE_SECONDS` | unset (`None`) | 1–86400 | operator tolerance; outranks observation |
| `RunnerLivenessPolicy.minimum_tolerance_seconds` | `360` | 1–86400 | floor when no operator statement exists |
| `RunnerLivenessPolicy.cadence_multiplier` | `3` | 1–100 | how many observed intervals may pass |
| `RunnerLivenessPolicy.cadence_sample_size` | `12` | 2–1000 | newest rows examined for cadence |
| `RunnerLivenessPolicy.minimum_cadence_gaps` | `3` | 1–1000 | positive gaps required to trust a cadence |
| `RunnerLivenessPolicy.clock_skew_diagnostic_seconds` | `5` | 0–3600 | wording only; never certifies liveness |

## Tests

Offline and synthetic only (temporary migrated SQLite databases, labelled fixture
candles, injected clocks, no network, no real data):

* `tests/test_runner_liveness.py` — the pure policy (states, precedence,
  boundaries, cadence observation, validation, JSON projection, future-heartbeat
  regressions) and `ForwardTestService.status()` / `GET /api/forward` end to end,
  including the reported false-healthy case, recovery, per-instrument scoping,
  the operator override, and the read-only guarantee.
* `tests/frontend/runner-liveness.test.mjs` — the header contract: every state's
  label and tone, market-freshness separation, the server-owned tolerance quoted
  verbatim, the missing-verdict regression, future heartbeats at +1 s / +3 s /
  larger never reads healthy, and a static check that the file never reads the
  browser clock.
