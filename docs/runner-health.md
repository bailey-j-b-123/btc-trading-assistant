# Runner heartbeat evidence

The status API derives `runner_liveness` from stored heartbeat rows using the
server clock. The dashboard consumes that verdict; it never calculates liveness
using the browser clock. Current market data and zero catch-up backlog cannot
rescue stale, missing, failed, stopped or future runner evidence. Older or
malformed payloads remain unconfirmed.

The default reader policy has a 360-second monitoring floor (covering the
documented default 60-second poll plus 300-second transient backoff), a cadence
multiplier of three, and a sample of at most 12 newest heartbeat rows. Positive
gaps between distinct timestamps estimate observed cadence; at least three gaps
are required before their median can widen the floor. One restart gap cannot
buy hours of grace. The estimate describes observed gaps, not proof of the actual
polling interval. A slow pass can still exceed the policy; stale is unconfirmed,
not a claim that a process died. An optional explicit reader tolerance of
1..86400 seconds overrides the inference. No operator setting is changed by the
patch, and no strategy or stored identity includes this monitoring policy.

The payload includes policy version, evaluation time, signed age, tolerance,
tolerance source, observed cadence and gap count, state, boolean confirmation
and explanatory detail. Exact tolerance boundaries are inclusive. Every future
timestamp, including a fraction of a second, is clock-inconsistent; it is never
clamped into a healthy zero age. A recent confirmed heartbeat is evidence of
reported activity within policy, not a live process probe.

Reads are bounded and preserve insertion ordering for equal timestamps. The
newest row and its trail come from one query. Status reads write nothing: no
migrations, history changes, backfills, process control or exchange calls.
Market freshness, strategy qualification and paper metrics remain separate.
