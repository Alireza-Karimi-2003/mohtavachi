#!/bin/sh
set -eu

echo "Validating Production configuration..."
python -c "from app.config import validate_production_configuration; validate_production_configuration()"

echo "Running database migrations..."
alembic upgrade head

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
