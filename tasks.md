# Tasks

The backlog for Work Journal. **Each task is one pull request.** Tasks cite requirement IDs from [project-requirements.md](project-requirements.md) (`FR-*`, `NFR-*`, `R*`).

## How to work a task

- **Status symbols:** ☐ todo · ◐ in progress / PR open · ☑ done (merged) · ⛔ blocked · ✖ dropped (kept so task IDs stay stable)
- **Size:** S = up to half a day · M = about a day · L = two to three days. Split anything bigger.
- **Branch:** named after the change, as `<type>/<short-description>`.
  - Examples: `feat/telegram-webhook-storage`, `fix/latex-escaping`, `docs/project-planning-documents`.
  - Types match the commit types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `build`.
  - Branch from `main`. When a task depends only lightly on an unmerged PR, stack it on that PR's branch and write "Stacked on #N". When it depends heavily, wait for the merge. `CLAUDE.md` step 1 has the rule.
- **Commits:** atomic. Each commit is one logical change, with its tests in the same commit. Messages use `type(scope): summary`.
- **PR:** titled `T-XXX: <task title>`, using the PR template. The same PR updates this file (status and PR link) and adds a short entry to [change-log.md](change-log.md).
- **Definition of done:**
  - All acceptance criteria are met.
  - Lint, type-check and tests pass in CI.
  - The change-log entry says in one line what changed and lists what to verify, without checkboxes.
  - No personal data or secrets are committed.

## Overview

| Milestone | Tasks | Status |
|---|---|---|
| M0: Documents | T-000 | ☑ |
| M1: Engine and command line | T-001 – T-024 and T-054 (T-007 dropped) | ◐ |
| M2: Journal loop | T-025 – T-053 | ☐ |
| M3: Quality and habit | Epics (at the end of this file) | — |
| M4: Open to others | Epics (at the end of this file) | — |

---

## M0: Documents

### T-000 · Project documents
**Status:** ☑ · **Size:** S · **Depends on:** — · **Requirements:** all (defines them) · **PR:** [#1](https://github.com/matchastack/work-journal/pull/1)

As the owner, I want the requirements, backlog, change log and Claude conventions written down before any code, so that every later PR is grounded and easy to verify.

- [x] `project-requirements.md` covers these, with IDs and priorities:
  - background, goals, scope, users and concepts
  - journeys and functional requirements
  - non-functional requirements and resume rules
  - LLM policy, constraints and risks
  - architecture, milestones and open questions
- [x] This file breaks M0–M2 into PR-sized tasks, each with a user story, acceptance criteria, dependencies and requirement IDs. M3–M4 are listed as epics.
- [x] Every Must requirement is covered by at least one task.
- [x] `change-log.md` has its format and the T-000 entry.
- [x] `CLAUDE.md` covers the workflow, branch naming, conventions, LLM rules and data rules.
- [x] A PR template is added and the README is expanded.
- [x] No personal data is committed.

---

## M1: Engine and command line

The goal of M1: everything the app does, usable from the `wj` command-line tool on local files. The owner's real data lives in the git-ignored `local/` folder.

### T-001 · Backend scaffold
**Status:** ☑ · **Size:** S · **Depends on:** T-000 · **Requirements:** NFR-MAINT-1, NFR-SEC-1 · **PR:** [#2](https://github.com/matchastack/work-journal/pull/2)

As a developer, I want a runnable Python project with the tooling in place, so that later tasks only add features.

- [x] `backend/` is a uv project (Python 3.12) with an `app` package, and `uv sync` works from a clean checkout.
- [x] Settings are read from the environment with pydantic-settings. `.env.example` lists every variable, with no real values.
- [x] A FastAPI app with `GET /healthz`, which returns `{"status": "ok"}`.
- [x] A `wj` command-line entry point (Typer) with `wj --help` and `wj version`.
- [x] ruff (lint and format), pyright (strict for `app/`) and pytest are configured. Smoke tests cover `/healthz` and `wj version`.
- [x] `.gitignore` covers `.env`, `local/` and build outputs.
- [x] The "Commands" section of `CLAUDE.md` is filled in.

### T-002 · CI pipeline
**Status:** ☑ · **Size:** S · **Depends on:** T-001 · **Requirements:** NFR-MAINT-1 · **PR:** [#3](https://github.com/matchastack/work-journal/pull/3)

As the owner, I want every PR checked automatically, so that I only review changes that already pass lint, types and tests.

- [x] `.github/workflows/ci.yml` runs on pull requests and on pushes to `main`.
- [x] It runs `ruff check`, `ruff format --check`, `pyright` and `pytest`. Tests marked `llm` are excluded.
- [x] The uv cache is enabled, and a run takes under 5 minutes.
- [x] CI is green on this PR.

### T-003 · Core schemas
**Status:** ☑ · **Size:** M · **Depends on:** T-001 · **Requirements:** FR-PRF-1, FR-PRF-4, FR-PRF-5, FR-PRF-6, FR-PRF-7, FR-PRF-8, FR-PRF-9, FR-PRF-10, FR-EXT-1, NFR-PRIV-1 · **PR:** [#4](https://github.com/matchastack/work-journal/pull/4)

As a developer, I want typed models for every core object, so that the engine, the API and LLM outputs share one definition.

- [x] Pydantic models:
  - **Profile:** basics, roles, education, projects, skills, summaries and open questions.
  - **Bullet:** id, text, strength, verification, swaps, needs, notes, status, tags, priority, visibility and sources.
  - **Role:** title variants, load-bearing, during-education, keep-for/cut-for, sensitivity and honesty boundaries.
  - **Skill:** category, tier, verify-before-shipping and preferred spelling, plus a gaps list.
  - **Education:** honours, coursework, subsets and rules.
  - **Fact and Metric:** Metric has subject, a value (single, from→to, or range), unit and qualifier.
  - **Variant, the ChangeOp union, JobPosting and Application.**
- [x] Field names follow JSON Resume wherever JSON Resume has one.
- [x] Dates are stored as `YYYY-MM`. IDs are stable strings.
- [x] `wj schema export` writes the JSON Schema files.
- [x] A fixture profile for a fictional person (`backend/tests/fixtures/`) validates. Round-trip tests (load → dump → load) pass.

### T-004 · Profile operations
**Status:** ☑ · **Size:** S · **Depends on:** T-003 · **Requirements:** FR-PRF-2, FR-PRF-3, FR-PRF-11, FR-REV-3, NFR-DATA-1 · **PR:** [#5](https://github.com/matchastack/work-journal/pull/5)

As a developer, I want pure functions that apply change operations and compare profiles, so that AI proposals and manual edits change the profile in exactly the same way.

- [x] `apply(profile, ops) -> profile` supports every op in the `ChangeOp` union (T-003):
  - AddBullet, EditBullet, SetBulletStatus, AddSwap
  - AddRole, AddEducation, AddProject
  - UpdateField, AddSkill, ResolveOpenQuestion
- [x] Ops address items by ID, and an unknown ID raises a clear error. Apply is atomic: either every op applies or none does.
- [x] `diff(a, b)` returns the changed items and fields in a readable structure.
- [x] Tests confirm that applying ops and then diffing shows exactly the applied changes.

### T-005 · Number checker
**Status:** ☑ · **Size:** M · **Depends on:** T-003 · **Requirements:** FR-FID-1, FR-FID-2, FR-FID-3, FR-EXT-2 · **PR:** [#6](https://github.com/matchastack/work-journal/pull/6)

As the owner, I want every number in generated text checked against my facts, so that nothing I publish or send misstates a metric.

- [x] It extracts:
  - integers and decimals, including thousands separators ("1,500")
  - percentages and multipliers ("5x")
  - currency, durations and counts
  - ranges ("3–5 days", "12% to 4.5%")
  - qualifiers ("about", "over", "up to", "300+")
- [x] It flags a number that isn't in the cited facts, a changed unit, or a stronger qualifier.
- [x] It accepts figures derived by code (percentage change, ratio, multiplier) and records the formula.
- [x] A metric that is in the facts but missing from the text is reported as a warning.
- [x] It can also parse metrics out of existing bullets, for import.
- [x] Unit tests cover every rule, including LaTeX-escaped input (`15\%`).

### T-006 · Import master resume from LaTeX
**Status:** ☑ · **Size:** M · **Depends on:** T-003, T-005 · **Requirements:** FR-IMP-1, FR-IMP-3, FR-IMP-4, FR-IMP-5, FR-WRT-6, NFR-PRIV-1 · **PR:** [#7](https://github.com/matchastack/work-journal/pull/7)

As the owner, I want my master resume converted into the profile format, so that the app starts from everything I've already written.

- [x] `wj import tex <path>` parses the template's macros into the profile: `\section`, `\resumeSubheading`, `\resumeSubheadingOneLine`, `\resumeProjectHeading`, `\resumeItem` and the skills lines.
- [x] Structured comments are mapped:
  - **Per bullet:** `ID`, `STRENGTH`, `VERIFIED`, `SWAPS` (with their context), `NEEDS` (open-question IDs) and `WARNING` / `NOTE` / `UPGRADE AVAILABLE` notes.
  - **Benched and planned items:** benched bullets (with reasons) and planned projects.
  - **Per role:** keep/cut notes and approved title variants.
  - **Education:** coursework subsets.
  - **Summaries:** summary variants.
  - **Skills:** skills presets, proficiency tiers and the gaps list.
  - **Open questions.**
- [x] LaTeX markup is converted to plain text: `\%` → `%`, `\&` → `&`, `--` → en dash.
- [x] Each active bullet gets a linked fact, with metrics parsed by the number checker (T-005).
- [x] No LLM calls are made. Anything that can't be mapped is listed in an import report.
- [x] Output goes to `local/profile.json`, which git ignores. Nothing personal is committed.
- [x] Tests use a fictional `.tex` fixture that exercises every macro and comment tag.

### T-007 · Import master-resume.json and the application log
**Status:** ✖ dropped · **Size:** — · **Depends on:** — · **Requirements:** FR-IMP-2 (dropped) · **PR:** —

Dropped after OQ-1: the JSON twin isn't needed. The LaTeX master (T-006) holds everything the app needs, and the app's database replaces the JSON twin. Past applications aren't imported; the application log starts with the first tailored resume (T-021).

### T-008 · Variant selection
**Status:** ☑ · **Size:** M · **Depends on:** T-003 · **Requirements:** FR-RES-5, FR-PRF-4, FR-PRF-5, FR-PRF-7, FR-PRF-8, FR-PRF-9, R7, R8 · **PR:** [#8](https://github.com/matchastack/work-journal/pull/8)

As the owner, I want variants to pick the right bullets, coursework, skills and summary from the master profile, so that every resume is consistent without manual deleting.

- [x] A variant selects roles, projects and bullets by role type (keep-for/cut-for and tags), status and priority. Benched and planned items are never selected.
- [x] Load-bearing roles are always included.
- [x] The variant chooses the coursework subset, the skills preset, the summary variant (or none) and the title variant.
- [x] Section order can be configured. The default is Education → Work Experience → Projects → Technical Skills.
- [x] Visibility is applied, for example showing or hiding the phone number per variant.
- [x] The only predefined variant is the full master document (OQ-6). One-page resumes are tailored to a posting (T-020).
- [x] Golden tests run against the fictional fixture.

### T-009 · Consistency linter and resume rules
**Status:** ☑ · **Size:** M · **Depends on:** T-005, T-008 · **Requirements:** R1, R2, R3, R4, R9, R10, FR-PRF-6 · **PR:** [#20](https://github.com/matchastack/work-journal/pull/20)

As the owner, I want my profile and every rendered resume checked against my rules, so that mistakes I've made before can't come back.

- [x] `wj lint` reports:
  - inconsistent date formats
  - skill names that don't use the preferred spelling
  - duplicate bullets
  - the same metric on bullets under different roles (R4)
  - placeholder text such as TODO in anything sendable (R2)
  - skills on the gaps list, or unconfirmed skills marked verify-before-shipping (R1, R3)
  - titles that aren't approved variants (R9)
  - breaches of the education rules (R10)
- [x] Each finding has a rule ID, a severity (error or warning) and a location.
- [x] Errors block rendering a sendable resume; warnings don't.
- [x] There are tests for each rule.

### T-010 · LaTeX renderer
**Status:** ☑ · **Size:** M · **Depends on:** T-008 · **Requirements:** FR-RES-2, FR-RES-3, FR-RES-4, FR-RES-5, FR-RES-7, R6, NFR-SEC-3, NFR-PERF-2 · **PR:** [#11](https://github.com/matchastack/work-journal/pull/11)

As the owner, I want resumes rendered safely from data through LaTeX, so that PDFs are consistent and unusual text can't break them.

- [x] A Jinja environment with LaTeX-safe delimiters (`\VAR{}`, `\BLOCK{}`, `\#{}`), which escapes every value automatically.
- [x] Compiles with `latexmk -pdf` (pdfLaTeX):
  - shell-escape off and restricted file access
  - a 30 s timeout and an isolated temporary directory
  - the compile log is returned on failure
- [x] Page count and extractable text are checked with pypdf.
- [x] Cut-to-fit: while the resume is over the page limit, drop the lowest-value selected bullet and report what was cut. Load-bearing roles are never dropped, and fonts and margins are never shrunk.
- [x] Tests use a small fixture template. `wj render` writes the master PDF.
- [x] Tests cover escaping of `& % $ # _ { } ~ ^ \`.
- [x] The required TeX Live packages are documented and installed in CI.

### T-011 · Owner's resume template
**Status:** ☑ · **Size:** S · **Depends on:** T-006, T-010 · **Requirements:** FR-RES-1, FR-RES-6 · **PR:** [#11](https://github.com/matchastack/work-journal/pull/11) (with T-010, at the owner's request)

As the owner, I want my own LaTeX template used for every resume, so that generated PDFs look exactly like the ones I send today.

- [x] The template's preamble and macros are unchanged; the document body is generated from the profile.
- [x] All personal content is removed from the committed template, and Jake Gutierrez's MIT license credit is included.
- [x] `wj render` produces the full multi-page master document, and a tailored resume (T-020) fits one page. Checked here by cutting the master to one page; T-020 does the same for each tailored resume.
- [x] Manual check: the rendered master matches the compiled uploaded master for all active content. Screenshots or a checklist go in the change log.

### T-012 · LLM client
**Status:** ◐ · **Size:** M · **Depends on:** T-001 · **Requirements:** NFR-COST-1, NFR-REL-2, NFR-MAINT-2, NFR-PRIV-2 · **PR:** [#9](https://github.com/matchastack/work-journal/pull/9)

As a developer, I want one client for every Claude call, so that routing, retries, caching, refusals and cost logging behave the same everywhere.

- [x] Each task is routed to a tier (heavy, standard or light, as in requirements §10). Model IDs come from environment variables.
- [x] Structured outputs are parsed into Pydantic models, and invalid output is retried once.
- [x] Stable prefixes use prompt caching. Refusals and transient errors are handled.
- [x] Prompts load from versioned files. Each call records task, model, prompt version, tokens, cost and latency (to JSONL until the database exists). Journal text is never logged.
- [x] Tests use a fake client. `pytest -m llm` makes real calls only when an API key is set.

### T-013 · Style checker
**Status:** ◐ · **Size:** S · **Depends on:** T-003 · **Requirements:** FR-WRT-1, FR-WRT-2, FR-WRT-4 · **PR:** [#10](https://github.com/matchastack/work-journal/pull/10)

As the owner, I want wording rules checked by code, so that every bullet follows the same style.

- [x] Resume style checks:
  - starts with an action verb
  - the past tense, for the current role too (a bullet describes work done)
  - no first-person pronouns
  - a length limit of about 2 lines at the template's width
- [x] LinkedIn style: character limits for each section.
- [x] The owner's words-to-avoid list is applied.
- [x] Each finding has a severity, and there are tests for each rule.

### T-014 · Fact extraction
**Status:** ☐ · **Size:** M · **Depends on:** T-005, T-012 · **Requirements:** FR-EXT-1, FR-EXT-2, FR-CAP-8 · **PR:** —

As the owner, I want my informal notes turned into structured facts, so that meaning and numbers are captured without me formatting anything.

- [ ] A standard-tier prompt returns facts with a statement, kind, metrics, ownership, tools, outcome and date, linked to an existing role or project (or a proposed new one).
- [ ] Facts containing numbers that aren't in the entry are dropped (by the T-005 checker).
- [ ] Text unrelated to work produces no facts.
- [ ] `wj extract <file>` prints the facts as JSON.
- [ ] Unit tests use the fake client, plus an opt-in evaluation on casual entries for a fictional person.

### T-015 · Claim verifier
**Status:** ☐ · **Size:** M · **Depends on:** T-005, T-009, T-012, T-013 · **Requirements:** FR-FID-4, FR-FID-5, FR-FID-6, FR-FID-7, FR-FID-8, R1, R3 · **PR:** —

As the owner, I want every generated sentence checked for claims my facts don't support, so that professional wording never turns into exaggeration.

- [ ] A standard-tier judge splits text into claims and marks each one supported, unsupported or contradicted by the cited facts.
- [ ] It detects:
  - inflated ownership
  - added tools, team sizes, outcomes or scope
  - breaches of honesty boundaries or the gaps list
  - vocabulary from a posting that isn't a true synonym
- [ ] The combined verifier runs the number check, claim check, style check and resume rules. A failure is regenerated once with feedback; if it still fails, it's flagged.
- [ ] The report is stored as structured data.
- [ ] Unit tests use the fake client. An opt-in evaluation uses known-bad examples, such as inflation and invented numbers.

### T-016 · Evaluation harness
**Status:** ☐ · **Size:** M · **Depends on:** T-014, T-015 · **Requirements:** NFR-MAINT-2 (and the evaluation bar in requirements §10) · **PR:** —

As the owner, I want LLM quality measured on a fixed dataset, so that any change to a prompt or model is judged by numbers.

- [ ] A dataset format covering entries → expected facts, facts → bullets, and adversarial cases.
  - Fictional data is committed.
  - Private cases go in `local/evals/`, which git ignores.
- [ ] `wj eval <suite>` reports altered numbers, inflated claims, fact recall and cost.
- [ ] A baseline report is committed.
- [ ] `CLAUDE.md` says to run it before changing prompts or model routing.

### T-017 · Writers
**Status:** ☐ · **Size:** M · **Depends on:** T-015 · **Requirements:** FR-WRT-1, FR-WRT-2, FR-WRT-3, FR-WRT-4, FR-WRT-5 · **PR:** —

As the owner, I want facts written up as resume or LinkedIn text, so that I get polished wording in the right voice.

- [ ] A standard-tier writer handles the resume/HR, LinkedIn and job-posting styles, and can apply a swap fragment to a bullet.
- [ ] Every output passes the combined verifier, and failures are flagged.
- [ ] Approved new phrasings can be saved as swaps (the AddSwap op).
- [ ] `wj write --style resume|linkedin --facts <ids>`
- [ ] Tests use the fake client, plus an opt-in evaluation.

### T-018 · Profile synthesis
**Status:** ☐ · **Size:** M · **Depends on:** T-004, T-017 · **Requirements:** FR-REV-1, FR-PRF-2, FR-EXT-4 · **PR:** —

As the owner, I want new facts turned into proposed profile changes, so that my master profile grows without me rewriting it.

- [ ] A heavy-tier prompt takes new facts and the current profile and returns a change set. It can contain:
  - new bullets
  - edits to existing bullets, such as adding a metric from an answered open question
  - new items and skills
  - resolved open questions
- [ ] Every op cites facts, and every text op carries a verifier report.
- [ ] It never re-activates benched or planned items without a fact that justifies it.
- [ ] `wj propose` prints the change set. `wj apply` applies it to `local/profile.json` after confirmation.
- [ ] Tests use the fake client, plus an opt-in evaluation.

### T-019 · Job-posting parser
**Status:** ☐ · **Size:** S · **Depends on:** T-012 · **Requirements:** FR-TLR-1 · **PR:** —

As the owner, I want a pasted posting broken into structured requirements, so that tailoring can match against them.

- [ ] A light-tier prompt returns title, company, seniority, must-have and nice-to-have skills, responsibilities and key terms, keeping the posting's exact spellings.
- [ ] `wj posting parse <file>`
- [ ] Tests use the fake client, with three fictional postings as fixtures.

### T-020 · Tailoring: selection and assembly
**Status:** ☐ · **Size:** L · **Depends on:** T-009, T-010, T-017, T-019 · **Requirements:** FR-TLR-2, FR-TLR-3, FR-TLR-4, FR-TLR-5, FR-TLR-7, R1–R10 · **PR:** —

As the owner, I want a one-page resume tailored to a posting from my master profile, so that each application takes minutes and stays truthful.

- [ ] A heavy-tier step selects content:
  - bullets, by relevance, strength and verification status
  - respecting keep/cut rules and load-bearing roles
  - the title variant and a coursework subset of 4–6 courses
  - skills lines, using the posting's exact spelling and only skills in the catalogue
  - an optional summary variant and the placement of the skills section
- [ ] Approved swaps are used first. The writer is asked for a new phrasing only when no swap fits, and new phrasings are verified and marked for approval.
- [ ] The resume rules (T-009) and the verifier pass, and the result is fitted to one page (T-010).
- [ ] A skill marked verify-before-shipping is listed only once you confirm it for this resume (`wj tailor --confirm <skill>`); otherwise the T-009 check blocks the render.
- [ ] `wj tailor <posting-file>` writes the PDF.
- [ ] Tests use the fake client, plus an opt-in evaluation on fictional postings.

### T-021 · Tailoring: coverage report and application log
**Status:** ☐ · **Size:** S · **Depends on:** T-020 · **Requirements:** FR-TLR-6, FR-TLR-8, FR-FID-8 · **PR:** —

As the owner, I want to see which of the posting's terms my resume covers, and keep a record of what I sent, so that gaps are honest and every application can be traced.

- [ ] The coverage report lists covered terms (and where they appear) and missing terms. Missing terms are never added.
- [ ] Each tailored resume is saved as an Application with company, role, date, posting text, the bullet IDs and swaps used, the title variant, the verifier report and the PDF.
- [ ] `wj tailor` prints the report, and `wj applications` lists the log.
- [ ] Tests.

### T-022 · ATS check
**Status:** ☐ · **Size:** S · **Depends on:** T-010, T-021 · **Requirements:** FR-TLR-9, FR-RES-4 · **PR:** —

As the owner, I want each PDF checked the way an applicant-tracking system reads it, so that the words I tailored for are actually machine-readable.

- [ ] Text is extracted from the PDF with pypdf.
- [ ] Every covered key term is found, the sections appear in the expected order, and there are no ligature or encoding problems.
- [ ] The result is included in the tailoring report.
- [ ] Tests on fixture PDFs.

### T-023 · Portfolio template and static build
**Status:** ☑ · **Size:** M · **Depends on:** T-008 · **Requirements:** FR-PRT-2, FR-PRT-4, FR-PRT-6, FR-PRT-8, NFR-A11Y-1 · **PR:** [#13](https://github.com/matchastack/work-journal/pull/13)

As the owner, I want my profile rendered as a portfolio page in the style of my current site, so that visitors see my up-to-date work.

- [x] Jinja2 templates with the sections: hero, about/education, experience, projects with category tabs, skills, contact and resume downloads.
  - Tailwind is built with the standalone CLI.
  - A little JavaScript handles the tabs and the mobile menu.
- [x] The web variant shows active bullets only. The phone number is hidden by default, and benched and planned items are never shown.
- [x] Open Graph tags and JSON-LD `Person` data.
- [x] `wj portfolio build --out <dir>` writes static HTML.
- [x] An automated accessibility check (axe-core) finds no serious issues, and the layout works at a 360 px width.
- [x] Snapshot tests run against the fictional fixture.

### T-024 · LinkedIn pack generator
**Status:** ☐ · **Size:** M · **Depends on:** T-017 · **Requirements:** FR-LIN-1, FR-LIN-2, FR-LIN-4 · **PR:** —

As the owner, I want LinkedIn text generated from my profile, and only for what changed, so that updating LinkedIn takes minutes.

- [ ] The standard-tier writer produces the headline (≤ 220 characters), About (≤ 2,600) and position descriptions (≤ 2,000), in the first person.
- [ ] Titles come from the approved variants, and a mismatch with the latest application's title is flagged.
- [ ] Output is compared with the last "done" snapshot, and only changed sections are listed.
- [ ] Every sentence is verified.
- [ ] `wj linkedin` prints the pack.

### T-054 · ASCII characters everywhere
**Status:** ☑ · **Size:** S · **Depends on:** T-006 · **Requirements:** FR-WRT-6 · **PR:** [#19](https://github.com/matchastack/work-journal/pull/19)

As the owner, I want everything the app writes to use ASCII characters, so that files, resumes and pages never carry look-alikes such as the en dash.

- [ ] Rules in `app/text.py` replace typographic dashes, quotes, ellipses, spaces, invisible characters and symbols with their ASCII versions, in every string of every schema model.
- [ ] Letters with accents and currency signs stay.
- [ ] Code writes ASCII itself, for example "..." when it shortens text.
- [ ] The import's profile, facts and report are pure ASCII.
- [ ] Rendered output follows the same rule: resume and portfolio date ranges, page titles and separators (in the PRs that add them).
- [ ] Tests.

---

## M2: Journal loop

The goal of M2: the Telegram bot, background jobs and web app running on Railway for the owner.

### T-025 · Database foundation
**Status:** ◐ · **Size:** M · **Depends on:** T-001 · **Requirements:** NFR-MAINT-1 · **PR:** [#14](https://github.com/matchastack/work-journal/pull/14)

As a developer, I want PostgreSQL, migrations and test fixtures in place, so that features can store data safely.

- [x] `compose.yml` runs Postgres 16 for local development.
- [x] Async SQLAlchemy engine and session. Alembic is configured, and the first migration creates `users` and `settings`.
- [x] Every table that belongs to a user has a `user_id`.
- [x] Tests get a fresh database, and CI uses a Postgres service container.
- [x] `/healthz` also checks the database.

### T-026 · Column encryption
**Status:** ◐ · **Size:** S · **Depends on:** T-025 · **Requirements:** FR-JRN-4, NFR-SEC-2 · **PR:** [#15](https://github.com/matchastack/work-journal/pull/15)

As the owner, I want my journal text encrypted in the database, so that a leaked dump or backup can't be read.

- [x] A SQLAlchemy column type encrypts and decrypts text with the key from `DATA_ENCRYPTION_KEY`, and stores a key ID for rotation.
- [x] `wj keys rotate` re-encrypts data with a new key.
- [x] Tests: ciphertext at rest, a clean round trip, and a clear failure with the wrong key.

### T-027 · Persistence for engine data, and loading the master profile
**Status:** ☐ · **Size:** M · **Depends on:** T-003, T-025, T-026 · **Requirements:** FR-PRF-2, FR-PRF-3, FR-IMP-5, NFR-COST-1, NFR-DATA-1 · **PR:** —

As the owner, I want my imported profile and all engine data stored in the database, so that the app works from one durable source.

- [ ] Tables and repositories for:
  - profile versions (immutable JSONB)
  - facts, change sets and ops, variants
  - job postings, applications
  - artifacts (PDF bytes keyed by hash) and `llm_calls`
- [ ] `wj db load-profile local/profile.json --user <login>` loads the imported master profile as version 1. This is the "convert to database format" step for the owner's resume.
- [ ] Engine commands can read and write the database with `--db`, instead of local files.
- [ ] Migration tests. Restoring a version creates a new version.

### T-028 · Background jobs
**Status:** ☐ · **Size:** S · **Depends on:** T-025 · **Requirements:** NFR-REL-1 · **PR:** —

As a developer, I want a durable job queue with scheduled tasks, so that slow work and timers don't block requests.

- [ ] Procrastinate runs on Postgres, and `wj worker` starts the worker.
- [ ] Retries with backoff, and scheduled tasks using cron syntax.
- [ ] An example job with a test.
- [ ] The worker is documented in `CLAUDE.md`.

### T-029 · GitHub sign-in with an allowlist
**Status:** ☐ · **Size:** M · **Depends on:** T-025 · **Requirements:** FR-AUTH-1, FR-AUTH-2, NFR-SEC-4 · **PR:** —

As the owner, I want to sign in with GitHub and nobody else to get in, so that my data stays private.

- [ ] The OAuth flow uses `state`. The callback creates or loads the user.
- [ ] Only logins in `ALLOWED_GITHUB_LOGINS` can sign in; anyone else sees a clear message.
- [ ] The session cookie is HTTP-only, Secure and SameSite=Lax. Endpoints `/auth/me` and `/auth/logout`.
- [ ] Requests that change state are protected against CSRF.
- [ ] Tests use a mocked GitHub API.

### T-030 · Telegram webhook and message storage
**Status:** ☐ · **Size:** M · **Depends on:** T-026, T-028 · **Requirements:** FR-CAP-1, FR-CAP-2, FR-JRN-1, FR-JRN-2, FR-JRN-3, NFR-PERF-1, NFR-REL-1, NFR-SEC-4 · **PR:** —

As the owner, I want every message I send the bot saved safely the moment it arrives, so that nothing I journal is lost.

- [ ] `POST /telegram/webhook` checks the secret-token header, returning 401 if it's wrong, and responds within 1 s.
- [ ] Raw updates are stored idempotently by `update_id`. Parsed messages are stored encrypted, and edits keep their history.
- [ ] An unlinked chat gets the linking hint, and nothing is stored as a journal message.
- [ ] A purge job deletes raw updates older than 7 days.
- [ ] `wj telegram poll` for local development, and `wj telegram set-webhook`.
- [ ] Tests with recorded update payloads: new, duplicate, edited and unlinked.

### T-031 · Telegram account linking
**Status:** ☐ · **Size:** S · **Depends on:** T-029, T-030 · **Requirements:** FR-CAP-3, FR-SET-1 · **PR:** —

As the owner, I want to link my Telegram chat to my account in one tap, so that the bot knows the messages are mine.

- [ ] An API call creates a one-time token (15-minute expiry) and returns the deep link.
- [ ] `/start <token>` links the chat. Reused or expired tokens are rejected.
- [ ] A confirmation message is sent in Telegram, and the link status is available through the API.
- [ ] Tests.

### T-032 · Entry grouping
**Status:** ☐ · **Size:** S · **Depends on:** T-030 · **Requirements:** FR-CAP-4, FR-CAP-7 · **PR:** —

As the owner, I want my messages grouped into journal entries automatically, so that I can send several short messages about one thing.

- [ ] A message joins the open entry. A new entry starts after 30 minutes of quiet; the timeout can be configured.
- [ ] A scheduled job closes quiet entries, and `/done` closes an entry immediately.
- [ ] `/help` lists the commands.
- [ ] Tests for the edge cases: a gap of exactly 30 minutes, and edits to a closed entry.

### T-033 · Extraction on entry close, and summary reply
**Status:** ☐ · **Size:** M · **Depends on:** T-014, T-027, T-032 · **Requirements:** FR-CAP-5, FR-EXT-1, FR-CAP-8 · **PR:** —

As the owner, I want the bot to tell me what it understood from each entry, so that I can correct it while it's fresh.

- [ ] Closing an entry queues extraction (standard tier), and the facts are stored encrypted.
- [ ] A light-tier summary reply arrives within 60 s (p95), with a link to the entry in the web app.
- [ ] Triage: an entry unrelated to work produces no facts, just a short acknowledgement.
- [ ] Failures are retried, and the owner is told if extraction finally fails.
- [ ] Tests use the fake LLM client.

### T-034 · Follow-up question
**Status:** ☐ · **Size:** S · **Depends on:** T-033 · **Requirements:** FR-CAP-6 · **PR:** —

As the owner, I want at most one short follow-up question when my note is missing impact or numbers, so that entries become stronger without nagging.

- [ ] A light-tier check decides whether impact, metrics or ownership are missing.
- [ ] At most one question per entry, with a Skip button.
- [ ] The answer joins the same entry before extraction.
- [ ] Tests.

### T-035 · Reminders
**Status:** ☐ · **Size:** M · **Depends on:** T-028, T-031 · **Requirements:** FR-REM-1, FR-REM-2, FR-REM-3, FR-SET-2 · **Needs:** OQ-4 · **PR:** —

As the owner, I want a gentle reminder on my schedule, so that journaling becomes a habit.

- [ ] Each user has their own schedule and time zone, with a default from settings.
- [ ] The reminder is skipped if the owner journaled in the last 3 days.
- [ ] Buttons: "Nothing this week" (which is logged) and "Remind me tomorrow". `/pause` and `/resume`.
- [ ] Tests with a frozen clock.

### T-036 · Open-question prompts
**Status:** ☐ · **Size:** S · **Depends on:** T-035, T-027 · **Requirements:** FR-REM-5, FR-EXT-4, FR-PRF-10 · **PR:** —

As the owner, I want the bot to ask me the open questions from my master resume, one at a time, so that my weaker bullets get the details they need.

- [ ] A reminder can include one open question, most valuable first. Value is the strength of the bullets the question blocks.
- [ ] The answer is stored as a fact linked to the question.
- [ ] The next synthesis proposes the upgraded bullet, and the question is marked answered when the owner accepts.
- [ ] Tests.

### T-037 · Catch-up interview
**Status:** ☐ · **Size:** M · **Depends on:** T-033 · **Requirements:** FR-REM-4 · **PR:** —

As the owner, I want a short guided interview on first use, so that the months since my last resume update are captured quickly.

- [ ] It runs once after linking, or on `/catchup`, using the standard tier.
- [ ] It asks one question at a time, up to about 10, about roles, projects, achievements and skills since the profile was last updated.
- [ ] Answers become entries, then facts, then a change set.
- [ ] The owner can stop at any time and resume later.

### T-038 · Weekly synthesis, /refresh and notification
**Status:** ☐ · **Size:** S · **Depends on:** T-018, T-027, T-028, T-033 · **Requirements:** FR-REM-6, FR-REV-1 · **PR:** —

As the owner, I want proposals prepared weekly or on demand, with a heads-up in Telegram, so that I review in batches.

- [ ] Profile synthesis runs weekly on new facts, and also on `/refresh`.
- [ ] The bot sends "N updates proposed → Review" with a link. Nothing is sent when there are no proposals.
- [ ] Tests.

### T-039 · Web app scaffold
**Status:** ☐ · **Size:** M · **Depends on:** T-029 · **Requirements:** NFR-MAINT-1, NFR-A11Y-1 · **PR:** —

As a developer, I want the React app set up with typed API access and sign-in, so that pages can be added one at a time.

- [ ] `frontend/` with React, Vite, TypeScript (strict), Tailwind, React Router and TanStack Query.
- [ ] An API client whose types are generated from FastAPI's OpenAPI schema (`openapi-typescript`).
- [ ] A sign-in page, an authenticated layout, and navigation to every page.
- [ ] FastAPI serves the built app, and the Vite dev server proxies to the API.
- [ ] ESLint, tsc and Vitest run in CI.

### T-040 · Inbox
**Status:** ☐ · **Size:** M · **Depends on:** T-038, T-039 · **Requirements:** FR-REV-1, FR-REV-2, FR-REV-3, FR-REV-4 · **PR:** —

As the owner, I want to review each proposed change with its sources and checks, so that I stay in control of what my profile says.

- [ ] Lists pending change sets. Each op shows a word-level before/after diff, source facts (linked to their journal entries), the outputs it affects and the verifier report.
- [ ] Accept, edit (re-verified) or reject each op, with an optional reason. Bulk accept is available.
- [ ] Applying the accepted ops creates a new profile version. Rejection reasons are saved as style notes.
- [ ] Works with the keyboard. Tests for the API and components.

### T-041 · Journal page
**Status:** ☐ · **Size:** M · **Depends on:** T-033, T-039 · **Requirements:** FR-JRN-5, FR-JRN-6, FR-JRN-7, FR-EXT-3 · **PR:** —

As the owner, I want to read my journal as one document and correct it, so that my record stays accurate.

- [ ] One chronological document, grouped by month, with facts shown inline and each message's edit history.
- [ ] Entries and facts can be edited and deleted, with a warning when the profile cites a fact.
- [ ] Export as Markdown and as JSON.
- [ ] Tests.

### T-042 · Profile page: items, bullets and versions
**Status:** ☐ · **Size:** M · **Depends on:** T-027, T-039 · **Requirements:** FR-PRF-3, FR-PRF-4, FR-PRF-11 · **PR:** —

As the owner, I want to edit my roles, projects and bullets directly, so that I can fix things without going through the bot.

- [ ] View and edit basics, roles, education, projects and bullets. A bullet's text, status (active, benched with a reason, or planned), strength, verification status and tags can all be edited.
- [ ] Edits go through change sets and create versions.
- [ ] Version history with diffs, and a Restore button (which creates a new version).
- [ ] Tests.

### T-043 · Profile page: swaps, titles, coursework, skills, summaries and open questions
**Status:** ☐ · **Size:** M · **Depends on:** T-042 · **Requirements:** FR-PRF-5, FR-PRF-6, FR-PRF-7, FR-PRF-8, FR-PRF-9, FR-PRF-10, FR-WRT-5 · **PR:** —

As the owner, I want to manage the metadata that drives tailoring, so that my master-resume practice lives in the app.

- [ ] Edit swaps (with their context), approved title variants, load-bearing and keep/cut rules, and honesty boundaries.
- [ ] Edit coursework subsets, education rules and summary variants.
- [ ] Edit skills (tier, preferred spelling, verify-before-shipping), the gaps list and skills presets.
- [ ] Create, answer and close open questions.
- [ ] Tests.

### T-044 · Resumes page
**Status:** ☐ · **Size:** M · **Depends on:** T-010, T-011, T-027, T-039 · **Requirements:** FR-RES-5, FR-RES-6 · **PR:** —

As the owner, I want to preview and download my master resume and my tailored resumes, so that I always have a current version ready.

- [ ] Lists the master document and every tailored resume, with a PDF preview and a download for each.
- [ ] Shows a PDF preview, a page-count badge, the cut report and lint findings, with a download button.
- [ ] Renders are cached by content hash.
- [ ] Tests.

### T-045 · Tailor page and application history
**Status:** ☐ · **Size:** M · **Depends on:** T-021, T-022, T-039 · **Requirements:** FR-TLR-1, FR-TLR-2, FR-TLR-3, FR-TLR-4, FR-TLR-6, FR-TLR-7, FR-TLR-8, FR-TLR-9 · **PR:** —

As the owner, I want to paste a posting and get a tailored resume I can adjust, so that applying takes minutes.

- [ ] Paste a posting to see the parsed requirements, then a tailored draft with the coverage report, verifier findings, rule findings and ATS check.
- [ ] Approve new phrasings (they're saved as swaps), adjust swap choices and re-render.
- [ ] Confirm each skill marked verify-before-shipping that the draft lists, or drop it (R3).
- [ ] Download the PDF. The application is logged, and the history list shows the posting, date and PDF.
- [ ] Tests.

### T-046 · Public portfolio page and publishing
**Status:** ☐ · **Size:** M · **Depends on:** T-023, T-027, T-039 · **Requirements:** FR-PRT-1, FR-PRT-3, FR-PRT-4, FR-PRT-5, FR-PRT-7, FR-SET-3, NFR-PERF-2 · **Needs:** OQ-3 · **PR:** —

As the owner, I want my portfolio page served by the app and updated when I publish, so that my public profile is always current.

- [ ] `/p/<handle>` renders the published version with the built-in template. An unknown handle returns 404.
- [ ] Publish and unpublish from the web app, with a preview of the draft first.
- [ ] Visibility, open-to-work and noindex settings are respected. The resumes I choose can be downloaded.
- [ ] The page is cached. Tests: only the published version is shown, and hidden fields are absent.

### T-047 · LinkedIn page
**Status:** ☐ · **Size:** S · **Depends on:** T-024, T-039 · **Requirements:** FR-LIN-2, FR-LIN-3 · **PR:** —

As the owner, I want a checklist of LinkedIn sections to paste, so that LinkedIn stays in step with my resume.

- [ ] Shows only the changed sections, each with a copy button and a character counter.
- [ ] "Mark done" stores a snapshot.
- [ ] Tests.

### T-048 · Settings page
**Status:** ☐ · **Size:** M · **Depends on:** T-031, T-035, T-039 · **Requirements:** FR-SET-1, FR-SET-2, FR-SET-3, FR-SET-4, FR-SET-5, FR-SET-6, NFR-PRIV-3 · **PR:** —

As the owner, I want all my preferences in one place, so that I can tune the app without code changes.

- [ ] Settings:
  - Telegram link, with a deep link and QR code
  - reminder schedule and time zone
  - portfolio handle, visibility, open to work and indexing
  - style notes and confidential terms
- [ ] Export all data, and "delete all my data" with confirmation.
- [ ] Tests.

### T-049 · Confidential terms and sensitive roles
**Status:** ☐ · **Size:** S · **Depends on:** T-015, T-046 · **Requirements:** FR-SET-5, NFR-PRIV-4, R5 · **PR:** —

As the owner, I want confidential terms and sensitive-role details kept out of anything public, so that journaling freely can't get me in trouble.

- [ ] Confidential terms (case-insensitive, whole words) are blocked from public outputs: the portfolio page, published PDFs and the LinkedIn pack.
- [ ] For roles marked sensitive, generated text is checked for operational detail and flagged in the Inbox.
- [ ] Violations show as errors, naming the matched term.
- [ ] Tests.

### T-050 · Docker image
**Status:** ☐ · **Size:** M · **Depends on:** T-011, T-039 · **Requirements:** NFR-SEC-3 · **PR:** —

As a developer, I want one production image, so that the web service and the worker run the same tested build.

- [ ] A multi-stage build:
  - Node builds the web app and the portfolio CSS.
  - Python 3.12 slim adds a TeX Live subset (only the packages the template needs) and the uv dependencies.
- [ ] Runs as a non-root user, with a health check.
- [ ] CI builds the image and renders the fixture resume inside it.

### T-051 · Railway deployment
**Status:** ☐ · **Size:** M · **Depends on:** T-050 · **Requirements:** NFR-SEC-1 · **Needs:** OQ-3 · **PR:** —

As the owner, I want the app deployed and updated automatically, so that it's always running the latest merged code.

- [ ] `web` and `worker` services run from the image, with managed Postgres and `alembic upgrade head` before each deploy.
- [ ] Environment variables are documented (no values in git). A custom domain is set, and the Telegram webhook is set on deploy.
- [ ] Railway deploys `main` only after CI passes.
- [ ] A deployment runbook in `docs/deploy.md`.

### T-052 · Observability
**Status:** ☐ · **Size:** S · **Depends on:** T-051 · **Requirements:** NFR-OBS-1, NFR-COST-2, NFR-PRIV-2 · **PR:** —

As the owner, I want errors and LLM costs visible, so that problems and spending don't surprise me.

- [ ] Sentry for the API and the worker, with journal text scrubbed.
- [ ] Structured JSON logs that never contain journal text.
- [ ] A monthly LLM cost view in Settings, broken down by task and tier.

### T-053 · End-to-end tests and staging checklist
**Status:** ☐ · **Size:** M · **Depends on:** T-040, T-046, T-051 · **Requirements:** FR-REV-3, FR-PRT-3 · **PR:** —

As the owner, I want the main journeys tested end to end, so that releases don't break the core loop.

- [ ] A Playwright test covers: sign in (test mode) → accept in the Inbox → publish → the public page shows the change. The LLM is stubbed.
- [ ] `docs/staging-checklist.md` covers:
  - dev bot message → summary → proposal → publish
  - checking the page and the PDFs
  - tailoring a real posting
- [ ] The Playwright test runs in CI.

---

## M3: Quality and habit (epics, broken into tasks when M2 is done)

- **Confidentiality detector:** an LLM check on top of the blocklist (FR-SET-5, R5).
- **Inline approvals in Telegram:** single operations approved or rejected in the chat (FR-REV-5).
- **Voice notes:** transcribed into entries (FR-CAP-10).
- **Job postings through the bot** (FR-CAP-9).
- **Evaluations in CI on demand, and a cost dashboard.**
- **Monthly recap and brag-doc export** (FR-REM-7).
- **LinkedIn milestone posts** through the official Share API, opt-in (FR-LIN-5).
- **Application status tracking** (FR-TLR-10).

## M4: Open to others (epics)

- **Email or Google sign-in, and public sign-up** (FR-AUTH-3).
- **Import** from a resume PDF (heavy tier) and from a LinkedIn data-export ZIP (FR-IMP-6, FR-IMP-7).
- **Template gallery**, and per-user LaTeX templates compiled in a sandbox.
- **Custom domains, and export to GitHub Pages or JSON Resume** (FR-PRT-9).
- **Quotas, billing, a privacy policy, account deletion and a security review.**
