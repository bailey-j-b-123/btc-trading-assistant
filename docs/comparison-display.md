# Comparison display

The historical/forward comparison distinguishes rate metrics from R distributions.
Rates show a percentage and their numerator/denominator. R distributions show the
reported average and eligible sample count; they never acquire a percentage or a
"below floor" label merely because they lack a percentage field. An explicit
INSUFFICIENT_DATA status still shows the reporting-floor limitation.

The comparison's R payload does not currently include records_considered. The
view does not invent that population from the separate paper-plan denominators.
When a distribution includes a considered count it is shown alongside eligibility.
Missing or invalid counts remain UNKNOWN rather than zero. Missing comparability
is UNKNOWN; only an explicit boolean true produces a comparable label.

This changes presentation only. Metrics, sample thresholds, selection rules,
plan identities and stored observations are untouched. Observational R remains
hypothetical and is not realised profit. The historical and forward sides retain
separate populations and the backend's comparability verdict.
