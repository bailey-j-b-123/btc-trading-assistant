# Historical and forward cost comparisons

The comparison endpoint uses the cost assumptions from stored paper plans when
all recorded plans agree. Both historical replay and forward reporting use that
same fee and entry/exit slippage scenario, even if the request supplies different
costs. The response exposes the selected scenario, its fingerprint, and whether
it came from recorded plans. With no unique recorded scenario, the request's
scenario is used on both sides; mixed forward cohorts continue to withhold
combined metrics.

The report builder independently compares cost fingerprints. Its adjusted-R row
is not comparable when costs differ, and has unknown comparability when historical
cost provenance is absent. Rule-version checks still apply. The shared planning
configuration fingerprint must also match on every forward cohort; different
settings are not comparable and missing provenance is unknown. This checks the
same PlanningParameters contract under its two metadata key names, without
conflating unrelated step-specific configuration fingerprints. Raw observations do
not depend on fee/slippage assumptions and retain their existing version checks.

This is a read-only reporting correction. It changes no plans, identities,
qualification rules, stored observations or execution behavior. Matching scenarios
makes a descriptive comparison interpretable; it does not make paper outcomes
fills or establish profitability. Separate market periods, denominators,
insufficient samples and mixed cohorts remain limitations.
