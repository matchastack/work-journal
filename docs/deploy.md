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
0.23), read from their published packages. Check the first deploy against "Check it works" below.

## 1. Make the secrets

1. **The bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot` and
   follow its questions. It gives you the bot's token.
2. **Two random secrets**, one for the webhook and one for Vercel's cron to call the tick with.
   On your computer: `openssl rand -hex 32`, twice.
3. **The encryption key** for journal text. From `backend/`: `uv run wj keys new`. Keep a copy
   somewhere safe: without it, the stored journal can't be read.

## 2. Create the database on Neon

1. Sign up at [neon.com](https://neon.com) and create a project. Pick the region closest to you,
   such as Singapore.
2. Copy the project's connection string. Use the **direct** one (the host has no `-pooler`): the
   app's drivers keep prepared statements, which a transaction pooler breaks. It looks like
   `postgresql://user:password@ep-example-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`,
   and the app takes it as it is.

The free plan sleeps the database after 5 idle minutes and counts the hours it's awake. The app
wakes it only when you use the bot or the web app, and once a day for the tick.

## 3. Create the app on Vercel

1. Sign up at [vercel.com](https://vercel.com) with GitHub, and import
   `matchastack/work-journal` as a new project.
2. Set **Root Directory** to `backend`, and keep the option that includes files outside it in the
   build: the build step also builds `frontend/`. Vercel detects FastAPI.
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
`APP_URL=https://<project>.vercel.app`. Then, from `backend/`:

1. Optional: `uv run wj db load-profile --user <your GitHub username>` loads your imported
   resume as profile version 1.
2. `uv run wj telegram set-webhook` points Telegram at the deployed webhook.
3. `uv run wj telegram link --user <your GitHub username>` prints a link. Open it in Telegram
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
| The tick answers 401 | Set `CRON_SECRET` in Vercel (step 3), then redeploy: Vercel sends it with each call. |
| The bot stops receiving messages | `wj telegram poll` turns the webhook off; run `wj telegram set-webhook` again. |
| The database is asleep or slow on the first request | Neon wakes in about a second; the next requests are quick. |
