# User guide

How to use Work Journal: the Telegram bot, the web app (`frontend/`) and the command-line tool (`backend/`). It describes what works today. Pages and commands that a later task builds are marked **Coming**, with the task that adds them in [tasks.md](tasks.md).

This guide is for using the product. [CLAUDE.md](CLAUDE.md) lists every development command, and [docs/deploy.md](docs/deploy.md) sets up the hosting.

## Contents

1. [How it fits together](#how-it-fits-together)
2. [Set up on your computer](#set-up-on-your-computer)
3. [Journal from Telegram](#journal-from-telegram)
4. [The web app](#the-web-app)
5. [The command-line tool](#the-command-line-tool)
6. [Running and maintaining the backend](#running-and-maintaining-the-backend)
7. [Troubleshooting](#troubleshooting)

## How it fits together

1. **Journal.** You send short, informal notes about your work to your Telegram bot. The bot groups them into entries and saves the facts in them.
2. **Refresh.** When you want your profile to catch up, you send `/refresh`. The bot proposes changes to your career profile from the facts since the last refresh. Nothing runs on a schedule, and nothing changes the profile by itself.
3. **Review.** You decide on every proposed change in the web app's Inbox. Applying the ones you accept makes a new version of the profile; earlier versions stay.
4. **Outputs.** From the newest version, Work Journal makes your LaTeX resume, your portfolio page and your LinkedIn update text. You can also tailor a one-page resume to a job posting whenever you like.

Every generated sentence is checked against your facts, so wording never changes a number or inflates a claim.

| You want to... | Use |
|---|---|
| Record what you did | The Telegram bot |
| Fix a note or a saved fact | The web app, Journal page |
| Update your profile from new notes | `/refresh` in Telegram, then the web app's Inbox |
| Render the master resume, tailor a resume, build the portfolio page, write LinkedIn text | The command-line tool (web pages **Coming**) |

## Set up on your computer

You need [uv](https://docs.astral.sh/uv/) with Python 3.12 for the backend, and Node.js 22.22 or later for the web app. Docker is optional (it runs the local database), and TeX Live is needed only to render PDFs.

### Backend

From `backend/`:

```sh
uv sync                                  # install dependencies
cp .env.example .env                     # then fill in the settings below
uv run uvicorn app.main:app --reload     # the API at http://localhost:8000
```

Open `http://localhost:8000/healthz` to check it is up, or `http://localhost:8000/docs` for the API's reference.

`backend/.env` holds your settings; `backend/.env.example` lists every name with a comment. An empty value means "use the default". Never commit `.env`. The ones you will most often set:

| Setting | What it is for |
|---|---|
| `DATABASE_URL` | PostgreSQL, needed for the bot, the web app and every `--db` command |
| `DATA_ENCRYPTION_KEY` | Encrypts journal and fact text. Make one with `uv run wj keys new` and keep a copy somewhere safe: without it the stored journal can't be read |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `ALLOWED_GITHUB_LOGINS` | Sign-in to the web app. Only the GitHub usernames listed can sign in; empty means nobody |
| `APP_URL` | The app's address. `http://localhost:8000` by default; `https://` everywhere but your own computer |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | The bot's token from @BotFather, and a random secret for the webhook |
| `ANTHROPIC_API_KEY`, `LLM_MODEL_HEAVY`, `LLM_MODEL_STANDARD`, `LLM_MODEL_LIGHT` | Claude. Any command that writes or reads text with the model needs these |
| `ENTRY_TIMEOUT_MINUTES` | Minutes of quiet that close a journal entry (default 30) |

### Local database

```sh
docker compose up -d                     # from the repository root
```

Then set `DATABASE_URL=postgresql+asyncpg://work_journal@localhost:5432/work_journal` in `backend/.env`, and create the tables from `backend/`:

```sh
uv run alembic upgrade head
```

### Web app

From `frontend/`, with the API running on port 8000:

```sh
npm ci                                   # install dependencies
npm run dev                              # the web app at http://localhost:5173
```

To sign in on your own computer, set `APP_URL=http://localhost:5173` in `backend/.env` and use `http://localhost:5173/auth/callback` as the callback URL of your GitHub OAuth app (GitHub, Settings, Developer settings, OAuth Apps).

To serve the built web app from the API instead, run `npm run build` in `frontend/`. The API then serves `frontend/dist` at `/`.

### First-time data

If you have a master resume written in LaTeX, import it so your profile starts from it. From `backend/`:

```sh
uv run wj import tex path/to/master.tex            # writes profile.json and facts.json to ../local/
uv run wj db load-profile --user <github-login>    # loads them into the database as version 1
```

`local/` is git-ignored: it is the place for personal data. Or use `wj import tex path/to/master.tex --db` to save straight to the database. The import also writes `import-report.txt` beside them; read it for how the import went. Add `--force` to overwrite an earlier import.

## Journal from Telegram

### Link your chat

The bot journals only for a chat linked to your account. From `backend/`:

```sh
uv run wj telegram link --user <github-login>
```

It prints a one-time link, good for 15 minutes. Open it in Telegram; the bot answers "Linked." A message from any other chat gets a note on how to link.

### Connect the bot

The bot gets its messages in one of two ways:

| Mode | Command | When to use it |
|---|---|---|
| Webhook | `uv run wj telegram set-webhook` | The deployed app (needs an `https://` `APP_URL` and `TELEGRAM_WEBHOOK_SECRET`). Also sets the menu shown when you type `/`. |
| Polling | `uv run wj telegram poll` | Trying it on your computer. Runs until Ctrl-C and turns the webhook off, so run `set-webhook` again afterwards. |

### Send notes

Write as you would to a friend: what you built, fixed, decided, learned, and any numbers. Send as many messages as you like. A message sent more than 30 minutes (or `ENTRY_TIMEOUT_MINUTES`) after the last one starts a new entry. You can edit a message in Telegram; the new text is saved and the old one is kept.

When an entry closes, the bot may then:

1. **Ask one follow-up question** if the note lacks numbers, impact or your part in it. Reply with your answer, which joins the entry, or tap **Skip**.
2. **Ask which role the entry is about.** Tap a role, or **New role** and send its title and organisation as your next message.
3. **Reply with the facts it saved.** The reply links to the entry in the web app's Journal page.

### Commands

| Command | What it does |
|---|---|
| `/done` | Close the current entry now |
| `/skip` | Skip the question about the last entry |
| `/refresh` | Propose profile changes from your new facts. The bot says how many it proposes with a link to the Inbox, or that there is nothing new. While a refresh runs, `/refresh` says so. If proposals are still waiting, decide on them before sending it again |
| `/help` | List the commands |

Commands are never journaled. Message text is stored encrypted and is never logged.

**Coming:** reminders to journal (T-035), prompts for open questions (T-036) and a catch-up interview (T-037).

## The web app

Open `http://localhost:5173` while developing, or your deployed address. Sign in with GitHub; only the usernames in `ALLOWED_GITHUB_LOGINS` get in. The top bar shows your username and a **Sign out** button, and links to every page. The Inbox opens first.

| Page | Status | What it is for |
|---|---|---|
| [Inbox](#inbox) | Built | Review the changes proposed from your journal |
| [Journal](#journal) | Built | Everything you sent the bot, with the facts saved from it |
| Profile | Coming (T-042, T-043) | Your roles, projects and skills, with every earlier version |
| Resumes | Coming (T-044) | Preview and download resume variants |
| Tailor | Coming (T-045) | Tailor a resume to a job posting, and see where you applied |
| Portfolio | Coming (T-046) | Choose what the public page shows, and publish it |
| LinkedIn | Coming (T-047) | Copy the updated sections into LinkedIn |
| Settings | Coming (T-048) | Telegram, reminders and style notes |

A page that is not built yet says which task will add it. Until then, use the command-line equivalents in the next chapter.

### Inbox

After `/refresh`, each set of proposed changes appears here. Nothing changes in your profile until you apply it.

For each change you see the text before and after (removed words struck through, added words underlined), the facts it came from with a link to the journal entry, and the verifier's result. Then:

- **Accept** it as proposed.
- **Edit** its text and save. The verifier checks the new wording again.
- **Reject** it, with an optional reason.
- **Undo** a decision, until you apply the set.

A change the verifier flagged needs your decision; it is never accepted for you. A change that depends on another, such as a bullet under a new role, is rejected along with it and waits until that rejection is undone.

**Accept all that passed** accepts every waiting change the verifier passed. Once every change is decided, **Apply** makes a new version of your profile from the accepted ones. After each decision, focus moves to the next waiting change, so you can work through the list from the keyboard.

The master resume is then due to be rendered from the new version (see [Running and maintaining the backend](#running-and-maintaining-the-backend)).

### Journal

Everything you have sent the bot as one document, oldest first and grouped by month, with each entry's messages and saved facts together. An entry shows a status when it is not finished: open, waiting for you to choose its role in Telegram, finding its facts, or failed. An entry the bot judged not to be about work is marked.

You can correct anything in place:

- **Messages:** edit the text (earlier versions are kept) or delete it. An entry's last message can't be deleted; delete the entry instead.
- **Facts:** edit the statement, outcome, kind, your part in it, date, numbers, role, project and tools, or delete the fact. If the profile cites a fact, the page names the bullets before it deletes.
- **Entries:** delete a whole entry, with its messages and facts. If the profile cites any of those facts, the page names the bullets and asks you to confirm first.

**Download Markdown** and **Download JSON** at the top save the whole journal.

## The command-line tool

The `wj` tool runs from `backend/` as `uv run wj <command>`. Add `--help` to any command for its options. Most commands take their data from `local/` (for example `local/profile.json`); add `--db` to use the database instead, and `--user <github-login>` if the database has more than one user.

### Check and render your resume

| Task | Command |
|---|---|
| Check the profile against the resume rules | `uv run wj lint` |
| Render the master resume to `local/resumes/master.pdf`, with its `.tex` beside it | `uv run wj render` (add `--out <path>`) |

Rendering needs TeX Live; install it as `backend/templates/README.md` says. Linting flags problems such as a placeholder, a skill from the gaps list or a suspicious number shared across roles. Fix them in the profile before sending anything.

### Update the profile from your journal

`/refresh` in Telegram and the Inbox do this for the deployed app. On your computer, the same steps run from the command line:

```sh
uv run wj propose --db        # propose changes from facts since the last refresh
uv run wj apply --db          # review them one by one; accepted ones become the next version
uv run wj render --db         # render the newest version
```

`wj apply --yes` accepts every change that passed the checks and rejects the flagged ones without asking. Run `wj propose --db` from your computer when a refresh over months of facts is too long for the deployed app's time limit.

### Tailor a resume to a job posting

Save the posting's text in a file, then:

```sh
uv run wj tailor posting.txt --db
```

It picks the most relevant content from your profile, words it in the posting's language where your facts allow, and writes a one-page PDF to `local/resumes/<company>-<title>.pdf`. It also prints the posting's keyword coverage and an ATS check of the PDF (how an applicant-tracking system would read it), and logs the application. It needs the Claude settings and TeX Live.

- `--confirm <skill>` includes a skill marked verify-before-shipping that you can defend for this resume. Repeat for more.
- `--yes` uses every new phrasing that passed the checks, and saves it as a swap, without asking.
- `uv run wj posting parse posting.txt` prints the posting's title, skills and key terms as JSON; `wj tailor` accepts that JSON too.
- `uv run wj applications` lists the applications logged, newest first.

### Portfolio page

```sh
uv run wj portfolio build --out /tmp/site
```

Writes the static page from the profile's public content to the folder. Offer a resume on it with `--resume "Label=path/to/resume.pdf"` (repeat for more), and set `--site-url` for link previews. After you edit its template, run `backend/scripts/build-portfolio-css.sh`.

### LinkedIn text

```sh
uv run wj linkedin --db          # the headline, About and position texts that changed since last time
uv run wj linkedin --db --all    # every section, even unchanged ones
uv run wj linkedin done --db     # mark the pack done, once you have pasted it into LinkedIn
```

Paste the text into LinkedIn yourself; Work Journal never posts there.

### Work on single notes and bullets

| Task | Command |
|---|---|
| Extract the facts in a note and print them as JSON | `uv run wj extract note.txt` (add `--date YYYY-MM-DD`) |
| Write facts as a resume bullet | `uv run wj write --facts <ids>` |
| Write them as LinkedIn text, or in a posting's words | `--style linkedin`, or `--style posting --posting posting.json` |
| Write a new version of a bullet | `--edit <bullet-id>` (add `--swap <n>` to work in one of its swaps, `--save-swap` to keep the result as a swap) |

Every sentence written passes the verifier before it is stored or printed.

## Running and maintaining the backend

| Task | Command |
|---|---|
| Run the background worker (queued jobs and scheduled tasks) until Ctrl-C | `uv run wj worker` |
| Run one tick of background work, as the hosted app's daily cron does | `uv run wj tick` |
| Apply database migrations | `uv run alembic upgrade head` |
| Make a new encryption key | `uv run wj keys new` |
| Re-encrypt stored text with the first key in `DATA_ENCRYPTION_KEY` | `uv run wj keys rotate` |
| After each deploy of `main`: wait for the app, migrate, point Telegram at the webhook | `scripts/post-deploy.sh` |
| Score the LLM steps on the fictional cases | `uv run wj eval all` (`--private` for your own cases in `local/evals/`) |

**The master resume after a refresh.** The hosted app can't run LaTeX. After you apply changes in the Inbox, or when `/refresh` finds nothing new, run `uv run wj tick` on a computer with TeX Live and the settings from `docs/deploy.md` step 6. It renders the master resume and keeps it in the database, and `uv run wj render --db` writes the newest version to `local/resumes/master.pdf` at any time.

**Rotating keys.** Add the new key in front of the old one in `DATA_ENCRYPTION_KEY`, run `wj keys rotate`, and only then remove the old key.

**Evaluations.** Run `wj eval all` before changing a prompt or the model routing. The bar is 0 altered numbers, 0 inflated claims and at least 90% of facts recovered; `backend/evals/README.md` explains the suites.

## Troubleshooting

| Symptom | Fix |
|---|---|
| A command says `relation "..." does not exist` | The database is missing tables: `uv run alembic upgrade head` |
| GitHub sends you back to the wrong address after signing in | `APP_URL` and the GitHub OAuth app's callback URL must match: `<APP_URL>/auth/callback` |
| Sign-in is refused | Your GitHub username (in lowercase) must be in `ALLOWED_GITHUB_LOGINS` |
| The bot ignores your messages | The chat isn't linked: `uv run wj telegram link --user <github-login>`. If you used `wj telegram poll`, run `wj telegram set-webhook` to go back to the webhook |
| Tapping a role under the bot's question does nothing | Run `wj telegram set-webhook` again so Telegram sends button presses |
| A model command says a tier has no model | Set `ANTHROPIC_API_KEY` and the `LLM_MODEL_*` variable for that tier |
| `wj render` or `wj tailor` can't find LaTeX | Install TeX Live (`backend/templates/README.md`) |
| The bot says a refresh didn't finish in time | Run `uv run wj propose --db` from your computer |
| Journal text can't be read after changing keys | `DATA_ENCRYPTION_KEY` must still include the key the text was written with |

More hosting problems are in [docs/deploy.md](docs/deploy.md#when-something-goes-wrong).
