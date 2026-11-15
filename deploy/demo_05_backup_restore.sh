#!/usr/bin/env bash
set -Eeuo pipefail

# DEMO 05: PostgreSQL Backup & Restore
# Для существующего стенда Risk Engine.

cd "$(dirname "$0")/.."

MODE="${1:---check}"
MAIN_A="risk-engine-postgres"
MAIN_B="risk-engine-postgres-standby"
ALERT="risk-engine-alert-postgres"
IMAGE="postgres:16-alpine"

LABEL_KEY="risk-engine.demo"
LABEL_VALUE="backup-restore-05"

TMP_CONTAINER=""
BACKUP_DIR=""
RUN_START=0

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
    printf '[FAIL] %s\n' "$*" >&2
    exit 1
}

running() {
    [[ "$(docker inspect -f '{{.State.Running}}' \
        "$1" 2>/dev/null || true)" == "true" ]]
}

pg() {
    docker exec "$1" \
        psql -U "$2" -d "$3" -Atqc "$4"
}

# Определяем Primary независимо от названия контейнера.
primary_node() {
    local a b

    a="$(pg "$MAIN_A" riskengine riskengine \
        'SELECT pg_is_in_recovery();' 2>/dev/null || true)"

    b="$(pg "$MAIN_B" riskengine riskengine \
        'SELECT pg_is_in_recovery();' 2>/dev/null || true)"

    if [[ "$a" == "f" && "$b" == "t" ]]; then
        printf '%s\n' "$MAIN_A"
    elif [[ "$a" == "t" && "$b" == "f" ]]; then
        printf '%s\n' "$MAIN_B"
    else
        return 1
    fi
}

# Удаляем исключительно свой временный контейнер.
cleanup_temp() {
    local found_label

    [[ -n "$TMP_CONTAINER" ]] || return 0

    if ! docker container inspect "$TMP_CONTAINER" \
        >/dev/null 2>&1; then
        return 0
    fi

    found_label="$(docker inspect \
        -f "{{index .Config.Labels \"$LABEL_KEY\"}}" \
        "$TMP_CONTAINER" 2>/dev/null || true)"

    if [[ "$found_label" != "$LABEL_VALUE" ]]; then
        echo "[FAIL] Контейнер имеет чужую метку" >&2
        return 1
    fi

    docker rm -f "$TMP_CONTAINER" >/dev/null

    echo "[PASS] Временный контейнер удалён: $TMP_CONTAINER"
}

# Очистка выполняется даже при ошибке теста.
finish() {
    local rc=$?

    trap - EXIT INT TERM

    if [[ -n "$TMP_CONTAINER" ]]; then
        log "Удаление временного PostgreSQL"
        cleanup_temp || rc=1
    fi

    if [[ -n "$BACKUP_DIR" ]]; then
        rm -f -- "$BACKUP_DIR"/*.partial
        echo "Backups сохранены: $BACKUP_DIR"
    fi

    if (( rc == 0 )) && [[ "$MODE" == "--execute" ]]; then
        echo
        echo "============================================"
        echo " DEMO 05 SUCCESS: BACKUP + RESTORE VERIFIED"
        echo " Рабочие PostgreSQL не изменялись"
        echo "============================================"
    elif (( rc != 0 )); then
        echo "[FAIL] DEMO 05: код $rc" >&2
    fi

    exit "$rc"
}

case "$MODE" in
    --check|--execute|--cleanup) ;;
    *)
        fail "Usage: $0 --check|--execute|--cleanup"
        ;;
esac

(( $# <= 1 )) || fail "Допустим один аргумент"

(( EUID == 0 )) || fail "Запускай через sudo bash"

[[ -z "${DOCKER_HOST:-}" &&
   -z "${DOCKER_CONTEXT:-}" ]] || \
    fail "Удалённый Docker запрещён"

[[ "$(docker context show 2>/dev/null)" == "default" ]] || \
    fail "Docker context должен быть default"

for cmd in docker sha256sum python3; do
    command -v "$cmd" >/dev/null || \
        fail "Не найдена команда: $cmd"
done

docker info >/dev/null 2>&1 || \
    fail "Docker Engine недоступен"

# Режим очистки после аварийного прерывания.
if [[ "$MODE" == "--cleanup" ]]; then
    log "Поиск временных контейнеров DEMO 05"

    mapfile -t leftovers < <(
        docker ps -aq \
          --filter "label=$LABEL_KEY=$LABEL_VALUE"
    )

    if (( ${#leftovers[@]} == 0 )); then
        echo "[PASS] Временных контейнеров нет"
        exit 0
    fi

    docker ps -a \
        --filter "label=$LABEL_KEY=$LABEL_VALUE" \
        --format 'table {{.Names}}\t{{.Status}}'

    [[ -t 0 ]] || fail "Нужен интерактивный терминал"

    read -r -p "Введите CLEANUP для удаления: " answer

    [[ "$answer" == "CLEANUP" ]] || \
        fail "Очистка отменена"

    docker rm -f "${leftovers[@]}" >/dev/null

    echo "[PASS] Временные контейнеры DEMO 05 удалены"
    exit 0
fi

log "DEMO 05 — Backup and Restore ($MODE)"

echo
echo "[1] Проверка работающих контейнеров"

for c in "$MAIN_A" "$MAIN_B" "$ALERT"; do
    running "$c" || fail "Контейнер не работает: $c"
    echo "[PASS] $c"
done

PRIMARY="$(primary_node)" || \
    fail "Не удалось определить PostgreSQL Primary"

REPLICA="$MAIN_A"
[[ "$PRIMARY" == "$MAIN_A" ]] && REPLICA="$MAIN_B"

[[ "$(pg "$PRIMARY" riskengine riskengine \
    'SELECT pg_is_in_recovery();')" == "f" ]] || \
    fail "Основная БД не является Primary"

pg "$ALERT" alertsvc alertengine \
    'SELECT 1;' >/dev/null || \
    fail "Alert PostgreSQL недоступен"

docker exec "$PRIMARY" pg_dump --version \
    >/dev/null || fail "pg_dump отсутствует"

docker exec "$ALERT" pg_dump --version \
    >/dev/null || fail "pg_dump отсутствует в Alert DB"

docker image inspect "$IMAGE" >/dev/null 2>&1 || \
    fail "Образ $IMAGE не найден локально"

echo "Primary: $PRIMARY"
echo "Replica: $REPLICA"
echo "Alert DB: $ALERT"
echo "Restore image: $IMAGE"

if [[ "$MODE" == "--check" ]]; then
    echo
    echo "============================================"
    echo " DEMO 05 PREFLIGHT PASSED"
    echo " No changes were made"
    echo "============================================"
    exit 0
fi

[[ -t 0 ]] || fail "Нужен интерактивный терминал"

echo
echo "Будут созданы свежие дампы двух БД."
echo "Восстановление пройдёт во временном PostgreSQL."
echo "Рабочие базы данных и Docker volumes не удаляются."
echo

read -r -p "Для продолжения введи RESTORE: " answer

[[ "$answer" == "RESTORE" ]] || fail "Отменено"

umask 077

RUN_ID="$(date -u +%Y%m%d_%H%M%S)-$$"
BACKUP_DIR="$PWD/backups/demo05/$RUN_ID"

mkdir -p "$PWD/backups/demo05"
mkdir "$BACKUP_DIR" || fail "Ошибка создания каталога"

TMP_CONTAINER="risk-engine-demo05-restore-$RUN_ID"

trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

RUN_START=$SECONDS

log "[2] Backup основной PostgreSQL"

docker exec "$PRIMARY" \
    pg_dump -U riskengine -d riskengine \
    --format=custom \
    > "$BACKUP_DIR/riskengine.dump.partial" || \
    fail "Ошибка pg_dump основной БД"

[[ -s "$BACKUP_DIR/riskengine.dump.partial" ]] || \
    fail "Основной дамп пустой"

mv "$BACKUP_DIR/riskengine.dump.partial" \
   "$BACKUP_DIR/riskengine.dump"

echo "[PASS] riskengine.dump: $(stat -c '%s' \
    "$BACKUP_DIR/riskengine.dump") bytes"

log "[3] Backup Alert PostgreSQL"

docker exec "$ALERT" \
    pg_dump -U alertsvc -d alertengine \
    --format=custom \
    > "$BACKUP_DIR/alertengine.dump.partial" || \
    fail "Ошибка pg_dump Alert DB"

[[ -s "$BACKUP_DIR/alertengine.dump.partial" ]] || \
    fail "Дамп Alert DB пустой"

mv "$BACKUP_DIR/alertengine.dump.partial" \
   "$BACKUP_DIR/alertengine.dump"

echo "[PASS] alertengine.dump: $(stat -c '%s' \
    "$BACKUP_DIR/alertengine.dump") bytes"

log "[4] Контроль целостности SHA-256"

(
    cd "$BACKUP_DIR"

    sha256sum riskengine.dump alertengine.dump \
        > SHA256SUMS

    sha256sum -c SHA256SUMS
) || fail "Контрольные суммы не совпали"

echo "[PASS] SHA-256 verification"

log "[5] Запуск изолированного PostgreSQL"

# Тестовый PostgreSQL:
# - без сети;
# - без опубликованных портов;
# - без постоянного Docker volume;
# - данные только в tmpfs.
# Trust допускается только внутри изолированного
# временного контейнера без сетевого доступа.

docker run -d \
    --name "$TMP_CONTAINER" \
    --label "$LABEL_KEY=$LABEL_VALUE" \
    --network none \
    --tmpfs /var/lib/postgresql/data:rw,size=512m \
    -v "$BACKUP_DIR:/backups:ro" \
    -e POSTGRES_HOST_AUTH_METHOD=trust \
    "$IMAGE" >/dev/null || \
    fail "Ошибка запуска тестового PostgreSQL"

READY=0

for ((i=1; i<=45; i++)); do
    if docker exec "$TMP_CONTAINER" \
        pg_isready -U postgres >/dev/null 2>&1; then

        READY=1
        break
    fi

    sleep 2
done

(( READY == 1 )) || \
    fail "Тестовый PostgreSQL не стал доступным"

echo "[PASS] Isolated PostgreSQL running"

log "[6] Проверка формата резервных копий"

for dump in riskengine.dump alertengine.dump; do
    docker exec "$TMP_CONTAINER" \
        pg_restore --list "/backups/$dump" \
        >/dev/null || fail "Архив повреждён: $dump"

    echo "[PASS] pg_restore --list: $dump"
done

log "[7] Восстановление основной БД"

docker exec "$TMP_CONTAINER" \
    createdb -U postgres riskengine_test || \
    fail "Не удалось создать riskengine_test"

T0=$SECONDS

docker exec "$TMP_CONTAINER" \
    pg_restore \
    -U postgres \
    -d riskengine_test \
    --no-owner \
    --no-acl \
    --exit-on-error \
    --single-transaction \
    /backups/riskengine.dump || \
    fail "Ошибка восстановления riskengine"

MAIN_SECONDS=$((SECONDS - T0))

echo "[PASS] riskengine restored: ${MAIN_SECONDS}s"

log "[8] Восстановление базы уведомлений"

docker exec "$TMP_CONTAINER" \
    createdb -U postgres alertengine_test || \
    fail "Не удалось создать alertengine_test"

T0=$SECONDS

docker exec "$TMP_CONTAINER" \
    pg_restore \
    -U postgres \
    -d alertengine_test \
    --no-owner \
    --no-acl \
    --exit-on-error \
    --single-transaction \
    /backups/alertengine.dump || \
    fail "Ошибка восстановления alertengine"

ALERT_SECONDS=$((SECONDS - T0))

echo "[PASS] alertengine restored: ${ALERT_SECONDS}s"

log "[9] Проверка восстановленных таблиц"

for table in \
    alembic_version transactions alerts outbox_events; do

    check="$(pg "$TMP_CONTAINER" postgres riskengine_test \
        "SELECT to_regclass('public.$table') IS NOT NULL;")" || \
        fail "Ошибка проверки $table"

    [[ "$check" == "t" ]] || \
        fail "Отсутствует таблица $table"

    echo "[PASS] riskengine_test.public.$table"
done

for table in \
    alembic_version processed_events alert_notifications; do

    check="$(pg "$TMP_CONTAINER" postgres alertengine_test \
        "SELECT to_regclass('public.$table') IS NOT NULL;")" || \
        fail "Ошибка проверки $table"

    [[ "$check" == "t" ]] || \
        fail "Отсутствует таблица $table"

    echo "[PASS] alertengine_test.public.$table"
done

log "[10] Проверка чтения восстановленных данных"

for spec in \
    'riskengine_test transactions' \
    'riskengine_test alerts' \
    'riskengine_test outbox_events' \
    'alertengine_test processed_events' \
    'alertengine_test alert_notifications'; do

    read -r db table <<< "$spec"

    count="$(pg "$TMP_CONTAINER" postgres "$db" \
        "SELECT COUNT(*) FROM public.$table;")" || \
        fail "Ошибка чтения $db.$table"

    [[ "$count" =~ ^[0-9]+$ ]] || \
        fail "Некорректный COUNT: $db.$table"

    printf 'RESTORED %-20s %-20s rows=%s\n' \
        "$db" "$table" "$count"
done

log "[11] Проверка рабочего окружения"

NEW_PRIMARY="$(primary_node)" || \
    fail "Текущий Primary недоступен"

pg "$NEW_PRIMARY" riskengine riskengine \
    'SELECT 1;' >/dev/null || \
    fail "Основная БД недоступна"

pg "$ALERT" alertsvc alertengine \
    'SELECT 1;' >/dev/null || \
    fail "Alert DB недоступна"

if command -v curl >/dev/null; then
    curl -fsS --max-time 8 \
        http://127.0.0.1:8000/health || \
        fail "API недоступен"

    echo
    echo "[PASS] API available"
fi

echo
echo "Main DB restore:  ${MAIN_SECONDS}s"
echo "Alert DB restore: ${ALERT_SECONDS}s"
echo "Total demo time:  $((SECONDS - RUN_START))s"
