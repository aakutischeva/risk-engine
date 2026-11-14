
#!/bin/sh
set -eu
umask 077

export PGHOST="$(cat /run/backup-host)"
export PGUSER="$(cat /run/backup-user)"
export PGDATABASE="$(cat /run/backup-db)"
export PGPASSWORD="$(cat /run/backup-password)"
export PGCONNECT_TIMEOUT=10

if [ -z "$PGUSER" ] || [ -z "$PGDATABASE" ]; then
    echo "ERROR: PostgreSQL configuration is missing"
    exit 1
fi

BACKUP_DIR="/backups/$PGDATABASE"
mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/${PGDATABASE}_${TIMESTAMP}.dump"
TEMP_FILE="${BACKUP_FILE}.partial"

trap 'rm -f "$TEMP_FILE"' EXIT

echo "[$(date)] Starting backup: $PGDATABASE"

pg_dump \
    --no-password \
    --format=custom \
    --file="$TEMP_FILE" \
    "$PGDATABASE"

test -s "$TEMP_FILE"

pg_restore --list "$TEMP_FILE" > /dev/null

mv "$TEMP_FILE" "$BACKUP_FILE"
sha256sum "$BACKUP_FILE" > "${BACKUP_FILE}.sha256"

echo "[$(date)] Backup OK: $BACKUP_FILE"

# Remove automatic backups older than 14 days
find "$BACKUP_DIR" -type f -mtime +13 \
    \( -name "${PGDATABASE}_*.dump" \
    -o -name "${PGDATABASE}_*.dump.sha256" \) \
    -delete

echo "[$(date)] Backup finished"

