# Change log

One short entry per pull request, newest first: what changed and what to verify. Verification is ticked in the pull request, not here.

## 2026-09-28 · T-010 LaTeX renderer · [#11](https://github.com/matchastack/work-journal/pull/11)
**Changed:** added resume rendering. Values are escaped into LaTeX templates, compiled in a sandbox (no shell escape, no file access outside a temporary folder, 30 s timeout) and cut to fit the page limit, lowest-value bullets first. Adds `wj render`, a plain default template, TeX Live in CI, and Jinja2 and pypdf.

**Verify:**
- CI is green on #11
- `uv run wj render --variant master --profile tests/fixtures/profile.json` writes a one-page PDF that reads well
- The cut order is how you'd cut by hand

## 2026-09-28 · T-008 Variant selection · [#8](https://github.com/matchastack/work-journal/pull/8)
**Changed:** added variant selection: from the master profile, each variant picks its roles, bullets, title, coursework, skills preset, summary and contact details by role type, never showing benched or planned items and always keeping load-bearing roles. Proposed default variants for OQ-6.

**Verify:**
- CI is green on #8
- The golden files for the backend and data resumes look right
- The OQ-6 proposal: a master variant plus one resume per role type

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
