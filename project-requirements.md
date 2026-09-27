# Work Journal: Project Requirements

| | |
|---|---|
| **Document** | Project requirements (business requirements document) |
| **Version** | 0.1 (draft for owner review) |
| **Date** | 2026-09-27 |
| **Owner** | @matchastack |
| **Status** | Draft, under review in T-000 |

This document is the source of truth for what Work Journal must do and why. Tasks in [tasks.md](tasks.md) cite the requirement IDs defined here (`FR-*`, `NFR-*`, `R*`). When scope changes, update this document first.

## Contents

1. [Background and problem](#1-background-and-problem)
2. [Goals and success measures](#2-goals-and-success-measures)
3. [Scope](#3-scope)
4. [Users](#4-users)
5. [Key concepts](#5-key-concepts)
6. [User journeys](#6-user-journeys)
7. [Functional requirements](#7-functional-requirements)
8. [Resume rules](#8-resume-rules)
9. [Non-functional requirements](#9-non-functional-requirements)
10. [LLM policy](#10-llm-policy)
11. [Constraints and assumptions](#11-constraints-and-assumptions)
12. [Risks and mitigations](#12-risks-and-mitigations)
13. [Architecture overview](#13-architecture-overview)
14. [Milestones](#14-milestones)
15. [Open questions](#15-open-questions)

---

## 1. Background and problem

Most people update their resume, portfolio and LinkedIn only when they start job hunting. By then:
- Years of work are hard to remember.
- The details that make a resume credible (numbers, scope, tools, ownership) are gone.
- Several places need updating by hand.

The owner's current setup shows the cost of doing all this by hand:

- **A master resume in LaTeX.** It holds every bullet worth using. Comments in the file record each bullet's ID, strength, verification status, approved alternative phrasings ("swaps") and open questions. They also record rules about what may and may not be claimed.
- **Tailored resumes made by hand.** Each one-page resume is made by copying the master, deleting what doesn't fit the job posting and swapping in the posting's vocabulary.
- **A JSON twin of the master and an application log**, both kept in sync by hand. The app's database replaces both.
- **A portfolio website** that hardcodes the same content in code. Earlier resume exports have drifted away from it: duplicated entries, inconsistent names and mixed date formats.

Keeping all of this current depends on remembering to do it, and on remembering what happened months ago.

**Work Journal** replaces that manual upkeep:
- The owner sends short, informal messages about their work to a Telegram bot.
- The app turns them into a structured, versioned career profile.
- From that profile it keeps every output current: resumes, tailored resumes, a hosted portfolio page and LinkedIn text.
- It never changes the meaning or the numbers of what the owner said, and never publishes anything without the owner's approval.

## 2. Goals and success measures

| ID | Goal | Measure (target) |
|---|---|---|
| G1 | Capture work while it's fresh | The owner journals at least once every 2 weeks, measured over 3 months |
| G2 | Low effort | Writing an entry takes under 2 minutes, with no formatting and no other step needed |
| G3 | Keep outputs current | Proposals are ready within 2 minutes of an entry closing; a published change shows on the portfolio page immediately |
| G4 | Trustworthy wording | 0 altered or invented numbers, titles, dates or technologies in anything published or sent. Every bullet traces back to a fact. |
| G5 | Fast tailoring | A verified one-page tailored resume within 3 minutes of pasting a job posting |
| G6 | One source of truth | Resume variants, tailored resumes, the portfolio page and the LinkedIn pack all come from the same master profile |
| G7 | Affordable | LLM cost around US$2/month for regular journaling plus about US$0.50 per tailored resume; hosting at most US$15/month |

## 3. Scope

### In scope for v1 (milestones M1–M2)
- A Telegram bot for journaling, follow-up questions, open questions and reminders.
- Storage of journal messages, entries and extracted facts.
- A versioned master profile, variants, and review of proposed changes.
- Rewriting for each audience (HR/ATS, LinkedIn, a job posting), with a fidelity verifier.
- LaTeX resumes rendered from the owner's template.
- Job-posting tailoring, with a keyword coverage report and an application log.
- A portfolio page hosted by the app.
- A LinkedIn update pack (assisted: the owner pastes it in).
- Import of the owner's master resume from LaTeX.
- A web app for review, journal, profile, resumes, tailoring, portfolio, LinkedIn and settings.
- Deployment for one invited user.

### Out of scope for v1
- Automatic editing of LinkedIn profiles. It isn't possible (see [C1](#11-constraints-and-assumptions)).
- Updating third-party job boards.
- Editing websites the app doesn't host, such as an existing GitHub Pages site.
- Public sign-up, billing and custom domains.
- Voice notes, images and file attachments.
- Native mobile apps. Telegram is the mobile surface.

### Later
See milestones M3 and M4 in [§14](#14-milestones).

## 4. Users

| User | Description | In v1 |
|---|---|---|
| **Owner** | The first user: an engineer who keeps a LaTeX master resume, applies for roles now and then, and wants low-effort upkeep | Yes |
| **Visitor** | A recruiter or hiring manager who reads the public portfolio page and downloads resumes | Yes (public page only) |
| **Invited professional** | Another person using the app with their own data | No (M4) |

## 5. Key concepts

| Concept | Meaning |
|---|---|
| **Message** | One Telegram message from the owner or the bot. |
| **Entry** | Messages grouped into one journal entry. An entry closes after 30 minutes of quiet or on `/done`. |
| **Fact** | A neutral, structured record of something that happened, with these parts: a statement; metrics (subject, value, unit, qualifier); an ownership level (led / owned / contributed / assisted); tools; outcome; date; and the source entry. Facts are the ground truth for meaning and numbers. |
| **Brag bank** | All facts, whether or not any output uses them. |
| **Master profile** | The curated superset of everything worth using: basics, roles, education, projects, skills, summaries and bullets, with their metadata. Every change creates a new version. |
| **Bullet** | One line under a role or project, with: a stable ID; text; strength (high / medium / low, optionally per role type); verification status (yes / partial / no); swaps; open questions; notes; and a status (active / benched / planned). |
| **Swap** | A pre-approved alternative phrasing for a bullet or part of a bullet, tuned to a role type's or posting's vocabulary. It's the same fact in different words. |
| **Role type** | A kind of job the owner applies for, such as backend/full stack or ML/AI. The master profile groups presets by role type: coursework subsets, skills presets, summary variants and keep/cut notes. |
| **Variant** | A rule set that turns the master profile into one output. It covers selection, order, title choice, coursework subset, skills preset, summary, visibility, page limit and template. |
| **Change set** | A group of proposed operations on the master profile. Each operation cites the facts it came from and carries a verifier report. |
| **Verifier** | The check every generated sentence must pass before a person sees it: numbers, claims, style and resume rules. |
| **Publish** | Making a profile version live on the portfolio page. |
| **Application** | A tailored resume made for one job posting, logged with everything used to make it. |
| **Open question** | A missing detail that blocks a stronger bullet, such as the scale of a system or a root cause. The bot asks these over time. |

## 6. User journeys

- **J1 — Capture.**
  1. The owner messages the bot about something they did.
  2. The bot stores the message.
  3. If impact, numbers or ownership are unclear, the bot may ask **one** follow-up question.
  4. When the entry closes, the app extracts facts, and the bot replies with a one-to-three-line summary of what it understood.
- **J2 — Review and publish.**
  1. Weekly, or when the owner sends `/refresh`, the app proposes changes to the master profile. The bot says how many there are.
  2. The owner opens the Inbox. Each change shows a before/after diff, its sources and a verifier report.
  3. The owner accepts, edits or rejects each change. Accepting creates a new version.
  4. Publishing updates the portfolio page, and the resume variants re-render.
- **J3 — Tailor.**
  1. The owner pastes a job posting.
  2. The app selects bullets and uses approved swaps where one fits. Where none fits, it offers a new verified phrasing.
  3. It picks a title variant, a coursework subset and skills lines, fits the resume to one page, and shows a keyword coverage report.
  4. The owner adjusts it and downloads the PDF. The application is logged.
- **J4 — Catch up.**
  1. On first use, the bot runs a short interview about what has happened since the master resume was last updated.
  2. After that, it works through open questions a little at a time.
- **J5 — LinkedIn.** The owner opens the LinkedIn page and sees only the sections that changed. They copy each one into LinkedIn and mark it done.
- **J6 — Maintain.** The owner edits the master profile directly. For example: fix wording, bench a bullet, add a swap or answer an open question. These edits create versions like any other change.

## 7. Functional requirements

Priority: **M** = Must (v1) · **S** = Should (v1 if time allows) · **C** = Could (later milestone).

### 7.1 Telegram capture (FR-CAP)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CAP-1 | The bot receives updates through a webhook secured by Telegram's secret-token header. | M | Requests without the correct header get 401 and are not stored. |
| FR-CAP-2 | Only linked chats are accepted. | M | An unlinked chat gets a short "link your account in the web app" reply, and its message is not stored as a journal message. |
| FR-CAP-3 | Account linking uses a one-time deep link (`t.me/<bot>?start=<token>`) shown in the web app. | M | The token works once and expires after 15 minutes. Linking is confirmed in the chat and in Settings. |
| FR-CAP-4 | Messages are grouped into entries. An entry closes after 30 minutes without a message, or on `/done`. | M | The timeout can be configured. `/done` closes the entry immediately. |
| FR-CAP-5 | When an entry closes, the bot replies with a one-to-three-line summary of the facts it understood. | M | The summary is sent within 60 s of closing (p95). |
| FR-CAP-6 | The bot asks at most one follow-up question per entry, when impact, metrics or ownership are unclear. | M | Never more than one question per entry. A Skip button is offered, and the answer joins the same entry. |
| FR-CAP-7 | Commands: `/start`, `/done`, `/skip`, `/refresh`, `/catchup`, `/pause`, `/resume`, `/help`. | M | `/help` lists every command. |
| FR-CAP-8 | Chit-chat and messages unrelated to work are recognised and don't become facts. | S | A triage label is stored with each entry. |
| FR-CAP-9 | Job postings can be sent to the bot for tailoring. | C | M3 |
| FR-CAP-10 | Voice notes are transcribed into entries. | C | M3 |

### 7.2 Journal storage and view (FR-JRN)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-JRN-1 | Every Telegram update is stored when it arrives, and storing is idempotent on `update_id`. | M | Replaying an update creates no duplicate. |
| FR-JRN-2 | Raw updates are purged after 7 days; parsed messages are kept. | M | A scheduled job deletes raw updates older than 7 days. |
| FR-JRN-3 | If the owner edits a message in Telegram, the stored text is updated and earlier versions are kept. | M | Edit history is visible on the Journal page. |
| FR-JRN-4 | Journal message text and fact text are encrypted per column. | M | A database dump shows only ciphertext for these columns. |
| FR-JRN-5 | The web app shows the journal as one chronological document, grouped by month, with extracted facts inline. | M | |
| FR-JRN-6 | Entries and facts can be edited or deleted in the web app. | M | Deleting a fact that the profile cites asks for confirmation. Telegram doesn't notify bots of deleted messages, so deleting happens in the web app. |
| FR-JRN-7 | All entries and facts can be exported as Markdown and as JSON. | M | Export of 1,000 entries finishes within 10 s. |

### 7.3 Fact extraction (FR-EXT)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-EXT-1 | Each closed entry becomes zero or more facts. Each fact has a statement, kind, metrics, ownership, tools, outcome and date, plus a link to an existing role or project (or a proposed new one). | M | The facts match the schema. Entries unrelated to work produce no facts. |
| FR-EXT-2 | Every metric in a fact must appear in the entry text. | M | The number check rejects facts with numbers the entry doesn't contain. |
| FR-EXT-3 | The owner can edit or delete facts. | M | |
| FR-EXT-4 | An answer to an open question is stored as a fact linked to that question. | M | |

### 7.4 Master profile and versions (FR-PRF)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-PRF-1 | The master profile holds basics, roles, education, projects, skills, summaries and bullets. It uses JSON Resume field names where one exists, plus the metadata in §5. | M | JSON Schema exported; fixture validates. |
| FR-PRF-2 | Every change creates an immutable version, recording its parent, its author (owner or AI) and its change set. | M | |
| FR-PRF-3 | Any version can be restored. Restoring creates a new version. | M | |
| FR-PRF-4 | Each bullet has a status: *active*, *benched* (kept for the record with a reason, never rendered) or *planned* (never rendered until it exists). | M | |
| FR-PRF-5 | Each role records the following: approved title variants; a *load-bearing* flag (must always appear); a *during-education* flag (can be cut without leaving a gap); keep-for and cut-for role types; a sensitivity level (e.g. "architecture and outcomes only"); and honesty boundaries (things never to claim). | M | |
| FR-PRF-6 | Each skill records a category, a proficiency tier (strong / working / academic), a *verify-before-shipping* flag and a preferred spelling. A separate *gaps* list holds skills that must never be claimed. | M | |
| FR-PRF-7 | Education records honours, the full coursework list, named coursework subsets per role type, and rules such as "never print GPA" or "fixed order of majors". | M | |
| FR-PRF-8 | Summary variants are stored per role type and are off by default. | M | |
| FR-PRF-9 | Skills presets are stored per role type. | M | |
| FR-PRF-10 | Open questions have an ID, the bullets they block, and a status. | M | |
| FR-PRF-11 | Edits the owner makes in the web app create change sets and versions, just as AI proposals do. | M | |

### 7.5 Review inbox (FR-REV)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-REV-1 | Each proposed change set lists its operations. Each operation shows a before/after diff, its source facts (linked to their entries), the outputs it affects, and its verifier report. | M | |
| FR-REV-2 | The owner can accept each operation, edit it (it is re-verified) or reject it with an optional reason. | M | |
| FR-REV-3 | Accepted operations are applied atomically as one new profile version. | M | |
| FR-REV-4 | Rejection reasons feed into future prompts as style notes. | S | |
| FR-REV-5 | Single operations can be approved or rejected inline in Telegram. | C | M3 |

### 7.6 Fidelity verifier (FR-FID)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-FID-1 | **Number check.** Every number, percentage, multiplier, amount, duration, count and range in generated text matches a metric in the cited facts, in the same unit. | M | A changed number, an invented number or a changed unit is an error. |
| FR-FID-2 | A qualifier can't become stronger: "about 200" must not become "over 200". Derived figures, such as a percentage change, are allowed only when code computes them, and the formula is shown. | M | |
| FR-FID-3 | A metric that is in the facts but left out of the text is a warning, not an error. | M | |
| FR-FID-4 | **Claim check.** Generated text is split into claims, and each must be supported by the cited facts. It must not inflate ownership, or add tools, team sizes, outcomes or scope. | M | Known-bad test cases are caught. |
| FR-FID-5 | A claim that contradicts a role's honesty boundaries, or names a skill on the gaps list, is an error. | M | |
| FR-FID-6 | Vocabulary from a job posting is accepted only where it is a true synonym of what the facts say. | M | |
| FR-FID-7 | Text that fails is regenerated once, using the verifier's feedback. If it still fails, it is shown flagged and is never accepted automatically. | M | |
| FR-FID-8 | The verifier report is stored with each proposal and each application. | M | |

### 7.7 Writing styles (FR-WRT)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-WRT-1 | **Resume/HR style.** Starts with an action verb. Uses present tense for the current role and past tense otherwise. Follows "what + how + measurable result". No pronouns. At most 2 lines at the template's width. | M | The style check passes. |
| FR-WRT-2 | **LinkedIn style.** First person and slightly narrative, within LinkedIn's length limits. | M | |
| FR-WRT-3 | **Job-posting style.** Uses the posting's vocabulary where it is true, and leads with the most relevant facts. | M | |
| FR-WRT-4 | The owner's style notes (words to avoid, preferences) apply to every style. | M | |
| FR-WRT-5 | New phrasings the owner approves are saved as swaps on the bullet, so they can be reused. | M | |

### 7.8 LaTeX resumes (FR-RES)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-RES-1 | Resumes render from the owner's LaTeX template (based on Jake's Resume, compiled with pdfLaTeX), converted into a Jinja template. The preamble and macros stay unchanged. | M | |
| FR-RES-2 | Every value is LaTeX-escaped automatically. | M | Tests cover `& % $ # _ { } ~ ^ \`. |
| FR-RES-3 | Compilation runs with shell-escape off, restricted file access, a timeout and an isolated temporary directory. | M | |
| FR-RES-4 | Each render reports its page count and checks that the text can be extracted. | M | |
| FR-RES-5 | Named variants render on demand and after each accepted change. | M | |
| FR-RES-6 | The whole master profile can render as the full, multi-page master document for review. | S | |
| FR-RES-7 | A resume is fitted to one page by cutting the lowest-value content first. The font is never shrunk below 10 pt and margins never below 0.5 in. | M | |

### 7.9 Job tailoring (FR-TLR)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-TLR-1 | A pasted job posting is parsed into title, company, seniority, must-have and nice-to-have skills, responsibilities and key terms. The posting's exact spellings are kept. | M | |
| FR-TLR-2 | Bullets are selected from the whole master profile by relevance, strength and verification status. Selection respects keep/cut rules, load-bearing roles and bullet status. | M | |
| FR-TLR-3 | Each selected bullet uses the best approved swap. If no swap fits, a new phrasing is generated, verified and offered for approval. | M | |
| FR-TLR-4 | Each posting gets its own choice of title variant, coursework subset (4–6 courses), skills lines and optional summary. Skills lines use the posting's exact spelling where true, and only skills from the catalogue. | M | |
| FR-TLR-5 | Skills stay last by default. They move above Work Experience only for postings that are mostly a list of technologies. | S | |
| FR-TLR-6 | A keyword coverage report lists covered and missing terms. Missing terms are shown as gaps and never added. | M | |
| FR-TLR-7 | The result fits on one page (FR-RES-7) and passes the resume rules (§8) and the verifier. | M | |
| FR-TLR-8 | Each tailored resume is logged as an application with company, role, date, posting text, the bullet IDs and swaps used, the title variant, the verifier report and the PDF. | M | |
| FR-TLR-9 | **ATS check.** Text extracted from the PDF contains the covered key terms, with the sections in the expected order. | M | |
| FR-TLR-10 | Each application's status can be updated: applied, interviewing, offer or rejected. | C | M3 |

### 7.10 Portfolio page (FR-PRT)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-PRT-1 | A public page at `/p/<handle>`, rendered on the server from the latest *published* version. | M | An unknown handle returns 404. |
| FR-PRT-2 | The built-in template has these sections: hero, about/education, experience, projects with category tabs, skills, contact and resume downloads. | M | |
| FR-PRT-3 | Drafts never show. Publishing updates the page immediately. | M | |
| FR-PRT-4 | Each field has a visibility setting, and the phone number is hidden by default. Benched and planned items never show. | M | |
| FR-PRT-5 | An "open to work" toggle, and an option to hide the page from search engines. | M | |
| FR-PRT-6 | Open Graph tags and JSON-LD `Person` structured data. | M | |
| FR-PRT-7 | Visitors can download the resume variants the owner chooses. | S | |
| FR-PRT-8 | Meets WCAG 2.1 AA and is responsive down to a 360 px width. | M | An automated accessibility check reports no serious issues. |
| FR-PRT-9 | Custom domains, more templates, and export to GitHub Pages or JSON Resume. | C | M4 |

### 7.11 LinkedIn pack (FR-LIN)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-LIN-1 | Generate LinkedIn text from the master profile: the headline (≤ 220 characters), About (≤ 2,600) and each position description (≤ 2,000). | M | |
| FR-LIN-2 | Show only the sections that changed since they were last marked done. | M | |
| FR-LIN-3 | Each section has a copy button and a character counter. "Mark done" stores a snapshot. | M | |
| FR-LIN-4 | LinkedIn titles come from the role's approved title variants. A title that differs from the most recent application's title is flagged. | S | |
| FR-LIN-5 | Optional milestone posts through LinkedIn's official Share API, drafted by the app and approved by the owner. | C | M3 |

### 7.12 Reminders and habit (FR-REM)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-REM-1 | Reminders run on a configurable schedule and time zone. The default is Friday at 18:00, Asia/Singapore. | M | |
| FR-REM-2 | No reminder is sent if the owner journaled in the last 3 days. | M | |
| FR-REM-3 | Reminder buttons "Nothing this week" and "Remind me tomorrow". `/pause` stops reminders and `/resume` restarts them. | M | |
| FR-REM-4 | A first-run catch-up interview covers the time since the profile was last updated. | M | |
| FR-REM-5 | The bot asks open questions over time: at most one per reminder, most valuable first. | M | |
| FR-REM-6 | When proposals are ready, the bot says so ("N updates proposed → Review") with a link. | M | |
| FR-REM-7 | A monthly recap, and a brag-doc export for performance reviews. | C | M3 |

### 7.13 Settings (FR-SET)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-SET-1 | Shows the Telegram link status and allows relinking. | M | |
| FR-SET-2 | Reminder schedule, time zone and pause. | M | |
| FR-SET-3 | Portfolio handle, default visibility, open to work, and search-engine indexing. | M | |
| FR-SET-4 | Style notes. | M | |
| FR-SET-5 | Confidential terms, which are blocked from public outputs. | M | |
| FR-SET-6 | Data export, and "delete all my data" with confirmation. | M | |

### 7.14 Import and export (FR-IMP)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-IMP-1 | Import the owner's master resume from LaTeX: the macros give the structure, and structured comments give the metadata. | M | Structure: sections, roles, projects, bullets and skills lines. Metadata: bullet ID, strength, verification, swaps, open questions, notes, benched and planned items, title variants, coursework subsets, summary variants, skills presets, proficiency tiers and gaps. |
| FR-IMP-2 | ~~Import the JSON twin (`master-resume.json`), including the application log.~~ **Dropped** (OQ-1): the LaTeX master holds everything the app needs, and the app's database replaces the JSON twin. | — | — |
| FR-IMP-3 | Import is deterministic: it makes no LLM calls, and reports anything it couldn't map. | M | |
| FR-IMP-4 | Each imported bullet gets a linked fact, with metrics parsed by the number check, so the verifier can check future rewording. | M | |
| FR-IMP-5 | Imported personal data is stored only in the database, or in a git-ignored local file until the database exists. It is never stored in git. | M | |
| FR-IMP-6 | Import from a resume PDF, with LLM help, for other users. | C | M4 |
| FR-IMP-7 | Import from a LinkedIn data-export ZIP. | C | M4 |

### 7.15 Authentication (FR-AUTH)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-AUTH-1 | Sign in with GitHub. Only allow-listed GitHub usernames can sign in. | M | Other users see a clear message. |
| FR-AUTH-2 | The session cookie is HTTP-only, Secure and SameSite=Lax. Logging out clears it. | M | |
| FR-AUTH-3 | Email or Google sign-in, and public sign-up. | C | M4 |

## 8. Resume rules

These rules come from the owner's own master-resume practice. Code enforces them for every **sendable** resume: tailored resumes and named variants. The full master document is exempt from R6, because it spans several pages by design.

| ID | Rule | How it's enforced |
|---|---|---|
| R1 | Never invent a metric, date, title or technology. | The verifier (§7.6). Titles come only from approved variants, and technologies only from the skills catalogue, never the gaps list. |
| R2 | Never ship a placeholder. | Rendering is blocked if the output contains TODO or placeholder text. Missing optional fields, such as an undated project, produce a warning. |
| R3 | Never claim a skill that can't survive a whiteboard question. | A skill flagged *verify-before-shipping* needs explicit confirmation for each resume. Skills on the gaps list are always blocked. |
| R4 | Never let two bullets share a suspicious number. | A warning when the same metric (value and unit) appears in bullets under different roles. |
| R5 | For sensitive roles, describe architecture and outcomes only. | Roles marked sensitive get stricter confidentiality checks, and anything that looks like operational detail is flagged. |
| R6 | Anything sent is one page. | A page-count check. The resume is fitted by cutting, never by going below a 10 pt font or 0.5 in margins. |
| R7 | Load-bearing roles always appear. | Selection can't drop them. |
| R8 | Benched and planned items never render. | Enforced during selection. |
| R9 | No unearned seniority modifiers on titles. | Titles come only from the approved variants. |
| R10 | Education rules (e.g. never print GPA, fixed order of majors). | Stored as education rules and checked at render time. |

## 9. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-SEC-1 | Secrets live only in environment variables, never in git, logs or client code. `.env.example` lists variable names without values. |
| NFR-SEC-2 | Journal and fact text are encrypted per column. The key is held outside the database, and key rotation is supported. |
| NFR-SEC-3 | LaTeX compiles with shell-escape off, restricted file access, a 30 s timeout and an isolated temporary directory. Every value is escaped. |
| NFR-SEC-4 | The Telegram webhook verifies the secret-token header. GitHub OAuth uses a `state` parameter. Requests that change state are protected against CSRF. |
| NFR-PRIV-1 | No personal data is committed to git. Tests and fixtures use a fictional person. |
| NFR-PRIV-2 | Journal text is sent only to the Claude API, and is never logged. |
| NFR-PRIV-3 | The owner can export all their data and delete all their data. |
| NFR-PRIV-4 | Confidential terms never appear in public outputs: the portfolio page, published PDFs or the LinkedIn pack. |
| NFR-REL-1 | Webhook processing is idempotent, and background jobs retry with backoff. |
| NFR-REL-2 | LLM calls retry on transient errors. Refusals are handled and shown to the owner. |
| NFR-PERF-1 | The webhook responds within 1 s; heavy work runs in background jobs. |
| NFR-PERF-2 | A resume renders within 10 s. The portfolio page's time to first byte is at most 300 ms when cached. |
| NFR-COST-1 | Models are routed as in §10, and every LLM call is logged with its tokens and cost. |
| NFR-COST-2 | The owner can see the monthly LLM cost. |
| NFR-OBS-1 | Errors are reported to Sentry. Logs are structured and never contain journal text. |
| NFR-A11Y-1 | The portfolio page meets WCAG 2.1 AA, and the web app can be used with a keyboard. |
| NFR-MAINT-1 | Python is type-checked (pyright) and TypeScript runs in strict mode. Lint and tests run in CI. PRs are small and made of atomic commits. |
| NFR-MAINT-2 | Prompts are versioned files, and each LLM call records its prompt version. |
| NFR-DATA-1 | Profile versions are immutable. History is deleted only when the owner asks. |

## 10. LLM policy

### Model routing

The heavier models are used only where a task needs them. Exact model versions are set in configuration (environment variables), not in code or documents, so they can change without a code change.

| Tier | Model family | Used for |
|---|---|---|
| Heavy | Claude Opus | Profile synthesis (turning facts into a change set); tailoring selection and swap choice; PDF resume import (M4) |
| Standard | Claude Sonnet | Fact extraction; the catch-up interview; writing a single bullet or applying a swap; the claim verifier; the LinkedIn pack |
| Light | Claude Haiku | Follow-up questions; bot summaries; message triage; parsing job postings |

### Rules

- Structured outputs are parsed into Pydantic models. Invalid output is retried once, then fails visibly.
- Stable prompt prefixes (instructions and profile context) use prompt caching.
- Every generated sentence passes the verifier (§7.6) before it is stored as a proposal.
- Unit tests use a fake client. Real API calls run only in opt-in evaluation runs.
- **Evaluation bar** before a prompt or the model routing changes: 0 altered numbers, 0 inflated claims and at least 90 % of facts recovered on the evaluation set.
- Bulk re-generation uses the Message Batches API.

### Cost estimate

At list prices in September 2026: about US$2/month for regular journaling, plus about US$0.50 per tailored resume. The `llm_calls` table will measure the real figures once the app is running.

## 11. Constraints and assumptions

| ID | Constraint or assumption |
|---|---|
| C1 | LinkedIn's Profile Edit API is limited to approved partners. A self-serve app can only sign in and post to the member's own feed, and automating the LinkedIn website breaks its User Agreement. So LinkedIn updates are assisted (§7.11). |
| C2 | Telegram bot chats are not end-to-end encrypted; they are stored on Telegram's servers. The owner should keep anything under NDA out of messages. |
| C3 | Telegram's Bot API can't fetch chat history, so updates must be stored as they arrive. Bots are not told when a message is deleted. |
| C4 | v1 has one user, the owner. The data model is ready for more: every row belongs to a user. |
| C5 | The resume template needs pdfLaTeX (it uses `\pdfgentounicode`). |
| C6 | Hosting is on Railway (Singapore region). Costs are estimates. |
| A1 | The owner provides `master-resume.tex` (received). The JSON twin isn't needed (OQ-1). Past applications aren't imported; the app's application log starts with the first tailored resume. |
| A2 | The owner reviews proposals at least once a month. |

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The owner stops journaling | The product loses its value | A low-friction bot, at most one follow-up question, reminders with snooze, instant summaries, open questions as prompts, and a monthly recap (M3) |
| The LLM inflates or invents claims | Harm to the owner's reputation | The verifier (numbers, claims, honesty boundaries, gaps list), human review, and no automatic publishing |
| Confidential details leak into public outputs | Employment or legal risk | A confidential-terms blocklist (M2), sensitive-role rules (R5) and an LLM detector (M3) |
| A data breach | Personal data exposed | Column encryption, a 7-day limit on raw updates, secrets management, and an allowlist for login |
| Errors in the template or LaTeX break rendering | No resumes | Escaping, a smoke render in CI, and page and text checks |
| LLM costs creep up | Budget overrun | Model routing, prompt caching, the cost log and a monthly view |
| The Telegram API changes | Capture breaks | A thin adapter around the bot library, and integration tests with recorded payloads |

## 13. Architecture overview

```
Telegram ──webhook──▶ FastAPI "web" service ──▶ PostgreSQL ◀── "worker" service (Procrastinate)
                        │        │                                  │           │
        React web app ◀─┘        └─▶ public portfolio page          ▼           ▼
                                                               Claude API   LaTeX (latexmk)
```

| Component | Responsibility |
|---|---|
| `web` service | API, the webhook, the React web app (served as built files) and public portfolio pages |
| `worker` service | Extraction, synthesis, reminders, rendering and purges |
| PostgreSQL | All data. Profile versions are stored as immutable JSONB snapshots, and PDFs as bytes keyed by content hash. |
| Claude API | Extraction, writing, verification, synthesis and tailoring, routed by tier (§10) |
| TeX Live | Resume rendering, with only the packages the template needs |

### Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy 2 with Alembic; uv, ruff, pyright, pytest |
| Jobs | Procrastinate (a Postgres-backed queue with scheduled tasks) |
| LLM | Anthropic Python SDK, with structured outputs, prompt caching and the Batches API |
| Telegram | python-telegram-bot |
| Resume | Jinja2 with LaTeX-safe delimiters, latexmk/pdfLaTeX, pypdf |
| Portfolio page | Jinja2 rendered on the server, with Tailwind built by the standalone CLI |
| Web app | React, Vite, TypeScript, Tailwind, TanStack Query, React Router; API types generated with `openapi-typescript` |
| Auth | GitHub OAuth with an allowlist |
| Hosting | Railway: `web` and `worker` services from one Docker image, plus managed Postgres; Sentry for errors |

### Main tables

| Area | Tables |
|---|---|
| Accounts | `users`, `settings`, `telegram_links` |
| Journal | `telegram_updates`, `journal_messages`, `journal_entries`, `facts` |
| Profile | `profile_versions`, `change_sets`, `change_ops`, `variants`, `publications` |
| Applications | `job_postings`, `applications`, `artifacts` |
| Operations | `llm_calls` |

## 14. Milestones

| Milestone | Outcome | Tasks |
|---|---|---|
| **M0: Documents** | This document, the task backlog, the change log and `CLAUDE.md` | T-000 |
| **M1: Engine and command line** | Usable from the command line on local files: schemas, import of the owner's master resume, rendering with the owner's template, the number/claim/style verifier, writers, synthesis, tailoring, and the portfolio and LinkedIn generators | T-001 – T-024 |
| **M2: Journal loop** | Deployed for the owner: database, encryption, background jobs, sign-in, the Telegram bot, the web app and deployment | T-025 – T-053 |
| **M3: Quality and habit** | LLM confidentiality detector, inline approvals in Telegram, voice notes, postings via the bot, evaluations in CI, monthly recap and brag doc, LinkedIn milestone posts, application status | Epics |
| **M4: Open to others** | Email/Google sign-in and sign-up, PDF and LinkedIn-export import, a template gallery and per-user templates, custom domains, GitHub Pages and JSON Resume export, quotas, billing, a privacy policy and a security review | Epics |

## 15. Open questions

| ID | Question | Needed by | Answer |
|---|---|---|---|
| OQ-1 | Is `master-resume.json` (the JSON twin with bullet IDs, open questions and the application log) needed? | T-007 | **Resolved 2026-09-27: no.** The LaTeX master has everything the app needs, and the database replaces the JSON twin. FR-IMP-2 and T-007 are dropped. |
| OQ-2 | May I commit the template skeleton? That means Jake's Resume preamble and macros (MIT-licensed, credited) plus your one-line subheading macro, with **all personal content removed**. | T-011 | **Resolved 2026-09-27: yes.** |
| OQ-3 | What should the portfolio handle (`/p/<handle>`) and the app's domain be? | T-046, T-051 | Open |
| OQ-4 | Is the default reminder time right: Friday 18:00, Asia/Singapore? | T-035 | Open |
| OQ-5 | Which application-log fields matter to you, beyond what FR-TLR-8 lists (e.g. contacts, outcome)? | T-021 | Open |
| OQ-6 | Should the named resume variants be the role types from your master resume (backend/full stack, ML/AI, identity/security, systems)? | T-008 | Open |
