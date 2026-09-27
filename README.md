# Work Journal

Keep your resume, portfolio page and LinkedIn up to date by journaling your work in Telegram.

You send short, informal messages about your work to a Telegram bot. Work Journal turns them into a structured, versioned career profile, and from it generates:
- LaTeX resumes
- one-page resumes tailored to specific job postings
- a hosted portfolio page
- LinkedIn update text

Every generated sentence is checked so that professional wording never changes the meaning or the numbers, and nothing goes public without your approval.

**Status:** planning. There's no code yet; see the task backlog.

## Documents

| Document | Purpose |
|---|---|
| [project-requirements.md](project-requirements.md) | What the product must do and why (the source of truth) |
| [tasks.md](tasks.md) | The backlog: one task = one pull request |
| [change-log.md](change-log.md) | What changed in each PR, and how to verify it |
| [CLAUDE.md](CLAUDE.md) | Conventions for Claude Code sessions |

## Planned stack

FastAPI (Python) · React + TypeScript · PostgreSQL · Telegram bot · LaTeX · Claude API · Railway
