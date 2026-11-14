
#!/bin/sh
set -eu
umask 077

# Save credentials for scheduled cron jobs
printf '%s' "$PGHOST" > /run/backup-host
printf '%s' "$PGUSER" > /run/backup-user
printf '%s' "$PGDATABASE" > /run/backup-db
printf '%s' "$PGPASSWORD" > /run/backup-password

# Daily backup at 03:00
echo '0 3 * * * /bin/sh /opt/backup/backup.sh >> /proc/1/fd/1 2>&1' > /etc/crontabs/root

echo "Backup scheduler initialized for $PGDATABASE"
echo "Schedule: every day at 03:00"

# Create the first backup immediately
/bin/sh /opt/backup/backup.sh

# Start cron in foreground
exec crond -f -l 8

