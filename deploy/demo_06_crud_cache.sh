#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

MODE="${1:---check}"
API_URL="http://127.0.0.1:8000"

PG_A="risk-engine-postgres"
PG_B="risk-engine-postgres-standby"
ALERT_DB="risk-engine-alert-postgres"

TMP_DIR=""

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]]; then
        rm -rf -- "$TMP_DIR"
    fi
}

trap cleanup EXIT

running() {
    [[ "$(docker inspect -f '{{.State.Running}}' \
        "$1" 2>/dev/null || true)" == "true" ]]
}

pg_query() {
    docker exec "$1" \
        psql -U "$2" -d "$3" -Atqc "$4"
}

cache_header() {
    awk 'tolower($1)=="x-cache:" {
        print toupper($2)
    }' "$1" | tr -d '\r' | tail -n 1
}

if [[ "$MODE" != "--check" && "$MODE" != "--execute" ]]; then
    fail "Usage: $0 --check|--execute"
fi

(( $# <= 1 )) || fail "Only one argument is allowed"
(( EUID == 0 )) || fail "Run with sudo bash"

[[ -z "${DOCKER_HOST:-}" &&
   -z "${DOCKER_CONTEXT:-}" ]] || \
    fail "Remote Docker is not allowed"

[[ "$(docker context show 2>/dev/null)" == "default" ]] || \
    fail "Docker context must be default"

for cmd in docker curl python3 awk timeout mktemp; do
    command -v "$cmd" >/dev/null || \
        fail "Missing command: $cmd"
done

docker info >/dev/null 2>&1 || \
    fail "Docker Engine unavailable"

log "DEMO 06 — CRUD, Redis Cache, RabbitMQ ($MODE)"

echo
echo "[1] Checking services"

for name in \
    risk-engine-api \
    "$PG_A" \
    "$PG_B" \
    risk-engine-pg-haproxy \
    risk-engine-redis \
    risk-engine-rabbitmq \
    risk-engine-rabbitmq-haproxy \
    risk-engine-outbox-publisher \
    risk-engine-alert-notifier \
    "$ALERT_DB"; do

    running "$name" || \
        fail "Container not running: $name"

    echo "[PASS] $name"
done

ROLE_A="$(pg_query "$PG_A" riskengine riskengine \
    'SELECT pg_is_in_recovery();')"

ROLE_B="$(pg_query "$PG_B" riskengine riskengine \
    'SELECT pg_is_in_recovery();')"

if [[ "$ROLE_A" == "f" && "$ROLE_B" == "t" ]]; then
    PRIMARY="$PG_A"
elif [[ "$ROLE_A" == "t" && "$ROLE_B" == "f" ]]; then
    PRIMARY="$PG_B"
else
    fail "Cannot determine Primary ($ROLE_A/$ROLE_B)"
fi

echo "Primary: $PRIMARY"

pg_query "$ALERT_DB" alertsvc alertengine \
    'SELECT 1;' >/dev/null || \
    fail "Alert PostgreSQL unavailable"

curl -fsS --max-time 8 "$API_URL/health"
echo

docker exec risk-engine-api python3 -c \
    'import socket; socket.create_connection(
        ("redis", 6379), timeout=5
    ).close()' || \
    fail "API cannot connect to Redis"

echo "[PASS] API -> Redis:6379"

timeout 25 docker exec risk-engine-rabbitmq \
    rabbitmqctl list_queues \
    -p risk_engine \
    name type messages_ready consumers || \
    fail "RabbitMQ queues unavailable"

if [[ "$MODE" == "--check" ]]; then
    echo
    echo "========================================="
    echo " DEMO 06 PREFLIGHT PASSED"
    echo " No changes were made"
    echo "========================================="
    exit 0
fi

[[ -t 0 ]] || \
    fail "Interactive terminal required"

echo
echo "A test transaction and Outbox event will be created."
echo "SQL CRUD will use a temporary PostgreSQL table."
echo "Existing transaction records will not be modified."
echo

read -r -p "Type CRUD to continue: " ANSWER

[[ "$ANSWER" == "CRUD" ]] || fail "Cancelled"

TMP_DIR="$(mktemp -d /tmp/risk-engine-demo06.XXXXXX)"

TX_ID="demo06-$(date -u +%Y%m%d%H%M%S)-$$"

PAYLOAD="$(printf \
'{"transaction_id":"%s","amount":2700,"payer_key":"demo06@example.test","receiver_key":"receiver@example.test","transaction_type":"PIX"}' \
"$TX_ID")"

# ---------------------------------------------
# STEP 2: CREATE TRANSACTION
# ---------------------------------------------

log "[2] CREATE: POST /transactions"

curl -fsS --max-time 20 \
    -X POST "$API_URL/transactions" \
    -H 'Content-Type: application/json' \
    --data "$PAYLOAD" \
    -o "$TMP_DIR/post.json" || \
    fail "POST /transactions failed"

python3 - "$TMP_DIR/post.json" "$TX_ID" <<'PY_POST'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

assert data.get("transaction_id") == sys.argv[2]
assert data.get("idempotent") is False

assessment = data.get("risk_assessment") or {}

assert "fraud_score" in assessment
assert "risk_level" in assessment

print("[PASS] Transaction:", data["transaction_id"])
print("       fraud_score:", assessment["fraud_score"])
print("       risk_level:", assessment["risk_level"])
PY_POST

COUNT="$(pg_query "$PRIMARY" riskengine riskengine \
    "SELECT COUNT(*) FROM transactions
     WHERE transaction_id='$TX_ID';")"

[[ "$COUNT" == "1" ]] || \
    fail "Transaction not found in PostgreSQL"

echo "[PASS] PostgreSQL INSERT confirmed"

# ---------------------------------------------
# STEP 3: REDIS CACHE-ASIDE
# ---------------------------------------------

log "[3] READ: Redis Cache-Aside MISS -> HIT"

curl -fsS --max-time 15 \
    -D "$TMP_DIR/headers1" \
    -o "$TMP_DIR/get1.json" \
    "$API_URL/transactions/$TX_ID" || \
    fail "First GET failed"

curl -fsS --max-time 15 \
    -D "$TMP_DIR/headers2" \
    -o "$TMP_DIR/get2.json" \
    "$API_URL/transactions/$TX_ID" || \
    fail "Second GET failed"

FIRST="$(cache_header "$TMP_DIR/headers1")"
SECOND="$(cache_header "$TMP_DIR/headers2")"

echo "First GET  X-Cache: ${FIRST:-missing}"
echo "Second GET X-Cache: ${SECOND:-missing}"

[[ "$FIRST" == "MISS" ]] || \
    fail "Expected X-Cache MISS"

[[ "$SECOND" == "HIT" ]] || \
    fail "Expected X-Cache HIT"

python3 - \
    "$TMP_DIR/get1.json" \
    "$TMP_DIR/get2.json" \
    "$TX_ID" <<'PY_GET'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    first = json.load(f)

with open(sys.argv[2], encoding="utf-8") as f:
    second = json.load(f)

assert first.get("transaction_id") == sys.argv[3]
assert second.get("transaction_id") == sys.argv[3]

assert (
    first.get("risk_assessment")
    == second.get("risk_assessment")
)

print("[PASS] Both GET requests returned same transaction")
PY_GET

echo "[PASS] Redis Cache-Aside: MISS -> HIT"

# ---------------------------------------------
# STEP 4: IDEMPOTENCY
# ---------------------------------------------

log "[4] Testing idempotent POST"

curl -fsS --max-time 20 \
    -X POST "$API_URL/transactions" \
    -H 'Content-Type: application/json' \
    --data "$PAYLOAD" \
    -o "$TMP_DIR/retry.json" || \
    fail "Repeated POST failed"

python3 - \
    "$TMP_DIR/post.json" \
    "$TMP_DIR/retry.json" <<'PY_REPEAT'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    original = json.load(f)

with open(sys.argv[2], encoding="utf-8") as f:
    repeated = json.load(f)

assert repeated.get("idempotent") is True
assert original["transaction_id"] == repeated["transaction_id"]

assert (
    original["risk_assessment"]
    == repeated["risk_assessment"]
)

assert original["created_at"] == repeated["created_at"]

print("[PASS] Repeated POST: idempotent=true")
PY_REPEAT

COUNT="$(pg_query "$PRIMARY" riskengine riskengine \
    "SELECT COUNT(*) FROM transactions
     WHERE transaction_id='$TX_ID';")"

[[ "$COUNT" == "1" ]] || \
    fail "Duplicate transactions detected"

OUTBOX_COUNT="$(pg_query "$PRIMARY" riskengine riskengine \
    "SELECT COUNT(*) FROM outbox_events
     WHERE aggregate_id='$TX_ID';")"

[[ "$OUTBOX_COUNT" == "1" ]] || \
    fail "Expected exactly one Outbox event"

echo "[PASS] One transaction and one Outbox event"

# ---------------------------------------------
# STEP 5: RABBITMQ / EVENT PROCESSING
# ---------------------------------------------

log "[5] RabbitMQ -> Alert Notifier -> Alert DB"

PROCESSED=0

for ((i=1; i<=45; i++)); do

    COUNT="$(timeout 15 docker exec "$ALERT_DB" \
        psql -U alertsvc -d alertengine -Atqc \
        "SELECT COUNT(*) FROM processed_events
         WHERE transaction_id='$TX_ID';" \
        2>/dev/null || true)"

    if [[ "$COUNT" == "1" ]]; then
        PROCESSED=1
        break
    fi

    sleep 1
done

(( PROCESSED == 1 )) || \
    fail "Event was not processed"

echo "[PASS] Transaction -> Outbox -> RabbitMQ -> Notifier"
echo "[PASS] Event found in processed_events"

# ---------------------------------------------
# STEP 6: SQL CRUD
# ---------------------------------------------

log "[6] SQL CRUD using a temporary PostgreSQL table"

docker exec -i "$PRIMARY" \
    psql -X \
    -v ON_ERROR_STOP=1 \
    -U riskengine \
    -d riskengine \
    -P pager=off <<'SQL_CRUD'

BEGIN;

CREATE TEMP TABLE demo06_crud (
    id integer PRIMARY KEY,
    description text NOT NULL
) ON COMMIT DROP;

-- CREATE
INSERT INTO demo06_crud (id, description)
VALUES (1, 'created')
RETURNING *;

-- READ
SELECT *
FROM demo06_crud
WHERE id = 1;

DO $verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM demo06_crud
        WHERE id = 1 AND description = 'created'
    ) THEN
        RAISE EXCEPTION 'CREATE/READ check failed';
    END IF;
END
$verify$;

-- UPDATE
UPDATE demo06_crud
SET description = 'updated'
WHERE id = 1
RETURNING *;

DO $verify$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM demo06_crud
        WHERE id = 1 AND description = 'updated'
    ) THEN
        RAISE EXCEPTION 'UPDATE check failed';
    END IF;
END
$verify$;

-- DELETE
DELETE FROM demo06_crud
WHERE id = 1
RETURNING *;

DO $verify$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM demo06_crud
        WHERE id = 1
    ) THEN
        RAISE EXCEPTION 'DELETE check failed';
    END IF;
END
$verify$;

SELECT COUNT(*) AS remaining_rows
FROM demo06_crud;

COMMIT;

SQL_CRUD

echo "[PASS] SQL INSERT, SELECT, UPDATE, DELETE"

echo
echo "========================================="
echo " DEMO 06 SUCCESS"
echo " POST / GET / MISS -> HIT"
echo " IDEMPOTENCY VERIFIED"
echo " OUTBOX -> RABBITMQ -> NOTIFIER"
echo " SQL CRUD VERIFIED"
echo " Transaction: $TX_ID"
echo "========================================="
