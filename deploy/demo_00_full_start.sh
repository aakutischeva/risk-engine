#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

MODE="${1:---check}"

case "$MODE" in
  --check|--start|--verify) ;;
  *) echo "Usage: $0 --check|--start|--verify"; exit 2 ;;
esac

[[ $# -le 1 ]] || exit 2
[[ $EUID -eq 0 ]] || {
  echo "Run with sudo bash"
  exit 1
}

[[ -z "${DOCKER_HOST:-}" &&
   -z "${DOCKER_CONTEXT:-}" ]] || {
  echo "Remote Docker is not allowed"
  exit 1
}

[[ "$(docker context show)" == default ]] || exit 1

[[ "$(docker context inspect default \
    --format '{{.Endpoints.docker.Host}}')" == unix://* ]] || {
  echo "Expected local Docker socket"
  exit 1
}

COMPOSE=(
  docker compose
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

INFRA=(
  risk-engine-etcd
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

APPS=(
  risk-engine-notification-receiver
  risk-engine-outbox-publisher
  risk-engine-alert-notifier
  risk-engine-alert-dispatcher
  risk-engine-api
)

BACKUPS=(
  risk-engine-backup-postgres-1
  risk-engine-backup-alert-postgres-1
)

running() {
  [[ "$(docker inspect -f '{{.State.Running}}' \
    "$1" 2>/dev/null || true)" == true ]]
}

start_one() {
  if running "$1"; then
    echo "[RUNNING] $1"
  else
    docker start "$1" >/dev/null
    echo "[STARTED] $1"
  fi
}

stage() {
  local label="$1"
  shift
  echo
  echo "=== $label ==="

  local c
  for c in "$@"; do
    start_one "$c"
  done
}

wait_pg() {
  local i a b

  for ((i=0; i<100; i++)); do
    a="$(docker exec risk-engine-postgres \
      psql -U riskengine -d riskengine -Atqc \
      'SELECT pg_is_in_recovery();' \
      2>/dev/null || true)"

    b="$(docker exec risk-engine-postgres-standby \
      psql -U riskengine -d riskengine -Atqc \
      'SELECT pg_is_in_recovery();' \
      2>/dev/null || true)"

    if [[ "$a/$b" == f/t || "$a/$b" == t/f ]]; then
      echo "[PASS] Patroni: Primary/Replica ($a/$b)"
      return 0
    fi

    sleep 2
  done

  echo "[FAIL] Patroni roles not ready" >&2
  return 1
}

wait_mq() {
  local status
  local deadline=$((SECONDS + 180))

  while (( SECONDS < deadline )); do
    status="$(timeout 12 docker exec \
      risk-engine-rabbitmq \
      rabbitmqctl cluster_status \
      2>/dev/null || true)"

    if printf '%s\n' "$status" |
       sed -n '/^Running Nodes$/,/^Versions$/p' |
       grep -q 'rabbit@rabbitmq-3' && \
       printf '%s\n' "$status" |
       sed -n '/^Running Nodes$/,/^Versions$/p' |
       grep -q 'rabbit@rabbitmq-2'; then

      echo "[PASS] RabbitMQ: 3 nodes running"
      return 0
    fi

    sleep 2
  done

  echo "[FAIL] RabbitMQ cluster not ready" >&2
  return 1
}

verify() {
  echo
  echo "=== SYSTEM STATUS ==="
  bash deploy/status.sh

  echo
  echo "=== INTEGRATION VERIFICATION ==="
  bash deploy/verify.sh
}

echo "========================================="
echo " RISK ENGINE — DEMO 00 FULL START"
echo " Mode: $MODE"
echo "========================================="

[[ -f .env ]] || {
  echo "[FAIL] .env missing" >&2
  exit 1
}

"${COMPOSE[@]}" config --quiet
echo "[PASS] Compose configuration valid"

for c in "${INFRA[@]}" "${APPS[@]}" "${BACKUPS[@]}"; do
  docker inspect "$c" >/dev/null 2>&1 || {
    echo "[FAIL] Container missing: $c" >&2
    echo "This is not a fresh VM installer." >&2
    exit 1
  }
done

for c in risk-engine-migrate-1 \
         risk-engine-alert-migrate-1; do

  state="$(docker inspect \
    -f '{{.State.Status}}:{{.State.ExitCode}}' \
    "$c" 2>/dev/null || true)"

  [[ "$state" == exited:0 ]] || {
    echo "[FAIL] Migration $c: $state" >&2
    exit 1
  }
done

echo "[PASS] Containers and migrations found"

if [[ "$MODE" == "--check" ]]; then
  docker ps -a \
    --filter label=com.docker.compose.project=risk-engine \
    --format 'table {{.Names}}\t{{.Status}}'

  echo "[PASS] DEMO 00 PREFLIGHT"
  echo "No containers modified"
  exit 0
fi

if [[ "$MODE" == "--verify" ]]; then
  verify
  exit 0
fi

[[ -t 0 ]] || {
  echo "[FAIL] Interactive terminal required"
  exit 1
}

echo
echo "Existing containers will be started if stopped."
echo "No build, recreation or volume removal."
echo "Verification creates one test transaction."
echo

read -r -p "Type START to proceed: " ANSWER

[[ "$ANSWER" == START ]] || {
  echo "Cancelled"
  exit 0
}

stage "1. Coordination / databases / cache" \
  risk-engine-etcd \
  risk-engine-redis \
  risk-engine-alert-postgres

stage "2. PostgreSQL Patroni cluster" \
  risk-engine-postgres \
  risk-engine-postgres-standby

wait_pg

stage "3. PostgreSQL HAProxy" \
  risk-engine-pg-haproxy

stage "4. RabbitMQ cluster" \
  risk-engine-rabbitmq \
  risk-engine-rabbitmq-2 \
  risk-engine-rabbitmq-3

wait_mq

stage "5. RabbitMQ HAProxy" \
  risk-engine-rabbitmq-haproxy

stage "6. Webhook / workers / API" \
  "${APPS[@]}"

stage "7. Backup schedulers" \
  "${BACKUPS[@]}"

echo
echo "Waiting for FastAPI..."

READY=0

for ((i=0; i<45; i++)); do
  if curl -fsS --max-time 3 \
    http://127.0.0.1:8000/health \
    >/dev/null 2>&1; then

    READY=1
    break
  fi

  sleep 2
done

(( READY == 1 )) || {
  echo "[FAIL] API not available" >&2
  exit 1
}

echo "[PASS] FastAPI healthy"

verify

echo
echo "========================================="
echo " DEMO 00 FULL START SUCCESS"
echo "========================================="
