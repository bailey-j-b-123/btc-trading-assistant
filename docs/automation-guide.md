# Automation guide: AI collaboration through GitHub

BRAIN development is done by multiple AI agents (Arena, Codex, ChatGPT) under
one human owner. **GitHub is the only coordination surface** — no shared
machines, no shared databases, no side channels. If it is not written in an
issue, branch, PR, or CI result, it did not happen.

`AGENTS.md` at the repository root is the binding rules document for every
agent; this guide explains the coordination model it assumes.

## Current automation status

- **GitHub CI is now working.** The Python and JavaScript GitHub Actions jobs
  on PR #47 both completed successfully. CI runs the test suites on
  GitHub-hosted runners; it does not automate agent execution.
- **Automatic Arena task pickup is not configured.** Creating an issue does
  not automatically assign it to Arena or start an Arena task; an agent must
  still be selected and the task claimed manually.
- **Unattended local Codex execution is not configured.** No issue or PR
  triggers a Codex process on an operator's local machine. Local Codex runs
  must be started and supervised by the operator.

## The model

| Surface | Role |
|---|---|
| Issue | The task: objective, "implement ONLY" list, safety requirements, required evidence |
| Branch | One task, one agent, one branch (`<agent>/<slug>`; Arena uses its session branch) |
| Pull request | The deliverable, with the PR template filled in (exact test results) |
| CI (GitHub Actions) | Automatic gate: Python + JavaScript test suites on GitHub-hosted runners, no secrets |
| Human review + merge | The only merge authority. No auto-merge, no deployment |

## Flow

1. **Task.** A human — or an agent, after a human approves the need — opens
   an issue with the "AI development task" template: objective, implement-only
   list, safety requirements, required evidence.
2. **Pickup.** An agent claims the issue (comment + fills "Picked up by").
   One agent owns a branch at a time; an agent never pushes to another
   agent's branch or PR. This pickup is manual; automatic Arena pickup is not
   configured yet.
3. **Work.** The agent checks out the latest `main`, creates its branch,
   implements *only* the listed deliverables, and runs the full suites
   locally (`python -m pytest` and `npm test`). Local Codex execution is
   operator-started; unattended execution is not configured.
4. **PR.** The agent opens one focused PR to `main` and fills the PR template:
   files changed, exact test results, the safety checklist, and the scope
   boundaries.
5. **CI.** GitHub-hosted runners run both test suites on every PR open/update
   (no secrets, minimal permissions, no production database, no exchange
   access). On failure the agent fixes and pushes a new commit on the same
   branch — never by weakening a test.
6. **Review & merge.** A human reviews and merges manually. Merging is the
   only point at which work reaches `main`; there is no deployment step after
   it.
7. **Close-out.** The PR is merged; the issue is closed with a link to the
   merged PR and the final test evidence.

## Rules that keep this safe

- One branch per task per agent; conflicts are resolved through issue
  comments, not by editing each other's branches.
- Never force-push a shared branch, never push to `main`, never merge
  automatically, never delete another agent's open branch.
- No credentials in code, branches, issues, or PRs; CI has none.
- Stored trading records are append-only history: new behaviour goes under a
  new version/fingerprint cohort, never by rewriting old rows; no backfill.
- The documented xfail in `tests/test_decision_invariance.py` stays
  (see `AGENTS.md`).
- A missing or zero cost/assumption input is a reported gap, never treated as
  proof of anything.

## What CI does today

- **Trigger:** every PR open/update, plus manual dispatch. GitHub-hosted
  `ubuntu-latest` runners only — never a personal machine.
- **Jobs:** `python-tests` (Python 3.11, `pip install -e ".[dev]"`, full
  `pytest` suite) and `js-tests` (Node 22, `npm test` =
  `node --test tests/frontend/*.test.mjs`; the frontend package declares zero
  npm dependencies, so there is no install step and no lockfile to maintain).
- **Permissions:** `contents: read`. **Secrets:** none. No
  `pull_request_target` (that trigger checks out untrusted head code with
  base-branch token scope — a known footgun, deliberately avoided).
- The test suite is fully offline (fake-exchange fixtures, temporary SQLite
  files): nothing in CI can reach an exchange or a production database.

## What still requires manual setup or approval

- **Branch protection on `main`** — requiring the CI status checks to pass
  and requiring a review is a repository-settings change the human makes in
  the GitHub UI; CI alone cannot enforce it. Until then, "CI green" is
  advisory, and the human review is the actual gate.
- **Label management** — optional; the templates deliberately reference no
  labels, so nothing is needed to make them work.
- **Any future secret usage** (for example a service token) — must be
  individually approved and minimally scoped; the default is none.
- **Lint gating** — the documented command
  `ruff check --isolated --select E4,E7,E9,F src tests` currently has a small
  number of pre-existing findings on `main`; a CI lint job is deliberately
  *not* added until that debt is cleared in its own PR, so the first
  infrastructure PR is not red on day one.
- **Deployments** — none exist and none are planned; any deployment
  automation is a separate, explicitly approved workstream.
