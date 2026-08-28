#!/bin/sh
set -e

# Kör databas-migrationer vid varje start (idempotent)
alembic upgrade head

# Starta servern. Railway sätter PORT, lokalt faller vi tillbaka på 8000
exec uvicorn app.api:app --host 0.0.0.0 --port "${PORT:-8000}"
