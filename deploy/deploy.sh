#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE=(
  sudo docker compose
  -f docker-compose.yml
  -f docker-compose.standby.yml
  -f docker-compose.dcs.yml
  -f docker-compose.patroni.yml
  -f docker-compose.haproxy.yml
  -f docker-compose.rabbitmq-ha.yml
  -f docker-compose.rabbitmq-proxy.yml
  -f docker-compose.backup.yml
  --profile ha
  --profile backup
)

APPLICATIONS=(
  api
  outbox-publisher
  alert-notifier
  alert-dispatcher
  notification-receiver
)

INFRASTRUCTURE=(
  risk-engine-postgres
  risk-engine-postgres-standby
  risk-engine-etcd
  risk-engine-pg-haproxy
  risk-engine-redis
  risk-engine-rabbitmq
  risk-engine-rabbitmq-2
  risk-engine-rabbitmq-3
  risk-engine-rabbitmq-haproxy
  risk-engine-alert-postgres
)

echo "========================================"
echo "       RISK ENGINE — DEPLOYMENT"
echo "========================================"

echo
echo "=== 1. CHECKING COMPOSE CONFIGURATION ==="

"${COMPOSE[@]}" config --quiet

echo "[PASS] Compose configuration valid"

echo
echo "=== 2. CHECKING EXISTING INFRASTRUCTURE ==="

for CONTAINER in "${INFRASTRUCTURE[@]}"; do
    STATE=$(sudo docker inspect \
        -f '{{.State.Running}}' "$CONTAINER" \
        2>/dev/null || true)

    if [[ "$STATE" != "true" ]]; then
        echo "[FAIL] $CONTAINER is not running"
        echo "Initial bootstrap or infrastructure recovery is required."
        exit 1
    fi

    echo "[PASS] $CONTAINER"
done

echo
echo "=== 3. BUILDING APPLICATION IMAGES ==="

"${COMPOSE[@]}" build "${APPLICATIONS[@]}"

echo "[PASS] Application images built"

echo
echo "=== 4. STARTING APPLICATION SERVICES ==="

"${COMPOSE[@]}" up -d \
    --no-deps \
    --no-recreate \
    "${APPLICATIONS[@]}"

echo "[PASS] Application services started"

echo
echo "=== 5. VERIFYING DEPLOYMENT ==="

bash deploy/verify.sh

echo
echo "========================================"
echo "       DEPLOYMENT SUCCESS"
echo "========================================"
