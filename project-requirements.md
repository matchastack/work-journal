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
- The app extracts facts from each entry and keeps them all in one database, the brag bank.
- When the owner asks (`/refresh`), for example before a job hunt, the app turns the facts gathered since the last refresh into changes to a structured, versioned career profile. Once the owner has reviewed them, it updates every output from that profile: the master resume, the hosted portfolio page and the LinkedIn text. Tailored resumes are made on demand, one per job posting.
- Nothing is updated on a schedule: journaling is small and frequent, and refreshing is occasional.
- It never changes the meaning or the numbers of what the owner said, and never publishes anything without the owner's approval.

## 2. Goals and success measures

| ID | Goal | Measure (target) |
|---|---|---|
| G1 | Capture work while it's fresh | The owner journals at least once every 2 weeks, measured over 3 months |
| G2 | Low effort | Writing an entry takes under 2 minutes, with no formatting and no other step needed |
| G3 | Current outputs on demand | Proposals are ready within 5 minutes of `/refresh`; once the owner applies them, every output shows the new version immediately |
| G4 | Trustworthy wording | 0 altered or invented numbers, titles, dates or technologies in anything published or sent. Every bullet traces back to a fact. |
| G5 | Fast tailoring | A verified one-page tailored resume within 3 minutes of pasting a job posting |
| G6 | One source of truth | The master resume, tailored resumes, the portfolio page and the LinkedIn pack all come from the same master profile |
| G7 | Affordable | LLM cost around US$2/month for regular journaling plus about US$0.50 per tailored resume; hosting free (Vercel and Neon free plans) |

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
- Project facts from the owner's GitHub repositories, read with a read-only token (§7.16).
- A web app for review, journal, profile, resumes, tailoring, portfolio, LinkedIn and settings.
- Deployment for one invited user.

### Out of scope for v1
- Automatic editing of LinkedIn profiles. It isn't possible (see [C1](#11-constraints-and-assumptions)).
- Updating third-party job boards.
- Editing websites the app doesn't host, such as an existing GitHub Pages site.
- Writing to GitHub, reading code diffs, and reading repositories for the owner's roles. Work for a role stays journaled (§7.16).
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
| **Fact** | A neutral, structured record of something that happened, with these parts: a statement; metrics (subject, value, unit, qualifier); an ownership level (led / owned / contributed / assisted); tools; outcome; date; and its source: a journal entry, the imported resume, or the pull requests and commits of a linked repository. Facts are the ground truth for meaning and numbers. |
| **Brag bank** | All facts, whether or not any output uses them. |
| **Repository link** | A GitHub repository linked to one project in the profile. Its merged pull requests and commits become facts, as journal entries do, and each is read only once (§7.16). |
| **AI coding agent** | A tool, such as Claude Code, that commits or opens pull requests for the owner. Its work for the owner counts as theirs, since they thought of and directed it (FR-GH-4). |
| **Master profile** | The curated superset of everything worth using: basics, roles, education, projects, skills, summaries and bullets, with their metadata. Every change creates a new version. |
| **Bullet** | One line under a role or project, with: a stable ID; text; strength (high / medium / low, optionally per role type); verification status (yes / partial / no); swaps; open questions; notes; and a status (active / benched / planned). |
| **Swap** | A pre-approved alternative phrasing for a bullet or part of a bullet, tuned to a role type's or posting's vocabulary. It's the same fact in different words. |
| **Role type** | A kind of job the owner applies for, such as backend/full stack or ML/AI. The master profile groups presets by role type: coursework subsets, skills presets, summary variants and keep/cut notes. |
| **Variant** | A rule set that turns the master profile into one output. It covers selection, order, title choice, coursework subset, skills preset, summary, visibility, page limit and template. |
| **Change set** | A group of proposed operations on the master profile. Each operation cites the facts it came from and carries a verifier report. |
| **Verifier** | The check every generated sentence must pass before a person sees it: numbers, claims, style and resume rules. |
| **Refresh** | An on-demand run, started with `/refresh`. It proposes profile changes from the facts gathered since the last refresh, and once the owner has applied them, it updates every output from the new version. |
| **Publish** | Turning the portfolio page on. While it's on, it shows the version from the latest refresh. |
| **Application** | A tailored resume made for one job posting, logged with everything used to make it. |
| **Open question** | A missing detail that blocks a stronger bullet, such as the scale of a system or a root cause. The bot asks these over time. |

## 6. User journeys

- **J1 — Capture.**
  1. The owner messages the bot about something they did.
  2. The bot stores the message.
  3. When the entry closes, if impact, numbers or ownership are unclear, the bot may ask **one** follow-up question. The owner answers it or skips it.
  4. Then the bot asks which role the entry is about. The owner picks one of their roles, or names a new one.
  5. The app extracts facts, linked to that role, and the bot replies with a one-to-three-line summary of what it understood.
- **J2 — Refresh.** Only when the owner asks; nothing in this journey runs on a schedule or when an entry closes.
  1. The owner sends `/refresh`. The app proposes changes to the master profile from the facts gathered since the last refresh. The bot says how many there are, or that there's nothing new.
  2. The owner opens the Inbox. Each change shows a before/after diff, its sources and a verifier report.
  3. The owner accepts, edits or rejects each change.
  4. Applying the accepted changes creates a new version and updates every output from it: the master resume re-renders, the LinkedIn pack is regenerated, and the portfolio page moves to the new version if it's published.
- **J3 — Tailor.**
  1. The owner pastes a job posting.
  2. The app selects bullets and uses approved swaps where one fits. Where none fits, it offers a new verified phrasing.
  3. It picks a title variant, a coursework subset and skills lines, fits the resume to one page, and shows a keyword coverage report.
  4. The owner adjusts it and downloads the PDF. The application is logged.
- **J4 — Catch up.**
  1. On first use, the bot runs a short interview about what has happened since the master resume was last updated.
  2. After that, it works through open questions a little at a time.
- **J5 — LinkedIn.** After a refresh, the owner opens the LinkedIn page and sees only the sections that changed. They copy each one into LinkedIn and mark it done.
- **J6 — Maintain.** The owner edits the master profile directly. For example: fix wording, bench a bullet, add a swap or answer an open question. These edits create versions like any other change.
- **J7 — Link a repository.**
  1. The owner links a GitHub repository to a project in their profile, or to a new one they name, and chooses the month to read from.
  2. The app reads the merged pull requests and commits that count as the owner's work, including those an AI coding agent made for them, and saves facts that cite them.
  3. From then on, each `/refresh` first reads what's new in every linked repository, then proposes changes from all new facts (J2).

## 7. Functional requirements

Priority: **M** = Must (v1) · **S** = Should (v1 if time allows) · **C** = Could (later milestone).

### 7.1 Telegram capture (FR-CAP)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-CAP-1 | The bot receives updates through a webhook secured by Telegram's secret-token header. | M | Requests without the correct header get 401 and are not stored. |
| FR-CAP-2 | Only linked chats are accepted. | M | An unlinked chat gets a short "link your account in the web app" reply, and its message is not stored as a journal message. |
| FR-CAP-3 | Account linking uses a one-time deep link (`t.me/<bot>?start=<token>`) shown in the web app. | M | The token works once and expires after 15 minutes. Linking is confirmed in the chat and in Settings. |
| FR-CAP-4 | Messages are grouped into entries. An entry closes after 30 minutes without a message, or on `/done`. | M | The timeout can be configured. `/done` closes the entry immediately. An entry left open closes at the next message after the timeout, or at the daily tick (§13). |
| FR-CAP-5 | When an entry closes and its role is chosen (FR-CAP-11), the bot replies with a one-to-three-line summary of the facts it understood. | M | The summary is sent within 2 minutes of the owner choosing the role (p95): whatever closes the entry, `/done` or the tick, also runs the jobs that follow (§13), and so does choosing the role. |
| FR-CAP-6 | The bot asks at most one follow-up question per entry, when impact, metrics or ownership are unclear. | M | Never more than one question per entry. It's asked when the entry closes, before the role question (FR-CAP-11), and only while the owner is likely there to answer: within the entry timeout of the close, with no other entry open. A Skip button and `/skip` are offered. The answer, the owner's next message within the entry timeout, joins the same entry; after that, the question lapses. |
| FR-CAP-7 | Commands: `/start`, `/done`, `/skip`, `/refresh`, `/catchup`, `/pause`, `/resume`, `/help`. | M | `/help` lists every command, and so does Telegram's command menu when the owner types "/". |
| FR-CAP-8 | Chit-chat and messages unrelated to work are recognised and don't become facts. | S | A triage label is stored with each entry. |
| FR-CAP-9 | Job postings can be sent to the bot for tailoring. | C | M3 |
| FR-CAP-10 | Voice notes are transcribed into entries. | C | M3 |
| FR-CAP-11 | When an entry closes, the bot asks which role it's about: a button for each role in the profile, and one for a new role, which the owner then names. Every entry gets a role this way. | M | Nothing is extracted from an entry until the owner answers, and every fact from it links to the role chosen, never to one the model picks. A new role's name is stored encrypted, and the entry's facts carry it until a refresh adds the role to the profile. |

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
| FR-EXT-1 | Each closed entry becomes zero or more facts. Each fact has a statement, kind, metrics, ownership, tools, outcome and date, plus a link to the entry's role, as the owner chose it (FR-CAP-11), and to an existing project (or a proposed new one). | M | The facts match the schema. Entries unrelated to work produce no facts. |
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
| FR-REV-6 | Profile changes are proposed only on demand, when the owner sends `/refresh`. Nothing is proposed on a schedule or when an entry closes. | M | A refresh covers the facts added or edited since the last refresh. Until then, facts wait in the brag bank. |
| FR-REV-7 | Applying a refresh's accepted changes updates every output from the new version, in one step: the master resume re-renders, the LinkedIn pack is regenerated, and the portfolio page moves to the new version if it's published. Outputs are never updated on a schedule or when an entry closes. | M | Owner edits in the web app reach the outputs at the next refresh. A refresh with no new facts still brings every output up to date with the latest version. Tailored resumes already made are not changed. |

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
| FR-WRT-1 | **Resume/HR style.** Starts with an action verb in the past tense: a bullet describes work done, so this holds for the current role too. Follows "what + how + measurable result". No pronouns. At most 2 lines at the template's width. | M | The style check passes. |
| FR-WRT-2 | **LinkedIn style.** First person and slightly narrative, within LinkedIn's length limits. Text about the current role may use the present or the past tense; text about past roles uses the past tense. | M | |
| FR-WRT-3 | **Job-posting style.** Uses the posting's vocabulary where it is true, and leads with the most relevant facts. | M | |
| FR-WRT-4 | The owner's style notes (words to avoid, preferences) apply to every style. | M | |
| FR-WRT-5 | New phrasings the owner approves are saved as swaps on the bullet, so they can be reused. | M | |
| FR-WRT-6 | Everything the app writes uses the ASCII version of a character where one exists: dashes, quotes, ellipses, spaces and symbols such as the multiplication sign. This covers stored text, reports, rendered resumes and pages, and the web app. Rules in code make the replacement, for imported, generated and typed text alike, rather than instructions in LLM prompts. Letters and currency signs without an ASCII version stay. | M | The import's output is pure ASCII, and rendered date ranges use a hyphen. |

### 7.8 LaTeX resumes (FR-RES)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-RES-1 | Resumes render from the owner's LaTeX template (based on Jake's Resume, compiled with pdfLaTeX), converted into a Jinja template. The preamble and macros stay unchanged. | M | |
| FR-RES-2 | Every value is LaTeX-escaped automatically. | M | Tests cover `& % $ # _ { } ~ ^ \`. |
| FR-RES-3 | Compilation runs with shell-escape off, restricted file access, a timeout and an isolated temporary directory. | M | |
| FR-RES-4 | Each render reports its page count and checks that the text can be extracted. | M | |
| FR-RES-5 | The master resume re-renders as part of a refresh (FR-REV-7). There are no predefined one-page variants: every one-page resume is tailored to a posting (FR-TLR). | M | |
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
| FR-TLR-7 | The result fits on one page (FR-RES-7), and every new text in it passes the verifier. It is checked against the resume rules (§8): a broken rule is reported as R2 says, without stopping the PDF. | M | |
| FR-TLR-8 | Each tailored resume is logged as an application with company, role, date, posting text, the bullet IDs and swaps used, the title variant, the verifier report and the PDF. | M | |
| FR-TLR-9 | **ATS check.** Text extracted from the PDF contains the covered key terms, with the sections in the expected order. | M | |
| FR-TLR-10 | Each application's status can be updated: applied, interviewing, offer or rejected. | C | M3 |

### 7.10 Portfolio page (FR-PRT)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-PRT-1 | A public page at `/p/<handle>`, rendered on the server from the latest *published* version. | M | An unknown handle returns 404. |
| FR-PRT-2 | The built-in template has these sections: hero, about/education, experience, projects with category tabs, skills, contact and resume downloads. | M | |
| FR-PRT-3 | Drafts never show. While the page is published, it shows the version from the latest refresh (FR-REV-7). The owner can publish or unpublish it at any time, and publishing takes effect immediately. | M | |
| FR-PRT-4 | Each field has a visibility setting, and the phone number is hidden by default. Benched and planned items never show. | M | |
| FR-PRT-5 | An "open to work" toggle, and an option to hide the page from search engines. | M | |
| FR-PRT-6 | Open Graph tags and JSON-LD `Person` structured data. | M | |
| FR-PRT-7 | Visitors can download the resumes the owner chooses. | S | |
| FR-PRT-8 | Meets WCAG 2.1 AA and is responsive down to a 360 px width. | M | An automated accessibility check reports no serious issues. |
| FR-PRT-9 | Custom domains, more templates, and export to GitHub Pages or JSON Resume. | C | M4 |

### 7.11 LinkedIn pack (FR-LIN)

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-LIN-1 | Generate LinkedIn text from the master profile: the headline (≤ 220 characters), About (≤ 2,600) and each position description (≤ 2,000). The pack is regenerated as part of a refresh (FR-REV-7). | M | |
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
| FR-REM-6 | When a refresh's proposals are ready, the bot says so ("N updates proposed → Review") with a link, or says there's nothing new. | M | |
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

### 7.16 GitHub projects (FR-GH)

The owner's GitHub repositories are a second source of facts, for projects only. Repository history becomes facts the same way a journal entry does; from there, the refresh, the verifier and the review work as for any fact.

| ID | Requirement | Pri | Acceptance criteria |
|---|---|---|---|
| FR-GH-1 | The app reads GitHub with a fine-grained personal access token, set in the `GITHUB_REPOS_TOKEN` environment variable. The token is read-only and limited to the repositories the owner picks. The app never writes to GitHub. | S | Before anything is read, the app refuses a token that isn't fine-grained (`github_pat_...`), and one whose account isn't the owner's. The token never appears in the database, a log or an error message. |
| FR-GH-2 | The owner links a repository to one project: one in the profile, or a new one they name. Repositories are for personal and open-source projects; work for a role stays journaled. | S | A GitHub fact links to its project and never to a role. Synthesis puts it only under that project, or under the new project it names. Linking a benched project warns that its bullets never render (R8). Linking a repository again carries on where it stopped; unlinking stops reading it and keeps its facts. |
| FR-GH-3 | Linking imports the history from a month the owner chooses. In a repository the owner's account owns that isn't a fork, the app reads every pull request merged into the default branch and every commit on it, plus the description, the languages and the README. Elsewhere, it reads only the pull requests and commits that count as the owner's work (FR-GH-4), not the whole history. A pull request merged into another branch isn't read, as that work may never ship. | S | For a project already in the profile, the history starts by default at the month the profile was loaded, so what the imported resume already says isn't proposed again. Before importing, the app says how many pull requests and commits it found and how many model calls they take, and asks to go on. `wj github digest` shows exactly what the model would read, without calling it. |
| FR-GH-4 | **Whose work counts.** Two kinds of work count as the owner's: their own, and the work an AI coding agent did for them, such as Claude Code or GitHub Copilot's coding agent. The owner thought of and directed the agent's work, and it shows AI-assisted development. An agent's work counts in a repository the owner's account owns. Elsewhere, it counts when it's part of the owner's pull request, or names the owner as co-author or assignee. Other people's work doesn't count, and nor do automation bots such as Dependabot, Renovate and github-actions. | S | Agents are recognised from a list in code, and an agent the list doesn't know counts as another person. Code labels an agent's item with the agent's name, and adds the agent to the tools of the facts that cite it. An AI co-author doesn't lower the ownership cap (FR-GH-6). A new skill named only by agent-made facts carries a note in the Inbox: accept it only if you can defend it (R3). The agent itself is exempt. A fixture repository whose commits are all by Claude Code gives the owner's facts, owned, with Claude Code as a tool. A Dependabot pull request gives no fact, and nor does another person's commit. |
| FR-GH-5 | Some items are left out: merges, reverts and the commits they revert (and a pull request whose squash or merge commit was reverted), empty messages, and squash commits that repeat a pull request. Before the model sees the text, code removes several things: commit hashes, issue and pull-request numbers, addresses, emails, @mentions, trailers such as `Co-authored-by`, checklists, and the README's roadmap, to-do and acknowledgement sections. | S | No line, commit or star count reaches the model, and no number the code removed can back a metric. Code writes who made each item in words, without digits. |
| FR-GH-6 | The history becomes facts (standard tier), a few dozen items at a time. Each fact cites the pull requests and commits it rests on. Every number in a fact must be in the cited items' own text (FR-EXT-2), and every tool must be named there. Code sets the project and the date (the month of the latest cited item). Code also sets the ownership cap: owned in a repository the owner's account owns that isn't a fork; contributed elsewhere, or when another person co-authored a cited item. The owner can change the cap when linking, but never to led. | S | A fact that cites no item, or an item it wasn't given, is dropped. Known-bad cases are caught: a number found only in an uncited item, a tool named nowhere, and "led". |
| FR-GH-7 | Each item is read once. The app keeps the keys of the items it has read: commit hashes and pull-request numbers, never their text. Each read looks back 14 days before the last one, so commits pushed late are still found. | S | Two reads at once never save the same item twice, for example a refresh on the server and `wj github sync`. A second read with nothing new makes no model call. A window the model can't read is recorded and skipped. When GitHub can't be reached, that repository waits for the next read. |
| FR-GH-8 | Each refresh (FR-REV-6) first reads what's new in every linked repository, then proposes changes from all new facts, journal ones included. On the server, it reads a limited number of windows, so the refresh still fits the function's time limit. `wj propose --db` and `wj github sync` read everything. | S | A repository that can't be read never stops the refresh. The bot says what it read, what's left and what failed (FR-REM-6). With GitHub unreachable, a refresh still proposes from the journal's facts. |
| FR-GH-9 | Repository text is handled like journal text (NFR-PRIV-2): it goes only to the Claude API, and is never logged or stored. The app keeps only the facts (encrypted), the links to their items, and the read keys. Repository names are stored encrypted and kept out of logs, and job arguments carry IDs only. | S | A whole read, logged at debug level, shows no repository name, path, text or token. |
| FR-GH-10 | GitHub facts show on the Journal page, by repository and month, with links to their pull requests and commits. They can be edited and deleted like other facts (FR-JRN-6), and the export includes them (FR-JRN-7). The Inbox shows each GitHub source fact with its repository and links (FR-REV-1). | S | When a refresh creates a project from a public repository's facts, the project gets the repository's address as its URL. A private repository's name and address never reach the profile, a resume or the portfolio page. |

## 8. Resume rules

These rules come from the owner's own master-resume practice. Code enforces them for every **sendable** resume, which means every tailored resume. The full master document is exempt from R6, because it spans several pages by design.

| ID | Rule | How it's enforced |
|---|---|---|
| R1 | Never invent a metric, date, title or technology. | The verifier (§7.6). Titles come only from approved variants, and technologies only from the skills catalogue, never the gaps list. |
| R2 | Never ship a placeholder. | Placeholder text, such as TODO, in a resume is an error. A tailored resume that breaks a rule is still written, so the work that went into it isn't lost, but its errors are printed last and kept with its application, and it mustn't be sent until they're fixed. Missing optional fields, such as an undated project, produce a warning. |
| R3 | Never claim a skill that can't survive a whiteboard question. | A skill flagged *verify-before-shipping* needs explicit confirmation for each resume; a resume that names it without one is an error, reported as R2 says. Tailoring never lists a skill on the gaps list, and one named in text is an error. |
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
| NFR-PRIV-2 | Journal text, and the repository text read for project facts (§7.16), are sent only to the Claude API, and are never logged. |
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
| Standard | Claude Sonnet | Fact extraction, from journal notes and repository history; the catch-up interview; writing a single bullet or applying a swap; the claim verifier; the LinkedIn pack |
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
| C6 | Hosting is free: Vercel's Hobby plan (for personal, non-commercial use) runs the app, and Neon's free plan holds Postgres. Neither runs an always-on process, so Vercel's cron calls the app's tick once a day, the most its free plan allows (§13). That's enough: the owner journals at the end of the day, `/done` runs the jobs that follow at once, and the database, whose free plan counts the hours it's awake, wakes only when it's used. Vercel can't run LaTeX, so resumes render on the owner's machine for now: a refresh's master resume when `wj tick` or `wj worker` runs there, and any version with `wj render --db`. |
| C7 | GitHub's REST API has limits: a token may make 5,000 requests an hour, and its search allows 30 requests a minute and returns at most 1,000 results a query. A commit is recognised as the owner's only when GitHub links its email to their account. It's recognised as an AI agent's only when the app knows the agent (FR-GH-4); an agent the app doesn't know counts as another person until it's added. Only the default branch is read. A commit pushed more than 14 days after it was made, outside a pull request, may be missed. Fine-grained tokens expire, so the owner renews theirs. |
| A1 | The owner provides `master-resume.tex` (received). The JSON twin isn't needed (OQ-1). Past applications aren't imported; the app's application log starts with the first tailored resume. |
| A2 | The owner refreshes only when they need current outputs, such as before a job hunt, so refreshes may be months apart. |

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| The owner stops journaling | The product loses its value | A low-friction bot, at most one follow-up question, reminders with snooze, instant summaries, open questions as prompts, and a monthly recap (M3) |
| The LLM inflates or invents claims | Harm to the owner's reputation | The verifier (numbers, claims, honesty boundaries, gaps list), human review, and no automatic publishing |
| Confidential details leak into public outputs | Employment or legal risk | A confidential-terms blocklist (M2), sensitive-role rules (R5) and an LLM detector (M3) |
| A data breach | Personal data exposed | Column encryption, a 7-day limit on raw updates, secrets management, and an allowlist for login |
| Errors in the template or LaTeX break rendering | No resumes | Escaping, a smoke render in CI, and page and text checks |
| A refresh covers months of facts at once | A long review | Bulk accept in the Inbox, and the verifier on every proposed sentence so the owner can review quickly |
| LLM costs creep up | Budget overrun | Model routing, prompt caching, the cost log and a monthly view |
| The Telegram API changes | Capture breaks | A thin client for the few Bot API methods used, and tests with recorded payloads |
| A free plan changes, or ticks stop | Jobs run late, or the app has to move | Jobs wait in Postgres and run on the next tick. The app runs anywhere Python and Postgres do, with `wj worker` in place of the tick. |
| Other people's work is credited to the owner | Inflated claims | Only the owner's own work, and AI agents' work for them, is read (FR-GH-4). Code caps ownership, and every fact cites its pull requests and commits |
| Code an AI agent wrote is claimed as a skill the owner can't defend | A failed interview question (R3) | A new skill named only by agent-made facts carries a note in the Inbox, and the owner decides |
| A private repository is revealed | Privacy | Only a public repository's address reaches the profile. Repository names are encrypted and never logged (FR-GH-9, FR-GH-10) |
| Weak bullets from repository history, or secrets in commit messages | A weaker resume, or a leak | No line, commit or star counts reach the model, and routine work is left out. Only personal and open-source repositories are read, and every change is reviewed before anything is published |

## 13. Architecture overview

```
Telegram ──webhook──▶ ┌─────────────────────────────────┐ ──▶ PostgreSQL (Neon):
                      │ FastAPI app (a Vercel function) │     data and the job queue
Vercel cron ──tick──▶ │ API, webhook, tick, portfolio   │ ──▶ Claude API
  (once a day)        └─────────────────────────────────┘ ──▶ GitHub API (read-only token)
React web app: built into the function, which serves it at /
Owner's machine: the `wj` commands, including `wj render --db` (LaTeX) and `wj github` (GitHub)
```

| Component | Responsibility |
|---|---|
| FastAPI app (a Vercel function) | API, the Telegram webhook, the tick, public portfolio pages and the React web app's built files |
| Tick (`/internal/tick`) | Called once a day by Vercel's cron, with a secret (`CRON_SECRET`). It defers the scheduled tasks that are due, then runs queued jobs (extraction, synthesis, reminders and purges) for about 20 s; a job that has started may finish within the function's 300 s. A request that queues jobs, such as `/done`, runs them itself right after answering, so replies don't wait for a tick. Locally, `wj worker` does both without stopping. |
| PostgreSQL (Neon) | All data and the job queue. Profile versions are stored as immutable JSONB snapshots, and PDFs as bytes keyed by content hash. |
| Claude API | Extraction, writing, verification, synthesis and tailoring, routed by tier (§10) |
| TeX Live | Resume rendering on the owner's machine (a refresh's master resume through `wj tick` or `wj worker`, and `wj render --db`), until rendering moves to a host that can run it |
| GitHub REST API | The owner's repository history, for project facts (§7.16). It's read with a read-only token, by `wj github` and at the start of each refresh |

### Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy 2 with Alembic; uv, ruff, pyright, pytest |
| Jobs | Procrastinate (a Postgres-backed queue with scheduled tasks), run by the tick in production and by `wj worker` locally |
| LLM | Anthropic Python SDK, with structured outputs, prompt caching and the Batches API |
| Telegram | The Bot API over HTTPS with `httpx2`, through a thin client for the few methods used |
| Resume | Jinja2 with LaTeX-safe delimiters, latexmk/pdfLaTeX, pypdf |
| Portfolio page | Jinja2 rendered on the server, with Tailwind built by the standalone CLI |
| Web app | React, Vite, TypeScript, Tailwind, TanStack Query, React Router; API types generated with `openapi-typescript` |
| Auth | GitHub OAuth with an allowlist |
| Hosting | Vercel Hobby (one FastAPI function, which also serves the web app, and its cron for the daily tick) and Neon's free Postgres; Sentry for errors |

### Main tables

| Area | Tables |
|---|---|
| Accounts | `users`, `settings`, `telegram_links` |
| Journal | `telegram_updates`, `journal_messages`, `journal_entries`, `facts`, `repo_links`, `repo_items` |
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
| OQ-3 | What should the portfolio handle (`/p/<handle>`) and the app's domain be? | T-046, T-051 | **Partly answered 2026-10-04:** the app runs at Vercel's free `<project>.vercel.app` address until the owner picks a domain. The handle is still open. |
| OQ-4 | Is the default reminder time right: Friday 18:00, Asia/Singapore? | T-035 | Open |
| OQ-5 | Which application-log fields matter to you, beyond what FR-TLR-8 lists (e.g. contacts, outcome)? | T-021 | Open |
| OQ-6 | Should the named resume variants be the role types from your master resume (backend/full stack, ML/AI, identity/security, systems)? | T-008 | **Answered on #8:** no. Only the master document is predefined. Every one-page resume is tailored to a posting, from the points that suit it best (T-020). |
