# CLAUDE.md

Guidance for Claude Code sessions in this repository. It's kept short; the details live in the linked documents.

## What this is

Work Journal turns informal Telegram journal messages into a structured, versioned career profile. From that profile it generates LaTeX resumes, resumes tailored to job postings, a hosted portfolio page and LinkedIn update text. The owner approves every change.

| Document | Purpose |
|---|---|
| [project-requirements.md](project-requirements.md) | The source of truth. Read the parts relevant to a task before starting it. |
| [tasks.md](tasks.md) | The backlog. One task = one PR. |
| [change-log.md](change-log.md) | A short entry per PR: what changed and what the owner must verify |

## Workflow

1. **Pick a task.** Take the lowest-ID task that isn't done or in progress, and read the requirements it cites. Then decide whether to start:
   - **Independent** (all its dependencies are merged): start it.
   - **Small dependency on an open PR** (it only needs that PR's scaffold or a stable interface): start it as a stacked PR (see step 2).
   - **Large dependency** (it builds on logic that is still under review and may change): wait for that PR to merge, or pick another task.
2. **Name the branch after the change,** as `<type>/<short-description>`, in kebab-case.
   - Examples: `feat/telegram-webhook-storage`, `fix/latex-escaping`, `docs/project-planning-documents`.
   - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`.
   - Don't use generic or session-generated names.
   - Branch from `main`. For a stacked PR, branch from the dependency's branch, set the PR's base to that branch, and write "Stacked on #N" in the PR. Once the dependency merges, retarget the PR to `main`.
3. **Commit atomically.** Each commit is one logical change, with the tests for that change in the same commit. Messages use `type(scope): summary`. A PR usually has several commits; only a tiny task has just one.
4. **Check before pushing.** Lint, type-check and tests must pass (see Commands).
5. **Update the docs in the same PR.**
   - In `tasks.md`, set the task's status and PR link.
   - In `change-log.md`, add a **short** entry: one line on what changed, and a few plain bullets on what to verify. **No checkboxes:** verification is ticked only in the PR. Don't copy the PR description; the details live in the PR.
6. **Open the PR** titled `T-XXX: <task title>`, using the PR template. Never push to `main`, and never merge.
7. **Change the requirements first.** If scope needs to change, update `project-requirements.md` in the PR and call it out in the PR description.

## Commands

Run these from `backend/` (uv project, Python 3.12):

| What | Command |
|---|---|
| Install dependencies | `uv sync` |
| Run the API locally (auto-reload) | `uv run uvicorn app.main:app --reload`, then open `/healthz` or `/docs` |
| Start Postgres for local development | `docker compose up -d` (repo root); set `DATABASE_URL` and `TEST_DATABASE_URL` as `compose.yml` says |
| Apply database migrations | `uv run alembic upgrade head` (after changing `app/db/models.py`: `uv run alembic revision --autogenerate --rev-id <next> -m "<change>"`) |
| Run the background worker (jobs and scheduled tasks in `app/jobs.py`) | `uv run wj worker` (needs `DATABASE_URL`) |
| Run the command-line tool | `uv run wj --help` |
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format .` |
| Type-check | `uv run pyright` |
| Tests | `uv run pytest` (tests that call the real Claude API: `uv run pytest -m llm`) |
| **All checks before pushing** | `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest` (CI runs the same checks on every PR and on `main`: `.github/workflows/ci.yml`) |

Add a dependency with `uv add <package>` (or `uv add --dev <package>` for tools), and commit `pyproject.toml` together with `uv.lock`.

## Layout

| Path | Contents |
|---|---|
| `backend/` | FastAPI app, engine and the `wj` command-line tool (from T-001) |
| `backend/migrations/` | Alembic migrations for `app/db/models.py` |
| `backend/templates/` | LaTeX resume template and portfolio templates |
| `frontend/` | React web app (from T-039) |
| `local/` | **Git-ignored** personal data: the imported profile and private evaluations. Never commit it. |

## Conventions

- **Python:** 3.12, with type hints everywhere. pyright is strict for `app/`, and ruff handles lint and formatting.
- **Schemas:** the Pydantic v2 models in `app/schema/` are the only definition of the core objects. Field names follow JSON Resume where one exists.
- **Profile operations** are pure functions; I/O stays at the edges.
- **TypeScript:** strict mode. API types are generated from OpenAPI, never written by hand.
- **Tests** sit next to the behaviour they cover. Fixtures use a fictional person (`backend/tests/fixtures/`).

## LLM rules

- **Route by tier** (requirements §10). Model IDs come from environment variables, never hardcoded.
  - **Heavy (Opus):** profile synthesis and tailoring selection.
  - **Standard (Sonnet):** extraction, writing, the claim verifier, the catch-up interview and the LinkedIn pack.
  - **Light (Haiku):** follow-up questions, summaries, triage and parsing job postings.
- **Every call goes through `app/llm/client.py`.** Prompts are versioned files in `app/llm/prompts/`.
- **Every generated sentence passes the verifier** before it's stored.
- **Tests:** unit tests use the fake client. Real API calls happen only in `pytest -m llm` and `wj eval`, never in default CI.
- **Evaluations:** run the evaluation suite before changing a prompt or the routing, and report the numbers in the PR.
- **Check the docs first:** before writing Claude API code, check the current SDK documentation (the `claude-api` skill) rather than relying on memory.

## Resume rules (enforced in code; never bypass)

- Never invent a metric, date, title or technology.
- Nothing sendable may contain a placeholder.
- Never claim skills from the gaps list.
- No suspicious number may be shared across roles.
- For sensitive roles, describe architecture and outcomes only.
- Anything sent is one page. Cut to fit; never shrink below a 10 pt font or 0.5 in margins.
- Load-bearing roles always appear.
- Benched and planned items never render.

The full list is in requirements §8.

## Data and security

- **Never commit personal data.** That means no real resume content, journal text, names, contact details or employers, whether in code, fixtures, docs, commit messages or PR descriptions. Real data lives in the database or in `local/`.
- **Secrets live only in environment variables.** `.env.example` lists names, never values.
- **Never log journal or fact text.**
- **Pass IDs, never journal or fact text, as job arguments.** Procrastinate stores them as plain JSON and logs them.
- **LaTeX:** escape every value, keep shell-escape off, and use a timeout and a temporary directory.
- **The Telegram webhook** must verify the secret-token header.

## Don't

- Don't push to `main`, merge PRs or rewrite shared history.
- Don't add a dependency without saying why in the PR.
- Don't widen a task's scope. Propose a new task in `tasks.md` instead.
- Don't skip or disable tests to get CI green.
