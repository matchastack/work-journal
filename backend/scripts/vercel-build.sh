#!/bin/sh
# Vercel's build step for the backend (`[tool.vercel.scripts]` in pyproject.toml). Vercel runs it
# from backend/, with the Python dependencies installed, before the new deployment goes live.
#
# 1. It builds the web app into backend/webapp/, which goes into the function with the API.
#    Vercel's WEB_DIST_DIR=webapp tells FastAPI to serve it at /.
# 2. On production deployments only, it migrates the database, so the new code never runs on an
#    old schema. A failed migration fails the build, and the old deployment keeps running.
#    Preview deployments have no DATABASE_URL (it's set for production only).
set -eu

(cd ../frontend && npm ci && npm run build -- --outDir ../backend/webapp --emptyOutDir)

if [ "${VERCEL_ENV:-}" = production ]; then
  python -m alembic upgrade head
fi
