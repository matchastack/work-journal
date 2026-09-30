# Change log

One short entry per pull request, newest first: what changed and what to verify. Verification is ticked in the pull request, not here.

## 2026-09-28 · T-013 Style checker · [#10](https://github.com/matchastack/work-journal/pull/10)
**Changed:** added the style checker. Resume bullets must open with an action verb in the role's tense, use no first-person pronouns and fit in 2 lines at the template's width. LinkedIn text must fit each section's limit, and every style avoids your words to avoid.

**Verify:**
- CI is green on #10
- A few of your own bullets get the findings you'd expect
- Whether current-role bullets must use the present tense (see the PR)

## 2026-09-28 · T-026 Column encryption · [#15](https://github.com/matchastack/work-journal/pull/15)
**Changed:** added `EncryptedText`, a column type that stores text as AES-256-GCM ciphertext under keys from `DATA_ENCRYPTION_KEY`, with `wj keys new` and `wj keys rotate`. Journal and fact tables will use it. Adds `cryptography`.

**Verify:**
- CI is green on #15
- `uv run wj keys new` prints a key, and the rotation steps in `app/db/crypto.py` are clear

## 2026-09-28 · T-025 Database foundation · [#14](https://github.com/matchastack/work-journal/pull/14)
**Changed:** added PostgreSQL: an async SQLAlchemy engine, Alembic migrations creating `users` and keyed `settings`, a `/healthz` database check, `compose.yml` for local Postgres 16, and database tests on a fresh database (a Postgres service in CI). Adds SQLAlchemy, asyncpg and Alembic.

**Verify:**
- CI is green on #14
- With `docker compose up -d` and `DATABASE_URL` set, `uv run alembic upgrade head` works and `/healthz` shows `"database": "ok"`
## 2026-09-28 · T-008 Variant selection · [#8](https://github.com/matchastack/work-journal/pull/8)
**Changed:** added variant selection: from the master profile, each variant picks its roles, bullets, title, coursework, skills preset, summary and contact details by role type, never showing benched or planned items and always keeping load-bearing roles. Only the master document is predefined (OQ-6): one-page resumes are tailored to each posting.

**Verify:**
- CI is green on #8
- The golden files for the master document, and for the backend and data selections that tailoring will build on, look right

## 2026-09-28 · T-006 Import master resume from LaTeX · [#7](https://github.com/matchastack/work-journal/pull/7)
**Changed:** added `wj import tex`, which turns the master resume's template commands and structured comments into a profile, one fact per active bullet and a report of what to check and what wasn't imported, all written to the git-ignored `local/` folder.

**Verify:**
- CI is green on #7
- Importing your own file gives the counts in the PR, and the report's mappings look right
- The sensitive roles are marked (see the PR)
- `local/profile.json`, `local/facts.json` and the report contain no en dashes

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
