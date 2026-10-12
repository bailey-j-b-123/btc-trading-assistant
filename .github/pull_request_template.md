## Summary

<!-- One paragraph: what this PR does and why. Link the task: Fixes #NN. -->

## Files changed

<!-- Bulleted list of the files/directories changed and what each change does. -->

## Tests run (exact commands and results)

- Python: `python -m pytest` → <!-- e.g. "1182 passed, 1 xfailed" -->
- JavaScript: `npm test` → <!-- e.g. "27 test files, all passing" -->
- <!-- Targeted runs, if any: command + result -->

## Safety checklist (answer every box)

- [ ] No trading-strategy changes (or the task explicitly authorises them and this PR says so)
- [ ] No access to credentials, API keys, or production databases
- [ ] No historical backfill and no modification of existing journal / ledger / paper records
- [ ] No real-money trading path added or changed
- [ ] The documented xfail in `tests/test_decision_invariance.py` is untouched
- [ ] CI (GitHub-hosted, no secrets) passes on this PR
- [ ] No automatic merges or deployments introduced

## What this does NOT do

<!-- Scope boundaries: deliberately out of scope, remaining limitations. -->
