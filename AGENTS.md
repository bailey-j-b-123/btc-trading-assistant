# AGENTS.md — BRAIN development rules

This file is the shared contract for every AI agent (Arena, Codex, ChatGPT)
and human working in this repository. Read it fully before changing code.
The `README.md` is the system's documentation of record (what each step does
and why); this file is about **how development happens**. For the coordination
model between agents, see `docs/automation-guide.md`.

## What this repository is (and is not)

`btc-trading-assistant` (BRAIN) is a deterministic, read-only evidence and
trade-*planning* system for Binance Spot BTC/USDT with a paper-only forward
ledger. It is **not** a trading bot: it contains no order placement, no
execution, no account functionality, and no real-money trading — and none may
be added.

## Non-negotiable safety rules

1. **No real-money trading. No order placement. No exchange credentials.**
   Tests and CI use offline fake-exchange fixtures only. Never add a code
   path that could submit an order, and never read, print, or commit
   credentials, API keys, or `.env` contents (only `.env.example` is tracked).
2. **No production database access.** The live databases
   (`data/*.sqlite3*`, `backups/`) are gitignored and live on the operator's
   machine only. Tests create temporary SQLite files; CI creates its own
   temporary state in the runner workspace. Never connect to, copy, back up,
   or modify a production database from code, tests, or CI.
3. **No historical backfill. No modification of existing trading records.**
   Journal records, forward-ledger observations, and paper plans are
   append-only history. New behaviour is introduced under a new version /
   fingerprint cohort so old records stay distinguishable — never by
   rewriting, re-identifying, or backfilling stored rows.
4. **No trading-strategy changes unless the task explicitly authorises them.**
   Strategy work (qualification, planning, outcome-resolution rules) is a
   separate, human-reviewed workstream. Infrastructure, documentation, and
   tooling PRs must not alter those rules.
5. **No automatic merges or deployments.** Merging a PR is a human act. There
   is no deployment pipeline; creating one requires explicit approval.
6. **Preserve the documented expected failure.**
   `tests/test_decision_invariance.py::test_a_shallow_stored_start_does_not_change_live_setups_at_a_later_boundary`
   is an xfail pinning a known, documented shallow-history defect (see
   `docs/decision-invariance.md`). Do not remove the marker, convert it to a
   pass, or silence it. If the underlying defect is fixed, that is a
   deliberate task in its own right.
7. **Never hard-code magic thresholds** from audits or examples (for instance
   the 35-bps / 0.5-ATR numbers from earlier PRs) into checks or gates.
   Behaviour is configuration-driven with explicit, documented defaults.
8. **CI safety (GitHub Actions):** GitHub-hosted runners only (never a
   personal machine); no secrets in workflows; minimal `permissions:`
   (contents: read); never use the `pull_request_target` trigger.
9. **Accuracy over speed.** Prefer minimal, well-tested changes. Report
   exactly what was changed, which tests were run, and what failed. Never
   claim improved profitability — this system makes no profitability claims,
   and PRs must not imply any.

## Working rules

- Start every task from the latest `main` (`git fetch origin main`).
- Branch naming: `<agent>/<short-slug>` (Arena uses its session branch name;
  Codex/ChatGPT name branches their own).
- One agent owns a branch at a time. Do not push to, edit, or rebase another
  agent's open branch/PR. Coordinate overlaps through issue comments.
- Open exactly **one focused PR per task**, using the PR template.
- Never force-push a shared branch, never push directly to `main`, and never
  delete another agent's branch.
- Before opening a PR, run the full test suites locally and paste the exact
  results into the PR description (the template has the section for it).
- If CI fails, fix it on the same branch with a new commit — never by
  weakening or deleting a test. If a test is genuinely wrong, fixing it is
  its own explicitly stated task.

## Commands

Environment: Python 3.11+, Node 22.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The same commands CI runs:

```bash
.venv/bin/python scripts/offline_pytest.py  # full Python suite in a fresh clone
npm test                     # frontend suite (node --test, zero npm deps)
```

The guarded runner adds regression checks for network calls, environment-file
reads, SQLite paths, writes, and process control; it is not an OS sandbox for
hostile native code. CI also uses a network namespace on a disposable hosted
runner. For unattended pickup and isolation limits, read
`docs/coordinator-contract.md`. Never run development tests in a live checkout.

Targeted runs:

```bash
.venv/bin/python -m pytest tests/test_trade_planning.py
node --test tests/frontend/decision.test.mjs
```

Lint (the documented repo check; `ruff` is not a declared dependency — install
it explicitly, e.g. `python -m pip install ruff`):

```bash
ruff check --isolated --select E4,E7,E9,F src tests
```

Note: `main` currently carries a small number of pre-existing findings for
this command. That debt is tracked separately; do not change the selection,
add per-file ignores, or "fix" unrelated files inside feature work.

## Invariants to respect in any change

- Plan / observation identity is fingerprinted; changing planning inputs
  changes plan ids. Do not disturb version strings or fingerprints unless the
  task is explicitly about identity (and even then: no re-identification of
  stored records).
- Refusal reasons carry exact numbers and stay machine- and human-readable;
  gaps are reported as gaps (missing inputs are never invented).
- Dashboard, journal, and forward runner must stay consistent for the same
  configuration.
- The test suite stays fully offline: no test may open a real network
  connection to an exchange.
