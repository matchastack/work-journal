#!/usr/bin/env bash
# The steps to take on your computer after each deploy: run it after every merge to main, from any
# folder, as `backend/scripts/post-deploy.sh`.
#
# Vercel deploys each merge to main by itself (docs/deploy.md), and its build migrates the
# database. Some changes also need a step that only a command on your computer can take, such as
# telling Telegram about a new bot command. This script takes all of them, every time, so you
# don't need to know which deploy needs which: each step does nothing when there's nothing to do.
# It can run before Vercel has finished deploying, or after.
#
# It acts on what backend/.env points at, which must be the deployed app and its database
# (docs/deploy.md, step 6). It only reads secrets from there, and never prints them.
#
# Adding a step: put it before the last step, which checks the app, write it so that running it
# again is harmless, and have the change-log entry tell the owner to run this script.
set -euo pipefail

script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$script")/.."

step() { printf '\n==> %s\n' "$1"; }
fail() {
  printf '\npost-deploy: %s\n' "$1" >&2
  exit 1
}

if [ "${1:-}" != --on-main ]; then
  for tool in git uv curl; do
    command -v "$tool" > /dev/null || fail "$tool isn't installed."
  done

  step "Get the code that's deployed: main, as on GitHub"
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    fail "You have uncommitted changes. Commit or stash them, then run this again."
  fi
  git fetch --quiet origin main
  branch=$(git symbolic-ref --quiet --short HEAD || true)
  if [ "$branch" != main ]; then
    git switch --quiet main
    echo "Switched to main${branch:+ from $branch (to go back: git switch $branch)}."
  fi
  git merge --quiet --ff-only origin/main ||
    fail "Your main and GitHub's have different commits. Make your main match origin/main first."
  [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] ||
    fail "Your main has commits that GitHub's doesn't. Push them through a PR first."
  echo "At $(git log -1 --format='%h %s')"

  # Main's version of this script may have steps that this one doesn't, so it takes the rest.
  exec bash "$script" --on-main
fi

step "Install main's dependencies"
uv sync --locked

step "Check that backend/.env points at the deployed app"
app_url=$(
  uv run python - << 'PYTHON'
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
) || fail "Set backend/.env as docs/deploy.md says in step 6, then run this again."

step "Migrate the database (Vercel's build does this too; they take turns)"
uv run alembic upgrade head
echo "The database is at the latest revision."

step "Point Telegram at the webhook, and update the bot's command menu"
uv run wj telegram set-webhook

# New steps go above this line.
step "Check that the app answers"
curl --fail --silent --show-error --retry 2 --max-time 30 "$app_url/healthz" ||
  fail "$app_url/healthz didn't answer. See \"When something goes wrong\" in docs/deploy.md."
printf '\n\nDone.\n'
