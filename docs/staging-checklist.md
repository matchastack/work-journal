# Staging checklist

Before you merge a change to the core loop, or after a change to the hosting, try it on staging:
a second copy of the hosted app with a bot, a database and a sign-in of its own, used the way you
use the real one. Each run takes about half an hour.

The end-to-end test (`backend/tests/e2e/`, run in CI) already covers the web app's part of the
loop: signing in, accepting a change in the Inbox, publishing and the public page, with GitHub
and Claude stubbed. This checklist covers what it can't: Telegram, the real models, Vercel and
Neon, LaTeX and the PDFs, and a real job posting.

Copy the checklist into the pull request and tick it there, as with the change log's **Verify**
bullets. Note anything that fails, with the step.

## Set up staging once

Staging gets its own piece of everything [deploy.md](deploy.md) sets up, so a run never touches
your bot, your public page or the data you journal into.

| Piece | Staging's own | How |
|---|---|---|
| Bot | A dev bot | In Telegram, message @BotFather, send `/newbot`, and name it, for example, "Work Journal (dev)". It gives you the dev bot's token. |
| Database | A Neon branch, `staging` | In your Neon project, open **Branches**, click **New branch**, name it `staging`, take `production` as its parent and include its current data. Copy its direct connection string (connection pooling off), as in deploy.md step 2. |
| App | A second Vercel project, such as `work-journal-staging` | Import `matchastack/work-journal` again and set it up as in deploy.md step 3, with the variables below for its **Production** environment. Set its function region and keep Fluid compute on, as there. |
| Sign-in | A second GitHub OAuth app | As in deploy.md step 4, with the callback `https://<staging>.vercel.app/auth/callback`. |
| Secrets | New webhook and cron secrets | `openssl rand -hex 32`, twice. |

The staging project's variables are production's (deploy.md step 3), except:

| Variable | Staging's value |
|---|---|
| `APP_URL` | `https://<staging>.vercel.app`, the address Vercel shows for the staging project |
| `DATABASE_URL` | the `staging` branch's direct connection string |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | the staging OAuth app's |
| `TELEGRAM_BOT_TOKEN` | the dev bot's |
| `TELEGRAM_WEBHOOK_SECRET`, `CRON_SECRET` | the new secrets |
| `DATA_ENCRYPTION_KEY` | production's: the branch's copy of your journal is encrypted with it |

Keep the Claude settings as in production: staging tries the real models, so each run costs a
few model calls. If you'd rather staging held none of your journal, create the branch with
**schema only** instead, make a new key with `wj keys new`, and load your profile in step 3 of
"Before each run". A schema-only branch can't be reset from its parent: delete it and create it
again instead.

Then make a **working copy for staging** on your computer, so commands for staging can't reach
production. From the repository:

```sh
git worktree add ../work-journal-staging main
```

In `../work-journal-staging/backend/`, create `.env` as deploy.md step 6 says, with staging's
values, and add the Claude settings and TeX Live (`backend/templates/README.md`) for the
renders. Every staging command below runs from there. Don't run `scripts/post-deploy.sh` for
staging: it only runs against `main` and production.

## Before each run

In this order, so the deploy migrates the database you reset.

- [ ] **Reset the database.** In Neon, open the `staging` branch and click **Reset from parent**.
      Staging starts again from today's copy of production.
- [ ] **Deploy the change to staging.** Push the branch. In the staging project's
      **Deployments**, open the branch's preview deployment and click **Promote**, then confirm.
      Vercel builds a new production deployment of the branch with staging's variables, and the
      build migrates the staging database.
- [ ] **Bring the working copy to the same code.** From `../work-journal-staging/backend/`:
      `git switch --detach origin/<branch>` (after `git fetch`), then `uv sync`, then
      `uv run alembic upgrade head`, which finds nothing to do after the build's migration.
      With a schema-only branch, load your profile:
      `uv run wj db load-profile <your checkout>/local/profile.json --user <your GitHub username>`
      (the working copy has a `local/` folder of its own, which starts empty).
- [ ] **Connect the dev bot.** `uv run wj telegram set-webhook`, then
      `uv run wj telegram link --user <your GitHub username>` and open the link in Telegram
      within 15 minutes: the dev bot answers "Linked."
- [ ] `https://<staging>.vercel.app/healthz` answers `{"status":"ok","database":"ok"}`.
- [ ] In the staging web app, sign in with GitHub. On **Settings**, turn off **Let search engines
      index the page**, so staging's copy of your page stays out of search results.

## Dev bot message, summary, proposal, publish

- [ ] Send the dev bot a short note about a piece of work, with a number in it, and a word you'll
      search the logs for later, such as a made-up project name. Then send `/done`.
- [ ] If the bot asks a follow-up question, answer it or tap **Skip**. When it asks which role
      the entry is about, tap one.
- [ ] Within a minute, the bot replies with the facts it saved: each one says what the note
      says, with the same numbers, and the reply links to the entry. The link opens the entry on
      the Journal page, with its messages and facts.
- [ ] Send `/refresh`. Within a few minutes the bot says "N updates proposed from M facts since
      your last refresh", with a link to the Inbox (or that there's nothing new or nothing to
      review, which is right only if your note adds nothing to the profile).
- [ ] In the Inbox, each change shows the text before and after, the facts it came from, and the
      verifier's result. No number differs from your note, and nothing claims more than you did.
- [ ] Accept one change, reject another with a reason, decide on the rest, and apply. The page
      says "Applied: your profile is now at version N." The reason appears in Settings, under
      the style notes.
- [ ] Render the master resume: from the working copy, `uv run wj tick`. It renders version N
      and keeps the PDF in the database.
- [ ] On the **Portfolio** page, the page says it shows (or will show) version N, and the resume
      downloads offer "Master resume (version N)". Tick it and save. If the page isn't
      published, **Publish**: it says "Published since ...".

## Check the page and the PDFs

- [ ] In a private window, open `https://<staging>.vercel.app/p/<handle>`. The change you
      accepted shows, and the one you rejected doesn't. Nothing marked Resume only or Private
      shows, nor your phone number, nor benched or planned items.
- [ ] At a 360 px width (the browser's device toolbar), nothing runs off the side, the menu opens
      and closes, and the project tabs filter the projects.
- [ ] The page's headers: `curl -sI https://<staging>.vercel.app/p/<handle>` shows a
      `cache-control` starting with `public, max-age=0` (the app also sends
      `s-maxage=60, stale-while-revalidate=60`, which Vercel's CDN acts on and may leave out),
      an `etag`, `x-content-type-options: nosniff`, a `content-security-policy` starting with
      `default-src 'none'`, and `x-robots-tag: noindex` (since indexing is off). Run it again
      within a minute: `x-vercel-cache` says `HIT` or `STALE`, so Vercel's CDN keeps the page.
- [ ] A handle nobody has, `curl -sI https://<staging>.vercel.app/p/no-such-page`, answers `404`
      with `cache-control: no-store`.
- [ ] Download the resume from the page. It saves under a name made of the handle and the
      button's text, such as `<handle>-resume.pdf`, opens, and shows version N: the accepted
      change is in it. Its text can be selected and copied, nothing in
      it is a placeholder, and its font and margins look as roomy as before.
- [ ] **Unpublish** on the Portfolio page. Within about two minutes, the page and its resume link
      answer "Page not found". Publish again if you go on.

## Tailor a real posting

- [ ] Save the text of a real job posting in a file outside the repository, such as
      `~/postings/backend-engineer.txt`. Never put it in the repository.
- [ ] From the working copy: `uv run wj tailor ~/postings/backend-engineer.txt --db`. Approve or
      decline each new phrasing it asks about.
- [ ] It writes a one-page PDF and its `.tex` to `local/resumes/`, prints the posting's keyword
      coverage and the ATS check, and ends without errors (exit status 0). Errors printed at the
      end mean the resume mustn't be sent: note them in the pull request.
- [ ] Open the PDF. It's one page; every number, date, title and tool in it is in your profile or
      facts; it claims nothing from your skill gaps; and nothing is a placeholder.
- [ ] `uv run wj applications --db` lists the application, newest first.
- [ ] If it passed its checks, the tailored resume is among the resumes the Portfolio page can
      offer. Offer it, download it from the public page, and take it off again.

The web app's Tailor page is **Coming** (T-045); once it's built, tailor the same posting there
too.

## Look at the logs

- [ ] In the staging project on Vercel, open **Logs** for the last hour. No request answered 500.
      These warnings each point at a step that went wrong: `couldn't run ... jobs now`,
      `refresh ... failed`, `gave up on journal entry ...`, `couldn't make the bot's ... call`,
      `GitHub sign-in failed` and `TELEGRAM_BOT_TOKEN isn't set`.
- [ ] Search the logs for the word you put in your note. Nothing is found: journal and fact text
      are never logged.
- [ ] In Neon's SQL Editor, on the `staging` branch:
      `SELECT task, tier, outcome FROM llm_calls ORDER BY at DESC LIMIT 10;` lists the run's
      calls, each with a tier as requirements section 10 routes it, and an outcome.
      `SELECT status, outputs, output_version FROM refreshes ORDER BY requested_at DESC LIMIT 1;`
      shows the refresh `proposed`, its outputs `updated` and version N.
- [ ] Vercel lists the staging project's cron job. A tick by hand,
      `curl -s -H "Authorization: Bearer <staging CRON_SECRET>" https://<staging>.vercel.app/internal/tick`,
      answers `{"retried":0}` or similar.

## Afterwards

- [ ] Note the results in the pull request.
- [ ] Unpublish staging's page, unless you'll run again soon.
- [ ] Merging deploys `main` to the staging project too, so staging follows `main` until the next
      run.
