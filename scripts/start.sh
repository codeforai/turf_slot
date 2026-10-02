#!/bin/sh
# Container entrypoint: apply migrations, then serve.
# Migrations hold a PostgreSQL advisory lock, so several instances starting at once is safe.
set -e

echo "Applying database migrations..."
alembic upgrade head

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "Seeding demo data..."
  python -m app.cli seed-demo --force
fi

# One worker by default: small instances (512 MB) and per-process Prometheus metrics.
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers \
  --forwarded-allow-ips="*" \
  --no-access-log
