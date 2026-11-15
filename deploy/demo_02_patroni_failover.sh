#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."
MODE="${1:---check}"
NODE_A="risk-engine-postgres"
NODE_B="risk-engine-postgres-standby"
OLD_PRIMARY=""
NEW_PRIMARY=""
STOP_ATTEMPTED=0

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

running() {
    [[ "$(docker inspect -f '{{.State.Running}}' \
        "$1" 2>/dev/null || true)" == "true" ]]
}

sql() {
    docker exec "$1" \
        psql -U riskengine -d riskengine -Atqc "$2" \
        2>/dev/null
}

role() {
    sql "$1" 'SELECT pg_is_in_recovery();' |
        tr -d '[:space:]'
}

recover_old_primary() {
    (( STOP_ATTEMPTED == 1 )) || return 0

    log "Возвращаем остановленный узел: $OLD_PRIMARY"

    if ! running "$OLD_PRIMARY"; then
        if ! docker start "$OLD_PRIMARY"; then
            echo "[FAIL] Не удалось запустить $OLD_PRIMARY" >&2
            return 1
        fi
    fi

    local i r receiver

    for ((i=1; i<=60; i++)); do
        r="$(role "$OLD_PRIMARY" || true)"

        receiver="$(sql "$OLD_PRIMARY" \
            'SELECT status FROM pg_stat_wal_receiver LIMIT 1;' \
            || true)"

        if [[ "$r" == "t" && "$receiver" == "streaming" ]]; then
            echo "[PASS] Бывший Primary вернулся как Replica (streaming)"
            return 0
        fi

        sleep 2
    done

    echo "[WARN] Узел запущен, но присоединение как Replica не подтверждено." >&2
    echo "Проверь: sudo docker logs --tail 100 $OLD_PRIMARY" >&2
    return 1
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM

    if (( STOP_ATTEMPTED == 1 )); then
        recover_old_primary || rc=1

        docker exec "$NEW_PRIMARY" patronictl \
            -c /etc/patroni/patroni.yml list \
            2>/dev/null || true

        if (( rc == 0 )); then
            echo "[PASS] DEMO 02 SUCCESS: failover, API write, node rejoin"
        else
            echo "[WARN] DEMO 02 requires inspection" >&2
        fi
    fi

    exit "$rc"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$MODE" != "--check" && "$MODE" != "--execute" ]]; then
    echo "Usage: sudo bash deploy/demo_02_patroni_failover.sh --check|--execute"
    exit 2
fi

[[ $EUID -eq 0 ]] || fail "Запусти через sudo bash ..."

[[ -z "${DOCKER_HOST:-}" && -z "${DOCKER_CONTEXT:-}" ]] || \
    fail "Задан удаленный Docker host/context"

[[ "$(docker context show 2>/dev/null)" == "default" ]] || \
    fail "Docker context не default"

command -v curl >/dev/null || fail "curl не установлен"

docker info >/dev/null 2>&1 || fail "Docker Engine недоступен"

log "DEMO 02 — Patroni failover ($MODE)"

echo
echo "[1] Проверка контейнеров"

for c in "$NODE_A" "$NODE_B" risk-engine-etcd \
         risk-engine-pg-haproxy risk-engine-api; do

    running "$c" || fail "Контейнер не работает: $c"
    echo "[PASS] $c"
done

echo
echo "[2] Определение Primary и Replica"

RA="$(role "$NODE_A")"
RB="$(role "$NODE_B")"

if [[ "$RA" == "f" && "$RB" == "t" ]]; then
    OLD_PRIMARY="$NODE_A"
    NEW_PRIMARY="$NODE_B"
elif [[ "$RA" == "t" && "$RB" == "f" ]]; then
    OLD_PRIMARY="$NODE_B"
    NEW_PRIMARY="$NODE_A"
else
    fail "Некорректные роли: $NODE_A=$RA, $NODE_B=$RB"
fi

echo "Primary: $OLD_PRIMARY"
echo "Replica: $NEW_PRIMARY"

echo
echo "[3] Проверка репликации"

STREAMS="$(sql "$OLD_PRIMARY" \
    "SELECT COUNT(*) FROM pg_stat_replication WHERE state='streaming';")"

[[ "$STREAMS" == "1" ]] || fail "Нет одной streaming-реплики"

[[ "$(sql "$NEW_PRIMARY" \
    'SELECT status FROM pg_stat_wal_receiver LIMIT 1;')" == "streaming" ]] || \
    fail "Replica не получает WAL"

LAG="$(sql "$OLD_PRIMARY" \
    "SELECT COALESCE(MAX(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)),0)::bigint FROM pg_stat_replication WHERE state='streaming';")"

echo "[PASS] WAL streaming"
echo "WAL replay lag: $LAG bytes"

docker exec "$OLD_PRIMARY" patronictl \
    -c /etc/patroni/patroni.yml list

echo
echo "[4] Проверка HAProxy и API"

docker exec risk-engine-pg-haproxy \
    grep -q 'httpchk GET /primary' \
    /usr/local/etc/haproxy/haproxy.cfg || \
    fail "HAProxy не проверяет /primary"

DB_HOST="$(docker exec risk-engine-api python -c \
    'import os; from urllib.parse import urlsplit; print(urlsplit(os.getenv("DATABASE_URL", "")).hostname or "")')"

[[ "$DB_HOST" == "pg-haproxy" ]] || \
    fail "API не использует pg-haproxy"

curl -fsS --max-time 10 \
    http://127.0.0.1:8000/health

echo
echo "[PASS] Предварительные проверки завершены"

if [[ "$MODE" == "--check" ]]; then
    echo "Никаких изменений не производилось."
    exit 0
fi

echo
echo "WARNING: Будет аварийно остановлен $OLD_PRIMARY"
echo "Возможны кратковременный простой и потеря последних записей."
echo "Выполняй только на учебном стенде без важных операций."

[[ -t 0 ]] || fail "Для --execute нужен терминал"

read -r -p "Для продолжения введи FAILOVER: " ANSWER

[[ "$ANSWER" == "FAILOVER" ]] || fail "Отменено"

echo
echo "[5] Создание резервной копии"

umask 077
BACKUP_DIR="$PWD/backups/demo"

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/riskengine_pre_failover_$(date -u +%Y%m%d_%H%M%S).dump"

log "Создаем backup: $BACKUP_FILE"

docker exec "$OLD_PRIMARY" \
    pg_dump -U riskengine -d riskengine -Fc \
    > "$BACKUP_FILE" || fail "Ошибка pg_dump"

[[ -s "$BACKUP_FILE" ]] || fail "Резервная копия пуста"

docker exec -i "$OLD_PRIMARY" pg_restore -l \
    < "$BACKUP_FILE" > /dev/null || \
    fail "Архив не прошел проверку pg_restore -l"

echo "[PASS] Backup создан и проверен"

echo
echo "[6] Создание тестовой транзакции"

TX_ID="demo-patroni-$(date -u +%Y%m%d%H%M%S)-$$"

PAYLOAD="{\"transaction_id\":\"$TX_ID\",\"amount\":2700,\"payer_key\":\"patroni-demo@example.test\",\"receiver_key\":\"receiver@example.test\",\"transaction_type\":\"PIX\"}"

curl -fsS --max-time 20 \
    -H 'Content-Type: application/json' \
    -X POST http://127.0.0.1:8000/transactions \
    -d "$PAYLOAD" > /dev/null || \
    fail "POST /transactions до отказа не удался"

echo "[PASS] Транзакция создана: $TX_ID"

echo
echo "[7] Проверка появления транзакции на Replica"

REPLAYED=0

for ((i=1; i<=30; i++)); do
    COUNT="$(sql "$NEW_PRIMARY" \
        "SELECT COUNT(*) FROM transactions WHERE transaction_id='$TX_ID';" \
        || true)"

    if [[ "$COUNT" == "1" ]]; then
        REPLAYED=1
        break
    fi

    sleep 2
done

(( REPLAYED == 1 )) || \
    fail "Транзакция не появилась на Replica"

echo "[PASS] Данные получены Replica"

echo
echo "[8] Аварийное завершение Primary"

STOP_ATTEMPTED=1
START_TIME=$(date +%s)

docker stop --signal SIGKILL --timeout 0 \
    "$OLD_PRIMARY" > /dev/null || \
    fail "Остановить Primary не удалось"

echo "[PASS] Primary остановлен"

echo
echo "[9] Ожидание автоматического failover"

PROMOTED=0

for ((i=1; i<=60; i++)); do

    if [[ "$(role "$NEW_PRIMARY" || true)" == "f" ]]; then
        PROMOTED=1
        break
    fi

    sleep 2
done

(( PROMOTED == 1 )) || \
    fail "Failover не произошел за время проверки"

ELAPSED=$(($(date +%s) - START_TIME))

echo "[PASS] Новый Primary: $NEW_PRIMARY"
echo "Время переключения: ${ELAPSED} секунд"

echo
echo "[10] Проверка записи через HAProxy"

AFTER_ID="${TX_ID}-after"
AFTER_PAYLOAD="${PAYLOAD//$TX_ID/$AFTER_ID}"

WRITE_OK=0

for ((i=1; i<=12; i++)); do

    if curl -fsS --max-time 10 \
        -H 'Content-Type: application/json' \
        -X POST http://127.0.0.1:8000/transactions \
        -d "$AFTER_PAYLOAD" >/dev/null 2>&1; then

        WRITE_OK=1
        break
    fi

    sleep 5
done

(( WRITE_OK == 1 )) || \
    fail "Запись через API после failover не восстановилась"

echo "[PASS] API -> HAProxy -> новый Primary работает"

echo
echo "[11] Возврат старого Primary"

log "Основной тест завершен"
log "На выходе скрипт вернет старый Primary как Replica"
