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

app_url=$(uv run --frozen python -c 'from app.config import get_settings; print(get_settings().app_url)')
if [[ $app_url != https://* ]]; then
  echo "APP_URL in backend/.env is $app_url; set it to the deployed address (https://...)." >&2
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
