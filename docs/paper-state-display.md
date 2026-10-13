# Paper outcome settlement and slot occupancy

These are separate backend facts. A recorded AMBIGUOUS outcome can be settled
for outcome reporting while occupying a paper-trade slot until the existing
horizon policy releases it. An unsettled outcome can also have a released slot.
Neither settlement nor release proves an ordered fill, a win, or eligible R.

The dashboard paper summary now calls the report's unresolved_count "Unsettled
outcomes" and completed_count "Settled outcomes". Existing report counts,
cohort separation and R denominators are unchanged. Ambiguous outcomes remain
excluded from eligible R by the backend's existing rules.

The tracker labels its legacy unresolved_paper_plan_count as occupied slots
among the shown plans. This count is bounded by the tracker response, not an
instrument-wide census. Zero occupancy means "NO SLOTS OCCUPIED", not "ALL
SETTLED". Individual plans display outcome settlement and slot occupancy
independently, including settled/occupied and unsettled/free combinations.
Missing or nonboolean flags stay UNKNOWN. The ledger-change signature preserves
those distinctions so a missing-to-explicit-false update refreshes the card.

Tracker polls and performance reports are independent reads. The dashboard does
not copy tracker occupancy into report totals or infer settlement from a slot
count. The backend decides the facts; this patch only changes their presentation.
"Settled" does not promise immutable finality: append-only evidence rechecks
remain governed by the existing backend version policy.

No planning, strategy, occupancy/release policy, thresholds, stored identities,
outcome resolution, record contents, real order paths or runtime settings change.
The documented history-start expected failure remains unresolved.
