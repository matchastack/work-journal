# Change log

What each pull request changed, and **what you need to verify**. Newest first.

Every PR adds one entry here. When you've checked everything under **Verify**, tick the boxes, or tell Claude, and the PR moves out of "Awaiting your verification".

## Awaiting your verification

| Task | PR | What to check |
|---|---|---|
| T-000 Project documents | [#1](https://github.com/matchastack/work-journal/pull/1) | Read the documents and answer the open questions (below) |

---

## 2026-09-27 · T-000 · Project documents

**PR:** [#1](https://github.com/matchastack/work-journal/pull/1) · **Branch:** `docs/project-planning-documents` · **Status:** awaiting your review

### What changed
- **`project-requirements.md`**: the requirements document the project is grounded in. It covers:
  - problem, goals with measurable targets, scope, users and key concepts
  - user journeys
  - 104 functional requirements with priorities and acceptance criteria
  - 19 non-functional requirements and 10 resume rules
  - LLM policy, constraints, risks, architecture, milestones and open questions
- **`tasks.md`**: 54 PR-sized tasks for M0–M2. Each has a user story, acceptance criteria, dependencies and requirement IDs. M3–M4 are listed as epics.
- **`change-log.md`**: this file.
- **`CLAUDE.md`**: conventions for Claude Code sessions: workflow, branch naming, commits, LLM rules, data and security rules.
- **`.github/pull_request_template.md`**: the structure for every task PR.
- **`README.md`**: a short project overview, with links to the documents.

### Why
You asked for the requirements, the backlog, a change log and Claude instructions before any code is written.

### Changes since the plan you saw
- **Your master resume shaped the design.** `master-resume.tex` arrived while I was writing these documents.
  - Its metadata is now part of the data model (§5, FR-PRF): strength, verification status, swaps, open questions, benched and planned items, title variants, coursework subsets, skills presets, proficiency tiers and the gaps list.
  - Its hard rules are now resume rules enforced in code (§8).
  - Tailoring follows your "delete and swap" method: approved swaps first, and new verified phrasing only when no swap fits (FR-TLR-3).
- **Import is deterministic.** Your master resume is imported by parsing its LaTeX macros and structured comments, with no LLM (T-006). This replaces importing the older PDFs from the example repo. PDF import stays for other users (M4).
- **Import and rendering come early.** Both moved to the start of M1 (T-006, T-010, T-011), so you can check your real resume rendered from data before any LLM features land.
- **The template is pdfLaTeX** (Jake's Resume). The renderer uses pdfLaTeX (C5).
- **Model routing as you asked** (§10): the heavy tier (Opus) only for synthesis and tailoring selection; the standard tier (Sonnet) and light tier (Haiku) for short tasks. Exact model versions live in environment variables, not in the repo.
- **Branch names describe the change** (e.g. `docs/project-planning-documents`), as you asked. The rule is recorded in `CLAUDE.md`.

### Your answers applied during review
- **OQ-1:** `master-resume.json` isn't needed. FR-IMP-2 and T-007 are marked dropped, keeping their IDs so references stay stable. Past applications aren't imported; the application log starts with the first tailored resume.

### Verify
- [ ] `project-requirements.md` §3 Scope: anything missing, or anything that shouldn't be there?
- [ ] §7 Functional requirements: are the priorities (M/S/C) right?
- [ ] §8 Resume rules: do they match how you use your master resume?
- [ ] §10 LLM policy: is routing by tier as you want it?
- [ ] §15 Open questions: OQ-1 is answered. Please answer OQ-2 to OQ-6.
- [ ] `tasks.md`: are the task sizes and order right? Anything to split or merge?
- [ ] `CLAUDE.md`: are the workflow and branch-naming rules as you want them?
- [ ] This file: does the entry format tell you what you need?
- [ ] The diff contains no personal data. Your master resume's content is **not** committed.

### Not done / follow-ups
- There's no code yet. T-001 (backend scaffold) starts after you merge this PR.
- `master-resume.tex` isn't stored in the repo, and it only lasts as long as this session's uploads. If T-006 or T-011 runs in a new session, please upload it again.
