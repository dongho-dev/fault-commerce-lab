#!/bin/sh
set -eu

alembic upgrade head
if [ "${SEED_CATALOG:-false}" = "true" ]; then
  python -m scripts.seed_catalog
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
