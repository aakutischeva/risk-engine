#!/bin/sh
set -eu

umask 077

test -s /run/secrets/patroni_superuser_password
test -s /run/secrets/pg_replicator_password
test -s /var/lib/postgresql/data/PG_VERSION

export PATRONI_SUPERUSER_PASSWORD
PATRONI_SUPERUSER_PASSWORD="$(cat /run/secrets/patroni_superuser_password)"

export PATRONI_REPLICATION_PASSWORD
PATRONI_REPLICATION_PASSWORD="$(cat /run/secrets/pg_replicator_password)"

echo "Patroni secrets loaded; existing PostgreSQL data found."

exec su-exec postgres patroni /etc/patroni/patroni.yml
