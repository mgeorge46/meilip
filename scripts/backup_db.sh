#!/usr/bin/env bash
# Daily pg_dump of the production database.
#
# Intended to run on the prod host as a systemd timer (or cron) at 02:00
# local time. Keeps 14 daily dumps locally; weekly syncs to S3 if configured.
#
# Env (via /etc/meili/backup.env or docker .env):
#   DATABASE_URL  — e.g. postgres://user:pass@localhost:5432/meili_prd01
#   BACKUP_DIR    — default /var/backups/meili
#   S3_BACKUP_BUCKET — optional; weekly pushes go here
set -euo pipefail

BACKUP_DIR=${BACKUP_DIR:-/var/backups/meili}
TS=$(date -u +%Y%m%dT%H%M%SZ)
DOW=$(date +%u)   # 1..7 (Mon..Sun)

mkdir -p "$BACKUP_DIR"

DUMP="$BACKUP_DIR/meili-$TS.sql.gz"
pg_dump --no-owner --no-acl "$DATABASE_URL" | gzip -9 > "$DUMP"

# Rotate: keep 14 most recent local dumps
ls -1t "$BACKUP_DIR"/meili-*.sql.gz | tail -n +15 | xargs -r rm --

# Weekly push to S3 on Sundays
if [[ "$DOW" == "7" && -n "${S3_BACKUP_BUCKET:-}" ]]; then
  aws s3 cp "$DUMP" "s3://${S3_BACKUP_BUCKET}/weekly/" --only-show-errors
fi

echo "OK $DUMP"
