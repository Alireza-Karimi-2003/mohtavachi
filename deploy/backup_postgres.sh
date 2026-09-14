#!/bin/sh
set -eu

COMPOSE_FILE="docker-compose.production.yml"
BACKUP_DIR="${1:-./backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/mohtavachi_${STAMP}.dump"

mkdir -p "$BACKUP_DIR"
echo "Creating PostgreSQL backup: $OUT"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$OUT"

find "$BACKUP_DIR" -type f -name 'mohtavachi_*.dump' -mtime +"$RETENTION_DAYS" -delete

echo "Backup complete. ✅"
