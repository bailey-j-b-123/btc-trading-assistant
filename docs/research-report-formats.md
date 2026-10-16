# Mixed research experiment reports

The original Strategy Lab runner records `strategy`, `stage` and a dataset
fingerprint. Novel and Project Alpha record `hypothesis`; Alpha does not record
`stage`. Previously `strategy_lab.report._report` indexed the original schema
unconditionally, raising `KeyError: 'strategy'` as soon as it encountered a
completed Novel/Alpha experiment. This was a reporting defect, not a trading loss.

The report now projects both known formats without changing the database:

- `format` distinguishes `strategy`, `hypothesis` and `unrecognised`.
- `strategy` retains the original field or projects the recorded hypothesis;
  `definition` preserves the complete original metadata for inspection.
- `stage`, `period`, `cost`, `exit_policy` remain their recorded values or null.
  An absent stage produces `stage_not_recorded`; no validation status is invented.
- A recorded dataset fingerprint is preserved. If only a nonempty dataset
  manifest is recorded, its content identity is projected and explicitly tagged
  `derived_from_recorded_manifest`. This is report metadata only, never a new
  experiment ID or a rewrite of old rows. Missing information remains null.
- Unknown formats retain raw definitions/results and warnings; trade groupings
  remain null because their financial event schema is not established.

Existing financial results and grouping calculations for known formats are
unchanged. No decision, fill, risk, cost, dataset acquisition, promotion or
production code changes are involved. The report command remains the existing
`python -m trading_assistant.strategy_lab --root /path/to/marked-lab report`;
this change does not add a new tester.

## Reproduction and verification

`python -m pytest tests/test_research_report_formats.py tests/test_strategy_lab_overnight.py`
exercises a temporary synthetic database containing original, Novel, Alpha and
unknown metadata. Before the fix the new test fails with `KeyError: 'strategy'`;
afterwards all three targeted tests pass. The test verifies original fields,
explicit fingerprint provenance, missing labels, raw unknown definitions and
unchanged experiment rows. Synthetic results are not performance evidence.

`docs/research/report-format-verification.json` records a separate check using
the two already completed genuine-2023 #79 replay experiments. Their own
marked lab was opened read-only and copied to a disposable research workspace.
Both results report correctly and exactly match the stored financial results;
hashes of every stored table stay identical before/after reporting. No new
backtest, archive download, production access or protected-2025 access occurred.

This does not reconcile incompatible engines into one profitability claim.
Missing historical stage metadata cannot be recovered from this report; its null
value is intentional. Other BRAIN v3 diagnostics and rule experiments are separate
tasks, not silently bundled into this repair. This draft depends on #79 and its
unmerged research chain; nothing is deployed.
