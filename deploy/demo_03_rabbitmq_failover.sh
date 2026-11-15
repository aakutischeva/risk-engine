#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

MODE="${1:---check}"
NODES=(risk-engine-rabbitmq risk-engine-rabbitmq-2 risk-engine-rabbitmq-3)
VHOST="risk_engine"
QUEUE="risk.alerts"
STOPPED=""
ORIGINAL_LEADER=""
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

ready() {
    running "$1" && timeout 15 docker exec "$1" \
        rabbitmq-diagnostics -q check_running >/dev/null 2>&1
}

pick_cli_node() {
    local node
    for node in "${NODES[@]}"; do
        if ready "$node"; then
            printf '%s\n' "$node"
            return 0
        fi
    done
    return 1
}

quorum_status() {
    local cli
    cli="$(pick_cli_node)" || return 1

    timeout 25 docker exec "$cli" rabbitmq-queues \
        quorum_status --vhost "$VHOST" "$1"
}

queue_leader() {
    awk 'tolower($0) ~ /leader/ && /rabbit@rabbitmq/ {
        if (match($0, /rabbit@rabbitmq(-2|-3)?/)) {
            print substr($0, RSTART, RLENGTH)
            exit
        }
    }'
}

container_for() {
    case "$1" in
        rabbit@rabbitmq)   echo risk-engine-rabbitmq ;;
        rabbit@rabbitmq-2) echo risk-engine-rabbitmq-2 ;;
        rabbit@rabbitmq-3) echo risk-engine-rabbitmq-3 ;;
        *) return 1 ;;
    esac
}

check_cluster() {
    local cli status running_section node

    cli="$(pick_cli_node)" || return 1

    status="$(timeout 25 docker exec "$cli" \
        rabbitmqctl cluster_status)" || return 1

    running_section="$(printf '%s\n' "$status" |
        sed -n '/^Running Nodes$/,/^Versions$/p')"

    for node in rabbit@rabbitmq rabbit@rabbitmq-2 rabbit@rabbitmq-3; do
        printf '%s\n' "$running_section" |
            grep -Fxq "$node" || return 1
    done
}

check_queue() {
    local info leader voters

    info="$(quorum_status "$1")" || return 1
    leader="$(printf '%s\n' "$info" | queue_leader)"
    voters="$(printf '%s\n' "$info" | grep -ic 'voter' || true)"

    [[ -n "$leader" && "$voters" -eq 3 ]] || return 1

    printf '%s\n' "$info"
}

restore_node() {
    (( STOP_ATTEMPTED == 1 )) || return 0

    log "Возвращаем узел $STOPPED"

    if ! running "$STOPPED"; then
        docker start "$STOPPED" || return 1
    fi

    local deadline=$((SECONDS + 150))

    while (( SECONDS < deadline )); do
        if ready "$STOPPED" && check_cluster && \
           check_queue "$QUEUE" >/dev/null && \
           check_queue "risk.alerts.dead" >/dev/null; then

            echo "[PASS] 3/3 RabbitMQ nodes, обе Quorum Queue доступны"

            quorum_status "$QUEUE" || true
            return 0
        fi

        sleep 3
    done

    echo "[WARN] Полное восстановление кластера не подтверждено" >&2
    echo "Проверь: sudo docker logs --tail 100 $STOPPED" >&2
    return 1
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM

    if (( STOP_ATTEMPTED == 1 )); then
        restore_node || rc=1

        if (( rc == 0 )); then
            echo '[PASS] DEMO 03 SUCCESS: leader changed, event processed, node rejoined'
        else
            echo '[WARN] DEMO 03 requires inspection' >&2
        fi
    fi

    exit "$rc"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

send_transaction() {
    local tx_id="$1" payload response count deadline

    payload="{\"transaction_id\":\"$tx_id\",\"amount\":2700,\"payer_key\":\"rabbit-demo@example.test\",\"receiver_key\":\"receiver@example.test\",\"transaction_type\":\"PIX\"}"

    response="$(curl -fsS --max-time 20 \
        -H 'Content-Type: application/json' \
        -X POST http://127.0.0.1:8000/transactions \
        -d "$payload")" || return 1

    printf '%s' "$response" | python3 -c '
import json, sys
result = json.load(sys.stdin)
assert result.get("transaction_id") == sys.argv[1], "wrong transaction id"
assert "risk_assessment" in result, "risk assessment missing"
' "$tx_id" || return 1

    echo "[PASS] POST /transactions: $tx_id"

    deadline=$((SECONDS + 100))

    while (( SECONDS < deadline )); do
        count="$(timeout 15 docker exec risk-engine-alert-postgres \
            psql -U alertsvc -d alertengine -Atqc \
            "SELECT COUNT(*) FROM processed_events WHERE transaction_id='$tx_id';" \
            2>/dev/null || true)"

        if [[ "$count" == "1" ]]; then
            echo "[PASS] Сообщение обработано: processed_events содержит $tx_id"
            return 0
        fi

        sleep 2
    done

    return 1
}

if [[ "$MODE" != "--check" && "$MODE" != "--execute" ]]; then
    fail "Использование: sudo bash deploy/demo_03_rabbitmq_failover.sh --check|--execute"
fi

(( EUID == 0 )) || fail 'Запускай через sudo bash ...'

[[ -z "${DOCKER_HOST:-}" && -z "${DOCKER_CONTEXT:-}" ]] || \
    fail 'Удалённый Docker запрещён'

[[ "$(docker context show 2>/dev/null)" == default ]] || \
    fail 'Docker context должен быть default'

command -v curl >/dev/null && \
command -v timeout >/dev/null && \
command -v python3 >/dev/null || \
    fail 'Нужны curl, timeout и python3'

docker info >/dev/null 2>&1 || fail 'Docker Engine недоступен'

log "DEMO 03 — RabbitMQ Quorum Queue failover ($MODE)"

for name in "${NODES[@]}" risk-engine-rabbitmq-haproxy \
            risk-engine-outbox-publisher risk-engine-alert-notifier \
            risk-engine-api risk-engine-alert-postgres; do

    running "$name" || fail "Контейнер не запущен: $name"
    echo "[PASS] $name"
done

log '[1] Проверка кластера и брокерных узлов'

for name in "${NODES[@]}"; do
    ready "$name" || fail "RabbitMQ не отвечает: $name"
done

check_cluster || fail 'Ожидались три работающих узла RabbitMQ'

echo '[PASS] 3/3 RabbitMQ nodes running'

log '[2] Проверка Quorum Queue'

MAIN_STATUS="$(check_queue "$QUEUE")" || \
    fail "Недоступна очередь $QUEUE или нет 3 voters"

DEAD_STATUS="$(check_queue risk.alerts.dead)" || \
    fail 'Недоступна очередь risk.alerts.dead'

printf '%s\n' "$MAIN_STATUS"

ORIGINAL_LEADER="$(printf '%s\n' "$MAIN_STATUS" | queue_leader)"

STOPPED="$(container_for "$ORIGINAL_LEADER")" || \
    fail 'Не удалось определить контейнер лидера'

echo "Leader ($QUEUE): $ORIGINAL_LEADER -> $STOPPED"
echo '[PASS] Обе очереди: 3 voters / 1 leader'

log '[3] Проверка доступа к RabbitMQ HAProxy'

docker exec risk-engine-outbox-publisher python3 -c \
    'import socket; socket.create_connection(("rabbitmq-haproxy", 5672), timeout=5).close()' \
    || fail 'Publisher не подключается к RabbitMQ HAProxy'

echo '[PASS] rabbitmq-haproxy:5672 reachable'

if [[ "$MODE" == '--check' ]]; then
    echo '[PASS] PREFLIGHT PASSED: контейнеры и данные не изменены'
    exit 0
fi

[[ -t 0 ]] || fail 'Для --execute нужен интерактивный терминал'

echo
printf 'Будет аварийно остановлен %s (лидер %s).\n' \
    "$STOPPED" "$QUEUE"

echo 'Временно прервутся соединения; возможны повторная доставка и задержки.'

read -r -p 'Для продолжения введи RABBIT_FAILOVER: ' ANSWER

[[ "$ANSWER" == 'RABBIT_FAILOVER' ]] || fail 'Отмена'

log '[4] Проверка обработки до отказа'

send_transaction \
    "demo-rabbit-before-$(date -u +%Y%m%d%H%M%S)-$$" || \
    fail 'Сквозной тест до отказа не прошёл'

log '[5] Аварийная остановка лидера'

CURRENT_STATUS="$(quorum_status "$QUEUE")" || \
    fail 'Не удалось перепроверить лидера'

[[ "$(printf '%s\n' "$CURRENT_STATUS" | queue_leader)" == "$ORIGINAL_LEADER" ]] || \
    fail 'Лидер очереди изменился до аварии: повтори проверку'

STOP_ATTEMPTED=1
START_TS=$(date +%s)

docker stop --signal SIGKILL --timeout 0 \
    "$STOPPED" >/dev/null || \
    fail 'Не удалось остановить узел RabbitMQ'

echo "[PASS] Остановлен $STOPPED"

log '[6] Ожидание нового лидера Quorum Queue'

NEW_LEADER=''
deadline=$((SECONDS + 120))

while (( SECONDS < deadline )); do
    status="$(quorum_status "$QUEUE" 2>/dev/null || true)"
    candidate="$(printf '%s\n' "$status" | queue_leader)"

    if [[ -n "$candidate" && "$candidate" != "$ORIGINAL_LEADER" ]]; then
        NEW_LEADER="$candidate"
        break
    fi

    sleep 3
done

[[ -n "$NEW_LEADER" ]] || \
    fail 'Не удалось подтвердить избрание нового лидера'

echo "[PASS] Новый лидер: $NEW_LEADER"
echo "Переключение обнаружено за $(($(date +%s) - START_TS)) сек."

log '[7] Проверка доставки сообщений при одном отказавшем узле'

send_transaction \
    "demo-rabbit-after-$(date -u +%Y%m%d%H%M%S)-$$" || \
    fail 'Обработка событий не восстановилась после отказа'

echo '[PASS] API -> Outbox -> RabbitMQ -> Notifier -> Alert DB'

log '[8] Возврат остановленного узла'
echo 'Автоматический запуск и контроль восстановления выполняются при выходе.'
