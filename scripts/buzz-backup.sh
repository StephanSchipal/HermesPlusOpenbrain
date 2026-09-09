#!/usr/bin/env bash
# Nightly Buzz backup. Install on the VPS:
#   chmod +x /root/HermesPlusOpenbrain/scripts/buzz-backup.sh
#   ( crontab -l 2>/dev/null; echo "17 3 * * * /root/HermesPlusOpenbrain/scripts/buzz-backup.sh >> /root/backups/buzz/cron.log 2>&1" ) | crontab -
set -euo pipefail

COMPOSE_DIR=/root/HermesPlusOpenbrain/deploy
COMPOSE_FILE=docker-compose.buzz.yml
DEST=/root/backups/buzz
KEEP_DAYS=7
STAMP=$(date +%Y%m%d-%H%M%S)

mkdir -p "$DEST"
cd "$COMPOSE_DIR"

# 1. env (BUZZ_* lines only)
grep -E '^\s*(BUZZ_|RELAY_OWNER_PUBKEY)' .env > "$DEST/env-$STAMP.txt"

# 2. postgres logical dump
docker compose -f "$COMPOSE_FILE" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$DEST/pg-$STAMP.sql.gz"

# 3. minio + git volumes (tar via a throwaway alpine mount)
for vol in buzz_buzz-minio-data buzz_buzz-git-data; do
  docker run --rm -v "$vol":/v -v "$DEST":/b alpine \
    tar czf "/b/${vol}-$STAMP.tar.gz" -C /v .
done

# 4. rotate
find "$DEST" -type f -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) buzz backup ok -> $DEST (pg-$STAMP.sql.gz)"
