# Work Journal

Keep your resume, portfolio page and LinkedIn up to date by journaling your work in Telegram.

You send short, informal messages about your work to a Telegram bot, and Work Journal keeps the facts in them. When you need current outputs, such as before a job hunt, you send `/refresh`: it turns the new facts into a structured, versioned career profile, and from it generates:
- LaTeX resumes
- one-page resumes tailored to specific job postings
- a hosted portfolio page
- LinkedIn update text

Every generated sentence is checked so that professional wording never changes the meaning or the numbers, and nothing goes public without your approval.

**Status:** in development. Milestone M1 (engine and command line) is under way; see the task backlog.

## Development

The backend lives in `backend/` and needs [uv](https://docs.astral.sh/uv/) and Python 3.12. From `backend/`:

```sh
uv sync          # install dependencies
uv run pytest    # run the tests
uv run wj --help # the command-line tool
```

The web app lives in `frontend/` and needs Node.js 22.22 or later. From `frontend/`:

```sh
npm ci           # install dependencies
npm run dev      # the web app at http://localhost:5173, using the API on port 8000
npm test         # run the tests
```

The full list of commands is in [CLAUDE.md](CLAUDE.md#commands).

## Documents

| Document | Purpose |
|---|---|
| [project-requirements.md](project-requirements.md) | What the product must do and why (the source of truth) |
| [tasks.md](tasks.md) | The backlog: one task = one pull request |
| [change-log.md](change-log.md) | What changed in each PR, and how to verify it |
| [CLAUDE.md](CLAUDE.md) | Conventions for Claude Code sessions |

## Planned stack

FastAPI (Python) · React + TypeScript · PostgreSQL · Telegram bot · LaTeX · Claude API · Railway
