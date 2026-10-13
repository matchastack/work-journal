#!/usr/bin/env bash
# Run after each deploy of `main`, from anywhere in the repository: `backend/scripts/post-deploy.sh`.
#
# It runs every step that a deploy can need, every time, so you never have to work out which ones
# this deploy needs. Each step is safe to repeat: a step with nothing to do changes nothing.
#
# It uses the settings in backend/.env (docs/deploy.md, step 6), and runs on your checkout of
# `main`, which must match origin/main: the code Vercel deployed.
#
# To add a step that a later change needs after its deploy, add it below, and make it safe to
# repeat.
set -euo pipefail

cd "$(dirname "$0")/.."

step() { printf '\n==> %s\n' "$1"; }

step "Checking that this checkout matches the deployed code (origin/main)"
git fetch --quiet origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "This checkout isn't origin/main. Run \`git switch main && git pull\`, then run this again." >&2
  exit 1
fi
if [ ! -f .env ]; then
  echo "backend/.env is missing: set it up as docs/deploy.md (step 6) says." >&2
  exit 1
fi

step "Installing dependencies"
uv sync --frozen --quiet

step "Checking that backend/.env points at the deployed app"
# Stops before anything touches a database or Telegram. Prints hosts only, never secrets.
if ! app_url=$(
  uv run --frozen python - << 'PYTHON'
import sys
from urllib.parse import urlsplit

from app.config import LOCAL_HOSTS, get_settings

settings = get_settings()
problems: list[str] = []
if urlsplit(settings.app_url).hostname in LOCAL_HOSTS:
    problems.append(f"APP_URL is {settings.app_url}, not the deployed app's https:// address")
database = None
if settings.database_url is None:
    problems.append("DATABASE_URL isn't set")
else:
    database = urlsplit(settings.database_url.get_secret_value()).hostname
    if database is None or database in LOCAL_HOSTS:
        problems.append("DATABASE_URL is a database on this computer, not the deployed one")
for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"):
    if getattr(settings, name.lower()) is None:
        problems.append(f"{name} isn't set")
if problems:
    print("\n".join(f"- {problem}" for problem in problems), file=sys.stderr)
    sys.exit(1)
print(f"App: {settings.app_url}\nDatabase: {database}", file=sys.stderr)
print(settings.app_url)
PYTHON
); then
  echo "Set backend/.env as docs/deploy.md (step 6) says, then run this again." >&2
  exit 1
fi

step "Waiting for $app_url/healthz to answer"
for attempt in $(seq 1 30); do
  if health=$(curl --silent --fail --max-time 10 "$app_url/healthz"); then
    echo "$health"
    break
  fi
  if [ "$attempt" = 30 ]; then
    echo "The app didn't answer within 5 minutes. Check the deployment on Vercel." >&2
    exit 1
  fi
  sleep 10
done

step "Migrating the database (the production build did this too, so it usually changes nothing)"
uv run --frozen alembic upgrade head

step "Pointing Telegram at the webhook and updating the bot's command menu"
uv run --frozen wj telegram set-webhook

printf '\nDone: %s is up to date.\n' "$app_url"
