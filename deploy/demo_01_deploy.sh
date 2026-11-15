#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

MODE="${1:---check}"

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

echo "========================================="
echo " RISK ENGINE — DEPLOYMENT DEMONSTRATION"
echo "========================================="

if [[ "$MODE" != "--check" && "$MODE" != "--execute" ]]; then
    echo "Usage: $0 --check | --execute"
    exit 1
fi

echo
echo "[1] Checking Docker Compose configuration"

"${COMPOSE[@]}" config --quiet

echo "[PASS] Compose configuration valid"

echo
echo "[2] Checking infrastructure"

REQUIRED=(
  risk-engine-api
  risk-engine-postgres
  risk-engine-postgres-standby
  risk-engine-pg-haproxy
  risk-engine-redis
  risk-engine-rabbitmq
  risk-engine-rabbitmq-2
  risk-engine-rabbitmq-3
  risk-engine-rabbitmq-haproxy
  risk-engine-alert-postgres
)

for container in "${REQUIRED[@]}"; do
    state=$(sudo docker inspect \
      -f '{{.State.Running}}' "$container" 2>/dev/null || true)

    if [[ "$state" != "true" ]]; then
        echo "[FAIL] $container is not running"
        exit 1
    fi

    echo "[PASS] $container"
done

echo
echo "[3] Checking API"

curl -fsS --max-time 10 \
  http://127.0.0.1:8000/health

echo

OLD_ID=$(sudo docker inspect \
  -f '{{.Id}}' risk-engine-api)

echo "Current API container: ${OLD_ID:0:12}"

if [[ "$MODE" == "--check" ]]; then
    echo
    echo "========================================="
    echo " PREFLIGHT PASSED"
    echo " No containers were modified"
    echo "========================================="
    exit 0
fi

echo
echo "WARNING: API will be recreated."
echo "PostgreSQL, Redis and RabbitMQ will remain running."
echo "Active API requests may be interrupted."
echo

read -r -p "Type DEPLOY to continue: " answer

if [[ "$answer" != "DEPLOY" ]]; then
    echo "Cancelled."
    exit 0
fi

echo
echo "[4] Building API image"

"${COMPOSE[@]}" build api

echo
echo "[5] Recreating API container"

"${COMPOSE[@]}" up -d \
  --no-deps \
  --force-recreate \
  api

echo
echo "[6] Waiting for API"

READY=false

for i in $(seq 1 30); do
    if curl -fsS --max-time 3 \
       http://127.0.0.1:8000/health \
       >/dev/null 2>&1; then

        READY=true
        break
    fi

    sleep 2
done

if [[ "$READY" != "true" ]]; then
    echo "[FAIL] API did not become available"
    echo "Inspect logs: sudo docker logs risk-engine-api"
    exit 1
fi

NEW_ID=$(sudo docker inspect \
  -f '{{.Id}}' risk-engine-api)

echo "[PASS] API is available"

echo
echo "Old container: ${OLD_ID:0:12}"
echo "New container: ${NEW_ID:0:12}"

if [[ "$OLD_ID" == "$NEW_ID" ]]; then
    echo "[FAIL] Container was not recreated"
    exit 1
fi

echo
echo "[7] Running integration verification"

bash deploy/verify.sh

echo
echo "========================================="
echo " DEPLOYMENT DEMONSTRATION SUCCESS"
echo "========================================="
