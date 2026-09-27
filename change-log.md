# Change log

One short entry per pull request, newest first: what changed and what to verify. The details live in the PR itself.

## Awaiting verification
- T-001 · [#2](https://github.com/matchastack/work-journal/pull/2) · Backend scaffold
- T-000 · [#1](https://github.com/matchastack/work-journal/pull/1) · Project documents

---

## 2026-09-27 · T-001 Backend scaffold · [#2](https://github.com/matchastack/work-journal/pull/2)
**Changed:** added the `backend/` Python project: settings from the environment, a FastAPI app with `/healthz`, the `wj` command, and ruff, pyright and pytest.

**Verify** (from `backend/`):
- [ ] `uv sync`, then the all-checks line in `CLAUDE.md` passes
- [ ] `uv run uvicorn app.main:app --reload`, then http://127.0.0.1:8000/healthz shows `{"status":"ok"}`
- [ ] `uv run wj --help` lists `version`, and `uv run wj version` prints `0.1.0`

## 2026-09-27 · T-000 Project documents · [#1](https://github.com/matchastack/work-journal/pull/1)
**Changed:** added the requirements, task backlog, change log, `CLAUDE.md`, PR template and README. Dropped the JSON import (OQ-1), and approved committing the template skeleton (OQ-2).

**Verify:**
- [x] Scope, priorities, resume rules and LLM routing in `project-requirements.md`
- [x] Task sizes and order in `tasks.md`
- [x] Workflow and branch naming in `CLAUDE.md`
- [ ] This log is short enough
- [x] No personal data in the diff
