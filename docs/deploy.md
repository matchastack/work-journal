# Deploying Work Journal for free

Work Journal runs on free plans (requirements C6 and §13):

| Piece | Service | What it does |
|---|---|---|
| The app | Vercel, Hobby plan | The API, the Telegram webhook, the web app and the tick, as one Python function. Vercel's cron calls the tick once a day, to run scheduled tasks and queued jobs. |
| Database | Neon, free plan | All data and the job queue |
| Bot | Telegram | Sends your messages to the webhook |

You set these up once, in this order. Your secrets go only into Vercel's settings and your own
`backend/.env`, never into git.

These steps follow Vercel's Python builder (`@vercel/python` 21) and runtime (`vercel-runtime`
0.23), read from their published packages, and Neon's documentation as of October 2026. Check
the first deploy against "Check it works" below.

## 1. Make the secrets

1. **The bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot` and
   follow its questions. It gives you the bot's token.
2. **Two random secrets**, one for the webhook and one for Vercel's cron to call the tick with.
   On your computer: `openssl rand -hex 32`, twice.
3. **The encryption key** for journal text. From `backend/`: `uv run wj keys new`. Keep a copy
   somewhere safe: without it, the stored journal can't be read.

## 2. Create the database on Neon

Sign up at [neon.com](https://neon.com) and click **New Project**. Fill in the form like this,
then click **Create project**:

| Setting | Choose |
|---|---|
| **Project name** | Any name, such as `work-journal` |
| **Region** | The one nearest you, such as **AWS Asia Pacific 1 (Singapore)**. Step 3 runs the app in the same place, since each request queries the database several times. |
| **Postgres database** | On. Expand it and set **Postgres version** to **16**, the version the tests run on (`compose.yml` and CI). A project keeps the version it was created with. |
| **Object storage**, **Functions**, **AI gateway** and **Neon Auth** | Off. The app keeps everything in Postgres, runs on Vercel, calls Claude itself and signs you in with GitHub. |

Then copy the connection string:

1. Click **Connect**. Keep the branch (`production`), database (`neondb`) and role
   (`neondb_owner`) it shows.
2. Turn **Connection pooling** off. You then get the **direct** connection string, whose host has
   no `-pooler`. Neon advises a direct connection for migrations, which each production build
   runs, and for `LISTEN`, which `wj worker` uses. One person's use stays far below the limit on
   direct connections.
3. Copy the string. It includes the password, and looks like
   `postgresql://neondb_owner:password@ep-example-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`.
   The app takes it as it is: it's `DATABASE_URL` in steps 3 and 6.

On the free plan, the database sleeps after 5 idle minutes, and each project gets 100 CU-hours of
compute a month (400 hours at the smallest size) and 1 GB of storage. The app wakes the database
only when you use the bot or the web app, and once a day for the tick, so it stays well within
both.

## 3. Create the app on Vercel

1. Sign up at [vercel.com](https://vercel.com) with GitHub, and import
   `matchastack/work-journal` as a new project.
2. Set **Root Directory** to `backend`, and keep the option that includes files outside it in the
   build: the build step also builds `frontend/`. Vercel detects FastAPI.

   Leave **Framework Settings** as Vercel fills them in, with no **Override** turned on.
   `backend/pyproject.toml` gives Vercel the app and the build step, and Vercel installs the
   dependencies from `uv.lock`. An install command would replace that install, and its
   `requirements.txt` example doesn't exist here. A build command would replace the build step,
   so the web app wouldn't be built and the database wouldn't be migrated.
3. Add the **environment variables**, for **Production** only. Preview deployments of other
   branches then never touch your data.

   | Variable | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `APP_URL` | `https://<project>.vercel.app`, the address Vercel shows for the project |
   | `WEB_DIST_DIR` | `webapp` |
   | `DATABASE_URL` | Neon's direct connection string |
   | `DATA_ENCRYPTION_KEY` | from `wj keys new` |
   | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | from step 4 |
   | `ALLOWED_GITHUB_LOGINS` | your GitHub username |
   | `TELEGRAM_BOT_TOKEN` | from @BotFather |
   | `TELEGRAM_WEBHOOK_SECRET` | the first random secret |
   | `CRON_SECRET` | the second random secret; Vercel's cron sends it to the tick |
   | `ANTHROPIC_API_KEY`, `LLM_MODEL_HEAVY`, `LLM_MODEL_STANDARD`, `LLM_MODEL_LIGHT` | your Claude API key and model IDs; `backend/.env.example` describes each, and the optional prices |

4. Deploy. The build step (`backend/scripts/vercel-build.sh`) builds the web app and then
   migrates the database. If a migration fails, the build fails and nothing changes.
5. **Run the app next to the database.** In the project's **Settings**, open **Functions**, and
   under **Function Regions** pick the region nearest Neon's: **Singapore (`sin1`)** for Neon's
   Singapore. Otherwise Vercel runs the app in Washington, D.C. (`iad1`), and every database
   query makes the trip. Then redeploy.

Each later merge to `main` deploys the same way.

## 4. Let GitHub sign you in

Create an OAuth app at **GitHub, Settings, Developer settings, OAuth Apps**, with the callback
`https://<project>.vercel.app/auth/callback`. Put its client ID and a new client secret into
Vercel (step 3), then redeploy.

## 5. The daily tick

There's nothing to set up: `backend/vercel.json` schedules it. Vercel's cron calls
`/internal/tick` once a day, at some time between 19:00 and 20:00 UTC; once a day is the most its
free plan allows. Vercel sends `CRON_SECRET` with each call, and the tick refuses any other
caller. To run it at another time, change the hour in `vercel.json` (it's in UTC), for example to
a few hours after you usually journal.

Each run answers `{"retried": 0}` or similar.

## 6. Connect the bot and your chat

From your computer, in `backend/.env`, set the same `DATABASE_URL`, `DATA_ENCRYPTION_KEY`,
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` as on Vercel, and
`APP_URL=https://<project>.vercel.app`. Then, from `backend/`, on the same code as the
deployment:

1. `uv run alembic upgrade head` creates any tables the database doesn't have yet. Each
   production build does this too, so after a deploy it changes nothing; before one, the commands
   below need it.
2. Optional: `uv run wj db load-profile --user <your GitHub username>` loads your imported
   resume as profile version 1.
3. `uv run wj telegram set-webhook` points Telegram at the deployed webhook.
4. `uv run wj telegram link --user <your GitHub username>` prints a link. Open it in Telegram
   within 15 minutes; the bot answers "Linked."

## Check it works

- `https://<project>.vercel.app/healthz` answers `{"status":"ok","database":"ok"}`.
- The web app opens at `https://<project>.vercel.app`, and you can sign in.
- Send the bot a message. In Neon's SQL Editor, `SELECT count(*) FROM journal_messages;` goes
  up by one. The text itself is stored encrypted.
- Send a note about your work, then `/done`. Within a minute, the bot replies with the facts it
  saved. In Neon, `SELECT task, outcome FROM llm_calls ORDER BY at DESC LIMIT 2;` shows the calls.
- Vercel lists the tick among the project's cron jobs, and the logs of each run show it
  answering 200.

## When something goes wrong

| Symptom | Fix |
|---|---|
| The build can't find `../frontend` | Turn on the Root Directory option that includes files outside it (step 3). |
| The build says no FastAPI entrypoint was found | Root Directory must be `backend`. |
| The build fails on `requirements.txt`, or the web app or new tables are missing after a deploy | Turn off every **Override** in Framework Settings (step 3), then redeploy. |
| The tick answers 401 | Set `CRON_SECRET` in Vercel (step 3), then redeploy: Vercel sends it with each call. |
| A command fails with `relation "..." does not exist` | The database is missing tables: run `uv run alembic upgrade head` (step 6). |
| The bot stops receiving messages | `wj telegram poll` turns the webhook off; run `wj telegram set-webhook` again. |
| The database is asleep or slow on the first request | Neon wakes in about a second; the next requests are quick. |
| Every request is slow, not just the first | Vercel runs the app far from the database: set the function region (step 3), then redeploy. |
