#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

MODE="${1:---check}"
DEMO_CHAIN="RISK_DEMO04"

API="risk-engine-api"
REDIS="risk-engine-redis"
ALERT_DB="risk-engine-alert-postgres"

CLEANUP_NEEDED=0

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

network_ip() {
    docker inspect \
        -f '{{json .NetworkSettings.Networks}}' "$1" |
        python3 -c '
import json, sys

networks = json.load(sys.stdin)
suffix = "_" + sys.argv[1]

found = [
    (name, info.get("IPAddress", ""))
    for name, info in networks.items()
    if name.endswith(suffix)
]

if len(found) != 1 or not found[0][1]:
    raise SystemExit("Network/IP not found: " + suffix)

print(found[0][0] + "|" + found[0][1])
' "$2"
}

# Запуск iptables в сетевом пространстве API.
ns() {
    nsenter -t "$API_PID" -n iptables -w 5 "$@"
}

# Удаляем только цепочку, принадлежащую DEMO 04.
remove_demo_chain() {
    if ! ns -S "$DEMO_CHAIN" >/dev/null 2>&1; then
        return 0
    fi

    while ns -C OUTPUT -j "$DEMO_CHAIN" \
        >/dev/null 2>&1; do

        ns -D OUTPUT -j "$DEMO_CHAIN" || return 1
    done

    ns -F "$DEMO_CHAIN" || return 1
    ns -X "$DEMO_CHAIN" || return 1

    ! ns -S "$DEMO_CHAIN" >/dev/null 2>&1
}

# Аварийная очистка при завершении скрипта.
finish() {
    local rc=$?

    trap - EXIT INT TERM

    if (( CLEANUP_NEEDED )); then
        log "Автоматическое удаление временного правила"

        if remove_demo_chain; then
            echo "[PASS] Временный запрет удалён"
        else
            echo "[FAIL] Автоматическая очистка не удалась" >&2
            echo "Выполни --cleanup вручную" >&2
            rc=1
        fi
    fi

    exit "$rc"
}

# Проверка нового TCP-соединения.
check_tcp() {
    docker exec "$1" python3 -c '
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])

try:
    sock = socket.create_connection(
        (host, port),
        timeout=3
    )
except OSError as exc:
    print(f"CONNECT FAILED {host}:{port}: {exc}")
    sys.exit(1)
else:
    sock.close()
    print(f"CONNECTED {host}:{port}")
' "$2" "$3"
}

case "$MODE" in
    --check|--execute|--cleanup) ;;
    *) fail "Usage: $0 --check|--execute|--cleanup" ;;
esac

(( $# <= 1 )) || fail "Укажи один режим"
(( EUID == 0 )) || fail "Запускай через sudo bash"

for cmd in docker iptables nsenter python3 curl; do
    command -v "$cmd" >/dev/null || \
        fail "Не найдена команда: $cmd"
done

[[ -z "${DOCKER_HOST:-}" &&
   -z "${DOCKER_CONTEXT:-}" ]] || \
    fail "Удалённый Docker запрещён"

[[ "$(docker context show 2>/dev/null)" == "default" ]] || \
    fail "Docker context должен быть default"

docker info >/dev/null 2>&1 || \
    fail "Docker Engine недоступен"

running "$API" || fail "API не запущен"

API_PID="$(docker inspect \
    -f '{{.State.Pid}}' "$API")"

[[ "$API_PID" =~ ^[0-9]+$ ]] &&
    (( API_PID > 1 )) || fail "PID API не найден"

# Исключаем случайное изменение firewall хоста.
[[ "$(readlink "/proc/$API_PID/ns/net")" != \
   "$(readlink /proc/1/ns/net)" ]] || \
    fail "API использует сетевое пространство хоста"

ns -S OUTPUT >/dev/null || \
    fail "Нет доступа к iptables API"

# Отдельный режим аварийной очистки.
if [[ "$MODE" == "--cleanup" ]]; then
    log "Очистка DEMO 04"

    remove_demo_chain || fail "Ошибка очистки"

    echo "[PASS] Временное правило отсутствует"
    exit 0
fi

running "$REDIS" || fail "Redis не запущен"
running "$ALERT_DB" || fail "Alert PostgreSQL не запущен"

PAIR="$(network_ip "$API" tx_cache)" || \
    fail "Сеть API не найдена"

IFS='|' read -r CACHE_NETWORK API_IP <<< "$PAIR"

PAIR="$(network_ip "$REDIS" tx_cache)" || \
    fail "Сеть Redis не найдена"

IFS='|' read -r REDIS_NETWORK REDIS_IP <<< "$PAIR"

[[ "$CACHE_NETWORK" == "$REDIS_NETWORK" ]] || \
    fail "API и Redis находятся в разных сетях"

BRIDGE="$(docker network inspect "$CACHE_NETWORK" \
    -f '{{index .Options "com.docker.network.bridge.name"}}')"

[[ "$BRIDGE" == "br-re-cache" ]] || \
    fail "Неожиданный bridge: $BRIDGE"

PAIR="$(network_ip "$ALERT_DB" alert_db)" || \
    fail "Сеть Alert PostgreSQL не найдена"

IFS='|' read -r ALERT_NETWORK ALERT_IP <<< "$PAIR"

echo "========================================="
echo " RISK ENGINE — FIREWALL DEMONSTRATION"
echo "========================================="

echo "Mode:      $MODE"
echo "API:       $API_IP"
echo "Redis:     $REDIS_IP"
echo "Alert DB:  $ALERT_IP"
echo "Bridge:    $BRIDGE"

log "[1] Проверка постоянных правил Firewall"

iptables -w 5 -C DOCKER-USER \
    -j RISK_ENGINE_SEG || \
    fail "Нет перехода к RISK_ENGINE_SEG"

echo
echo "=== DOCKER-USER ==="
iptables -w 5 -S DOCKER-USER

echo
echo "=== RISK_ENGINE_SEG ==="
iptables -w 5 -S RISK_ENGINE_SEG || \
    fail "Нет правил сегментации"

echo
echo "=== raw PREROUTING ==="
iptables -w 5 -t raw -S PREROUTING

if ns -S "$DEMO_CHAIN" >/dev/null 2>&1; then
    fail "Осталась тестовая цепочка. Выполни --cleanup"
fi

log "[2] Разрешённое соединение API -> Redis"

check_tcp "$API" "$REDIS_IP" 6379 || \
    fail "Соединение с Redis должно проходить"

echo "[PASS] API -> Redis разрешено"

log "[3] Запрещённое соединение API -> Alert PostgreSQL"

if check_tcp "$API" "$ALERT_IP" 5432; then
    fail "API смог подключиться к чужой базе"
fi

echo "[PASS] API -> Alert PostgreSQL запрещено"

curl -fsS --max-time 5 \
    http://127.0.0.1:8000/health >/dev/null || \
    fail "API /health недоступен"

if [[ "$MODE" == "--check" ]]; then
    echo
    echo "========================================="
    echo " DEMO 04 PREFLIGHT PASSED"
    echo " No changes were made"
    echo "========================================="
    exit 0
fi

[[ -t 0 ]] || fail "Нужен интерактивный терминал"

echo
echo "WARNING: временно блокируется API -> Redis."
echo "Постоянные правила Firewall не меняются."
echo "Возможны ошибки операций, использующих Redis."
echo

read -r -p "Type FIREWALL to continue: " ANSWER

[[ "$ANSWER" == "FIREWALL" ]] || fail "Отменено"

# Обработчики устанавливаем ДО изменения правил.
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CLEANUP_NEEDED=1

log "[4] Установка временного запрещающего правила"

ns -N "$DEMO_CHAIN"

ns -A "$DEMO_CHAIN" \
    -d "$REDIS_IP" \
    -p tcp --dport 6379 \
    -j REJECT --reject-with tcp-reset

ns -I OUTPUT 1 -j "$DEMO_CHAIN"

echo
echo "=== TEMPORARY FIREWALL RULE ==="
ns -L "$DEMO_CHAIN" -n -v -x

log "[5] Проверяем отказ соединения API -> Redis"

if check_tcp "$API" "$REDIS_IP" 6379; then
    fail "Блокировка не сработала"
fi

PKTS="$(ns -L "$DEMO_CHAIN" -n -v -x |
    awk '$3=="REJECT" {print $1; exit}')"

[[ "$PKTS" =~ ^[0-9]+$ ]] &&
    (( PKTS > 0 )) || \
    fail "Счётчик блокировки не увеличился"

echo "[PASS] Соединение заблокировано"
echo "[PASS] Пакетов заблокировано: $PKTS"

log "[6] Удаляем временное правило"

remove_demo_chain || \
    fail "Не удалось удалить правило"

CLEANUP_NEEDED=0

log "[7] Проверяем восстановление соединения"

check_tcp "$API" "$REDIS_IP" 6379 || \
    fail "Соединение с Redis не восстановилось"

curl -fsS --max-time 5 \
    http://127.0.0.1:8000/health >/dev/null || \
    fail "API недоступен после теста"

echo "[PASS] Redis снова доступен"
echo "[PASS] API продолжает работать"

echo
echo "========================================="
echo " DEMO 04 SUCCESS"
echo " ALLOWED -> BLOCKED -> RESTORED"
echo "========================================="
