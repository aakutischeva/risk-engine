#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

echo "========================================"
echo "       RISK ENGINE — SYSTEM STATUS"
echo "========================================"

echo
echo "=== 1. DOCKER CONTAINERS ==="
sudo docker ps -a \
  --filter label=com.docker.compose.project=risk-engine \
  --format 'table {{.Names}}\t{{.Status}}'

echo
echo "=== 2. FASTAPI ==="
if curl -fsS --max-time 5 http://127.0.0.1:8000/health; then
    echo
    echo "[PASS] API is available"
else
    echo "[FAIL] API is unavailable"
fi

echo
echo "=== 3. POSTGRESQL HA ROLES ==="

for NODE in risk-engine-postgres risk-engine-postgres-standby; do
    printf "%s: " "$NODE"

    sudo docker exec "$NODE" \
      psql -U riskengine -d riskengine -Atqc \
      "SELECT CASE WHEN pg_is_in_recovery()
       THEN 'REPLICA' ELSE 'PRIMARY' END;" \
      2>/dev/null || echo "UNAVAILABLE"
done

echo
echo "=== 4. POSTGRESQL REPLICATION ==="

for NODE in risk-engine-postgres risk-engine-postgres-standby; do
    sudo docker exec "$NODE" \
      psql -U riskengine -d riskengine -Atqc \
      "SELECT application_name || ': ' || state ||
       ' (' || sync_state || ')'
       FROM pg_stat_replication;" 2>/dev/null || true
done

echo
echo "=== 5. RABBITMQ CLUSTER ==="

timeout 20s sudo docker exec risk-engine-rabbitmq-2 \
  rabbitmqctl cluster_status 2>/dev/null \
  | sed -n '/^Running Nodes$/,/^Versions$/p' \
  || echo "RabbitMQ cluster check unavailable"

echo
echo "=== 6. RABBITMQ QUEUES ==="

timeout 20s sudo docker exec risk-engine-rabbitmq-2 \
  rabbitmqctl list_queues -p risk_engine \
  name type messages_ready consumers \
  2>/dev/null || echo "RabbitMQ queue check unavailable"

echo
echo "=== STATUS CHECK FINISHED ==="
