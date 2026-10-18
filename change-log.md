# Change log

One short entry per pull request, newest first: what changed and what to verify. Verification is ticked in the pull request, not here.

## 2026-10-18 · T-035 Reminders · [#52](https://github.com/matchastack/work-journal/pull/52)
**Changed:** The bot sends a gentle reminder on your schedule (Fridays at 18:00, Asia/Singapore, by default), skipped if you journaled in the last 3 days, with "Nothing this week" and "Remind me tomorrow" buttons. `/pause` and `/resume` stop and restart it, and Vercel's daily tick now runs at 18:00 Singapore time to send it.

**Verify:**
- CI is green on the PR
- After the deploy, run `scripts/post-deploy.sh`; typing "/" in the chat lists /pause and /resume, and Vercel shows the tick's cron at 10:00 UTC
- `/pause` pauses reminders, and `/resume` answers "Reminders are back on: Fridays at 18:00 (Asia/Singapore)."
- On a Friday after 3 days without journaling, the reminder arrives between 18:00 and 19:00; "Remind me tomorrow" brings it back on Saturday evening, and "Nothing this week" is noted

## 2026-10-18 · Tailored resumes' LaTeX source · [#56](https://github.com/matchastack/work-journal/pull/56)
**Changed:** `wj tailor` writes the LaTeX source beside the PDF, as `wj render` does; `--tex-only` or `--pdf-only` writes just one of them.

**Verify:**
- CI is green on #56
- `uv run wj tailor <posting> --db` says it wrote the PDF and its source, and the `.tex` compiles on its own to the same resume
- With `--tex-only`, only the `.tex` is written; with `--pdf-only`, only the PDF

## 2026-10-18 · T-065 Tailored resumes are written even when a rule fails · [#55](https://github.com/matchastack/work-journal/pull/55)
**Changed:** `wj tailor` no longer stops before the render when the resume breaks a rule, such as a skill marked verify-before-shipping that isn't confirmed. It writes the PDF and logs the application, then prints the rule errors last and exits with status 1: fix them before you send it.

**Verify:**
- CI is green on the PR
- `uv run wj tailor <posting.txt> --db` on the posting that stopped before writes the PDF, then ends with "... breaks the resume rules below; fix them before you send it." and the Claude Code error
- Running it again with `--confirm "Claude Code"` (only if you can defend it) ends without the error

## 2026-10-17 · User guide · [#54](https://github.com/matchastack/work-journal/pull/54)
**Changed:** added `user-guide.md`, which explains how to use the Telegram bot, the web app and the command-line tool, and marks the pages still to come. `CLAUDE.md`, the PR template and `tasks.md` now require a PR that creates or changes a feature to update it.

**Verify:**
- The guide's commands and options match `uv run wj <command> --help`
- The Inbox and Journal sections match the running web app
- The "Keep the user guide current" rule in `CLAUDE.md` reads clearly
- No personal data in the diff

## 2026-10-16 · T-022 ATS check · [#50](https://github.com/matchastack/work-journal/pull/50)
**Changed:** `wj tailor` now reads the PDF's text back the way an applicant tracking system does, and says whether it has every covered term and the section headings in order, or what it lacks (a term split by a hyphen at a line end, a missing heading, ligatures or garbled characters). The result is kept with the application.

**Verify:**
- CI is green on the PR
- `uv run wj tailor <posting.txt> --db` ends with "ATS check passed: ..." for your resume
- If it reports a problem, the PDF opened in a viewer shows the same thing, such as a covered term broken across two lines

## 2026-10-15 · T-021 Tailoring: coverage report and application log · [#49](https://github.com/matchastack/work-journal/pull/49)
**Changed:** `wj tailor` now prints which of the posting's skills and key terms the resume shows, and where, and lists the rest as gaps, which are never added. It logs each resume as an application, with the posting, the PDF and the checks, and `wj applications` lists the log.

**Verify:**
- CI is green on the PR
- `uv run wj tailor <posting.txt> --db` ends with the coverage report and "Logged it as application ..."; a term that's in the PDF is listed as covered, and one that isn't is a gap
- `uv run wj applications --db` lists that application first, with the title it gave your current role
- After the next tailored resume, `uv run wj linkedin --db` compares your current role's title with that application's

## 2026-10-15 · The master resume's LaTeX source · [#48](https://github.com/matchastack/work-journal/pull/48)
**Changed:** `wj render` also writes the LaTeX source the PDF was compiled from, after any cuts, beside the PDF (`local/resumes/master.tex` by default).

**Verify:**
- CI is green on #48
- `uv run wj render --db` says it wrote `master.pdf` and its source, `master.tex`, and the `.tex` compiles on its own to the same resume

## 2026-10-14 · T-020 Tailoring: selection and assembly · [#47](https://github.com/matchastack/work-journal/pull/47)
**Changed:** `wj tailor <posting>` writes a one-page resume tailored to a job posting. It chooses the roles, bullets, titles, courses and skills from your profile, works in your approved swaps, offers new phrasings in the posting's words for you to approve (approved ones are saved as swaps), checks the resume rules, and fits the result to one page.

**Verify:**
- CI is green on the PR
- With your key, `uv run pytest -m llm tests/test_tailoring_tailor_live.py` passes (4 cases)
- `uv run wj tailor <posting.txt> --db` writes a one-page PDF to `local/resumes/` that reads as tailored to the posting, with nothing your profile doesn't claim
- A skill marked verify-before-shipping is left out of the skills unless you pass `--confirm <skill>`
- Answering no to a new phrasing keeps the bullet's own words, and nothing is saved

## 2026-10-14 · T-060 LinkedIn and job-posting writers, and swaps · [#46](https://github.com/matchastack/work-journal/pull/46)
**Changed:** `wj write --style linkedin` writes facts as first-person LinkedIn text, and `--style posting --posting <file>` writes a resume bullet in a posting's words, using a term only where your facts back it. `wj write --edit <bullet> --swap <n>` works one of the bullet's swaps into it, and `--save-swap` saves a phrasing you approve as a swap on the bullet. Everything passes the verifier, and with `--db` your words to avoid apply.

**Verify:**
- CI is green on #46
- With your key, `uv run pytest -m llm tests/test_writing_live.py` passes (13 cases), and `uv run wj eval all` still meets the bar
- `wj write --db --style linkedin --edit <bullet>` reads as you'd write it on LinkedIn, and says no more than the bullet
- With a posting from `wj posting parse`, `--style posting` uses the posting's words only where they're true
- `--save-swap` saves the next profile version only when you answer yes

## 2026-10-14 · T-024 LinkedIn pack generator · [#44](https://github.com/matchastack/work-journal/pull/44)
**Changed:** `wj linkedin` writes your LinkedIn headline, About and position descriptions from your profile's public content, in the first person and within LinkedIn's limits, and prints only the sections that changed since you last ran `wj linkedin done`. Each section passes the verifier; positions use each role's first approved title, and a current title that differs from your latest application's is flagged.

**Verify:**
- With your key: `uv run pytest -m llm tests/test_linkedin_live.py` passes
- `uv run wj linkedin --db` prints all four kinds of section, each reading as you'd put it on LinkedIn, with no claim your profile doesn't make
- After `uv run wj linkedin done --db`, running `wj linkedin --db` again says nothing changed, without calling Claude

## 2026-10-14 · Milestone and task statuses · [#45](https://github.com/matchastack/work-journal/pull/45)
**Changed:** `tasks.md` marks M0 done, since T-000, T-055 and T-056 are merged, and marks the merged T-038, T-040, T-041, T-062 and T-063 done, with T-038 linked to #43.

**Verify:**
- The overview table shows M0 as done, and M1 and M2 still in progress

## 2026-10-13 · T-038 Refresh: proposals on demand and the master resume · [#43](https://github.com/matchastack/work-journal/pull/43)
**Changed:** `/refresh` proposes changes to your profile from the facts added or edited since your last refresh, and the bot says how many wait in the Inbox, with a link, or that there's nothing new. Once you've decided on them, or at once when there's nothing new, your master resume is due to be rendered from the newest version; Vercel can't run LaTeX, so `wj tick` on your computer renders it. `wj propose --db` is a refresh too.

**Verify:**
- CI is green on the PR
- Before the deploy: Vercel's Settings, Functions shows Fluid compute on (the app may now run for 300 s)
- After the deploy, `scripts/post-deploy.sh`, so the command menu shows `/refresh`
- With a few journal entries since your last proposal, `/refresh` gets "Looking for changes", then within a few minutes "N updates proposed" with a link that opens the Inbox
- Sending `/refresh` again before you apply them says they still wait; after you apply them, `/refresh` says there's nothing new
- After you apply them, `uv run wj tick` on your computer (TeX Live and the Claude settings in `backend/.env`) logs that the master resume shows the new version

## 2026-10-13 · T-063 Post-deploy script · [#42](https://github.com/matchastack/work-journal/pull/42)
**Changed:** added `backend/scripts/post-deploy.sh`, to run after each deploy. It waits for the app, migrates the database and runs `wj telegram set-webhook`; every step is safe to repeat. It stops first if `backend/.env` points at this computer, and migrations that start at the same time, such as the script's and Vercel's build, now take turns instead of failing.

**Verify:**
- After the next deploy, `scripts/post-deploy.sh` from `backend/` ends with "Done", and the bot still answers
- Run from an out-of-date checkout, it stops and asks you to `git pull`
- With `DATABASE_URL` pointing at localhost, it lists the problem and stops before migrating

## 2026-10-12 · T-040 Inbox · [#36](https://github.com/matchastack/work-journal/pull/36)
**Changed:** the web app's Inbox lists the changes proposed to your profile. Each one shows a word diff, the facts it came from, the outputs it changes and the verifier's report. You accept, edit (checked again) or reject each one, and Apply turns the accepted ones into a new profile version; reasons you give for rejecting become style notes. Nothing proposes changes until the refresh (T-038), so the Inbox stays empty until then.

**Verify:**
- CI is green on #36
- With a change set in your local database (the PR has a script), accepting a change moves focus to the next one, and Apply makes a new profile version
- "Accept all that passed" leaves the changes the verifier flagged waiting, and Apply stays off until every change is decided
- Editing a bullet's text, with the LLM settings, shows the verifier's new report on the change
- Rejecting a new role also rejects the bullets under it, and they can't be accepted until you undo the role's rejection

## 2026-10-12 · T-041 Journal page · [#35](https://github.com/matchastack/work-journal/pull/35)
**Changed:** the web app's Journal page shows your journal as one document by month, with the facts saved from each entry. You can edit or delete messages, entries and facts there (deleting something your profile cites names the bullets and asks first), and download it all as Markdown or JSON.

**Verify:**
- CI is green on #35
- The link in the bot's reply to an entry opens the Journal page at that entry
- Editing a message keeps its earlier text under "Edited once"; editing a fact shows the change at once
- Download Markdown writes the times in your time zone; Download JSON holds every entry and fact
## 2026-10-13 · T-062 Migrations follow the production database · [#41](https://github.com/matchastack/work-journal/pull/41)
**Changed:** the deploy of `main` failed because production's database is at migration 0010 (from #35, not yet merged) and `main` didn't have it. `main` now carries that migration and its `facts.updated_at` column, and the follow-up question's migration comes after it.

**Verify:**
- CI is green on #41
- After the merge, the production deployment's build log shows `Running upgrade 0010 -> 0011` and the deployment goes live
- #35 and #36 merge `main` without a conflict in their 0010 migration

## 2026-10-13 · T-034 Follow-up question · [#39](https://github.com/matchastack/work-journal/pull/39)
**Changed:** when an entry closes and the note leaves out a number, its impact or your part in it, the bot asks one short question about it, with a Skip button, before asking the entry's role. Your next message joins the entry, so its facts can use the answer. `/skip` skips the question too.

**Verify:**
- CI is green on #39
- After the deploy, run `uv run wj telegram set-webhook` once, so the command menu shows `/skip`
- With your bot: a note without numbers, such as "sped up the search page by caching the filters", then `/done`, gets one question with a Skip button; your answer is followed by the role question, and the facts reply uses the answer
- Skip, or `/skip`, goes straight on to the role question, and a note that already gives its numbers gets no question
- With your key, `uv run pytest -m llm tests/test_follow_up_live.py` passes (5 cases)

## 2026-10-13 · T-016 Evaluation harness · [#38](https://github.com/matchastack/work-journal/pull/38)
**Changed:** added `wj eval`, which runs fictional journal notes, facts and known-bad texts through fact extraction, the resume writer and the verifier with the real models. It reports altered numbers, inflated claims, fact recall and cost against the bar for changing a prompt or the model routing. Your own cases go in `local/evals/` and run with `--private`.

**Verify:**
- CI is green on #38
- With your key, `uv run wj eval all --out evals/baseline.md` runs the 33 cases, prints the report, and writes the baseline to commit with this PR
- The report quotes no note, fact or bullet: only case names, counts and the verifier's codes
- `backend/evals/README.md` is clear enough to add a case of your own to `local/evals/`

## 2026-10-12 · T-018 Profile synthesis · [#37](https://github.com/matchastack/work-journal/pull/37)
**Changed:** added `wj propose` and `wj apply`. A heavy-tier prompt plans what your new journal facts change in the profile (new roles, bullets, new versions of bullets, skills, answered questions), the resume writer writes and verifies each bullet, and code drops anything not backed by your facts or words. You review each change, and with `--db` the accepted ones become the profile's next version, ready for `wj render --db`.

**Verify:**
- CI is green on #37
- With your key, `uv run pytest -m llm tests/test_synthesis_live.py` passes (4 cases)
- With your key, after a few journal entries: `uv run wj propose --db` shows changes you'd make, each with its facts; `uv run wj apply --db` saves the ones you accept as a new version; `uv run wj render --db` shows them in the master resume

## 2026-10-12 · T-017 Resume writer · [#34](https://github.com/matchastack/work-journal/pull/34)
**Changed:** added the resume writer and `wj write`: a standard-tier prompt writes facts as a resume bullet, new or a new version of an existing one, and the verifier checks it; one that fails is written once more, then flagged. The backlog now puts the master resume first: LinkedIn and job-posting writing moved to T-060, and the refresh's LinkedIn and portfolio updates to T-061.

**Verify:**
- CI is green on #34
- With your key, `uv run pytest -m llm tests/test_writing_live.py` passes (6 cases)
- With your key and a loaded profile, `uv run wj write --db --edit <one of your bullet IDs> --facts fact_<that bullet ID>` prints a version of that bullet you'd put on your resume, and says it passed the checks
- The re-planned T-017, T-038, T-060 and T-061 in `tasks.md` match the order you want

## 2026-10-11 · T-059 Choose the role of each entry · [#33](https://github.com/matchastack/work-journal/pull/33)
**Changed:** when an entry closes, the bot asks which role it's about, with a button for each role in your profile and New role. Facts are extracted only after you answer, and every one links to the role you chose; a new role's name is kept (encrypted) for a refresh to add.

**Verify:**
- CI is green on #33
- After the deploy, run `uv run wj telegram set-webhook` once, so Telegram sends the webhook button presses
- With your bot: a note, then `/done`, gets the question as a reply to the note; tapping a role edits the question to show it, and the facts reply follows
- New role, then a message such as "Data Engineer, Example Co", gives the entry that role, and that message isn't journaled
- Entries closed but not yet processed when this deploys are asked about too

## 2026-10-08 · T-058 Ticks run scheduled tasks only as of their own time · [#32](https://github.com/matchastack/work-journal/pull/32)
**Changed:** the worker inside a tick no longer defers scheduled tasks by the real clock, which ran a daily task an extra time and turned `main`'s CI red when it ran a few minutes after the task's time.

**Verify:**
- CI is green on #32, and on `main` after the merge

## 2026-10-07 · T-057 Telegram command menu · [#31](https://github.com/matchastack/work-journal/pull/31)
**Changed:** typing "/" in the bot's chat shows its commands. `wj telegram set-webhook` and `wj telegram poll` set the menu from the same list `/help` prints.

**Verify:**
- CI is green on #31
- Run `uv run wj telegram set-webhook` once against the deployed app; in Telegram, typing "/" to the bot lists `/done` and `/help` (reopen the chat if the menu doesn't refresh)

## 2026-10-05 · T-033 Extraction on entry close, and summary reply · [#30](https://github.com/matchastack/work-journal/pull/30)
**Changed:** when an entry closes, the bot triages it (light tier), saves the facts in an entry about work, and replies with the saved facts, up to three, and a link to the entry. It runs right after `/done`, or, for an entry left without `/done`, in the daily tick that closes it. Failures are retried at the next tick, and you're told when one finally fails.

**Verify:**
- CI is green on #30
- With your key, `uv run pytest -m llm tests/test_triage_live.py` passes (6 cases)
- With your bot: a note about your work, then `/done`, gets a reply listing the facts it saved

## 2026-10-04 · T-032 Entry grouping · [#29](https://github.com/matchastack/work-journal/pull/29)
**Changed:** journal messages are grouped into entries. An entry closes after 30 minutes of quiet (`ENTRY_TIMEOUT_MINUTES`), or on `/done`; a scheduled task closes quiet ones, and `/help` lists the commands. Messages journaled earlier are grouped by the same rule.

**Verify:**
- CI is green on #29
- Optional, with your bot: `/done` and `/help` answer in Telegram, and `journal_entries` shows how each entry closed

## 2026-10-04 · T-051 Vercel deployment · [#28](https://github.com/matchastack/work-journal/pull/28)
**Changed:** the app deploys to Vercel's free plan, with Postgres on Neon's: a protected tick, called once a day by Vercel's cron, runs scheduled tasks and queued jobs, and production builds build the web app and migrate the database. `docs/deploy.md` is the runbook.

**Verify:**
- CI is green on #28
- Following `docs/deploy.md`, `/healthz` on the deployed app answers `"database":"ok"`, and a message to the bot reaches `journal_messages`

## 2026-10-04 · T-031 Telegram account linking · [#27](https://github.com/matchastack/work-journal/pull/27)
**Changed:** a one-time link to the bot (15 minutes) links the Telegram chat it's opened in to your journal; linking a new chat moves the link. The web app's API can make the link and show the link status, and `wj telegram link` prints one.

**Verify:**
- CI is green on #27
- Optional, with your bot: `uv run wj telegram link --user <your GitHub username>`, open the link in Telegram, and the bot answers "Linked."

## 2026-10-04 · T-030 Telegram webhook and message storage · [#26](https://github.com/matchastack/work-journal/pull/26)
**Changed:** added the Telegram webhook, which checks Telegram's secret header, keeps every raw update for 7 days and turns messages from a linked chat into encrypted journal messages (edits keep the earlier text). Also `wj telegram poll` to journal on your computer, and `wj telegram set-webhook`.

**Verify:**
- CI is green on #26
- Optional, with a bot from @BotFather: `uv run wj telegram poll`, then message the bot; it answers with the linking hint (linking comes in T-031)

## 2026-10-04 · T-056 Free hosting on Vercel and Neon · [#25](https://github.com/matchastack/work-journal/pull/25)
**Changed:** the requirements and tasks now host the app on Vercel's and Neon's free plans. Vercel's cron calls a protected tick once a day in place of an always-on worker, and resumes render on your machine for now.

**Verify:**
- C6 and §13 in `project-requirements.md` describe the hosting you want
- A summary within 2 minutes of an entry closing suits you

## 2026-10-01 · T-015 Claim verifier · [#23](https://github.com/matchastack/work-journal/pull/23)
**Changed:** added the verifier every generated sentence must pass. It runs the number check, a standard-tier claim check, the style check and the resume rules. The claim check catches inflated ownership, an added tool, team size, outcome or scope, a broken honesty boundary or gaps-list skill, and a posting term that isn't a synonym. Text that fails is written once more with the findings as feedback, then flagged. Its report holds no fact text.

**Verify:**
- CI is green on #23
- With your key, `uv run pytest -m llm tests/test_verifier_live.py` passes (14 tests)
- The issues the claim check names, listed in the PR, are what you'd want flagged

## 2026-09-30 · T-014 Fact extraction · [#21](https://github.com/matchastack/work-journal/pull/21)
**Changed:** added fact extraction and `wj extract`: a standard-tier prompt turns an informal note into structured facts, and code drops any fact with a number the note doesn't give and removes tools the note doesn't name.

**Verify:**
- CI is green on #21
- With your key, `uv run pytest -m llm tests/test_extraction_live.py` passes
- `wj extract` on a note of your own gives facts that say only what the note says

## 2026-09-28 · T-019 Job-posting parser · [#12](https://github.com/matchastack/work-journal/pull/12)
**Changed:** added the job-posting parser: a light-tier prompt breaks a pasted posting into title, company, seniority, skills, responsibilities and key terms. Code then checks every skill and term against the posting, so each keeps the posting's exact spelling. Adds `wj posting parse`.

**Verify:**
- CI is green on #12
- The prompt's fields and rules suit tailoring
- Optional, with a key: `uv run wj posting parse` on a real posting

## 2026-09-28 · T-012 LLM client · [#9](https://github.com/matchastack/work-journal/pull/9)
**Changed:** added the client every Claude call goes through: routing each task to a tier's model from the environment, versioned prompt files, prompt caching, validated structured output with one retry, refusal and error handling, and a call log with tokens and cost but no text, saved to the `llm_calls` table (or to a JSONL file from the command line). Adds the `anthropic` SDK.

**Verify:**
- CI is green on #9
- The task tiers in `backend/app/llm/routing.py` are the ones you want
- Optional, with a key: `uv run pytest -m llm` passes

## 2026-10-02 · T-055 Refresh on demand · [#24](https://github.com/matchastack/work-journal/pull/24)
**Changed:** the requirements and tasks now say that the profile, the master resume, the portfolio page and the LinkedIn pack change only when you send `/refresh` and apply the reviewed changes. Weekly synthesis is gone; journal entries are still captured and summarised as they arrive.

**Verify:**
- J2 and FR-REV-6 / FR-REV-7 in `project-requirements.md` match the flow you want
- A published portfolio page following each applied refresh, without a separate publish step, suits you

## 2026-10-01 · T-027 Persistence for engine data · [#22](https://github.com/matchastack/work-journal/pull/22)
**Changed:** added database tables and repositories for profile versions (immutable), change sets, encrypted facts, variants, postings, applications, PDFs and LLM calls; `wj db load-profile` to load your imported resume as version 1; and `--db` on `wj lint`, `wj render`, `wj portfolio build` and `wj import tex`. Your first sign-in claims the user that `load-profile` created.

**Verify:**
- CI is green on #22
- With local Postgres and `DATA_ENCRYPTION_KEY` set: after `uv run alembic upgrade head`, `uv run wj db load-profile --user <your GitHub username>` loads your import, and `uv run wj render --db` gives the same PDF as `uv run wj render`

## 2026-09-28 · T-039 Web app scaffold · [#18](https://github.com/matchastack/work-journal/pull/18)
**Changed:** added the React web app in `frontend/` (Vite, strict TypeScript, Tailwind, React Router, TanStack Query). It has an API client typed from the OpenAPI schema (`wj openapi`), a sign-in page, and a signed-in layout with navigation to every page. FastAPI serves the build, and CI runs ESLint, tsc, Vitest and the build.

**Verify:**
- CI is green on #18, including the new **Frontend checks** job
- After `npm run build` in `frontend/`, http://localhost:8000/ shows the sign-in page, and signing in opens the inbox
- Keyboard only: Tab reaches "Skip to content", then every navigation link

## 2026-09-28 · T-029 GitHub sign-in with an allowlist · [#17](https://github.com/matchastack/work-journal/pull/17)
**Changed:** added sign-in with GitHub (`state` and PKCE), limited to `ALLOWED_GITHUB_LOGINS`. It uses server-side sessions in HTTP-only, Secure, SameSite=Lax cookies, a CSRF token that every change must send back, and `/auth/me` and `/auth/logout`. Migration `0003` adds GitHub identities and a `sessions` table. `httpx2` becomes a runtime dependency.

**Verify:**
- CI is green on #17
- With a GitHub OAuth app whose callback is `http://localhost:8000/auth/callback`, signing in at `/auth/login` works for your username, and `/auth/me` shows it
- A username that isn't on the allowlist sees the "This journal is private" page

## 2026-09-28 · T-028 Background jobs · [#16](https://github.com/matchastack/work-journal/pull/16)
**Changed:** added a Procrastinate job queue on Postgres, with retries and exponential backoff, cron schedules, an example job, a 15-minute heartbeat, and `wj worker`. Adds Procrastinate and psycopg.

**Verify:**
- CI is green on #16
- `uv run wj worker` starts and logs a heartbeat within 15 minutes

## 2026-09-28 · T-013 Style checker · [#10](https://github.com/matchastack/work-journal/pull/10)
**Changed:** added the style checker. Resume bullets must open with an action verb in the past tense, even for the current role, use no first-person pronouns and fit in 2 lines at the template's width. LinkedIn text must fit each section's limit, and every style avoids your words to avoid.

**Verify:**
- CI is green on #10
- A few of your own bullets get the findings you'd expect
- A bullet in the present tense is flagged, even for your current role

## 2026-09-30 · T-009 Consistency linter and resume rules · [#20](https://github.com/matchastack/work-journal/pull/20)
**Changed:** added `wj lint`, which checks the master profile against the resume rules (R1-R4, R9, R10) and for mixed date formats, misspelt skill names and duplicate bullets, and a check that stops a tailored resume with rule errors from rendering.

**Verify:**
- CI is green on #20
- `uv run wj lint` on your profile gives the counts in the PR, and each finding points at the right item
- The education rules it reads, and the one it lists as not checked, match what you meant

## 2026-09-28 · T-023 Portfolio page · [#13](https://github.com/matchastack/work-journal/pull/13)
**Changed:** added the portfolio page, built as static HTML in the style of your site. It shows public, active content only, with project tabs by role type, link previews and JSON-LD. Adds `wj portfolio build`, Tailwind's standalone CLI, and accessibility checks in Chromium, which CI runs.

**Verify:**
- CI is green on #13
- The page built from your profile reads like your site and shows nothing you'd keep off the web
- Tabs following your keep-for tags suits you
## 2026-09-29 · T-054 ASCII characters everywhere · [#19](https://github.com/matchastack/work-journal/pull/19)
**Changed:** the rules in `app/text.py` now give every common typographic character its ASCII version (dashes, quotes, the ellipsis, spaces and symbols), and code writes "..." when it shortens text. FR-WRT-6 covers everything the app writes.

**Verify:**
- CI is green on #19
- Importing your file gives the same counts as before, and all three output files are pure ASCII
- The table in the PR matches how you want each character written
## 2026-09-28 · T-010 LaTeX renderer, T-011 Owner's resume template · [#11](https://github.com/matchastack/work-journal/pull/11)
**Changed:** added resume rendering. Values are escaped into LaTeX templates, compiled in a sandbox (no shell escape, no file access outside a temporary folder, 30 s timeout) and cut to fit the page limit, lowest-value bullets first. The default template is your layout, without comments or personal content. Adds `wj render`, TeX Live with `cm-super` in CI, and Jinja2 and pypdf.

**Verify:**
- CI is green on #11
- `uv run wj render` on your imported profile looks like your own PDF. Checked: the 2 pages match yours pixel for pixel, except that date ranges use an ASCII hyphen
- The cut order is how you'd cut by hand

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
