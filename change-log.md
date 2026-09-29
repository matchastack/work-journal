# Change log

One short entry per pull request, newest first: what changed and what to verify. Verification is ticked in the pull request, not here.

## 2026-09-28 · T-005 Number checker · [#6](https://github.com/matchastack/work-journal/pull/6)
**Changed:** added a deterministic checker that blocks any number in generated text that the cited facts don't back (invented numbers, changed units, stronger qualifiers), accepts figures computed by code with their formula, and extracts metrics from existing bullets for import.

**Verify:**
- CI is green on #6
- The example in the PR prints the expected three lines
- The rules in the PR (±10% for "about", the ranges for "hundreds" and "thousands", unit families) match your judgement
## 2026-09-28 · T-004 Profile operations · [#5](https://github.com/matchastack/work-journal/pull/5)
**Changed:** added `apply()`, which applies change operations atomically and by ID, and `diff()`, which lists the changes between two profiles at readable paths.

**Verify:**
- CI is green on #5
- The operations and guard rails in `app/profile_ops.py` cover the edits you'd expect to make

## 2026-09-27 · T-003 Core schemas · [#4](https://github.com/matchastack/work-journal/pull/4)
**Changed:** added typed models for the master profile (with all the master-resume metadata), facts and metrics, variants, change operations, job postings and applications, plus `wj schema export`.

**Verify:**
- CI is green on #4
- `uv run wj schema export /tmp/wj-schemas` writes 6 schema files
- The profile model captures everything your master resume records

## 2026-09-27 · T-002 CI pipeline · [#3](https://github.com/matchastack/work-journal/pull/3)
**Changed:** added a GitHub Actions workflow that runs the backend's lint, format, type and test checks on every PR and on `main`.

**Verify:**
- The **CI / Backend checks** run on #3 is green and takes under 5 minutes

## 2026-09-27 · T-001 Backend scaffold · [#2](https://github.com/matchastack/work-journal/pull/2)
**Changed:** added the `backend/` Python project: settings from the environment, a FastAPI app with `/healthz`, the `wj` command, and ruff, pyright and pytest.

**Verify** (from `backend/`):
- `uv sync`, then the all-checks line in `CLAUDE.md` passes
- `uv run uvicorn app.main:app --reload`, then http://127.0.0.1:8000/healthz shows `{"status":"ok"}`
- `uv run wj --help` lists `version`, and `uv run wj version` prints `0.1.0`

## 2026-09-27 · T-000 Project documents · [#1](https://github.com/matchastack/work-journal/pull/1)
**Changed:** added the requirements, task backlog, change log, `CLAUDE.md`, PR template and README. Dropped the JSON import (OQ-1), and approved committing the template skeleton (OQ-2).

**Verify:**
- Scope, priorities, resume rules and LLM routing in `project-requirements.md`
- Task sizes and order in `tasks.md`
- Workflow and branch naming in `CLAUDE.md`
- This log is short enough
- No personal data in the diff
