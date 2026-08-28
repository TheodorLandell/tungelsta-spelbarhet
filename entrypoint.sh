#!/bin/sh
set -e

# Kör databas-migrationer vid varje start (idempotent)
alembic upgrade head

# Starta servern
exec uvicorn app.api:app --host 0.0.0.0 --port 8000
