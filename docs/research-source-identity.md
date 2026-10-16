# Research source identity v2

This is an identity repair only. Trading decisions, signals, stops, targets,
fills, sizing, costs and stored records are unchanged.

## Confirmed defect

`strategy_lab.models.research_code_fingerprint` previously whitelisted five
Strategy Lab files. Runner, acquisition/validation, persistence, campaign and
forward-paper code could change without changing its digest. Market-data parser,
repository and configuration source were also outside its coverage. This could
deduplicate or resume a cohort despite a material source change. Alpha/Novel had
extra independent source hashes, but the shared/original-runner identity gap
still existed; not every experiment was necessarily affected.

Regression tests modify **temporary source copies**, not the checkout. Against
the parent, 15 tests fail and 4 pass: 14 omitted-file mutations retain the digest,
and a changed runner source resumes the previous experiment ID. These tests
model a new process by clearing the documented process-level digest cache; they
do not claim a changed file hot-loads itself into an existing Python process.

## Repair and compatibility

The versioned `brain-research-source-v2` digest hashes sorted relative paths and
bytes of Python source across the core `trading_assistant` package. It excludes
`web/`, `ai_explanation/` and `strategy_lab/report.py`, which are presentation
surfaces rather than research decision inputs. It includes all Strategy Lab
runners, data/storage code, shared parsing, financial and planning dependencies,
and future core Python modules instead of maintaining a short admission list.
Coverage is deliberately conservative: even a core comment or unrelated core
edit creates a new research identity rather than risking stale reuse.

Existing explicit configuration and data fingerprints remain included in the
existing experiment definitions. Only new computations receive the new source
digest. Old experiments are not re-keyed, migrated, backfilled, rewritten or
continued under a different source. A new run using changed core source creates
a separate cohort. Completed old results remain available through the existing
report. Existing forward-paper source mismatch protection continues to reject
old promoted cohorts; any future promotion requires fresh evidence, not manual
rewriting of its source field.

The digest is cached once per process, as before. Install source changes only
with workers stopped and restart into the new code; hot swapping is unsupported.
Do not clear a running production worker's cache to continue old research.

## Tests and limitations

`python -m pytest tests/test_research_source_identity.py tests/test_research_exit_policy.py tests/test_strategy_lab_paper.py`
passes all68 targeted cases after the fix. The new19 tests cover17 material files,
presentation exclusions, and separate-cohort admission preserving the old partial
experiment row exactly. Existing exit-policy and source-mismatch forward-paper
tests remain intact. Temporary synthetic candles are not performance evidence.

This does not fingerprint installed third-party binaries, the Python interpreter,
external machine state or runtime hot patches. Execution-environment receipts
remain a separate reproducibility concern. Nor does it establish a trading edge,
run a strategy campaign, access protected prices, modify production behavior or
fix shallow-history detection. It is a focused draft on top of #80 and its
unmerged research dependencies; human review and merge remain required.
